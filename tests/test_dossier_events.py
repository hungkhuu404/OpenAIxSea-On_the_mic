"""Dossier maintenance through the real SQLite ledger, context, router and F."""

import asyncio
from contextlib import contextmanager
from datetime import date
from importlib import reload
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

import ledger
from core import paths
from core.enums import AtomFlag, Verdict as Colour
from core.models import Atom, Session, Turn, Verdict
from pipeline import context, dossier_events as events, llm, runner
from pipeline.drafts import VerdictDraft
from rules.registry import load_pack


OLD_TS = "2025-01-08T00:00:00Z"


def database():
    with sqlite3.connect(paths.ledger_db()) as conn:
        return {table: conn.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid").fetchall()
                for table in ("sessions", "turns", "atoms", "verdicts", "events", "rule_packs",
                              "pending_dossier", "session_flags", "search_fts")}


def runtime_pack():
    row = ledger.current_pack()
    return load_pack(row.active, quarantined=row.quarantined, mode="live")


def store(sid="one", texts=("new benefit",), *, sku="kera", day="2025-01-08",
          colours=None, deciders=None):
    session = Session(session_id=sid, sku_pin=sku, as_of=day, channel="written",
                      source="Synthetic dossier test", turns=[Turn(
                          turn_id=f"t{i:02}", speaker_label="[KOL 1]", attribution="constructed",
                          text=text) for i, text in enumerate(texts, 1)])
    ledger.add_session(session)
    if colours is None:
        runner.process_session(sid, "live")
        return ledger.get_session(sid)
    ctx = context.build_context(sku, day, runtime_pack())
    atoms = [Atom(atom_id=f"{turn.turn_id}.a1", turn_id=turn.turn_id, span=turn.text,
                  start=0, end=len(turn.text), axis="cong_dung", speaker_role="khong_ro",
                  subject_is_product=True) for turn in session.turns]
    ledger.add_atoms(sid, atoms)
    verdicts = []
    for index, (atom, colour) in enumerate(zip(atoms, colours, strict=True)):
        decider = (deciders[index] if deciders else
                   {"green": "allow_list", "yellow": "llm_votes", "red": "rule:R04",
                    "abstain": "abstain:LLM_NEI"}[colour])
        verdicts.append(Verdict(
            atom_key=f"{sid}:{atom.atom_id}", rule_pack_version=ledger.current_pack().version,
            verdict=colour, decided_by=decider, reason_code="LLM_NEI" if colour == "abstain" else None,
            as_of=day, dossier_hash=ctx.dossier_hash, ts=OLD_TS))
    ledger.append_verdicts(verdicts)
    return ledger.get_session(sid)


@pytest.fixture
def db(monkeypatch, tmp_path):
    ledger.init_db(tmp_path / "ledger.db")
    ledger.create_pack_version(["R04", "R06"], [], "merge", approved_by="test-owner")
    state = SimpleNamespace(colour="green", missing_s1=set(), missing_e=set(), hook=None)

    def forbidden(*args, **kwargs):
        pytest.fail("LLM transport or lookup reached by synthetic dossier tests")

    monkeypatch.setattr(llm, "call", forbidden)
    monkeypatch.setattr(llm, "_backend", forbidden)

    def extract(session, turn, previous, ctx, purpose="live", cache_read=True):
        assert ledger._connection.get() is None, "S1 lookup ran inside writer transaction"
        assert cache_read
        if state.hook is not None:
            hook, state.hook = state.hook, None
            hook()
        if session.session_id in state.missing_s1:
            llm._completed(SimpleNamespace(kind="s1", error=object()))
        return [Atom(atom_id=f"{turn.turn_id}.a1", turn_id=turn.turn_id, span=turn.text,
                     start=0, end=len(turn.text), axis="cong_dung", speaker_role="khong_ro",
                     subject_is_product=True)]

    def judge(atom, items, ctx, purpose, cache_read=True):
        assert ledger._connection.get() is None, "E lookup ran inside writer transaction"
        assert cache_read
        if atom.span in state.missing_e:
            return VerdictDraft("abstain", "abstain:LLM_OFF", reason_code="LLM_OFF")
        if state.colour == "abstain":
            return VerdictDraft("abstain", "abstain:LLM_NEI", reason_code="LLM_NEI")
        if state.colour == "yellow":
            return VerdictDraft("yellow", "llm_votes", family="R05",
                                closest_permitted_id=items[0]["permitted_id"])
        return VerdictDraft("green", "llm_votes", family="STANDARD",
                            permitted_id=items[0]["permitted_id"])

    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=extract))
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    return state


