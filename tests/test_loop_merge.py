"""Synthetic activation, offline replay and stage-ledger restoration."""

import builtins
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override, settings
from core.models import Atom, ClusterFile, ClusterRun, Context, Job, JobAttempt, RuleAttempt, RuleMeta, Session, Turn
import ledger
from loop import merge as m
from pipeline import aggregate, context, extract, guardrail, llm, runner
from rules import registry

TS = "2026-10-21T00:00:00Z"
RULE = "R04x"
FILES = [f"rules/{RULE}.py", f"rules/{RULE}.meta.json", f"tests/test_{RULE}.py"]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8").stdout.strip()


def candidate(root, *, attempts=("pass",)):
    (root / "rules").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    meta = RuleMeta(id=RULE, family="R04", origin="t5", article_id="LDUOC-2016/D6",
        anchors=["LDUOC-2016/D6/K15"], law_span="chữa bệnh", source_case_ids=["source"],
        trigger="Synthetic treatment phrase", precedence=14, status="proposed",
        attempts=[RuleAttempt(ts=TS, gate_result=outcome) for outcome in attempts])
    (root / FILES[1]).write_text(meta.model_dump_json(), encoding="utf-8")
    (root / FILES[0]).write_text('''"""Synthetic candidate for offline tests."""
from rules._common import make_hit
RULE_ID = "R04x"
FAMILY = "R04"
ORIGIN = "t5"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "chữa bệnh"
PRECEDENCE = 14
def check(view):
    return [make_hit(RULE_ID, FAMILY, "synthetic treatment", ANCHORS[0], LAW_SPAN, PRECEDENCE)] if "synthetic treatment" in view.text else []
''', encoding="utf-8")
    (root / FILES[2]).write_text("def test_candidate():\n    assert True\n", encoding="utf-8")
    return meta


def job(world, *, status="passed", gate="pass", branch="rule/source", job_id="source-job"):
    value = Job(job_id=job_id, rule_id=RULE, branch=branch, cluster_key="source", cluster_item_ids=["source"],
                cluster_hash="source", status=status, config="synthetic", ts=TS,
                attempts=[JobAttempt(attempt_no=1, started=TS, gate_result=gate)])
    ledger.create_job(value)
    return value


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "A. sample checkout"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("Synthetic\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture base")
    remote = tmp_path / "fake remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(guardrail, "LAW_BASIS_FILE", Path(__file__).resolve().parents[1] / "rules/law_basis.json")
    monkeypatch.setattr(registry, "__file__", str(root / "rules/registry.py"))
    set_override("EVAL_DIR", str(tmp_path / "memory"))
    set_override("WORKTREE_ROOT", str(tmp_path / "job worktrees"))
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps(dict(case_id="source", sku_id="synthetic", text="synthetic treatment",
                                     label="red", source_date="2026-10-21")) + "\n", encoding="utf-8")
    monkeypatch.setattr(paths, "GOLDEN_CASES", golden)
    ledger.init_db(settings.LEDGER_DB)
    monkeypatch.setattr(context, "build_context", lambda sku, as_of, pack=None: Context(
        sku_id=sku, category_norm="TPBVSK", as_of=as_of, dossier_state="PRESENT", dossier_hash="synthetic"))
    def extract_atoms(session, turn, previous, ctx, purpose="live", cache_read=True):
        assert cache_read
        return [Atom(atom_id=f"{turn.turn_id}.a1", turn_id=turn.turn_id, span=turn.text, start=0, end=len(turn.text),
                     axis="cong_dung", speaker_role="khong_ro", subject_is_product=True)]
    monkeypatch.setattr(extract, "extract_atoms", extract_atoms)
    monkeypatch.setattr(llm, "_backend", lambda *args: pytest.fail("Unexpected model transport"))
    backfills = []
    def backfill(pack):
        backfills.append(pack)
        runtime = registry.load_pack(pack.active, quarantined=pack.quarantined, mode="live")
        results = runner.replay([session.session_id for session in ledger.list_sessions()], pack=runtime,
                                pack_version=pack.version, persist=True, purpose="live")
        ledger.append_event("backfill.done", dict(version=pack.version, changed_cases=results.changed_cases,
                                                  changed_atoms=results.changed_atoms))
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", SimpleNamespace(backfill=backfill))
    return SimpleNamespace(root=root, remote=remote, backfills=backfills)


