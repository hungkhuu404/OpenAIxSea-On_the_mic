import json
import hashlib
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
from types import ModuleType

import pytest

from core import paths
from core.config import set_override
from core.models import ClusterFile, ClusterRun, GateCheck, GateResult, Job, RuleMeta
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
    real = [json.loads(line) for line in (ROOT / "tests/gate_fixtures/logs/codex_real_macos.jsonl").read_text(encoding="utf-8").splitlines()]
    command = next(row["item"]["command"] for row in real if row.get("item", {}).get("type") == "command_execution")
    shell = shlex.split(command)[:2]
    examples = draft.ALLOWED_COMMANDS.splitlines() + [
        "FAIL: cmd /c type loop_input/%NAME%.json", "FAIL: cmd /c type loop_input/!NAME!.json"]
    for number, line in enumerate(examples):
        if not line.startswith(("PASS: ", "FAIL: ")):
            continue
        passing = line.startswith("PASS: ")
        command = line.split(": ", 1)[1].replace("<rule_id>", "R04x")
        if not command.startswith(("powershell ", "cmd ")):
            command = " ".join(shell) + " " + shlex.quote(command)
        log = tmp_path / f"log-{number}.jsonl"
        log.write_text(json.dumps(dict(type="item.completed", item=dict(
            type="command_execution", command=command))) + "\n", encoding="utf-8")
        _, errors = scan_log(log, root, "R04x")
        assert len(errors) == (0 if passing else 1), (command, errors)


@pytest.mark.parametrize("launcher", [False, True], ids=["direct", "launcher"])
def test_retry_pid_command_sparse_checkout_and_provenance(world, monkeypatch, launcher):
    import ledger

    if launcher:
        script = world["base"].parent / "launcher with spaces.py"
        shutil.copyfile(ROOT / "tests/loop_fixtures/fake_codex_launcher.py", script)
        worker = world["base"].parent / "fake tool with spaces.py"
        set_override("CODEX_CMD", f'"{sys.executable}" "{script}" "{worker}"')
    original = subprocess.Popen
    managed = []

    def popen(args, *positional, **kwargs):
        process = original(args, *positional, **kwargs)
        if "--sandbox" in args:
            managed.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    original_execute = draft._execute

    def execute(job, worktree, number, log_dir):
        try:
            return original_execute(job, worktree, number, log_dir)
        finally:
            assert ledger.get_job(job.job_id).pid is None
            assert not (paths.jobs_dir() / f"{job.job_id}.pid").exists()

    monkeypatch.setattr(draft, "_execute", execute)
    result = job(world)
    assert result.status == "passed", result.attempts
    assert len(result.attempts) == 2 and result.pid is None and not result.dry
    assert not (paths.jobs_dir() / "job1.pid").exists()
    assert draft.funnel(world["digest"]) == dict(K=1, I=0, J=1, R=0)
    first = json.loads((world["capture"] / "job1-1.json").read_text(encoding="utf-8"))
    second = json.loads((world["capture"] / "job1-2.json").read_text(encoding="utf-8"))
    assert first["prompt"] == draft.ALLOWED_COMMANDS + "\n\n" + draft.FOLLOW
    assert second["retry"] == draft.ALLOWED_COMMANDS + "\n\nGate errors from the previous attempt:\n" + result.attempts[0].error_lines[0] + "\n"
    assert len(managed) == 2
    for record, process in zip((first, second), managed, strict=True):
        assert record["job_pid"] == int(record["pidfile"]) == process.pid
        assert record["worker_pid"] > 0
        assert process.poll() == 0
        if launcher:
            assert record["worker_pid"] != process.pid
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
    import ledger
    assert ledger.events_since(0)[-1].payload == dict(job_id="job1", status="failed", pr_url=None)


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


