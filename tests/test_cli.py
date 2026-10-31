"""CLI parsing, public-cache boundaries and output with fake collaborators."""

from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from core import paths
from core.config import settings
from core.models import Atom, Session, Turn, Verdict
from pipeline import cli, llm
from pipeline.runner import SessionResult


_V1_PACK = cli._v1_pack


def session(session_id="kera-reenact-2025-01-08", text="synthetic claim"):
    return Session(session_id=session_id, sku_pin="demo_sku", as_of=date(2025, 1, 8),
                   channel="written", source="Synthetic test", turns=[
                       Turn(turn_id="t01", speaker_label="[KOL 1]", attribution="constructed", text=text),
                       Turn(turn_id="t02", speaker_label="[KOL 2]", attribution="constructed", text=text)])


def result(value):
    atoms, verdicts = [], []
    for turn in value.turns:
        atoms.append(Atom(atom_id=f"{turn.turn_id}.a1", turn_id=turn.turn_id, span=turn.text,
                          start=0, end=len(turn.text), axis="cong_dung", speaker_role="kol",
                          subject_is_product=True))
        verdicts.append(Verdict(atom_key=f"{value.session_id}:{turn.turn_id}.a1",
                                rule_pack_version=1, verdict="abstain", decided_by="abstain:LLM_OFF",
                                reason_code="LLM_OFF", as_of=value.as_of, dossier_hash="synthetic",
                                ts="2026-01-01T00:00:00Z"))
    return SessionResult(value, atoms, verdicts, turn_verdicts={"t01": "abstain", "t02": "abstain"})


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    root = tmp_path / "repo space.dot"
    root.mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache" / "llm")
    monkeypatch.setattr(paths, "LOCAL_DIR", root / ".local")
    monkeypatch.setattr(paths, "KERA_DIR", root / "data" / "demo" / "kera")
    monkeypatch.setattr(paths, "GOLDEN_CASES", root / "data" / "cases" / "golden.jsonl")
    monkeypatch.setattr(settings, "LEDGER_DB", str(root / ".local" / "ledger.db"))
    monkeypatch.setattr(settings, "EVAL_DIR", str(root / "eval"))
    monkeypatch.setattr(settings, "LLM_BACKEND", "codex")
    monkeypatch.setattr(settings, "LLM_LOCAL_CACHE_DIR", str(root / ".local" / "cache"))
    monkeypatch.setattr(settings, "OFFLINE", 0)
    monkeypatch.setattr(cli, "config_line", lambda: "cfg=synthetic")
    fake_pack = SimpleNamespace(mode="measure")
    monkeypatch.setattr(cli, "_v1_pack", lambda: fake_pack)
    state = SimpleNamespace(stored={}, calls=[], inits=[], warm=[], s1=[], ingested=[], clip=False)

    def add(value):
        return state.stored.setdefault(value.session_id, value)

    def init(path):
        state.inits.append(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def process(session_id, purpose, reprocess=False, cache_read=True):
        state.calls.append((session_id, purpose, reprocess, cache_read))
        return result(state.stored[session_id])

    def from_text(sku, day, text, source):
        state.ingested.append((sku, day, text, source))
        return session("text-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], text)

    def reenactment(path, sku, day):
        assert isinstance(path, Path)
        return session("clip-a1" if state.clip else f"kera-reenact-{day}")

    def compute(value, pack, version, purpose, emit=None):
        assert pack is fake_pack and version == 1
        state.warm.append((value.session_id, purpose))
        if emit is not None:
            emit("step", dict(session_id=value.session_id, step="A", outcome="continue", ms=0))
        return result(value)

    monkeypatch.setitem(sys.modules, "ledger", SimpleNamespace(init_db=init, add_session=add))
    monkeypatch.setitem(sys.modules, "pipeline.runner", SimpleNamespace(
        process_session=process, _compute=compute))
    monkeypatch.setitem(sys.modules, "pipeline.ingest", SimpleNamespace(
        from_reenactment=reenactment, from_text=from_text,
        from_eval_case=lambda row: session("case-" + row["case_id"], row["text"])))
    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(
        extract_atoms=lambda value, turn, previous, ctx, purpose, cache_read=True:
        state.s1.append((value.session_id, turn.turn_id, purpose))))
    import pipeline.context
    monkeypatch.setattr(pipeline.context, "build_context", lambda sku, day, pack: object())
    return root, state


def test_kera_prints_one_line_per_turn_and_initializes_once(boundary, capsys):
    _, state = boundary
    assert cli.main(["kera", "--as-of", "2025-01-08"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == ["cfg=synthetic", "t01 abstain | t01.a1 abstain LLM_OFF",
                         "t02 abstain | t02.a1 abstain LLM_OFF"]
    assert lines[3].startswith("calls_made=0 cache_hits=0 offline_misses=0 errors=0 elapsed_s=")
    assert cli.main(["kera", "--as-of", "2025-01-08"]) == 0
    assert len(state.stored) == len(state.inits) == 1
    assert state.calls == [("kera-reenact-2025-01-08", "live", False, True)] * 2


def test_fresh_implies_reprocess_and_disables_cache_reads(boundary):
    _, state = boundary
    state.stored["fixture"] = session("fixture")
    assert cli.main(["process", "--session", "fixture", "--purpose", "shadow", "--fresh"]) == 0
    assert state.calls == [("fixture", "shadow", True, False)]


def test_sentence_file_path_with_spaces_is_utf8(boundary, tmp_path, capsys):
    _, state = boundary
    path = tmp_path / "sentence space.txt"
    path.write_text("câu tổng hợp\n\ncâu thứ hai\n", encoding="utf-8")
    assert cli.main(["sentences", "--sku", "demo_sku", "--as-of", "2025-01-08",
                     "--file", str(path), "--source", "Synthetic test"]) == 0
    assert [row[2] for row in state.ingested] == ["câu tổng hợp", "câu thứ hai"]
    output = capsys.readouterr().out
    assert len(state.calls) == 2 and "near_hits=" in output
    assert all(f"{session_id}:t01.a1 abstain LLM_OFF" in output for session_id in state.stored)


def test_shadow_refuses_mixed_backend_before_ingest_or_calls(boundary, capsys):
    _, state = boundary
    location = paths.CACHE_DIR / "aa" / ("a" * 64 + ".json")
    location.parent.mkdir(parents=True)
    location.write_text('{"backend":"api"}', encoding="utf-8")
    assert cli.main(["shadow", "--kera"]) == 2
    assert capsys.readouterr().err.strip() == (
        "cache/llm holds api rows: one backend per committed cache (contracts section 3)")
    assert state.warm == state.s1 == state.inits == []
    assert not paths.ledger_db().exists()


def test_shadow_refuses_clip_and_never_touches_present_dir(boundary, monkeypatch, capsys):
    root, state = boundary
    forbidden = root / "must-not-read"
    monkeypatch.setattr(settings, "PRESENT_DIR", str(forbidden))
    original = Path.stat
    def stat(path, *args, **kwargs):
        assert not path.is_relative_to(forbidden), "PRESENT_DIR was touched"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    state.clip = True
    assert cli.main(["shadow", "--kera"]) == 2
    assert "shadow refuses clip- targets" in capsys.readouterr().err
    assert state.warm == state.s1 == state.inits == []
    assert not paths.ledger_db().exists()


def test_shadow_s1_only_no_judge_or_negq_and_numeric_summary(boundary, capsys):
    _, state = boundary
    assert cli.main(["shadow", "--kera", "--s1-only"]) == 0
    assert state.s1 == [("kera-reenact-2025-01-08", "t01", "shadow"),
                        ("kera-reenact-2025-01-08", "t02", "shadow")]
    assert state.warm == state.calls == state.inits == []
    captured = capsys.readouterr()
    assert "targets=1 calls_made=0 cache_hits=0 offline_misses=0 errors=0 elapsed_s=" in captured.out
    assert captured.err == ""
    assert not paths.ledger_db().exists()


def test_shadow_sentences_json_path_with_space(boundary, tmp_path):
    _, state = boundary
    path = tmp_path / "constructed sentences.json"
    path.write_text(json.dumps(dict(sku_id="demo_sku", as_of="2025-01-08", source="Synthetic",
                                    sentences=["first", "second"])), encoding="utf-8")
    assert cli.main(["shadow", "--sentences", str(path)]) == 0
    assert len(state.warm) == 2 and [p for _, p in state.warm] == ["shadow", "shadow"]
    assert state.inits == state.calls == [] and not paths.ledger_db().exists()


def test_shadow_missing_sentence_fixture_prints_one_note(boundary, tmp_path, capsys):
    assert cli.main(["shadow", "--sentences", str(tmp_path / "missing.json")]) == 0
    assert capsys.readouterr().out.count("note:") == 1


def test_shadow_rejects_clip_regression_before_conversion(boundary):
    root, state = boundary
    path = root / "eval" / "regressions.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps(dict(item_id="item", sku_id="demo_sku", as_of="2025-01-08",
                                    text="synthetic", session_id="clip-a1")) + "\n", encoding="utf-8")
    assert cli.main(["shadow", "--regressions"]) == 2
    assert state.warm == state.s1 == state.inits == []


