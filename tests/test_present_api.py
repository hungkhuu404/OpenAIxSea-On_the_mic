"""Synthetic presenter privacy and media-boundary checks."""

import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import set_override


@pytest.fixture
def assets(tmp_path):
    (tmp_path / "slides").mkdir()
    (tmp_path / "clip").mkdir()
    for name in ("02.png", "01.png"):
        (tmp_path / "slides" / name).write_bytes(b"fake")
    for name in ("a1.mp4", "a1.subs.json", "a1.meta.json", "a1.transcript.json"):
        (tmp_path / "clip" / name).write_text("{}", encoding="utf-8")
    (tmp_path / "names.json").write_text(json.dumps({"[KOL 1]": "SYNTHETIC_SECRET_NAME"}), encoding="utf-8")
    (tmp_path / "present.json").write_text(json.dumps({"pr1_third_target": "synthetic:t01.a1", "labeler": "tester", "extra": "hidden"}), encoding="utf-8")
    set_override("PRESENT_DIR", str(tmp_path))
    return tmp_path


def test_manifest_unset_and_public(assets):
    set_override("PUBLIC_MODE", 1)
    with TestClient(create_app()) as client:
        assert client.get("/api/present/manifest").status_code == 404
    set_override("PUBLIC_MODE", 0)
    with TestClient(create_app(public=True)) as client:
        assert client.get("/api/present/manifest").status_code == 404
        assert client.get("/present/clip/a1.mp4").status_code == 404
    set_override("PRESENT_DIR", None)
    with TestClient(create_app()) as client:
        assert client.get("/api/present/manifest").status_code == 404


def test_video_hides_assets(assets):
    set_override("VIDEO_MODE", 1)
    with TestClient(create_app()) as client:
        manifest = client.get("/api/present/manifest?presenter=1").json()
        assert manifest["slides"] == []
        assert not manifest["names"]
        assert all(not clip["available"] and not clip["subtitles"] for clip in manifest["clips"].values())
        assert client.get("/present/clip/a1.mp4").status_code == 404


def test_allow_list_and_no_name_disclosure(assets):
    set_override("VIDEO_MODE", 0)
    set_override("PUBLIC_MODE", 0)
    with TestClient(create_app()) as client:
        response = client.get("/api/present/manifest?presenter=1")
        assert response.status_code == 200
        assert "SYNTHETIC_SECRET_NAME" not in response.text
        assert response.json()["slides"] == ["/present/slides/01.png", "/present/slides/02.png"]
        assert "extra" not in response.json()["present"]
        for path in ("names.json", "translations.json", "present.json", "clip/a1.meta.json", "clip/a1.transcript.json", "%2e%2e/names.json", "slides/%2e%2e/names.json"):
            assert client.get("/present/" + path).status_code == 404
        assert client.get("/present/clip/a1.mp4").status_code == 200


def test_clip_attribution_requires_presenter(assets):
    (assets / "clip" / "a1.meta.json").write_text(json.dumps({"speaker_line": "Synthetic attribution", "source_line": "Synthetic source"}), encoding="utf-8")
    with TestClient(create_app()) as client:
        assert "speaker_line" not in client.get("/api/present/manifest").json()["clips"]["a1"]
        assert client.get("/api/present/manifest?presenter=1").json()["clips"]["a1"]["speaker_line"] == "Synthetic attribution"


def test_law_display_uses_guardrail_fake(monkeypatch):
    import sys
    from types import ModuleType
    fake = ModuleType("pipeline.guardrail")
    fake.display_text = lambda anchor: "Filtered synthetic text: " + anchor
    monkeypatch.setitem(sys.modules, "pipeline.guardrail", fake)
    with TestClient(create_app()) as client:
        response = client.get("/api/ui/law/SYNTHETIC/D1")
        assert response.json() == {"display_text": "Filtered synthetic text: SYNTHETIC/D1"}


def test_missing_guardrail_is_not_ready(monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "pipeline.guardrail":
            raise ModuleNotFoundError(name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with TestClient(create_app()) as client:
        response = client.get("/api/ui/law/SYNTHETIC/D1")
        assert response.status_code == 503
        assert response.json() == {"todo": "pipeline.guardrail"}
