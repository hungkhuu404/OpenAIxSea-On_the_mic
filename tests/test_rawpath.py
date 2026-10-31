"""Real code-only routing and isolated typed-session scheduling."""

import builtins
from concurrent.futures import Future
from datetime import date
import json
from pathlib import Path
import re
import sys
from time import perf_counter
from types import ModuleType
import unicodedata

import pytest

from core.config import set_override, settings
from pipeline import rawpath


DAY = date(2026, 10, 31)
RED = "uống một tháng là chữa khỏi hẳn tiểu đường"
GREEN = "Giúp giảm nguy cơ biến chứng do tiểu đường"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    from pipeline import llm, router

    def forbidden(*args, **kwargs):
        pytest.fail("The raw path must not call extraction, judging or negq")

    for name in ("call", "acall", "map_calls"):
        monkeypatch.setattr(llm, name, forbidden)
    monkeypatch.setattr(router, "_negq", forbidden)
    judge = ModuleType("pipeline.judge")
    judge.judge = forbidden
    monkeypatch.setitem(sys.modules, "pipeline.judge", judge)


@pytest.fixture
def database():
    import ledger

    ledger.init_db(settings.LEDGER_DB)
    return ledger


@pytest.fixture
def scheduled(monkeypatch):
    calls = []
    events = ModuleType("pipeline.dossier_events")

    def auto_run(session_id):
        calls.append(session_id)
        # Scheduling may return unfinished work; preflight must never await it.
        return Future()

    events.auto_run = auto_run
    monkeypatch.setitem(sys.modules, "pipeline.dossier_events", events)
    return calls


def _missing(monkeypatch, module, missing_name=None):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == module:
            raise ModuleNotFoundError("Simulated missing dependency", name=missing_name or module)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)


def test_r04_uses_real_rules_and_law_under_100_ms_after_warmup(database):
    rawpath.check_sentence("dk_betics_gold", DAY, RED)
    started = perf_counter()
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, RED)
    elapsed_ms = (perf_counter() - started) * 1000
    print(f"Warmed raw R04 check: {elapsed_ms:.3f} ms")
    assert elapsed_ms < 100
    assert verdict.verdict == "red" and verdict.decided_by == "rule:R04"
    assert "R04" in verdict.fired_rules
    assert verdict.rule_pack_version == database.current_pack().version
    anchors = json.loads((ROOT / "data/law/index.json").read_text(encoding="utf-8"))
    law = next(row["text"] for row in anchors if row["id"] == "LDUOC-2016/D6/K15")
    assert verdict.law_span and verdict.law_span in law
    assert verdict.llm_call_ids == []
    assert database.list_sessions() == []


def test_declared_function_is_green_via_shared_allow_list(database):
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, GREEN)
    assert verdict.verdict == "green" and verdict.decided_by == "allow_list"
    assert verdict.permitted_id and verdict.evidence_span
    assert verdict.legal_basis == ["ND15-2018/D27/K6"]


@pytest.mark.parametrize("text", [
    "Thực phẩm này không phải là thuốc và không có tác dụng thay thế thuốc chữa bệnh",
    "Sản phẩm không chữa bệnh.",
])
def test_disclaimer_and_clear_negation_do_not_produce_red(database, text):
    assert rawpath.check_sentence("dk_betics_gold", DAY, text).verdict != "red"


def test_unrelated_claim_needs_full_path_or_human(database):
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, "Sản phẩm có hộp màu tím.")
    assert (verdict.verdict, verdict.reason_code, verdict.decided_by) == (
        "abstain", "LLM_OFF", "abstain:LLM_OFF")
    assert verdict.llm_call_ids == []


def test_ambiguous_negation_abstains_without_asking_negq(database):
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, "chữa bệnh không rõ")
    assert verdict.verdict == "abstain" and verdict.reason_code == "RULE_AMBIGUOUS"
    assert verdict.llm_call_ids == []


@pytest.mark.parametrize("offline", [0, 1])
def test_public_checks_never_call_models_even_with_live_backend(database, offline):
    set_override("LLM_BACKEND", "codex")
    set_override("OFFLINE", offline)
    set_override("PUBLIC_MODE", 1)
    assert rawpath.check_sentence("dk_betics_gold", DAY, RED).verdict == "red"
    assert rawpath.check_sentence("dk_betics_gold", DAY, "Hộp màu tím.").reason_code == "LLM_OFF"


@pytest.mark.parametrize("sku,when,reason", [
    ("not-a-known-product", DAY, "SKU_UNRESOLVED"),
    ("kera", date(2024, 12, 12), "DOSSIER_EMPTY"),
])
def test_shared_preconditions_are_retained(database, sku, when, reason):
    verdict = rawpath.check_sentence(sku, when, "Hộp màu tím.")
    assert verdict.verdict == "abstain" and verdict.reason_code == reason


