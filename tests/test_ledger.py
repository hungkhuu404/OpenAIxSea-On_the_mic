"""Synthetic ledger behavior, including persistence and transaction boundaries."""

import builtins
import json
import sqlite3
import sys
from types import SimpleNamespace
import unicodedata

import pytest

from core.config import settings
from core.models import Atom, Session, Turn, Verdict
import ledger


@pytest.fixture(autouse=True)
def textnorm_dependency(monkeypatch):
    """Keep storage tests runnable while the collaborator has not landed."""
    try:
        import textnorm
    except ModuleNotFoundError as exc:
        if exc.name != "textnorm":
            raise

        def fold(text):
            decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
            return "".join(c for c in decomposed if not unicodedata.combining(c))

        monkeypatch.setitem(sys.modules, "textnorm", SimpleNamespace(fold=fold))


def make_session(session_id="synthetic", text="Một đĩa rau mới."):
    return Session(session_id=session_id, as_of="2026-10-21", channel="written", source="Synthetic",
                   turns=[Turn(turn_id="t01", speaker_label="[KOL 1]", attribution="press_attributed", text=text)])


def make_atom(text="đĩa rau", atom_id="t01.a1", start=4):
    return Atom(atom_id=atom_id, turn_id="t01", span=text, start=start, end=start + len(text),
                axis="thanh_phan", speaker_role="kol", subject_is_product=True)


def make_verdict(pack=1):
    return Verdict(atom_key="synthetic:t01.a1", rule_pack_version=pack, verdict="abstain",
                   decided_by="abstain:LLM_OFF", reason_code="LLM_OFF", as_of="2026-10-21",
                   dossier_hash="synthetic", ts="2026-10-21T00:00:00+00:00")


@pytest.fixture
def db():
    ledger.init_db(settings.LEDGER_DB)
    return settings.LEDGER_DB


def test_append_only_and_rollback(db):
    ledger.add_session(make_session())
    ledger.add_atoms("synthetic", [make_atom()])
    ledger.append_verdicts([make_verdict()])
    ledger.append_event("queue.changed", {})
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        for table in ("verdicts", "events"):
            for sql in (f"UPDATE {table} SET {'data=data' if table == 'verdicts' else 'type=type'}", f"DELETE FROM {table}"):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    conn.execute(sql)
    with pytest.raises(ValueError):
        with ledger.transaction():
            ledger.add_session(make_session("rollback"))
            ledger.append_event("queue.changed", {"rolled_back": True})
            ledger.append_verdicts([make_verdict()])
            ledger.create_pack_version([], ["R_SYNTH"], "quarantine")
            raise ValueError("abort")
    assert ledger.get_session("rollback") is None
    assert len(ledger.events_since(0)) == 1
    assert len(ledger.latest_verdicts("synthetic")) == 1
    assert ledger.current_pack().version == 1


def test_search_phrase_fold_prefix_and_original(db):
    texts = ["Một đĩa rau mới.", "MỘT ĐĨA RAU MỚI."]
    for i, text in enumerate(texts):
        ledger.add_session(make_session(f"sample-{i}", text))
    for query in ("đĩa rau", "dia rau", "DIA RAU", "dĩa rau", "dia ra*"):
        hits = ledger.search(query)
        assert len(hits) == 2
        assert [h["snippet"] for h in hits] == texts
        if query == "đĩa rau":
            assert all(h["column"] == "x" for h in hits)
    assert len(ledger.search("dia rau", "sample-1")) == 1
    assert ledger.search('" OR *') == []
    assert ledger.search("rau đĩa") == []


def test_exact_matches_rank_before_folded(db):
    ledger.add_session(make_session("folded", "dia rau"))
    ledger.add_session(make_session("exact", "đĩa rau"))
    assert [h["column"] for h in ledger.search("đĩa rau")] == ["x", "xf"]


def test_ingest_replace_and_verdict_history(db):
    first = ledger.add_session(make_session())
    again = ledger.add_session(make_session(text="A different line."))
    assert first == again
    ledger.add_atoms("synthetic", [make_atom()])
    ids = ledger.append_verdicts([make_verdict(), make_verdict()])
    assert ledger.latest_verdicts("synthetic")[0].verdict_id == ids[-1]
    assert ledger.latest_verdicts("synthetic")[0].supersedes == ids[0]
    ledger.add_session_flags("synthetic", [{"flag_code": "NO_AD_CONFIRMATION", "rule_id": "R06",
        "verdict": "red", "legal_basis": [], "message": "Synthetic flag"}])
    view = ledger.get_session("synthetic")
    assert len(view.turns) == len(view.atoms) == len(view.verdicts) == len(view.flags) == 1
    new = make_atom("mới", "t01.a2", 12)
    with ledger.transaction():
        ledger.replace_atoms("synthetic", [new])
    assert ledger.get_session("synthetic").atoms == [new]
    assert [h["kind"] for h in ledger.search("đĩa rau")] == ["turn"]
    assert any(h["atom_key"] == "synthetic:t01.a2" for h in ledger.search("mới"))
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 2
    with pytest.raises(RuntimeError, match="requires transaction"):
        ledger.replace_atoms("synthetic", [])


