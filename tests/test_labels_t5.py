"""Synthetic labels, durable cluster memory and dry draft dispatch."""

import builtins
import json
import subprocess
import sys
from threading import Event
import time
from types import SimpleNamespace

import pytest

from core import paths
from core.config import config_fields, set_override, settings
from core.models import Atom, ClusterFile, ClusterRun, Job, NearHit, Session, Turn, Verdict
import ledger
from loop import labels, t5

TS = "2026-10-21T00:00:00Z"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "A. synthetic repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("Synthetic\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture base")
    remote = tmp_path / "fake remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache/llm")
    set_override("EVAL_DIR", "memory")
    set_override("LOOP_DRY", 1)
    ledger.init_db(settings.LEDGER_DB)
    started = Event()
    calls = []
    def run_job(job_id):
        calls.append(job_id)
        started.set()
    monkeypatch.setitem(sys.modules, "loop.draft", SimpleNamespace(run_job=run_job))
    quarantines = []
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", SimpleNamespace(
        quarantine=lambda *args: quarantines.append(args)))
    return SimpleNamespace(root=root, remote=remote, started=started, calls=calls, quarantines=quarantines)


def seed(number, pattern="phòng … bệnh", origin="abstain", text=None, session_id=None, calls=()):
    session_id = session_id or f"synthetic-{number}"
    text = text or f"Sản phẩm phòng nhiều bệnh {number}"
    session = Session(session_id=session_id, sku_pin="synthetic", as_of="2026-10-21", channel="written",
        source="Synthetic public sentence", turns=[Turn(turn_id="t01", speaker_label="[KOL 1]",
        attribution="constructed", text=text)])
    ledger.add_session(session)
    near_hits = [NearHit(pattern=pattern, gap=1, law_anchor="LDUOC-2016/D6/K15", law_span="chữa bệnh", span=text)] if pattern else []
    atom = Atom(atom_id="t01.a1", turn_id="t01", span=text, start=0, end=len(text), axis="cong_dung",
                speaker_role="kol", subject_is_product=True, near_hits=near_hits, s1_call_id=calls[0] if calls else None)
    ledger.add_atoms(session_id, [atom])
    verdict = Verdict(atom_key=f"{session_id}:t01.a1", rule_pack_version=1, verdict=origin,
        decided_by="abstain:DOSSIER_EMPTY" if origin == "abstain" else "rule:R04" if origin == "red" else "llm_votes",
        reason_code="DOSSIER_EMPTY" if origin == "abstain" else None, as_of=session.as_of,
        dossier_hash="synthetic", ts=TS, llm_call_ids=list(calls[1:]))
    verdict_id, = ledger.append_verdicts([verdict])
    return verdict.atom_key, verdict_id


def record(number, **kwargs):
    target, _ = seed(number, **kwargs)
    return labels.record_label(target, "red", "R04", "fixture")


def cluster_files():
    return [ClusterFile.model_validate_json(file.read_text(encoding="utf-8")) for file in paths.clusters_dir().glob("*.json")]


@pytest.mark.parametrize("origin,pattern", [("abstain", "phòng … bệnh"), ("yellow", "trị … bệnh")])
def test_third_label_starts_one_job(world, origin, pattern):
    first = record(1, origin=origin, pattern=pattern)
    record(2, origin=origin, pattern=pattern)
    assert ledger.list_jobs() == [] and world.calls == []
    before = time.monotonic()
    record(3, origin=origin, pattern=pattern)
    assert world.started.wait(max(0, 1 - (time.monotonic() - before)))
    assert time.monotonic() - before < 1
    job, = ledger.list_jobs()
    assert world.calls == [job.job_id] and job.status == "queued" and job.dry
    assert job.rule_id == "R04b" and job.branch == f"rule/R04b-{job.job_id}"
    assert job.config == config_fields()["cfg"]
    cluster, = cluster_files()
    assert cluster.name == "t5-" + cluster.cluster_hash[:8] and cluster.commit == "" and cluster.runs == []
    assert len(cluster.item_ids) == 3 and cluster.cluster_hash == job.cluster_hash
    events = ledger.events_since(0)
    assert [event.type for event in events].count("t5.cluster") == 1
    assert next(event.payload for event in events if event.type == "job.started")["rule_id"] == "R04b"
    assert first.level == "atom" and first.text.endswith("1") and first.sku_id == "synthetic"
    assert first.source_note == "Synthetic public sentence" and first.origin_verdict.verdict == origin
    assert len(ledger.list_labels()) == 3
    record(4, origin=origin, pattern=pattern)
    assert len(ledger.list_jobs()) == len(cluster_files()) == 1
    assert t5.maybe_trigger() == []


