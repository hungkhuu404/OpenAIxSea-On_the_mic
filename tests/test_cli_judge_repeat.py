"""Sequential repeat measurements through the real judge, LLM layer and ledger."""

from datetime import date
import json
import threading
from time import monotonic_ns
from types import SimpleNamespace

import pytest

import ledger
from core import paths
from core.config import set_override
from core.models import Atom, PermittedItem, Session, Turn
from pipeline import cli, context, extract, judge, llm


@pytest.fixture
def repeat_case(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "committed")
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(paths, "EVIDENCE_DIR", tmp_path / "evidence")
    paths.EVIDENCE_DIR.mkdir()
    (paths.EVIDENCE_DIR / "demo_sku.json").write_text('{"product_name":"Demo Oats"}', encoding="utf-8")
    set_override("LLM_LOCAL_CACHE_DIR", str(tmp_path / "local-cache"))
    set_override("LLM_BACKEND", "codex")
    set_override("OFFLINE", 0)
    # The measurement must override even a much shorter live timeout.
    set_override("LLM_TIMEOUT_S", 1)
    db = tmp_path / "ledger.db"
    ledger.init_db(db)
    text = "First claim. Second claim."
    session = Session(session_id="repeat-synthetic", sku_pin="demo_sku", as_of=date(2025, 1, 8),
        channel="written", source="Synthetic measurement", turns=[
            Turn(turn_id="t01", speaker_label="[KOL 1]", attribution="constructed", text=text)])
    atoms = [Atom(atom_id=f"t01.a{index}", turn_id="t01", span=span, start=text.index(span),
        end=text.index(span) + len(span), axis="thanh_phan", speaker_role="khong_ro",
        subject_is_product=True) for index, span in enumerate(("First claim.", "Second claim."), 1)]
    ledger.add_session(session)
    ledger.add_atoms(session.session_id, atoms)
    ctx = context.Context(sku_id="demo_sku", category_norm="thuc_pham_bo_sung",
        as_of=session.as_of, dossier_state="PRESENT", dossier_hash="synthetic", P=[
            PermittedItem(permitted_id="demo_sku:thanh_phan:1", axis_group="thanh_phan",
                          text="The product contains oats.", confidence="high", source_field="synthetic"),
            PermittedItem(permitted_id="demo_sku:lieu_dung:1", axis_group="lieu_dung",
                          text="UNRELATED_USAGE", confidence="high", source_field="synthetic")])
    monkeypatch.setattr(context, "build_context", lambda sku, day, pack: ctx)
    monkeypatch.setattr(cli, "config_line", lambda: "cfg=synthetic")

    def no_extraction(*args, **kwargs):
        raise AssertionError("judge-repeat must read stored atoms without calling S1")

    monkeypatch.setattr(extract, "extract_atoms", no_extraction)
    state = SimpleNamespace(calls=[], events=[], active=0, maximum=0, labels=[], errors={})
    lock, both = threading.Lock(), threading.Barrier(2)

    def backend(model, effort, schema_name, schema, messages, kind, timeout_s):
        assert kind in ("t1", "t2") and ledger._connection.get() is None
        supplied = json.loads(messages[-2]["content"])["permitted_items"]
        assert [item["permitted_id"] for item in supplied] == ["demo_sku:thanh_phan:1"]
        claim = json.loads(messages[-1]["content"])["claim"]
        with lock:
            pair = len(state.calls) // 2
            state.calls.append((kind, claim, timeout_s))
            state.active += 1
            state.maximum = max(state.maximum, state.active)
            state.events.append(("start", pair, monotonic_ns()))
        both.wait(timeout=5)
        label = state.labels[pair] if state.labels else "supported"
        selected = supplied[0]["permitted_id"]
        response = (dict(label=label, permitted_id=selected if label == "supported" else None,
                         closest_permitted_id=None if label == "supported" else selected,
                         evidence_span="contains oats", numbers=None) if kind == "t1" else
                    dict(entails=[dict(permitted_id=selected, entails=label == "supported",
                                       evidence_span="contains oats")]))
        with lock:
            state.events.append(("end", pair, monotonic_ns()))
            state.active -= 1
        if kind in state.errors:
            return llm._Transport(error=llm._error(state.errors[kind], "synthetic transport error"))
        return llm._Transport(response=response)

    monkeypatch.setattr(llm, "_backend", backend)
    return state, session, atoms, ctx, db


