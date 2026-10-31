"""Aggregate-only replay checks using invented cases and fake transports."""

import builtins
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unicodedata

import pytest

from core import paths
from core.config import set_override
from core.models import ReplayResult, RuleHit
from gate import oracle


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    cases = [dict(case_id="invented-case-alpha", sku_id="dk_betics_gold",
                  text="SYNTHETIC_ALPHA_MARKER công dụng tưởng tượng", label="yellow"),
             dict(case_id="invented-case-beta", sku_id="dk_betics_gold",
                  text="SYNTHETIC_BETA_MARKER không cần thêm thứ gì để chữa bệnh", label="red")]
    source = tmp_path / "synthetic.jsonl"
    jsonl(source, cases)
    manifest = tmp_path / "freeze.json"
    manifest.write_text(json.dumps({"canonicalization": oracle.CANONICALIZATION,
        "files": {source.name: {"sha256": oracle.canonical_hash(cases), "n": len(cases)}}}), encoding="utf-8")
    monkeypatch.setattr(oracle, "FREEZE_FILE", manifest)
    monkeypatch.setattr(oracle, "config_line", lambda: "cfg=synthetic offline=1")
    set_override("LLM_BACKEND", "api")
    return SimpleNamespace(cases=cases, source=source, manifest=manifest,
                           cache=tmp_path / "external cache", out=tmp_path / "aggregate.json")


def args(inputs):
    return ["replay", "--heldout", str(inputs.source), "--cache-dir", str(inputs.cache),
            "--out", str(inputs.out), "--v2-rules", "R04x"]


def pack(ids=("R04",)):
    return SimpleNamespace(active=tuple(SimpleNamespace(rule_id=rule_id) for rule_id in ids))


def result(colour, *, miss=False, negq=0, decided_by=None):
    if miss:
        return ReplayResult(status="not_evaluated", negq_miss=negq)
    provenance = decided_by or ("abstain:LLM_OFF" if colour == "abstain" else
                               "rule:R04" if colour == "red" else "llm_votes")
    return SimpleNamespace(status="ok", case_verdict=colour, negq_miss=negq,
                           verdicts=[{"decided_by": provenance}])


def fake_replay(monkeypatch, cases_seen, outputs):
    def replay(cases, *, pack, persist, purpose, pack_version):
        assert persist is False
        cases_seen.append((cases, purpose, pack_version))
        return outputs[pack_version - 1]
    monkeypatch.setitem(sys.modules, "pipeline.runner", SimpleNamespace(replay=replay))
    monkeypatch.setattr(oracle, "build_packs", lambda *unused: (pack(), pack(("R04", "R04x"))))


def repository_snapshot():
    status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=paths.REPO_ROOT,
                            check=True, capture_output=True).stdout
    local = {path.relative_to(paths.LOCAL_DIR): path.read_bytes()
             for path in paths.LOCAL_DIR.rglob("*") if path.is_file()}
    return status, paths.LOCAL_DIR.exists(), local


def test_canonical_hash_matches_committed_golden():
    manifest = json.loads(oracle.FREEZE_FILE.read_text(encoding="utf-8"))
    assert oracle.canonical_hash(oracle._jsonl(paths.GOLDEN_CASES)) == manifest["files"]["golden.jsonl"]["sha256"]
    first = {"case_id": "b", "text": "tiếng Việt"}
    second = {"case_id": "a", "text": "tưởng tượng"}
    assert oracle.canonical_hash([first, second]) == oracle.canonical_hash([
        {key: unicodedata.normalize("NFD", value) for key, value in second.items()}, first])


@pytest.mark.parametrize("option", ["--heldout", "--cache-dir", "--out", "--candidate-dir"])
def test_rejects_repository_paths_before_reading_cases(inputs, monkeypatch, option):
    values = args(inputs)
    if option == "--candidate-dir":
        values += [option, str(paths.REPO_ROOT / "synthetic candidate")]
    else:
        values[values.index(option) + 1] = str(paths.REPO_ROOT / "synthetic unsafe")
    monkeypatch.setattr(oracle, "_frozen_cases", lambda *unused: pytest.fail("unsafe path was read"))
    assert oracle.main(values) == 2
    assert not inputs.cache.exists() and not inputs.out.exists()


