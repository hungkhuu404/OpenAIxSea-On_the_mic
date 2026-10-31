"""Static shell checks independent of pipeline and ledger availability."""

from pathlib import Path
import re

from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_local_resources_and_fonts():
    for path in (ROOT / "ui").rglob("*"):
        if path.is_file() and path.suffix in {".js", ".css", ".html"}:
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"https?://|(?:src|href)=[\"']//", text)
            assert not re.search(r"\b(?:Inter|Geist)\b", text)
    css = (ROOT / "ui/styles/app.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", css)


def test_palette_variables():
    text = (ROOT / "ui/styles/tokens.css").read_text(encoding="utf-8")
    for palette in ("oil", "graphite"):
        block = re.search(r'\[data-palette="' + palette + r'"\]\s*\{([^}]+)', text)[1]
        for name in ("bg", "s1", "s2", "line", "t1", "t2", "t3", "accent", "green", "yellow", "red", "abstain"):
            assert f"--{name}:" in block


def test_local_font_files_and_licenses():
    css = (ROOT / "ui/styles/fonts.css").read_text(encoding="utf-8")
    for path in re.findall(r"url\('([^']+)'\)", css):
        assert (ROOT / "ui" / path.lstrip("/")).read_bytes().startswith(b"wOF2")
    for name in ("be-vietnam-pro", "newsreader", "jetbrains-mono"):
        license_text = (ROOT / f"ui/fonts/{name}-OFL.txt").read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in license_text


def test_shell_and_synthetic_fixture():
    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        data = client.get("/api/ui/fixture").json()
        assert len(data["atoms"]) == 5
        assert data["turns"][1]["display_speaker"] == "Speaker unknown"
        assert data["turns"][2]["display_speaker"].startswith("Reenactment line")