def test_recall_eligible_effective_rows_per_session_and_idempotency(db, monkeypatch):
    first = store("clip", ("a", "b", "c", "d", "e"),
                  colours=["green"] * 3 + ["yellow", "red"],
                  deciders=["allow_list", "llm_votes", "allow_list", "llm_votes", "rule:R04"])
    store("reenactment", ("f",), colours=["green"])
    excluded = [store("on-date", day="2025-03-24", colours=["green"]),
                store("future", day="2025-04-01", colours=["green"]),
                store("other", sku="non_alco", colours=["green"])]
    ledger.create_pack_version(["R04"], [], "merge", approved_by="test-owner")
    original_transaction, writers = ledger.transaction, []

    @contextmanager
    def transaction():
        if ledger._connection.get() is None:
            writers.append(True)
        with original_transaction() as conn:
            yield conn

    monkeypatch.setattr(ledger, "transaction", transaction)
    result = events.apply_recall("kera", "2025-03-24", "Synthetic recall source")
    assert len(writers) == 1
    assert result["sessions"] == [dict(session_id="clip", flagged_atoms=3),
                                  dict(session_id="reenactment", flagged_atoms=1)]
    assert result["banners"]["clip"] == dict(
        text="Dossier recalled on 2025-03-24, after the 2025-01-08 broadcast: 3 claims that "
             "were green need review. No human needed to apply this.", source="Synthetic recall source")
    assert "1 claims" in result["banners"]["reenactment"]["text"]
    assert ledger.events_since(0)[-1].type == "recall.applied"
    assert ledger.events_since(0)[-1].payload == result
    current = {row.atom_key: row for row in ledger.get_session("clip").verdicts}
    for old in first.verdicts:
        new = current[old.atom_key]
        assert ledger.get_verdict(old.verdict_id) == old
        if old.verdict == "green":
            assert new.flags == [AtomFlag.RECALLED_AFTER] and new.supersedes == old.verdict_id
            assert new.verdict_id != old.verdict_id and new.ts != OLD_TS
            mutable = {"verdict_id", "supersedes", "flags", "ts"}
            assert new.model_dump(exclude=mutable) == old.model_dump(exclude=mutable)
        else:
            assert new == old
    assert all(ledger.get_session(row.session_id) == row for row in excluded)
    before = database()["verdicts"]
    again = events.apply_recall("kera", date(2025, 3, 24), "Synthetic recall source")
    assert all(row["flagged_atoms"] == 0 for row in again["sessions"])
    assert database()["verdicts"] == before


def test_recall_is_recomputed_for_backfill_reprocess_and_later_ingest_after_reload(db):
    original = store().verdicts[0]
    events.apply_recall("kera", "2025-03-24", "Synthetic recall")
    recalled, = ledger.get_session("one").verdicts
    assert recalled.flags == [AtomFlag.RECALLED_AFTER]
    before = database()["verdicts"]
    for _ in range(2):
        assert events.backfill(runtime_pack())["changed_atoms"] == 0
        assert database()["verdicts"] == before
    reload(events)
    reload(runner)
    updated = runner.process_session("one", "live", reprocess=True).verdicts[0]
    assert updated.flags == [AtomFlag.RECALLED_AFTER] and updated.verdict == "green"
    assert updated.supersedes == recalled.verdict_id
    assert store("late-historical").verdicts[0].flags == [AtomFlag.RECALLED_AFTER]
    assert ledger.get_verdict(original.verdict_id) == original
    assert ledger.get_verdict(recalled.verdict_id) == recalled
    assert events.backfill(runtime_pack())["changed_atoms"] == 0


def test_recall_is_an_input_and_never_a_union_of_historical_flags(db):
    first = store().verdicts[0]
    stale_id, = ledger.append_verdicts([first.model_copy(update={
        "verdict_id": None, "supersedes": first.verdict_id,
        "flags": [AtomFlag.PREV_RED, AtomFlag.RECALLED_AFTER]})])
    stale = ledger.get_verdict(stale_id)
    assert events.backfill(runtime_pack())["changed_atoms"] == 1
    assert ledger.get_session("one").verdicts[0].flags == []
    assert ledger.get_verdict(stale_id) == stale
    events.apply_recall("kera", "2025-03-24", "Synthetic recall")
    current, = ledger.get_session("one").verdicts
    ledger.append_verdicts([current.model_copy(update={
        "verdict_id": None, "supersedes": current.verdict_id,
        "flags": [AtomFlag.PREV_RED, AtomFlag.RECALLED_AFTER]})])
    assert events.backfill(runtime_pack())["changed_atoms"] == 1
    assert ledger.get_session("one").verdicts[0].flags == [AtomFlag.RECALLED_AFTER]
    db.colour = "yellow"
    assert events.backfill(runtime_pack())["changed_atoms"] == 1
    assert ledger.get_session("one").verdicts[0].flags == []


