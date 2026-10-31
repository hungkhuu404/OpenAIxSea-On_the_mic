"""Run production entrypoints inside a disposable repository and ledger."""

from contextlib import contextmanager, redirect_stdout
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import subprocess
import sys
import tarfile
from time import perf_counter

import pytest

from .support import record


NAMESPACES = {"core", "textnorm", "pipeline", "rules", "ledger", "api", "loop", "scripts", "gate"}


def require(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            pytest.skip(f"not ready: {name}")
        raise


def export_head(source, destination):
    """One archive read includes only committed code/data/cache, never local rows."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    roots = ["AGENTS.md", ".gitignore", "docs", "core", "textnorm", "pipeline", "rules",
             "gate", "loop", "ledger", "api", "ui", "scripts", "data", "fixtures", "tests",
             "cache/llm", "pytest.ini", "requirements.txt"]
    available = subprocess.check_output(["git", "ls-tree", "--name-only", head], cwd=source, text=True).splitlines()
    roots = [path for path in roots if path.split("/")[0] in available]
    assert roots, "No permitted committed demo source directories"
    archive = subprocess.check_output(["git", "archive", "--format=tar", head, "--", *roots], cwd=source)
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for member in files:
            path = PurePosixPath(member.name)
            forbidden = any(part.startswith(("heldout", "held-out")) or part.endswith("_private")
                            or part in {"_raw", "review", "candidates.jsonl"} for part in path.parts)
            if forbidden or path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise AssertionError("Committed demo source contains a disallowed path")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(files.extractfile(member).read())
    # Git state in this template is entirely new, with no link to the real index.
    git(destination, "init", "-q")
    git(destination, "config", "user.name", "Demo Test")
    git(destination, "config", "user.email", "demo@example.invalid")
    git(destination, "config", "core.autocrlf", "false")
    git(destination, "add", "--all")
    git(destination, "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic rehearsal baseline")
    return head


def git(root, *args):
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(name, None)
    result = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, check=True)
    return result.stdout


@contextmanager
def isolated_imports(root):
    saved_path = sys.path[:]
    saved = {name: module for name, module in sys.modules.items() if name.split(".")[0] in NAMESPACES}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in NAMESPACES:
                del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path
        importlib.invalidate_caches()


def pin_demo(monkeypatch, root, shared_conftest, backend):
    spec = importlib.util.spec_from_file_location("_demo_shared_settings", shared_conftest)
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    shared.pin_test_settings(monkeypatch, root / ".local" / "controls")
    from core.config import set_override
    overrides = {"LLM_BACKEND": backend, "OFFLINE": 1, "LOOP_DRY": 0,
                 "REGRESSION_AUTOCOMMIT": 0, "LOOP_OPEN_PR": 0,
                 "PUBLIC_MODE": 0, "VIDEO_MODE": 0, "JUDGE_MODE": "two",
                 "WORKTREE_ROOT": str(root.parent / (root.name + "-jobs"))}
    for name, value in overrides.items():
        set_override(name, value)
        monkeypatch.setenv(name, str(value))
    for name in ("LLM_CACHE_DIR", "PRESENT_DIR", "DEMO_EXPECTED"):
        set_override(name, None)
        monkeypatch.delenv(name, raising=False)
    return overrides


def install_process_guard(monkeypatch, root):
    from core.config import command_args, settings

    original = subprocess.Popen
    version_args = command_args(settings.CODEX_CMD) + ["--version"]

    def popen(args, *positional, **kwargs):
        if isinstance(args, (str, bytes)) or kwargs.get("shell"):
            raise RuntimeError("Demo tests require explicit offline subprocess arguments")
        words = [str(arg) for arg in args]
        executable = Path(words[0]).name.lower()
        version_only = words == version_args
        if version_only:
            return original(args, *positional, **kwargs)
        cwd = Path(kwargs.get("cwd") or Path.cwd()).resolve()
        if executable not in {"git", "git.exe"} or not cwd.is_relative_to(root):
            raise RuntimeError("Demo tests permit only local Git and the configuration version query")
        if any(word in {"push", "pull", "fetch", "clone", "ls-remote", "submodule"} for word in words[1:]):
            raise RuntimeError("Demo tests forbid Git network operations")
        for i, word in enumerate(words[:-1]):
            if word == "-C":
                target = (cwd / words[i + 1]).resolve()
                if not target.is_relative_to(root):
                    raise RuntimeError("Demo Git must remain inside the temporary copy")
        env = kwargs.get("env") or os.environ
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            if env.get(name) and not (cwd / env[name]).resolve().is_relative_to(root):
                raise RuntimeError("Demo Git cannot use an outside index or tree")
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)


class Harness:
    def __init__(self, root, monkeypatch, reporter):
        self.root = root
        self.patch = monkeypatch
        self.reporter = reporter

    def set(self, name, value):
        from core.config import set_override
        set_override(name, value)
        if value is None:
            self.patch.delenv(name, raising=False)
        else:
            self.patch.setenv(name, str(value))

    def cli(self, *arguments):
        require("pipeline.cli")
        from pipeline.llm import run_scope
        with self.patch.context() as patch, redirect_stdout(io.StringIO()), run_scope(offline=True, measuring=True):
            patch.setattr(sys, "argv", ["pipeline.cli", *map(str, arguments)])
            try:
                runpy.run_module("pipeline.cli", run_name="__main__")
            except SystemExit as exc:
                assert exc.code in (None, 0), f"Pipeline CLI exited with {exc.code}"

    def prepare_kera(self):
        self.cli("kera", "--as-of", "2025-01-08")
        self.cli("kera", "--as-of", "2024-12-12")

    def session(self, session_id):
        import ledger
        value = record(ledger.get_session(session_id))
        value["turns"] = [record(turn) for turn in value["turns"]]
        value["atoms"] = [record(atom) for atom in value["atoms"]]
        return value

    def verdicts(self, session_id):
        import ledger
        return [record(row) for row in ledger.effective_verdicts(session_id)]

    def atoms(self, session_id, turn_id):
        return [atom for atom in self.session(session_id)["atoms"] if atom["turn_id"] == turn_id]

    def turn(self, session_id, turn_id):
        return next(turn for turn in self.session(session_id)["turns"] if turn["turn_id"] == turn_id)

    def turn_rows(self, session_id, turn_id):
        atom_ids = {atom["atom_id"] for atom in self.atoms(session_id, turn_id)}
        return [row for row in self.verdicts(session_id) if row["atom_key"].split(":", 1)[1] in atom_ids]

    def colour(self, session_id, turn_id):
        aggregate = require("pipeline.aggregate")
        rows = self.turn_rows(session_id, turn_id)
        assert rows, f"{turn_id}: observed no verdicts"
        from core.models import Verdict
        return aggregate.turn_verdict([Verdict.model_validate(row) for row in rows])

    def events(self):
        import ledger
        events = []
        cursor = 0
        while True:
            page = [record(event) for event in ledger.events_since(cursor)]
            if not page:
                return events
            assert page[-1]["id"] > cursor, "Event pagination did not advance"
            events.extend(page)
            cursor = page[-1]["id"]

    def search(self, query, session_id):
        import ledger
        start = perf_counter()
        result = ledger.search(query, session_id=session_id)
        ms = (perf_counter() - start) * 1000
        self.reporter.soft.append({"search_ms": f"{ms:.3f} ms", "budget": "15.000 ms", "within_budget": ms <= 15})
        return [record(row) for row in result]

    def prepare_pr1(self, fixture):
        inputs = json.loads(fixture.read_text(encoding="utf-8"))
        text_file = self.root / ".local" / "pr1-input.txt"
        text_file.parent.mkdir(exist_ok=True)
        text_file.write_text("\n".join(inputs["sentences"]) + "\n", encoding="utf-8")
        self.cli("sentences", "--sku", inputs["sku_id"], "--as-of", inputs["as_of"],
                 "--file", text_file, "--source", inputs["source"])
        return inputs

    async def request(self, path, *, public=False):
        from api.app import create_app
        import httpx
        app = create_app(public=public)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo.invalid") as client:
            response = await client.get(path)
            assert response.status_code == 200, f"{path}: observed HTTP {response.status_code}"
            return response.json()

    def protected_state(self):
        from core.config import settings
        roots = {"eval": Path(settings.EVAL_DIR), "rules": self.root / "rules"}
        state = {}
        for label, root in roots.items():
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file() and (label == "eval" or path.name.endswith(".meta.json")):
                        state[f"{label}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
        return state
