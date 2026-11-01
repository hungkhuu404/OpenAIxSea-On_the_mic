"""T5 integration through the real ledger, context, router, H2 and aggregation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

import ledger
from core import paths
from core.config import settings
from core.enums import AtomFlag, Channel
from core.models import Atom, RuleHit, Session, Turn
from pipeline import llm, runner
from pipeline.drafts import VerdictDraft
from rules.registry import RuntimePack, load_pack


def session(session_id="sqlite-fixture", text="alpha beta gamma"):
    return Session(session_id=session_id, sku_pin="kera", as_of=date(2025, 1, 8),
                   channel="written", source="Synthetic integration test", turns=[
                       Turn(turn_id="t01", speaker_label="[KOL 1]",
                            attribution="constructed", text=text)])


def count(table):
    with sqlite3.connect(paths.ledger_db()) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def replay_state():
    # Cache hits may append llm_calls; compare the persisted session state exactly.
    with sqlite3.connect(paths.ledger_db()) as conn:
        return {table: conn.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid").fetchall()
                for table in ("sessions", "turns", "atoms", "verdicts", "session_flags",
                              "search_fts", "events", "rule_packs")}


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


@pytest.mark.parametrize("matched,expected", [
    ("FABRICATED QUOTE", None), ("alpha", "alpha"), ("", ""),
], ids=["outside-atom", "inside-atom", "empty"])
@pytest.mark.parametrize("scope", ["session", "turn"])
def test_red_rule_evidence_span_is_source_bound_in_sqlite(integrated, monkeypatch, matched, expected, scope):
    r04, = load_pack(["R04"], mode="live").active
    hit = RuleHit(rule_id="R04", family="R04", matched=matched,
                  law_anchor=r04.meta.anchors[0], law_span=r04.meta.law_span,
                  precedence=r04.meta.precedence, ambiguous_negation=False)
    synthetic = replace(r04, module=SimpleNamespace(check=lambda view: [hit]))
    pack = RuntimePack(active=(synthetic,), quarantined=(), mode="live")
    version = ledger.current_pack().version
    monkeypatch.setattr(runner, "_live_pack", lambda: (pack, version))

    original = ledger.add_session(session())
    result = (runner.process_turn(original.session_id, "t01", "live") if scope == "turn" else
              runner.process_session(original.session_id, "live"))
    stored = ledger.get_session(original.session_id)
    verdict, = stored.verdicts
    atom, = stored.atoms
    assert result.verdicts == stored.verdicts
    assert verdict.verdict == result.case_verdict == "red"
    assert verdict.decided_by == result.case_decided_by == "rule:R04"
    assert verdict.fired_rules == ["R04"]
    assert verdict.legal_basis == ["LDUOC-2016/D6/K15"]
    assert verdict.law_span == r04.meta.law_span
    assert verdict.evidence_span == expected
    assert atom.span == "alpha beta gamma"
    if verdict.evidence_span:
        assert verdict.evidence_span in atom.span
        assert verdict.evidence_span in original.turns[0].text


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


def two_turn_session():
    value = session()
    value.turns.append(Turn(turn_id="t02", speaker_label="[unidentified]",
                           attribution="not_applicable", text="delta epsilon"))
    return value.model_copy(update={"channel": Channel.VIDEO})


def test_process_turn_preserves_other_turn_and_real_previous_context(integrated, monkeypatch):
    value = two_turn_session()
    ledger.add_session(value)
    runner.process_session(value.session_id, "live")
    original = ledger.get_session(value.session_id)
    first_atom = original.atoms[0]
    first_verdict = original.verdicts[0]
    previous_second = original.verdicts[1]
    seen = []
    extract = sys.modules["pipeline.extract"].extract_atoms

    def record(source, turn, previous, *args, **kwargs):
        seen.append((source.session_id, source.channel, turn, previous))
        return extract(source, turn, previous, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=record))
    integrated.spans = ["epsilon"]
    before_events = len(ledger.events_since(0))
    result = runner.process_turn(value.session_id, "t02", "live", reprocess=True)
    stored = ledger.get_session(value.session_id)
    assert seen == [(value.session_id, "video", value.turns[1], value.turns[0])]
    assert result.turn_verdicts == {"t02": "green"}
    assert stored.atoms == [first_atom, result.atoms[0]]
    assert stored.verdicts == [first_verdict, result.verdicts[0]]
    assert result.verdicts[0].supersedes == previous_second.verdict_id
    assert ledger.get_verdict(previous_second.verdict_id) == previous_second
    assert not [row for row in ledger.search("delta") if row["kind"] == "atom"]
    assert [row for row in ledger.search("alpha") if row["kind"] == "atom"]
    assert {event.payload.get("turn_id") for event in ledger.events_since(0)[before_events:]
            if event.payload.get("turn_id")} == {"t02"}
    integrated.spans = []
    runner.process_turn(value.session_id, "t02", "live", reprocess=True)
    assert ledger.get_session(value.session_id).atoms == [first_atom]
    assert ledger.get_session(value.session_id).verdicts == [first_verdict]


def test_process_turn_concurrent_identical_requests_share_model_calls(integrated, monkeypatch):
    ledger.add_session(session())
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    extract = sys.modules["pipeline.extract"].extract_atoms
    original_future = runner.Future

    class ObservedFuture(original_future):
        def result(self, *args, **kwargs):
            waiting.set()
            return super().result(*args, **kwargs)

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return extract(*args, **kwargs)

    monkeypatch.setattr(runner, "Future", ObservedFuture)
    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=slow))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runner.process_turn, "sqlite-fixture", "t01", "live", True)
        assert entered.wait(timeout=10)
        second = pool.submit(runner.process_turn, "sqlite-fixture", "t01", "live", True)
        try:
            assert waiting.wait(timeout=10)
        finally:
            release.set()
        assert first.result(timeout=20) is second.result(timeout=20)
    assert integrated.extracts == 1
    assert sorted(integrated.calls) == ["s1", "t1", "t2"]
    assert count("verdicts") == 1


def test_process_turn_concurrent_different_turns_merge_fresh_state(integrated):
    value = two_turn_session()
    ledger.add_session(value)
    integrated.barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runner.process_turn, value.session_id, turn.turn_id, "live", True)
                   for turn in value.turns]
        results = [future.result(timeout=20) for future in futures]
    stored = ledger.get_session(value.session_id)
    assert [atom.turn_id for atom in stored.atoms] == ["t01", "t02"]
    assert {row.verdict_id for row in stored.verdicts} == {
        result.verdicts[0].verdict_id for result in results}
    assert count("atoms") == count("verdicts") == 2


@pytest.mark.parametrize("second", [(True, True), (False, False)])
def test_process_turn_does_not_coalesce_different_request_modes(integrated, monkeypatch, second):
    barrier = threading.Barrier(2)

    def compute(session_id, turn_id, purpose, reprocess, cache_read):
        barrier.wait(timeout=10)
        return reprocess, cache_read

    monkeypatch.setattr(runner, "_process_turn", compute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runner.process_turn, "sqlite-fixture", "t01", "live")
        other = pool.submit(runner.process_turn, "sqlite-fixture", "t01", "live",
                            reprocess=second[0], cache_read=second[1])
        assert first.result(timeout=20) == (False, True)
        assert other.result(timeout=20) == second


@pytest.mark.parametrize("change", ["pack", "recall"])
def test_process_turn_rejects_changed_pack_or_recall(integrated, monkeypatch, change):
    ledger.add_session(session())
    extract = sys.modules["pipeline.extract"].extract_atoms
    expected = {}

    def mutate(*args, **kwargs):
        atoms = extract(*args, **kwargs)
        if change == "pack":
            ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
        else:
            from pipeline.dossier_events import apply_recall
            apply_recall("kera", "2025-03-24", "Synthetic recall")
        expected.update(replay_state())
        return atoms

    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=mutate))
    with pytest.raises(ValueError, match="changed during computation"):
        runner.process_turn("sqlite-fixture", "t01", "live", reprocess=True)
    assert replay_state() == expected


def test_process_turn_rolls_back_and_releases_failed_request(integrated, monkeypatch):
    ledger.add_session(two_turn_session())
    runner.process_session("sqlite-fixture", "live")
    before = replay_state()
    append = ledger.append_verdicts

    def fail(rows):
        append(rows)
        raise RuntimeError("Synthetic append failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append_verdicts", fail)
        with pytest.raises(RuntimeError, match="Synthetic append failure"):
            runner.process_turn("sqlite-fixture", "t02", "live", reprocess=True)
    assert replay_state() == before
    assert runner.process_turn("sqlite-fixture", "t02", "live", reprocess=True).verdicts


def test_process_turn_offline_never_calls_backend(integrated, monkeypatch):
    ledger.add_session(session())
    monkeypatch.setattr(settings, "OFFLINE", 1)
    result = runner.process_turn("sqlite-fixture", "t01", "live", reprocess=True)
    assert result.verdicts[0].reason_code == "LLM_OFF"
    assert integrated.calls == []
    with pytest.raises(ValueError, match="Turn not found"):
        runner.process_turn("sqlite-fixture", "missing", "live")


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


@pytest.mark.parametrize("interleaving", ["after_compute", "before_writer"])
def test_backfill_rejects_stale_pack_after_newer_quarantine_replay(
        integrated, monkeypatch, interleaving):
    empty = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    ledger.add_session(session(text="chữa bệnh"))
    first = runner.process_session("sqlite-fixture", "shadow").verdicts[0]
    assert first.verdict == "green" and first.rule_pack_version == empty.version
    assert sorted(integrated.calls) == ["s1", "t1", "t2"]
    calls = list(integrated.calls)
    active = ledger.create_pack_version(["R04"], [], "merge", approved_by="hungkhuu404")
    compute, transaction = runner._compute, ledger.transaction
    armed = interleaved = False
    expected = newest = None

    def quarantine_and_replay():
        nonlocal interleaved, expected, newest
        interleaved = True  # Disarm before the competing pack's writes and replay.
        assert ledger._connection.get() is None
        pack = ledger.create_pack_version([], ["R04"], "quarantine", rule_id="R04",
                                          approved_by="hungkhuu404")
        result = runner.replay(["sqlite-fixture"], persist=True)
        assert result[0].status == "ok" and result[0].case_verdict == "abstain"
        assert (result.changed_cases, result.changed_atoms) == (1, 1)
        newest, = ledger.effective_verdicts("sqlite-fixture")
        assert newest.verdict == "abstain" and newest.reason_code == "RULE_QUARANTINED"
        assert newest.flags == [AtomFlag.PREV_RED] and newest.rule_pack_version == pack.version
        assert newest.supersedes == first.verdict_id and count("verdicts") == 2
        expected = replay_state()

    def compute_then_interleave(*args, **kwargs):
        nonlocal armed
        result = compute(*args, **kwargs)
        if not interleaved:
            verdict, = result.verdicts
            assert verdict.verdict == "red" and verdict.decided_by == "rule:R04"
            assert verdict.rule_pack_version == active.version
            armed = True
            if interleaving == "after_compute":
                quarantine_and_replay()
        return result

    @contextmanager
    def interleave_before_writer():
        if armed and not interleaved:
            # Cache logging transactions have finished; the writer has not begun.
            quarantine_and_replay()
        with transaction() as conn:
            yield conn

    monkeypatch.setattr(runner, "_compute", compute_then_interleave)
    if interleaving == "before_writer":
        monkeypatch.setattr(ledger, "transaction", interleave_before_writer)
    with pytest.raises(ValueError, match="Rule pack changed during replay"):
        runner.replay(["sqlite-fixture"], persist=True)
    assert interleaved and replay_state() == expected
    assert ledger.get_session("sqlite-fixture").verdicts == [newest]
    assert ledger.effective_verdicts("sqlite-fixture", active.version) == [first]
    assert ledger.get_verdict(first.verdict_id) == first
    assert integrated.calls == calls


@pytest.mark.parametrize("text", ["chữa bệnh", "alpha"], ids=["changed", "unchanged"])
def test_backfill_rejects_explicit_stale_pack_even_without_a_diff(integrated, text):
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    ledger.add_session(session(text=text))
    first = runner.process_session("sqlite-fixture", "shadow").verdicts[0]
    assert first.verdict == "green"
    calls = list(integrated.calls)
    active = ledger.create_pack_version(["R04"], [], "merge", approved_by="hungkhuu404")
    stale = load_pack(active.active, quarantined=active.quarantined, mode="live")
    ledger.create_pack_version([], ["R04"], "quarantine", rule_id="R04",
                               approved_by="hungkhuu404")
    current = runner.replay(["sqlite-fixture"], persist=True)
    assert current[0].status == "ok"
    assert current[0].case_verdict == ("abstain" if text == "chữa bệnh" else "green")
    if text == "alpha":
        assert (current.changed_cases, current.changed_atoms) == (0, 0)
        assert ledger.effective_verdicts("sqlite-fixture") == [first]
    expected = replay_state()
    effective = ledger.effective_verdicts("sqlite-fixture")
    with pytest.raises(ValueError, match="Rule pack changed during replay"):
        runner.replay(["sqlite-fixture"], stale, persist=True, pack_version=active.version)
    assert replay_state() == expected
    assert ledger.effective_verdicts("sqlite-fixture") == effective
    assert ledger.get_verdict(first.verdict_id) == first
    assert integrated.calls == calls


def test_backfill_rejects_stale_pack_without_targets(integrated):
    version = ledger.current_pack().version
    rules = load_pack([], mode="live")
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    expected = replay_state()
    with pytest.raises(ValueError, match="Rule pack changed during replay.*again"):
        runner.replay([], rules, persist=True, pack_version=version)
    assert replay_state() == expected and integrated.calls == []


def test_backfill_stale_computation_preserves_newer_r06_session_flag(integrated, monkeypatch):
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    ledger.add_session(session().model_copy(update={"sku_pin": "non_alco"}))
    first = runner.process_session("sqlite-fixture", "shadow")
    assert first.flags == [] and first.verdicts[0].reason_code == "DOSSIER_EMPTY"
    calls = list(integrated.calls)
    compute = runner._compute
    interleaved = False
    expected = None

    def compute_then_enable_r06(*args, **kwargs):
        nonlocal interleaved, expected
        result = compute(*args, **kwargs)
        if not interleaved:
            interleaved = True
            assert result.flags == [] and ledger._connection.get() is None
            ledger.create_pack_version(["R06"], [], "merge", approved_by="hungkhuu404")
            newer = runner.replay(["sqlite-fixture"], persist=True)
            assert (newer.changed_cases, newer.changed_atoms) == (1, 0)
            assert newer[0].status == "ok" and newer[0].case_verdict == "red"
            assert [flag.rule_id for flag in ledger.get_session("sqlite-fixture").flags] == ["R06"]
            expected = replay_state()
        return result

    monkeypatch.setattr(runner, "_compute", compute_then_enable_r06)
    with pytest.raises(ValueError, match="Rule pack changed during replay.*again"):
        runner.replay(["sqlite-fixture"], persist=True)
    assert interleaved and replay_state() == expected
    assert [flag.rule_id for flag in ledger.get_session("sqlite-fixture").flags] == ["R06"]
    assert ledger.get_session("sqlite-fixture").verdicts == first.verdicts
    assert count("session_flags") == count("verdicts") == 1
    assert integrated.calls == calls


@pytest.mark.parametrize("interleaving", ["after_compute", "before_writer"])
@pytest.mark.parametrize("target_kind", ["session", "case"])
@pytest.mark.parametrize("mutation", ["history", "atoms", "flags"])
def test_backfill_rejects_same_pack_session_mutation(
        integrated, monkeypatch, interleaving, target_kind, mutation):
    from pipeline.context import build_context

    pack = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    value = session("case-concurrent")
    ledger.add_session(value)
    integrated.spans = ["alpha"]
    first = runner.process_session(value.session_id, "shadow")
    original, = first.verdicts
    flags = build_context("non_alco", value.as_of, load_pack(["R06"], mode="live")).session_flags
    assert [flag.rule_id for flag in flags] == ["R06"]
    calls = list(integrated.calls)
    integrated.colour = "yellow"
    compute, transaction = runner._compute, ledger.transaction
    armed = interleaved = False
    expected = None
    newest = original

    def mutate_session():
        nonlocal interleaved, expected, newest
        interleaved = True
        assert ledger._connection.get() is None
        with transaction():
            if mutation == "history":
                verdict = original.model_copy(update={
                    "flags": [AtomFlag.RECALLED_AFTER], "supersedes": original.verdict_id})
                verdict_id, = ledger.append_verdicts([verdict])
                newest = ledger.get_verdict(verdict_id)
            elif mutation == "atoms":
                atom = first.atoms[0].model_copy(update={"span": "beta", "start": 6, "end": 10})
                ledger.replace_atoms(value.session_id, [atom])
            else:
                ledger.replace_session_flags(value.session_id, flags)
        assert ledger.current_pack().version == pack.version
        expected = replay_state()

    def compute_then_interleave(*args, **kwargs):
        nonlocal armed
        result = compute(*args, **kwargs)
        assert result.verdicts[0].verdict == "yellow" and result.flags == []
        armed = True
        if interleaving == "after_compute":
            mutate_session()
        return result

    @contextmanager
    def interleave_before_writer():
        if armed and not interleaved:
            mutate_session()
        with transaction() as conn:
            yield conn

    monkeypatch.setattr(runner, "_compute", compute_then_interleave)
    if interleaving == "before_writer":
        monkeypatch.setattr(ledger, "transaction", interleave_before_writer)
    target = value.session_id if target_kind == "session" else dict(
        case_id="concurrent", sku_id="kera", text=value.turns[0].text)
    with pytest.raises(ValueError, match="Session changed during replay.*again"):
        runner.replay([target], persist=True)
    assert interleaved and replay_state() == expected
    assert ledger.get_session(value.session_id).verdicts == [newest]
    assert ledger.get_verdict(original.verdict_id) == original
    assert count("verdicts") == (2 if mutation == "history" else 1)
    assert newest.supersedes == (original.verdict_id if mutation == "history" else None)
    assert integrated.calls == calls


def test_backfill_rejects_explicit_case_created_during_computation(integrated, monkeypatch):
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    case = dict(case_id="concurrent-new", sku_id="kera", text="alpha beta gamma")
    rules, version = load_pack([], mode="live"), ledger.current_pack().version
    for span in ("alpha", "beta"):
        integrated.spans = [span]
        runner.run_cases([case], "shadow", rules, version)
    calls = list(integrated.calls)
    integrated.spans, integrated.colour = ["alpha"], "yellow"
    compute = runner._compute
    interleaved = False
    expected = newest = None

    def compute_then_create(*args, **kwargs):
        nonlocal interleaved, expected, newest
        result = compute(*args, **kwargs)
        if not interleaved:
            interleaved = True
            assert ledger.get_session("case-concurrent-new") is None
            integrated.spans, integrated.colour = ["beta"], "green"
            newer = runner.replay([case], rules, persist=True, pack_version=version)
            assert (newer.changed_cases, newer.changed_atoms) == (1, 1)
            newest, = ledger.get_session("case-concurrent-new").verdicts
            assert newest.verdict == "green" and newest.supersedes is None
            expected = replay_state()
        return result

    monkeypatch.setattr(runner, "_compute", compute_then_create)
    with pytest.raises(ValueError, match="Session changed during replay.*again"):
        runner.replay([case], rules, persist=True, pack_version=version)
    assert interleaved and replay_state() == expected
    stored = ledger.get_session("case-concurrent-new")
    assert [atom.span for atom in stored.atoms] == ["beta"] and stored.verdicts == [newest]
    assert count("verdicts") == 1 and integrated.calls == calls


@pytest.mark.parametrize("purpose", ["live", "oracle"])
def test_explicit_nonpersisting_replay_ignores_current_pack(
        integrated, monkeypatch, tmp_path, purpose):
    monkeypatch.setattr(settings, "LLM_BACKEND", "api")
    monkeypatch.setattr(settings, "LLM_CACHE_DIR", str(tmp_path / "measure-cache"))
    stale_version = ledger.current_pack().version
    rules = load_pack([], mode="measure")
    case = dict(case_id="nonpersisting", sku_id="kera", text="alpha")
    runner.run_cases([case], "shadow", rules, stale_version)
    calls = list(integrated.calls)
    ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    expected = replay_state()

    def forbidden(*args, **kwargs):
        pytest.fail("explicit nonpersisting replay accessed the ledger")

    with monkeypatch.context() as no_ledger:
        for name in ("current_pack", "get_session", "transaction", "record_llm_call"):
            no_ledger.setattr(ledger, name, forbidden)
        result, = runner.replay([case], rules, purpose=purpose, pack_version=stale_version)
    assert result.status == "ok" and result.case_verdict == "green"
    assert result.verdicts[0].rule_pack_version == stale_version
    assert replay_state() == expected and integrated.calls == calls


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