def seed(session_id="synthetic", sku="synthetic", text="synthetic treatment"):
    session = Session(session_id=session_id, sku_pin=sku, as_of="2026-10-21", channel="written", source="Synthetic",
        turns=[Turn(turn_id="t01", speaker_label="[KOL 1]", attribution="constructed", text=text)])
    ledger.add_session(session)
    runner.process_session(session_id, "live")
    return ledger.get_session(session_id)


@pytest.mark.parametrize("approved_by", [None, "", " "])
def test_human_approval_is_required(world, approved_by):
    candidate(world.root)
    with pytest.raises(ValueError, match="approved_by"):
        m.merge(RULE, approved_by)
    assert ledger.current_pack().version == 1 and world.backfills == []


@pytest.mark.parametrize("status,gate", [(None, None), ("failed", "fail"), ("failed", "pass"), ("error", "pass"), ("passed", "fail")])
def test_no_passing_gate_is_refused(world, status, gate):
    candidate(world.root, attempts=())
    if status:
        job(world, status=status, gate=gate)
    with pytest.raises(ValueError, match="passing gate"):
        m.merge(RULE, "fixture")
    assert ledger.current_pack().version == 1
    assert RuleMeta.model_validate_json((world.root / FILES[1]).read_text()).approved_by is None


def test_fresh_ledger_dry_run_merge_and_snapshot_restore(world, tmp_path):
    candidate(world.root)
    ledger.init_db(tmp_path / "fresh stage.db")
    stored = seed()
    old_id = stored.verdicts[0].verdict_id
    old_who = aggregate.who_decided(stored.verdicts)
    assert old_who.human == 1 and ledger.list_jobs() == []
    snapshot = tmp_path / "snapshot.db"
    with sqlite3.connect(settings.LEDGER_DB) as source, sqlite3.connect(snapshot) as destination:
        source.backup(destination)
    before_events = ledger.events_since(0)
    preview = m.dry_run(RULE)
    assert (preview.changed_cases, preview.changed_atoms, preview.changed_golden_outside_sources) == (2, 2, 0)
    assert not preview.flag_many
    assert ledger.get_session("synthetic").verdicts[0].verdict_id == old_id
    assert ledger.events_since(0) == before_events and ledger.current_pack().version == 1
    pack = m.merge(RULE, "fixture")
    assert pack.version == 2 and pack.change == "merge" and pack.active == [RULE]
    assert world.backfills == [pack]
    current = ledger.get_session("synthetic").verdicts[0]
    assert current.supersedes == old_id and current.verdict == "red" and current.rule_pack_version == 2
    assert aggregate.who_decided([current]).code == 1
    meta = RuleMeta.model_validate_json((world.root / FILES[1]).read_text())
    assert meta.approved_by == "fixture" and meta.approved_at and meta.status == "active"
    assert [event.type for event in ledger.events_since(0)][-2:] == ["backfill.done", "rule.merged"]
    with sqlite3.connect(snapshot) as source, sqlite3.connect(settings.LEDGER_DB) as destination:
        source.backup(destination)
    assert (world.root / FILES[0]).exists()
    assert ledger.current_pack().version == 1 and RULE not in ledger.current_pack().active
    assert registry.load_pack(ledger.current_pack().active).active == ()
    assert m.dry_run(RULE).changed_cases == 2
    assert m.merge(RULE, "fixture").version == 2


