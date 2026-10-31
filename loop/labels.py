"""Human labels and public correction memory; clip text stays local."""

from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
from threading import RLock
from uuid import uuid4

from core import paths
from core.config import settings
from core.models import RegressionItem

_LOCK = RLock()


def _resolve(target, *, effective=True):
    import ledger
    verdict = None
    if isinstance(target, int) or str(target).isdigit():
        verdict = ledger.get_verdict(int(target))
        if verdict is None:
            raise ValueError("Unknown verdict")
        target = verdict.atom_key
    session_id, separator, atom_id = str(target).rpartition(":")
    if not separator:
        raise ValueError("Expected an atom key or verdict_id")
    session = ledger.get_session(session_id)
    if session is None:
        raise ValueError("Unknown session")
    if session_id.startswith("clip-") or session.channel == "video":
        raise ValueError("Clip sessions cannot enter correction memory")
    atom = next((atom for atom in session.atoms if atom.atom_id == atom_id), None)
    if atom is None:
        raise ValueError("Unknown atom")
    if effective or verdict is None:
        verdict = next((row for row in ledger.effective_verdicts(session_id) if row.atom_key == target), None)
    if verdict is None:
        raise ValueError("Atom has no effective verdict")
    if not session.sku_pin:
        raise ValueError("Correction memory requires a session SKU")
    return session, atom, verdict


def _git(*args):
    return subprocess.run(["git", *args], cwd=paths.REPO_ROOT, check=True,
                          capture_output=True, encoding="utf-8")


def _commit_paths(atom, verdict):
    from pipeline.llm import cache_path
    root = paths.REPO_ROOT.resolve()
    if not paths.eval_dir().is_relative_to(root):
        raise ValueError("REGRESSION_AUTOCOMMIT requires EVAL_DIR inside the repository")
    copies = []
    for call_id in dict.fromkeys([atom.s1_call_id, *verdict.llm_call_ids]):
        if call_id is None:
            continue
        source = cache_path(paths.local_cache_dir(), call_id)
        destination = cache_path(paths.CACHE_DIR, call_id)
        if source.is_file():
            copies.append((source, destination))
        elif not destination.is_file():
            raise ValueError(f"Missing cache row for regression: {call_id}")
    allowed = {paths.regressions_file().relative_to(root).as_posix()}
    allowed.update(destination.relative_to(root).as_posix() for _, destination in copies)
    staged = _git("diff", "--cached", "--no-renames", "--name-only", "-z").stdout.split("\0")
    staged = {Path(PureWindowsPath(name).as_posix()).as_posix() for name in staged if name}
    if staged - allowed:
        raise ValueError("Unrelated files are staged; refusing regression autocommit")
    return copies, sorted(allowed)


def _record(session, atom, verdict, label, rule_id, labeler, kind):
    import ledger
    ts = datetime.now(timezone.utc).isoformat()
    item_id = uuid4().hex
    item = None
    if label is not None:
        item = RegressionItem(item_id=item_id, level="atom", text=atom.span,
            sku_id=session.sku_pin, as_of=session.as_of, label=label,
            rule_ids=[rule_id] if rule_id else [], labeler=labeler, ts=ts, kind=kind,
            origin_verdict=verdict.model_dump(include={"verdict", "reason_code", "decided_by"}),
            near_hit_patterns=[hit.pattern for hit in atom.near_hits], source_note=session.source,
            session_id=session.session_id, atom_id=atom.atom_id)
    copies, staged = _commit_paths(atom, verdict) if item and settings.REGRESSION_AUTOCOMMIT else ([], [])
    if item:
        file = paths.regressions_file()
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as stream:
            stream.write(item.model_dump_json() + "\n")
    ledger.add_label(dict(target=verdict.atom_key, kind=kind, label=label, rule_id=rule_id,
                          labeler=labeler, verdict_id=verdict.verdict_id, ts=ts))
    commit = ""
    if staged:
        for source, destination in copies:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        _git("add", "--", *staged)
        _git("commit", "-m", f"regression: {item_id}")
        commit = _git("rev-parse", "HEAD").stdout.strip()
    ledger.append_event("label.recorded", dict(item_id=item_id, label=label, rule_id=rule_id,
                                               labeler=labeler, kind=kind, commit=commit))
    ledger.append_event("queue.changed", {})
    return item


def record_label(target, label, rule_id, labeler, auto_trigger=True):
    if label is None:
        raise ValueError("A label is required")
    with _LOCK:
        item = _record(*_resolve(target), label, rule_id, labeler, "label")
        if auto_trigger:
            from loop.t5 import maybe_trigger
            maybe_trigger()
        return item


def record_report_wrong(verdict_id, reporter, correct_label=None):
    import ledger
    with _LOCK:
        session, atom, verdict = _resolve(verdict_id, effective=False)
        rule_id = verdict.decided_by.removeprefix("rule:") if verdict.decided_by.startswith("rule:") else None
        prior = [row for row in ledger.list_labels() if row["kind"] == "report_wrong" and row["rule_id"] == rule_id]
        item = _record(session, atom, verdict, correct_label, rule_id, reporter, "report_wrong")
        already_requested = len(prior) >= 2 or any(row["labeler"] == settings.RULE_KEEPER for row in prior)
        if rule_id and not already_requested and (len(prior) == 1 or reporter == settings.RULE_KEEPER):
            from pipeline.dossier_events import quarantine
            quarantine(rule_id, "reported wrong")
        return item
