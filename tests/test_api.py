"""HTTP contracts, presenter privacy, and a real streaming socket."""

import builtins
from contextlib import contextmanager
import json
import socket
import threading
import time

from fastapi.testclient import TestClient
import httpx
import pytest
import uvicorn

from api.app import STUB_ROUTES, create_app
from core import config
from core.models import Session, Turn
import ledger
from tests.test_ledger import make_session, textnorm_dependency


def test_health_startup_and_secret(monkeypatch, capsys):
    sentinel = "synthetic-secret-value"
    config.set_override("OPENAI_API_KEY", sentinel)
    calls = []
    real = config.config_fields

    def fields():
        calls.append(1)
        return real()

    monkeypatch.setattr(config, "config_fields", fields)
    with TestClient(create_app()) as client:
        health = client.get("/api/health").json()
        client.get("/api/health")
        assert set(real()) <= health.keys()
        assert {"stage", "palette", "pack_version"} <= health.keys()
        assert health["api_key"] == "set"
        assert len(calls) == 1
    output = capsys.readouterr().out
    assert output.strip() == " ".join(f"{key}={value}" for key, value in real().items())
    assert sentinel not in output + json.dumps(health)


@pytest.mark.parametrize("blocked", [None, "present", "public", "video", "query"])
def test_display_speaker(tmp_path, blocked):
    (tmp_path / "names.json").write_text(json.dumps({"[KOL 1]": "Invented Speaker Amber"}), encoding="utf-8")
    config.set_override("PRESENT_DIR", str(tmp_path) if blocked != "present" else None)
    config.set_override("PUBLIC_MODE", int(blocked == "public"))
    config.set_override("VIDEO_MODE", int(blocked == "video"))
    with TestClient(create_app()) as client:
        session = make_session()
        session.turns.extend([Turn.model_validate(session.turns[0].model_dump() | {"turn_id": f"t0{i}", "attribution": att})
                             for i, att in enumerate(("press_unknown", "constructed", "not_applicable"), 2)])
        ledger.add_session(session)
        suffix = "" if blocked == "query" else "?presenter=1"
        turns = client.get("/api/sessions/synthetic" + suffix).json()["turns"]
        assert turns[0]["display_speaker"] == ("Invented Speaker Amber" if blocked is None else "[KOL 1]")
        assert turns[1]["display_speaker"] == "Speaker unknown"
        assert turns[2]["display_speaker"] == "Reenactment line (team-written) · [KOL 1]"
        assert turns[3]["display_speaker"] == "[KOL 1]"
        assert client.get("/api/sessions" + suffix).json()[0]["turns"] == turns
        clip = Session.model_validate(make_session("clip-synthetic").model_dump() | {"channel": "video"})
        clip.turns[0] = Turn.model_validate(clip.turns[0].model_dump() | {"attribution": "not_applicable"})
        ledger.add_session(clip)
        assert client.get("/api/sessions/clip-synthetic" + suffix).json()["turns"][0]["display_speaker"] == "Speaker: unidentified"
        assert "display_speaker" not in ledger.get_session("synthetic").model_dump_json()


def test_unreadable_names_and_routes(tmp_path):
    config.set_override("PRESENT_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        ledger.add_session(make_session())
        assert client.get("/api/sessions/synthetic?presenter=1").json()["turns"][0]["display_speaker"] == "[KOL 1]"
        assert client.get("/api/sessions/missing").status_code == 404
        assert len(client.get("/api/search", params={"q": "dia rau"}).json()) == 1
        event_id = ledger.append_event("queue.changed", {})
        assert client.get("/api/events").json()[0]["id"] == event_id
        assert client.get("/api/events", params={"since": event_id}).json() == []
        for (method, path), task in STUB_ROUTES.items():
            response = client.request(method, "/api" + path.replace("{id}", "synthetic"))
            assert response.status_code == 501
            assert response.json() == {"todo": task}


def test_missing_textnorm_is_503(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "textnorm":
            raise ModuleNotFoundError("not ready", name="textnorm")
        return original(name, *args, **kwargs)

    with TestClient(create_app()) as client:
        monkeypatch.setattr(builtins, "__import__", missing)
        response = client.get("/api/search?q=synthetic")
        assert response.status_code == 503 and response.json() == {"todo": "textnorm"}


@contextmanager
def live_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("Server did not start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def test_sse_replay_resume_and_event_after_connection():
    with live_server(create_app()) as url:
        first = ledger.append_event("queue.changed", {"n": 1})
        second = ledger.append_event("queue.changed", {"n": 2})
        with httpx.stream("GET", url + "/api/events/stream?since=0",
                          headers={"Last-Event-ID": str(first)}, timeout=5) as response:
            assert response.status_code == 200
            # Headers have arrived: this write happens after the connection.
            third = ledger.append_event("queue.changed", {"n": 3})
            seen = []
            for line in response.iter_lines():
                if line.startswith("id: "):
                    seen.append(int(line[4:]))
                if len(seen) == 2:
                    break
            assert seen == [second, third]
