"""Approval, legal-basis and LLM-colour boundaries, using temporary rule packs."""

import importlib
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from core import paths
from rules import registry


FIXTURES = Path(__file__).parent / "gate_fixtures" / "behaviour"


def ready(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            pytest.skip(f"not ready: {name}")
        raise


@pytest.fixture
def temporary_rules(tmp_path, monkeypatch):
    directory = tmp_path / "rules"
    directory.mkdir()
    source = (FIXTURES / "R04x.py").read_text(encoding="utf-8")
    base = json.loads((FIXTURES / "R04x.meta.json").read_text(encoding="utf-8"))
    for rule_id, approved in [("R04x", "hungkhuu404"), ("R04y", None), ("R04z", None)]:
        meta = dict(base, id=rule_id, status="active" if approved else "proposed", approved_by=approved)
        (directory / f"{rule_id}.py").write_text(source.replace('"R04x"', f'"{rule_id}"'), encoding="utf-8")
        (directory / f"{rule_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(registry, "__file__", str(directory / "registry.py"))
    return directory


def test_live_pack_contains_only_approved_rules(temporary_rules):
    pack = registry.load_pack(["R04x"], mode="live")
    assert all(rule.meta.approved_by is not None for rule in pack.active)
    with pytest.raises(ValueError, match="approval"):
        registry.load_pack(["R04x", "R04y"], mode="live")
    measured = registry.load_pack(["R04x", "R04y"], mode="measure")
    assert sum(rule.meta.approved_by is None for rule in measured.active) == 1
    with pytest.raises(ValueError, match="one unapproved"):
        registry.load_pack(["R04x", "R04y", "R04z"], mode="measure")


def test_measure_runner_never_writes_live_verdicts(temporary_rules, monkeypatch):
    runner = ready("pipeline.runner")
    import ledger

    def forbidden(*args, **kwargs):
        pytest.fail("A measuring runner must not read or write the live ledger")
    for name in ("current_pack", "add_session", "add_atoms", "replace_atoms",
                 "append_verdicts", "add_session_flags", "append_event", "record_llm_call"):
        monkeypatch.setattr(ledger, name, forbidden)
    pack = registry.load_pack(["R04x", "R04y"], mode="measure")
    case = dict(case_id="synthetic", sku_id="demo_sku", as_of="2025-01-08",
                source_date="2025-01-08", text="An ordinary synthetic claim.",
                label="red", rule_ids=["R04"])
    runner.run_cases([case], purpose="live", pack=pack, pack_version=1)
    assert not paths.ledger_db().exists()


def test_law_basis_has_no_unknown_or_forbidden_anchors():
    root = paths.REPO_ROOT
    index = {row["id"] for row in json.loads((paths.LAW_DIR / "index.json").read_text(encoding="utf-8"))}
    forbidden = json.loads((root / "gate/data/forbidden_anchors.json").read_text(encoding="utf-8"))
    rows = json.loads((root / "rules/law_basis.json").read_text(encoding="utf-8"))["rows"]
    for row in rows:
        anchor = row["anchor"]
        assert anchor in index
        assert anchor not in forbidden["exact"]
        assert not any(anchor == p or anchor.startswith(p + "/") for p in forbidden["prefixes"])


def fake_response(schema, selection, found, name=""):
    """Fill the transport's schema subset, using its actual enum spellings."""
    if "enum" in schema:
        choices = schema["enum"]
        for value in choices:
            if isinstance(value, str) and value.replace("_", " ") == selection.replace("_", " "):
                return value
        if "found" in choices and "none" in choices:
            return "found" if found else "none"
        return next(value for value in choices if value is not None)
    kind = schema["type"]
    if isinstance(kind, list):
        kind = next(value for value in kind if value != "null")
    if kind == "object":
        return {key: fake_response(child, selection, found, key)
                for key, child in schema["properties"].items()}
    if kind == "array":
        return [fake_response(schema["items"], selection, found)]
    if kind == "boolean":
        return found
    if kind == "string":
        return "demo_sku:cong_dung:1" if "id" in name else "A declared synthetic benefit."
    return None if kind == "null" else 0


def test_fake_votes_follow_schema_enums():
    from pipeline.llm import _validate

    schema = {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["negated", "not_negated", "unsure"]},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "entails": {"type": "boolean"}}}},
    }}
    value = fake_response(schema, "not negated", False)
    _validate(value, schema)
    assert value == {"decision": "not_negated", "items": [{"entails": False}]}