def test_live_quarantine_and_pack_version_are_honoured(database):
    row = database.current_pack()
    newer = database.create_pack_version([rule for rule in row.active if rule != "R04"],
                                         ["R04"], "quarantine", rule_id="R04")
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, RED)
    assert verdict.verdict == "abstain" and verdict.reason_code == "RULE_QUARANTINED"
    assert verdict.flags == ["PREV_RED"]
    assert verdict.rule_pack_version == newer.version


def test_atom_shape_nfc_identity_near_hits_and_shared_steps(database, monkeypatch):
    from pipeline import router
    from pipeline.ingest import from_text

    seen, calls = [], []
    original_inputs = router.code_inputs

    def inputs(atom, ctx, turn_text):
        result = original_inputs(atom, ctx, turn_text)
        seen.append(atom)
        return result

    monkeypatch.setattr(router, "code_inputs", inputs)
    for name in ("step_a", "step_b", "step_d"):
        original = getattr(router, name)

        def step(*args, _name=name, _original=original):
            calls.append(_name)
            return _original(*args)

        monkeypatch.setattr(router, name, step)
    text = "Sản phẩm phòng thêm bệnh."
    decomposed = unicodedata.normalize("NFD", text)
    verdict = rawpath.check_sentence("dk_betics_gold", str(DAY), decomposed)
    expected = from_text("dk_betics_gold", DAY, text, "Synthetic source")
    assert verdict.atom_key == f"{expected.session_id}:t01.a1"
    atom, = seen
    assert atom.span == text and atom.start == 0 and atom.end == len(text)
    assert atom.axis == "cong_dung" and atom.speaker_role == "khong_ro" and atom.subject_is_product
    assert atom.near_hits and all(hit.span in atom.span for hit in atom.near_hits)
    assert calls == ["step_a", "step_b", "step_d"]
    assert verdict.reason_code == "LLM_OFF"


def test_standalone_check_bootstraps_only_an_empty_approved_ledger():
    import ledger

    assert not Path(settings.LEDGER_DB).exists()
    verdict = rawpath.check_sentence("dk_betics_gold", DAY, RED)
    assert verdict.verdict == "red" and verdict.rule_pack_version == 1
    assert ledger.list_sessions() == []


def test_full_preflight_ingests_once_and_schedules_without_waiting(database, scheduled):
    first = rawpath.preflight("dk_betics_gold", DAY, RED)
    second = rawpath.preflight("dk_betics_gold", str(DAY), RED, full=True)
    stored, = database.list_sessions()
    assert stored.session_id == first.atom_key.split(":")[0] == second.atom_key.split(":")[0]
    assert scheduled == [stored.session_id, stored.session_id]
    assert stored.turns[0].text == RED
    assert re.fullmatch(r"Constructed sentence, entered by the team at \d{2}:\d{2}", stored.source)
    assert stored.verdicts == []


@pytest.mark.parametrize("public,full", [(0, False), (1, True), (1, False)])
def test_raw_only_preflight_has_no_ingestion_or_scheduling(database, scheduled, public, full):
    set_override("PUBLIC_MODE", public)
    verdict = rawpath.preflight("dk_betics_gold", DAY, RED, full=full)
    assert verdict.verdict == "red"
    assert database.list_sessions() == [] and scheduled == []


@pytest.mark.parametrize("public,full", [(0, False), (1, True)])
def test_raw_only_preflight_does_not_require_auto_run(database, monkeypatch, public, full):
    _missing(monkeypatch, "pipeline.dossier_events")
    set_override("PUBLIC_MODE", public)
    assert rawpath.preflight("dk_betics_gold", DAY, RED, full=full).verdict == "red"
    assert database.list_sessions() == []


def test_missing_auto_run_is_reported_before_ingesting(database, monkeypatch):
    _missing(monkeypatch, "pipeline.dossier_events")
    with pytest.raises(ModuleNotFoundError) as caught:
        rawpath.preflight("dk_betics_gold", DAY, RED, full=True)
    assert caught.value.name == "pipeline.dossier_events"
    assert database.list_sessions() == []


def test_transitive_dependency_error_is_not_hidden(database, monkeypatch):
    _missing(monkeypatch, "pipeline.context", "dependency_inside_context")
    with pytest.raises(ModuleNotFoundError) as caught:
        rawpath.check_sentence("dk_betics_gold", DAY, RED)
    assert caught.value.name == "dependency_inside_context"
