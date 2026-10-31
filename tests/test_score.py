import json
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from core.enums import ErrorScale
from core.models import OracleReport
from gate import score
from pipeline import runner


@pytest.fixture
def scoring(tmp_path, monkeypatch):
    cases = [dict(case_id="sample-03", sku_id="kera", text="Synthetic three", label="red", source_date="2026-01-01"),
             dict(case_id="sample-01", sku_id="demo_sku", text="Synthetic one", label="green", source_date="2025-06-01"),
             dict(case_id="sample-02", sku_id="demo_sku", text="Synthetic two", label="red", abstain_expected=True)]
    source = tmp_path / "cases.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in cases) + "\n", encoding="utf-8")
    monkeypatch.setattr(paths, "GOLDEN_CASES", source)
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(score, "BASELINE_FILE", tmp_path / "baseline.json")
    monkeypatch.setattr(score, "config_line", lambda: "cfg=fixture")
    calls = []
    state = dict(status="ok", negq=0)

    def replay(cases, **kwargs):
        calls.append((cases, kwargs))
        return runner.ReplayBatch(SimpleNamespace(status=state["status"], case_verdict=case["label"]
            if not case.get("abstain_expected") else "abstain", negq_miss=state["negq"], verdicts=[])
            for case in cases)

    monkeypatch.setattr(runner, "replay", replay)
    monkeypatch.setattr(runner, "run_cases", replay)
    return source, calls, state


@pytest.mark.parametrize("predicted,label,refusal,expected", [
    ("green", "green", False, ErrorScale.CORRECT), ("abstain", "red", False, ErrorScale.ABSTAIN),
    ("yellow", "red", False, ErrorScale.LIGHTER_THAN_LABEL),
    ("red", "green", False, ErrorScale.HEAVIER_THAN_LABEL),
    ("abstain", "green", True, ErrorScale.CORRECT), ("green", "green", True, ErrorScale.HEAVIER_THAN_LABEL),
])
def test_error_scale(predicted, label, refusal, expected):
    assert score.classify_error(predicted, label, refusal) == expected


def test_v1_replay_aggregate_and_dates(scoring, capsys):
    _, calls, _ = scoring
    ledger = paths.ledger_db()
    assert not ledger.exists()
    assert score.main(["--aggregate-only"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["counts"]["correct"] == 3
    assert summary["accuracy_decided"] == dict(value=1, correct=2, denominator=2)
    assert summary["correct_refusals"] == dict(x=1, n=1)
    cases, kwargs = calls[0]
    assert [case["source_date"] for case in cases] == ["2025-01-08", "2025-06-01", "2026-10-21"]
    assert kwargs["pack_version"] == 1 and kwargs["persist"] is False
    assert not ledger.exists() and not paths.LOCAL_DIR.exists()


def test_standin_and_baseline_without_ledger(scoring, capsys):
    _, calls, _ = scoring
    assert score.main(["--standin-every", "2", "--aggregate-only"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["label"] == "STAND-IN" and summary["n_cases"] == 1
    assert calls[0][0][0]["case_id"] == "sample-02"
    assert score.main(["--write-baseline", "--aggregate-only"]) == 0
    assert json.loads(score.BASELINE_FILE.read_text(encoding="utf-8"))["wrong_case_ids"] == []
    assert not paths.ledger_db().exists()


@pytest.mark.parametrize("status,negq", [("not_evaluated", 0), ("ok", 1)])
def test_baseline_refuses_incomplete_cache(scoring, capsys, status, negq):
    _, _, state = scoring
    state.update(status=status, negq=negq)
    score.BASELINE_FILE.write_text("unchanged", encoding="utf-8")
    assert score.main(["--write-baseline", "--aggregate-only"]) == 2
    assert score.BASELINE_FILE.read_text(encoding="utf-8") == "unchanged"
    assert "not_evaluated=" in capsys.readouterr().err


def test_current_pack_reads_existing_ledger(scoring):
    import ledger
    ledger.init_db(paths.ledger_db())
    row = ledger.current_pack()
    assert score.main(["--pack", "current", "--aggregate-only"]) == 0
    assert scoring[1][0][1]["pack_version"] == row.version
    assert [rule.rule_id for rule in scoring[1][0][1]["pack"].active] == row.active


@pytest.mark.parametrize("args", [["--pack", "current"],
    ["--pack", "current", "--standin-every", "2"], ["--pack", "current", "--purpose", "oracle"]])
def test_missing_current_and_wrong_pack(scoring, args):
    assert score.main(args) == 2
    assert not paths.ledger_db().exists()


def test_table_and_oracle_report_shape(scoring, tmp_path):
    target = tmp_path / "report.json"
    assert score.main(["--out", str(target)]) == 0
    report = OracleReport.model_validate_json(target.read_text(encoding="utf-8"))
    assert report.transitions == {} and report.pack_v2 is None
    assert len(list(paths.LOCAL_DIR.glob("score_*.json"))) == 1


def test_llm_dispatch_uses_explicit_pack_and_cache_override(scoring, tmp_path):
    from core.config import settings
    cache = tmp_path / "cache override"
    assert score.main(["--mode", "llm", "--cache-dir", str(cache), "--aggregate-only"]) == 0
    assert settings.LLM_CACHE_DIR == str(cache.resolve())
    assert scoring[1][0][1]["purpose"] == "shadow"
    assert scoring[1][0][1]["pack_version"] == 1
    assert not paths.ledger_db().exists()


def test_v1_excludes_unapproved_quarantined_and_t5(monkeypatch):
    from rules import registry
    seen = []
    def info(id, approved, status="active", origin="law_compiled"):
        return SimpleNamespace(rule_id=id, meta=SimpleNamespace(approved_by=approved, status=status, origin=origin))
    monkeypatch.setattr(registry, "all_rules", lambda: [info("a", "owner"), info("b", None),
        info("c", "owner", "quarantined"), info("d", "owner", origin="t5")])
    monkeypatch.setattr(registry, "load_pack", lambda ids, **kwargs: seen.extend(ids))
    score.build_pack("v1")
    assert seen == ["a"]


def test_real_runner_offline_no_ledger(tmp_path, monkeypatch, capsys):
    from pipeline import llm
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "empty cache")
    monkeypatch.setattr(score, "config_line", lambda: "cfg=fixture")
    monkeypatch.setattr(llm, "_backend", lambda *args, **kwargs: pytest.fail("network transport called"))
    import ledger
    monkeypatch.setattr(ledger, "current_pack", lambda: pytest.fail("ledger opened"))
    assert score.main(["--standin-every", "89", "--aggregate-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["not_evaluated"] == 1 and result["n_cases"] == 1
    assert not paths.ledger_db().exists()
