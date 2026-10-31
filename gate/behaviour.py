"""Offline behavioural checks for a single proposed rule, without live writes."""

from datetime import date
import json
from pathlib import Path

from core import paths
from core.enums import ErrorScale, Verdict
from core.models import AtomView, ClusterFile, GateCheck, GateResult, RegressionItem, RuleMeta, Session, Turn


CACHE_MISS = "cache miss: run python -m pipeline.cli shadow"


class _CacheMiss(Exception):
    pass


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _golden_cases():
    cases = _jsonl(paths.GOLDEN_CASES)
    for case in cases:
        case["as_of"] = ("2025-01-08" if case["sku_id"] == "kera"
                         else case.get("source_date") or "2026-10-21")
    return cases


def _memory_cases():
    path = paths.regressions_file()
    if not path.exists():
        return []
    cases = []
    for raw in _jsonl(path):
        item = RegressionItem.model_validate(raw)
        case = item.model_dump(mode="json")
        case.update(case_id=item.item_id, source_date=case["as_of"])
        cases.append(case)
    return cases


def _packs(rule_id, candidate_dir, ci):
    from rules import registry

    if ci:
        active = [r.rule_id for r in registry.all_rules()
                  if r.meta.approved_by and r.meta.status == "active"
                  and r.rule_id != rule_id]
        quarantined, version = [], None
    else:
        if not paths.ledger_db().is_file():
            raise ValueError("LEDGER_DB must name an existing ledger file")
        from ledger import current_pack

        row = current_pack()
        active, quarantined, version = list(row.active), list(row.quarantined), row.version
        if rule_id in active or rule_id in quarantined:
            raise ValueError("Candidate must not already be in the live pack")
    before = registry.load_pack(active, quarantined=quarantined, mode="live")
    after = registry.load_pack([*active, rule_id], quarantined=quarantined,
                               mode="measure", candidate_dir=candidate_dir)
    candidate = registry.load_pack([rule_id], mode="measure", candidate_dir=candidate_dir)
    return before, after, candidate, version


def _replay(golden, memory, pack, version):
    from pipeline import runner

    results = list(runner.replay(golden, pack=pack, persist=False,
                                purpose="live", pack_version=version))
    if memory:
        results.extend(runner.run_cases(memory, purpose="live", pack=pack,
                                        pack_version=version))
    if len(results) != len(golden) + len(memory):
        raise ValueError("Runner returned a different number of results than inputs")
    if any(r.status == "not_evaluated" or r.negq_miss for r in results):
        raise _CacheMiss
    return results


def _colour(case, result):
    if case.get("level") == "atom":
        from pipeline.aggregate import turn_verdict

        return turn_verdict(result.verdicts) or Verdict.ABSTAIN
    return result.case_verdict or Verdict.ABSTAIN


def _distance(case, colour):
    if case.get("abstain_expected") or case["label"] == "abstain":
        scale = ErrorScale.CORRECT if colour == "abstain" else ErrorScale.HEAVIER_THAN_LABEL
    elif colour == case["label"]:
        scale = ErrorScale.CORRECT
    elif colour == "abstain":
        scale = ErrorScale.ABSTAIN
    else:
        severity = {"green": 0, "abstain": 1, "yellow": 2, "red": 3}
        scale = (ErrorScale.LIGHTER_THAN_LABEL if severity[colour] < severity[case["label"]]
                 else ErrorScale.HEAVIER_THAN_LABEL)
    return list(ErrorScale).index(scale)


