"""Synchronous recall, atom-rule quarantine and cache-only backfill.

App callers run maintenance with asyncio.to_thread or a background thread.
auto_run only starts a daemon worker; maintenance never calls asyncio.run.
"""

from datetime import date, datetime, timezone
import sys
from threading import Thread
import traceback

from core.enums import AtomFlag, ReasonCode, Verdict as Colour


def _now():
    return datetime.now(timezone.utc).isoformat()


def auto_run(session_id):
    """Return immediately; processing and errors stay in the worker thread."""
    def run():
        try:
            from pipeline.runner import process_session

            process_session(session_id, "live")
        except Exception:
            traceback.print_exc(file=sys.stderr)

    Thread(target=run, daemon=True, name="process-session").start()


def _recall_state():
    """Read durable recall inputs, ignoring event order, duplicates and UI data."""
    import ledger

    recalls, cursor = set(), 0
    while rows := ledger.events_since(cursor):
        for row in rows:
            if row.type == "recall.applied":
                recalls.add((row.payload["sku_id"], date.fromisoformat(row.payload["recall_date"])))
        cursor = rows[-1].id
    return tuple(sorted(recalls))


def _eligible(row):
    return row.verdict == Colour.GREEN and row.decided_by in ("allow_list", "llm_votes")


def _mark_recalled(session, verdicts, recalls):
    """Annotate current finalized decisions without changing context or prompts."""
    if any(sku == session.sku_pin and session.as_of < day for sku, day in recalls):
        for row in verdicts:
            if _eligible(row) and AtomFlag.RECALLED_AFTER not in row.flags:
                row.flags = [*row.flags, AtomFlag.RECALLED_AFTER]


def apply_recall(sku_id, recall_date, source):
    import ledger
    from textnorm import nfc

    day = date.fromisoformat(recall_date) if isinstance(recall_date, str) else recall_date
    if type(day) is not date:
        raise ValueError("recall_date must be a date")
    sku_id, source = nfc(sku_id), nfc(source)
    sessions, banners = [], {}
    with ledger.transaction():
        for session in ledger.list_sessions():
            if session.sku_pin != sku_id or session.as_of >= day:
                continue
            eligible = [row for row in session.verdicts
                        if _eligible(row) and AtomFlag.RECALLED_AFTER not in row.flags]
            ledger.append_verdicts([row.model_copy(deep=True, update={
                "verdict_id": None, "supersedes": row.verdict_id,
                "flags": [*row.flags, AtomFlag.RECALLED_AFTER], "ts": _now(),
            }) for row in eligible])
            count = len(eligible)
            sessions.append(dict(session_id=session.session_id, flagged_atoms=count))
            banners[session.session_id] = dict(
                text=f"Dossier recalled on {day.isoformat()}, after the "
                     f"{session.as_of.isoformat()} broadcast: {count} claims that were green "
                     "need review. No human needed to apply this.",
                source=source,
            )
        payload = dict(sku_id=sku_id, recall_date=day.isoformat(), sessions=sessions,
                       source=source, banners=banners)
        ledger.append_event("recall.applied", payload)
    return payload


def quarantine(rule_id, reason):
    import ledger
    from rules.registry import load_pack

    info, = load_pack([], quarantined=[rule_id], mode="live").quarantined
    if "check_session" in info.module.__dict__ or "check" not in info.module.__dict__:
        raise ValueError(f"Quarantine accepts only atom rules; {rule_id} is a session rule")
    with ledger.transaction():
        current = ledger.current_pack()
        if rule_id in current.quarantined:
            return dict(rule_id=rule_id, reason=reason, affected=0, version=current.version)
        if rule_id not in current.active:
            raise ValueError("Only an active rule can be quarantined")
        sessions = ledger.list_sessions()
        pack = ledger.create_pack_version(
            [key for key in current.active if key != rule_id],
            [*current.quarantined, rule_id], "quarantine", rule_id=rule_id)
        changed = []
        for session in sessions:
            for row in session.verdicts:
                if row.decided_by != f"rule:{rule_id}":
                    continue
                changed.append(row.model_copy(deep=True, update={
                    "verdict_id": None, "supersedes": row.verdict_id,
                    "rule_pack_version": pack.version, "verdict": Colour.ABSTAIN,
                    "decided_by": "abstain:RULE_QUARANTINED",
                    "reason_code": ReasonCode.RULE_QUARANTINED,
                    "flags": [AtomFlag.PREV_RED], "fired_rules": [],
                    "permitted_id": None, "closest_permitted_id": None,
                    "evidence_span": None, "numbers": None, "legal_basis": [],
                    "law_span": None, "qlnd_category": None,
                    "action": "Send to review queue", "ts": _now(),
                }))
        ledger.append_verdicts(changed)
        payload = dict(rule_id=rule_id, reason=reason, affected=len(changed), version=pack.version)
        ledger.append_event("rule.quarantined", payload)
    return payload


def backfill(pack):
    import ledger
    from pipeline.runner import replay

    ids = [row.session_id for row in ledger.list_sessions()]
    current = ledger.current_pack()
    if ([rule.rule_id for rule in pack.active] != current.active
            or [rule.rule_id for rule in pack.quarantined] != current.quarantined):
        raise ValueError("Backfill requires the current ledger rule pack")
    version = current.version

    def emit(batch):
        if len(batch) != len(ids) or any(row.status != "ok" for row in batch):
            raise ValueError("Backfill replay incomplete; fill the cache and replay again")
        if [row.session_id for row in ledger.list_sessions()] != ids:
            raise ValueError("Stored session set changed during replay; replay again")
        ledger.append_event("backfill.done", dict(
            version=version, changed_cases=batch.changed_cases, changed_atoms=batch.changed_atoms))

    batch = replay(ids, pack, persist=True, pack_version=version, _on_persist=emit)
    return dict(version=version, changed_cases=batch.changed_cases, changed_atoms=batch.changed_atoms)
