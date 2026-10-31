"""Step F tests use local sources and replace unavailable collaborators explicitly."""

import builtins
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unicodedata

from pydantic import BaseModel
import pytest

from core.models import Context, SessionFlag, Verdict
from pipeline import guardrail


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def context():
    return Context.model_validate(json.loads(
        (ROOT / "fixtures" / "context_fake.json").read_text(encoding="utf-8")))


def _draft(colour="yellow", family="R05", **updates):
    result = {"atom_key": "synthetic:t01.a1", "verdict": colour,
              "decided_by": "llm_votes", "family": family, "fired_rules": [],
              "closest_permitted_id": "demo_sku:cong_dung:1", "evidence_span": None}
    if colour == "green":
        result.update(permitted_id="demo_sku:cong_dung:1", closest_permitted_id=None,
                      decided_by="allow_list")
    elif colour == "red":
        result.update(decided_by=f"rule:{family}", fired_rules=[family], closest_permitted_id=None)
    result.update(updates)
    return result


def _ctx(context, as_of="2025-01-08", category="thuc_pham_bo_sung"):
    return Context.model_validate({**context.model_dump(), "as_of": as_of, "category_norm": category})


def _fake_registry(monkeypatch, rule_id="R04", family="R04", span="chữa bệnh", origin="law_compiled"):
    registry = ModuleType("rules.registry")
    meta = SimpleNamespace(law_span=span, family=family, origin=origin)
    registry.all_rules = lambda: [SimpleNamespace(rule_id=rule_id, meta=meta)]
    monkeypatch.setitem(sys.modules, "rules.registry", registry)


def _missing(monkeypatch, module, missing_name=None):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == module:
            raise ModuleNotFoundError("Simulated missing dependency", name=missing_name or module)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)