def test_rejects_repository_symlink(inputs, tmp_path, monkeypatch):
    link = tmp_path / "repository link"
    link.symlink_to(paths.REPO_ROOT, target_is_directory=True)
    values = args(inputs)
    values[values.index("--out") + 1] = str(link / "synthetic.json")
    monkeypatch.setattr(oracle, "_frozen_cases", lambda *unused: pytest.fail("unsafe symlink accepted"))
    assert oracle.main(values) == 2


@pytest.mark.parametrize("backend", ["off", "codex"])
def test_refuses_unusable_sealed_backend(inputs, monkeypatch, backend):
    set_override("LLM_BACKEND", backend)
    monkeypatch.setattr(oracle, "_frozen_cases", lambda *unused: pytest.fail("invalid backend read cases"))
    assert oracle.main(args(inputs)) == 2


def test_heldout_requires_external_cache(inputs):
    values = args(inputs)
    index = values.index("--cache-dir")
    del values[index:index + 2]
    assert oracle.main(values) == 2


@pytest.mark.parametrize("change", ["hash", "filename", "canonicalization"])
def test_hash_and_manifest_are_checked_before_pack_or_replay(inputs, monkeypatch, change):
    manifest = json.loads(inputs.manifest.read_text(encoding="utf-8"))
    if change == "hash":
        manifest["files"][inputs.source.name]["sha256"] = "0" * 64
    elif change == "filename":
        manifest["files"]["other.jsonl"] = manifest["files"].pop(inputs.source.name)
    else:
        manifest["canonicalization"] = "unknown canonicalization"
    inputs.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(oracle, "build_packs", lambda *unused: pytest.fail("invalid freeze reached rules"))
    assert oracle.main(args(inputs)) == 2
    assert not inputs.out.exists()


def test_missing_candidate_is_an_error(inputs, capsys):
    values = args(inputs)
    values[-1] = "SYNTHETIC_MISSING_RULE"
    assert oracle.main(values) == 2
    assert not inputs.out.exists()
    assert "SYNTHETIC_MISSING_RULE" not in capsys.readouterr().out


def test_metadata_packs_load_external_unapproved_candidate_with_spaces(tmp_path, monkeypatch):
    from rules import registry

    def info(rule_id, origin, approval, status="active"):
        return SimpleNamespace(rule_id=rule_id, meta=SimpleNamespace(origin=origin, approved_by=approval, status=status))
    discovered = [info("LAW", "law_compiled", "keeper"), info("T5", "t5", "keeper"),
                  info("DISABLED", "t5", "keeper", "quarantined"), info("UNAPPROVED", "law_compiled", None)]
    monkeypatch.setattr(registry, "all_rules", lambda: discovered)
    calls = []
    def load(ids, **kwargs):
        calls.append((ids, kwargs))
        return pack(ids)
    monkeypatch.setattr(registry, "load_pack", load)
    candidate = tmp_path / "candidate tree with spaces"
    oracle.build_packs("R04x", candidate)
    assert calls == [(["LAW"], {"mode": "live"}),
                     (["LAW", "T5", "R04x"], {"mode": "measure", "candidate_dir": candidate})]
    monkeypatch.undo()
    candidate_rules = candidate / "rules"
    candidate_rules.mkdir(parents=True)
    for suffix in (".py", ".meta.json"):
        shutil.copyfile(paths.REPO_ROOT / f"tests/gate_fixtures/behaviour/R04x{suffix}",
                        candidate_rules / f"R04x{suffix}")
    for reference in ("L1_PREDECL", "R04"):
        (candidate_rules / f"{reference}.py").write_text(
            f'RULE_ID = {reference!r}\nraise AssertionError("unselected reference was executed")\n',
            encoding="utf-8")
    before, after = oracle.build_packs("R04x", candidate)
    assert before.mode == "live" and after.mode == "measure"
    selected = next(item for item in after.active if item.rule_id == "R04x")
    assert selected.meta.approved_by is None
    assert selected.module.check(SimpleNamespace(text="demo trigger"))
    law_rule = next(item for item in after.active if item.rule_id == "R04")
    assert Path(law_rule.module.__file__).resolve().is_relative_to(paths.REPO_ROOT)