def test_repeat_with_real_allocator_opens_only_one_fake_pr(world, monkeypatch):
    import ledger

    set_override("LOOP_OPEN_PR", 1)
    world["outcomes"][:] = [True]
    real_git, real_run = draft._git, subprocess.run
    pushes, prs = [], []

    def git_call(root, *args):
        if args[0] == "push":
            pushes.append(args)
            return ""
        return real_git(root, *args)

    def run(args, **kwargs):
        if args[0] == "gh":
            prs.append(args)
            body = Path(args[-1]).read_text(encoding="utf-8")
            assert "pending human review" in body and "source_items" in body
            return subprocess.CompletedProcess(args, 0, stdout="https://example.invalid/pr/1\n")
        return real_run(args, **kwargs)

    monkeypatch.setattr(draft, "_git", git_call)
    monkeypatch.setattr(subprocess, "run", run)
    jobs = cli.run_cluster(world["path"], repeat=2)
    assert {row.rule_id for row in jobs} == {"R04b"}
    assert len({row.branch for row in jobs}) == 2
    assert all(row.status == "passed" for row in jobs)
    assert len(pushes) == len(prs) == 1
    assert jobs[0].pr_url and jobs[1].pr_url is None
    restored = ClusterFile.model_validate_json(world["path"].read_text(encoding="utf-8"))
    assert len(restored.runs) == 2
    assert all(isinstance(run, ClusterRun) for run in restored.runs)
    assert [run.result for run in restored.runs] == ["passed", "passed"]
    expected = dict(K=2, I=2, J=0, R=0)
    ledger.init_db(world["base"] / ".local/fresh.db")
    assert draft.funnel(world["digest"]) == expected


def test_suffixed_rule_uses_registered_metadata_family(world, monkeypatch):
    from rules import registry

    meta = RuleMeta(id="R04b", family="R04", origin="t5", article_id="LDUOC-2016/D6",
        anchors=["LDUOC-2016/D6/K15"], law_span="chữa bệnh", trigger="Synthetic family mapping.",
        precedence=14, status="proposed")
    info = registry.RuleInfo("R04b", ModuleType("synthetic_rule"), meta)
    monkeypatch.setattr(registry, "all_rules", lambda: [info])
    correction = world["source"] | dict(rule_ids=["R04b"])
    assert draft._family([correction]) == "R04"
    assert draft._family([correction, world["source"]]) == "R04"


def test_default_never_pushes_or_calls_gh(world, monkeypatch):
    real_git, real_run = draft._git, subprocess.run

    def git_call(root, *args):
        assert args[0] != "push"
        return real_git(root, *args)

    def run(args, **kwargs):
        assert args[0] != "gh"
        return real_run(args, **kwargs)

    monkeypatch.setattr(draft, "_git", git_call)
    monkeypatch.setattr(subprocess, "run", run)
    assert job(world).status == "passed"


def test_inputs_do_not_leak_evaluation_or_other_memory(world):
    extra = world["source"] | dict(item_id="unrelated", text="Unrelated synthetic correction memory")
    with paths.regressions_file().open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(extra) + "\n")
    assert job(world).status == "passed"
    folder = paths.worktree_root() / "job1/loop_input"
    permitted = {item["text"] for rows in draft._read(folder / "permitted_items.json").values() for item in rows}
    inputs = "\n".join(path.read_text(encoding="utf-8") for path in folder.iterdir())
    golden = [json.loads(line) for line in paths.GOLDEN_CASES.read_text(encoding="utf-8").splitlines() if line]
    assert all(case["text"] not in inputs for case in golden if case["text"] not in permitted)
    assert extra["text"] not in inputs


@pytest.mark.parametrize("problem", ["missing", "exception", "nonzero"])
def test_execution_errors_are_recorded_and_nonzero_gate_pass_retries(world, monkeypatch, problem):
    import gate
    import ledger

    world["outcomes"][:] = [True]
    if problem == "missing":
        set_override("CODEX_CMD", "missing-synthetic-codex")
    elif problem == "exception":
        def broken(*args, **kwargs):
            raise RuntimeError("Synthetic gate exception")
        monkeypatch.setattr(gate, "run_gate", broken)
    else:
        monkeypatch.setenv("DRAFT_TEST_EXIT", "7")
    result = job(world)
    assert result.status == ("failed" if problem == "nonzero" else "error")
    assert len(result.attempts) == (3 if problem == "nonzero" else 1)
    assert all(row.error_lines for row in result.attempts)
    assert result.pid is None and not (paths.jobs_dir() / "job1.pid").exists()
    events = ledger.events_since(0)
    assert sum(row.type == "job.attempt" for row in events) == len(result.attempts)
    assert events[-1].type == "job.finished" and events[-1].payload["status"] == result.status


