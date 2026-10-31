"""Dossier boundaries, prompt isolation and S0 collaborator contracts."""

import builtins
from collections import Counter
from datetime import date
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from core.enums import AxisGroup
from core.models import Context as CoreContext, DossierView, SessionFlag
from core.paths import EVIDENCE_DIR, KERA_DIR, LAW_DIR
from pipeline import context


class EmptyPack:
    def check_session(self, dossier, as_of):
        return []


EMPTY_PACK = EmptyPack()
GROUPS = list(AxisGroup)


def install_module(monkeypatch, name, **api):
    module = ModuleType(name)
    module.__dict__.update(api)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@pytest.fixture
def guardrail(monkeypatch):
    return install_module(monkeypatch, "pipeline.guardrail",
                          is_effective=lambda anchor, as_of: False,
                          finalize_flag=lambda flag, ctx: flag)


@pytest.mark.parametrize(("as_of", "state"), [
    ("0001-01-01", "CHƯA_CÓ_HỒ_SƠ"),
    ("2024-12-12", "CHƯA_CÓ_HỒ_SƠ"),
    ("2024-12-16", "CHƯA_CÓ_HỒ_SƠ"),
    ("2024-12-17", "ĐÃ_KÝ_CHƯA_NỘP"),
    ("2024-12-20", "ĐÃ_KÝ_CHƯA_NỘP"),
    ("2024-12-22", "ĐÃ_KÝ_CHƯA_NỘP"),
    ("2024-12-23", "HIỆU_LỰC"),
    ("2025-03-23", "HIỆU_LỰC"),
    ("2025-03-24", "ĐÃ_THU_HỒI"),
    ("2025-03-25", "ĐÃ_THU_HỒI"),
    ("9999-12-31", "ĐÃ_THU_HỒI"),
])
def test_kera_state_boundaries(guardrail, as_of, state):
    ctx = context.build_context("kera", date.fromisoformat(as_of), pack=EMPTY_PACK)
    assert ctx.dossier_state == state
    assert ctx.as_of == date.fromisoformat(as_of)
    if state != "HIỆU_LỰC":
        assert ctx.P == ctx.null_fields == ctx.reference_only == []
    else:
        assert len(ctx.P) == 25


def test_kera_permitted_content_and_stable_ids(guardrail):
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    assert isinstance(ctx, CoreContext)
    assert ctx.category_norm == "thuc_pham_bo_sung"
    assert Counter(item.axis_group for item in ctx.P) == {"ho_so": 3, "thanh_phan": 22}
    assert [item.permitted_id for item in ctx.P] == (
        [f"kera:ho_so:{n}" for n in range(1, 4)]
        + [f"kera:thanh_phan:{n}" for n in range(1, 23)])
    texts = {item.text for item in ctx.P}
    assert {"22 nguyên liệu, trong đó 10 loại bột rau củ quả chiếm 28,13%",
            "30 viên | 96 g (3,2 g x 30 viên)", "Vitamin A, B6, B12",
            "Năng lượng (Energy): 10 kcal / 3,2 g (1 viên)"} <= texts
    assert all("xơ" not in text.casefold() for text in texts)
    assert set(ctx.null_fields) == {"doi_tuong", "lieu_dung", "cong_dung"}
    assert ctx.P[0].source_field == "product.name_on_label.value"
    assert ctx.P[4].source_field == "ingredients_declared.ten_vegetable_powders.value[0]"
    assert ctx.P[-6].source_field == "nutrition_declared.rows[0]"
    assert ctx.P[-1].text == "Natri (Sodium): 6,43 mg / 3,2 g (1 viên)"


@pytest.mark.parametrize("sku", sorted(path.stem for path in EVIDENCE_DIR.glob("*.json")
                                       if path.stem != "kera"))
def test_evidence_groups_are_restricted(guardrail, sku):
    ctx = context.build_context(sku, "2025-01-08", pack=EMPTY_PACK)
    assert ctx.dossier_state == "PRESENT"
    assert ctx.null_fields == []
    assert ctx.prompt_view(["doi_tuong", "ho_so", "xuat_xu"]) == []
    assert all(item.confidence != "low" for item in ctx.P)