def test_case_prints_trace_without_ledger(boundary, capsys):
    _, state = boundary
    paths.GOLDEN_CASES.parent.mkdir(parents=True)
    paths.GOLDEN_CASES.write_text('{"case_id":"one","text":"synthetic"}\n', encoding="utf-8")
    assert cli.main(["case", "--id", "one"]) == 0
    output = capsys.readouterr().out
    assert '"step": "A"' in output and "t01 abstain" in output
    assert state.inits == state.calls == []


def test_v1_selection_uses_only_approved_active_law_compiled(boundary, monkeypatch):
    import rules.registry

    candidates = [SimpleNamespace(rule_id=name, meta=SimpleNamespace(
        approved_by=approval, origin=origin, status=status)) for name, approval, origin, status in [
            ("keep", "owner", "law_compiled", "active"),
            ("candidate", None, "law_compiled", "active"),
            ("adaptive", "owner", "t5", "active"),
            ("quarantined", "owner", "law_compiled", "quarantined")]]
    calls = []
    monkeypatch.setattr(rules.registry, "all_rules", lambda: candidates)
    monkeypatch.setattr(rules.registry, "load_pack", lambda ids, **kw: calls.append((ids, kw)))
    _V1_PACK()
    assert calls == [(["keep"], {"mode": "measure"})]


