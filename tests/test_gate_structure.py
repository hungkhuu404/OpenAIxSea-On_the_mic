import builtins
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from core.models import GateCheck
from gate import run


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "A. sample project" / "wt root"
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "AGENTS.md").write_text("Synthetic candidate\n", encoding="utf-8")
    git(root, "add", "AGENTS.md")
    git(root, "commit", "-qm", "Fixture base")
    quote = next(row["text"] for row in json.loads((paths.LAW_DIR / "index.json").read_text(encoding="utf-8"))
                 if row["id"] == "LDUOC-2016/D6/K15")
    (root / "rules").mkdir()
    (root / "tests").mkdir()
    source = f'''"""{quote}"""
RULE_ID = "R04x"
FAMILY = "R04"
ORIGIN = "t5"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "chữa bệnh"
PRECEDENCE = 10
def check(atom_view):
    return []
'''
    (root / "rules/R04x.py").write_text(source, encoding="utf-8")
    meta = dict(id="R04x", family="R04", origin="t5", anchors=["LDUOC-2016/D6/K15"],
                law_span="chữa bệnh", precedence=10)
    (root / "rules/R04x.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "tests/test_R04x.py").write_text("def test_candidate():\n    assert True\n", encoding="utf-8")
    return root


@pytest.fixture
def behaviour(monkeypatch):
    fake = SimpleNamespace(run_behaviour=lambda *args, **kwargs: SimpleNamespace(
        checks=[GateCheck(id=i, passed=True, detail="fixture") for i in (8, 9, 10)],
        error_lines=[], observed_changes=[], revealed_case_ids=[]))
    monkeypatch.setitem(sys.modules, "gate.behaviour", fake)
    return fake


def checks(result):
    return {check.id: check for check in result.checks}


def test_clean_candidate(candidate, behaviour):
    result = run.run_gate("R04x", candidate)
    assert result.passed and all(check.passed for check in result.checks)
    assert checks(result)[7].detail == "not provided"


@pytest.mark.parametrize("addition", ["import os\n", "import re\nBAD = re.compile('x')\n",
                                     "from re import compile as alias\n"])
def test_impurity_blocks_execution(candidate, behaviour, monkeypatch, addition):
    path = candidate / "rules/R04x.py"
    path.write_text(path.read_text(encoding="utf-8") + addition + "raise RuntimeError('must not execute')\n", encoding="utf-8")
    monkeypatch.setattr(run, "_own_tests", lambda *args: pytest.fail("executed failed static candidate"))
    result = run.run_gate("R04x", candidate)
    assert not checks(result)[1].passed
    assert checks(result)[4].detail == "blocked by failed static checks"
    assert sum("behaviour blocked" in error for error in result.error_lines) == 1


def test_docstring_and_scope(candidate, behaviour):
    path = candidate / "rules/R04x.py"
    path.write_text(path.read_text(encoding="utf-8").replace("Thông tin", "Paraphrase"), encoding="utf-8")
    (candidate / "unrelated.txt").write_text("extra", encoding="utf-8")
    (candidate / "loop_input").mkdir()
    (candidate / "loop_input/input.json").write_text("{}", encoding="utf-8")
    result = run.run_gate("R04x", candidate)
    assert not checks(result)[2].passed and not checks(result)[3].passed
    assert not any("loop_input" in error for error in result.error_lines)


@pytest.mark.parametrize("quote", ["…", "...", " … ... \n "])
def test_docstring_requires_quotation(candidate, behaviour, quote):
    path = candidate / "rules/R04x.py"
    body = path.read_text(encoding="utf-8").split('"""', 2)[2]
    path.write_text(f'"""{quote}"""{body}', encoding="utf-8")
    result = run.run_gate("R04x", candidate)
    assert not checks(result)[2].passed
    assert "contains no quotation" in checks(result)[2].detail
    assert checks(result)[4].detail == "blocked by failed static checks"


def test_own_test_failure(candidate, behaviour):
    (candidate / "tests/test_R04x.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    assert not checks(run.run_gate("R04x", candidate))[4].passed


def test_forbidden_anchor(candidate, behaviour):
    path = candidate / "rules/R04x.meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["anchors"] = ["LQC-2012/D8/K10"]
    path.write_text(json.dumps(meta), encoding="utf-8")
    result = run.run_gate("R04x", candidate)
    assert not checks(result)[5].passed
    assert any("forbidden anchor" in error for error in result.error_lines)


def test_subprocess_environment_is_minimal(candidate, behaviour, monkeypatch):
    for name, value in dict(OPENAI_API_KEY="fake-key", LOOP_DRY="1", REGRESSION_AUTOCOMMIT="1", S1_MODEL="wrong").items():
        monkeypatch.setenv(name, value)
    set_override("LLM_BACKEND", "codex")
    (candidate / "tests/test_R04x.py").write_text('''import os
def test_environment():
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["OFFLINE"] == "1"
    assert os.environ["LLM_BACKEND"] == "codex"
    assert os.environ["LOOP_DRY"] == "0"
    assert os.environ["REGRESSION_AUTOCOMMIT"] == "0"
    assert os.environ["S1_MODEL"] == "gpt-6-luna"
    assert "PYTHONPATH" not in os.environ
    assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
''', encoding="utf-8")
    assert run.run_gate("R04x", candidate).passed


def test_missing_behaviour_fails_closed(candidate, monkeypatch):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "gate.behaviour":
            raise ModuleNotFoundError("missing", name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    result = run.run_gate("R04x", candidate)
    assert not result.passed and all(not checks(result)[i].passed for i in (8, 9, 10))
    assert result.error_lines == ["R04x: not ready: gate.behaviour"]


def test_ci_scope(candidate, behaviour):
    base = git(candidate, "rev-parse", "HEAD").stdout.decode().strip()
    for name in ("eval/synthetic.jsonl", "cache/llm/synthetic.json"):
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Fixture rule")
    result = run.run_gate("R04x", candidate, ci=True, base=base, log="ignored")
    assert result.passed and checks(result)[3].passed
    assert all(checks(result)[i].detail == "not provided" for i in (7, 8))
    (candidate / "pipeline").mkdir()
    (candidate / "pipeline/extra.py").write_text("", encoding="utf-8")
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Fixture extra")
    assert not checks(run.run_gate("R04x", candidate, ci=True, base=base))[3].passed


def test_ci_scope_detects_renamed_source(candidate, behaviour):
    destination = candidate / "tests/test_R04x.py"
    source = candidate / "tests/test_security.py"
    source.write_bytes(destination.read_bytes())
    git(candidate, "add", "tests/test_security.py")
    git(candidate, "commit", "-qm", "Fixture security test")
    base = git(candidate, "rev-parse", "HEAD").stdout.decode().strip()
    destination.unlink()
    git(candidate, "mv", "tests/test_security.py", "tests/test_R04x.py")
    git(candidate, "add", ".")
    git(candidate, "commit", "-qm", "Fixture rename")
    result = run.run_gate("R04x", candidate, ci=True, base=base)
    assert not checks(result)[3].passed
    assert "tests/test_security.py" in checks(result)[3].detail


def test_overlap_without_precedence(candidate, behaviour, monkeypatch, tmp_path):
    meta_path = candidate / "rules/R04x.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["precedence"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    source = candidate / "rules/R04x.py"
    source.write_text(source.read_text(encoding="utf-8").replace("return []", "return [1]"), encoding="utf-8")
    sample = tmp_path / "public.jsonl"
    sample.write_text(json.dumps(dict(case_id="synthetic-001", sku_id="demo_sku", text="chữa bệnh")) + "\n", encoding="utf-8")
    monkeypatch.setattr(paths, "GOLDEN_CASES", sample)
    result = run.run_gate("R04x", candidate)
    assert not checks(result)[6].passed and result.revealed_case_ids == ["synthetic-001"]
