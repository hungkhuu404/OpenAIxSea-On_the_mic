"""Synthetic oracle transport tests; no sealed cases or network calls."""

import hashlib
import json
import subprocess
import sys
import unicodedata
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from core.models import RuleHit
from gate import score
from pipeline import llm
from pipeline.drafts import VerdictDraft


def snapshot_local():
    return {path.relative_to(paths.LOCAL_DIR).as_posix(): path.read_bytes()
            for path in paths.LOCAL_DIR.rglob("*") if path.is_file()}


def status():
    return subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=paths.REPO_ROOT,
                          capture_output=True, check=True).stdout


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_real_runner_cache_and_oracle_logs_are_external(tmp_path, monkeypatch, capsys, existing_ledger):
    import ledger
    cases = [dict(case_id=f"synthetic-{index}", sku_id="dk_betics_gold", text=text, label="yellow")
             for index, text in enumerate(("Tuyên bố thử nghiệm alpha.", "Tuyên bố thử nghiệm beta.",
                                          "không cần dùng thêm thứ gì để chữa bệnh"), 1)]
    source = tmp_path / "synthetic.jsonl"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases), encoding="utf-8")
    canonical = "".join(unicodedata.normalize("NFC", json.dumps(row, sort_keys=True,
                        separators=(",", ":"), ensure_ascii=False)) + "\n" for row in cases)
    freeze = tmp_path / "freeze.json"
    freeze.write_text(json.dumps({"files": {"synthetic.jsonl": {
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "n": 3}}}), encoding="utf-8")
    monkeypatch.setattr(score, "FREEZE_FILE", freeze)
    monkeypatch.setattr(score, "config_line", lambda: "cfg=fixture")
    set_override("LLM_BACKEND", "api")
    set_override("OFFLINE", 0)

    def check(view):
        return [RuleHit(rule_id="R04", family="R04", matched="chữa bệnh", law_anchor="LDUOC-2016/D6/K15",
                        law_span="chữa bệnh", precedence=4, ambiguous_negation=True)] if "không" in view.text else []

    pack = SimpleNamespace(check_atom=check, check_session=lambda *args: [], quarantined_hits=lambda *args: [],
                           mode="live", active=(), quarantined=())
    monkeypatch.setattr(score, "build_pack", lambda choice: (pack, 1, "v1"))

    def judge(atom, items, ctx, purpose, cache_read=True):
        # T6 is an explicit collaborator fake; all four call kinds still use real llm.call/cache/logging.
        for kind in ("t1", "t2"):
            result = llm.call(kind=kind, purpose=purpose, model="fixture", effort="low",
                              schema_name="test_response", cache_read=cache_read,
                              messages=[dict(role="user", content=json.dumps(dict(kind=kind, span=atom.span)))])
            assert result.error is None
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
    for function in ("current_pack", "init_db", "record_llm_call", "append_event"):
        monkeypatch.setattr(ledger, function, lambda *args, **kwargs: pytest.fail("oracle touched ledger"))
    before_status, before_local = status(), snapshot_local()
    database = paths.ledger_db()
    if existing_ledger:
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"synthetic ledger must not be opened")
    before_database = database.read_bytes() if database.exists() else None
    cache = tmp_path / "external cache"
    output = tmp_path / "aggregate.json"
    args = ["--mode", "llm", "--purpose", "oracle", "--cases", str(source),
            "--cache-dir", str(cache), "--out", str(output)]
    assert score.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["not_evaluated"] == summary["negq_miss"] == 0
    assert "synthetic-" not in json.dumps(summary)
    assert set(kinds) == {"s1", "t1", "t2", "negq"}
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in cache.glob("*/*.json")]
    assert {row["kind"] for row in rows} == {"s1", "t1", "t2", "negq"}
    assert all(row["purpose"] == "oracle" for row in rows)
    assert (cache / "llm_calls.jsonl").is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["transitions"] == {}
    monkeypatch.setattr(llm, "_api", lambda *args: pytest.fail("replay called transport"))
    args[1] = "replay"
    assert score.main(args) == 0
    assert status() == before_status and snapshot_local() == before_local
    assert (database.read_bytes() if database.exists() else None) == before_database


@pytest.mark.parametrize("extra", [[], ["--cache-dir", ".local/invalid"],
    ["--cache-dir", "/tmp/synthetic-cache", "--out", ".local/invalid-report.json"]])
def test_oracle_rejects_unsafe_paths_without_running(extra, monkeypatch):
    set_override("LLM_BACKEND", "api")
    monkeypatch.setattr(score, "build_pack", lambda *args: pytest.fail("invalid oracle started"))
    assert score.main(["--purpose", "oracle", *extra]) == 2


def test_oracle_refuses_codex_before_reading_any_cases(monkeypatch):
    set_override("LLM_BACKEND", "codex")
    monkeypatch.setattr(score, "build_pack", lambda *args: pytest.fail("codex oracle started"))
    assert score.main(["--purpose", "oracle", "--cache-dir", "/tmp/synthetic-cache"]) == 2


def test_frozen_manifest_detects_changed_synthetic_case(tmp_path, monkeypatch):
    freeze = tmp_path / "manifest.json"
    freeze.write_text(json.dumps({"files": {"synthetic": {"sha256": "0" * 64, "n": 1}}}), encoding="utf-8")
    monkeypatch.setattr(score, "FREEZE_FILE", freeze)
    with pytest.raises(ValueError, match="frozen manifest"):
        score._verify_freeze([dict(case_id="synthetic")])