def test_shadow_counts_completed_batched_calls_hits_and_offline_misses(boundary, monkeypatch, capsys):
    _, state = boundary
    monkeypatch.setattr(llm, "_backend", lambda *args: llm._Transport(response={"ok": True}))
    def warm(value, pack, s1_only):
        requests = [dict(kind=kind, purpose="shadow", model="synthetic-" + kind,
                         effort="none", schema_name="test_response",
                         messages=[dict(role="user", content="synthetic batch")])
                    for kind in ("s1", "t1", "t2", "negq")]
        with llm.run_scope(measuring=True):
            llm.map_calls(requests, parallel=4)
    monkeypatch.setattr(cli, "_warm", warm)
    assert cli.main(["shadow", "--kera"]) == 0
    assert "calls_made=4 cache_hits=0 offline_misses=0 errors=0" in capsys.readouterr().out
    monkeypatch.setattr(settings, "OFFLINE", 1)
    assert cli.main(["shadow", "--kera"]) == 0
    assert "calls_made=0 cache_hits=4 offline_misses=0 errors=0" in capsys.readouterr().out
    for location in paths.CACHE_DIR.glob("*/*.json"):
        location.unlink()
    assert cli.main(["shadow", "--kera"]) == 1
    assert "calls_made=0 cache_hits=0 offline_misses=4 errors=0" in capsys.readouterr().out
    assert state.inits == [] and not paths.ledger_db().exists()


def test_shadow_s1_only_observes_only_s1_results(boundary, monkeypatch, capsys):
    monkeypatch.setattr(llm, "_backend", lambda *args: llm._Transport(response={"ok": True}))
    def extract(value, turn, previous, ctx, purpose, cache_read=True):
        llm.call("s1", purpose, "synthetic-s1", "none", "test_response",
                 [dict(role="user", content=turn.turn_id)], cache_read=cache_read)
        return []
    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=extract))
    with llm.observe_results() as results:
        assert cli.main(["shadow", "--kera", "--s1-only"]) == 0
    assert [result.kind for result in results] == ["s1", "s1"]
    assert "calls_made=2 cache_hits=0 offline_misses=0 errors=0" in capsys.readouterr().out


