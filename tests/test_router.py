"""S2 uses synthetic dossier items and fake collaborators, never a backend."""

from datetime import date
import json
import sys
from types import SimpleNamespace

import pytest

from core.models import Atom, PermittedItem, RuleHit, Verdict
from pipeline import router
from pipeline.context import Context
from pipeline.drafts import VerdictDraft
from textnorm import match_key


@pytest.fixture(autouse=True)
def no_equivalents(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REPO_ROOT", tmp_path)


@pytest.fixture
def collaborators(monkeypatch):
    calls = []

    def finalize(draft, ctx, version, *, pack):
        assert isinstance(draft, dict)
        calls.append(("F", draft, version, pack))
        return Verdict(atom_key=draft["atom_key"], rule_pack_version=version,
                       verdict=draft["verdict"], decided_by=draft["decided_by"],
                       reason_code=draft["reason_code"], fired_rules=draft["fired_rules"],
                       permitted_id=draft["permitted_id"], flags=draft["flags"],
                       evidence_span=draft["evidence_span"], llm_call_ids=draft["llm_call_ids"],
                       as_of=ctx.as_of, dossier_hash=ctx.dossier_hash,
                       action="review", ts="2026-01-01T00:00:00Z")

    def judge(atom, items, ctx, purpose, cache_read=True):
        calls.append(("E", purpose, items, cache_read))
        return VerdictDraft("green", "llm_votes", family="STANDARD",
                            permitted_id=items[0]["permitted_id"])

    monkeypatch.setitem(sys.modules, "pipeline.guardrail", SimpleNamespace(finalize=finalize))
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    monkeypatch.setitem(sys.modules, "rules.nearhit", SimpleNamespace(detect=lambda view: []))
    return calls


def pack(hits=(), quarantined=()):
    return SimpleNamespace(mode="live", check_atom=lambda view: list(hits),
                           quarantined_hits=lambda view: list(quarantined))


def hit(rule_id="R04", ambiguous=False, precedence=1):
    return RuleHit(rule_id=rule_id, family=rule_id, matched="chữa bệnh",
                   law_anchor="synthetic", law_span="synthetic",
                   precedence=precedence, ambiguous_negation=ambiguous)


def route(value=None, ctx=None, rules=None, purpose="live", emit=None, **changes):
    value = value or atom()
    return router.route_atom(value, ctx or context(), rules or pack(), purpose, emit,
                             session_id="fixture", turn_text=value.span,
                             rule_pack_version=7, **changes)


def context(**changes):
    return Context(**(dict(
        sku_id="demo_sku", category_norm="thuc_pham_bo_sung",
        as_of=date(2025, 1, 8), dossier_state="PRESENT", dossier_hash="synthetic",
        P=[PermittedItem(permitted_id="demo_sku:cong_dung:1", axis_group="cong_dung",
                         text="hỗ trợ tiêu hóa", confidence="high", source_field="test")],
    ) | changes))


def atom(span="hỗ trợ tiêu hóa", **changes):
    return Atom(**(dict(atom_id="t01.a1", turn_id="t01", span=span,
                       start=0, end=len(span), axis="cong_dung",
                       speaker_role="kol", subject_is_product=True) | changes))


def test_allow_list_matches_whole_syllables_with_accents():
    items = context().P
    assert router._allow_item(match_key("tiêu hóa")[0], items) == items[0].permitted_id
    assert router._allow_item("tiêu", items) == items[0].permitted_id
    assert router._allow_item("tiê", items) is None
    assert router._allow_item("tieu hoa", items) is None
    assert router._allow_item("", items) is None


@pytest.mark.parametrize("negation", router._NEGATION)
def test_allow_list_never_drops_dossier_negation(negation):
    item = context().P[0].model_copy(update={"text": f"{negation} chứa đường"})
    assert router._allow_item(match_key("chứa đường")[0], [item]) is None
    assert router._allow_item(match_key("đường")[0], [item]) is None
    assert router._allow_item(match_key(item.text.upper() + ".")[0], [item]) == item.permitted_id


def test_allow_list_does_not_treat_partial_syllables_as_negation():
    item = context().P[0].model_copy(update={"text": "khôngkhí chứa đường"})
    assert router._allow_item(match_key("chứa đường")[0], [item]) == item.permitted_id


@pytest.mark.parametrize("word", router._INTENSIFIERS)
def test_intensifiers_block_allow_list(word):
    text = f"hỗ trợ tiêu hóa {word}"
    item = context().P[0].model_copy(update={"text": text})
    assert router._allow_item(match_key(text)[0], [item]) is None


@pytest.mark.parametrize("axis,subject,nulls,empty,expected", [
    ("doi_tuong", False, ["doi_tuong"], True, "DOSSIER_EMPTY"),
    ("doi_tuong", True, ["doi_tuong"], True, "DOSSIER_EMPTY"),
    ("cong_dung", False, [], False, "SUBJECT_AMBIGUOUS"),
    ("doi_tuong", True, ["doi_tuong"], False, "DOSSIER_FIELD_MISSING"),
    ("lieu_dung", True, ["lieu_dung"], False, "DOSSIER_FIELD_MISSING"),
    ("doi_tuong", True, [], False, "DOSSIER_EMPTY"),
])
def test_condition_order(axis, subject, nulls, empty, expected):
    ctx = context(null_fields=nulls)
    if empty:
        ctx.P = []
    items = [item for item in ctx.P if item.axis_group in router.axis_groups(axis)]
    assert router._condition(atom(axis=axis, subject_is_product=subject), ctx, items) == expected


def test_unresolved_beats_empty():
    assert router._condition(atom(), context(sku_id=None, P=[]), []) == "SKU_UNRESOLVED"


def test_transitive_missing_module_is_not_hidden(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name="dependency_inside_judge")
    monkeypatch.setattr(router, "import_module", missing)
    with pytest.raises(ModuleNotFoundError) as caught:
        router._optional_module("pipeline.judge")
    assert caught.value.name == "dependency_inside_judge"


def test_negq_only_carries_span_and_turn_and_caller_purpose(monkeypatch):
    from pipeline import llm

    calls = []
    result = SimpleNamespace(error=None, response={"answer": "negated"})
    monkeypatch.setattr(llm, "call", lambda **kw: calls.append(kw) or result)
    assert router._negq("không chữa bệnh", "Tôi nói không chữa bệnh", "oracle") == ("negated", result)
    assert calls[0]["purpose"] == "oracle"
    assert calls[0]["kind"] == calls[0]["schema_name"] == "negq"
    import json
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "span": "không chữa bệnh", "turn_text": "Tôi nói không chữa bệnh"}


