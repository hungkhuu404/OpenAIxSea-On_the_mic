"""Clip integration uses invented local assets and offline collaborators only."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType

from fastapi.testclient import TestClient
import pytest

from api.app import create_app
from api import sessions
from core import config
from core.models import Atom, Verdict
from pipeline.ingest_clip import from_clip_transcript
import ledger


@pytest.fixture
def assets(tmp_path):
    clip = tmp_path / "clip"
    clip.mkdir()
    (clip / "a1.mp4").write_bytes(b"synthetic placeholder")
    (clip / "a1.transcript.json").write_text(json.dumps([
        {"start": 0, "end": 2.5, "text": "Synthetic claim"}]), encoding="utf-8")
    (clip / "a1.subs.json").write_text("[]", encoding="utf-8")
    (clip / "a1.meta.json").write_text(json.dumps({"source_line": "Synthetic publication",
        "speaker_line": "Quote attributed by the press to [TEST SPEAKER]",
        "as_of_note": "Synthetic date note"}), encoding="utf-8")
    (tmp_path / "names.json").write_text(json.dumps({"[unidentified]": "Invented Name"}), encoding="utf-8")
    config.set_override("PRESENT_DIR", str(tmp_path))
    config.set_override("OFFLINE", 1)
    return tmp_path


@pytest.fixture
def runner(monkeypatch):
    calls = []
    module = ModuleType("pipeline.runner")

    def run(sid, purpose, reprocess=False):
        # This would fail if startup or a route ran directly on its event loop.
        asyncio.run(asyncio.sleep(0))
        calls.append((sid, purpose, threading.get_ident(), reprocess))
        session = ledger.get_session(sid)
        if not session.atoms:
            turn = session.turns[0]
            ledger.add_atoms(sid, [Atom(atom_id="t01.a1", turn_id="t01", span=turn.text,
                start=0, end=len(turn.text), axis="cong_dung", speaker_role="kol", subject_is_product=True)])
            ledger.append_verdicts([Verdict(atom_key=f"{sid}:t01.a1", rule_pack_version=1,
                verdict="abstain", decided_by="abstain:LLM_OFF", reason_code="LLM_OFF",
                as_of=session.as_of, dossier_hash="synthetic", ts="2026-10-21T00:00:00Z")])
        return {"session_id": sid}

    module.process_session = run
    monkeypatch.setitem(sys.modules, "pipeline.runner", module)
    return calls


def test_clip_adapter(assets):
    session = from_clip_transcript(assets / "clip/a1.transcript.json", "kera", "2025-01-08", "Synthetic publication")
    assert session.session_id == "clip-a1" and session.channel == "video"
    turn, = session.turns
    assert (turn.turn_id, turn.start_s, turn.end_s) == ("t01", 0, 2.5)
    assert turn.attribution == "not_applicable" and turn.speaker_label == "[unidentified]"
    assert session.source == "Synthetic publication"


def test_startup_worker_idempotence_and_clip_route(assets, runner, monkeypatch):
    threads = []
    original = sessions.ensure_clip_session

    def ensure(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr("api.app.ensure_clip_session", ensure)
    loop_thread = []
    original_lifespan = importlib.import_module("api.app").ledger_lifespan
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(public):
        loop_thread.append(threading.get_ident())
        async with original_lifespan(public):
            yield

    monkeypatch.setattr("api.app.ledger_lifespan", lifespan)
    for _ in range(2):
        with TestClient(create_app()) as client:
            assert client.post("/api/sessions", json={"kind": "clip"}).json() == {"session_ids": ["clip-a1"]}
            stored = ledger.get_session("clip-a1")
            assert stored.verdicts[0].reason_code == "LLM_OFF"
            assert len(ledger.list_sessions()) == 1
    assert len(runner) == 1
    assert all(thread not in loop_thread for thread in threads)
    assert len([event for event in ledger.events_since(0) if event.type == "session.ingested"]) == 1


@pytest.mark.parametrize("blocked", [None, "present", "public", "video", "query"])
def test_clip_attribution_four_conditions(assets, runner, blocked):
    with TestClient(create_app()) as client:
        if blocked == "present":
            config.set_override("PRESENT_DIR", None)
        if blocked == "public":
            config.set_override("PUBLIC_MODE", 1)
        if blocked == "video":
            config.set_override("VIDEO_MODE", 1)
        suffix = "" if blocked == "query" else "?presenter=1"
        data = client.get("/api/sessions/clip-a1" + suffix).json()
        assert ("speaker_line" in data) == (blocked is None)
        assert data["turns"][0]["display_speaker"] == "Speaker: unidentified"
        assert "Invented Name" not in json.dumps(data)
        manifest = client.get("/api/present/manifest" + suffix)
        if manifest.status_code == 200:
            assert ("speaker_line" in manifest.json()["clips"]["a1"]) == (blocked is None)
        if blocked == "video":
            assert not manifest.json()["clips"]["a1"]["available"]
            assert client.get("/present/clip/a1.mp4").status_code == 404


def test_process_recall_and_missing_collaborators(assets, runner, monkeypatch):
    with TestClient(create_app()) as client:
        assert client.post("/api/sessions/clip-a1/process", json={"reprocess": True}).status_code == 200
        assert runner[-1][-1]
        recall = ModuleType("pipeline.dossier_events")
        calls = []

        def apply(*args):
            asyncio.run(asyncio.sleep(0))
            calls.append(args)
            return {"ok": True}

        recall.apply_recall = apply
        monkeypatch.setitem(sys.modules, "pipeline.dossier_events", recall)
        body = {"sku_id": "kera", "recall_date": "2025-03-24", "source": "Synthetic recall"}
        assert client.post("/api/dossier-events/recall", json=body).json() == {"ok": True}
        assert calls[0][1].isoformat() == body["recall_date"]
        original = sessions.import_module

        def missing(name):
            if name == "pipeline.dossier_events":
                raise ModuleNotFoundError(name=name)
            return original(name)

        monkeypatch.setattr(sessions, "import_module", missing)
        response = client.post("/api/dossier-events/recall", json=body)
        assert response.status_code == 503 and response.json() == {"todo": "pipeline.dossier_events"}


def test_real_offline_clip_startup(assets):
    # The actual runner is used when present; a missing dependency is explicit.
    try:
        importlib.import_module("pipeline.runner")
    except ModuleNotFoundError as error:
        if error.name != "pipeline.runner":
            raise
        pytest.skip("not ready: pipeline.runner")
    config.set_override("LLM_BACKEND", "codex")
    with TestClient(create_app()):
        session = ledger.get_session("clip-a1")
        assert session.atoms
        assert all(row.reason_code == "LLM_OFF" for row in session.verdicts)


def test_text_ingest_runs_once_and_falls_back_without_dossier_events(runner, monkeypatch):
    original = sessions.import_module

    def missing(name):
        if name == "pipeline.dossier_events":
            raise ModuleNotFoundError(name=name)
        return original(name)

    monkeypatch.setattr(sessions, "import_module", missing)
    with TestClient(create_app()) as client:
        body = {"kind": "text", "sku_id": "demo_sku", "as_of": "2026-10-21", "text": "Synthetic claim"}
        first = client.post("/api/sessions", json=body)
        assert first.status_code == 200
        second = client.post("/api/sessions", json=body)
        assert first.json()["session_id"] == second.json()["session_id"]
        assert len(runner) == 1
        assert len([row for row in ledger.events_since(0) if row.type == "session.ingested"]) == 1


def test_text_ingest_auto_run_and_transitive_import_failure(monkeypatch):
    module = ModuleType("pipeline.dossier_events")
    calls = []
    module.auto_run = calls.append
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", module)
    with TestClient(create_app()) as client:
        body = {"kind": "text", "sku_id": "demo_sku", "as_of": "2026-10-21", "text": "Synthetic claim"}
        response = client.post("/api/sessions", json=body)
        assert response.status_code == 200 and calls == [response.json()["session_id"]]
        original = sessions.import_module

        def broken(name):
            if name == "pipeline.runner":
                raise ModuleNotFoundError(name="transitive_dependency")
            return original(name)

        monkeypatch.setattr(sessions, "import_module", broken)
        with pytest.raises(ModuleNotFoundError) as caught:
            client.post(f"/api/sessions/{calls[0]}/process")
        assert caught.value.name == "transitive_dependency"


def test_search_unknown_attribution_and_clip_excluded_from_queue(assets, runner):
    with TestClient(create_app()) as client:
        assert client.get("/api/queue").json() == []
        ledger.add_session(dict(session_id="synthetic-unknown", sku_pin="demo_sku", as_of="2026-10-21",
            channel="livestream", source="Synthetic", turns=[dict(turn_id="t01", text="Một đĩa rau",
                attribution="press_unknown", speaker_label="[unidentified]")]))
        hits = client.get("/api/search?q=dia%20rau&presenter=1").json()
        assert len(hits) == 1 and hits[0]["display_speaker"] == "Speaker unknown"


def test_public_startup_never_ingests_local_clips(assets, monkeypatch):
    def forbidden():
        raise AssertionError("public startup touched local clips")

    monkeypatch.setattr("api.app.ensure_clip_session", forbidden)
    monkeypatch.setattr("api.public.prepare_replay", lambda: ledger.init_db(config.settings.LEDGER_DB))
    config.set_override("PUBLIC_MODE", 1)
    with TestClient(create_app()) as client:
        assert ledger.get_session("clip-a1") is None
        assert client.get("/api/present/manifest?presenter=1").status_code == 404
        assert client.get("/api/sessions/clip-a1?presenter=1").status_code == 404
