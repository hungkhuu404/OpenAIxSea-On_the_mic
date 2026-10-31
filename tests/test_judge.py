"""Step E's decision table and trust boundaries, with synthetic dossier items."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from datetime import date
import json
import threading
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from core.models import Atom, PermittedItem, Session, Turn
from pipeline import judge, llm
from pipeline.context import Context
from pipeline.drafts import VerdictDraft


def judged(*args, **kwargs):
    return asdict(judge.judge(*args, **kwargs))


@pytest.fixture
def case(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "EVIDENCE_DIR", tmp_path)
    (tmp_path / "demo_sku.json").write_text(json.dumps({"product_name": "Demo Oats"}), encoding="utf-8")
    atom = Atom(atom_id="t01.a1", turn_id="t01", span="Contains oats.", start=0, end=14,
                axis="thanh_phan", speaker_role="khong_ro", subject_is_product=True)
    items = [{"permitted_id": "demo_sku:thanh_phan:1", "text": "Contains oats and vitamin B."},
             {"permitted_id": "demo_sku:thanh_phan:2", "text": "Each serving contains 2 g of oats."}]
    ctx = Context(sku_id="demo_sku", category_norm="thuc_pham_bo_sung", as_of=date(2025, 1, 8),
        dossier_state="PRESENT", dossier_hash="synthetic-hash", P=[
            PermittedItem(**item, axis_group="thanh_phan", confidence="high", source_field="synthetic")
            for item in items])
    return atom, items, ctx


@pytest.fixture
def votes(monkeypatch, case):
    _, items, _ = case
    state = SimpleNamespace(
        t1=dict(label="supported", permitted_id=items[0]["permitted_id"], closest_permitted_id=None,
                evidence_span="Contains oats", numbers=None),
        t2=dict(entails=[dict(permitted_id=item["permitted_id"], entails=index == 0,
                              evidence_span=item["text"] if index == 0 else "")
                        for index, item in enumerate(items)]),
        errors={}, requests=[], active=0, maximum=0)
    both_started = asyncio.Event()

    async def fake(**request):
        state.requests.append(request)
        state.active += 1
        state.maximum = max(state.maximum, state.active)
        if state.active == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        state.active -= 1
        kind = request["kind"]
        return SimpleNamespace(request_sha256=kind + "-synthetic-sha", error=state.errors.get(kind),
                               response=state.t1 if kind == "t1" else state.t2)

    monkeypatch.setattr(llm, "acall", fake)
    return state


@pytest.mark.parametrize("label, found, colour, reason", [
    ("supported", True, "green", None),
    ("supported", False, "abstain", "VOTES_SPLIT"),
    ("beyond_scope", False, "yellow", None),
    ("beyond_scope", True, "abstain", "VOTES_SPLIT"),
    ("contradicts", False, "yellow", None),
    ("contradicts", True, "abstain", "VOTES_SPLIT"),
    ("not_enough_info", False, "abstain", "LLM_NEI"),
    ("not_enough_info", True, "abstain", "LLM_NEI"),
])
def test_combination_table(case, votes, label, found, colour, reason):
    atom, items, ctx = case
    votes.t1.update(label=label, closest_permitted_id=items[0]["permitted_id"])
    votes.t2["entails"][0]["entails"] = found
    result = judged(atom, items, ctx, "live")
    assert result["verdict"] == colour and result["reason_code"] == reason
    assert result["decided_by"] == (f"abstain:{reason}" if reason else "llm_votes")
    assert result["llm_call_ids"] == ["t1-synthetic-sha", "t2-synthetic-sha"]
    assert votes.maximum == 2 and len(votes.requests) == 2
    assert colour != "red"
    if colour == "green":
        assert result["family"] == "STANDARD" and result["permitted_id"] == items[0]["permitted_id"]
    if colour == "yellow":
        assert result["family"] == "R05" and result["closest_permitted_id"] == items[0]["permitted_id"]


@pytest.mark.parametrize("purpose", ["live", "shadow", "oracle"])
@pytest.mark.parametrize("cache_read", [True, False])
def test_forwarding_and_timeout(case, votes, purpose, cache_read):
    judged(*case, purpose, cache_read=cache_read)
    assert {request["kind"] for request in votes.requests} == {"t1", "t2"}
    assert all(request["purpose"] == purpose and request["cache_read"] is cache_read
               for request in votes.requests)
    assert all(request["schema_name"] == request["kind"] for request in votes.requests)
    if purpose != "live":
        assert all(request["timeout_s"] == 60 for request in votes.requests)


def test_repeat_timeout_override(case, votes):
    judged(*case, "live", cache_read=False, timeout_s=60)
    assert all(request["timeout_s"] == 60 for request in votes.requests)


@pytest.mark.parametrize("kind", ["timeout", "off", "nonzero_exit", "no_output", "parse", "schema", "http"])
def test_error_results_abstain(case, votes, kind):
    votes.errors["t2"] = SimpleNamespace(kind=kind)
    result = judged(*case, "live")
    reason = "LLM_TIMEOUT" if kind == "timeout" else "LLM_OFF"
    assert result["verdict"] == "abstain" and result["reason_code"] == reason
    assert result["decided_by"] == f"abstain:{reason}"


def test_timeout_takes_priority_over_another_error(case, votes):
    votes.errors.update(t1=SimpleNamespace(kind="off"), t2=SimpleNamespace(kind="timeout"))
    assert judged(*case, "live")["reason_code"] == "LLM_TIMEOUT"


def test_disabled_judge_makes_no_request(case, votes):
    set_override("JUDGE_MODE", "off")
    result = judged(*case, "live")
    assert result["verdict"] == "abstain" and result["reason_code"] == "LLM_OFF"
    assert result["llm_call_ids"] == [] and votes.requests == []


@pytest.mark.parametrize("invalid", ["t1_id", "t1_closest", "t2_id", "t2_missing", "t2_duplicate"])
def test_invalid_item_votes_are_not_enough_info(case, votes, invalid):
    if invalid == "t1_id":
        votes.t1["permitted_id"] = "another-sku:item:1"
    elif invalid == "t1_closest":
        votes.t1["closest_permitted_id"] = "another-sku:item:1"
    elif invalid == "t2_id":
        votes.t2["entails"][0]["permitted_id"] = "another-sku:item:1"
    elif invalid == "t2_missing":
        votes.t2["entails"].pop()
    else:
        votes.t2["entails"][1] = dict(votes.t2["entails"][0])
    result = judged(*case, "live")
    assert result["verdict"] == "abstain" and result["reason_code"] == "LLM_NEI"


def test_green_selects_t2_item_with_its_own_quote(case, votes):
    atom, items, ctx = case
    votes.t2["entails"][0]["entails"] = False
    votes.t2["entails"][1].update(entails=True, evidence_span="2 g of oats")
    result = judged(atom, items, ctx, "live")
    assert result["verdict"] == "green" and result["permitted_id"] == items[1]["permitted_id"]
    assert result["evidence_span"] == "2 g of oats"


def test_green_prefers_t1_item_even_when_t2_orders_another_first(case, votes):
    _, items, _ = case
    votes.t2["entails"][1].update(entails=True, evidence_span="2 g of oats")
    votes.t2["entails"].reverse()
    result = judged(*case, "live")
    assert result["permitted_id"] == items[0]["permitted_id"]
    assert result["evidence_span"] == "Contains oats"


def test_t2_selected_quote_is_repaired_from_that_item(case, votes):
    _, items, _ = case
    votes.t2["entails"][0]["entails"] = False
    votes.t2["entails"][1].update(entails=True, evidence_span="invented evidence")
    result = judged(*case, "live")
    assert result["verdict"] == "green" and result["evidence_span"] == items[1]["text"]


def test_bad_quote_is_repaired_without_changing_colour_and_numbers_pass_through(case, votes):
    _, items, _ = case
    numbers = dict(claim="one serving", dossier="2 g", consistent=False)
    votes.t1.update(evidence_span="invented evidence", numbers=numbers)
    result = judged(*case, "live")
    assert result["verdict"] == "green" and result["evidence_span"] == items[0]["text"]
    assert result["numbers"].model_dump() == numbers


def test_only_whitelisted_slice_and_metadata_enter_requests(case, votes):
    atom, items, ctx = case
    enriched = [dict(item, note="EXCLUDED_NOTE", regulator_warnings=["EXCLUDED_WARNING"])
                for item in items]
    extra = dict(ctx.model_dump(), gaps="EXCLUDED_GAP", label_claims_not_in_declaration="EXCLUDED_SLOGAN")
    extra["P"] = [dict(text="EXCLUDED_FULL_DOSSIER")]
    judged(atom, enriched, SimpleNamespace(**extra), "live")
    for request in votes.requests:
        messages = request["messages"]
        assert atom.span in messages[-1]["content"]
        assert items[0]["text"] in messages[-2]["content"]
        assert "Demo Oats" in messages[-2]["content"]
        assert "EXCLUDED_" not in json.dumps(messages)
        assert str(ctx.as_of) not in json.dumps(messages)


def test_draft_integrates_with_finalizer(case, votes):
    from pipeline.guardrail import finalize

    atom, items, ctx = case
    draft = judged(atom, items, ctx, "live")
    result = finalize(dict(draft, atom_key="synthetic:t01.a1"), ctx, rule_pack_version=1)
    assert result.verdict == "green" and result.evidence_span in ctx.P[0].text
    assert result.law_span and result.legal_basis == ["ND15-2018/D27/K2"]


def test_real_t5_router_accepts_the_shared_judge_draft(case, votes):
    from pipeline.router import route_atom
    from rules.registry import load_pack

    atom, _, ctx = case
    text = "Contains extra oats."
    atom = atom.model_copy(update={"span": text, "end": len(text)})
    result = route_atom(atom, ctx, load_pack([], mode="live"), "live",
        session_id="synthetic", turn_text=text, rule_pack_version=1)
    assert result.atom_key == "synthetic:t01.a1"
    assert result.verdict == "green" and result.decided_by == "llm_votes"
    assert result.llm_call_ids == ["t1-synthetic-sha", "t2-synthetic-sha"]


def test_real_offline_miss_makes_no_ledger(case):
    with llm.run_scope(offline=True, measuring=True):
        result = judged(*case, "live")
    assert result["verdict"] == "abstain" and result["reason_code"] == "LLM_OFF"
    assert len(result["llm_call_ids"]) == 2 and not paths.ledger_db().exists()


def test_malformed_cached_vote_abstains_through_finalizer(case, monkeypatch, tmp_path):
    from pipeline.guardrail import finalize

    atom, items, ctx = case
    cache = tmp_path / "cache"
    set_override("LLM_CACHE_DIR", str(cache))
    set_override("LLM_BACKEND", "codex")
    set_override("OFFLINE", 0)
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "diagnostics")
    calls = []

    def backend(model, effort, schema_name, schema, messages, kind, timeout_s):
        calls.append(kind)
        response = (dict(label="supported", permitted_id=items[0]["permitted_id"],
            closest_permitted_id=None, evidence_span="Contains oats", numbers=None)
            if kind == "t1" else dict(entails=[dict(permitted_id=item["permitted_id"],
                entails=index == 0, evidence_span=item["text"] if index == 0 else "")
                for index, item in enumerate(items)]))
        llm._validate(response, schema)
        return llm._Transport(response=response)

    monkeypatch.setattr(llm, "_backend", backend)
    with llm.run_scope(measuring=True):
        warm = judge.judge(atom, items, ctx, "live")
    assert warm.verdict == "green" and sorted(calls) == ["t1", "t2"]
    assert {path.stem for path in cache.rglob("*.json")} == set(warm.llm_call_ids)
    location = llm.cache_path(cache, warm.llm_call_ids[0])
    row = json.loads(location.read_text(encoding="utf-8"))
    assert row["kind"] == "t1" and row["error"] is None
    del row["response"]["label"]
    location.write_text(json.dumps(row), encoding="utf-8")
    before = {path: path.read_bytes() for path in cache.rglob("*.json")}
    set_override("OFFLINE", 1)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline judge must use the existing cache without transport")

    monkeypatch.setattr(llm, "_backend", forbidden)
    with llm.run_scope(measuring=True), llm.observe_results() as observed:
        draft = judge.judge(atom, items, ctx, "live")
        result = finalize(dict(asdict(draft), atom_key="synthetic:t01.a1"), ctx, rule_pack_version=1)
    assert draft.verdict == result.verdict == "abstain"
    assert draft.reason_code == result.reason_code == "LLM_OFF"
    assert result.decided_by == "abstain:LLM_OFF"
    assert result.llm_call_ids == warm.llm_call_ids
    assert len(observed) == 2 and all(vote.cache_hit for vote in observed)
    failed = next(vote for vote in observed if vote.kind == "t1")
    assert failed.error.kind == "schema" and failed.response is None
    assert next(vote for vote in observed if vote.kind == "t2").error is None
    assert sorted(calls) == ["t1", "t2"]
    assert {path: path.read_bytes() for path in cache.rglob("*.json")} == before
    assert not paths.ledger_db().exists()


def test_sync_judge_rejects_an_event_loop_without_starting_votes(case, votes):
    async def check():
        with pytest.raises(RuntimeError, match="worker thread"):
            judge.judge(*case, "live")
    asyncio.run(check())
    assert votes.requests == []


def test_verdict_draft_is_local_dataclass_with_required_decision_fields(case, votes):
    result = judge.judge(*case, "live")
    assert is_dataclass(result) and result.verdict == "green"
    assert type(result) is VerdictDraft
    assert result.decided_by == "llm_votes"
    with pytest.raises(TypeError):
        judge.VerdictDraft()


def test_real_schemas_accept_vote_shapes_and_reject_extra_fields(votes):
    for kind, response in (("t1", votes.t1), ("t2", votes.t2)):
        schema = llm._schema(kind)
        llm._validate(response, schema)
        with pytest.raises(ValueError, match="additional property"):
            llm._validate(dict(response, unexpected=True), schema)


@pytest.mark.parametrize("parallel", [1, 2, 3])
def test_shadow_bounds_total_calls_across_judge_pairs(case, monkeypatch, tmp_path, parallel):
    from pipeline import cli, context
    from rules.registry import load_pack

    atom, items, ctx = case
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "committed")
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    set_override("OFFLINE", 0)
    set_override("LLM_PARALLEL", parallel)
    sessions = [Session(session_id=f"shadow-synthetic-{index}", sku_pin=ctx.sku_id,
        as_of=ctx.as_of, channel="written", source="Synthetic batch", turns=[
            Turn(turn_id=atom.turn_id, speaker_label="[KOL 1]", attribution="constructed",
                 text=atom.span)]) for index in range(2)]
    monkeypatch.setattr(cli, "_shadow_targets", lambda args: sessions)
    monkeypatch.setattr(cli, "_v1_pack", lambda: load_pack([], mode="measure"))
    monkeypatch.setattr(cli, "config_line", lambda: "cfg=synthetic")
    monkeypatch.setattr(context, "build_context", lambda *args: ctx)
    lock = threading.Lock()
    filled, overflow, release = threading.Event(), threading.Event(), threading.Event()
    active = maximum = 0

    def backend(model, effort, schema_name, schema, messages, kind, timeout_s):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active > parallel:
                overflow.set()
            if kind != "s1" and active >= parallel:
                filled.set()
        try:
            if kind == "s1":
                response = dict(atoms=[dict(span=atom.span, axis=str(atom.axis),
                    speaker_role="khong_ro", role_span=None, subject_is_product=True)])
            else:
                assert release.wait(timeout=5), "Batch test did not release the backend"
                response = (dict(label="beyond_scope", permitted_id=None,
                    closest_permitted_id=items[0]["permitted_id"], evidence_span=items[0]["text"],
                    numbers=None) if kind == "t1" else dict(entails=[dict(
                    permitted_id=item["permitted_id"], entails=False, evidence_span="") for item in items]))
            llm._validate(response, schema)
            return llm._Transport(response=response)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(llm, "_backend", backend)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def run():
            with llm.observe_results() as observed:
                return cli.main(["shadow", "--golden"]), observed
        future = pool.submit(run)
        try:
            assert filled.wait(timeout=5), "Judge calls did not start"
            assert not overflow.wait(timeout=.2), f"Observed {maximum} calls for a budget of {parallel}"
        finally:
            release.set()
        code, observed = future.result(timeout=10)
    assert code == 0 and maximum == parallel
    assert sorted(row.kind for row in observed) == ["s1", "s1", "t1", "t1", "t2", "t2"]
    assert not paths.ledger_db().exists()
