import json
from pathlib import Path
import shutil
import sys
import subprocess
import textwrap
from types import ModuleType, SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from core.models import Atom, LLMCall, ReplayResult
from gate import behaviour as b


FIXTURES = Path(__file__).parent / "gate_fixtures" / "behaviour"


@pytest.fixture
def env(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate with spaces.and.dots"
    (candidate / "rules").mkdir(parents=True)
    for name in ("R04x.py", "R04x.meta.json"):
        shutil.copyfile(FIXTURES / name, candidate / "rules" / name)
    golden = tmp_path / "golden.jsonl"
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    golden.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    monkeypatch.setattr(paths, "GOLDEN_CASES", golden)
    before, after, candidate_pack = object(), object(), object()
    monkeypatch.setattr(b, "_packs", lambda *a: (before, after, candidate_pack, 7))
    state = SimpleNamespace(candidate=candidate, before=before, after=after,
        candidate_pack=candidate_pack, old=["abstain", "green", "red"],
        new=["red", "green", "red"], hits=set(), calls=[],
        missing=False, negq_miss=0, memory_old="abstain", memory_new="red")

    def replay(cases, *, pack, persist, purpose, pack_version):
        assert not persist and purpose == "live" and pack_version == 7
        assert pack in (before, after)
        state.calls.append(("replay", cases, pack))
        colours = state.old if pack is before else state.new
        return [ReplayResult(status="not_evaluated" if state.missing else "ok",
                             case_verdict=c, negq_miss=state.negq_miss) for c in colours]

    def run_cases(cases, *, purpose, pack, pack_version):
        assert purpose == "live" and pack_version == 7
        state.calls.append(("memory", cases, pack))
        colour = state.memory_old if pack is before else state.memory_new
        return [ReplayResult(status="ok", case_verdict=colour) for c in cases]

    runner = ModuleType("pipeline.runner")
    runner.replay, runner.run_cases = replay, run_cases
    monkeypatch.setitem(sys.modules, "pipeline.runner", runner)
    import pipeline
    monkeypatch.setattr(pipeline, "runner", runner, raising=False)

    def hits(case, pack):
        assert pack is candidate_pack
        state.calls.append(("hits", case, pack))
        return [object()] if case["case_id"] in state.hits else []

    monkeypatch.setattr(b, "_candidate_hits", hits)
    cluster = tmp_path / "cluster.json"
    cluster.write_text(json.dumps(dict(name="demo", item_ids=["source"],
        texts=[cases[0]["text"]], labels=["red"], cluster_hash="demo",
        created_at="2026-10-31T00:00:00Z", commit="demo", runs=[])), encoding="utf-8")
    state.cluster = cluster
    return state


def run(env, **kwargs):
    return b.run_behaviour("R04x", env.candidate, **kwargs)


def test_only_fixes_pass_with_spaces_and_source(env):
    result = run(env, sources=env.cluster)
    assert result.passed
    assert [c.id for c in result.checks] == [8, 9, 10]
    assert result.observed_changes == [{"item_id": "source", "before": "abstain", "after": "red"}]
    assert result.error_lines == result.revealed_case_ids == []


def test_source_remaining_abstain_fails_8(env):
    env.new[0] = "abstain"
    result = run(env, sources=env.cluster)
    assert not result.passed
    assert len(result.error_lines) == 1
    assert result.error_lines[0].startswith("R04x check 8:")
    assert "source" in result.error_lines[0]
    assert result.revealed_case_ids == ["source"]


def test_green_to_red_fails_9(env):
    env.new[1] = "red"
    result = run(env)
    assert not result.passed
    assert result.error_lines[0].startswith("R04x check 9:")
    assert "outside" in result.error_lines[0]


def test_own_hit_outside_family_fails_even_when_case_colour_stays_red(env):
    env.old[1] = env.new[1] = "red"
    env.hits.add("outside")
    result = run(env)
    assert len(result.error_lines) == 1
    assert result.error_lines[0].startswith("R04x check 10:")
    assert "outside" in result.error_lines[0] and "R05" in result.error_lines[0]
    assert "outside" not in [c["item_id"] for c in result.observed_changes]


@pytest.mark.parametrize("missing,negq", [(True, 0), (False, 1)])
def test_cache_incomplete_is_only_shadow_line(env, missing, negq):
    env.missing, env.negq_miss = missing, negq
    result = run(env, sources=env.cluster)
    assert result.error_lines == [b.CACHE_MISS]
    assert not result.passed
    assert result.observed_changes == result.revealed_case_ids == []


def test_check10_cache_miss_discards_partial_failures(env, monkeypatch):
    env.new[1] = "red"
    def missing(*args):
        raise b._CacheMiss
    monkeypatch.setattr(b, "_candidate_hits", missing)
    assert run(env).error_lines == [b.CACHE_MISS]


def test_after_cache_miss_is_not_a_rule_failure(env, monkeypatch):
    from pipeline import runner
    original = runner.replay
    def replay(cases, **kwargs):
        if kwargs["pack"] is env.after:
            return [ReplayResult(status="not_evaluated") for case in cases]
        return original(cases, **kwargs)
    monkeypatch.setattr(runner, "replay", replay)
    assert run(env).error_lines == [b.CACHE_MISS]


def test_unchanged_errors_and_improvements_do_not_block(env):
    env.new[2] = "yellow"
    assert run(env).passed


def test_measurement_forces_offline_without_changing_settings(env, monkeypatch):
    from pipeline import llm, runner
    from core.config import settings
    set_override("OFFLINE", 0)
    original = runner.replay
    def replay(cases, **kwargs):
        assert llm._OFFLINE.get() and llm._MEASURING.get()
        assert settings.OFFLINE == 0
        return original(cases, **kwargs)
    monkeypatch.setattr(runner, "replay", replay)
    run(env)
    assert settings.OFFLINE == 0 and not llm._OFFLINE.get()


def test_rule_id_cannot_escape_candidate_directory(tmp_path):
    with pytest.raises(ValueError, match="Rule id"):
        b.run_behaviour("../other", tmp_path, ci=True)


@pytest.mark.parametrize("ci,sources", [(False, None), (True, "does-not-exist")])
def test_sources_skip(env, ci, sources):
    result = run(env, ci=ci, sources=sources)
    assert result.checks[0].passed and result.checks[0].detail == "not provided"


def test_broadcast_dates_are_passed_to_runner(env):
    run(env)
    cases = env.calls[0][1]
    assert [c["as_of"] for c in cases] == ["2025-01-08", "2026-10-21", "2025-01-08"]


def test_missing_source_is_rejected(env):
    raw = json.loads(env.cluster.read_text(encoding="utf-8"))
    raw["item_ids"] = ["missing"]
    env.cluster.write_text(json.dumps(raw), encoding="utf-8")
    assert "source item missing is missing" in run(env, sources=env.cluster).error_lines[0]


@pytest.mark.parametrize("field,value", [("texts", ["Changed text"]), ("labels", ["yellow"])])
def test_cluster_must_match_frozen_items(env, field, value):
    raw = json.loads(env.cluster.read_text(encoding="utf-8"))
    raw[field] = value
    env.cluster.write_text(json.dumps(raw), encoding="utf-8")
    assert "differs from its frozen cluster" in run(env, sources=env.cluster).error_lines[0]


def test_memory_uses_configured_path_and_passes_recorded_date(env):
    path = paths.regressions_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(item_id="memory", level="case", text="A synthetic memory claim.",
        sku_id="kera", as_of="2024-12-12", label="red", rule_ids=["R04"],
        labeler="hungkhuu404", ts="2026-10-31T00:00:00Z")) + "\n", encoding="utf-8")
    result = run(env)
    assert result.passed
    memory = next(c[1][0] for c in env.calls if c[0] == "memory")
    assert memory["case_id"] == "memory" and memory["as_of"] == "2024-12-12"
    assert memory["source_date"] == "2024-12-12"
    assert any(c["item_id"] == "memory" for c in result.observed_changes)


