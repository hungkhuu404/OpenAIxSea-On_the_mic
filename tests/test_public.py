"""Offline public replay, request boundaries and temporary storage isolation."""

import asyncio
import builtins
from datetime import date
import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType

from fastapi.testclient import TestClient
import httpx
import pytest

from api.app import create_app
from core import config, paths
from core.models import Session
import ledger


SENTENCE = {"sku_id": "dk_betics_gold", "as_of": "2026-10-31",
            "text": "uống một tháng là chữa khỏi hẳn tiểu đường"}


@pytest.fixture
def collaborators(monkeypatch):
    calls = []

    def ingest(path, sku_id, as_of):
        calls.append(("ingest", as_of, threading.get_ident()))
        return Session(session_id=f"kera-reenact-{as_of}", sku_pin=sku_id, as_of=as_of,
                       channel="livestream", source="Synthetic public replay")

    def run(session_id, purpose):
        calls.append(("run", session_id, threading.get_ident()))
        asyncio.run(asyncio.sleep(0))

    def recall(sku_id, recall_date, source):
        calls.append(("recall", recall_date, threading.get_ident()))

    def preflight(sku_id, as_of, text, full):
        calls.append(("preflight", full, threading.get_ident()))
        return {"verdict": "red", "decided_by": "rule:R04"}

    def no_llm(*args, **kwargs):
        raise AssertionError("Public mode must not invoke a model")

    for name, functions in {
        "pipeline.ingest": {"from_reenactment": ingest},
        "pipeline.runner": {"process_session": run},
        "pipeline.dossier_events": {"apply_recall": recall},
        "pipeline.rawpath": {"preflight": preflight},
        "pipeline.llm": {"call": no_llm, "acall": no_llm},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(functions)
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_public_replay_runs_off_loop_and_leaves_laptop_untouched(tmp_path, collaborators):
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 0)
    laptop = Path(config.settings.LEDGER_DB)
    laptop.parent.mkdir(parents=True, exist_ok=True)
    laptop.write_bytes(b"Synthetic laptop ledger must remain untouched")
    config.set_override("LLM_CACHE_DIR", str(tmp_path / "untrusted-cache"))
    local_cache = config.settings.LLM_LOCAL_CACHE_DIR
    temp_db = None

    async def exercise():
        nonlocal temp_db
        loop_thread = threading.get_ident()
        app = create_app()
        assert config.settings.PUBLIC_MODE == config.settings.OFFLINE == 1
        async with app.router.lifespan_context(app):
            temp_db = Path(config.settings.LEDGER_DB)
            assert temp_db != laptop and temp_db.is_file()
            assert config.settings.LLM_CACHE_DIR == str(paths.CACHE_DIR)
            assert config.settings.LLM_LOCAL_CACHE_DIR != local_cache
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/sessions")
                assert response.status_code == 200
                assert {s["session_id"] for s in response.json()} == {
                    "kera-reenact-2025-01-08", "kera-reenact-2024-12-12"}
                raw = await client.post("/api/preflight", json=SENTENCE)
                assert raw.status_code == 200 and raw.json()["verdict"] == "red"
                assert (await client.get("/api/health")).json()["offline"] == 1
            assert all(call[2] != loop_thread for call in collaborators)

    asyncio.run(exercise())
    assert [(c[0], c[1]) for c in collaborators] == [
        ("ingest", date(2025, 1, 8)), ("run", "kera-reenact-2025-01-08"),
        ("ingest", date(2024, 12, 12)), ("run", "kera-reenact-2024-12-12"),
        ("recall", date(2025, 3, 24)), ("preflight", False)]
    assert laptop.read_bytes() == b"Synthetic laptop ledger must remain untouched"
    assert not temp_db.exists()
    assert config.settings.LEDGER_DB == str(laptop)
    assert config.settings.LLM_LOCAL_CACHE_DIR == local_cache


def test_public_rejects_writes_and_presenter_content(tmp_path, collaborators):
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 1)
    config.set_override("PRESENT_DIR", str(tmp_path / "presenter"))
    with TestClient(create_app()) as client:
        for route in ("/api/labels", "/api/sessions", "/api/report-wrong", "/api/rules/R1/merge", "/future-write"):
            assert client.post(route, json={}).status_code == 403
        assert client.delete("/api/sessions/test").status_code == 403
        for route in ("/api/present/manifest", "/present/", "/present/names.json", "/present/clip/a1.mp4"):
            assert client.get(route).status_code == 404
        assert client.post("/api/preflight", json=SENTENCE).status_code == 200