def test_transitions_misses_refusals_and_mcnemar(monkeypatch):
    monkeypatch.setattr(oracle, "overlap_flags", lambda cases: 2)
    cases = [dict(label="red"), dict(label="yellow"), dict(label="green"),
             dict(label="red"), dict(label="yellow", abstain_expected=True)]
    old = [result("abstain"), result("yellow"), result("green", miss=True), result("yellow"), result("abstain")]
    new = [result("red"), result("red"), result("green", miss=True, negq=1), result("yellow"), result("abstain")]
    report = oracle.summarize(cases, old, new, pack(), pack(("R04", "R04x")))
    assert report.n_cases == 5 and report.cache_miss == 1 and report.negq_miss == 1
    assert report.abstain_to_correct == report.correct_to_wrong == 1
    assert report.transitions == {"abstain → correct": 1, "correct → heavier than label": 1,
                                  "abstain → abstain": 1, "lighter than label → lighter than label": 1,
                                  "correct → correct": 1}
    assert report.red_wrong.model_dump() == {"k": 1, "n": 2}
    assert report.correct_refusals.model_dump() == {"x": 1, "n": 1}
    assert report.who_decided_v1.human == 3 and report.who_decided_v1.denominator == 5
    assert report.who_decided_v2.source == report.who_decided_v1.source == "oracle"
    assert report.mcnemar_p is None and report.overlap_flags == 2
    assert json.loads(report.pack_v2) == ["R04", "R04x"]
    with pytest.raises(ValueError, match="number of results"):
        oracle.summarize(cases, old[:-1], new, pack(), pack())


def test_exact_mcnemar():
    assert oracle.mcnemar(0, 9) is None
    assert oracle.mcnemar(0, 10) == 2 / 1024
    assert oracle.mcnemar(5, 5) == 1
    assert oracle.mcnemar(3, 8) == oracle.mcnemar(8, 3)


def test_overlap_counts_containment_and_cluster_sentences(tmp_path):
    directory = paths.eval_dir()
    jsonl(directory / "regressions.jsonl", [{"text": "alpha beta gamma"}])
    (directory / "clusters").mkdir()
    (directory / "clusters" / "synthetic.json").write_text(json.dumps({"texts": ["phi chi psi omega"]}), encoding="utf-8")
    cases = [{"text": text} for text in ["alpha beta gamma delta epsilon zeta", "phi chi psi omega", "unrelated words", ""]]
    assert oracle.overlap_flags(cases) == 2


@pytest.mark.parametrize("before,after,hint", [
    ("abstain", "yellow", "merge PR2"), ("yellow", "yellow", "merge, say reliability only"),
    ("yellow", "red", "do not merge, say why")])
def test_cli_hints_and_aggregate_output(inputs, monkeypatch, capsys, before, after, hint):
    seen = []
    fake_replay(monkeypatch, seen, [[result(before)] * 2, [result(after)] * 2])
    inputs.cases[1]["label"] = "yellow"
    jsonl(inputs.source, inputs.cases)
    manifest = json.loads(inputs.manifest.read_text(encoding="utf-8"))
    manifest["files"][inputs.source.name]["sha256"] = oracle.canonical_hash(inputs.cases)
    inputs.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    assert oracle.main(args(inputs)) == 0
    output = capsys.readouterr().out
    assert hint in output and "delete the extracted" in output
    report = json.loads(inputs.out.read_text(encoding="utf-8"))
    assert report["standin"] is False
    for case in inputs.cases:
        assert case["case_id"] not in output + inputs.out.read_text(encoding="utf-8")
        assert case["text"] not in output + inputs.out.read_text(encoding="utf-8")
    assert all(purpose == "oracle" for _, purpose, _ in seen)
    assert all(case["as_of"] == "2026-10-21" for cases, _, _ in seen for case in cases)


