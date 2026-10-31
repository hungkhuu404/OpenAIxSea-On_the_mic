"""Discover rule metadata and build approved live or isolated measuring packs."""

import ast
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class RuleInfo:
    rule_id: str
    module: ModuleType
    meta: object


def _metadata(directory):
    from core.models import RuleMeta

    metadata = {}
    for path in sorted(directory.glob("*.meta.json")):
        meta = RuleMeta.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if meta.id in metadata:
            raise ValueError(f"Duplicate metadata id: {meta.id}")
        metadata[meta.id] = meta
    return metadata


def _discover(directory, selected=None, *, metadata=None):
    metadata = _metadata(directory) if metadata is None else metadata
    rules = {}
    seen = set()
    for path in sorted(directory.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            if selected is None or path.stem in selected:
                raise
            continue
        ids = [node.value.value for node in tree.body
               if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
               and any(isinstance(target, ast.Name) and target.id == "RULE_ID"
                       for target in node.targets)]
        if not ids:
            continue
        rule_id, = ids
        if rule_id not in metadata or rule_id in seen:
            raise ValueError(f"Duplicate or mismatched rule id: {rule_id}")
        seen.add(rule_id)
        if selected is not None and rule_id not in selected:
            continue
        meta = metadata[rule_id]
        location = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(f"rules._loaded_{path.stem}_{location}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for name, expected in (("FAMILY", meta.family), ("ORIGIN", meta.origin),
                               ("ANCHORS", meta.anchors), ("LAW_SPAN", meta.law_span),
                               ("PRECEDENCE", meta.precedence)):
            if module.__dict__[name] != expected:
                raise ValueError(f"Rule/meta mismatch: {rule_id}.{name}")
        if not {"check", "check_session"} & module.__dict__.keys():
            raise ValueError(f"Rule has no check function: {rule_id}")
        rules[rule_id] = RuleInfo(rule_id, module, meta)
    return rules


def all_rules():
    return sorted(_discover(Path(__file__).parent).values(), key=lambda rule: rule.rule_id)


@dataclass(frozen=True)
class RuntimePack:
    active: tuple
    quarantined: tuple
    mode: str

    def _atom_hits(self, rules, atom_view):
        return sorted((hit for rule in rules if "check" in rule.module.__dict__
                       for hit in rule.module.check(atom_view)),
                      key=lambda hit: (hit.precedence, hit.rule_id))

    def check_atom(self, atom_view):
        return self._atom_hits(self.active, atom_view)

    def quarantined_hits(self, atom_view):
        return self._atom_hits(self.quarantined, atom_view)

    def check_session(self, dossier_view, as_of):
        return [flag for rule in sorted(self.active, key=lambda rule: (rule.meta.precedence, rule.rule_id))
                if "check_session" in rule.module.__dict__
                for flag in rule.module.check_session(dossier_view, as_of)]


def load_pack(rule_ids, quarantined=(), mode="live", candidate_dir=None):
    if mode not in ("live", "measure"):
        raise ValueError(f"Unknown pack mode: {mode}")
    if candidate_dir is not None and mode != "measure":
        raise ValueError("Candidates are permitted only in measure mode")
    active_ids, quarantined_ids = tuple(rule_ids), tuple(quarantined)
    if len(set(active_ids + quarantined_ids)) != len(active_ids + quarantined_ids):
        raise ValueError("Duplicate or overlapping pack ids")
    directory = Path(__file__).parent
    main_metadata = _metadata(directory)
    metadata = dict(main_metadata)
    candidate_ids = set()
    if candidate_dir is not None:
        candidate_directory = Path(candidate_dir) / "rules"
        candidates = _metadata(candidate_directory)
        selected = [rule_id for rule_id in active_ids if rule_id in candidates
                    and (rule_id not in metadata or candidates[rule_id].approved_by is None)]
        if len(selected) != 1:
            raise ValueError("Measure mode requires exactly one selected candidate")
        candidate_ids = set(selected)
        metadata[selected[0]] = candidates[selected[0]]
    selected_meta = [metadata[rule_id] for rule_id in active_ids + quarantined_ids]
    unapproved = [meta for meta in selected_meta if meta.approved_by is None]
    if (mode == "live" and unapproved) or len(unapproved) > 1:
        raise ValueError("Live rules require approval; measure mode permits one unapproved rule")
    active_ids = tuple(rule_id for rule_id in active_ids
                       if metadata[rule_id].status == "active"
                       or (mode == "measure" and metadata[rule_id].status == "proposed"))
    needed = set(active_ids + quarantined_ids)
    discovered = _discover(directory, needed - candidate_ids, metadata=main_metadata)
    if candidate_dir is not None:
        discovered.update(_discover(candidate_directory, needed & candidate_ids, metadata=candidates))
    active = tuple(discovered[rule_id] for rule_id in active_ids)
    return RuntimePack(active, tuple(discovered[rule_id] for rule_id in quarantined_ids), mode)
