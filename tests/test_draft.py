import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType

import pytest

from core import paths
from core.config import set_override
from core.models import GateCheck, GateResult, Job
from loop import cli, draft


ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def world(monkeypatch, tmp_path):
    import ledger
    import gate

    base = tmp_path / "project with spaces.and.dots"
    base.mkdir()
    git(base, "init", "-b", "main")
    git(base, "config", "user.name", "Fixture Owner")
    git(base, "config", "user.email", "fixture@example.invalid")
    for name in ("AGENTS.md", "pytest.ini", "requirements.txt", "rules/R04.py", "rules/_common.py",
                 "rules/R04.meta.json", "rules/collisions.txt", "rules/law_basis.json",
                 "core/models.py", "textnorm/__init__.py", ".agents/skills/draft-rule/SKILL.md",
                 "data/law/nd-15-2018/dieu-27.md", "tests/loop_fixtures/dry_attempts.json"):
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for name in ("docs/contracts.md", "gate/extra.py", "eval/extra.json", "cache/llm/extra.json",
                 "data/cases/golden.jsonl", "data/demo/extra.json", "data/evidence/extra.json", "reports/extra.json"):
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("Excluded synthetic fixture", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "Synthetic fixture")
    monkeypatch.setattr(paths, "REPO_ROOT", base)
    monkeypatch.setattr(paths, "LOCAL_DIR", base / ".local")
    set_override("WORKTREE_ROOT", str(tmp_path / "B. test projects" / "wt root"))
    ledger.init_db(paths.ledger_db())
    source = dict(item_id="item1", level="atom", text="Demo phòng thêm bệnh.",
                  sku_id="demo_sku", as_of="2025-01-08", label="red", rule_ids=["R04"],
                  labeler="hungkhuu404", ts="2026-10-31T00:00:00Z")
    paths.regressions_file().parent.mkdir(parents=True)
    paths.regressions_file().write_text(json.dumps(source) + "\n", encoding="utf-8")
    cluster_path, digest, _ = cli.make_cluster(["item1"], "synthetic")
    capture = tmp_path / "capture"
    capture.mkdir()
    fake = tmp_path / "fake tool with spaces.py"
    shutil.copyfile(ROOT / "tests/loop_fixtures/fake_codex.py", fake)
    set_override("CODEX_CMD", f'"{sys.executable}" "{fake}"')
    monkeypatch.setenv("DRAFT_TEST_CAPTURE", str(capture))
    monkeypatch.setenv("DRAFT_TEST_LEDGER", str(paths.ledger_db()))
    monkeypatch.setenv("DRAFT_TEST_JOBS", str(paths.jobs_dir()))
    calls = []
    outcomes = [False, True]

    def run_gate(rule_id, candidate_dir, log=None, sources=None):
        assert Path(candidate_dir).parent == paths.worktree_root()
        assert sources == cluster_path
        calls.append((candidate_dir, log))
        passed = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        return GateResult(passed=passed, checks=[GateCheck(id=7, passed=True, detail="fixture")],
            error_lines=[] if passed else [f"{rule_id} check 4: fix the synthetic near miss."])
    monkeypatch.setattr(gate, "run_gate", run_gate)
    return dict(base=base, capture=capture, digest=digest, path=cluster_path,
                outcomes=outcomes, calls=calls, source=source)


def job(world, job_id="job1", rule_id="R04b"):
    import ledger
    ledger.create_job(Job(job_id=job_id, kind="draft_rule", rule_id=rule_id,
        branch=f"rule/{rule_id}-{job_id}", cluster_key="synthetic", cluster_item_ids=["item1"],
        cluster_hash=world["digest"], status="queued", dry=False, config="", ts=draft._now()))
    return draft.run_job(job_id)


def test_allowed_block_skill_and_gate_agree():
    from gate.run import PURITY_IMPORTS, PURITY_FORBIDDEN
    body = (ROOT / ".agents/skills/draft-rule/SKILL.md").read_text(encoding="utf-8")
    assert body.split("## Allowed commands\n\n", 1)[1].rstrip("\n") == draft.ALLOWED_COMMANDS
    assert len(draft.ALLOWED_COMMANDS.splitlines()) < 60
    for prefix, values in [("Allowed imports: ", PURITY_IMPORTS),
                           ("Rejected names (including attributes): ", PURITY_FORBIDDEN)]:
        printed = next(line.removeprefix(prefix) for line in draft.ALLOWED_COMMANDS.splitlines() if line.startswith(prefix))
        assert tuple(printed.split(", ")) == values
    assert all(word in body for word in ("re.search", "re.finditer", "re.fullmatch"))
    assert not any(line.startswith("OFFLINE=1 ") for line in body.splitlines())