def invoke(n=3):
    return cli.main(["judge-repeat", "--session", "repeat-synthetic", "--turn", "t01", "--n", str(n)])


def test_repeat_is_sequential_fresh_and_does_not_write_ledger(repeat_case, capsys):
    state, session, atoms, ctx, db = repeat_case
    items = ctx.prompt_view(("thanh_phan",))
    with llm.run_scope(measuring=True):
        for atom in atoms:
            judge.judge(atom, items, ctx, "shadow")
    committed = {path: path.read_bytes() for path in paths.CACHE_DIR.glob("*/*.json")}
    assert len(committed) == 4
    before_db = db.read_bytes()
    state.calls.clear()
    state.events.clear()
    state.maximum = 0
    with llm.observe_results() as results:
        assert invoke() == 0
    assert len(results) == len(state.calls) == 12
    assert all(row.purpose == "live" and not row.cache_hit for row in results)
    assert all(timeout == 60 for _, _, timeout in state.calls)
    assert state.maximum == 2
    starts = [event for event in state.events if event[0] == "start"]
    ends = [event for event in state.events if event[0] == "end"]
    for pair in range(5):
        assert max(t for _, p, t in ends if p == pair) <= min(t for _, p, t in starts if p == pair + 1)
    assert [claim for _, claim, _ in state.calls][::2] == [atom.span for atom in atoms] * 3
    assert db.read_bytes() == before_db
    assert {path: path.read_bytes() for path in committed} == committed
    lines = (paths.LOCAL_DIR / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 16 and all(json.loads(line)["purpose"] == "live" for line in lines[4:])
    output = capsys.readouterr().out.splitlines()
    runs = [line for line in output if line.startswith("repeat=")]
    assert len(runs) == 3
    assert all("t01.a1 green" in line and "t01.a2 green" in line and "turn=green" in line for line in runs)
    assert all("errors_by_kind={}" in line for line in runs)
    assert 'atom=t01.a1 colours={"green": 3}' in output
    assert 'atom=t01.a2 colours={"green": 3}' in output
    assert 'turn_colours={"green": 3}' in output
    assert "matches_run1=3/3" in output


def test_repeat_counts_changes_against_first_run(repeat_case, capsys):
    state, _, _, _, _ = repeat_case
    state.labels = ["supported", "supported", "beyond_scope", "supported", "supported", "supported"]
    assert invoke() == 0
    output = capsys.readouterr().out
    assert 'atom=t01.a1 colours={"green": 2, "yellow": 1}' in output
    assert 'turn_colours={"green": 2, "yellow": 1}' in output
    assert "matches_run1=2/3" in output


def test_repeat_reports_error_kind_per_run_without_ledger_writes(repeat_case, capsys):
    state, _, _, _, db = repeat_case
    state.errors = {"t2": "timeout"}
    before = db.read_bytes()
    assert invoke(2) == 1
    output = capsys.readouterr().out
    runs = [line for line in output.splitlines() if line.startswith("repeat=")]
    assert len(runs) == 2 and all('errors_by_kind={"timeout": 2}' in line for line in runs)
    assert all("LLM_TIMEOUT" in line and "turn=abstain" in line for line in runs)
    assert db.read_bytes() == before


def test_repeat_validates_inputs_without_model_calls(repeat_case, capsys):
    state, _, _, _, db = repeat_case
    assert invoke(0) == 2
    assert "--n must be positive" in capsys.readouterr().err
    db.unlink()
    assert invoke(1) == 2
    assert "existing ledger" in capsys.readouterr().err
    assert state.calls == [] and not db.exists()


def test_repeat_refuses_disabled_judge_without_calls_or_writes(repeat_case, capsys):
    state, _, _, _, db = repeat_case
    set_override("JUDGE_MODE", "off")
    before = db.read_bytes()
    assert invoke(2) == 2
    captured = capsys.readouterr()
    assert "judge-repeat requires JUDGE_MODE=two" in captured.err
    assert "matches_run1" not in captured.out
    assert state.calls == [] and db.read_bytes() == before
    assert not (paths.LOCAL_DIR / "llm_calls.jsonl").exists()
