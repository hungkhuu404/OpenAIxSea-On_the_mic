"""Exercise session event refresh against synthetic DOM values."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_session_events():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for UI module tests")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "tests/session_ui.mjs"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
