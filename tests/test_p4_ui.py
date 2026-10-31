"""Exercise presentation modules using synthetic DOM values, without a browser."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_p4_ui():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for UI module tests")
    result = subprocess.run([node, "tests/p4_ui.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
