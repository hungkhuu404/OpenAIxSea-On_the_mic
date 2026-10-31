"""Offline case scoring and aggregate-only oracle reporting."""

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from core import paths
from core.config import config_line, set_override, settings
from core.enums import ErrorScale
from core.models import OracleReport

FREEZE_FILE = paths.DATA_DIR / "cases/FREEZE.json"
BASELINE_FILE = paths.REPO_ROOT / "gate/baseline.json"
ERROR_ORDER = list(ErrorScale)
SEVERITY = {"green": 0, "yellow": 1, "red": 2}


def classify_error(verdict, label, abstain_expected=False):
    if abstain_expected:
        return ErrorScale.CORRECT if verdict == "abstain" else ErrorScale.HEAVIER_THAN_LABEL
    if verdict == label:
        return ErrorScale.CORRECT
    if verdict == "abstain":
        return ErrorScale.ABSTAIN
    return (ErrorScale.LIGHTER_THAN_LABEL if SEVERITY[verdict] < SEVERITY[label]
            else ErrorScale.HEAVIER_THAN_LABEL)


def case_as_of(case):
    return "2025-01-08" if case["sku_id"] == "kera" else case.get("source_date") or case.get("as_of") or "2026-10-21"


def build_pack(choice):
    from rules.registry import all_rules, load_pack

    if choice == "v1":
        ids = [rule.rule_id for rule in all_rules() if rule.meta.approved_by is not None
               and rule.meta.status == "active" and rule.meta.origin == "law_compiled"]
        return load_pack(ids, mode="live"), 1, "v1"
    if not paths.ledger_db().is_file():
        raise ValueError("LEDGER_DB does not exist; current pack cannot be read")
    import ledger
    row = ledger.current_pack()
    return load_pack(row.active, quarantined=row.quarantined, mode="live"), row.version, row.version


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _verify_freeze(cases):
    rows = sorted(cases, key=lambda row: row["case_id"])
    canonical = "".join(unicodedata.normalize("NFC", json.dumps(row, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False)) + "\n" for row in rows)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    manifest = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    if not any(entry["sha256"] == digest and entry["n"] == len(rows) for entry in manifest["files"].values()):
        raise ValueError("Case file does not match the frozen manifest")