@pytest.mark.parametrize("proof", ["job", "cluster"])
def test_gate_provenance_fallbacks(world, proof):
    candidate(world.root, attempts=("fail",))
    if proof == "job":
        job(world)
    else:
        run = ClusterRun(job_id="old", rule_id=RULE, branch="rule/old", started=TS, result="passed")
        cluster = ClusterFile(name="old", cluster_hash="old", created_at=TS, commit="", runs=[run])
        paths.clusters_dir().mkdir(parents=True)
        (paths.clusters_dir() / "old.json").write_text(cluster.model_dump_json(), encoding="utf-8")
    assert m.merge(RULE, "fixture").version == 2


def test_last_meta_attempt_is_used(world):
    candidate(world.root, attempts=("pass", "fail"))
    with pytest.raises(ValueError, match="passing gate"):
        m.merge(RULE, "fixture")


def test_branch_checkout_uses_temp_path_with_spaces_and_no_network(world, monkeypatch):
    branch = "rule/passing"
    base = git(world.root, "rev-parse", "HEAD")
    git(world.root, "switch", "-c", branch)
    candidate(world.root, attempts=())
    git(world.root, "add", ".")
    git(world.root, "commit", "-qm", "Fixture candidate")
    git(world.root, "switch", "--detach", base)
    job(world, branch=branch)
    seed()
    roots = []
    original = m._meta
    def capture(root, rule_id):
        roots.append(root)
        assert root != world.root and " " in str(root) and not root.is_relative_to(world.root)
        return original(root, rule_id)
    monkeypatch.setattr(m, "_meta", capture)
    before = git(world.root, "status", "--porcelain")
    assert m.dry_run(RULE).changed_cases == 2
    assert git(world.root, "status", "--porcelain") == before
    assert not any((world.root / file).exists() for file in FILES)
    assert m.merge(RULE, "fixture").version == 2
    assert all((world.root / file).exists() for file in FILES)
    assert roots and all(not root.exists() for root in roots)
    assert not list((world.remote / "refs/heads").iterdir())


@pytest.mark.parametrize("proof", ["job", "cluster"])
def test_job_worktree_fallback(world, proof):
    git(world.root, "branch", "rule/not-local")
    if proof == "job":
        value = job(world, branch="rule/not-local")
    else:
        value = ClusterRun(job_id="source-job", rule_id=RULE, branch="rule/not-local", started=TS, result="passed")
        cluster = ClusterFile(name="source", cluster_hash="source", created_at=TS, commit="", runs=[value])
        paths.clusters_dir().mkdir(parents=True)
        (paths.clusters_dir() / "source.json").write_text(cluster.model_dump_json(), encoding="utf-8")
    root = paths.worktree_root() / value.job_id
    candidate(root, attempts=())
    assert m.dry_run(RULE).changed_cases == 1
    assert m.merge(RULE, "fixture").version == 2


