"""CI checks for isolation, network protection, and rehearsal helpers."""

import asyncio
import importlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from .harness import Harness, export_head, git, install_process_guard, isolated_imports, require
from .network import NetworkBlocked, install_guard
from .support import DemoReport, expected_agreement, read_expected, select_pr1_target


@pytest.mark.parametrize("address", [("127.0.0.1", 80), ("localhost", 80), ("192.0.2.1", 443), ("::1", 80)])
@pytest.mark.parametrize("method", ["connect", "connect_ex", "create_connection"])
def test_outgoing_connections_are_blocked(address, method):
    with pytest.raises(NetworkBlocked):
        if method == "create_connection":
            socket.create_connection(address)
        else:
            with socket.socket() as sock:
                if method == "connect":
                    sock.connect(address)
                else:
                    sock.connect_ex(address)


def test_asyncio_self_pipe_works_and_does_not_allow_loopback():
    async def coroutine():
        await asyncio.sleep(0)
        return 7

    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(coroutine()) == 7
    finally:
        loop.close()
    with socket.socket() as sock, pytest.raises(NetworkBlocked):
        sock.connect(("127.0.0.1", 80))


def test_socketpair_can_transfer_data_under_the_guard():
    left, right = socket.socketpair()
    try:
        left.sendall(b"demo")
        assert right.recv(4) == b"demo"
    finally:
        left.close()
        right.close()


def test_socketpair_fallback_has_one_connect_allowance(monkeypatch):
    connections = []
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: connections.append(address))

    def fallback():
        with socket.socket() as sock:
            sock.connect(("127.0.0.1", 12345))
            with pytest.raises(NetworkBlocked):
                sock.connect(("127.0.0.1", 12345))
        return "synthetic pair"

    monkeypatch.setattr(socket, "socketpair", fallback)
    install_guard(monkeypatch)
    assert socket.socketpair() == "synthetic pair"
    assert connections == [("127.0.0.1", 12345)]
    with socket.socket() as sock, pytest.raises(NetworkBlocked):
        sock.connect(("127.0.0.1", 12345))


def test_http_uses_asgi_transport_without_opening_a_socket():
    from fastapi import FastAPI
    import httpx

    app = FastAPI()

    @app.get("/synthetic")
    def synthetic():
        return {"ok": True}

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo.invalid") as client:
            return await client.get("/synthetic")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def _two_atoms():
    atoms = [{"atom_id": "t01.a1", "near_hits": []},
             {"atom_id": "t01.a2", "near_hits": [{"pattern": "phòng … bệnh"}]}]
    verdicts = [{"atom_key": "demo:t01.a1", "verdict": "abstain", "reason_code": "LLM_NEI"},
                {"atom_key": "demo:t01.a2", "verdict": "abstain", "reason_code": "DOSSIER_EMPTY"}]
    return atoms, verdicts


def test_pr1_selects_the_unique_matching_atom_without_dropping_the_other():
    atoms, verdicts = _two_atoms()
    assert select_pr1_target("Synthetic two-atom sentence.", atoms, verdicts)["atom_id"] == "t01.a2"
    assert len(atoms) == 2


@pytest.mark.parametrize("case", ["none", "two", "wrong_reason", "wrong_colour"])
def test_pr1_rejects_zero_or_ambiguous_targets(case):
    atoms, verdicts = _two_atoms()
    if case == "none":
        atoms[1]["near_hits"] = []
    elif case == "two":
        atoms[0]["near_hits"] = [{"pattern": "phòng … bệnh"}]
        verdicts[0]["reason_code"] = "DOSSIER_EMPTY"
    elif case == "wrong_reason":
        verdicts[1]["reason_code"] = "LLM_NEI"
    else:
        verdicts[1]["verdict"] = "yellow"
    with pytest.raises(AssertionError, match="Synthetic sentence"):
        select_pr1_target("Synthetic sentence", atoms, verdicts)


def test_missing_module_skips_only_its_own_name(monkeypatch):
    def absent(name):
        raise ModuleNotFoundError("synthetic missing module", name=name)
    monkeypatch.setattr(importlib, "import_module", absent)
    with pytest.raises(pytest.skip.Exception, match="not ready: pipeline.runner"):
        require("pipeline.runner")


def test_missing_dependency_is_not_mistaken_for_missing_module(monkeypatch):
    def broken(name):
        raise ModuleNotFoundError("synthetic dependency failure", name="synthetic_dependency")
    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError) as error:
        require("pipeline.runner")
    assert error.value.name == "synthetic_dependency"


def test_existing_module_is_used_without_capability_probing(monkeypatch):
    module = SimpleNamespace(value="synthetic")
    monkeypatch.setattr(importlib, "import_module", lambda name: module)
    assert require("pipeline.runner") is module


def test_expected_file_errors_never_reveal_its_location(tmp_path):
    for path in (None, tmp_path / "absent.json"):
        with pytest.raises(AssertionError) as error:
            read_expected(path)
        assert str(tmp_path) not in str(error.value)
    malformed = tmp_path / "invalid.json"
    malformed.write_text("{broken", encoding="utf-8")
    with pytest.raises(AssertionError, match="unreadable or invalid JSON"):
        read_expected(malformed)