def test_atom_comparison_excludes_session_colour():
    result = ReplayResult(status="ok", case_verdict="red")
    assert b._colour({"level": "atom"}, result) == "abstain"
    assert b._colour({"level": "case"}, result) == "red"


@pytest.mark.parametrize("label,values", [
    ("green", ["green", "abstain", "yellow", "red"]),
    ("yellow", ["yellow", "abstain", "green", "red"]),
    ("red", ["red", "abstain", "green", "yellow"]),
])
def test_error_scale(label, values):
    ranks = [b._distance({"label": label}, v) for v in values]
    assert ranks[0] == 0 < ranks[1] < ranks[2] <= ranks[3]


def test_abstain_expected_colours_are_equally_farther():
    case = {"label": "green", "abstain_expected": True}
    assert b._distance(case, "abstain") == 0
    assert {b._distance(case, c) for c in ("green", "yellow", "red")} == {3}


def test_pack_selection_ci_never_opens_ledger(monkeypatch, tmp_path):
    from rules import registry
    import ledger
    def forbidden():
        pytest.fail("CI must not open a ledger")
    monkeypatch.setattr(ledger, "current_pack", forbidden)
    metas = [SimpleNamespace(rule_id=name, meta=SimpleNamespace(approved_by=approved, status=status))
             for name, approved, status in [("R04", "hungkhuu404", "active"),
                ("R05", None, "active"), ("R09", "hungkhuu404", "quarantined")]]
    monkeypatch.setattr(registry, "all_rules", lambda: metas)
    calls = []
    monkeypatch.setattr(registry, "load_pack", lambda ids, **kw: calls.append((ids, kw)))
    b._packs("R04x", tmp_path, ci=True)
    assert calls[0] == (["R04"], {"quarantined": [], "mode": "live"})
    assert calls[1][0] == ["R04", "R04x"] and calls[1][1]["mode"] == "measure"
    assert calls[2][0] == ["R04x"]
    assert not paths.ledger_db().exists()


