"""Measure candidate effects offline, then activate human-approved rules."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import RLock

from core import paths
from core.config import settings
from core.enums import RuleStatus
from core.models import ClusterFile, DryRun, RegressionItem, RuleMeta

_LOCK = RLock()


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          encoding="utf-8").stdout


def _files(rule_id):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", rule_id):
        raise ValueError("Invalid rule id")
    return [f"rules/{rule_id}.py", f"rules/{rule_id}.meta.json", f"tests/test_{rule_id}.py"]


def _records(rule_id):
    import ledger
    jobs = sorted(ledger.list_jobs(rule_id=rule_id), key=lambda job: (job.ts, job.job_id))
    clusters = [ClusterFile.model_validate_json(file.read_text(encoding="utf-8"))
                for file in sorted(paths.clusters_dir().glob("*.json"))]
    passing = [job for job in jobs if job.status == "passed" and job.attempts
               and job.attempts[-1].gate_result == "pass"]
    runs = sorted((run for cluster in clusters for run in cluster.runs
                   if run.rule_id == rule_id and run.result == "passed"), key=lambda run: run.started)
    return jobs, passing, clusters, runs


@contextmanager
def _source(rule_id, jobs, passing, runs):
    files = _files(rule_id)
    if (paths.REPO_ROOT / files[0]).is_file():
        yield paths.REPO_ROOT, None
        return
    records = sorted([(job.ts, job.job_id, job.branch) for job in passing] +
                     [(run.started, run.job_id, run.branch) for run in runs])
    if records:
        _, job_id, branch = records[0]
        try:
            revision = _git(paths.REPO_ROOT, "rev-parse", "--verify", "--end-of-options", branch + "^{commit}").strip()
            for file in files:
                _git(paths.REPO_ROOT, "cat-file", "-e", f"{revision}:{file}")
        except subprocess.CalledProcessError:
            revision = None
        if revision:
            with TemporaryDirectory(prefix="rule source ") as folder:
                root = Path(folder)
                _git(paths.REPO_ROOT, "worktree", "add", "--detach", "--no-checkout", str(root), revision)
                try:
                    _git(root, "checkout", revision, "--", *files)
                    yield root, revision
                finally:
                    _git(paths.REPO_ROOT, "worktree", "remove", "--force", str(root))
            return
        worktrees = [job_id]
    else:
        worktrees = [job.job_id for job in jobs]
    for job_id in worktrees:
        root = paths.worktree_root() / job_id
        if (root / files[0]).is_file():
            yield root, None
            return
    raise ValueError(f"No candidate source is available for {rule_id}")


def _meta(root, rule_id):
    files = _files(rule_id)
    if not all((root / file).is_file() for file in files):
        raise ValueError("Candidate requires its module, metadata and test file")
    meta = RuleMeta.model_validate_json((root / files[1]).read_text(encoding="utf-8"))
    if meta.id != rule_id:
        raise ValueError("Candidate metadata id mismatch")
    return meta


def _inactive(row, rule_id):
    if rule_id in row.active or rule_id in row.quarantined:
        raise ValueError("Candidate is already in the live pack")


def dry_run(rule_id):
    import ledger
    from gate.score import case_as_of
    from pipeline.runner import replay
    from pipeline.llm import run_scope
    from rules.registry import load_pack

    _files(rule_id)
    if not paths.ledger_db().is_file():
        raise ValueError("LEDGER_DB must name an existing ledger file")
    with _LOCK:
        row = ledger.current_pack()
        _inactive(row, rule_id)
        jobs, passing, clusters, runs = _records(rule_id)
        with _source(rule_id, jobs, passing, runs) as (root, _):
            meta = _meta(root, rule_id)
            before = load_pack(row.active, quarantined=row.quarantined, mode="live")
            after = load_pack([*row.active, rule_id], quarantined=row.quarantined,
                              mode="measure", candidate_dir=None if root == paths.REPO_ROOT else root)
            sessions = ledger.list_sessions()
            golden = [json.loads(line) for line in paths.GOLDEN_CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
            golden = [case | {"as_of": case_as_of(case), "source_date": case_as_of(case)} for case in golden]
            source_ids = set(meta.source_case_ids)
            source_ids.update(item for job in jobs for item in job.cluster_item_ids)
            source_ids.update(item for cluster in clusters if any(run.rule_id == rule_id for run in cluster.runs)
                              for item in cluster.item_ids)
            memory = paths.regressions_file()
            sources = [RegressionItem.model_validate_json(line) for line in memory.read_text(encoding="utf-8").splitlines()
                       if line.strip()] if memory.exists() else []
            source_skus = {case["sku_id"] for case in golden if case["case_id"] in source_ids}
            source_skus.update(item.sku_id for item in sources if item.item_id in source_ids)
            targets = [session.session_id for session in sessions] + golden
            descriptors = [(session.session_id, session.sku_pin, False) for session in sessions]
            descriptors += [(case["case_id"], case["sku_id"], True) for case in golden]
            with run_scope(offline=True, measuring=True):
                old = list(replay(targets, pack=before, persist=False, purpose="live", pack_version=row.version))
                new = list(replay(targets, pack=after, persist=False, purpose="live", pack_version=row.version))
            if len(old) != len(targets) or len(new) != len(targets):
                raise ValueError("Replay result count does not match targets")
            if any(result.status != "ok" or result.negq_miss for result in [*old, *new]):
                raise ValueError("cache miss: run python -m pipeline.cli shadow")
            changed_cases = changed_atoms = outside = 0
            changes = []
            other_sku = False
            ignored = {"verdict_id", "rule_pack_version", "llm_call_ids", "supersedes", "ts"}
            for (item_id, sku, is_golden), left, right in zip(descriptors, old, new, strict=True):
                previous = {verdict.atom_key: verdict.model_dump(mode="json", exclude=ignored) for verdict in left.verdicts}
                current = {verdict.atom_key: verdict.model_dump(mode="json", exclude=ignored) for verdict in right.verdicts}
                keys = sorted(key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key))
                if not keys and left.case_verdict == right.case_verdict:
                    continue
                changed_cases += 1
                changed_atoms += len(keys)
                outside += is_golden and item_id not in source_ids
                other_sku |= sku not in source_skus
                changes.append(dict(item_id=item_id, sku_id=sku, before=left.case_verdict,
                                    after=right.case_verdict, changed_atom_keys=keys))
            return DryRun(rule_id=rule_id, changed_cases=changed_cases, changed_atoms=changed_atoms,
                          changed_golden_outside_sources=outside, flag_many=changed_cases > 5 or other_sku,
                          observed_changes=changes)


def _check_staged(files):
    staged = _git(paths.REPO_ROOT, "diff", "--cached", "--no-renames", "--name-only", "-z").split("\0")
    staged = {Path(PureWindowsPath(name).as_posix()).as_posix() for name in staged if name}
    if staged - set(files):
        raise ValueError("Unrelated files are staged; refusing merge autocommit")


def merge(rule_id, approved_by):
    import ledger
    from rules.registry import load_pack
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("approved_by is required")
    files = _files(rule_id)
    if not paths.ledger_db().is_file():
        raise ValueError("LEDGER_DB must name an existing ledger file")
    with _LOCK:
        row = ledger.current_pack()
        _inactive(row, rule_id)
        jobs, passing, clusters, runs = _records(rule_id)
        with _source(rule_id, jobs, passing, runs) as (root, revision):
            meta = _meta(root, rule_id)
            if not passing and not (meta.attempts and meta.attempts[-1].gate_result == "pass") and not runs:
                raise ValueError("A passing gate record is required")
            # Resolve the collaborator before approving files or activating a pack.
            from pipeline.dossier_events import backfill
            if settings.REGRESSION_AUTOCOMMIT:
                _check_staged(files)
            originals = {file: (paths.REPO_ROOT / file).read_bytes() if (paths.REPO_ROOT / file).exists() else None for file in files}
            index = _git(paths.REPO_ROOT, "write-tree").strip()
            try:
                if root != paths.REPO_ROOT:
                    if revision:
                        _git(paths.REPO_ROOT, "checkout", revision, "--", *files)
                    else:
                        for file in files:
                            target = paths.REPO_ROOT / file
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(root / file, target)
                meta.approved_by = approved_by
                meta.approved_at = datetime.now(timezone.utc).isoformat()
                meta.status = RuleStatus.ACTIVE
                (paths.REPO_ROOT / files[1]).write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
                with ledger.transaction():
                    if ledger.current_pack().version != row.version:
                        raise ValueError("Rule pack changed; repeat the merge with the current pack")
                    pack = ledger.create_pack_version([*row.active, rule_id], row.quarantined,
                        "merge", rule_id=rule_id, approved_by=approved_by)
                    backfill(load_pack(pack.active, quarantined=pack.quarantined, mode="live"))
                    ledger.append_event("rule.merged", dict(rule_id=rule_id, version=pack.version, approved_by=approved_by))
                    if settings.REGRESSION_AUTOCOMMIT:
                        _git(paths.REPO_ROOT, "add", "--", *files)
                        _check_staged(files)
                        _git(paths.REPO_ROOT, "commit", "-m", f"merge rule {rule_id} (approved_by {approved_by})")
                return pack
            except BaseException:
                for file, content in originals.items():
                    target = paths.REPO_ROOT / file
                    if content is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(content)
                _git(paths.REPO_ROOT, "reset", "-q", index, "--", *files)
                raise