@pytest.mark.parametrize("expected", [None, [], {}, {"runs": []}, {"runs": {}},
    {"runs": {"2025-01-08": {"turns": [None]}}},
    {"runs": {"2025-01-08": {"turns": [{"turn": [], "atoms": []}]}}},
    {"runs": {"2025-01-08": {"turns": [{"turn": "t01", "atoms": [], "proposed_turn_verdict": []}]}}}])
def test_changed_expected_shape_is_reported_without_crashing(expected):
    session = {"turns": [], "atoms": []}
    assert expected_agreement(expected, session, [], lambda text: (text, list(range(len(text))))) == {"status": "expected file shape changed"}


def test_soft_agreement_uses_overlap_and_reports_counts_only():
    from textnorm import match_key

    session = {"turns": [{"turn_id": "t01", "text": "Hạt mây dịu."}],
               "atoms": [{"atom_id": "t01.a1", "turn_id": "t01", "start": 0, "end": 11}]}
    expected = {"runs": {"2025-01-08": {"turns": [{"turn": "t01", "proposed_turn_verdict": "green",
        "atoms": [{"span": "hạt mây", "proposed_verdict": "green"}]}]}}}
    rows = [{"atom_key": "demo:t01.a1", "verdict": "green", "reason_code": None}]
    result = expected_agreement(expected, session, rows, match_key)
    assert result == {"status": "compared", "turn_matches": 1, "turn_total": 1,
                      "atom_matches": 1, "atom_total": 1, "reason_counts": {}}
    assert "Hạt" not in json.dumps(result, ensure_ascii=False)


def test_report_includes_configuration_and_separate_t06_line(tmp_path):
    report = DemoReport(tmp_path / "report.md", config="cfg=synthetic offline=1")
    report.add("January", "PASS")
    report.add("t06 (report only)", "FAIL", "observed abstain; reason_code=VOTES_SPLIT")
    report.add("Recall", "SKIP", "not ready: pipeline.dossier_events")
    report.soft.append({"search": "0.125 ms"})
    report.write()
    text = report.path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "cfg=synthetic offline=1"
    assert "January | PASS" in text and "t06 (report only) | FAIL" in text
    assert "not ready: pipeline.dossier_events" in text and "0.125 ms" in text


