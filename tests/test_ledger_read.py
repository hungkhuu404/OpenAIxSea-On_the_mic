"""Synthetic review reads, sparse backfills and transactional review actions."""

import json
import sqlite3

import pytest

from core.config import settings
from core.models import Job
import ledger


STAMP = "2026-10-21T00:00:00Z"


@pytest.fixture
def db():
    ledger.init_db(settings.LEDGER_DB)
    return settings.LEDGER_DB


def job(job_id="job-one", **changes):
    return dict(job_id=job_id, rule_id="R_SYNTH", branch="rule/synthetic", cluster_key="cluster-one",
                cluster_hash="synthetic", cluster_item_ids=["item-one", "item-two"], status="queued",
                dry=True, config="synthetic-config", pid=1234, ts=STAMP) | changes


def verdict(atom_id="t01.a1", pack=1):
    return dict(atom_key=f"synthetic:{atom_id}", rule_pack_version=pack, verdict="abstain",
                decided_by="abstain:LLM_OFF", reason_code="LLM_OFF", as_of="2026-10-21",
                dossier_hash="synthetic", ts=STAMP)


def stored_session():
    ledger.add_session(dict(session_id="synthetic", as_of="2026-10-21", channel="written",
                            source="Synthetic", turns=[dict(turn_id="t01", speaker_label="[KOL 1]",
                            attribution="not_applicable", text="one two three")]))
    ledger.add_atoms("synthetic", [dict(atom_id=f"t01.a{i}", turn_id="t01", span=text,
        start=start, end=start + len(text), axis="thanh_phan", speaker_role="kol",
        subject_is_product=True) for i, (text, start) in enumerate(
            [("one", 0), ("two", 4), ("three", 8)], 1)])


def test_job_round_trip_and_attempt_order(db):
    failed = dict(attempt_no=1, started=STAMP, finished="2026-10-21T00:01:00Z", gate_result="fail",
                  error_lines=["R_SYNTH check 4: synthetic failure"], files_read=["rules/R_SYNTH.py"],
                  log_path="synthetic-attempt-one.log")
    passed = dict(attempt_no=2, started="2026-10-21T00:02:00Z", finished="2026-10-21T00:03:00Z",
                  gate_result="pass", error_lines=[], files_read=["rules/R_SYNTH.py"],
                  log_path="synthetic-attempt-two.log")
    ledger.create_job(job(attempts=[passed, failed]))
    # The job snapshot may lag the separately persisted attempt records.
    with sqlite3.connect(db) as conn:
        data = json.loads(conn.execute("SELECT data FROM jobs").fetchone()[0])
        data["attempts"] = []
        conn.execute("UPDATE jobs SET data=?", (json.dumps(data),))
    actual = ledger.get_job("job-one")
    assert isinstance(actual, Job)
    assert actual.model_dump(mode="json") == Job.model_validate(job(attempts=[failed, passed])).model_dump(mode="json")
    ledger.update_job("job-one", status="passed", attempts=[failed, passed], pid=None)
    assert ledger.get_job("job-one").status == "passed"
    assert ledger.get_job("job-one").pid is None
    assert ledger.get_job("missing") is None


def test_jobs_filter_and_newest_first(db):
    # Insert in reverse chronological order; timestamps can have different precision.
    ledger.create_job(job("newest", status="passed", ts="2026-10-21T00:00:00.1+00:00"))
    ledger.create_job(job("other", rule_id="R_OTHER", status="passed"))
    ledger.create_job(job("oldest", ts="2026-10-20T23:00:00Z"))
    assert [j.job_id for j in ledger.list_jobs()] == ["newest", "other", "oldest"]
    assert [j.job_id for j in ledger.list_jobs(rule_id="R_SYNTH")] == ["newest", "oldest"]
    assert [j.job_id for j in ledger.list_jobs(status="passed")] == ["newest", "other"]
    assert [j.job_id for j in ledger.list_jobs(rule_id="R_SYNTH", status="passed")] == ["newest"]
    assert ledger.list_jobs(rule_id="unknown") == []


def test_get_verdict_preserves_history_and_assigned_ids(db):
    first, second = ledger.append_verdicts([verdict(), verdict(pack=2)])
    assert ledger.get_verdict(first).verdict_id == first
    assert ledger.get_verdict(first).supersedes is None
    assert ledger.get_verdict(second).verdict_id == second
    assert ledger.get_verdict(second).supersedes == first
    assert ledger.get_verdict(second).rule_pack_version == 2
    assert ledger.get_verdict(999) is None


