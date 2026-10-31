"""Command-line ingest, processing, cache warming and routing traces."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import io
import json
from pathlib import Path
import re
import sys
from time import monotonic

from core import paths
from core.config import config_line, settings


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    kera = sub.add_parser("kera")
    kera.add_argument("--as-of", required=True)
    kera.add_argument("--reprocess", action="store_true")
    process = sub.add_parser("process")
    process.add_argument("--session", required=True)
    process.add_argument("--purpose", choices=("live", "shadow"), required=True)
    process.add_argument("--reprocess", action="store_true")
    process.add_argument("--fresh", action="store_true")
    sentences = sub.add_parser("sentences")
    sentences.add_argument("--sku", required=True)
    sentences.add_argument("--as-of", required=True)
    sentences.add_argument("--file", type=Path, required=True)
    sentences.add_argument("--source", required=True)
    shadow = sub.add_parser("shadow")
    shadow.add_argument("--s1-only", action="store_true")
    for name in ("golden", "kera", "regressions"):
        shadow.add_argument(f"--{name}", action="store_true")
    shadow.add_argument("--sentences", type=Path, nargs="?",
                        const=paths.REPO_ROOT / "tests" / "demo" / "pr1_sentences.json")
    case = sub.add_parser("case")
    case.add_argument("--id", required=True)
    repeat = sub.add_parser("judge-repeat")
    repeat.add_argument("--session", required=True)
    repeat.add_argument("--turn", required=True)
    repeat.add_argument("--n", type=int, required=True)
    return parser


def _check_backend():
    # Only committed cache rows, never PRESENT_DIR or local clip rows.
    for path in sorted(paths.CACHE_DIR.glob("[0-9a-f][0-9a-f]/*.json")):
        if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
            continue
        backend = json.loads(path.read_text(encoding="utf-8"))["backend"]
        if backend != settings.LLM_BACKEND:
            raise ValueError(f"cache/llm holds {backend} rows: one backend per committed cache (contracts section 3)")


def _v1_pack():
    from rules.registry import all_rules, load_pack

    ids = [rule.rule_id for rule in all_rules()
           if rule.meta.approved_by is not None and rule.meta.origin == "law_compiled"
           and rule.meta.status == "active"]
    return load_pack(ids, mode="measure")


def _json_lines(path):
    if not path.is_file():
        print(f"note: missing {path.name}; no targets")
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _public_case(row):
    if str(row.get("session_id") or "").startswith("clip-"):
        raise ValueError("shadow refuses clip- targets")
    return row


def _public_session(session):
    if session.session_id.startswith("clip-"):
        raise ValueError("shadow refuses clip- targets")
    return session


def _shadow_targets(args):
    from pipeline.ingest import from_eval_case, from_reenactment, from_text

    use_all = not (args.golden or args.kera or args.regressions or args.sentences is not None)
    sessions = []
    if args.golden or use_all:
        sessions.extend(from_eval_case(_public_case(row)) for row in _json_lines(paths.GOLDEN_CASES))
    if args.kera or use_all:
        sessions.append(from_reenactment(paths.KERA_DIR / "reenactment_script.md", "kera", "2025-01-08"))
    if args.regressions or use_all:
        for row in _json_lines(paths.regressions_file()):
            _public_case(row)
            sessions.append(from_eval_case(dict(case_id=row["item_id"], sku_id=row["sku_id"],
                                                 source_date=row["as_of"], text=row["text"])))
    if args.sentences is not None or use_all:
        path = args.sentences or paths.REPO_ROOT / "tests" / "demo" / "pr1_sentences.json"
        if not path.is_file():
            print(f"note: missing {path.name}; no targets")
        else:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            sessions.extend(from_text(fixture["sku_id"], fixture["as_of"], text, fixture["source"])
                            for text in fixture["sentences"])
    return [_public_session(session) for session in sessions]


def _warm(session, pack, s1_only):
    from pipeline.llm import run_scope

    with run_scope(measuring=True):
        if not s1_only:
            from pipeline.runner import _compute
            return _compute(session, pack, 1, "shadow")
        from pipeline.context import build_context
        from pipeline.extract import extract_atoms

        ctx = build_context(session.sku_pin, session.as_of, pack)
        previous = None
        for turn in session.turns:
            extract_atoms(session, turn, previous, ctx, "shadow", cache_read=True)
            previous = turn
    return None


def _shadow(args):
    from pipeline.llm import observe_results

    _check_backend()
    started = monotonic()
    sessions = _shadow_targets(args)
    pack = _v1_pack()
    incomplete = 0
    with observe_results() as results:
        with ThreadPoolExecutor(max_workers=settings.LLM_PARALLEL) as pool:
            futures = [pool.submit(copy_context().run, _warm, session, pack, args.s1_only)
                       for session in sessions]
            for future in futures:
                computed = future.result()
                if computed is not None and computed.cache_missing:
                    incomplete += 1
    counts = _print_summary(results, started, targets=len(sessions))
    if incomplete:
        print(f"incomplete_targets={incomplete}: full shadow could not complete every required step",
              file=sys.stderr)
    return int(bool(counts["errors"] or counts["offline_misses"] or incomplete))


def _print_summary(results, started, *, targets=None):
    counts = dict(calls_made=0, cache_hits=0, offline_misses=0, errors=0)
    for result in results:
        if result.cache_hit:
            counts["cache_hits"] += 1
        elif result.error is not None and result.error.kind == "off":
            counts["offline_misses"] += 1
        else:
            counts["calls_made"] += 1
            counts["errors"] += int(result.error is not None)
    prefix = "" if targets is None else f"targets={targets} "
    print(prefix + " ".join(f"{name}={value}" for name, value in counts.items())
          + f" elapsed_s={monotonic() - started:.3f}")
    return counts


def _ensure_ledger():
    import ledger

    if not paths.ledger_db().is_file():
        ledger.init_db(paths.ledger_db())
    return ledger


def _print_result(result):
    by_key = {verdict.atom_key: verdict for verdict in result.verdicts}
    for turn in result.session.turns:
        parts = [f"{turn.turn_id} {result.turn_verdicts[turn.turn_id]}"]
        for atom in result.atoms:
            if atom.turn_id == turn.turn_id:
                verdict = by_key[f"{result.session.session_id}:{atom.atom_id}"]
                why = verdict.reason_code or verdict.decided_by
                parts.append(f"{atom.atom_id} {verdict.verdict} {why}")
        print(" | ".join(parts))


def _case(case_id):
    from pipeline.ingest import from_eval_case
    from pipeline.llm import run_scope
    from pipeline.runner import _compute

    case = next((row for row in _json_lines(paths.GOLDEN_CASES) if row["case_id"] == case_id), None)
    if case is None:
        raise ValueError(f"Unknown golden case: {case_id}")

    def trace(kind, payload):
        print(json.dumps({"type": kind, **payload}, ensure_ascii=False, sort_keys=True))

    with run_scope(offline=True, measuring=True):
        result = _compute(from_eval_case(case), _v1_pack(), 1, "live", trace)
    _print_result(result)


def _judge_repeat(args):
    from dataclasses import asdict
    from pydantic import BaseModel
    import ledger
    from pipeline.context import build_context
    from pipeline.drafts import VerdictDraft
    from pipeline.judge import judge
    from pipeline.llm import observe_results, run_scope
    from pipeline.router import axis_groups
    from pipeline.runner import _live_pack

    if args.n < 1:
        raise ValueError("--n must be positive")
    if not paths.ledger_db().is_file():
        raise ValueError("judge-repeat requires an existing ledger")
    session = ledger.get_session(args.session)
    if session is None:
        raise ValueError("Session not found")
    if not any(turn.turn_id == args.turn for turn in session.turns):
        raise ValueError("Turn not found")
    atoms = [atom for atom in session.atoms if atom.turn_id == args.turn]
    if not atoms:
        raise ValueError("Turn has no stored atoms")
    pack, _ = _live_pack()
    ctx = build_context(session.sku_pin, session.as_of, pack)
    started = monotonic()
    with run_scope(measuring=True, local_writes=True), observe_results() as results:
        for iteration in range(1, args.n + 1):
            for atom in atoms:
                draft = judge(atom, ctx.prompt_view(axis_groups(atom.axis)), ctx,
                              "live", cache_read=False)
                if isinstance(draft, VerdictDraft):
                    draft = asdict(draft)
                elif isinstance(draft, BaseModel):
                    draft = draft.model_dump()
                if draft["verdict"] == "red" or draft["decided_by"].startswith("rule:"):
                    raise ValueError("Only code rules may decide red")
                print(f"repeat={iteration} {session.session_id}:{atom.atom_id} "
                      f"{draft['verdict']} {draft['decided_by']}")
    counts = _print_summary(results, started)
    return int(bool(counts["errors"] or counts["offline_misses"]))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        from pipeline.llm import observe_results

        print(config_line())
        if args.command == "shadow":
            return _shadow(args)
        if args.command == "case":
            _case(args.id)
            return 0
        if args.command == "judge-repeat":
            return _judge_repeat(args)
        ledger = _ensure_ledger()
        from pipeline.runner import process_session

        started = monotonic()
        with observe_results() as results:
            if args.command == "process":
                result = process_session(args.session, args.purpose,
                                         reprocess=args.reprocess or args.fresh,
                                         cache_read=not args.fresh)
                _print_result(result)
            elif args.command == "kera":
                from pipeline.ingest import from_reenactment

                session = from_reenactment(paths.KERA_DIR / "reenactment_script.md", "kera", args.as_of)
                stored = ledger.add_session(session)
                _print_result(process_session(stored.session_id, "live", reprocess=args.reprocess))
            else:
                from pipeline.ingest import from_text

                for text in args.file.read_text(encoding="utf-8").splitlines():
                    if not text.strip():
                        continue
                    session = ledger.add_session(from_text(args.sku, args.as_of, text, args.source))
                    result = process_session(session.session_id, "live")
                    print(session.session_id)
                    _print_result(result)
                    for atom, verdict in zip(result.atoms, result.verdicts, strict=True):
                        patterns = ", ".join(hit.pattern for hit in atom.near_hits)
                        print(f"{verdict.atom_key} {verdict.verdict} "
                              f"{verdict.reason_code or verdict.decided_by} near_hits={patterns}")
        counts = _print_summary(results, started)
        return int(bool(counts["errors"]))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        if exc.name not in {"ledger", "pipeline.ingest", "pipeline.extract", "pipeline.judge",
                            "pipeline.guardrail", "pipeline.aggregate", "rules.nearhit"}:
            raise
        print(f"not ready: {exc.name}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