def test_recall_state_paginates_ignores_unrelated_events_and_deduplicates(db):
    with ledger.transaction():
        for _ in range(501):
            ledger.append_event("step", dict(sku_id="unrelated", recall_date="not a date"))
    for day in ("2025-04-01", "2025-03-24", "2025-03-24"):
        events.apply_recall("kera", day, "Synthetic source")
    assert events._recall_state() == (("kera", date(2025, 3, 24)), ("kera", date(2025, 4, 1)))
    assert store("after-page-one").verdicts[0].flags == [AtomFlag.RECALLED_AFTER]


def test_explicit_in_memory_eval_and_oracle_never_read_runtime_recall(db, monkeypatch):
    events.apply_recall("kera", "2025-03-24", "Private synthetic source")
    pack, version = runtime_pack(), ledger.current_pack().version

    def forbidden(*args, **kwargs):
        pytest.fail("In-memory eval accessed the ledger")

    with monkeypatch.context() as patch:
        for name in ("get_session", "events_since", "current_pack", "transaction"):
            patch.setattr(ledger, name, forbidden)
        case = dict(case_id="synthetic", sku_id="kera", text="new benefit")
        for result in (runner.run_cases([case], "live", pack, version)[0],
                       runner.run_cases([case], "oracle", pack, version)[0],
                       runner.replay([case], pack, pack_version=version)[0],
                       runner.replay([case], pack, purpose="oracle", pack_version=version)[0]):
            assert result.status == "ok" and result.verdicts[0].verdict == "green"
            assert result.verdicts[0].flags == []


def test_persisted_case_dictionary_gets_recall_state(db):
    events.apply_recall("kera", "2025-03-24", "Synthetic source")
    case = dict(case_id="historical", sku_id="kera", text="new benefit")
    pack, version = runtime_pack(), ledger.current_pack().version
    result = runner.replay([case], pack, persist=True, pack_version=version)
    assert (result.changed_cases, result.changed_atoms) == (1, 1)
    assert ledger.get_session("case-historical").verdicts[0].flags == [AtomFlag.RECALLED_AFTER]
    repeated = runner.replay([case], pack, persist=True, pack_version=version)
    assert (repeated.changed_cases, repeated.changed_atoms) == (0, 0)


@pytest.mark.parametrize("operation", ["process", "reprocess", "replay", "case"])
def test_new_recall_during_compute_cannot_store_stale_green(db, monkeypatch, operation):
    if operation == "process":
        ledger.add_session(Session(session_id="one", sku_pin="kera", as_of="2025-01-08",
                                   channel="written", source="Synthetic", turns=[Turn(
                                       turn_id="t01", speaker_label="[KOL 1]",
                                       attribution="constructed", text="new benefit")]))
    elif operation != "case":
        store(colours=["yellow"])
    compute, expected = runner._compute, []

    def compute_then_recall(*args, **kwargs):
        result = compute(*args, **kwargs)
        assert result.verdicts[0].verdict == "green"
        assert ledger._connection.get() is None
        events.apply_recall("kera", "2025-03-24", "Concurrent synthetic recall")
        expected.append(database())
        return result

    monkeypatch.setattr(runner, "_compute", compute_then_recall)
    with pytest.raises(ValueError, match="Recall state changed during"):
        if operation in ("process", "reprocess"):
            runner.process_session("one", "live", reprocess=operation == "reprocess")
        else:
            target = ("one" if operation == "replay" else
                      dict(case_id="new-historical", sku_id="kera", text="new benefit"))
            runner.replay([target], persist=True)
    assert database() == expected[0]