def test_different_patterns_and_keys_do_not_mix(world):
    record(1)
    record(2, pattern="trị … bệnh")
    record(3, origin="yellow")
    assert ledger.list_jobs() == []


def test_cluster_files_are_memory_on_fresh_ledger(world, tmp_path):
    old = [record(i) for i in (1, 2)]
    target, _ = seed(3)
    old.append(labels.record_label(target, "red", "R04", "fixture", auto_trigger=False))
    cluster = ClusterFile(name="pr2", item_ids=[item.item_id for item in old], texts=[item.text for item in old],
                          labels=[item.label for item in old], cluster_hash="synthetic", created_at=TS, commit="")
    paths.clusters_dir().mkdir(parents=True)
    (paths.clusters_dir() / "pr2.json").write_text(cluster.model_dump_json(), encoding="utf-8")
    ledger.init_db(tmp_path / "fresh.db")
    record(4, pattern="trị … bệnh")
    record(5, pattern="trị … bệnh")
    assert ledger.list_jobs() == [] and world.calls == []


def test_no_hit_similarity_requires_every_member(world):
    for number, text in enumerate(["abcdefghi", "abcdefghi", "abcdefghi"], 1):
        record(number, pattern=None, text=text)
    assert world.started.wait(1) and len(ledger.list_jobs()) == 1
    assert t5._similar("abcdefgh", "abcdefgh") == 1
    assert t5._similar("abc", "xyz") == 0
    assert t5._similar("", "a") == 0


def test_no_hit_items_that_are_dissimilar_do_not_cluster(world):
    for number, text in enumerate(["aaaaaaaa", "zzzzzzzz", "bbbbbbbb"], 1):
        record(number, pattern=None, text=text)
    assert ledger.list_jobs() == []


def test_shared_pattern_and_no_hit_member_cluster_once(world):
    for number in (1, 2, 3):
        target, _ = seed(number, text="Synthetic same phrase", pattern="phòng … bệnh" if number < 3 else None)
        if number == 1:
            atom = ledger.get_session("synthetic-1").atoms[0]
            atom.near_hits.append(atom.near_hits[0].model_copy(update={"pattern": "trị … bệnh"}))
            with ledger.transaction():
                ledger.replace_atoms("synthetic-1", [atom])
        labels.record_label(target, "red", "R04", "fixture", auto_trigger=False)
    assert len(t5.maybe_trigger()) == 1
    assert world.started.wait(1)
    assert len(cluster_files()) == 1


def test_atom_key_and_old_verdict_id_use_effective_verdict(world):
    target, old = seed(1)
    new = Verdict.model_validate(ledger.get_verdict(old).model_dump() | {
        "verdict": "yellow", "reason_code": None, "decided_by": "llm_votes"})
    latest, = ledger.append_verdicts([new])
    item = labels.record_label(old, "red", "R04", "fixture", auto_trigger=False)
    assert item.origin_verdict.verdict == "yellow"
    assert ledger.list_labels()[0]["verdict_id"] == latest
    assert ledger.list_labels()[0]["target"] == target
    assert git(world.root, "rev-list", "--count", "HEAD") == "1"
    assert not paths.CACHE_DIR.exists()


def test_clip_is_refused_without_writes(world):
    target, verdict_id = seed(1, session_id="clip-synthetic")
    for invoke in (lambda: labels.record_label(target, "red", "R04", "fixture"),
                   lambda: labels.record_report_wrong(verdict_id, "fixture")):
        with pytest.raises(ValueError, match="Clip"):
            invoke()
    assert ledger.list_labels() == [] and not paths.regressions_file().exists()


