"""Run Review module tests against small DOM values, without browser access."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_review_module():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for UI module tests")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "tests/review_ui.mjs"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
