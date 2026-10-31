"""Real WAL readers must not combine data from concurrent commits."""
from threading import Event, Thread

import pytest

from core.config import settings
import ledger


def seed():
    ledger.init_db(settings.LEDGER_DB)
    ledger.add_session(dict(session_id="synthetic", as_of="2026-10-21", channel="written",
        source="Synthetic", turns=[dict(turn_id="t01", speaker_label="[unidentified]",
        attribution="not_applicable", text="old new")]))
    replace("old", 1)


def replace(word, pack):
    start = 0 if word == "old" else 4
    with ledger.transaction():
        ledger.replace_atoms("synthetic", [dict(atom_id="t01.a1", turn_id="t01", span=word,
            start=start, end=start + len(word), axis="thanh_phan", speaker_role="kol",
            subject_is_product=True)])
        ledger.replace_session_flags("synthetic", [dict(flag_code="NO_AD_CONFIRMATION",
            rule_id="R06", verdict="red", legal_basis=[], message=word)])
        ledger.append_verdicts([dict(atom_key="synthetic:t01.a1", rule_pack_version=pack,
            verdict="abstain", decided_by="abstain:LLM_OFF", reason_code="LLM_OFF",
            as_of="2026-10-21", dossier_hash=word, evidence_span=word,
            ts="2026-10-21T00:00:00Z")])


@pytest.mark.parametrize("listing", [False, True], ids=["get_session", "list_sessions"])
def test_session_read_keeps_snapshot_while_writer_commits(monkeypatch, listing):
    seed()
    begin_write, committed = Event(), Event()
    errors = []

    def writer():
        try:
            assert begin_write.wait(5), "reader never reached verdict lookup"
            with ledger.transaction():
                pack = ledger.create_pack_version([], [], "quarantine")
                replace("new", pack.version)
        except BaseException as error:
            errors.append(error)
        finally:
            committed.set()

    effective = ledger.effective_verdicts

    def after_atoms(*args, **kwargs):
        # get_session has already materialized atoms and flags at this point.
        begin_write.set()
        assert committed.wait(5), "a read snapshot must not block the WAL writer"
        assert not errors, errors
        return effective(*args, **kwargs)

    thread = Thread(target=writer, daemon=True)
    thread.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "effective_verdicts", after_atoms)
            view = ledger.list_sessions()[0] if listing else ledger.get_session("synthetic")
    finally:
        begin_write.set()
        thread.join(6)
    assert not thread.is_alive()
    assert not errors, errors
    assert view.atoms[0].span == view.flags[0].message == view.verdicts[0].evidence_span == "old"
    assert view.verdicts[0].rule_pack_version == 1
    updated = ledger.get_session("synthetic")
    assert updated.atoms[0].span == updated.flags[0].message == updated.verdicts[0].evidence_span == "new"
    assert updated.verdicts[0].rule_pack_version == 2


def test_failed_read_releases_snapshot_and_connection(monkeypatch):
    seed()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic read failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "effective_verdicts", fail)
        with pytest.raises(RuntimeError, match="synthetic read failure"):
            ledger.get_session("synthetic")
    # A later write must commit, rather than join a leaked read transaction.
    replace("new", 1)
    assert ledger.get_session("synthetic").atoms[0].span == "new"


def test_effective_verdicts_share_snapshot_with_default_pack(monkeypatch):
    seed()
    errors = []

    def writer():
        try:
            with ledger.transaction():
                ledger.create_pack_version([], [], "quarantine")
                ledger.replace_atoms("synthetic", [])
        except BaseException as error:
            errors.append(error)

    current_pack = ledger.current_pack

    def commit_after_pack_read():
        pack = current_pack()
        thread = Thread(target=writer, daemon=True)
        thread.start()
        thread.join(5)
        assert not thread.is_alive(), "reader must not block the WAL writer"
        assert not errors, errors
        return pack

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "current_pack", commit_after_pack_read)
        rows = ledger.effective_verdicts("synthetic")
    assert len(rows) == 1
    assert rows[0].evidence_span == "old"
    assert ledger.effective_verdicts("synthetic") == []