def test_autocommit_copies_cache_and_never_pushes(world):
    from pipeline.llm import cache_path
    set_override("REGRESSION_AUTOCOMMIT", 1)
    calls = ["a" * 64, "b" * 64]
    for call in calls:
        file = cache_path(paths.local_cache_dir(), call)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"request_sha256": call, "response": {}}), encoding="utf-8")
    target, _ = seed(1, calls=calls)
    item = labels.record_label(target, "red", "R04", "fixture", auto_trigger=False)
    assert git(world.root, "log", "-1", "--format=%s") == f"regression: {item.item_id}"
    assert git(world.root, "status", "--porcelain") == ""
    assert not list((world.remote / "refs/heads").iterdir())
    for call in calls:
        assert cache_path(paths.CACHE_DIR, call).read_bytes() == cache_path(paths.local_cache_dir(), call).read_bytes()
    event = next(event for event in ledger.events_since(0) if event.type == "label.recorded")
    assert event.payload["commit"] == git(world.root, "rev-parse", "HEAD")


def test_autocommit_refuses_unrelated_staged_rename(world):
    set_override("REGRESSION_AUTOCOMMIT", 1)
    git(world.root, "mv", "README.md", "other.md")
    target, _ = seed(1)
    with pytest.raises(ValueError, match="Unrelated"):
        labels.record_label(target, "red", "R04", "fixture", auto_trigger=False)
    assert ledger.list_labels() == [] and not paths.regressions_file().exists()


@pytest.mark.parametrize("staged,allowed", [("docs\\unrelated.txt", False), ("memory\\regressions.jsonl", True)])
def test_windows_staged_paths_are_compared_as_posix(world, monkeypatch, staged, allowed):
    monkeypatch.setattr(labels, "_git", lambda *args: SimpleNamespace(stdout=staged + "\0"))
    target, verdict_id = seed(1)
    atom = ledger.get_session("synthetic-1").atoms[0]
    if allowed:
        assert labels._commit_paths(atom, ledger.get_verdict(verdict_id))[1] == ["memory/regressions.jsonl"]
    else:
        with pytest.raises(ValueError, match="Unrelated"):
            labels._commit_paths(atom, ledger.get_verdict(verdict_id))


def test_autocommit_refuses_external_eval(world, tmp_path):
    set_override("REGRESSION_AUTOCOMMIT", 1)
    set_override("EVAL_DIR", str(tmp_path / "external memory"))
    target, _ = seed(1)
    with pytest.raises(ValueError, match="EVAL_DIR inside"):
        labels.record_label(target, "red", "R04", "fixture", auto_trigger=False)
    assert ledger.list_labels() == [] and not paths.regressions_file().exists()


def test_allocate_skips_rules_clusters_and_jobs(world):
    (world.root / "rules").mkdir()
    (world.root / "rules/R04b.py").write_text("", encoding="utf-8")
    run = ClusterRun(job_id="old", rule_id="R04c", branch="rule/old", started=TS, result="failed")
    cluster = ClusterFile(name="old", cluster_hash="old", created_at=TS, commit="", runs=[run])
    paths.clusters_dir().mkdir(parents=True)
    (paths.clusters_dir() / "old.json").write_text(cluster.model_dump_json(), encoding="utf-8")
    ledger.create_job(Job(job_id="reserved", rule_id="R04d", branch="rule/reserved", cluster_key="test",
                          cluster_hash="test", status="queued", config="test", ts=TS))
    assert t5.allocate_rule_id("R04") == "R04e"


def test_two_reports_quarantine_once_and_never_guess_label(world):
    _, first = seed(1, origin="red")
    _, second = seed(2, origin="red")
    for verdict_id in (first, second, first):
        assert labels.record_report_wrong(verdict_id, "fixture") is None
    assert world.quarantines == [("R04", "reported wrong")]
    assert all(row["label"] is None for row in ledger.list_labels())
    assert not paths.regressions_file().exists()


def test_keeper_report_and_correct_label(world):
    _, verdict_id = seed(1, origin="red")
    item = labels.record_report_wrong(verdict_id, settings.RULE_KEEPER, "yellow")
    assert world.quarantines == [("R04", "reported wrong")]
    assert item.kind == "report_wrong" and item.label == "yellow" and item.rule_ids == ["R04"]


def test_missing_draft_module_does_not_consume_items(world, monkeypatch):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "loop.draft":
            raise ModuleNotFoundError("not ready: loop.draft", name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    record(1)
    record(2)
    with pytest.raises(ModuleNotFoundError, match="loop.draft"):
        record(3)
    assert ledger.list_jobs() == [] and cluster_files() == []