def test_export_ignores_uncommitted_cache_and_uses_an_independent_index(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    cache = source / "cache" / "llm"
    cache.mkdir(parents=True)
    (cache / "committed.json").write_text('{"row": 1}', encoding="utf-8")
    (source / "core").mkdir()
    (source / "core" / "__init__.py").write_text("", encoding="utf-8")
    git(source, "init", "-q")
    git(source, "config", "user.name", "Demo Test")
    git(source, "config", "user.email", "demo@example.invalid")
    git(source, "add", "--all")
    git(source, "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic source")
    (cache / "committed.json").write_text('{"row": 2}', encoding="utf-8")
    (cache / "uncommitted.json").write_text('{}', encoding="utf-8")
    before_index = (source / ".git" / "index").read_bytes()
    destination = tmp_path / "copy"
    destination.mkdir()
    export_head(source, destination)
    assert json.loads((destination / "cache/llm/committed.json").read_text(encoding="utf-8")) == {"row": 1}
    assert not (destination / "cache/llm/uncommitted.json").exists()
    assert (source / ".git" / "index").read_bytes() == before_index
    assert (destination / ".git").is_dir()
    assert (destination / ".git").resolve() != (source / ".git").resolve()


def test_import_isolation_restores_the_original_modules(tmp_path):
    import core

    original = core
    package = tmp_path / "core"
    package.mkdir()
    (package / "__init__.py").write_text("synthetic = True\n", encoding="utf-8")
    with isolated_imports(tmp_path):
        copied = importlib.import_module("core")
        assert copied is not original and copied.synthetic is True
        assert Path(copied.__file__).parent == package
    assert sys.modules["core"] is original


def test_process_guard_blocks_codex_and_real_git_mutations(monkeypatch, tmp_path):
    install_process_guard(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="only local Git"):
        subprocess.run(["codex", "exec", "synthetic"], cwd=tmp_path)
    with pytest.raises(RuntimeError, match="only local Git"):
        subprocess.run(["git", "add", "--all"], cwd=tmp_path.parent)
    with pytest.raises(RuntimeError, match="Git network"):
        subprocess.run(["git", "fetch", "origin"], cwd=tmp_path)
    with pytest.raises(RuntimeError, match="outside index"):
        subprocess.run(["git", "status"], cwd=tmp_path, env={"GIT_INDEX_FILE": str(tmp_path.parent / "index")})
    with pytest.raises(RuntimeError, match="only local Git"):
        subprocess.run(["codex", "exec", "synthetic", "--version"], cwd=tmp_path)


def test_demo_environment_pins_the_copy_and_empty_local_cache(demo_environment):
    from core.config import Settings, settings
    from core import paths

    root = demo_environment.root
    assert Path.cwd() == paths.REPO_ROOT == root
    for value in (settings.LEDGER_DB, settings.EVAL_DIR, settings.LLM_LOCAL_CACHE_DIR):
        assert Path(value).is_relative_to(root)
    assert list(Path(settings.LLM_LOCAL_CACHE_DIR).iterdir()) == []
    assert settings.LLM_CACHE_DIR is settings.PRESENT_DIR is None
    assert settings.OFFLINE == 1
    assert settings.JUDGE_MODE == "two"
    assert settings.PUBLIC_MODE == settings.VIDEO_MODE == 0
    assert settings.REGRESSION_AUTOCOMMIT == settings.LOOP_OPEN_PR == settings.LOOP_DRY == 0
    defaults = Settings(environ={})
    assert settings.S1_MODEL == defaults.S1_MODEL
    assert settings.JUDGE_MODEL == defaults.JUDGE_MODEL
    assert settings.DRAFT_MODEL == defaults.DRAFT_MODEL
    assert (root / ".git").is_dir()


def test_synthetic_candidate_loads_only_for_measurement(tmp_path):
    import shutil
    from core.models import AtomView, RuleMeta
    from rules.registry import load_pack

    directory = tmp_path / "candidate"
    (directory / "rules").mkdir(parents=True)
    fixtures = Path(__file__).parent / "fixtures"
    for suffix in (".py", ".meta.json"):
        shutil.copyfile(fixtures / ("H3_DEMO_R04" + suffix), directory / "rules" / ("H3_DEMO_R04" + suffix))
    meta = RuleMeta.model_validate(json.loads((fixtures / "H3_DEMO_R04.meta.json").read_text(encoding="utf-8")))
    assert meta.origin == "t5" and meta.approved_by is None
    pack = load_pack([meta.id], mode="measure", candidate_dir=directory)
    view = AtomView(text="Demo mây làm tan mọi bệnh.", key="demo mây làm tan mọi bệnh",
                    axis="cong_dung", speaker_role="kol", category_norm="thuc_pham_bo_sung",
                    as_of="2026-10-31", turn_text="Demo mây làm tan mọi bệnh.")
    assert [hit.rule_id for hit in pack.check_atom(view)] == [meta.id]
    with pytest.raises(ValueError, match="Candidates are permitted only in measure mode"):
        load_pack([meta.id], mode="live", candidate_dir=directory)


def test_event_reader_follows_every_page(monkeypatch, tmp_path):
    calls = []

    def events_since(cursor):
        calls.append(cursor)
        return {0: [{"id": 1}], 1: [{"id": 2}], 2: []}[cursor]

    monkeypatch.setitem(sys.modules, "ledger", SimpleNamespace(events_since=events_since))
    h = Harness(tmp_path, monkeypatch, DemoReport(tmp_path / "report.md"))
    assert h.events() == [{"id": 1}, {"id": 2}]
    assert calls == [0, 1, 2]


def test_copied_ledger_views_use_only_the_effective_verdict(demo_environment):
    import ledger
    from core.config import settings
    from core.models import Atom, Session, Verdict

    h = demo_environment
    ledger.init_db(settings.LEDGER_DB)
    fixture = h.root / "fixtures"
    session = Session.model_validate(json.loads((fixture / "session_fake.json").read_text(encoding="utf-8")))
    ledger.add_session(session)
    atoms = [Atom.model_validate(row) for row in json.loads((fixture / "atoms_fake.json").read_text(encoding="utf-8"))]
    ledger.add_atoms(session.session_id, atoms)
    rows = [Verdict.model_validate(row) for row in json.loads((fixture / "verdicts_fake.json").read_text(encoding="utf-8"))]
    ledger.append_verdicts(rows)
    assert h.session(session.session_id)["flags"] == []
    assert len(h.verdicts(session.session_id)) == 5
    next_pack = ledger.create_pack_version([], [], "merge", approved_by="hungkhuu404")
    previous = next(iter(h.verdicts(session.session_id)))
    replacement = Verdict.model_validate({**previous, "verdict_id": None,
        "rule_pack_version": next_pack.version, "supersedes": previous["verdict_id"]})
    ledger.append_verdicts([replacement])
    assert len(h.verdicts(session.session_id)) == 5
    selected = next(row for row in h.verdicts(session.session_id) if row["atom_key"] == previous["atom_key"])
    assert selected["supersedes"] == previous["verdict_id"]


def test_pr1_input_fixture_has_the_fixed_three_sentences():
    value = json.loads((Path(__file__).parent / "pr1_sentences.json").read_text(encoding="utf-8"))
    assert value["sku_id"] == "seloderma_psc300_plus"
    assert value["as_of"] == "2026-10-31"
    assert value["sentences"] == ["Dùng đều đặn giúp dự phòng các bệnh mãn tính",
                                   "Uống mỗi ngày để dự phòng tái phát bệnh cũ",
                                   "Sản phẩm hỗ trợ dự phòng nhiều bệnh thường gặp"]