def test_dk_betics_gold(guardrail):
    ctx = context.build_context("dk_betics_gold", "2025-01-08", pack=EMPTY_PACK)
    assert ctx.category_norm == "TPBVSK"
    assert Counter(item.axis_group for item in ctx.P) == {"cong_dung": 2, "thanh_phan": 1}
    assert ctx.reference_only == []


def test_non_alco_low_items_are_display_only(guardrail):
    ctx = context.build_context("non_alco", "2025-01-08", pack=EMPTY_PACK)
    assert ctx.category_norm == "TPBVSK"
    assert len(ctx.P) == 1
    assert ctx.P[0].axis_group == "cong_dung"
    assert ctx.P[0].confidence == "medium"
    assert Counter(item["axis_group"] for item in ctx.reference_only) == {
        "cong_dung": 1, "thanh_phan": 1, "lieu_dung": 1}
    assert all(item["confidence"] == "low" for item in ctx.reference_only)
    prompt = json.dumps(ctx.prompt_view(GROUPS), ensure_ascii=False)
    assert all(item["text"] not in prompt for item in ctx.reference_only)
    assert ctx.prompt_view(["thanh_phan", "lieu_dung"]) == []


@pytest.mark.parametrize("sku", ["love_world_coffee", "seloderma_psc300_plus"])
def test_empty_evidence_dossiers(guardrail, sku):
    assert context.build_context(sku, "2025-01-08", pack=EMPTY_PACK).P == []


def test_prompt_view_is_an_ordered_projection(guardrail):
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    assert ctx.prompt_view([]) == []
    assert ctx.prompt_view(["ho_so", "ho_so"]) == [
        {"permitted_id": item.permitted_id, "text": item.text} for item in ctx.P[:3]]
    assert ctx.prompt_view(reversed(GROUPS)) == [
        {"permitted_id": item.permitted_id, "text": item.text} for item in ctx.P]
    projected = ctx.prompt_view(GROUPS)
    projected[0]["text"] = "changed outside context"
    assert ctx.P[0].text != projected[0]["text"]


def test_kera_forbidden_fields_are_absent(guardrail):
    source = json.loads((KERA_DIR / "dossier.json").read_text(encoding="utf-8"))
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    payload = json.dumps(ctx.prompt_view(GROUPS), ensure_ascii=False)
    assert source["label_claims"]["slogan"]["value"] not in payload
    regulator = source["nutrition_declared"]["regulator_statement_on_fiber"]
    assert regulator["text"] not in payload
    assert all(set(item) == {"permitted_id", "text"} for item in ctx.prompt_view(GROUPS))
    assert ctx.reference_only == []


def test_kera_allow_list_ignores_unrelated_source_sections(monkeypatch, guardrail):
    before = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    source = json.loads((KERA_DIR / "dossier.json").read_text(encoding="utf-8"))
    for parent, fields in (
        (source, ("self_declaration", "responsible_parties", "related_regulatory_events",
                  "sales_window", "gaps")),
        (source["label_claims"], ("slogan", "front_icons", "certification_logos", "box_bullets",
                                   "mandatory_label_defects_found_by_regulator")),
        (source["ingredients_declared"], ("not_declared_but_present", "actual_vs_declared")),
        (source["ingredients_declared"]["ten_vegetable_powders"], ("per_item_percent_on_label",)),
        (source["nutrition_declared"], ("fiber_row", "regulator_statement_on_fiber")),
    ):
        for field in fields:
            parent[field] = "FORBIDDEN_CONTENT"

    def poison_metadata(value):
        if isinstance(value, dict):
            for field in value:
                if field in {"note", "sources", "method", "excerpt"}:
                    value[field] = "FORBIDDEN_CONTENT"
                else:
                    poison_metadata(value[field])
        elif isinstance(value, list):
            for item in value:
                poison_metadata(item)

    poison_metadata(source)
    source["validity"]["note"] = "FORBIDDEN_CONTENT"
    original = context._read_json
    monkeypatch.setattr(context, "_read_json", lambda path:
                        source if path == KERA_DIR / "dossier.json" else original(path))
    after = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    assert after.model_dump() == before.model_dump()
    assert "FORBIDDEN_CONTENT" not in after.model_dump_json()