def test_allow_list_stops_rules_and_finalizes(collaborators):
    def forbidden(view):
        pytest.fail("B ran after A decided")
    rules = pack()
    rules.check_atom = forbidden
    result = route(rules=rules)
    assert (result.verdict, result.decided_by, result.permitted_id) == (
        "green", "allow_list", "demo_sku:cong_dung:1")
    assert result.rule_pack_version == 7 and result.action == "review"
    assert [(row[0], row[1]["family"]) for row in collaborators] == [("F", "STANDARD")]


def test_runtime_pack_is_forwarded_to_guardrail(collaborators):
    from rules.registry import load_pack

    rules = load_pack([], mode="measure")
    route(rules=rules)
    assert collaborators[-1][3] is rules


def test_code_rule_precedence_and_all_fired_rules(collaborators):
    result = route(atom("chữa bệnh"), rules=pack([hit("R09", precedence=9), hit()]))
    assert (result.verdict, result.decided_by, result.fired_rules) == (
        "red", "rule:R04", ["R04", "R09"])
    assert [row[0] for row in collaborators] == ["F"]


def test_intensifier_reaches_judge_instead_of_allow_list(collaborators):
    value = atom("hỗ trợ tiêu hóa hoàn toàn")
    ctx = context()
    ctx.P[0].text = value.span
    result = route(value, ctx)
    assert (result.verdict, result.decided_by) == ("green", "llm_votes")
    assert [row[0] for row in collaborators] == ["E", "F"]


@pytest.mark.parametrize("axis,subject", [("doi_tuong", False), ("lieu_dung", True),
                                         ("cong_dung", False)])
def test_empty_dossier_routes_to_empty_before_subject_or_null_fields(collaborators, axis, subject):
    result = route(atom("unmatched", axis=axis, subject_is_product=subject),
                   context(P=[], null_fields=["doi_tuong", "lieu_dung"]))
    assert (result.verdict, result.reason_code, result.action) == ("abstain", "DOSSIER_EMPTY", "review")
    assert [row[0] for row in collaborators] == ["F"]


def test_near_hits_are_stored_without_changing_colour(collaborators, monkeypatch):
    from core.models import NearHit

    near = NearHit(pattern="synthetic", gap=1, law_anchor="synthetic", law_span="synthetic", span="tiêu hóa")
    monkeypatch.setitem(sys.modules, "rules.nearhit", SimpleNamespace(detect=lambda view: [near]))
    value = atom()
    result = route(value)
    assert value.near_hits == [near]
    assert (result.verdict, result.decided_by) == ("green", "allow_list")


