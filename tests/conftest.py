"""Offline isolation shared by unit tests and the demo harness."""

import os
from pathlib import Path

import pytest

from core.config import Settings, set_override, settings


def pin_test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """Reset modes in settings and the environment; preserve machine settings."""
    defaults = Settings(environ={})
    preserved = {"LLM_BACKEND", "CODEX_CMD", "LLM_TIMEOUT_S", "OPENAI_API_KEY"}
    pinned = {name: getattr(defaults, name) for name in Settings.model_fields
              if name not in preserved}
    cache = tmp_path / "local cache"
    cache.mkdir(parents=True, exist_ok=True)
    pinned.update(OFFLINE=1, LEDGER_DB=str(tmp_path / "ledger" / "ledger.db"),
                  EVAL_DIR=str(tmp_path / "eval"), LLM_LOCAL_CACHE_DIR=str(cache))
    for name, value in pinned.items():
        set_override(name, value)
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(value))
    return pinned


@pytest.fixture(autouse=True)
def isolated_settings(request, tmp_path):
    if request.node.get_closest_marker("live"):
        yield
        return
    before = {name: getattr(settings, name) for name in Settings.model_fields}
    with pytest.MonkeyPatch.context() as patch:
        for name in Settings.model_fields:
            if name in os.environ:
                patch.setenv(name, os.environ[name])
            else:
                patch.delenv(name, raising=False)
        pin_test_settings(patch, tmp_path)
        try:
            yield
        finally:
            for name, value in before.items():
                set_override(name, value)