def _candidate_hits(case, pack):
    """Use the same cached S1 inputs, including code-verified speaker roles."""
    from pipeline.context import build_context
    from pipeline.extract import extract_atoms
    from textnorm import match_key, nfc

    text = nfc(case["text"])
    session = Session(session_id=f"case-{case['case_id']}", sku_pin=case["sku_id"],
                      as_of=date.fromisoformat(case["as_of"]),
                      channel="written" if case.get("modality") == "written" else "livestream",
                      source="Rule behaviour measurement",
                      turns=[Turn(turn_id="t01", speaker_label="[unidentified]",
                                  attribution="not_applicable", text=text)])
    ctx = build_context(session.sku_pin, session.as_of, pack=pack)
    turn = session.turns[0]
    atoms = extract_atoms(session, turn, None, ctx, purpose="live", cache_read=True)
    hits = []
    for atom in atoms:
        if atom.s1_call_id is None:
            raise _CacheMiss
        view = AtomView(text=atom.span, key=match_key(atom.span)[0], axis=atom.axis,
                        speaker_role=atom.speaker_role, role_span=atom.role_span,
                        category_norm=ctx.category_norm, as_of=ctx.as_of, turn_text=turn.text)
        hits.extend(pack.check_atom(view))
    return hits


def run_behaviour(rule_id, candidate_dir, sources=None, ci=False):
    """Return checks 8–10; incomplete cache is a job error, never a rule failure."""
    from pipeline.llm import run_scope

    candidate_dir = Path(candidate_dir)
    meta = RuleMeta.model_validate_json(
        (candidate_dir / "rules" / f"{rule_id}.meta.json").read_text(encoding="utf-8"))
    if meta.id != rule_id:
        raise ValueError("Candidate metadata id does not match the requested rule")
    before, after, candidate, version = _packs(rule_id, candidate_dir, ci)
    golden, memory = _golden_cases(), _memory_cases()
    cases = golden + memory
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate golden or correction-memory item id")
    skipped = ci or sources is None
    problems = {8: [], 9: [], 10: []}
    changes, revealed = [], set()

    def fail(check, case_id, message):
        problems[check].append(f"{rule_id} check {check}: {message}")
        if case_id in golden_ids:
            revealed.add(case_id)

    golden_ids = {case["case_id"] for case in golden}
    try:
        with run_scope(offline=True, measuring=True):
            old = _replay(golden, memory, before, version)
            new = _replay(golden, memory, after, version)
            outcomes = {}
            for case, previous, current in zip(cases, old, new, strict=True):
                key = case["case_id"]
                left, right = _colour(case, previous), _colour(case, current)
                outcomes[key] = (case, right)
                if left != right:
                    changes.append({"item_id": key, "before": left, "after": right})
                    if _distance(case, right) > _distance(case, left):
                        fail(9, key, f"{key} moves farther from its label ({left} to {right}).")
                if meta.family not in case["rule_ids"] and _candidate_hits(case, candidate):
                    fail(10, key, f"fires on {key}; its labelled families are {case['rule_ids']}.")
            if not skipped:
                cluster = ClusterFile.model_validate_json(Path(sources).read_text(encoding="utf-8"))
                if not (len(cluster.item_ids) == len(cluster.texts) == len(cluster.labels)):
                    raise ValueError("Source cluster item, text and label counts differ")
                for key, text, label in zip(cluster.item_ids, cluster.texts, cluster.labels, strict=True):
                    if key not in outcomes:
                        fail(8, key, f"source item {key} is missing from the evaluation items.")
                        continue
                    case, colour = outcomes[key]
                    if text != case["text"] or label != case["label"]:
                        fail(8, key, f"source item {key} differs from its frozen cluster.")
                    elif _distance(case, colour) != 0:
                        fail(8, key, f"source item {key} remains {colour}; expected {label}.")
    except _CacheMiss:
        return GateResult(passed=False, checks=[
            GateCheck(id=n, passed=False, detail="cache incomplete") for n in (8, 9, 10)
        ], error_lines=[CACHE_MISS])
    checks = [GateCheck(id=n, passed=not problems[n],
                        detail="not provided" if n == 8 and skipped else
                        "pass" if not problems[n] else f"{len(problems[n])} problem(s)")
              for n in (8, 9, 10)]
    return GateResult(passed=all(c.passed for c in checks), checks=checks,
                      error_lines=[line for n in (8, 9, 10) for line in problems[n]],
                      observed_changes=changes, revealed_case_ids=sorted(revealed))