@pytest.mark.parametrize("needs_kill", [False, True], ids=["terminate", "kill-after-wait-timeout"])
def test_attempt_exception_reaps_managed_process_and_clears_pid(world, monkeypatch, needs_kill):
    import ledger

    original = subprocess.Popen
    calls = []

    class Process:
        pid = 87654
        returncode = None

        def communicate(self, prompt):
            assert ledger.get_job("job1").pid == self.pid
            assert int((paths.jobs_dir() / "job1.pid").read_text(encoding="utf-8")) == self.pid
            assert prompt == draft.ALLOWED_COMMANDS + "\n\n" + draft.FOLLOW
            raise subprocess.TimeoutExpired("synthetic worker", 1)

        def terminate(self):
            calls.append("terminate")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if needs_kill and timeout is not None:
                raise subprocess.TimeoutExpired("synthetic managed process", timeout)
            self.returncode = 1
            return self.returncode

        def kill(self):
            calls.append("kill")

    process = Process()

    def popen(args, *positional, **kwargs):
        if "--sandbox" in args:
            return process
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = job(world)
    assert result.status == "error" and len(result.attempts) == 1
    assert "timed out" in result.attempts[0].error_lines[0]
    assert calls == (["terminate", ("wait", 5), "kill", ("wait", None)] if needs_kill
                     else ["terminate", ("wait", 5)])
    assert process.returncode == 1
    assert result.pid is None and not (paths.jobs_dir() / "job1.pid").exists()


def test_cluster_autocommit_records_previous_commit_on_next_write(world):
    destination = world["base"] / "eval"
    shutil.copytree(paths.eval_dir(), destination, dirs_exist_ok=True)
    set_override("EVAL_DIR", "eval")
    set_override("REGRESSION_AUTOCOMMIT", 1)
    path = paths.clusters_dir() / "synthetic.json"
    from core.models import ClusterFile
    cluster = ClusterFile.model_validate(draft._read(path))
    first = draft._save_cluster(path, cluster)
    assert draft._read(path)["commit"] is None
    draft._save_cluster(path, cluster)
    assert draft._read(path)["commit"] == first


@pytest.mark.skipif(os.name == "nt", reason="Executable symlink fixture is POSIX-only")
def test_quoted_executable_path_with_spaces(world):
    executable = world["base"].parent / "python with spaces"
    executable.symlink_to(sys.executable)
    fake = world["base"].parent / "fake tool with spaces.py"
    set_override("CODEX_CMD", f'"{executable}" "{fake}"')
    assert job(world).status == "passed"


def test_real_labels_t5_dispatch_to_real_dry_runner(world, monkeypatch):
    import ledger
    from threading import Event
    from core.models import Atom, NearHit, Session, Turn, Verdict
    from loop import labels, t5

    set_override("LOOP_DRY", 1)
    done = Event()
    original = draft.run_job
    results = []

    def run(job_id):
        try:
            results.append(original(job_id))
        finally:
            done.set()

    monkeypatch.setattr(draft, "run_job", run)
    items = []
    for number in range(3):
        session_id = f"synthetic-{number}"
        text = f"Sản phẩm phòng thêm bệnh {number}"
        session = Session(session_id=session_id, sku_pin="demo_sku", as_of="2025-01-08",
            channel="written", source="Synthetic fixture", turns=[Turn(turn_id="t1", speaker_label="[KOL 1]", attribution="constructed", text=text)])
        ledger.add_session(session)
        atom = Atom(atom_id="a1", turn_id="t1", span=text, start=0, end=len(text), axis="cong_dung",
            speaker_role="kol", subject_is_product=True, near_hits=[NearHit(pattern="phòng … bệnh", gap=1,
                law_anchor="LDUOC-2016/D6/K15", law_span="chữa bệnh", span=text)])
        ledger.add_atoms(session_id, [atom])
        verdict = Verdict(atom_key=f"{session_id}:a1", rule_pack_version=1, verdict="abstain",
            decided_by="abstain:DOSSIER_EMPTY", reason_code="DOSSIER_EMPTY", as_of=session.as_of,
            dossier_hash="synthetic", ts=draft._now())
        ledger.append_verdicts([verdict])
        items.append(labels.record_label(verdict.atom_key, "red", "R04", "fixture"))
    assert done.wait(10), "Synthetic T5 runner did not finish"
    assert len(results) == 1 and results[0].status == "passed" and results[0].dry
    result = results[0]
    expected = hashlib.sha256(json.dumps(sorted((i.item_id, i.text) for i in items),
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert result.cluster_hash == expected
    assert draft.funnel(expected) == dict(K=1, I=0, J=1, R=0)
    assert t5.maybe_trigger() == []
    with pytest.raises(ValueError, match="exact cluster"):
        cli.make_cluster([item.item_id for item in reversed(items)], "duplicate")
    assert len(ledger.list_labels()) == 3
    assert sum(row.type == "job.started" for row in ledger.events_since(0)) == 1