@pytest.mark.parametrize("target", ["pipeline.judge", "pipeline.router"])
@pytest.mark.parametrize("mode", ["two", "off"])
@pytest.mark.parametrize("t1", ["supported", "beyond_scope", "contradicts", "not_enough_info"])
@pytest.mark.parametrize("found", [True, False])
@pytest.mark.parametrize("negq", ["negated", "not negated", "unsure"])
def test_llm_votes_cannot_produce_red(monkeypatch, target, mode, t1, found, negq):
    module = ready(target)
    from core.config import set_override
    from core.models import Atom, PermittedItem
    from pipeline.context import Context
    from pipeline import llm

    set_override("JUDGE_MODE", mode)
    item = PermittedItem(permitted_id="demo_sku:cong_dung:1", axis_group="cong_dung",
                         text="A declared synthetic benefit.", confidence="high",
                         source_field="permitted_claims[0].text")
    ctx = Context(sku_id="demo_sku", category_norm="thuc_pham_bo_sung", as_of="2026-10-21",
                  dossier_state="PRESENT", P=[item], dossier_hash="synthetic")
    text = "không cần sản phẩm khác cho synthetic benefit."
    atom = Atom(atom_id="t01.a1", turn_id="t01", span=text, start=0, end=len(text),
                axis="cong_dung", speaker_role="khong_ro", subject_is_product=True)

    def call(*args, **kwargs):
        assert not args, "Use named call arguments from the LLM contract"
        kind = kwargs["kind"]
        assert kind in ("t1", "t2", "negq")
        schema = llm._schema(kwargs["schema_name"])
        response = fake_response(schema, negq if kind == "negq" else t1, found)
        llm._validate(response, schema)
        return llm.Result(request_sha256="0" * 64, kind=kind, purpose=kwargs["purpose"],
            backend="off", model=kwargs["model"], effort=kwargs["effort"], response=response,
            latency_ms=0, ts="2026-10-31T00:00:00Z", cache_hit=True)

    monkeypatch.setattr(llm, "call", call)
    if target == "pipeline.judge":
        from pipeline.router import axis_groups

        result = module.judge(atom, ctx.prompt_view(axis_groups(atom.axis)), ctx,
                              purpose="shadow", cache_read=True)
    else:
        from pipeline.drafts import VerdictDraft

        def judge(*args, **kwargs):
            # Fake E only; exercise the real router and F with every vote tuple.
            if mode == "off":
                return VerdictDraft("abstain", "abstain:LLM_OFF", reason_code="LLM_OFF")
            if t1 == "supported" and found:
                return VerdictDraft("green", "llm_votes", family="STANDARD",
                                    permitted_id=item.permitted_id, evidence_span=item.text)
            if t1 in ("beyond_scope", "contradicts") and not found:
                return VerdictDraft("yellow", "llm_votes", family="R05")
            reason = "LLM_NEI" if t1 == "not_enough_info" else "VOTES_SPLIT"
            return VerdictDraft("abstain", f"abstain:{reason}", reason_code=reason)

        fake_judge = ModuleType("pipeline.judge")
        fake_judge.judge = judge
        monkeypatch.setitem(sys.modules, "pipeline.judge", fake_judge)
        result = module.route_atom(atom, ctx, registry.RuntimePack((), (), "live"),
                                   purpose="shadow", emit=None, session_id="synthetic",
                                   turn_text=text, rule_pack_version=0, cache_read=True)
    verdict = result["verdict"] if isinstance(result, dict) else result.verdict
    assert verdict != "red"