def test_missing_live_ledger_is_not_created(tmp_path):
    with pytest.raises(ValueError, match="LEDGER_DB"):
        b._packs("R04x", tmp_path, ci=False)
    assert not paths.ledger_db().exists()


def test_live_pack_comes_from_actual_temp_ledger(tmp_path):
    import ledger
    ledger.init_db(paths.ledger_db())
    expected = ledger.create_pack_version(["R04"], [], "merge", approved_by="hungkhuu404")
    candidate = tmp_path / "candidate"
    (candidate / "rules").mkdir(parents=True)
    for name in ("R04x.py", "R04x.meta.json"):
        shutil.copyfile(FIXTURES / name, candidate / "rules" / name)
    before, after, own, version = b._packs("R04x", candidate, ci=False)
    assert version == expected.version
    assert [r.rule_id for r in before.active] == ["R04"]
    assert [r.rule_id for r in after.active] == ["R04", "R04x"]
    assert [r.rule_id for r in own.active] == ["R04x"]


def test_candidate_probe_uses_own_pack_and_verified_atom(monkeypatch):
    from pipeline import context, extract
    calls = []
    pack = SimpleNamespace(check_atom=lambda view: calls.append(view) or ["hit"])
    def ctx(sku, day, *, pack):
        assert sku == "demo_sku" and pack is probe
        return SimpleNamespace(category_norm="TPBVSK", as_of=day)
    probe = pack
    monkeypatch.setattr(context, "build_context", ctx)
    monkeypatch.setattr(extract, "extract_atoms", lambda *a, **kw: [Atom(
        atom_id="t01.a1", turn_id="t01", span="demo trigger", start=0, end=12,
        axis="cong_dung", speaker_role="duoc_si", role_span="dược sĩ",
        subject_is_product=True, s1_call_id="cached")])
    case = dict(case_id="demo", sku_id="demo_sku", as_of="2025-01-08", text="demo trigger")
    assert b._candidate_hits(case, pack) == ["hit"]
    assert calls[0].speaker_role == "duoc_si"


def test_missing_runner_propagates(env, monkeypatch):
    import pipeline
    monkeypatch.delattr(pipeline, "runner", raising=False)
    monkeypatch.setitem(sys.modules, "pipeline.runner", None)
    with pytest.raises(ModuleNotFoundError, match="pipeline.runner"):
        run(env)