def test_quarantine_effective_atom_rows_pack_history_and_idempotency(db):
    first = store(texts=("chữa bệnh",))
    other = store("other", colours=["red"])
    ledger.append_verdicts([other.verdicts[0].model_copy(update={
        "verdict": Colour.YELLOW, "decided_by": "llm_votes"})])
    other = ledger.get_session("other")
    result = events.quarantine("R04", "Synthetic reason")
    pack = ledger.current_pack()
    assert pack.change == "quarantine" and pack.rule_id == "R04"
    assert pack.active == ["R06"] and pack.quarantined == ["R04"]
    assert result["affected"] == 1 and ledger.events_since(0)[-1].payload == result
    new, = ledger.get_session("one").verdicts
    assert (new.verdict, new.reason_code, new.decided_by) == (
        "abstain", "RULE_QUARANTINED", "abstain:RULE_QUARANTINED")
    assert new.flags == [AtomFlag.PREV_RED] and new.supersedes == first.verdicts[0].verdict_id
    assert new.rule_pack_version == pack.version and new.ts != first.verdicts[0].ts
    assert ledger.get_verdict(first.verdicts[0].verdict_id) == first.verdicts[0]
    assert ledger.get_session("other") == other
    before = database()
    assert events.quarantine("R04", "Repeated")["affected"] == 0
    assert database() == before
    replayed = runner.replay(["one"], persist=True)
    assert (replayed.changed_cases, replayed.changed_atoms) == (0, 0)
    # Bring the unrelated synthetic row to the current computation first.
    runner.replay(["other"], persist=True)
    assert events.backfill(runtime_pack())["changed_atoms"] == 0
    assert ledger.get_session("one").verdicts == [new]


@pytest.mark.parametrize("rule_id", ["R06", "L1_PREDECL", "L1_RECALL"])
def test_session_rule_quarantine_rejected_without_any_state_write(db, monkeypatch, rule_id):
    ledger.create_pack_version(["R04", rule_id], [], "merge", approved_by="test-owner")
    store("green-atoms", colours=["green"])
    session = ledger.get_session("green-atoms")
    flags = context.build_context("non_alco", "2025-01-08", load_pack(["R06"])).session_flags
    ledger.add_session_flags(session.session_id, flags)
    before = database()

    def forbidden():
        pytest.fail("Rejected session rule opened a writer transaction")

    monkeypatch.setattr(ledger, "transaction", forbidden)
    with pytest.raises(ValueError, match="only atom rules.*session rule"):
        events.quarantine(rule_id, "Synthetic report")
    assert database() == before


def test_stale_red_replay_cannot_overwrite_actual_quarantine(db, monkeypatch):
    original = store(texts=("chữa bệnh",)).verdicts[0]
    compute, expected = runner._compute, []

    def compute_then_quarantine(*args, **kwargs):
        result = compute(*args, **kwargs)
        if not expected:
            assert result.verdicts[0].verdict == "red"
            assert events.quarantine("R04", "Concurrent report")["affected"] == 1
            expected.append(database())
        return result

    monkeypatch.setattr(runner, "_compute", compute_then_quarantine)
    with pytest.raises(ValueError, match="Rule pack changed during replay"):
        runner.replay(["one"], persist=True)
    assert database() == expected[0]
    current, = ledger.get_session("one").verdicts
    assert current.verdict == "abstain" and current.flags == [AtomFlag.PREV_RED]
    assert current.supersedes == original.verdict_id
    assert ledger.events_since(0)[-1].type == "rule.quarantined"
    assert events.backfill(runtime_pack())["changed_atoms"] == 0
    assert ledger.get_session("one").verdicts == [current]


def test_backfill_counts_changed_cases_atoms_version_and_one_event(db, monkeypatch):
    first = store().verdicts[0]
    stable = store("stable", texts=("chữa bệnh",))
    pack_row = ledger.create_pack_version(["R04", "R06"], [], "merge", approved_by="test-owner")
    db.colour = "yellow"
    calls, replay = [], runner.replay

    def record(*args, **kwargs):
        calls.append(kwargs)
        return replay(*args, **kwargs)

    monkeypatch.setattr(runner, "replay", record)
    result = events.backfill(runtime_pack())
    assert result == dict(version=pack_row.version, changed_cases=1, changed_atoms=1)
    assert calls[0]["persist"] and calls[0]["pack_version"] == pack_row.version
    new, = ledger.get_session("one").verdicts
    assert new.verdict == "yellow" and new.rule_pack_version == pack_row.version
    assert new.supersedes == first.verdict_id and ledger.get_verdict(first.verdict_id) == first
    assert ledger.get_session("stable") == stable
    emitted = [row for row in ledger.events_since(0) if row.type == "backfill.done"]
    assert len(emitted) == 1 and emitted[0].payload == result