def test_hash_uses_canonical_permitted_pairs_only(guardrail):
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    later = context.build_context("kera", "2025-02-01", pack=EMPTY_PACK)
    canonical = json.dumps([(item.permitted_id, item.text) for item in ctx.P],
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert ctx.dossier_hash == sha256(canonical.encode("utf-8")).hexdigest()
    assert later.dossier_hash == ctx.dossier_hash


def test_evidence_allow_list_usage_names_recall_and_reference_isolation(monkeypatch, guardrail):
    evidence = {
        "category": "unexpected", "ad_confirmation": {"status": "absent"},
        "recalls": [{"date": "2025-05-01"}, {"date": "2025-03-01"}],
        "permitted_claims": [{"text": "permitted", "confidence": "high", "note": "LEAK"}],
        "ingredients_declared": [{"text": "reference", "confidence": "low", "source_detail": "LEAK"}],
        "usage_declared": [{"text": "one daily", "confidence": "medium"}],
        "usage": [{"text": "with water", "confidence": "high"}],
        "gaps": ["LEAK"], "regulator_warnings": ["LEAK"], "note": "LEAK",
        "source_detail": "LEAK", "label_claims_not_in_declaration": ["LEAK"],
        "origin_declared": {"text": "LEAK", "confidence": "high"},
    }
    original = context._read_json
    monkeypatch.setattr(context, "_read_json", lambda path:
                        evidence if path == EVIDENCE_DIR / "non_alco.json" else original(path))
    seen = []
    pack = SimpleNamespace(check_session=lambda dossier, day: seen.append((dossier, day)) or [])
    ctx = context.build_context("non_alco", "2025-01-08", pack=pack)
    assert ctx.category_norm == "unknown"
    assert [item.text for item in ctx.P] == ["permitted", "one daily", "with water"]
    assert [item.permitted_id for item in ctx.P] == [
        "non_alco:cong_dung:1", "non_alco:lieu_dung:1", "non_alco:lieu_dung:2"]
    assert ctx.P[-1].source_field == "usage[0].text"
    assert "LEAK" not in ctx.model_dump_json()
    dossier, day = seen[0]
    assert isinstance(dossier, DossierView)
    assert dossier.ad_confirmation_status == "absent"
    assert dossier.declaration_date is None
    assert dossier.recall_date == date(2025, 3, 1)
    assert day == date(2025, 1, 8)


@pytest.mark.parametrize("sku", [None, "slimming_care_x3", "../demo/kera/dossier",
                                "..\\demo\\kera\\dossier", "C:/outside/product"])
def test_unknown_sku_never_becomes_a_source_path(monkeypatch, guardrail, sku):
    opened = []
    original = context._read_json

    def read(path):
        opened.append(path)
        assert path == LAW_DIR / "index.json"
        return original(path)

    monkeypatch.setattr(context, "_read_json", read)
    ctx = context.build_context(sku, "2025-01-08", pack=EMPTY_PACK)
    assert ctx.sku_id == sku
    assert ctx.dossier_state == "NONE"
    assert ctx.category_norm == "unknown"
    assert ctx.P == ctx.reference_only == ctx.null_fields == []
    assert opened == [LAW_DIR / "index.json"]


def test_missing_guardrail_does_not_open_any_law_file(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.guardrail", None)
    original = Path.open
    opened = []

    def open_source(path, *args, **kwargs):
        opened.append(path)
        assert not path.is_relative_to(LAW_DIR)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_source)
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    assert ctx.law_set == ctx.session_flags == []
    assert opened


def test_law_set_delegates_every_anchor_and_date(monkeypatch, guardrail):
    index = json.loads((LAW_DIR / "index.json").read_text(encoding="utf-8"))
    accepted = {index[0]["id"], index[-1]["id"]}
    seen = []

    def effective(anchor, day):
        seen.append((anchor, day))
        return anchor in accepted

    monkeypatch.setattr(guardrail, "is_effective", effective)
    original = Path.open

    def open_source(path, *args, **kwargs):
        if path.is_relative_to(LAW_DIR):
            assert path == LAW_DIR / "index.json"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_source)
    ctx = context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)
    assert ctx.law_set == [row["id"] for row in index if row["id"] in accepted]
    assert seen == [(row["id"], date(2025, 1, 8)) for row in index]