def test_ci_gates_only_added_t5_rule_files(monkeypatch, tmp_path):
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: Gate new adaptive rules\n", 1)[1].split("\n      - ", 1)[0]
    assert "if: github.event_name == 'pull_request'" in step
    assert "fetch-depth: 0" in workflow
    assert "CACHE_BACKEND || 'codex'" in workflow and 'OFFLINE: "1"' in workflow
    source = textwrap.dedent(step.split("        run: |\n", 1)[1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_BASE", "main")
    (tmp_path / "rules").mkdir()
    for name, origin in [("R04x", "t5"), ("R09x", "t5"), ("R04", "law_compiled")]:
        (tmp_path / "rules" / f"{name}.meta.json").write_text(
            json.dumps(dict(id=name, origin=origin)), encoding="utf-8")
    calls = []
    def execute(args, **kwargs):
        calls.append(args)
        if args[0] == "git":
            assert args == ["git", "diff", "--name-only", "--diff-filter=A", "-z",
                            "origin/main...HEAD", "--", "rules/"]
            return SimpleNamespace(stdout="\0".join([
                "rules/R04x.py", "rules/R09x.py", "rules/R04.py", "rules/registry.py",
                "rules/_common.py", "rules/roles.py", "rules/nearhit.py",
                "rules/R04x.meta.json", "rules/nested/ignored.py", ""]))
        assert kwargs["check"]
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", execute)
    exec(compile(source, "ci-rule-step", "exec"), {})
    assert [call[7] for call in calls[1:]] == ["R04x", "R09x"]
    assert all(call[:7] == [sys.executable, "-m", "gate.run", "--ci", "--base",
                             "origin/main", "--rule"] for call in calls[1:])


def real_runner_fixture(monkeypatch, tmp_path, *, rule_id="R04x", firing=False, cached=True):
    """Temporary synthetic S1 rows; no backend call or repository cache writes."""
    from core.config import settings
    from pipeline import extract, ingest, llm

    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    cache = tmp_path / "synthetic cache"
    set_override("LLM_CACHE_DIR", str(cache))
    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic integration must never call a backend")
    monkeypatch.setattr(llm, "_backend", forbidden)
    candidate = tmp_path / "candidate with spaces.and.dots"
    (candidate / "rules").mkdir(parents=True)
    source = (FIXTURES / "R04x.py").read_text(encoding="utf-8").replace('"R04x"', f'"{rule_id}"')
    meta = json.loads((FIXTURES / "R04x.meta.json").read_text(encoding="utf-8"))
    meta["id"] = rule_id
    (candidate / "rules" / f"{rule_id}.py").write_text(source, encoding="utf-8")
    (candidate / "rules" / f"{rule_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    text = "A demo trigger claim." if firing else "An ordinary synthetic claim."
    case = dict(case_id="synthetic", sku_id="demo_sku", source_date="2025-01-08",
                text=text, label="red", rule_ids=["R04"] if firing else ["R05"])
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps(case) + "\n", encoding="utf-8")
    monkeypatch.setattr(paths, "GOLDEN_CASES", golden)
    if cached:
        session = ingest.from_eval_case(case)
        request = extract._request(session, session.turns[0], None, "live", True)
        sha = llm._identity(settings.LLM_BACKEND, request["model"], request["effort"],
                            "s1", llm._schema("s1"), request["messages"])
        row = LLMCall(request_sha256=sha, kind="s1", purpose="live", backend=settings.LLM_BACKEND,
            model=request["model"], effort=request["effort"], latency_ms=0,
            ts="2026-10-31T00:00:00Z", response={"atoms": [dict(span=text, axis="cong_dung",
                speaker_role="khong_ro", role_span=None, subject_is_product=True)]})
        location = llm.cache_path(cache, sha)
        location.parent.mkdir(parents=True)
        location.write_text(row.model_dump_json(), encoding="utf-8")
    return candidate


def test_real_runner_and_router_without_live_ledger(monkeypatch, tmp_path):
    candidate = real_runner_fixture(monkeypatch, tmp_path)
    result = b.run_behaviour("R04x", candidate, ci=True)
    assert result.passed and result.observed_changes == []
    assert not paths.ledger_db().exists()


def test_real_runner_and_router_measure_code_red_with_new_candidate_id(monkeypatch, tmp_path):
    from rules.registry import all_rules

    assert "R04x" not in {rule.rule_id for rule in all_rules()}
    candidate = real_runner_fixture(monkeypatch, tmp_path, firing=True)
    result = b.run_behaviour("R04x", candidate, ci=True)
    assert result.passed
    assert result.observed_changes == [dict(item_id="synthetic", before="abstain", after="red")]
    assert not paths.ledger_db().exists()


def test_real_candidate_hits_outside_labelled_family(monkeypatch, tmp_path):
    candidate = real_runner_fixture(monkeypatch, tmp_path, firing=True)
    case = json.loads(paths.GOLDEN_CASES.read_text(encoding="utf-8"))
    case.update(label="yellow", rule_ids=["R05"])
    paths.GOLDEN_CASES.write_text(json.dumps(case) + "\n", encoding="utf-8")
    result = b.run_behaviour("R04x", candidate, ci=True)
    assert any(line.startswith("R04x check 10:") and "synthetic" in line
               for line in result.error_lines)
    assert not paths.ledger_db().exists()


def test_real_runner_missing_cache_returns_only_shadow_line(monkeypatch, tmp_path):
    candidate = real_runner_fixture(monkeypatch, tmp_path, cached=False)
    result = b.run_behaviour("R04x", candidate, ci=True)
    assert result.error_lines == [b.CACHE_MISS]
    assert not paths.ledger_db().exists()
