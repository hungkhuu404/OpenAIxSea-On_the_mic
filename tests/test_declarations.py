"""Keep the draft declarations present and free of displayed monetary amounts."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PARAGRAPH = (
    "`AGENTS.md` and `docs/contracts.md` were written before the event as context files for Codex. "
    "They describe the project's conventions, rules and data contracts so that Codex can adapt "
    "to the project as fast as possible and the team spends as little time as possible on setup. "
    "They contain no code; all code in this repository was written during the event."
)


@pytest.mark.parametrize("name", ["WRITEUP.md", "DECLARATIONS.md"])
def test_draft_exists_and_has_no_fine_amount(name):
    path = ROOT / "docs" / name
    assert path.is_file(), f"Missing draft: {name}"
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\d[\d.,\s]*\s+đồng\b", text, flags=re.IGNORECASE), name


def test_context_declaration_is_verbatim():
    text = (ROOT / "docs" / "DECLARATIONS.md").read_text(encoding="utf-8")
    assert CONTEXT_PARAGRAPH in text
