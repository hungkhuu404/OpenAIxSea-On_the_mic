"""Compare one candidate with the frozen baseline using cache-only replay."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
from math import comb
from pathlib import Path
import sys
import unicodedata

from core import paths
from core.config import config_line, set_override, settings
from core.enums import ErrorScale
from core.models import OracleReport

FREEZE_FILE = paths.DATA_DIR / "cases/FREEZE.json"
CANONICALIZATION = "NFC; rows sorted by case_id; json sort_keys, separators (',',':'), ensure_ascii=False; LF; trailing newline; SHA-256"


def canonical_hash(cases):
    canonical = "".join(unicodedata.normalize("NFC", json.dumps(row, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False)) + "\n"
                        for row in sorted(cases, key=lambda row: row["case_id"]))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _outside(path, argument):
    resolved = paths.resolve_path(path)
    if resolved.is_relative_to(paths.REPO_ROOT.resolve()):
        raise ValueError(f"{argument} must resolve outside the repository")
    return resolved


def _frozen_cases(path):
    manifest = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    if manifest["canonicalization"] != CANONICALIZATION:
        raise ValueError("Unsupported freeze canonicalization")
    entry = manifest["files"].get(path.name)
    if entry is None:
        raise ValueError("Case filename is absent from the frozen manifest")
    cases = _jsonl(path)
    if canonical_hash(cases) != entry["sha256"] or len(cases) != entry["n"]:
        raise ValueError("Case file does not match the frozen manifest")
    return cases


def build_packs(candidate, candidate_dir):
    from rules.registry import all_rules, load_pack

    rules = all_rules()
    baseline = [rule.rule_id for rule in rules if rule.meta.approved_by is not None
                and rule.meta.status == "active" and rule.meta.origin == "law_compiled"]
    current = baseline + [rule.rule_id for rule in rules if rule.meta.approved_by is not None
                          and rule.meta.status == "active" and rule.meta.origin == "t5"]
    if candidate in current:
        raise ValueError("Candidate must be a proposed rule outside the approved packs")
    before = load_pack(baseline, mode="live")
    try:
        after = load_pack([*current, candidate], mode="measure", candidate_dir=candidate_dir)
    except KeyError as error:
        raise ValueError("Requested candidate rule was not found") from error
    if candidate not in [rule.rule_id for rule in after.active]:
        raise ValueError("Requested candidate is not active in the measuring pack")
    return before, after


def _pack_label(pack):
    return json.dumps(sorted(rule.rule_id for rule in pack.active), separators=(",", ":"))


def mcnemar(b, c):
    n = b + c
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n) if n >= 10 else None


def overlap_flags(cases):
    from textnorm import match_key

    def bigrams(text):
        words = match_key(text)[0].split()
        return set(zip(words, words[1:]))

    sources = []
    regressions = paths.regressions_file()
    if regressions.is_file():
        sources.extend(row["text"] for row in _jsonl(regressions))
    for file in sorted(paths.clusters_dir().glob("*.json")):
        sources.extend(json.loads(file.read_text(encoding="utf-8"))["texts"])
    # Containment is always at least Jaccard, so it determines their maximum.
    source_sets = [bigrams(text) for text in sources]
    flagged = 0
    for case in cases:
        words = bigrams(case["text"])
        flagged += any(words and other and len(words & other) / min(len(words), len(other)) >= .5
                       for other in source_sets)
    return flagged


def summarize(cases, before, after, pack_v1, pack_v2, standin=False):
    from gate.score import classify_error
    from pipeline.aggregate import who_decided

    if len(cases) != len(before) or len(cases) != len(after):
        raise ValueError("Runner returned a different number of results")
    transitions = Counter()
    measured = datetime.now(timezone.utc).isoformat()
    misses = negq = improved = red = red_wrong = refusals = b = c = 0
    decided = [[], []]
    for case, old, new in zip(cases, before, after, strict=True):
        pair = []
        misses += old.status == "not_evaluated" or new.status == "not_evaluated"
        negq += max(old.negq_miss, new.negq_miss)
        for index, result in enumerate((old, new)):
            if result.status == "not_evaluated":
                colour = "abstain"
                decided[index].append({"decided_by": "abstain:LLM_OFF"})
            else:
                colour = result.case_verdict
                if colour is None:
                    raise ValueError("An evaluated case has no case verdict")
                decided[index].extend(result.verdicts)
            pair.append((colour, classify_error(colour, case["label"], case.get("abstain_expected", False))))
        (_, old_class), (right, new_class) = pair
        transitions[f"{old_class.value} → {new_class.value}"] += 1
        improved += old_class == ErrorScale.ABSTAIN and new_class == ErrorScale.CORRECT
        b += old_class == ErrorScale.CORRECT and new_class != ErrorScale.CORRECT
        c += old_class != ErrorScale.CORRECT and new_class == ErrorScale.CORRECT
        red += right == "red"
        red_wrong += right == "red" and new_class != ErrorScale.CORRECT
        refusals += bool(case.get("abstain_expected")) and right == "abstain"
    who = [who_decided(rows).model_copy(update={"measured_at": measured, "source": "oracle"})
           for rows in decided]
    return OracleReport(measured_at=measured, n_cases=len(cases), cache_miss=misses, negq_miss=negq,
                        pack_v1=_pack_label(pack_v1), pack_v2=_pack_label(pack_v2),
                        transitions=dict(transitions), abstain_to_correct=improved, correct_to_wrong=b,
                        red_wrong={"k": red_wrong, "n": red},
                        correct_refusals={"x": refusals, "n": sum(bool(case.get("abstain_expected")) for case in cases)},
                        who_decided_v1=who[0], who_decided_v2=who[1], mcnemar_p=mcnemar(b, c),
                        overlap_flags=overlap_flags(cases), standin=standin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay")
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument("--heldout", type=Path)
    source.add_argument("--standin-every", type=int)
    replay.add_argument("--cache-dir", type=Path)
    replay.add_argument("--out", required=True, type=Path)
    replay.add_argument("--v2-rules", required=True, action="append")
    replay.add_argument("--candidate-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if len(args.v2_rules) != 1:
            raise ValueError("Exactly one proposed candidate is required")
        output = _outside(args.out, "--out")
        candidate_dir = _outside(args.candidate_dir, "--candidate-dir") if args.candidate_dir else None
        if args.heldout is not None:
            heldout = _outside(args.heldout, "--heldout")
            if args.cache_dir is None:
                raise ValueError("--heldout requires --cache-dir outside the repository")
            cache = _outside(args.cache_dir, "--cache-dir")
            if settings.LLM_BACKEND in ("off", "codex"):
                raise ValueError("--heldout requires backend api; off and codex are refused")
            if output == heldout:
                raise ValueError("--out must not overwrite the input case file")
            cases = _frozen_cases(heldout)
            set_override("LLM_CACHE_DIR", str(cache))
            purpose = "oracle"
        else:
            if args.standin_every < 1 or args.cache_dir is not None:
                raise ValueError("stand-in requires a positive interval and the committed cache")
            cases = sorted(_jsonl(paths.GOLDEN_CASES), key=lambda case: case["case_id"])[args.standin_every - 1::args.standin_every]
            set_override("LLM_CACHE_DIR", None)
            purpose = "shadow"
        set_override("OFFLINE", 1)
        before, after = build_packs(args.v2_rules[0], candidate_dir)
        from gate.score import case_as_of, _write_json
        runner = import_module("pipeline.runner")
        from pipeline.llm import run_scope

        cases = [case | {"as_of": case_as_of(case), "source_date": case_as_of(case)} for case in cases]
        print(config_line(), file=sys.stderr)
        with run_scope(offline=True, measuring=True):
            old = list(runner.replay(cases, pack=before, persist=False, purpose=purpose, pack_version=1))
            new = list(runner.replay(cases, pack=after, persist=False, purpose=purpose, pack_version=2))
        report = summarize(cases, old, new, before, after, args.standin_every is not None)
        value = report.model_dump(mode="json")
        _write_json(output, value)
        print(json.dumps(value, ensure_ascii=False))
        if report.standin:
            print("STAND-IN, not the sealed set")
        if report.correct_to_wrong:
            print("do not merge, say why")
        elif report.abstain_to_correct:
            print("merge PR2")
        else:
            print("merge, say reliability only")
        if not report.standin:
            print("Reminder: delete the extracted held-out file and extracted cache directory.")
        return 0
    except ModuleNotFoundError as error:
        if error.name not in ("pipeline.runner", "pipeline.llm", "pipeline.aggregate", "rules.registry"):
            raise
        print(f"not ready: {error.name}", file=sys.stderr)
        return 2
    except (ValueError, OSError):
        # Inputs may contain case text or identifiers; diagnostics must stay aggregate-only.
        print("Oracle replay refused: invalid input, path, frozen hash or candidate metadata.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