def test_block_examples_pass_or_fail_the_real_logscan(tmp_path):
    from gate.logscan import scan_log
    root = tmp_path / "candidate with spaces"
    for name in (".agents/skills/draft-rule/SKILL.md", "loop_input/task.json", "loop_input/sources.json",
                 "rules/R04.py", "rules/_common.py", "rules/R04x.meta.json", "tests/test_R04x.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    for number, line in enumerate(draft.ALLOWED_COMMANDS.splitlines()):
        if not line.startswith(("PASS: ", "FAIL: ")):
            continue
        passing = line.startswith("PASS: ")
        command = line.split(": ", 1)[1].replace("<rule_id>", "R04x")
        log = tmp_path / f"log-{number}.jsonl"
        log.write_text(json.dumps(dict(type="item.completed", item=dict(
            type="command_execution", command=command))) + "\n", encoding="utf-8")
        _, errors = scan_log(log, root, "R04x")
        assert len(errors) == (0 if passing else 1), (command, errors)


def test_retry_pid_command_sparse_checkout_and_provenance(world):
    result = job(world)
    assert result.status == "passed", result.attempts
    assert len(result.attempts) == 2 and result.pid is None and not result.dry
    assert not (paths.jobs_dir() / "job1.pid").exists()
    assert draft.funnel(world["digest"]) == dict(K=1, I=0, J=1, R=0)
    first = json.loads((world["capture"] / "job1-1.json").read_text(encoding="utf-8"))
    second = json.loads((world["capture"] / "job1-2.json").read_text(encoding="utf-8"))
    assert first["prompt"] == draft.ALLOWED_COMMANDS + "\n\n" + draft.FOLLOW
    assert second["retry"] == draft.ALLOWED_COMMANDS + "\n\nGate errors from the previous attempt:\n" + result.attempts[0].error_lines[0] + "\n"
    assert first["pid"] == first["job_pid"] == int(first["pidfile"])
    args = first["args"]
    assert args[:4] == ["exec", "--sandbox", "workspace-write", "--json"]
    assert args[-1] == "-" and f"model_reasoning_effort=low" in args
    assert draft.FOLLOW not in " ".join(args)
    worktree = Path(first["cwd"])
    tracked = git(worktree, "ls-files", "-t").splitlines()
    present = [line[2:] for line in tracked if not line.startswith("S ")]
    assert all(name in {"AGENTS.md", "pytest.ini", "requirements.txt", "rules/collisions.txt", "rules/law_basis.json"}
               or name.startswith(("core/", "textnorm/", "data/law/", ".agents/skills/draft-rule/"))
               or (name.startswith("rules/") and (name.endswith(".py") or name == "rules/R04b.meta.json"))
               or name == "tests/test_R04b.py" for name in present)
    assert not (worktree / "rules/R04.meta.json").exists()
    assert not (worktree / "docs").exists()
    assert set(p.name for p in (worktree / "loop_input").iterdir()) >= {
        "task.json", "sources.json", "article.md", "disclaimer.txt", "permitted_items.json",
        "collisions.txt", "schema.json", "retry.md", "out.json"}
    meta = json.loads((worktree / "rules/R04b.meta.json").read_text(encoding="utf-8"))
    assert len(meta["attempts"]) == 2
    assert all(set(a) == {"ts", "gate_result", "error_lines", "files_read"} for a in meta["attempts"])
    assert not (worktree / "loop_input/pr-body.txt").exists()
    assert git(worktree, "status", "--porcelain").startswith("?? loop_input/")


def test_three_failed_attempts_leave_sources_unchanged(world):
    world["outcomes"][:] = [False]
    before = paths.regressions_file().read_bytes()
    result = job(world)
    assert result.status == "failed" and len(result.attempts) == 3
    assert paths.regressions_file().read_bytes() == before
    assert draft.funnel(world["digest"]) == dict(K=1, I=0, J=0, R=1)
    assert draft._read(world["path"])["runs"][0]["result"] == "failed"


def test_cache_miss_is_error_without_retry(world, monkeypatch):
    import gate
    monkeypatch.setattr(gate, "run_gate", lambda *a, **kw: GateResult(passed=False, error_lines=[draft.CACHE_MISS]))
    result = job(world)
    assert result.status == "error" and len(result.attempts) == 1
    assert result.attempts[0].error_lines == [draft.CACHE_MISS]
    assert draft.funnel(world["digest"]) == dict(K=0, I=0, J=0, R=0)


@pytest.mark.parametrize("setting", ["DRAFT_TEST_MODEL_ERROR", "DRAFT_TEST_APPROVED"])
def test_model_or_approval_error_never_retries(world, monkeypatch, setting):
    monkeypatch.setenv(setting, "1")
    result = job(world)
    assert result.status == "error" and len(result.attempts) == 1


def test_dry_mode_does_not_start_codex_or_publish(world, monkeypatch):
    set_override("LOOP_DRY", 1)
    set_override("LOOP_OPEN_PR", 1)
    monkeypatch.setattr(draft, "_execute", lambda *a: pytest.fail("dry mode started Codex"))
    result = job(world)
    assert result.dry and result.status == "passed"
    assert not (paths.worktree_root() / "job1").exists()
    assert not list(world["capture"].glob("job1-*.json"))


def test_cli_repeat_uses_one_allocation(world, monkeypatch):
    fake = ModuleType("loop.t5")
    allocations = []
    fake.allocate_rule_id = lambda family: allocations.append(family) or "R04b"
    monkeypatch.setitem(sys.modules, "loop.t5", fake)
    jobs = cli.run_cluster(world["path"], repeat=2)
    assert allocations == ["R04"]
    assert len({j.branch for j in jobs}) == 2
    assert {j.rule_id for j in jobs} == {"R04b"}
    assert len(draft._read(world["path"])["runs"]) == 2
    calls = [json.loads(line) for line in (world["capture"] / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert calls.count(["exec", "--help"]) == 1
    # Each job also obtains its required cfg through core.config, which probes --version.
    assert calls.count(["--version"]) == 3