def test_standin_selects_every_nth_case_without_ledger_or_repo_writes(inputs, monkeypatch, capsys):
    monkeypatch.setattr(paths, "GOLDEN_CASES", inputs.source)
    seen = []
    fake_replay(monkeypatch, seen, [[result("red")], [result("red")]])
    before = repository_snapshot()
    assert not paths.ledger_db().exists()
    assert oracle.main(["replay", "--standin-every", "2", "--v2-rules", "R04x", "--out", str(inputs.out)]) == 0
    assert "STAND-IN, not the sealed set" in capsys.readouterr().out
    assert json.loads(inputs.out.read_text(encoding="utf-8"))["standin"] is True
    assert [case["case_id"] for case in seen[0][0]] == ["invented-case-beta"]
    assert all(purpose == "shadow" for _, purpose, _ in seen)
    assert repository_snapshot() == before and not paths.ledger_db().exists()


@pytest.mark.parametrize("interval", [0, -1])
def test_standin_requires_positive_interval(inputs, interval):
    assert oracle.main(["replay", "--standin-every", str(interval), "--v2-rules", "R04x", "--out", str(inputs.out)]) == 2


def test_missing_collaborator_reports_not_ready(inputs, monkeypatch, capsys):
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "rules.registry":
            raise ModuleNotFoundError(name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    assert oracle.main(args(inputs)) == 2
    assert "not ready: rules.registry" in capsys.readouterr().err


def test_nested_missing_dependency_is_not_hidden(inputs, monkeypatch):
    def missing(*args):
        raise ModuleNotFoundError(name="synthetic_dependency")
    monkeypatch.setattr(oracle, "build_packs", missing)
    with pytest.raises(ModuleNotFoundError):
        oracle.main(args(inputs))


@pytest.fixture
def transport_setup(inputs, monkeypatch):
    try:
        import pipeline.runner as runner
        from pipeline import llm
    except ModuleNotFoundError as error:
        if error.name not in ("pipeline.runner", "pipeline.llm"):
            raise
        pytest.skip(f"not ready: {error.name}")
    from pipeline.drafts import VerdictDraft
    from rules import registry

    info = next(rule for rule in registry.all_rules() if rule.rule_id == "R04")
    def check(view):
        return [RuleHit(rule_id="R04", family="R04", matched="chữa bệnh", law_anchor="LDUOC-2016/D6/K15",
                        law_span="chữa bệnh", precedence=4, ambiguous_negation=True)] if "không" in view.text else []
    rules = SimpleNamespace(active=(info,), quarantined=(), mode="measure", check_atom=check,
                            check_session=lambda *unused: [], quarantined_hits=lambda *unused: [])
    monkeypatch.setattr(oracle, "build_packs", lambda *unused: (rules, rules))
    def judge(atom, items, ctx, purpose, cache_read=True):
        outputs = [llm.call(kind=kind, purpose=purpose, model="synthetic", effort="low",
                           schema_name="test_response", cache_read=cache_read,
                           messages=[{"role": "user", "content": atom.span}]) for kind in ("t1", "t2")]
        if any(output.error for output in outputs):
            return VerdictDraft("abstain", "abstain:LLM_OFF", reason_code="LLM_OFF")
        return VerdictDraft("yellow", "llm_votes", family="R05")
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    kinds = []
    def transport(model, effort, schema_name, schema, messages, kind, timeout):
        kinds.append(kind)
        if kind == "s1":
            text = json.loads(messages[-1]["content"])["current_turn"]
            response = {"atoms": [dict(span=text, axis="cong_dung", speaker_role="khong_ro",
                                        role_span=None, subject_is_product=True)]}
        elif kind == "negq":
            response = {"answer": "not_negated"}
        else:
            response = {"ok": True}
        return llm._Transport(response=response)
    monkeypatch.setattr(llm, "_api", transport)
    set_override("OFFLINE", 0)
    set_override("LLM_CACHE_DIR", str(inputs.cache))
    cases = [case | {"source_date": "2026-10-21"} for case in inputs.cases]
    with llm.observe_results() as rows:
        runner.run_cases(cases, purpose="oracle", pack=rules, pack_version=1)
    assert "negq" in kinds
    monkeypatch.setattr(llm, "_api", lambda *unused: pytest.fail("replay called network transport"))
    return SimpleNamespace(runner=runner, llm=llm, rows=rows, rules=rules)


@pytest.mark.parametrize("missing_kind", [None, "s1", "t1", "negq"])
def test_real_cache_replay_is_external_private_and_network_free(inputs, transport_setup, capsys, missing_kind):
    setup = transport_setup
    if missing_kind:
        row = next(row for row in setup.rows if row.kind == missing_kind)
        setup.llm.cache_path(inputs.cache, row.request_sha256).unlink()
    before = repository_snapshot()
    assert not paths.ledger_db().exists()
    assert oracle.main(args(inputs)) == 0
    output = capsys.readouterr().out
    report_text = inputs.out.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["cache_miss"] == (1 if missing_kind in ("s1", "t1") else 0)
    assert report["negq_miss"] == (1 if missing_kind == "negq" else 0)
    if missing_kind in ("s1", "t1"):
        assert report["who_decided_v2"]["human"] >= 1
    assert report["who_decided_v1"]["source"] == report["who_decided_v2"]["source"] == "oracle"
    for case in inputs.cases:
        for marker in (case["case_id"], case["text"], case["text"].split()[0]):
            assert marker not in output + report_text
    assert list(inputs.cache.glob("*/*.json")) and (inputs.cache / "llm_calls.jsonl").is_file()
    assert not list(paths.local_cache_dir().rglob("*"))
    assert repository_snapshot() == before and not paths.ledger_db().exists()


@pytest.mark.xfail(strict=True, reason="blocked: shadow replay writes .local; pipeline owner read-only scope is pending")
def test_real_standin_cache_hits_leave_local_and_ledger_unchanged(inputs, transport_setup, monkeypatch, tmp_path):
    setup = transport_setup
    public_cache = tmp_path / "committed synthetic cache"
    shutil.copytree(inputs.cache, public_cache)
    monkeypatch.setattr(paths, "CACHE_DIR", public_cache)
    monkeypatch.setattr(paths, "GOLDEN_CASES", inputs.source)
    before = repository_snapshot()
    try:
        assert oracle.main(["replay", "--standin-every", "1", "--v2-rules", "R04x", "--out", str(inputs.out)]) == 0
        assert repository_snapshot() == before and not paths.ledger_db().exists()
    finally:
        # Keep this dependency regression itself from leaving a log in the checkout.
        prior = before[2].get(Path("llm_calls.jsonl"))
        log = paths.LOCAL_DIR / "llm_calls.jsonl"
        if prior is None:
            log.unlink(missing_ok=True)
        else:
            log.write_bytes(prior)
        if not before[1] and paths.LOCAL_DIR.exists():
            paths.LOCAL_DIR.rmdir()


def test_refuses_multiple_candidates_and_input_overwrite(inputs):
    assert oracle.main([*args(inputs), "--v2-rules", "R09x"]) == 2
    values = args(inputs)
    values[values.index("--out") + 1] = str(inputs.source)
    before = inputs.source.read_bytes()
    assert oracle.main(values) == 2
    assert inputs.source.read_bytes() == before