def summarize(cases, results, pack, purpose, standin=False):
    from pipeline.aggregate import who_decided

    if len(cases) != len(results):
        raise ValueError("Runner returned a different number of results")
    counts = Counter({value.value: 0 for value in ErrorScale})
    table, verdicts = [], []
    abstain = decided = correct_decided = red = red_wrong = refusals = misses = negq = 0
    for case, result in zip(cases, results):
        negq += result.negq_miss
        if result.status == "not_evaluated":
            misses += 1
            table.append(dict(case_id=case["case_id"], status="not_evaluated"))
            continue
        verdict = result.case_verdict
        if verdict is None:
            raise ValueError("An evaluated case has no case_verdict")
        error = classify_error(verdict, case["label"], case.get("abstain_expected", False))
        counts[error.value] += 1
        abstain += verdict == "abstain"
        decided += verdict != "abstain"
        correct_decided += verdict != "abstain" and error == ErrorScale.CORRECT
        red += verdict == "red"
        red_wrong += verdict == "red" and error != ErrorScale.CORRECT
        refusals += case.get("abstain_expected", False) and verdict == "abstain"
        verdicts.extend(result.verdicts)
        table.append(dict(case_id=case["case_id"], verdict=verdict, error=error.value, status="ok"))
    measured = datetime.now(timezone.utc).isoformat()
    who = who_decided(verdicts).model_copy(update={"measured_at": measured,
                                                  "source": "oracle" if purpose == "oracle" else "ledger"})
    evaluated = len(cases) - misses
    summary = dict(counts=dict(counts), n_cases=len(cases),
                   accuracy_decided=dict(value=correct_decided / decided if decided else None,
                                         correct=correct_decided, denominator=decided),
                   abstain_rate=abstain / evaluated if evaluated else None,
                   red_wrong=dict(k=red_wrong, n=red),
                   correct_refusals=dict(x=refusals, n=sum(bool(case.get("abstain_expected")) for case in cases)),
                   who_decided=who.model_dump(mode="json"), not_evaluated=misses, negq_miss=negq,
                   pack=pack, measured_at=measured, standin=standin)
    if standin:
        summary["label"] = "STAND-IN"
    report = OracleReport(measured_at=measured, n_cases=len(cases), cache_miss=misses,
                          negq_miss=negq, pack_v1=str(pack), pack_v2=None, transitions={},
                          abstain_to_correct=0, correct_to_wrong=0,
                          red_wrong=summary["red_wrong"], correct_refusals=summary["correct_refusals"],
                          who_decided_v1=who, overlap_flags=0, standin=standin)
    return summary, table, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("replay", "llm"), default="replay")
    parser.add_argument("--purpose", choices=("shadow", "oracle"), default="shadow")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--pack", choices=("v1", "current"), default="v1")
    parser.add_argument("--standin-every", type=int)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.standin_every is not None:
            if args.standin_every < 1 or args.cases or args.pack != "v1" or args.purpose == "oracle":
                raise ValueError("stand-in requires a positive interval, public cases and pack v1")
            args.purpose, args.mode = "shadow", "replay"
        if args.purpose == "oracle":
            if args.pack != "v1" or args.write_baseline or settings.LLM_BACKEND == "codex":
                raise ValueError("oracle requires pack v1, refuses codex and cannot write a baseline")
            if args.cache_dir is None or paths.resolve_path(args.cache_dir).is_relative_to(paths.REPO_ROOT):
                raise ValueError("oracle requires --cache-dir outside the repository")
            if args.out and paths.resolve_path(args.out).is_relative_to(paths.REPO_ROOT):
                raise ValueError("oracle --out must be outside the repository")
            args.aggregate_only = True
        if args.write_baseline and (args.mode != "replay" or args.pack != "v1"):
            raise ValueError("baseline requires --mode replay --pack v1")
        if args.cache_dir is not None:
            set_override("LLM_CACHE_DIR", str(paths.resolve_path(args.cache_dir)))
        pack, version, label = build_pack(args.pack)
        cases = [json.loads(line) for line in (args.cases or paths.GOLDEN_CASES).read_text(
            encoding="utf-8").splitlines() if line.strip()]
        if args.purpose == "oracle":
            _verify_freeze(cases)
        if args.standin_every:
            cases = sorted(cases, key=lambda case: case["case_id"])[args.standin_every - 1::args.standin_every]
        cases = [case | {"as_of": case_as_of(case)} for case in cases]
        import pipeline.runner as runner
        from pipeline.llm import run_scope
        print(config_line(), file=sys.stderr)
        with run_scope(offline=args.mode == "replay", measuring=True):
            if args.mode == "replay":
                results = runner.replay(cases, pack=pack, persist=False, purpose=args.purpose, pack_version=version)
            else:
                results = runner.run_cases(cases, purpose=args.purpose, pack=pack, pack_version=version)
        summary, table, report = summarize(cases, list(results), label, args.purpose, args.standin_every is not None)
        print(json.dumps(summary, ensure_ascii=False))
        if args.write_baseline:
            if summary["not_evaluated"] or summary["negq_miss"]:
                raise ValueError(f"baseline refused: not_evaluated={summary['not_evaluated']} negq_miss={summary['negq_miss']}")
            _write_json(BASELINE_FILE, {"wrong_case_ids": [row["case_id"] for row in table
                                                        if row.get("error") != ErrorScale.CORRECT],
                                        "pack": label, "measured_at": summary["measured_at"]})
        if not args.aggregate_only:
            _write_json(paths.LOCAL_DIR / ("score_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"), table)
        if args.out:
            _write_json(paths.resolve_path(args.out), report.model_dump(mode="json"))
        return 0
    except ModuleNotFoundError as error:
        if error.name != "pipeline.runner":
            raise
        print("not ready: pipeline.runner", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