def test_explicit_pack_never_imports_ledger_or_registry(monkeypatch, guardrail):
    original = builtins.__import__

    def restricted(name, *args, **kwargs):
        assert name not in {"ledger", "rules.registry"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", restricted)
    original_dynamic = context.import_module

    def dynamic(name):
        assert name not in {"ledger", "rules.registry"}
        return original_dynamic(name)

    monkeypatch.setattr(context, "import_module", dynamic)
    flag = SessionFlag(flag_code="NO_AD_CONFIRMATION", rule_id="R06", message="Confirmation absent")
    seen = []

    def check(dossier, day):
        assert dossier.ad_confirmation_status == "absent"
        return [flag]

    def finalize(value, ctx):
        assert value is flag
        seen.append(ctx)
        return value

    monkeypatch.setattr(guardrail, "finalize_flag", finalize)
    ctx = context.build_context("non_alco", "2025-01-08",
                                pack=SimpleNamespace(check_session=check))
    assert seen == [ctx]
    assert ctx.session_flags == [flag]


def test_live_pack_uses_ledger_row_and_finalizes_each_flag(monkeypatch, guardrail):
    row = SimpleNamespace(active=["L1_PREDECL", "R06"], quarantined=["R09"])
    calls = []
    flag = SessionFlag(flag_code="NO_SELF_DECLARATION_AT_AS_OF", rule_id="L1_PREDECL",
                       message="Declaration absent at broadcast date")

    def current_pack():
        calls.append("current_pack")
        return row

    def check(dossier, day):
        assert dossier.model_dump() == {
            "sku_id": "kera", "category_norm": "thuc_pham_bo_sung",
            "ad_confirmation_status": "absent", "declaration_date": date(2024, 12, 23),
            "recall_date": date(2025, 3, 24)}
        assert day == date(2024, 12, 12)
        return [flag, flag]

    def load_pack(active, *, quarantined, mode):
        calls.append((active, quarantined, mode))
        return SimpleNamespace(check_session=check)

    finalized = []

    def finalize(value, ctx):
        finalized.append(value)
        assert ctx.dossier_state == "CHƯA_CÓ_HỒ_SƠ"
        return value if len(finalized) == 1 else None

    install_module(monkeypatch, "ledger", current_pack=current_pack)
    install_module(monkeypatch, "rules.registry", load_pack=load_pack)
    monkeypatch.setattr(guardrail, "finalize_flag", finalize)
    ctx = context.build_context("kera", "2024-12-12")
    assert calls == ["current_pack", (row.active, row.quarantined, "live")]
    assert finalized == [flag, flag]
    assert ctx.session_flags == [flag]


@pytest.mark.parametrize("missing", ["ledger", "rules.registry"])
def test_missing_live_collaborator_is_safe(monkeypatch, guardrail, missing):
    def unexpected(*args, **kwargs):
        pytest.fail("No live pack may be loaded with a missing collaborator")

    install_module(monkeypatch, "ledger", current_pack=unexpected)
    install_module(monkeypatch, "rules.registry", load_pack=unexpected)
    monkeypatch.setitem(sys.modules, missing, None)
    assert context.build_context("kera", "2024-12-12").session_flags == []


@pytest.mark.parametrize("missing_parent", ["pipeline", "rules"])
def test_missing_collaborator_parent_is_safe(monkeypatch, guardrail, missing_parent):
    original = context.import_module

    def load(name):
        if name.startswith(missing_parent + "."):
            raise ModuleNotFoundError("Missing parent", name=missing_parent)
        return original(name)

    monkeypatch.setattr(context, "import_module", load)
    assert context.build_context("kera", "2024-12-12").session_flags == []


def test_collaborator_import_bug_is_not_treated_as_missing(monkeypatch):
    def broken(name):
        raise ModuleNotFoundError("Broken dependency", name="broken_dependency")

    monkeypatch.setattr(context, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="Broken dependency"):
        context.build_context("kera", "2025-01-08", pack=EMPTY_PACK)


def test_present_empty_collaborator_is_not_treated_as_missing(monkeypatch, guardrail):
    install_module(monkeypatch, "rules.registry", load_pack=lambda *a, **kw: EMPTY_PACK)
    install_module(monkeypatch, "ledger")
    with pytest.raises(AttributeError, match="current_pack"):
        context.build_context("kera", "2025-01-08")


def test_collaborator_runtime_error_is_not_swallowed(guardrail):
    def broken(dossier, day):
        raise ModuleNotFoundError("Runtime failure", name="rules.registry")

    with pytest.raises(ModuleNotFoundError, match="Runtime failure"):
        context.build_context("kera", "2025-01-08", pack=SimpleNamespace(check_session=broken))


@pytest.mark.parametrize("method", ["is_effective", "finalize_flag"])
def test_guardrail_runtime_error_is_not_swallowed(monkeypatch, guardrail, method):
    def broken(*args):
        raise ModuleNotFoundError("Runtime failure", name="pipeline.guardrail")

    monkeypatch.setattr(guardrail, method, broken)
    flag = SessionFlag(flag_code="NO_AD_CONFIRMATION", rule_id="R06", message="Confirmation absent")
    pack = SimpleNamespace(check_session=lambda dossier, day: [flag])
    with pytest.raises(ModuleNotFoundError, match="Runtime failure"):
        context.build_context("non_alco", "2025-01-08", pack=pack)


@pytest.mark.parametrize(("text", "sku"), [
    ("Try DK-BETICS GOLD!", "dk_betics_gold"),
    ("kẹo KERA", "kera"), ("SUPER GREENS GUMMIES", "kera"),
    ("SUPERGREENS GUMMIES", "kera"),
    ("SELODERMA PSC 300 PLUS", "seloderma_psc300_plus"),
    ("Thực phẩm bổ sung Love World Nutrition Coffee", "love_world_coffee"),
    ("NON ALCO - ENZYME GIẢI RƯỢU", "non_alco"),
    ("Cao Việt Hoàng", "cao_viet_hoang"),
    ("keratin", None), ("xkera", None), ("kera2", None),
    ("DK-BETICS GOLDS", None), ("cao viet hoang", None),
    ("unknown product", None), ("", None),
    ("Kera and DK-Betics Gold", None),
])
def test_resolve_sku_closed_aliases_and_boundaries(text, sku):
    assert context.resolve_sku(text) == sku


def test_resolve_sku_reads_only_source_names(monkeypatch):
    evidence = {"product_name": "known product", "trade_names_seen": ["known alias"],
                "gaps": ["hidden alias"], "regulator_warnings": ["hidden alias"]}
    monkeypatch.setattr(context, "_evidence_files", lambda: {"example": Path("example.json")})
    monkeypatch.setattr(context, "_read_json", lambda path: evidence)
    assert context.resolve_sku("known alias") == "example"
    assert context.resolve_sku("hidden alias") is None
    assert context.resolve_sku("known aliaz") is None


def real_module(name):
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name or (exc.name and name.startswith(exc.name + ".")):
            pytest.skip(f"not ready: {name}")
        raise


@pytest.mark.parametrize(("sku", "as_of", "expected"), [
    ("kera", "2024-12-12", {"NO_SELF_DECLARATION_AT_AS_OF"}),
    ("non_alco", "2025-01-08", {"NO_AD_CONFIRMATION"}),
    ("dk_betics_gold", "2025-01-08", set()),
    ("kera", "2025-01-08", set()),
    ("kera", "2025-03-25", {"RECALLED_AT_AS_OF"}),
])
def test_real_session_rules(sku, as_of, expected):
    registry = real_module("rules.registry")
    real_module("pipeline.guardrail")
    rule_ids = [info.rule_id for info in registry.all_rules() if info.meta.origin == "law_compiled"]
    pack = registry.load_pack(rule_ids, mode="measure")
    ctx = context.build_context(sku, as_of, pack=pack)
    assert {flag.flag_code for flag in ctx.session_flags} == expected