@pytest.mark.parametrize("answer,error,expected", [
    ("negated", None, "llm_votes"),
    ("not_negated", None, "rule:R04"),
    ("unsure", None, "abstain:RULE_AMBIGUOUS"),
    (None, SimpleNamespace(kind="off"), "abstain:RULE_AMBIGUOUS"),
])
def test_ambiguous_negation(collaborators, monkeypatch, answer, error, expected):
    from pipeline import llm

    calls = []
    def lookup(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(request_sha256="negation", error=error,
                               response={"answer": answer})
    monkeypatch.setattr(llm, "call", lookup)
    value = atom("không chữa bệnh")
    routed = router._route_atom(value, context(), pack([hit(ambiguous=True)]), "live",
                                session_id="fixture", turn_text=value.span, rule_pack_version=7)
    assert routed.verdict.decided_by == expected
    assert routed.negq_miss == int(error is not None)
    assert len(calls) == 1


def test_quarantine_never_reaches_judge(collaborators):
    result = route(atom("chữa bệnh"), rules=pack(quarantined=[hit()]))
    assert (result.verdict, result.reason_code, result.flags) == (
        "abstain", "RULE_QUARANTINED", ["PREV_RED"])
    assert [row[0] for row in collaborators] == ["F"]


@pytest.mark.parametrize("purpose,warmed", [("live", False), ("shadow", True), ("oracle", True)])
def test_warm_judge_even_when_allow_list_decides(collaborators, purpose, warmed):
    result = route(purpose=purpose)
    assert result.decided_by == "allow_list"
    assert any(row[0] == "E" for row in collaborators) == warmed


def test_shadow_negation_warms_once_and_ignores_judge_colour(collaborators, monkeypatch):
    from pipeline import llm

    calls = []
    monkeypatch.setattr(llm, "call", lambda **kw: calls.append(kw) or SimpleNamespace(
        request_sha256="negation", error=None, response={"answer": "not_negated"}))
    result = route(atom("không chữa bệnh"), rules=pack([hit(ambiguous=True)]), purpose="shadow")
    assert result.verdict == "red" and result.decided_by == "rule:R04"
    assert len(calls) == 1 and calls[0]["purpose"] == "shadow"
    assert any(row[0] == "E" for row in collaborators)


def test_missing_judge_is_llm_off_and_no_events_when_emit_none(collaborators, monkeypatch):
    original = router.import_module
    def load(name):
        if name == "pipeline.judge":
            raise ModuleNotFoundError(name=name)
        return original(name)
    monkeypatch.setattr(router, "import_module", load)
    result = route(atom("unequal claim"))
    assert (result.verdict, result.reason_code, result.action) == ("abstain", "LLM_OFF", "review")


def test_one_event_for_every_atom_step(collaborators):
    events = []
    route(emit=lambda kind, payload: events.append((kind, payload)))
    assert [payload["step"] for _, payload in events] == list("ABCDEF")
    assert [payload["outcome"] for _, payload in events] == [
        "green", "skipped", "skipped", "skipped", "skipped", "green"]
    assert all(kind == "step" and payload["ms"] >= 0 and payload["session_id"] == "fixture"
               for kind, payload in events)


def test_judge_cannot_produce_red(collaborators, monkeypatch):
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(
        judge=lambda *args, **kwargs: VerdictDraft("red", "llm_votes", family="R04")))
    with pytest.raises(ValueError, match="Only code rules"):
        route(atom("unequal claim"))
    assert collaborators == []


def test_source_and_caller_inputs_are_required(collaborators):
    with pytest.raises(TypeError, match="session_id"):
        router.route_atom(atom(), context(), pack(), "live")
    with pytest.raises(ValueError, match="exact substring"):
        router.route_atom(atom(), context(), pack(), "live", session_id="fixture",
                          turn_text="other", rule_pack_version=3)


def test_allow_list_real_h2_boundary():
    from pipeline.context import build_context
    from rules.registry import load_pack

    rules = load_pack([], mode="measure")
    ctx = build_context("kera", date(2025, 1, 8), rules)
    item = ctx.P[4]
    value = atom(item.text, axis="thanh_phan")
    verdict = route(value, ctx, rules)
    assert verdict.verdict == "green" and verdict.decided_by == "allow_list"
    assert verdict.permitted_id == item.permitted_id
    assert verdict.evidence_span in item.text
    assert verdict.legal_basis == ["ND15-2018/D27/K2"]
    assert verdict.law_span and verdict.action is None


