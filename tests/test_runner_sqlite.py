"""T5 integration through the real ledger, context, router, H2 and aggregation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

import ledger
from core import paths
from core.config import settings
from core.enums import AtomFlag
from core.models import Atom, Session, Turn
from pipeline import llm, runner
from pipeline.drafts import VerdictDraft
from rules.registry import load_pack


def session(session_id="sqlite-fixture", text="alpha beta gamma"):
    return Session(session_id=session_id, sku_pin="kera", as_of=date(2025, 1, 8),
                   channel="written", source="Synthetic integration test", turns=[
                       Turn(turn_id="t01", speaker_label="[KOL 1]",
                            attribution="constructed", text=text)])


def count(table):
    with sqlite3.connect(paths.ledger_db()) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def integrated(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(settings, "LLM_LOCAL_CACHE_DIR", str(tmp_path / "local-cache"))
    monkeypatch.setattr(settings, "LLM_BACKEND", "codex")
    monkeypatch.setattr(settings, "OFFLINE", 0)
    ledger.init_db(tmp_path / "ledger.db")
    state = SimpleNamespace(spans=None, extracts=0, colour="green", calls=[], cache_reads=[],
                            barrier=None)

    def backend(*args):
        assert ledger._connection.get() is None, "LLM ran in the persistence transaction"
        state.calls.append(args[5])
        return llm._Transport(response={"ok": True})
    monkeypatch.setattr(llm, "_backend", backend)

    def request(kind, text, purpose, cache_read):
        state.cache_reads.append((kind, cache_read))
        return dict(kind=kind, purpose=purpose, model="synthetic-" + kind, effort="none",
                    schema_name="test_response", cache_read=cache_read,
                    messages=[dict(role="user", content=text)])

    def extract(value, turn, previous, ctx, purpose="live", cache_read=True):
        assert ledger._connection.get() is None
        state.extracts += 1
        result = llm.call(**request("s1", turn.text, purpose, cache_read))
        spans = [turn.text] if state.spans is None else state.spans
        return [Atom(atom_id=f"{turn.turn_id}.a{i}", turn_id=turn.turn_id, span=text,
                     start=turn.text.index(text), end=turn.text.index(text) + len(text),
                     axis="thanh_phan", speaker_role="khong_ro", subject_is_product=True,
                     s1_call_id=result.request_sha256 if result.error is None else None)
                for i, text in enumerate(spans, 1)]

    def judge(value, items, ctx, purpose, cache_read=True, *, timeout_s=None):
        assert ledger._connection.get() is None
        if timeout_s is not None:
            assert timeout_s == 60
        votes = llm.map_calls([request(kind, value.span, purpose, cache_read)
                              for kind in ("t1", "t2")], parallel=2)
        if state.barrier is not None:
            state.barrier.wait(timeout=10)
        ids = [vote.request_sha256 for vote in votes]
        if any(vote.error for vote in votes):
            return VerdictDraft("abstain", "abstain:LLM_OFF", reason_code="LLM_OFF",
                                llm_call_ids=ids)
        if state.colour == "yellow":
            return VerdictDraft("yellow", "llm_votes", family="R05",
                                closest_permitted_id=items[0]["permitted_id"], llm_call_ids=ids)
        return VerdictDraft("green", "llm_votes", family="STANDARD",
                            permitted_id=items[0]["permitted_id"], llm_call_ids=ids)

    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=extract))
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    # T4 is not merged here; only its pure case-to-session adapter is substituted.
    monkeypatch.setitem(sys.modules, "pipeline.ingest", SimpleNamespace(
        from_eval_case=lambda row: session("case-" + row["case_id"], row["text"])))
    return state


def test_process_reprocess_real_sqlite_preserves_history_and_search(integrated):
    state = integrated
    ledger.add_session(session())
    state.spans = ["alpha", "beta"]
    first = runner.process_session("sqlite-fixture", "live")
    assert first.case_decided_by == "llm_votes" and first.case_verdict == "green"
    assert count("atoms") == count("verdicts") == 2 and count("search_fts") == 3
    state.spans = ["gamma"]
    skipped = runner.process_session("sqlite-fixture", "live")
    assert state.extracts == 1 and skipped.case_decided_by == "llm_votes"
    assert [atom.span for atom in skipped.atoms] == ["alpha", "beta"]
    updated = runner.process_session("sqlite-fixture", "live", reprocess=True)
    stored = ledger.get_session("sqlite-fixture")
    assert isinstance(stored, ledger.SessionView)
    assert [atom.span for atom in stored.atoms] == ["gamma"]
    assert len(stored.verdicts) == 1 and count("verdicts") == 3 and count("search_fts") == 2
    assert updated.verdicts[0].supersedes == first.verdicts[0].verdict_id
    assert not [row for row in ledger.search("alpha") if row["kind"] == "atom"]
    assert [row["snippet"] for row in ledger.search("gamma") if row["kind"] == "atom"] == ["gamma"]


@pytest.mark.parametrize("operation", ["replay", "reprocess"])
def test_rule_off_replaces_real_session_flags(integrated, operation):
    ledger.create_pack_version(["R06"], [], "merge", approved_by="hungkhuu404")
    ledger.add_session(session().model_copy(update={"sku_pin": "non_alco"}))
    # Initial processing warms S1 before the cache-only replay.
    first = runner.process_session("sqlite-fixture", "live")
    assert [flag.rule_id for flag in first.flags] == ["R06"]
    assert first.case_verdict == "red" and first.case_decided_by == "rule:R06"
    assert ledger.get_session("sqlite-fixture").flags == first.flags
    assert count("session_flags") == count("verdicts") == 1
    calls = list(integrated.calls)
    pack = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    if operation == "replay":
        results = runner.replay(["sqlite-fixture"], persist=True)
        assert (results.changed_cases, results.changed_atoms) == (1, 0)
        result, = results
        assert result.status == "ok"
        assert ledger.get_session("sqlite-fixture").verdicts == first.verdicts
        expected_count = 1
    else:
        result = runner.process_session("sqlite-fixture", "live", reprocess=True)
        assert result.flags == [] and result.verdicts[0].rule_pack_version == pack.version
        assert result.verdicts[0].supersedes == first.verdicts[0].verdict_id
        expected_count = 2
    stored = ledger.get_session("sqlite-fixture")
    assert stored.flags == [] and count("session_flags") == 0
    assert [row.reason_code for row in stored.verdicts] == ["DOSSIER_EMPTY"]
    assert runner.process_session("sqlite-fixture", "live").case_verdict == result.case_verdict == "abstain"
    assert ledger.get_verdict(first.verdicts[0].verdict_id) == first.verdicts[0]
    repeated = runner.replay(["sqlite-fixture"], persist=True)
    assert repeated[0].status == "ok" and repeated[0].case_verdict == "abstain"
    assert (repeated.changed_cases, repeated.changed_atoms) == (0, 0)
    assert ledger.get_session("sqlite-fixture") == stored
    assert count("session_flags") == 0 and count("verdicts") == expected_count
    assert integrated.calls == calls


def test_process_reports_missing_session(integrated):
    with pytest.raises(ValueError, match="Session not found"):
        runner.process_session("missing", "live")


@pytest.mark.parametrize("reprocess", [False, True])
def test_concurrent_processing_serializes_persistence_and_history(integrated, reprocess):
    ledger.add_session(session())
    if reprocess:
        runner.process_session("sqlite-fixture", "live")
    before_events = len(ledger.events_since(0))
    integrated.barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runner.process_session, "sqlite-fixture", "live", reprocess)
                   for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    stored = ledger.get_session("sqlite-fixture")
    assert count("atoms") == 1 and count("search_fts") == 2
    assert [atom.span for atom in stored.atoms] == ["alpha beta gamma"]
    with sqlite3.connect(paths.ledger_db()) as conn:
        history = conn.execute("SELECT verdict_id,supersedes FROM verdicts ORDER BY verdict_id").fetchall()
    assert history == ([(1, None), (2, 1), (3, 2)] if reprocess else [(1, None)])
    assert stored.verdicts[0].verdict_id == history[-1][0]
    assert count("verdicts") == len(history)
    events = ledger.events_since(0)[before_events:]
    assert sum(row.type == "verdict" for row in events) == (2 if reprocess else 1)
    if reprocess:
        by_id = {result.verdicts[0].verdict_id: result.verdicts[0].supersedes for result in results}
        assert by_id == {2: 1, 3: 2}
    else:
        for result in results:
            assert result.atoms == stored.atoms and result.verdicts == stored.verdicts
            assert result.flags == stored.flags
            assert (result.case_verdict, result.case_decided_by) == ("green", "llm_votes")


def test_reprocess_restored_atom_links_to_latest_historical_verdict(integrated):
    ledger.add_session(session())
    integrated.spans = ["alpha", "beta"]
    first = runner.process_session("sqlite-fixture", "live")
    integrated.spans = ["gamma"]
    runner.process_session("sqlite-fixture", "live", reprocess=True)
    integrated.spans = ["alpha", "beta"]
    restored = runner.process_session("sqlite-fixture", "live", reprocess=True)
    assert restored.verdicts[1].supersedes == first.verdicts[1].verdict_id
    assert restored.verdicts == ledger.get_session("sqlite-fixture").verdicts


@pytest.mark.parametrize("reprocess", [False, True])
def test_pack_change_during_compute_rejects_stale_persistence(integrated, monkeypatch, reprocess):
    ledger.add_session(session())
    if reprocess:
        runner.process_session("sqlite-fixture", "live")
    before = ledger.get_session("sqlite-fixture").model_dump()
    events = ledger.events_since(0)
    verdicts = count("verdicts")
    extract = sys.modules["pipeline.extract"].extract_atoms
    def change_pack(*args, **kwargs):
        atoms = extract(*args, **kwargs)
        assert ledger._connection.get() is None
        ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
        return atoms

    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=change_pack))
    with pytest.raises(ValueError, match="Rule pack changed during computation"):
        runner.process_session("sqlite-fixture", "live", reprocess=reprocess)
    assert ledger.get_session("sqlite-fixture").model_dump() == before
    assert ledger.events_since(0) == events and count("verdicts") == verdicts


def test_reprocess_real_sqlite_rolls_back_atoms_verdicts_search_and_events(integrated, monkeypatch):
    state = integrated
    ledger.create_pack_version(["R06"], [], "merge", approved_by="hungkhuu404")
    ledger.add_session(session().model_copy(update={"sku_pin": "non_alco"}))
    state.spans = ["alpha", "beta"]
    runner.process_session("sqlite-fixture", "live")
    before = ledger.get_session("sqlite-fixture").model_dump()
    assert [flag["rule_id"] for flag in before["flags"]] == ["R06"]
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    events = ledger.events_since(0)
    original = ledger.append_event
    def fail(kind, payload):
        if kind == "turn.verdict":
            assert ledger.get_session("sqlite-fixture").flags == []
            raise RuntimeError("synthetic final persistence failure")
        return original(kind, payload)
    monkeypatch.setattr(ledger, "append_event", fail)
    state.spans = ["gamma"]
    with pytest.raises(RuntimeError, match="final persistence failure"):
        runner.process_session("sqlite-fixture", "live", reprocess=True)
    assert ledger.get_session("sqlite-fixture").model_dump() == before
    assert ledger.events_since(0) == events
    assert count("verdicts") == 2 and count("search_fts") == 3


def test_backfill_effective_rows_counts_approval_and_no_duplicate_writes(integrated):
    state = integrated
    ledger.add_session(session())
    first = runner.process_session("sqlite-fixture", "live")
    flagged = first.verdicts[0].model_copy(update={"flags": [AtomFlag.RECALLED_AFTER]})
    previous_id, = ledger.append_verdicts([flagged])
    historical = ledger.get_verdict(previous_id)
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    extracts = state.extracts
    skipped = runner.process_session("sqlite-fixture", "live")
    assert state.extracts == extracts and skipped.verdicts[0].rule_pack_version == 1
    rules = load_pack([], mode="live")
    cleared = runner.replay(["sqlite-fixture"], rules, persist=True, pack_version=2)
    assert isinstance(cleared, list) and cleared[0].status == "ok"
    assert (cleared.changed_cases, cleared.changed_atoms) == (1, 1)
    cleared_verdict, = ledger.effective_verdicts("sqlite-fixture")
    assert cleared_verdict.verdict == "green" and cleared_verdict.flags == []
    assert cleared_verdict.supersedes == previous_id and cleared_verdict.rule_pack_version == 2
    assert ledger.get_verdict(previous_id) == historical
    unchanged = runner.replay(["sqlite-fixture"], rules, persist=True, pack_version=2)
    assert (unchanged.changed_cases, unchanged.changed_atoms) == (0, 0)
    assert count("verdicts") == 3
    state.colour = "yellow"
    changed = runner.replay(["sqlite-fixture"], rules, persist=True, pack_version=2)
    assert (changed.changed_cases, changed.changed_atoms) == (1, 1)
    effective, = ledger.effective_verdicts("sqlite-fixture")
    assert effective.verdict == "yellow" and effective.rule_pack_version == 2
    assert effective.supersedes == cleared_verdict.verdict_id and effective.flags == []
    repeated = runner.replay(["sqlite-fixture"], rules, persist=True, pack_version=2)
    assert (repeated.changed_cases, repeated.changed_atoms) == (0, 0)
    assert count("verdicts") == 4
    earlier, = ledger.effective_verdicts("sqlite-fixture", 1)
    assert earlier.verdict == "green" and earlier == historical
    assert ledger.get_verdict(first.verdicts[0].verdict_id) == first.verdicts[0]


def test_backfill_uses_only_current_quarantine_flags_and_keeps_history(integrated):
    quarantined = ledger.create_pack_version([], ["R04"], "quarantine", rule_id="R04",
                                             approved_by="hungkhuu404")
    ledger.add_session(session(text="chữa bệnh"))
    # Shadow warms E even though the quarantined rule decides the verdict.
    first = runner.process_session("sqlite-fixture", "shadow")
    original = first.verdicts[0]
    assert original.flags == [AtomFlag.PREV_RED] and original.reason_code == "RULE_QUARANTINED"
    assert ledger.get_session("sqlite-fixture").verdicts == [original]
    calls = list(integrated.calls)
    unflagged = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    cleared = runner.replay(["sqlite-fixture"], persist=True)
    assert cleared[0].status == "ok" and cleared[0].case_verdict == "green"
    assert (cleared.changed_cases, cleared.changed_atoms) == (1, 1)
    current, = ledger.get_session("sqlite-fixture").verdicts
    assert current.verdict == "green" and current.flags == []
    assert current.supersedes == original.verdict_id and current.rule_pack_version == unflagged.version
    assert ledger.get_verdict(original.verdict_id) == original and count("verdicts") == 2
    earlier, = ledger.effective_verdicts("sqlite-fixture", quarantined.version)
    assert earlier == original
    repeated = runner.replay(["sqlite-fixture"], persist=True)
    assert (repeated.changed_cases, repeated.changed_atoms) == (0, 0)
    assert count("verdicts") == 2

    pack = ledger.create_pack_version([], ["R04"], "quarantine", rule_id="R04",
                                      approved_by="hungkhuu404")
    generated = runner.replay(["sqlite-fixture"], persist=True)
    assert generated[0].status == "ok" and generated[0].case_verdict == "abstain"
    assert (generated.changed_cases, generated.changed_atoms) == (1, 1)
    flagged, = ledger.get_session("sqlite-fixture").verdicts
    assert generated[0].verdicts == [flagged]
    assert flagged.flags == [AtomFlag.PREV_RED] and flagged.reason_code == "RULE_QUARANTINED"
    assert flagged.supersedes == current.verdict_id and flagged.rule_pack_version == pack.version
    assert ledger.get_verdict(original.verdict_id) == original
    assert ledger.get_verdict(current.verdict_id) == current and count("verdicts") == 3
    repeated = runner.replay(["sqlite-fixture"], persist=True)
    assert (repeated.changed_cases, repeated.changed_atoms) == (0, 0)
    assert ledger.get_session("sqlite-fixture").verdicts == [flagged] and count("verdicts") == 3
    assert integrated.calls == calls


def test_backfill_across_sessions_is_one_atomic_transaction(integrated, monkeypatch):
    state = integrated
    ledger.create_pack_version(["R06"], [], "merge", approved_by="hungkhuu404")
    for name in ("first", "second"):
        value = session(name)
        if name == "first":
            value = value.model_copy(update={"sku_pin": "non_alco"})
        ledger.add_session(value)
        runner.process_session(name, "live")
    before = {name: ledger.get_session(name).model_dump() for name in ("first", "second")}
    assert [flag["rule_id"] for flag in before["first"]["flags"]] == ["R06"]
    pack = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    state.colour = "yellow"
    original = ledger.append_verdicts
    def fail(verdicts):
        assert ledger.get_session("first").flags == []
        original(verdicts[:1])
        raise RuntimeError("synthetic backfill failure")
    monkeypatch.setattr(ledger, "append_verdicts", fail)
    with pytest.raises(RuntimeError, match="backfill failure"):
        runner.replay(["first", "second"], load_pack([], mode="live"),
                      persist=True, pack_version=pack.version)
    assert count("verdicts") == 2
    assert {name: ledger.get_session(name).model_dump() for name in before} == before
    assert [ledger.get_session(name).verdicts[0].verdict for name in before] == ["abstain", "green"]


def test_explicit_live_pack_can_persist_in_memory_cases(integrated):
    case = dict(case_id="new", sku_id="kera", text="alpha")
    runner.run_cases([case], "shadow", load_pack([], mode="measure"), 1)
    results = runner.replay([case], load_pack([], mode="live"), persist=True, pack_version=1)
    assert (results.changed_cases, results.changed_atoms) == (1, 1)
    assert ledger.get_session("case-new").verdicts[0].verdict == "green"
    assert count("search_fts") == 2


@pytest.mark.parametrize("purpose,mode", [("live", "measure"), ("shadow", "live"), ("oracle", "live")])
def test_persistence_rejects_measure_shadow_and_oracle(integrated, purpose, mode):
    with pytest.raises(ValueError):
        runner.replay([], load_pack([], mode=mode), persist=True, purpose=purpose, pack_version=1)
    assert count("verdicts") == 0 and integrated.calls == []


def test_persistence_checks_live_pack_approval_before_computation(integrated):
    rules = SimpleNamespace(mode="live", active=(SimpleNamespace(
        meta=SimpleNamespace(approved_by=None)),), quarantined=())
    with pytest.raises(ValueError, match="approved live pack"):
        runner.replay([], rules, persist=True, pack_version=1)
    assert count("verdicts") == 0 and integrated.calls == []


def test_fresh_shadow_process_forwards_reads_and_keeps_committed_cache_unchanged(integrated):
    ledger.add_session(session())
    with llm.observe_results() as initial:
        runner.process_session("sqlite-fixture", "shadow")
    committed = {path: path.read_bytes() for path in paths.CACHE_DIR.glob("*/*.json")}
    integrated.cache_reads.clear()
    with llm.observe_results() as fresh:
        runner.process_session("sqlite-fixture", "shadow", reprocess=True, cache_read=False)
    assert sorted(integrated.cache_reads) == [("s1", False), ("t1", False), ("t2", False)]
    assert {path: path.read_bytes() for path in paths.CACHE_DIR.glob("*/*.json")} == committed
    assert {row.request_sha256 for row in initial} == {row.request_sha256 for row in fresh}
    assert all(not row.cache_hit for row in fresh)
    assert len(list(paths.local_cache_dir().glob("*/*.json"))) == 3


def test_judge_repeat_is_fresh_sequential_and_does_not_write_ledger(integrated, monkeypatch, capsys):
    from pipeline import cli

    ledger.add_session(session())
    runner.process_session("sqlite-fixture", "shadow")
    monkeypatch.setattr(cli, "config_line", lambda: "cfg=synthetic")
    counts = {table: count(table) for table in ("llm_calls", "verdicts", "atoms", "events", "search_fts")}
    committed = {path: path.read_bytes() for path in paths.CACHE_DIR.glob("*/*.json")}
    integrated.cache_reads.clear()
    assert cli.main(["judge-repeat", "--session", "sqlite-fixture", "--turn", "t01", "--n", "2"]) == 0
    output = capsys.readouterr().out
    assert "repeat=1 sqlite-fixture:t01.a1 green llm_votes" in output
    assert "repeat=2 sqlite-fixture:t01.a1 green llm_votes" in output
    assert "calls_made=4 cache_hits=0 offline_misses=0 errors=0" in output
    assert integrated.cache_reads == [("t1", False), ("t2", False)] * 2
    assert {table: count(table) for table in counts} == counts
    assert {path: path.read_bytes() for path in paths.CACHE_DIR.glob("*/*.json")} == committed