@pytest.mark.parametrize("s1_only", [False, True])
def test_shadow_with_cached_s1_and_missing_judge_reports_incomplete(monkeypatch, tmp_path, capsys,
                                                                 s1_only):
    from pipeline import context, extract, router
    from rules.registry import load_pack

    monkeypatch.setattr(settings, "OFFLINE", 1)
    monkeypatch.setattr(settings, "LLM_BACKEND", "codex")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(cli, "config_line", lambda: "cfg=synthetic")
    value = session("case-shadow-incomplete")
    value.turns = value.turns[:1]
    ctx = context.Context(sku_id="demo_sku", category_norm="thuc_pham_bo_sung",
                          as_of=value.as_of, dossier_state="PRESENT", dossier_hash="synthetic",
                          P=[dict(permitted_id="demo_sku:cong_dung:1", axis_group="cong_dung",
                                  text="permitted claim", confidence="high", source_field="test")])
    monkeypatch.setattr(context, "build_context", lambda *args: ctx)
    monkeypatch.setattr(cli, "_shadow_targets", lambda args: [value])
    monkeypatch.setattr(cli, "_v1_pack", lambda: load_pack([], mode="measure"))

    def forbidden(*args, **kwargs):
        pytest.fail("Offline shadow must not call a backend or access the ledger")

    monkeypatch.setattr(llm, "_backend", forbidden)
    monkeypatch.setitem(sys.modules, "ledger", SimpleNamespace(
        current_pack=forbidden, init_db=forbidden, record_llm_call=forbidden))
    request = extract._request(value, value.turns[0], None, "shadow", True)
    missed = llm.call(**request)
    assert missed.error.kind == "off"
    cached = missed.model_copy(update={"error": None, "response": dict(atoms=[dict(
        span=value.turns[0].text, axis="cong_dung", speaker_role="khong_ro", role_span=None,
        subject_is_product=True)])})
    location = llm.cache_path(paths.CACHE_DIR, cached.request_sha256)
    location.parent.mkdir(parents=True)
    location.write_text(json.dumps(llm._record(cached), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setitem(sys.modules, "pipeline.judge", None)
    original = router.import_module
    judge_attempts = []
    def load(name):
        if name == "pipeline.judge":
            judge_attempts.append(name)
        return original(name)
    monkeypatch.setattr(router, "import_module", load)

    args = ["shadow", "--golden"] + (["--s1-only"] if s1_only else [])
    with llm.observe_results() as results:
        assert cli.main(args) == (0 if s1_only else 1)
    captured = capsys.readouterr()
    assert "targets=1 calls_made=0 cache_hits=1 offline_misses=0 errors=0" in captured.out
    assert [(row.kind, row.cache_hit, row.error) for row in results] == [("s1", True, None)]
    if s1_only:
        assert judge_attempts == [] and captured.err == ""
    else:
        assert judge_attempts == ["pipeline.judge"]
        assert "incomplete_targets=1" in captured.err and "full shadow" in captured.err
    assert not paths.ledger_db().exists()


def test_shadow_reports_real_errors_and_nonzero_exit(boundary, monkeypatch, capsys):
    monkeypatch.setattr(llm, "_backend", lambda *args: llm._Transport(
        error=llm._error("timeout", "synthetic timeout")))
    def warm(*args):
        llm.call("s1", "shadow", "synthetic", "none", "test_response", [])
    monkeypatch.setattr(cli, "_warm", warm)
    assert cli.main(["shadow", "--kera"]) == 1
    assert "calls_made=1 cache_hits=0 offline_misses=0 errors=1" in capsys.readouterr().out


def test_shadow_does_not_swallow_collaborator_runtime_failure(boundary, monkeypatch):
    def warm(*args):
        raise RuntimeError("synthetic collaborator failure")
    monkeypatch.setattr(cli, "_warm", warm)
    with pytest.raises(RuntimeError, match="collaborator failure"):
        cli.main(["shadow", "--kera"])


def test_live_summary_counts_actual_results(boundary, monkeypatch, capsys):
    _, state = boundary
    state.stored["fixture"] = session("fixture")
    monkeypatch.setattr(llm, "_backend", lambda *args: llm._Transport(response={"ok": True}))
    def process(*args, **kwargs):
        request = dict(kind="s1", purpose="shadow", model="synthetic", effort="none",
                       schema_name="test_response", messages=[])
        llm.call(**request)
        llm.call(**request)
        return result(state.stored["fixture"])
    monkeypatch.setitem(sys.modules, "pipeline.runner", SimpleNamespace(process_session=process))
    assert cli.main(["process", "--session", "fixture", "--purpose", "shadow"]) == 0
    assert "calls_made=1 cache_hits=1 offline_misses=0 errors=0" in capsys.readouterr().out