def test_public_rate_limit_and_window_expiry(monkeypatch, collaborators):
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 1)
    config.set_override("RATE_LIMIT_PER_MIN", 20)
    now = [100.0]
    monkeypatch.setattr("api.preflight.monotonic", lambda: now[0])
    app = create_app()
    with TestClient(app) as client:
        for _ in range(20):
            assert client.post("/api/preflight", json=SENTENCE).status_code == 200
        blocked = client.post("/api/preflight", json=SENTENCE)
        assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"

        async def another_client():
            transport = httpx.ASGITransport(app=app, client=("other-ip", 123))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
                assert (await other.post("/api/preflight", json=SENTENCE)).status_code == 200

        asyncio.run(another_client())
        now[0] += 60
        assert client.post("/api/preflight", json=SENTENCE).status_code == 200


@pytest.mark.parametrize("public", [False, True])
def test_preflight_not_ready_is_simulated(monkeypatch, collaborators, public):
    config.set_override("PUBLIC_MODE", int(public))
    config.set_override("OFFLINE", 1)
    monkeypatch.setitem(sys.modules, "pipeline.rawpath", None)
    with TestClient(create_app(public=public)) as client:
        response = client.post("/api/preflight", json=SENTENCE)
        assert response.status_code == 503 and response.json() == {"todo": "rawpath"}


def test_laptop_preflight_runs_full_in_worker(collaborators):
    config.set_override("PUBLIC_MODE", 0)
    config.set_override("OFFLINE", 1)
    with TestClient(create_app()) as client:
        assert client.post("/api/preflight", json=SENTENCE).status_code == 200
    assert [call[1] for call in collaborators if call[0] == "preflight"] == [True]


@pytest.mark.parametrize("missing", ["pipeline.ingest", "pipeline.runner", "pipeline.dossier_events"])
def test_missing_replay_module_starts_empty(monkeypatch, collaborators, capsys, missing):
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 1)
    monkeypatch.setitem(sys.modules, missing, None)
    with TestClient(create_app()) as client:
        assert client.get("/api/sessions").json() == []
    assert f"not ready: {missing}" in capsys.readouterr().out
    assert collaborators == []


def test_transitive_import_failure_is_not_hidden(monkeypatch, collaborators):
    config.set_override("PUBLIC_MODE", 0)
    config.set_override("OFFLINE", 1)
    original = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "pipeline.rawpath":
            raise ModuleNotFoundError("Synthetic broken dependency", name="synthetic_dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    with TestClient(create_app()) as client, pytest.raises(ModuleNotFoundError) as error:
        client.post("/api/preflight", json=SENTENCE)
    assert error.value.name == "synthetic_dependency"


def test_cache_backend_warning_once(tmp_path, monkeypatch, collaborators, capsys):
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 1)
    config.set_override("LLM_BACKEND", "codex")
    cache = tmp_path / "committed-cache"
    (cache / "ab").mkdir(parents=True)
    for name in ("one", "two"):
        (cache / "ab" / f"{name}.json").write_text(json.dumps({"backend": "api"}), encoding="utf-8")
    monkeypatch.setattr(paths, "CACHE_DIR", cache)
    with TestClient(create_app()):
        assert config.settings.LLM_BACKEND == "codex"
    assert capsys.readouterr().out.count("warning: cache backend") == 1


def test_real_rawpath_red_offline():
    try:
        importlib.import_module("pipeline.rawpath")
    except ModuleNotFoundError as error:
        if error.name != "pipeline.rawpath":
            raise
        pytest.skip("not ready: pipeline.rawpath")
    config.set_override("PUBLIC_MODE", 1)
    config.set_override("OFFLINE", 1)
    with TestClient(create_app()) as client:
        response = client.post("/api/preflight", json=SENTENCE)
        assert response.status_code == 200 and response.json()["verdict"] == "red"


def test_public_cli_sets_flags_before_creating_app(monkeypatch):
    import api.__main__ as cli

    config.set_override("PUBLIC_MODE", 0)
    config.set_override("OFFLINE", 0)
    seen = []

    def app():
        seen.append((config.settings.PUBLIC_MODE, config.settings.OFFLINE))
        return "synthetic-app"

    def serve(application, host, port):
        assert application == "synthetic-app" and host == "127.0.0.1" and port == 8000

    monkeypatch.setattr(sys, "argv", ["api", "--public"])
    monkeypatch.setattr(cli, "create_app", app)
    monkeypatch.setattr(cli.uvicorn, "run", serve)
    cli.main()
    assert seen == [(1, 1)]
