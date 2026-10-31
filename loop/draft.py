"""Isolated rule drafting, bounded retries and cluster-based provenance."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from core import paths
from core.config import command_args, config_fields, settings
from core.models import ClusterFile, ClusterRun, GateResult, RuleAttempt, RuleMeta


ALLOWED_COMMANDS = """Every command log is checked; a command outside this block fails the attempt. <rule_id> is the rule_id in loop_input/task.json.
Use one single-line command per shell call. One shell wrapper is allowed: powershell -Command, pwsh -Command, cmd /c, bash -lc or -c, sh -c or -lc, zsh -lc or -c (including /bin/zsh). Prefer separate calls; each command joined with ;, && or | is checked separately.
Never use $(, backticks, ${, embedded newlines, heredocs, redirection (<, >, >>), environment prefixes, $env:, env, set, export, cd, package installers, the gate, Python except the own-test command, or git except status/diff. Tests are already offline; never set or inspect environment variables.
Read only literal relative paths: no wildcards, parent traversal or absolute paths. Read commands: cat, type, head, tail, sed -n, rg, grep, ls, pwd. In Windows use only Get-Content for reads, not Select-String or Get-ChildItem -Recurse.
Get-Content accepts one path, positional or after -Path/-LiteralPath, with optional -Raw, -Encoding UTF8 (or utf8), and -TotalCount <n> in any order. No other parameters.
Readable files: AGENTS.md, pytest.ini, requirements.txt, rules/collisions.txt, rules/law_basis.json, rules/<name>.py, your own rules/<rule_id>.meta.json and tests/test_<rule_id>.py; files under loop_input/, core/, textnorm/, data/law/ and .agents/skills/draft-rule/.
Name files directly. Never list/search the worktree root, rules/ or tests/ as directories: they can contain forbidden files. This worktree has no docs directory; do not read contracts there.
Tests: python -m pytest -q tests/test_<rule_id>.py or pytest -q tests/test_<rule_id>.py, without any prefix. Python3 or the existing venv Python is accepted; optional -x, -s, -v and --tb=short are accepted. No other targets or options.
Git: git status with --short, -sb, --porcelain or -uall; git diff with --stat, --name-only, --cached, -- and allowed literal paths only.
Create/edit only rules/<rule_id>.py, rules/<rule_id>.meta.json and tests/test_<rule_id>.py. Use the file-editing tool, never shell commands or Python write scripts.
Allowed imports: re, unicodedata, typing, dataclasses, functools, itertools, textnorm, rules._common
Rejected names (including attributes): open, eval, exec, __import__, input, globals, locals, vars, getattr, setattr, delattr, compile, breakpoint, time, datetime, random, os, sys, socket, subprocess, pathlib, io, requests, urllib, http, builtins, read_text, read_bytes, write_text, write_bytes
Names starting with __ are also rejected. Never use re.compile; use re.search, re.finditer or re.fullmatch with pattern strings.
PASS: powershell -Command 'Get-Content -Raw -Encoding UTF8 .agents/skills/draft-rule/SKILL.md; Get-Content -Raw -Encoding UTF8 loop_input/task.json'
PASS: Get-Content -LiteralPath rules/R04.py -TotalCount 80
PASS: cat rules/_common.py
PASS: sed -n 1,80p loop_input/sources.json
PASS: rg -n "def " rules/_common.py
PASS: python -m pytest -q tests/test_<rule_id>.py
PASS: git status --short
FAIL: OFFLINE=1 pytest -q tests/test_<rule_id>.py
FAIL: env
FAIL: cat docs/contracts.md
FAIL: ls rules
FAIL: cat rules/R04.meta.json
FAIL: cat <<EOF"""

FOLLOW = "Follow .agents/skills/draft-rule/SKILL.md for loop_input/task.json."
CACHE_MISS = "cache miss: run python -m pipeline.cli shadow"
SPARSE_PATHS = ("/AGENTS.md", "/pytest.ini", "/requirements.txt", "/core/", "/textnorm/",
                "/rules/*.py", "/rules/collisions.txt", "/rules/law_basis.json",
                "/data/law/", "/.agents/skills/draft-rule/")
PINNED_VERSION = "0.164.0-alpha.11.1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("Identifier must contain only letters, digits, underscores and hyphens")
    return value


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _cluster_lock(path):
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Cluster is locked by another writer") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink()


def _save_cluster(path, cluster):
    relative = None
    if settings.REGRESSION_AUTOCOMMIT:
        relative = path.resolve().relative_to(paths.REPO_ROOT.resolve()).as_posix()
        previous = _git(paths.REPO_ROOT, "log", "-1", "--format=%H", "--", relative)
        if previous:
            cluster.commit = previous
    _write(path, cluster.model_dump(mode="json"))
    if relative is not None:
        _git(paths.REPO_ROOT, "add", "--", relative)
        _git(paths.REPO_ROOT, "commit", "--only", "-m", f"Record cluster {cluster.name}", "--", relative)
        return _git(paths.REPO_ROOT, "rev-parse", "HEAD")
    return cluster.commit


def _find_cluster(cluster_hash):
    matches = [p for p in sorted(paths.clusters_dir().glob("*.json"))
               if _read(p)["cluster_hash"] == cluster_hash]
    if len(matches) != 1:
        raise ValueError("Expected exactly one cluster file for the cluster hash")
    return matches[0]


def _source_items(cluster):
    wanted = set(cluster.item_ids)
    items = {}
    for line in paths.regressions_file().read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item["item_id"] in wanted:
                if item["item_id"] in items:
                    raise ValueError("Duplicate correction-memory item id")
                items[item["item_id"]] = item
    if items.keys() != wanted:
        raise ValueError("Cluster references missing correction-memory items")
    result = [items[key] for key in cluster.item_ids]
    if [r["text"] for r in result] != cluster.texts or [r["label"] for r in result] != cluster.labels:
        raise ValueError("Cluster no longer matches its frozen source items")
    return result


def _family(items):
    from rules.registry import all_rules

    families = {row["family"] for row in _read(paths.REPO_ROOT / "rules/law_basis.json")["rows"]}
    mapping = {rule.rule_id: rule.meta.family for rule in all_rules()}
    common = set.intersection(*({mapping.get(rule, rule) for rule in item["rule_ids"]} & families for item in items))
    if len(common) != 1:
        raise ValueError("Cluster must identify exactly one common legal-basis family")
    return common.pop()


def _prepare_inputs(worktree, rule_id, cluster):
    from core.enums import AxisGroup
    from core.models import AtomView
    from pipeline.context import build_context
    from pipeline.guardrail import is_effective
    from rules._common import DISCLAIMER
    from rules.nearhit import detect
    from rules.registry import RuntimePack
    from textnorm import match_key

    items = _source_items(cluster)
    family = _family(items)
    empty = RuntimePack((), (), "measure")
    contexts = [build_context(item["sku_id"], item["as_of"], pack=empty) for item in items]
    rows = _read(paths.REPO_ROOT / "rules/law_basis.json")["rows"]
    candidates = [row for row in rows if row["family"] == family and row["role"] == "primary" and all(
        row["category"] in ("any", str(ctx.category_norm))
        and (row["valid_from"] is None or row["valid_from"] <= item["as_of"])
        and (row["valid_to"] is None or item["as_of"] <= row["valid_to"])
        and is_effective(row["anchor"], date.fromisoformat(item["as_of"]))
        for item, ctx in zip(items, contexts, strict=True))]
    candidates.sort(key=lambda row: row["role"] != "primary")
    if not candidates:
        raise ValueError("No common effective law anchor for the source dates")
    anchor = candidates[0]["anchor"]
    index = {row["id"]: row for row in _read(paths.LAW_DIR / "index.json")}
    article = (paths.LAW_DIR / index[anchor]["file"]).read_text(encoding="utf-8").split("---", 2)[-1].strip()
    folder = worktree / "loop_input"
    folder.mkdir()
    _write(folder / "task.json", dict(rule_id=rule_id, family=family, anchor=anchor))
    sources = []
    for item, ctx in zip(items, contexts, strict=True):
        view = AtomView(text=item["text"], key=match_key(item["text"])[0], axis="cong_dung",
            speaker_role="khong_ro", category_norm=ctx.category_norm, as_of=ctx.as_of,
            turn_text=item["text"])
        near_hits = [dict(pattern=h.pattern, gap=h.gap, law_span=h.law_span) for h in detect(view)]
        sources.append(dict(item_id=item["item_id"], text=item["text"], sku_id=item["sku_id"],
                            label=item["label"], family=family, near_hits=near_hits))
    _write(folder / "sources.json", sources)
    permitted = {}
    for sku in sorted({p.stem for p in paths.EVIDENCE_DIR.glob("*.json")} | {"kera"}):
        ctx = build_context(sku, "2025-01-08" if sku == "kera" else "2026-10-21", pack=empty)
        permitted[sku] = ctx.prompt_view(list(AxisGroup))
    _write(folder / "permitted_items.json", permitted)
    (folder / "article.md").write_text(article + "\n", encoding="utf-8")
    (folder / "disclaimer.txt").write_text(DISCLAIMER + "\n", encoding="utf-8")
    (folder / "collisions.txt").write_text((paths.REPO_ROOT / "rules/collisions.txt").read_text(encoding="utf-8"), encoding="utf-8")
    _write(folder / "schema.json", {"type": "object", "properties": {
        "rule_id": {"type": "string", "enum": [rule_id]}, "summary": {"type": "string"}},
        "required": ["rule_id", "summary"], "additionalProperties": False})
    return family


def _worktree(job):
    root = paths.worktree_root()
    root.mkdir(parents=True, exist_ok=True)
    worktree = root / _slug(job.job_id)
    _git(paths.REPO_ROOT, "worktree", "add", "--no-checkout", "-b", job.branch, str(worktree), "HEAD")
    _git(worktree, "sparse-checkout", "set", "--no-cone", *SPARSE_PATHS)
    _git(worktree, "checkout", "HEAD")
    return worktree


@lru_cache(maxsize=None)
def _codex_info(command):
    version = subprocess.run([*command, "--version"], check=True, capture_output=True,
                             text=True, encoding="utf-8", timeout=15).stdout.strip()
    if version != f"codex-cli {PINNED_VERSION}":
        raise ValueError("Codex version does not match the pinned CLI")
    help_text = subprocess.run([*command, "exec", "--help"], check=True, capture_output=True,
                               text=True, encoding="utf-8", timeout=15).stdout
    if any(flag not in help_text for flag in ("--sandbox", "--json", "--output-schema", "-o", "-m", "-c")):
        raise ValueError("Codex exec does not support the required flags")
    return version


def _execute(job, worktree, number, log_dir):
    from ledger import update_job

    command = command_args(settings.CODEX_CMD)
    version = _codex_info(tuple(command))
    (log_dir / "version.txt").write_text(version + "\n", encoding="utf-8")
    folder = worktree / "loop_input"
    output = folder / "out.json"
    output.unlink(missing_ok=True)
    args = [*command, "exec", "--sandbox", "workspace-write", "--json", "--output-schema",
            str(folder / "schema.json"), "-o", str(output), "-m", settings.DRAFT_MODEL,
            "-c", f"model_reasoning_effort={settings.DRAFT_EFFORT}", "-"]
    prompt = ALLOWED_COMMANDS + "\n\n" + FOLLOW
    if number > 1:
        prompt += " Fix the exact gate errors in loop_input/retry.md."
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    pidfile = paths.jobs_dir(create=True) / f"{job.job_id}.pid"
    log = log_dir / f"attempt_{number}.jsonl"
    err = log.with_suffix(".stderr")
    with log.open("w", encoding="utf-8", newline="\n") as stdout, err.open("w", encoding="utf-8", newline="\n") as stderr:
        process = subprocess.Popen(args, cwd=worktree, stdin=subprocess.PIPE, stdout=stdout,
                                   stderr=stderr, text=True, encoding="utf-8", env=env)
        try:
            update_job(job.job_id, pid=process.pid)
            pidfile.write_text(str(process.pid), encoding="utf-8")
            process.communicate(prompt)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            pidfile.unlink(missing_ok=True)
            update_job(job.job_id, pid=None)
    if process.returncode and "model" in err.read_text(encoding="utf-8").lower():
        raise RuntimeError("Codex model error; see the attempt stderr")
    if process.returncode == 0:
        result = _read(output)
        if set(result) != {"rule_id", "summary"} or result["rule_id"] != job.rule_id or not isinstance(result["summary"], str):
            raise ValueError("Codex structured output does not match the job")
    return process.returncode, log


def _annotate_meta(worktree, job, attempts, revealed, family):
    path = worktree / "rules" / f"{job.rule_id}.meta.json"
    if not path.exists():
        return
    meta = RuleMeta.model_validate(_read(path))
    if (meta.id != job.rule_id or meta.family != family or meta.origin != "t5"
            or meta.status != "proposed" or meta.approved_by is not None or meta.approved_at is not None):
        raise ValueError("Draft metadata must remain proposed and unapproved for the job family")
    meta.attempts = [RuleAttempt(ts=a["finished"], gate_result=a["gate_result"],
                          error_lines=a["error_lines"], files_read=a["files_read"]) for a in attempts]
    meta.revealed_case_ids = sorted(revealed)
    _write(path, meta.model_dump(mode="json"))


def _publish(job, worktree, cluster_path, started, attempts, result):
    from pipeline.guardrail import display_text

    files = [f"rules/{job.rule_id}.py", f"rules/{job.rule_id}.meta.json", f"tests/test_{job.rule_id}.py"]
    _git(worktree, "add", "--sparse", "--", *files)
    _git(worktree, "commit", "-m", f"{job.rule_id}: propose a source-grounded rule", "--", *files)
    if not settings.LOOP_OPEN_PR:
        return None
    # Keep the per-cluster lock through the PR operation so concurrent passes cannot duplicate it.
    with _cluster_lock(cluster_path):
        cluster = ClusterFile.model_validate(_read(cluster_path))
        if any(run.result == "passed" for run in cluster.runs):
            return None
        meta = RuleMeta.model_validate(_read(worktree / files[1]))
        body = dict(source_items=cluster.item_ids,
                    law_quote=[display_text(anchor) for anchor in meta.anchors],
                    attempts=[{k: a[k] for k in ("attempt_no", "gate_result", "error_lines", "files_read")} for a in attempts],
                    observed_changes=result.observed_changes, approved_by="pending human review")
        body_path = worktree / "loop_input/pr-body.txt"
        body_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _git(worktree, "push", "-u", "origin", job.branch)
        url = subprocess.run(["gh", "pr", "create", "--head", job.branch,
            "--title", f"{job.rule_id}: proposed rule", "--body-file", str(body_path)],
            cwd=worktree, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        cluster.runs.append(_run_summary(job, started, attempts, "passed", url))
        _save_cluster(cluster_path, cluster)
        return url


def _run_summary(job, started, attempts, status, pr_url):
    return ClusterRun(job_id=job.job_id, rule_id=job.rule_id, branch=job.branch,
        started=started, attempts=[{k: a[k] for k in ("attempt_no", "gate_result", "error_lines")} for a in attempts],
        result=status, pr_url=pr_url)


def _record_run(path, job, started, attempts, status, pr_url):
    with _cluster_lock(path):
        cluster = ClusterFile.model_validate(_read(path))
        cluster.runs = [run for run in cluster.runs if run.job_id != job.job_id]
        cluster.runs.append(_run_summary(job, started, attempts, status, pr_url))
        _save_cluster(path, cluster)


def run_job(job_id):
    from ledger import append_event, get_job, update_job

    _slug(job_id)
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job.status in ("passed", "failed", "error"):
        return job
    if job.status != "queued":
        raise ValueError("Only a queued job may start")
    _slug(job.rule_id)
    started = _now()
    branch = f"rule/{job.rule_id}-{job.job_id}"
    job = update_job(job_id, status="running", branch=branch, dry=bool(settings.LOOP_DRY),
                     config=config_fields()["cfg"], pid=None)
    append_event("job.started", dict(job_id=job_id, rule_id=job.rule_id, cluster_key=job.cluster_key))
    attempts, revealed = [], set()
    cluster_path = worktree = None
    status, pr_url, family = "error", None, None
    log_dir = paths.logs_dir(create=True) / job_id
    log_dir.mkdir(exist_ok=True)
    for number in range(1, 4):
        attempt_started = _now()
        terminal_error = False
        files_read, log = [], None
        try:
            if settings.PUBLIC_MODE:
                raise ValueError("Public mode cannot start a draft-rule job")
            if number == 1:
                cluster_path = _find_cluster(job.cluster_hash)
                cluster = ClusterFile.model_validate(_read(cluster_path))
                if job.cluster_item_ids != cluster.item_ids:
                    raise ValueError("Job items do not match its frozen cluster")
                if not job.dry:
                    worktree = _worktree(job)
                    family = _prepare_inputs(worktree, job.rule_id, cluster)
            if job.dry:
                fixtures = _read(paths.REPO_ROOT / "tests/loop_fixtures/dry_attempts.json")
                raw = json.dumps(fixtures[number - 1]).replace("<rule_id>", job.rule_id)
                result = GateResult.model_validate_json(raw)
                log = log_dir / f"attempt_{number}.jsonl"
                log.write_text(json.dumps(dict(type="item.completed", item=dict(
                    type="agent_message", text="Simulated draft attempt"))) + "\n", encoding="utf-8")
            else:
                from gate import run_gate
                from gate.logscan import scan_log

                exit_code, log = _execute(job, worktree, number, log_dir)
                result = run_gate(job.rule_id, candidate_dir=worktree, log=log, sources=cluster_path)
                files_read, _ = scan_log(log, worktree, job.rule_id)
                if result.error_lines and set(result.error_lines) == {CACHE_MISS}:
                    terminal_error = True
                elif exit_code and result.passed:
                    result = GateResult(passed=False, error_lines=[f"Codex exited with status {exit_code}"])
        except Exception as exc:
            terminal_error = True
            result = GateResult(passed=False, error_lines=[str(exc)])
        attempt = dict(attempt_no=number, started=attempt_started, finished=_now(),
                       gate_result="pass" if result.passed else "fail", error_lines=result.error_lines,
                       files_read=files_read, log_path=str(log) if log is not None else None)
        attempts.append(attempt)
        revealed.update(result.revealed_case_ids)
        if worktree is not None:
            try:
                _annotate_meta(worktree, job, attempts, revealed, family)
            except Exception as exc:
                terminal_error = True
                attempt["gate_result"] = "fail"
                attempt["error_lines"].append(str(exc))
        if terminal_error:
            status = "error"
        elif result.passed:
            status = "passed"
            if not job.dry:
                try:
                    pr_url = _publish(job, worktree, cluster_path, started, attempts, result)
                except Exception as exc:
                    status = "error"
                    attempts[-1]["error_lines"].append(str(exc))
        else:
            status = "failed"
            if number < 3 and worktree is not None:
                try:
                    (worktree / "loop_input/retry.md").write_text(
                        ALLOWED_COMMANDS + "\n\nGate errors from the previous attempt:\n"
                        + "\n".join(result.error_lines) + "\n", encoding="utf-8")
                except Exception as exc:
                    status = "error"
                    attempt["error_lines"].append(str(exc))
        update_job(job_id, attempts=attempts)
        append_event("job.attempt", dict(job_id=job_id, attempt_no=number,
                     gate_result=attempt["gate_result"], error_lines=attempt["error_lines"], files_read=files_read))
        if status != "failed":
            break
    if cluster_path is not None:
        try:
            _record_run(cluster_path, job, started, attempts, status, pr_url)
        except Exception as exc:
            status = "error"
            attempts[-1]["error_lines"].append(str(exc))
            # Record the error run even when the optional local git commit failed.
            with _cluster_lock(cluster_path):
                cluster = ClusterFile.model_validate(_read(cluster_path))
                cluster.runs = [run for run in cluster.runs if run.job_id != job.job_id]
                cluster.runs.append(_run_summary(job, started, attempts, status, pr_url))
                _write(cluster_path, cluster.model_dump(mode="json"))
    job = update_job(job_id, status=status, attempts=attempts, pid=None, pr_url=pr_url)
    append_event("job.finished", dict(job_id=job_id, status=status, pr_url=pr_url))
    return job


def funnel(cluster_hash):
    cluster = ClusterFile.model_validate(_read(_find_cluster(cluster_hash)))
    runs = [run for run in cluster.runs if run.result != "error"]
    first = sum(run.result == "passed" and len(run.attempts) == 1 for run in runs)
    retried = sum(run.result == "passed" and len(run.attempts) > 1 for run in runs)
    return dict(K=len(runs), I=first, J=retried, R=sum(run.result == "failed" for run in runs))