def test_sparse_backfill_keeps_one_effective_verdict_per_atom(db):
    stored_session()
    first_ids = ledger.append_verdicts([verdict(f"t01.a{i}") for i in range(1, 4)])
    ledger.create_pack_version([], [], "merge")
    changed, = ledger.append_verdicts([verdict("t01.a2", pack=2)])
    # A future pack must not leak into the current view, even for the same atom.
    future, = ledger.append_verdicts([verdict("t01.a2", pack=3)])
    effective = ledger.effective_verdicts("synthetic")
    assert len(effective) == len({v.atom_key for v in effective}) == 3
    assert {v.verdict_id for v in effective} == {first_ids[0], changed, first_ids[2]}
    assert sum(v.rule_pack_version == 2 for v in effective) == 1
    assert {v.verdict_id for v in ledger.effective_verdicts("synthetic", 1)} == set(first_ids)
    assert future in {v.verdict_id for v in ledger.effective_verdicts("synthetic", 3)}
    assert ledger.get_session("synthetic").verdicts == effective
    assert ledger.effective_verdicts("missing") == []


def test_pending_update_and_status_filters(db):
    pending_id = ledger.add_pending(dict(sku_id="synthetic", change={"synthetic": "new dossier"}))
    rejected_id = ledger.add_pending(dict(sku_id="other", status="rejected"))
    initial = ledger.get_pending(pending_id)
    assert initial["status"] == "pending"
    assert initial["approved_by"] is initial["approved_at"] is None
    updated = ledger.update_pending(pending_id, status="approved", approved_by="synthetic-reviewer", approved_at=STAMP)
    assert ledger.get_pending(pending_id) == updated
    assert updated["change"] == initial["change"]
    assert updated["approved_by"] == "synthetic-reviewer" and updated["approved_at"] == STAMP
    assert [p["pending_id"] for p in ledger.list_pending("approved")] == [pending_id]
    assert [p["pending_id"] for p in ledger.list_pending("rejected")] == [rejected_id]
    assert len(ledger.list_pending()) == 2 and ledger.list_pending("pending") == []
    assert ledger.get_pending("missing") is None
    with pytest.raises(KeyError):
        ledger.update_pending("missing", status="rejected")
    with pytest.raises(ValueError):
        ledger.update_pending(pending_id, status="invalid")
    assert ledger.get_pending(pending_id) == updated


def label(target, **changes):
    return dict(target=target, kind="label", label="green", rule_id="R_SYNTH",
                labeler="synthetic-reviewer", verdict_id=None, ts=STAMP) | changes


def test_labels_include_unlabelled_reports_and_filter_session_boundaries(db):
    first = label("synthetic:t01.a1")
    report = label("synthetic:t01.a2", kind="report_wrong", label=None, verdict_id=42)
    session_label = label("synthetic")
    for action in (first, report, session_label, label("synthetic-extra:t01.a1"), label("other:t01.a1")):
        ledger.add_label(action)
    assert ledger.list_labels("synthetic") == [first, report, session_label]
    assert len(ledger.list_labels()) == 5
    assert ledger.list_labels("missing") == []
    with pytest.raises(ValueError):
        ledger.add_label(label("synthetic", kind="unknown"))


def test_review_writes_join_outer_transaction(db):
    pending_id = ledger.add_pending(dict(sku_id="synthetic"))
    with pytest.raises(RuntimeError, match="rollback"):
        with ledger.transaction():
            ledger.add_label(label("synthetic:t01.a1"))
            ledger.update_pending(pending_id, status="rejected")
            assert len(ledger.list_labels()) == 1
            assert ledger.get_pending(pending_id)["status"] == "rejected"
            raise RuntimeError("rollback")
    assert ledger.list_labels() == []
    assert ledger.get_pending(pending_id)["status"] == "pending"


def test_read_functions_work_with_read_only_connections(db, monkeypatch):
    stored_session()
    ledger.create_job(job())
    verdict_id, = ledger.append_verdicts([verdict()])
    pending_id = ledger.add_pending(dict(sku_id="synthetic"))
    ledger.add_label(label("synthetic:t01.a1"))
    connect = ledger._connect

    def read_only():
        conn = connect()
        conn.execute("PRAGMA query_only=ON")
        return conn

    monkeypatch.setattr(ledger, "_connect", read_only)
    assert ledger.get_job("job-one") and ledger.list_jobs()
    assert ledger.get_verdict(verdict_id)
    assert ledger.get_pending(pending_id) and ledger.list_pending()
    assert ledger.list_labels("synthetic")
    assert ledger.effective_verdicts("synthetic") and ledger.get_session("synthetic")
