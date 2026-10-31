import json
import re
from pathlib import Path

import pytest

from gate.logscan import scan_log

FIXTURE = Path(__file__).parent / "gate_fixtures/logs/codex_real_macos.jsonl"


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "A. sample project" / "wt root"
    for name in ("loop_input/note.txt", "core/sample.py", ".agents/skills/draft-rule/SKILL.md",
                 "rules/R04x.py", "rules/R04x.meta.json", "rules/R04.meta.json", "tests/test_R04x.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sample\n", encoding="utf-8")
    return root


def event(command):
    # Synthetic commands reuse the actual pinned CLI's completed-item envelope.
    row = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[4])
    row["item"]["command"] = command
    return row


def scan(tmp_path, candidate, rows):
    log = tmp_path / "sample.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return scan_log(log, candidate, "R04x")


def test_real_capture_is_preserved_and_bare_ls_rejected(tmp_path, candidate):
    raw = FIXTURE.read_bytes()
    assert not re.search(rb"(?i)Users|@|account|org-|user_|email", raw)
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    assert len(rows) == 14
    assert [row["item"]["exit_code"] for row in rows if row["type"] == "item.completed"
            and row.get("item", {}).get("type") == "command_execution"] == [0, 0]
    for row in rows:
        if "command" in row.get("item", {}):
            row["item"]["command"] = row["item"]["command"].replace("note.txt", "loop_input/note.txt")
    files, errors = scan(tmp_path, candidate, rows)
    assert files == ["loop_input/note.txt"]
    assert len(errors) == 2
    assert all("explicit path operand required" in error for error in errors)
    assert FIXTURE.read_bytes() == raw
    assert scan(tmp_path, candidate, rows[:7])[1] == []


def test_synthetic_ls_has_explicit_allowed_operand(tmp_path, candidate):
    files, errors = scan(tmp_path, candidate, [event("/bin/zsh -lc 'ls loop_input'")])
    assert errors == [] and "loop_input/note.txt" in files


@pytest.mark.parametrize("command", [
    "cat core/sample.py", "head -n 1 loop_input/note.txt", "tail -n 2 loop_input/note.txt",
    "/bin/zsh -lc 'sed -n 1,80p core/sample.py; rg -n sample core/sample.py'",
    "powershell -Command 'Get-Content -Raw -Encoding UTF8 .agents/skills/draft-rule/SKILL.md; Get-Content loop_input/note.txt'",
    'pwsh -Command "Get-Content -Path loop_input/note.txt -TotalCount 80 -Encoding utf8"',
    'powershell.exe -Command "Get-Content -Encoding UTF8 -LiteralPath loop_input/note.txt -Raw"',
    'Get-Content -TotalCount 80 loop_input/note.txt -Encoding utf8',
    'python -m pytest -q tests/test_R04x.py -x -s --tb=short',
    'pytest -q tests/test_R04x.py -v', 'git status --short',
    'git diff --cached --stat -- rules/R04x.py', 'cmd /c type loop_input/note.txt',
    'grep -n sample core/sample.py', 'pwd',
])
def test_allowed_commands(tmp_path, candidate, command):
    assert scan(tmp_path, candidate, [event(command)])[1] == []


@pytest.mark.parametrize("command", [
    'cat data/cases/golden.jsonl', 'cat rules/R04.meta.json', 'cat tests/test_rules_r04.py',
    'cat docs/contracts.md', 'cat ../note.txt', 'cat /outside/secret.txt',
    'OFFLINE=1 pytest -q tests/test_R04x.py', "$env:OFFLINE='1'", 'env',
    'cat <<EOF', 'cat $(pwd)', 'cat `pwd`', 'cat ${HOME}', 'cat core/sample.py > output.txt',
    'cd rules', 'python -c pass', 'git log', 'pip install package', 'python -m gate.run',
    'Get-Content -Path core/sample.py,loop_input/note.txt', 'ls rules', 'ls', 'rg sample',
    'grep sample', 'Get-Content -Encoding ascii core/sample.py',
    'Get-Content -Tail 1 core/sample.py', 'sed -n 1w core/sample.py',
    'rg --pre evil sample core/sample.py', 'git diff --output=core/sample.py',
    'pytest -q tests/test_R04x.py -p evil', 'cat core/*.py',
    'cmd /c cat core/sample.py; python -c pass',
    "/bin/zsh -lc '/bin/sh -c pwd'",
])
def test_one_bad_command_produces_one_error(tmp_path, candidate, command):
    _, errors = scan(tmp_path, candidate, [event(command)])
    assert len(errors) == 1 and errors[0].startswith("Log line 1: ")


def test_absolute_paths_backslashes_and_case(tmp_path, candidate):
    absolute = str(candidate / "core/sample.py").replace("/", "\\")
    assert scan(tmp_path, candidate, [event(f'cat "{absolute}"')])[1] == []
    assert scan(tmp_path, candidate, [event('cat CORE/SAMPLE.PY')])[1] == []


def test_file_events_and_fail_closed(tmp_path, candidate):
    valid = [{"type": "item.completed", "item": {"type": "file_change", "changes": [
        {"path": "rules/R04x.meta.json", "kind": "update"}, {"path": "tests/test_R04x.py", "kind": "add"}]}},
        {"type": "item.completed", "item": {"type": "file_read", "path": "core/sample.py"}},
        {"type": "item.completed", "item": {"type": "read_file", "path": "rules/R04x.meta.json"}}]
    assert scan(tmp_path, candidate, valid)[1] == []
    for row in ({"type": "item.completed", "item": {"type": "mcp_tool_call"}},
                {"type": "item.completed", "item": {"type": "web_search"}},
                {"type": "item.completed", "item": {}}, {"type": "future.event"},
                {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "core/sample.py"}]}}):
        assert len(scan(tmp_path, candidate, [row])[1]) == 1
    assert len(scan(tmp_path, candidate, [event("ls"), event("ls")])[1]) == 1


def test_symlink_cannot_hide_forbidden_file(tmp_path, candidate):
    (candidate / "core/link.py").symlink_to(candidate / "rules/R04.meta.json")
    assert len(scan(tmp_path, candidate, [event("cat core/link.py")])[1]) == 1