def test_proposed_candidate_measure_pack_reaches_real_guardrail(monkeypatch, tmp_path):
    from rules import registry

    registered = registry.all_rules()
    meta = next(rule.meta for rule in registered if rule.rule_id == "R04").model_dump(mode="json")
    meta.update(id="R12_ROUTER_CANDIDATE", origin="t5", status="proposed",
                approved_by=None, approved_at=None)
    assert all(rule.rule_id != meta["id"] for rule in registered)
    directory = tmp_path / "candidate with spaces" / "rules"
    directory.mkdir(parents=True)
    (directory / f"{meta['id']}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    constants = {"RULE_ID": meta["id"], "FAMILY": meta["family"], "ORIGIN": meta["origin"],
                 "ANCHORS": meta["anchors"], "LAW_SPAN": meta["law_span"],
                 "PRECEDENCE": meta["precedence"]}
    source = "".join(f"{name} = {value!r}\n" for name, value in constants.items())
    source += ("from rules._common import make_hit\n"
               "def check(atom_view):\n"
               "    return [make_hit(RULE_ID, FAMILY, atom_view.text, ANCHORS[0], LAW_SPAN, PRECEDENCE)]\n")
    (directory / f"{meta['id']}.py").write_text(source, encoding="utf-8")
    rules = registry.load_pack([meta["id"]], mode="measure", candidate_dir=directory.parent)
    assert rules.mode == "measure" and len(rules.active) == 1
    assert rules.active[0].meta.status == "proposed"
    assert rules.active[0].meta.approved_by is None

    def no_tree_lookup():
        pytest.fail("Candidate metadata must come from the supplied runtime pack")

    monkeypatch.setattr(registry, "all_rules", no_tree_lookup)
    result = route(atom("chữa bệnh"), rules=rules)
    assert (result.verdict, result.decided_by, result.fired_rules) == (
        "red", f"rule:{meta['id']}", [meta["id"]])
    assert result.rule_pack_version == 7
    assert result.legal_basis == ["LDUOC-2016/D6/K15"]
    assert result.law_span == rules.active[0].meta.law_span == "chữa bệnh"
    assert result.qlnd_category == "Disease-treatment claim for a non-drug"
    assert result.action == "Remove or warn"


def test_positive_atom_against_negated_dossier_reaches_real_h2_without_allow_list(monkeypatch):
    from pipeline import llm

    calls = []
    def judge(*args, **kwargs):
        calls.append("E")
        return VerdictDraft("abstain", "abstain:LLM_OFF", reason_code="LLM_OFF")

    def forbidden(*args, **kwargs):
        pytest.fail("Dossier negation safety must not ask an LLM for an override")

    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    monkeypatch.setattr(llm, "call", forbidden)
    item = PermittedItem(permitted_id="demo_sku:thanh_phan:1", axis_group="thanh_phan",
                         text="không chứa đường", confidence="high", source_field="test")
    verdict = route(atom("chứa đường", axis="thanh_phan"), context(P=[item]))
    assert calls == ["E"]
    assert (verdict.verdict, verdict.decided_by, verdict.reason_code) == (
        "abstain", "abstain:LLM_OFF", "LLM_OFF")
    assert verdict.permitted_id is None and verdict.legal_basis == []
    assert verdict.action == "Send to review queue"


@pytest.mark.parametrize("shape", ["dataclass", "mapping", "model"])
def test_judge_shapes_reach_real_h2(monkeypatch, shape):
    from dataclasses import asdict
    from pydantic import create_model

    draft = VerdictDraft("yellow", "llm_votes", family="R05",
                         closest_permitted_id=context().P[0].permitted_id)
    if shape == "mapping":
        draft = asdict(draft)
    elif shape == "model":
        draft = create_model("JudgeDraft", **{
            key: (type(value), value) for key, value in asdict(draft).items()
        })()
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(
        judge=lambda *args, **kwargs: draft))
    verdict = route(atom("unequal claim"))
    assert verdict.verdict == "yellow" and verdict.decided_by == "llm_votes"
    assert verdict.legal_basis == ["LQC-2012/D8/K9", "ND15-2018/D27/K2"]


@pytest.mark.parametrize("shape", [dict(verdict="red", decided_by="llm_votes", family="R04"),
                                      dict(verdict="green", decided_by="rule:R04", family="STANDARD")])
def test_mapping_judge_cannot_claim_code_authority(monkeypatch, shape):
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(
        judge=lambda *args, **kwargs: shape))
    with pytest.raises(ValueError, match="Only code rules"):
        route(atom("unequal claim"))
