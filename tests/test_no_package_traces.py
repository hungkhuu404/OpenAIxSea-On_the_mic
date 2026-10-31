"""Keep public tracked files free of local coordination material and secrets."""

from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _path_marker(path: str) -> str | None:
    normalized = path.casefold()
    parts = PurePosixPath(normalized).parts
    name = parts[-1]
    files = ["PLANS.md", ".env", "DIEU_PHOI.md", "VIEC_*.md", "HUNG_QUYET.md",
             "RA_SOAT.md", "TOM_TAT_*.md", "THAY_DOI_*.md", "CODEX_CONTEXT_*.md",
             "NHAT_KY_*.md", "BRIEF.md", "TASK.md", "*.zip", "*.mp4",
             "rollout-*.jsonl", "names.json"]
    for pattern in files:
        if fnmatchcase(name, pattern.casefold()):
            return pattern
    if normalized.startswith("docs/prompt-runs/"):
        return "docs/prompt-runs/"
    team_folder = "goi_" + "3110"
    if team_folder in parts[:-1]:
        return team_folder
    if parts[0] in {"hung", "dai", "tan", "phuc", "van_hanh", "repo_files"}:
        return parts[0] + "/"
    return None


def _text_markers() -> list[str]:
    # Build complete markers only at runtime so this test scans itself too.
    markers = ["TEAM" + "_DIR", "goi_" + "3110"]
    phrases = ["\u0110\u00c3 MERGE", "TH\u1edcI GIAN", "B\u1eaeT \u0110\u1ea6U",
               "K\u1eb8T", "X" + "ONG", "C\u1ea4U H\u00ccNH"]
    for phrase in phrases:
        marker = "[" + phrase + "]"
        markers.extend(_encoding_variants(marker))
    start = "B\u1eaft \u0111\u1ea7u khi"
    markers.extend(_encoding_variants(start))
    return markers


def _encoding_variants(text: str) -> list[str]:
    windows = []
    for byte in text.encode("utf-8"):
        try:
            windows.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            windows.append(chr(byte))
    return [text, text.encode("utf-8").decode("latin-1"), "".join(windows)]


def _text_marker(text: str) -> str | None:
    for marker in _text_markers():
        if marker in text:
            return marker
    patterns = [r"(?i)C:" + r"[\\/]" + "otm", r"(?i)D:" + r"[\\/]B\.",
                "/" + r"Users/[^/\s]+/", r"(?<![A-Za-z0-9])" + "s" + r"k-[A-Za-z0-9-]{20,}"]
    for pattern in patterns:
        if re.search(pattern, text):
            # Report the marker category, never reproduce a matching secret.
            return pattern
    return None


def test_no_package_traces():
    try:
        probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                               cwd=ROOT, capture_output=True, check=False)
    except FileNotFoundError:
        pytest.fail("git is required to inspect tracked public files")
    if probe.returncode:
        if b"not a git repository" in probe.stderr:
            pytest.skip("Public hygiene requires a git checkout; this folder is not one")
        pytest.fail("git could not inspect the checkout")
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True).stdout.decode("utf-8").split("\0")
    failures = []
    for path in filter(None, tracked):
        marker = _path_marker(path)
        if marker:
            failures.append(f"{path}: forbidden path marker {marker}")
            continue
        if path in {"AGENTS.md", "docs/contracts.md"}:
            continue
        raw = (ROOT / path).read_bytes()
        if b"\0" in raw:
            continue
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        marker = _text_marker(content)
        if marker:
            failures.append(f"{path}: forbidden text marker {marker}")
    assert not failures, "\n".join(failures)


def test_hygiene_detector_recognizes_markers_without_matching_slugs():
    for marker in _text_markers():
        assert _text_marker("prefix " + marker + " suffix") is not None
    synthetic_key = "s" + "k-" + "x" * 24
    assert _text_marker('"' + synthetic_key + '"') is not None
    assert _text_marker("tpbv" + synthetic_key) is None