def test_autocommit_is_local_and_windows_staged_paths_work(world, monkeypatch):
    candidate(world.root)
    git(world.root, "add", ".")
    git(world.root, "commit", "-qm", "Fixture proposed files")
    set_override("REGRESSION_AUTOCOMMIT", 1)
    original = m._git
    def windows(root, *args):
        result = original(root, *args)
        return result.replace("/", "\\") if args[:2] == ("diff", "--cached") else result
    monkeypatch.setattr(m, "_git", windows)
    meta = world.root / FILES[1]
    meta.write_text(meta.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    git(world.root, "add", FILES[1])
    assert m.merge(RULE, "fixture").version == 2
    assert git(world.root, "log", "-1", "--format=%s") == f"merge rule {RULE} (approved_by fixture)"
    assert git(world.root, "status", "--porcelain") == ""
    assert not list((world.remote / "refs/heads").iterdir())


def test_autocommit_refuses_unrelated_windows_staged_path(world, monkeypatch):
    candidate(world.root)
    set_override("REGRESSION_AUTOCOMMIT", 1)
    before = (world.root / FILES[1]).read_bytes()
    original = m._git
    def staged(root, *args):
        return "docs\\unrelated.txt\0" if args[:2] == ("diff", "--cached") else original(root, *args)
    monkeypatch.setattr(m, "_git", staged)
    with pytest.raises(ValueError, match="Unrelated"):
        m.merge(RULE, "fixture")
    assert ledger.current_pack().version == 1 and (world.root / FILES[1]).read_bytes() == before


def test_autocommit_rechecks_staging_after_backfill(world, monkeypatch):
    candidate(world.root)
    set_override("REGRESSION_AUTOCOMMIT", 1)
    original = sys.modules["pipeline.dossier_events"].backfill
    def stage_unrelated(pack):
        original(pack)
        (world.root / "unrelated.txt").write_text("Synthetic\n", encoding="utf-8")
        git(world.root, "add", "unrelated.txt")
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", SimpleNamespace(backfill=stage_unrelated))
    with pytest.raises(ValueError, match="Unrelated"):
        m.merge(RULE, "fixture")
    assert ledger.current_pack().version == 1
    assert git(world.root, "diff", "--cached", "--name-only") == "unrelated.txt"


def test_missing_t7_does_not_approve_or_activate(world, monkeypatch):
    candidate(world.root)
    before = (world.root / FILES[1]).read_bytes()
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "pipeline.dossier_events":
            raise ModuleNotFoundError("not ready: pipeline.dossier_events", name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="pipeline.dossier_events"):
        m.merge(RULE, "fixture")
    assert ledger.current_pack().version == 1 and (world.root / FILES[1]).read_bytes() == before


def test_real_t7_backfill_integration_when_available(world, monkeypatch):
    monkeypatch.delitem(sys.modules, "pipeline.dossier_events")
    try:
        importlib.import_module("pipeline.dossier_events")
    except ModuleNotFoundError as exc:
        if exc.name != "pipeline.dossier_events":
            raise
        pytest.skip("not ready: pipeline.dossier_events")
    candidate(world.root)
    previous = seed().verdicts[0]
    pack = m.merge(RULE, "fixture")
    current = ledger.get_session("synthetic").verdicts[0]
    assert current.verdict == "red" and current.supersedes == previous.verdict_id
    assert current.rule_pack_version == pack.version


def test_backfill_failure_rolls_back_pack_files_and_own_index(world, monkeypatch):
    candidate(world.root)
    git(world.root, "add", ".")
    before = (world.root / FILES[1]).read_bytes()
    index = git(world.root, "write-tree")
    def broken(pack):
        ledger.append_event("backfill.done", {"version": pack.version})
        raise ValueError("Synthetic backfill failure")
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", SimpleNamespace(backfill=broken))
    with pytest.raises(ValueError, match="Synthetic"):
        m.merge(RULE, "fixture")
    assert ledger.current_pack().version == 1 and ledger.events_since(0) == []
    assert (world.root / FILES[1]).read_bytes() == before and git(world.root, "write-tree") == index


@pytest.mark.parametrize("count,other_sku,flag", [(4, False, False), (5, False, True), (1, True, True)])
def test_preview_flags_many_cases_and_other_skus(world, count, other_sku, flag):
    candidate(world.root)
    for number in range(count):
        seed(f"synthetic-{number}", sku="other" if other_sku else "synthetic")
    preview = m.dry_run(RULE)
    assert preview.changed_cases == count + 1
    assert preview.flag_many is flag


def test_outside_source_golden_changes_are_counted(world):
    candidate(world.root)
    with paths.GOLDEN_CASES.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(case_id="outside", sku_id="other", text="synthetic treatment", label="red")) + "\n")
    preview = m.dry_run(RULE)
    assert preview.changed_cases == 2 and preview.changed_golden_outside_sources == 1 and preview.flag_many


@pytest.mark.parametrize("status,negq", [("not_evaluated", 0), ("ok", 1)])
def test_cache_miss_refuses_partial_preview(world, monkeypatch, status, negq):
    from core.models import ReplayResult
    candidate(world.root)
    monkeypatch.setattr(runner, "replay", lambda *args, **kwargs: [ReplayResult(status=status, negq_miss=negq)])
    with pytest.raises(ValueError, match="cache miss"):
        m.dry_run(RULE)
    assert ledger.current_pack().version == 1