def _synthetic_basis(monkeypatch, tmp_path, rows):
    path = tmp_path / "basis.json"
    path.write_text(json.dumps({"rows": rows, "law_span_by_family_category": {}}, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setattr(guardrail, "LAW_BASIS_FILE", path)


def _row(family, anchor, **updates):
    result = dict(family=family, category="any", anchor=anchor, role="primary",
                  valid_from=None, valid_to=None)
    result.update(updates)
    return result


def test_display_text_all_used_anchors_hide_fines():
    basis = json.loads((ROOT / "rules" / "law_basis.json").read_text(encoding="utf-8"))
    anchors = {row["anchor"] for row in basis["rows"]} | {"ND87-2026/D71/K4"}
    for anchor in anchors:
        text = guardrail.display_text(anchor)
        assert not re.search(r"\d[\d.,\s]*đồng", text, re.IGNORECASE), anchor
        assert not re.match(r"\d+\. ", text), anchor
    assert guardrail.display_text("ND87-2026/D71/K4").startswith("quảng cáo thực phẩm")


@pytest.mark.parametrize("opening", [
    "Phạt tiền từ 20.000.000 đồng đến 30.000.000 đồng đối với hành vi ",
    "Phạt tiền từ 20.000.000 đồng đến 30.000.000 đồng đối với một trong các hành vi sau đây: ",
])
def test_display_text_penalty_variants_and_remaining_amounts(monkeypatch, tmp_path, opening):
    path = tmp_path / "index.json"
    path.write_text(json.dumps([dict(id="SYNTHETIC", file="example.md",
                                    text="4. " + opening + "quảng cáo thực phẩm; 500 đồng; 1,000 đồng.")],
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(guardrail, "LAW_INDEX_FILE", path)
    shown = guardrail.display_text("SYNTHETIC")
    assert shown.startswith("quảng cáo thực phẩm")
    assert not re.search(r"\d\s*đồng", shown)
    assert "500" not in shown and "1,000" not in shown


@pytest.mark.parametrize(("colour", "family", "when", "category", "expected"), [
    ("yellow", "R05", "2025-01-08", "thuc_pham_bo_sung", ["LQC-2012/D8/K9", "ND15-2018/D27/K2"]),
    ("yellow", "R05", "2025-01-08", "TPBVSK", ["ND15-2018/D27/K6", "ND15-2018/D27/K2"]),
    ("red", "R08", "2025-02-12", "thuc_pham_bo_sung", ["ND15-2018/D27/K2"]),
    ("red", "R08", "2026-05-14", "thuc_pham_bo_sung", ["ND15-2018/D27/K2"]),
    ("red", "R08", "2026-05-15", "thuc_pham_bo_sung", ["ND87-2026/D71/K4"]),
    ("red", "R08", "2026-06-19", "thuc_pham_bo_sung", ["ND87-2026/D71/K4"]),
    ("red", "R09", "2025-08-14", "thuc_pham_bo_sung", ["LQC-2012/D8/K11"]),
    ("red", "R09", "2026-07-04", "thuc_pham_bo_sung", ["LQC-2012/D8/K11", "ND87-2026/D50/K2/a"]),
    ("green", "STANDARD", "2025-01-08", "thuc_pham_bo_sung", ["ND15-2018/D27/K2"]),
    ("green", "STANDARD", "2019-01-01", "TPBVSK", ["ND15-2018/D27/K6"]),
])
def test_dated_category_basis(context, colour, family, when, category, expected):
    ctx = _ctx(context, when, category)
    result = guardrail.finalize(_draft(colour, family), ctx, 7)
    assert isinstance(result, Verdict)
    assert result.verdict == colour
    assert result.legal_basis == expected
    assert result.as_of == ctx.as_of and result.dossier_hash == ctx.dossier_hash
    assert result.rule_pack_version == 7
    if when == "2025-01-08":
        assert not any(anchor.startswith("ND87-2026/") for anchor in result.legal_basis)


def test_law_not_yet_effective_abstains_without_losing_fired_rules(monkeypatch, tmp_path, context):
    _synthetic_basis(monkeypatch, tmp_path, [_row("R08", "ND87-2026/D71/K4")])
    result = guardrail.finalize(_draft("red", "R08"), _ctx(context), 2)
    assert result.verdict == "abstain"
    assert result.reason_code == "LAW_NOT_EFFECTIVE"
    assert result.decided_by == "abstain:LAW_NOT_EFFECTIVE"
    assert result.fired_rules == ["R08"]
    assert result.legal_basis == [] and result.law_span is None
    assert result.qlnd_category is None and result.action == "Send to review queue"


def test_missing_family_is_a_programming_error(context):
    with pytest.raises(ValueError, match="No legal-basis rows"):
        guardrail.finalize(_draft("red", "SYNTHETIC_MISSING"), _ctx(context), 1)
    with pytest.raises(ValueError, match="requires a legal-basis family"):
        guardrail.finalize(_draft(family=None), _ctx(context), 1)


def test_category_and_date_windows_filter_before_effective_check(monkeypatch, tmp_path, context):
    _synthetic_basis(monkeypatch, tmp_path, [
        _row("R05", "unrelated-category", category="TPBVSK"),
        _row("R05", "future-window", valid_from="2026-01-01"),
        _row("R05", "past-window", valid_to="2024-12-31"),
        _row("R05", "ND15-2018/D27/K2", role="secondary"),
        _row("R05", "LQC-2012/D8/K9", category="thuc_pham_bo_sung"),
    ])
    result = guardrail.finalize(_draft(), _ctx(context), 1)
    assert result.legal_basis == ["LQC-2012/D8/K9", "ND15-2018/D27/K2"]


@pytest.mark.parametrize("colour,family", [("green", "STANDARD"), ("yellow", "R05")])
def test_evidence_repaired_from_the_selected_item_without_changing_colour(context, colour, family):
    ctx = _ctx(context)
    draft = _draft(colour, family, evidence_span="Invented unsupported quote")
    result = guardrail.finalize(draft, ctx, 1)
    assert result.evidence_span == ctx.P[0].text
    assert result.verdict == colour
    assert draft["evidence_span"] == "Invented unsupported quote"
    result = guardrail.finalize(_draft(colour, family, evidence_span="hỗ trợ"), ctx, 1)
    assert result.evidence_span == "hỗ trợ"


@pytest.mark.parametrize("permitted_id", [None, "not-in-P"])
def test_green_must_name_an_item_in_P(context, permitted_id):
    with pytest.raises(ValueError, match="Unknown permitted item"):
        guardrail.finalize(_draft("green", "STANDARD", permitted_id=permitted_id), _ctx(context), 1)


def test_yellow_without_a_closest_item_cannot_display_an_unverified_quote(context):
    result = guardrail.finalize(_draft(closest_permitted_id=None, evidence_span="Unverified"), _ctx(context), 1)
    assert result.verdict == "yellow" and result.evidence_span is None
    with pytest.raises(ValueError, match="Unknown permitted item"):
        guardrail.finalize(_draft(closest_permitted_id="not-in-P"), _ctx(context), 1)


@pytest.mark.parametrize("colour,family", [("green", "R05"), ("yellow", "STANDARD")])
def test_colour_requires_the_matching_basis_family(context, colour, family):
    with pytest.raises(ValueError, match="requires legal-basis family"):
        guardrail.finalize(_draft(colour, family), _ctx(context), 1)


def test_red_rule_metadata_must_match_the_draft_family(monkeypatch, context):
    _fake_registry(monkeypatch, family="R08")
    with pytest.raises(ValueError, match="Draft family disagrees"):
        guardrail.finalize(_draft("red", "R04"), _ctx(context), 1)


def test_red_law_span_uses_meta_and_ordered_phrases(monkeypatch, context):
    _fake_registry(monkeypatch)
    red = _draft("red", "R04")
    assert guardrail.finalize(red, _ctx(context), 1).law_span == "chữa bệnh"
    _fake_registry(monkeypatch, span=["Missing phrase", "chữa bệnh", "phòng bệnh"])
    assert guardrail.finalize(red, _ctx(context), 1).law_span == "chữa bệnh"
    _fake_registry(monkeypatch, span=["chữa bệnh", "phòng bệnh"])
    assert guardrail.finalize(red, _ctx(context), 1).law_span == "chữa bệnh"
    _fake_registry(monkeypatch, span="Missing phrase")
    assert guardrail.finalize(red, _ctx(context), 1).law_span is None


def test_real_textnorm_preserves_diacritics_and_case(monkeypatch, context):
    try:
        from textnorm import law_normalize
    except ModuleNotFoundError as exc:
        if exc.name != "textnorm":
            raise
        pytest.skip("not ready: textnorm")
    _fake_registry(monkeypatch, span=unicodedata.normalize("NFD", "chữa bệnh"))
    result = guardrail.finalize(_draft("red", "R04"), _ctx(context), 1)
    assert law_normalize(result.law_span) == "chữa bệnh"
    _fake_registry(monkeypatch, span="chua benh")
    assert guardrail.finalize(_draft("red", "R04"), _ctx(context), 1).law_span is None
    _fake_registry(monkeypatch, span="CHỮA BỆNH")
    assert guardrail.finalize(_draft("red", "R04"), _ctx(context), 1).law_span is None


@pytest.mark.parametrize("module", ["rules.registry", "textnorm"])
def test_missing_collaborator_leaves_red_span_null_and_other_colours_work(monkeypatch, context, module):
    _missing(monkeypatch, module)
    for colour, family in [("yellow", "R05"), ("green", "STANDARD"), ("red", "R04")]:
        result = guardrail.finalize(_draft(colour, family), _ctx(context), 1)
        assert result.verdict == colour
        if colour == "red":
            assert result.law_span is None


@pytest.mark.parametrize("module", ["rules.registry", "textnorm"])
def test_transitive_missing_dependency_is_not_hidden(monkeypatch, context, module):
    _missing(monkeypatch, module, "dependency_inside_collaborator")
    with pytest.raises(ModuleNotFoundError, match="Simulated") as caught:
        guardrail.finalize(_draft("red", "R04"), _ctx(context), 1)
    assert caught.value.name == "dependency_inside_collaborator"


def test_existing_broken_collaborator_is_not_treated_as_missing(monkeypatch, context):
    registry = ModuleType("rules.registry")
    monkeypatch.setitem(sys.modules, "rules.registry", registry)
    with pytest.raises(ImportError):
        guardrail.finalize(_draft("red", "R04"), _ctx(context), 1)


@pytest.mark.parametrize(("family", "category"), [
    ("R04", "Disease-treatment claim for a non-drug"),
    ("R08", "Expert or patient endorsement"),
    ("R09", "Superlative without proof"),
])
def test_red_and_t5_categories_are_family_based(monkeypatch, tmp_path, context, family, category):
    result = guardrail.finalize(_draft("red", family), _ctx(context), 1)
    assert result.qlnd_category == category and result.action == "Remove or warn"
    _synthetic_basis(monkeypatch, tmp_path, [_row(family, "LDUOC-2016/D6/K15")])
    _fake_registry(monkeypatch, rule_id="R12", family=family, origin="t5")
    result = guardrail.finalize(_draft("red", family, decided_by="rule:R12", fired_rules=["R12"]),
                                _ctx(context), 1)
    assert result.qlnd_category == category and result.action == "Remove or warn"


def test_green_yellow_abstain_card_suggestions(context):
    assert guardrail.finalize(_draft(), _ctx(context), 1).qlnd_category == "Overclaim beyond the dossier"
    assert guardrail.finalize(_draft(), _ctx(context), 1).action == "Trust & Safety review"
    green = guardrail.finalize(_draft("green", "STANDARD"), _ctx(context), 1)
    assert green.qlnd_category is None and green.action is None
    abstain = guardrail.finalize(_draft("abstain", None, decided_by="abstain:LLM_OFF",
                                          reason_code="LLM_OFF", legal_basis=["untrusted"]), _ctx(context), 1)
    assert abstain.verdict == "abstain" and abstain.legal_basis == []
    assert abstain.qlnd_category is None and abstain.action == "Send to review queue"


def test_no_red_from_llm_and_pydantic_draft_support(context):
    with pytest.raises(ValueError, match="red draft must be decided by a rule"):
        guardrail.finalize(_draft("red", "R04", decided_by="llm_votes"), _ctx(context), 1)

    class Draft(BaseModel):
        atom_key: str
        verdict: str
        family: str
        decided_by: str
        fired_rules: list[str]
        closest_permitted_id: str | None
        evidence_span: str | None

    result = guardrail.finalize(Draft(**_draft()), _ctx(context).model_dump(mode="json"), 9)
    assert result.verdict == "yellow" and result.rule_pack_version == 9
    assert datetime.fromisoformat(result.ts).utcoffset() == timedelta(0)


def test_other_draft_fields_are_retained_and_context_fields_are_authoritative(context):
    original = json.loads((ROOT / "fixtures" / "verdicts_fake.json").read_text(encoding="utf-8"))[0]
    original.update(family="STANDARD", flags=["RECALLED_AFTER"], llm_call_ids=["synthetic-call"],
                    supersedes=10, numbers={"claim": 2, "dossier": 2, "consistent": True})
    ctx = _ctx(context)
    result = guardrail.finalize(original, ctx, 8)
    for field in ("verdict_id", "atom_key", "verdict", "decided_by", "fired_rules", "permitted_id",
                  "closest_permitted_id", "evidence_span", "llm_call_ids", "supersedes", "flags", "ts"):
        assert result.model_dump(mode="json")[field] == original[field]
    assert result.numbers.consistent is True
    assert result.as_of == ctx.as_of and result.rule_pack_version == 8


@pytest.mark.parametrize(("code", "rule_id", "message", "category", "action"), [
    ("NO_SELF_DECLARATION_AT_AS_OF", "L1_PREDECL", "No self-declaration on file at the broadcast date",
     "Sold before self-declaration", "Trust & Safety review"),
    ("NO_AD_CONFIRMATION", "R06", "Health-protection food advertised without an ad-content confirmation",
     "No ad-content confirmation", "Stop the stream (suggested)"),
    ("RECALLED_AT_AS_OF", "L1_RECALL", "Dossier recalled before the broadcast date",
     "Dossier recalled", "Trust & Safety review"),
])
def test_flags_keep_only_dated_basis_and_offer_separate_card_suggestions(context, code, rule_id, message, category, action):
    source = SessionFlag(flag_code=code, rule_id=rule_id, legal_basis=["ND115-2018/D22/K3"], message="Untrusted")
    result = guardrail.finalize_flag(source, _ctx(context, "2024-12-12", "TPBVSK"))
    assert isinstance(result, SessionFlag)
    expected = (["ND15-2018/D26/K1", "ND15-2018/D27/K1"] if rule_id == "R06"
                else ["ND15-2018/D4/K1", "ND15-2018/D5/K2/b"])
    assert result.legal_basis == expected
    assert "ND115-2018/D22/K3" not in result.legal_basis
    assert result.message == message
    assert not re.search(r"\d\s*đồng", result.message)
    assert guardrail.flag_card_suggestions(result) == {"qlnd_category": category, "action": action}
    assert set(result.model_dump()) == set(SessionFlag.model_fields)
    assert source.message == "Untrusted"


def test_flag_with_no_effective_basis_is_suppressed_and_invalid_rule_is_rejected(context):
    flag = SessionFlag(flag_code="NO_AD_CONFIRMATION", rule_id="R06", message="Synthetic")
    assert guardrail.finalize_flag(flag, _ctx(context, "2010-01-01")) is None
    with pytest.raises(ValueError, match="disagree"):
        guardrail.finalize_flag(flag.model_copy(update={"rule_id": "R04"}), _ctx(context))


def test_is_effective_uses_only_top_level_front_matter_and_caches_per_file(monkeypatch, tmp_path):
    law_file = tmp_path / "law.md"
    law_file.write_text("---\neffective_from: 2025-01-08\ntext_version_effective_from: 2099-01-01\n"
                        "nested:\n  effective_from: 1900-01-01\n---\neffective_from: 1900-01-01\n", encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text(json.dumps([dict(id=anchor, file="law.md", text="Synthetic")
                                 for anchor in ("FIRST", "SECOND")]), encoding="utf-8")
    monkeypatch.setattr(guardrail, "LAW_DIR", tmp_path)
    monkeypatch.setattr(guardrail, "LAW_INDEX_FILE", index)
    assert guardrail.is_effective("FIRST", date(2025, 1, 7)) is False
    law_file.unlink()
    assert guardrail.is_effective("SECOND", date(2025, 1, 8)) is True
    assert guardrail.is_effective("FIRST", date(2026, 1, 1)) is True


@pytest.mark.parametrize("content", [
    "effective_from: 2025-01-08\n",
    "---\neffective_from: 2025-01-08\n",
    "---\ntext_version_effective_from: 2025-01-08\n---\n",
    "---\n  effective_from: 2025-01-08\n---\n",
    "---\neffective_from: 2025-01-08\neffective_from: 2025-01-09\n---\n",
])
def test_malformed_or_missing_effective_date_is_not_silently_used(monkeypatch, tmp_path, content):
    (tmp_path / "law.md").write_text(content, encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text(json.dumps([dict(id="BAD", file="law.md", text="Synthetic")]), encoding="utf-8")
    monkeypatch.setattr(guardrail, "LAW_DIR", tmp_path)
    monkeypatch.setattr(guardrail, "LAW_INDEX_FILE", index)
    with pytest.raises(ValueError):
        guardrail.is_effective("BAD", date(2025, 1, 8))