def test_versions_approval_and_exclusion(monkeypatch):
    rules = [SimpleNamespace(rule_id=rid, meta=SimpleNamespace(approved_by=approval, status=status))
             for rid, approval, status in [("A", "reviewer", "active"), ("B", None, "active"),
                                            ("C", "reviewer", "active"), ("D", "reviewer", "quarantined")]]
    monkeypatch.setitem(sys.modules, "rules.registry", SimpleNamespace(all_rules=lambda: rules))
    ledger.init_db(settings.LEDGER_DB, exclude_rules=["C"])
    assert ledger.current_pack().active == ["A"]
    ledger.init_db(settings.LEDGER_DB)
    assert ledger.current_pack().version == 1
    pack = ledger.create_pack_version(["C"], ["A"], "quarantine", "A")
    assert pack.version == 2 and pack.active == ["C"] and pack.quarantined == ["A"]
    assert ledger.current_pack() == pack


def test_missing_registry(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "rules.registry":
            raise ModuleNotFoundError("not ready", name="rules.registry")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    ledger.init_db(settings.LEDGER_DB)
    assert ledger.current_pack().active == []


def test_registry_dependency_error_is_not_hidden(monkeypatch):
    original = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "rules.registry":
            raise ModuleNotFoundError("broken dependency", name="synthetic_dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(ModuleNotFoundError) as error:
        ledger.init_db(settings.LEDGER_DB)
    assert error.value.name == "synthetic_dependency"


def test_events_increasing_and_jobs(db):
    ids = [ledger.append_event("queue.changed", {"n": n}) for n in range(3)]
    assert [row.id for row in ledger.events_since(ids[0])] == ids[1:]
    assert len(ledger.events_since(0, limit=1)) == 1
    job = dict(job_id="synthetic-job", rule_id="R_SYNTH", branch="rule/synthetic", cluster_key="synthetic",
               cluster_hash="abc", status="queued", config="abc", ts="2026-10-21T00:00:00Z")
    assert ledger.create_job(job) == "synthetic-job"
    changed = ledger.update_job("synthetic-job", status="running", attempts=[dict(attempt_no=1, started=job["ts"])])
    assert changed.status == "running" and len(changed.attempts) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 1
    ledger.record_llm_call({"request_sha256": "abc", "response": None})
    pending = ledger.add_pending({"sku_id": "synthetic", "status": "pending"})
    with sqlite3.connect(db) as conn:
        assert json.loads(conn.execute("SELECT data FROM pending_dossier WHERE pending_id=?", (pending,)).fetchone()[0])["sku_id"] == "synthetic"


def test_effective_view_inherits_unchanged_atoms(db):
    ledger.add_session(make_session())
    ledger.add_atoms("synthetic", [make_atom()])
    first, = ledger.append_verdicts([make_verdict()])
    ledger.create_pack_version([], [], "merge")
    assert ledger.get_session("synthetic").verdicts[0].verdict_id == first
    assert ledger.latest_verdicts("synthetic", 2) == []
    latest, = ledger.append_verdicts([make_verdict(2)])
    assert ledger.get_session("synthetic").verdicts[0].verdict_id == latest


@pytest.fixture
def session_flags(db):
    ledger.add_session(make_session())
    flags = [dict(flag_code="NO_AD_CONFIRMATION", rule_id="R06", message="Synthetic ad flag"),
             dict(flag_code="RECALLED_AT_AS_OF", rule_id="L1_RECALL", message="Synthetic recall flag")]
    ledger.add_session_flags("synthetic", flags)
    return ledger.get_session("synthetic").flags


def test_replace_session_flags_removes_stale_and_clears_all(session_flags):
    ledger.add_session(make_session("other"))
    ledger.add_session_flags("other", session_flags)
    replacement = session_flags[1].model_copy(update={"message": "Updated synthetic recall"})
    ledger.replace_session_flags("synthetic", [replacement])
    assert ledger.get_session("synthetic").flags == [replacement]
    ledger.replace_session_flags("synthetic", [])
    assert ledger.get_session("synthetic").flags == []
    assert ledger.get_session("other").flags == session_flags


def test_replace_session_flags_rolls_back_after_partial_insert(session_flags):
    with pytest.raises(ValueError):
        ledger.replace_session_flags("synthetic", [session_flags[0], {"flag_code": "invalid"}])
    assert ledger.get_session("synthetic").flags == session_flags


def test_replace_session_flags_joins_outer_transaction(session_flags):
    with ledger.transaction():
        ledger.append_event("queue.changed", {"synthetic": True})
        ledger.replace_session_flags("synthetic", [session_flags[0]])
        assert ledger.get_session("synthetic").flags == [session_flags[0]]
        with pytest.raises(ValueError):
            ledger.replace_session_flags("synthetic", [session_flags[1], {"flag_code": "invalid"}])
        assert ledger.get_session("synthetic").flags == [session_flags[0]]
    assert ledger.get_session("synthetic").flags == [session_flags[0]]
    assert len(ledger.events_since(0)) == 1
    with pytest.raises(RuntimeError, match="rollback"):
        with ledger.transaction():
            ledger.replace_session_flags("synthetic", [])
            raise RuntimeError("rollback")
    assert ledger.get_session("synthetic").flags == [session_flags[0]]