@pytest.mark.parametrize("missing", ["s1", "e"])
def test_incomplete_cache_rolls_back_entire_backfill_without_success_event(db, missing):
    for sid in ("good", "missing"):
        store(sid, texts=(sid,))
    db.colour = "yellow"
    (db.missing_s1 if missing == "s1" else db.missing_e).add("missing")
    before = database()
    with pytest.raises(ValueError, match="replay incomplete"):
        events.backfill(runtime_pack())
    assert database() == before


def test_backfill_rejects_incomplete_session_scope(db):
    store()
    db.colour = "yellow"
    expected = []

    def add_session():
        ledger.add_session(Session(session_id="late", sku_pin="kera", as_of="2025-01-08",
                                   channel="written", source="Synthetic concurrent ingest"))
        expected.append(database())

    db.hook = add_session
    with pytest.raises(ValueError, match="session set changed during replay"):
        events.backfill(runtime_pack())
    assert database() == expected[0]


@pytest.mark.parametrize("operation", ["recall", "quarantine", "backfill"])
@pytest.mark.parametrize("failure", ["mid-verdict", "final-event"])
def test_transaction_failure_rolls_back_verdicts_pack_and_durable_event(
        db, monkeypatch, operation, failure):
    for sid in ("one", "two"):
        store(sid, texts=("chữa bệnh" if operation == "quarantine" else "new benefit",))
    db.colour = "yellow"
    before = database()
    append = ledger.append_verdicts
    event = ledger.append_event

    def fail_verdicts(rows):
        assert ledger._connection.get() is not None
        append(rows[:1])
        raise RuntimeError("Synthetic halfway failure")

    def fail_event(*args):
        assert ledger._connection.get() is not None
        event(*args)
        raise RuntimeError("Synthetic final failure")

    if failure == "mid-verdict":
        monkeypatch.setattr(ledger, "append_verdicts", fail_verdicts)
    else:
        monkeypatch.setattr(ledger, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="Synthetic .* failure"):
        if operation == "recall":
            events.apply_recall("kera", "2025-03-24", "Synthetic source")
        elif operation == "quarantine":
            events.quarantine("R04", "Synthetic report")
        else:
            events.backfill(runtime_pack())
    assert database() == before


def test_maintenance_works_through_to_thread_inside_running_loop(db):
    store()
    store("red", texts=("chữa bệnh",))

    async def check():
        recall = await asyncio.to_thread(events.apply_recall, "kera", "2025-03-24", "Synthetic")
        assert recall["sessions"][0]["flagged_atoms"] == 1
        result = await asyncio.to_thread(events.quarantine, "R04", "Synthetic")
        assert result["affected"] == 1
        result = await asyncio.to_thread(events.backfill, runtime_pack())
        assert result["changed_atoms"] == 0

    asyncio.run(check())


@pytest.mark.parametrize("fail", [False, True])
def test_auto_run_nonblocking_daemon_and_exception_logging(db, monkeypatch, capsys, fail):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    workers = []

    def process(sid, purpose):
        assert (sid, purpose) == ("synthetic", "live")
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        entered.set()
        try:
            assert release.wait(5), "auto_run blocked its caller"
            if fail:
                raise ValueError("Synthetic worker error")
        finally:
            completed.set()

    def thread(**kwargs):
        worker = threading.Thread(**kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(runner, "process_session", process)
    monkeypatch.setattr(events, "Thread", thread)

    async def check():
        try:
            assert events.auto_run("synthetic") is None
            assert await asyncio.to_thread(entered.wait, 5)
            assert not completed.is_set() and workers[0].daemon
        finally:
            release.set()
            await asyncio.to_thread(workers[0].join, 5)
        assert completed.is_set() and not workers[0].is_alive()

    before = database()
    asyncio.run(check())
    assert database() == before
    assert ("Synthetic worker error" in capsys.readouterr().err) == fail
    assert not [row for row in ledger.events_since(0) if row.type == "session.ingested"]


@pytest.mark.parametrize("path,body", [
    ("/api/dossier-events/update", dict(sku_id="kera", change={})),
    ("/api/pending/synthetic/approve", dict(approved_by="test-owner")),
])
def test_removed_dossier_mutations_remain_explicit_api_todos(db, path, body):
    from fastapi.testclient import TestClient
    from api.app import create_app

    assert "propose_dossier_update" not in events.__dict__
    assert "approve_pending" not in events.__dict__
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post(path, json=body)
    assert response.status_code == 503, response.text
    assert response.json() == {"todo": "pipeline.dossier_events"}
