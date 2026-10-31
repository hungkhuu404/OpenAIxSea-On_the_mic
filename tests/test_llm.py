"""LLM boundary contracts, using synthetic requests and fake transports only."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import paths
from core.config import set_override
from pipeline import llm


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    root = tmp_path / "repo space.dot"
    root.mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache" / "llm")
    monkeypatch.setattr(paths, "LOCAL_DIR", root / ".local")
    set_override("LLM_LOCAL_CACHE_DIR", str(root / ".local" / "cache"))
    set_override("LLM_BACKEND", "codex")
    set_override("OFFLINE", 0)
    calls, records = [], []
    monkeypatch.setitem(sys.modules, "ledger", SimpleNamespace(record_llm_call=records.append))

    def process(args, **kwargs):
        calls.append((args, kwargs))
        output = Path(args[args.index("-o") + 1])

        class Process:
            pid = 123456
            returncode = 0

            def communicate(self, input=None, timeout=None):
                self.input = input
                output.write_text('{"ok": true}', encoding="utf-8")
                return b"", b""

        proc = Process()
        calls[-1] += (proc,)
        return proc

    monkeypatch.setattr(llm.subprocess, "Popen", process)
    return root, calls, records


def request(**changes):
    values = dict(kind="s1", purpose="live", model="synthetic-model", effort="none",
                  schema_name="test_response", messages=[{"role": "user", "content": "Sản phẩm giả"}])
    return values | changes


def rows(root):
    return list(root.rglob("*.json"))


def test_cache_hit_and_live_log(boundary):
    root, calls, records = boundary
    first = llm.call(**request())
    second = llm.call(**request())
    assert first.response == second.response == {"ok": True}
    assert not first.cache_hit and second.cache_hit
    assert first.request_sha256 == second.request_sha256
    assert len(calls) == 1 and len(records) == 2
    assert records[0].purpose == "live"
    assert not (root / ".local" / "llm_calls.jsonl").exists()
    assert llm.cache_path(root / ".local" / "cache", first.request_sha256).exists()


@pytest.mark.parametrize("change", [dict(model="other"), dict(effort="low"),
    dict(messages=[{"role": "user", "content": "Khác"}])])
def test_request_identity(boundary, change):
    assert llm.call(**request()).request_sha256 != llm.call(**request(**change)).request_sha256


def test_shadow_promotion_and_measuring_logging(boundary):
    root, calls, records = boundary
    first = llm.call(**request())
    promoted = llm.call(**request(purpose="shadow"))
    with llm.run_scope(measuring=True):
        hit = llm.call(**request())
    assert promoted.cache_hit and hit.cache_hit and len(calls) == 1
    assert llm.cache_path(paths.CACHE_DIR, first.request_sha256).exists()
    lines = (root / ".local" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["purpose"] for line in lines] == ["shadow", "live"]
    assert len(records) == 1
    assert not paths.ledger_db().exists()


def test_offline_miss_does_not_log_or_start_backend(boundary):
    root, calls, records = boundary
    set_override("OFFLINE", 1)
    result = llm.call(**request())
    assert result.response is None and result.error.kind == "off"
    assert result.error.detail == "offline cache miss"
    assert calls == records == [] and rows(root) == []
    assert not (root / ".local").exists()


def test_offline_hit_and_fresh_does_not_overwrite(boundary):
    root, calls, records = boundary
    first = llm.call(**request())
    location = llm.cache_path(root / ".local" / "cache", first.request_sha256)
    before = location.read_bytes()
    assert not llm.call(**request(cache_read=False)).cache_hit
    assert location.read_bytes() == before and len(calls) == 2
    set_override("OFFLINE", 1)
    assert llm.call(**request()).cache_hit
    assert len(calls) == 2 and len(records) == 3


@pytest.mark.parametrize("purpose", ["live", "shadow"])
def test_malformed_cached_response_is_schema_error_without_promotion(boundary, monkeypatch, purpose):
    root, calls, records = boundary
    args = request()
    payload = {name: args[name] for name in ("model", "effort", "schema_name", "messages")}
    payload.update(backend="codex", schema=llm._schema(args["schema_name"]))
    sha = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    first = llm.call(**args)
    assert first.request_sha256 == sha
    location = llm.cache_path(root / ".local" / "cache", sha)
    row = json.loads(location.read_text(encoding="utf-8"))
    row["response"] = {}
    location.write_text(json.dumps(row), encoding="utf-8")
    before = {path: path.read_bytes() for path in rows(root)}
    set_override("OFFLINE", 1)

    def forbidden(*args, **kwargs):
        pytest.fail("Malformed cache response must not invoke the backend")

    monkeypatch.setattr(llm, "_backend", forbidden)
    with llm.observe_results() as observed:
        result = llm.call(**request(purpose=purpose))
    assert observed == [result]
    assert result.cache_hit and result.request_sha256 == sha
    assert result.response is None and result.error.kind == "schema"
    assert "required property" in result.error.detail
    assert result.latency_ms >= 0 and len(calls) == 1
    assert {path: path.read_bytes() for path in rows(root)} == before
    assert not llm.cache_path(paths.CACHE_DIR, sha).exists()
    if purpose == "live":
        assert len(records) == 2
        logged = records[-1].model_dump(mode="json")
    else:
        assert len(records) == 1
        lines = (root / ".local" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        logged = json.loads(lines[0])
    assert logged["request_sha256"] == sha and logged["purpose"] == purpose
    assert logged["response"] is None and logged["error"]["kind"] == "schema"
    assert logged["latency_ms"] == result.latency_ms


def test_scope_is_thread_local_and_preserves_environment(boundary):
    import os
    before = dict(os.environ)
    with llm.run_scope(offline=True):
        assert llm.call(**request()).error.kind == "off"
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(llm.call, **request()).result().response == {"ok": True}
    assert dict(os.environ) == before


def test_observations_include_hits_offline_misses_and_errors_and_reset(boundary, monkeypatch):
    with llm.observe_results() as observed:
        first = llm.call(**request())
        hit = llm.call(**request())
        with llm.run_scope(offline=True):
            missed = llm.call(**request(cache_read=False))
        monkeypatch.setattr(llm, "_backend", lambda *args: llm._Transport(
            error=llm._error("timeout", "synthetic timeout")))
        failed = llm.call(**request(cache_read=False))
    llm.call(**request())
    assert observed == [first, hit, missed, failed]
    assert [r.cache_hit for r in observed] == [False, True, False, False]
    assert [r.error.kind if r.error else None for r in observed] == [None, None, "off", "timeout"]


def test_observations_copy_into_batches_but_isolate_independent_threads(boundary):
    with llm.observe_results() as outer:
        with llm.run_scope(offline=True), llm.observe_results() as batch:
            results = llm.map_calls([request(model=f"batch-{i}") for i in range(8)], parallel=4)
        def independent():
            with llm.observe_results() as own, llm.run_scope(offline=True):
                result = llm.call(**request(model="independent"))
            return own, result
        with ThreadPoolExecutor(1) as pool:
            own, other = pool.submit(independent).result()
    assert len(outer) == len(batch) == 8
    assert {r.request_sha256 for r in outer} == {r.request_sha256 for r in results}
    assert own == [other] and other not in outer


def test_fresh_scope_writes_only_local_cache_and_resets(boundary):
    root, calls, records = boundary
    original = llm.call(**request(purpose="shadow"))
    committed = llm.cache_path(paths.CACHE_DIR, original.request_sha256)
    before = committed.read_bytes()
    with llm.run_scope(local_writes=True):
        refreshed = llm.call(**request(purpose="shadow", cache_read=False))
        new = llm.call(**request(purpose="shadow", model="fresh-only", cache_read=False))
    assert refreshed.request_sha256 == original.request_sha256
    assert committed.read_bytes() == before
    assert llm.cache_path(root / ".local" / "cache", new.request_sha256).is_file()
    assert not llm.cache_path(paths.CACHE_DIR, new.request_sha256).exists()
    llm.call(**request(purpose="shadow", model="after-scope"))
    assert len(list(paths.CACHE_DIR.glob("*/*.json"))) == 2


def test_local_write_scope_does_not_redirect_an_independent_thread(boundary):
    root, _, _ = boundary
    with llm.run_scope(local_writes=True):
        own = llm.call(**request(purpose="shadow", model="local-only"))
        with ThreadPoolExecutor(1) as pool:
            other = pool.submit(llm.call, **request(purpose="shadow", model="parallel-shadow")).result()
    assert llm.cache_path(root / ".local" / "cache", own.request_sha256).exists()
    assert not llm.cache_path(paths.CACHE_DIR, own.request_sha256).exists()
    assert llm.cache_path(paths.CACHE_DIR, other.request_sha256).exists()


def test_local_write_scope_does_not_promote_a_local_hit(boundary):
    root, _, _ = boundary
    original = llm.call(**request())
    with llm.run_scope(local_writes=True):
        hit = llm.call(**request(purpose="shadow"))
    assert hit.cache_hit and hit.request_sha256 == original.request_sha256
    assert not llm.cache_path(paths.CACHE_DIR, hit.request_sha256).exists()
    assert llm.cache_path(root / ".local" / "cache", hit.request_sha256).exists()


def test_event_loop_rejected_and_to_thread_copies_scope(boundary):
    async def check():
        with pytest.raises(RuntimeError, match="blocking: use acall"):
            llm.call(**request())
        assert (await llm.acall(**request())).response == {"ok": True}
        with llm.run_scope(offline=True):
            result = await asyncio.to_thread(llm.call, **request(cache_read=False))
            assert result.error.kind == "off"
    asyncio.run(check())


@pytest.mark.parametrize("command, expected", [
    ("cmd /c codex", ["cmd", "/c", "codex"]),
    ('"C:\\Program Files\\Codex\\codex.exe"', ["C:\\Program Files\\Codex\\codex.exe"]),
    ("/usr/bin/codex", ["/usr/bin/codex"]),
    ('"/opt/my tools/codex"', ["/opt/my tools/codex"]),
])
def test_command_parser(command, expected):
    assert llm.command_args(command) == expected


def test_codex_exact_argv_and_utf8_stdin(boundary):
    _, calls, _ = boundary
    set_override("CODEX_CMD", "cmd /c codex")
    llm.call(**request())
    args, kwargs, proc = calls[0]
    assert args[:3] == ["cmd", "/c", "codex"]
    assert args[3:13] == ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                         "-m", "synthetic-model", "-c", "model_reasoning_effort=none", "--output-schema"]
    assert args[14] == "-o" and args[16:] == ["-"]
    assert "Sản phẩm giả" in proc.input.decode("utf-8")
    assert all("Sản phẩm" not in str(arg) for arg in args)
    assert kwargs["shell"] is False


@pytest.mark.parametrize("mode, expected", [
    ("exit", "nonzero_exit"), ("missing", "no_output"), ("empty", "no_output"),
    ("parse", "parse"), ("schema", "schema"), ("timeout", "timeout"),
])
def test_transport_failures_retry_and_never_cache(boundary, monkeypatch, mode, expected):
    root, _, records = boundary
    attempts = []

    def fake(args, **kwargs):
        attempts.append(args)
        output = Path(args[args.index("-o") + 1])

        class Process:
            pid = 123456
            returncode = 1 if mode == "exit" else 0

            def communicate(self, input=None, timeout=None):
                if mode == "timeout" and input is not None:
                    raise subprocess.TimeoutExpired(args, timeout)
                if mode not in ("missing", "timeout", "exit"):
                    output.write_text({"empty": "", "parse": "not json", "schema": "{}"}[mode], encoding="utf-8")
                return b"", b"failed sk-fakeSecret Bearer abcDEF123456"

        return Process()

    monkeypatch.setattr(llm.subprocess, "Popen", fake)
    monkeypatch.setattr(llm, "_terminate_tree", lambda proc: None)
    for _ in range(2):
        result = llm.call(**request(timeout_s=.01))
        assert result.error.kind == expected and result.response is None
        assert "fakeSecret" not in result.error.stderr_tail
        assert "abcDEF123456" not in result.error.stderr_tail
    assert len(attempts) == len(records) == 2
    assert rows(root) == []


def test_validation_and_map_calls(boundary):
    for change in (dict(kind="bad"), dict(purpose="bad"), dict(schema_name="../test_response")):
        with pytest.raises(ValueError):
            llm.call(**request(**change))
    results = llm.map_calls([request(model="one"), request(model="two")], parallel=1)
    assert [r.model for r in results] == ["one", "two"]
    with pytest.raises(ValueError):
        llm.map_calls([], parallel=0)


def test_diagnose_does_not_write(boundary, capsys, monkeypatch, tmp_path):
    # Exercise the built-in fallback regardless of which step schemas have landed.
    monkeypatch.setattr(llm, "SCHEMA_DIR", tmp_path / "no step schemas")
    root, calls, records = boundary
    assert llm.main(["diagnose", "--kind", "s1"]) == 0
    text = capsys.readouterr().out
    for label in ("backend", "model", "effort", "schema", "request_sha256", "exit_code",
                  "error_kind", "output_file_present", "parse_result", "stderr_tail", "latency_ms"):
        assert label + "=" in text
    assert len(calls) == 1 and records == [] and rows(root) == []
    set_override("OFFLINE", 1)
    assert llm.main(["diagnose", "--kind", "t1"]) == 1
    assert len(calls) == 1


@pytest.fixture
def api_transport(monkeypatch):
    import httpx
    import openai
    requests = []
    state = {"status": None, "body": '{"ok": true}'}
    set_override("OPENAI_API_KEY", "synthetic-test-key")

    class Client:
        def __init__(self, **kwargs):
            self.responses = self
            self.options = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            requests.append((kwargs, self.options))
            if state["status"] is not None:
                response = httpx.Response(state["status"], request=httpx.Request("POST", "https://example.invalid"))
                raise openai.APIStatusError("synthetic failure", response=response, body=None)
            return SimpleNamespace(output_text=state["body"], usage=SimpleNamespace(
                input_tokens=10, output_tokens=2,
                input_tokens_details=SimpleNamespace(cached_tokens=3),
                output_tokens_details=SimpleNamespace(reasoning_tokens=1)))

    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    return requests, state


def test_api_schema_usage_and_backend_hash(boundary, api_transport):
    _, codex_calls, _ = boundary
    api_calls, _ = api_transport
    codex = llm.call(**request())
    set_override("LLM_BACKEND", "api")
    api = llm.call(**request())
    assert api.response == {"ok": True} and api.error is None
    assert api.usage.model_dump() == dict(input=10, output=2, cached=3, reasoning=1)
    assert api.request_sha256 != codex.request_sha256
    kwargs, options = api_calls[0]
    assert kwargs["max_output_tokens"] == 800
    assert kwargs["text"]["format"]["strict"] is True
    assert kwargs["input"] == request()["messages"]
    assert options["max_retries"] == 0 and len(codex_calls) == 1


@pytest.mark.parametrize("status, attempts", [(500, 2), (429, 2), (401, 1)])
def test_api_errors_are_http_and_uncached(boundary, api_transport, status, attempts):
    root, _, records = boundary
    calls, state = api_transport
    state["status"] = status
    set_override("LLM_BACKEND", "api")
    for _ in range(2):
        result = llm.call(**request())
        assert result.error.kind == "http"
        assert result.error.detail == f"HTTP status {status}"
    assert len(calls) == attempts * 2 and len(records) == 2
    assert rows(root) == []


def test_batch_timeout_and_retries(boundary, api_transport):
    calls, state = api_transport
    state["status"] = 500
    set_override("LLM_BACKEND", "api")
    result = llm.map_calls([request(timeout_s=.01)], parallel=1)[0]
    assert result.error.kind == "http" and len(calls) == 5
    assert all(options["timeout"] == 60 for _, options in calls)


def test_oracle_is_external_and_logs_hits_externally(boundary, api_transport, tmp_path):
    root, _, records = boundary
    calls, _ = api_transport
    set_override("LLM_BACKEND", "api")
    for directory in (None, str(root / "inside"), str(root / "x" / ".." / "inside")):
        set_override("LLM_CACHE_DIR", directory)
        with pytest.raises(ValueError, match="outside"):
            llm.call(**request(purpose="oracle"))
    outside = tmp_path / "external space.dot"
    set_override("LLM_CACHE_DIR", str(outside))
    set_override("LLM_BACKEND", "codex")
    with pytest.raises(ValueError, match="codex"):
        llm.call(**request(purpose="oracle"))
    set_override("LLM_BACKEND", "api")
    first = llm.call(**request(purpose="oracle"))
    second = llm.call(**request(purpose="oracle"))
    assert first.response == second.response == {"ok": True} and second.cache_hit
    assert len(calls) == 1 and records == []
    assert len((outside / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert rows(root) == [] and not (root / ".local").exists()
    assert not paths.ledger_db().exists()


def test_override_and_schema_affect_identity(boundary, monkeypatch, tmp_path):
    _, calls, _ = boundary
    override = tmp_path / "override space.dot"
    set_override("LLM_CACHE_DIR", str(override))
    first = llm.call(**request())
    assert llm.cache_path(override, first.request_sha256).exists()
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    modified = dict(llm._MINIMAL_SCHEMA, description="different schema")
    (schemas / "test_response.json").write_text(json.dumps(modified), encoding="utf-8")
    monkeypatch.setattr(llm, "SCHEMA_DIR", schemas)
    second = llm.call(**request())
    assert second.request_sha256 != first.request_sha256 and len(calls) == 2


def test_missing_ledger_only_falls_back_for_missing_ledger(boundary, monkeypatch):
    import builtins
    root, _, _ = boundary
    original = builtins.__import__
    missing = ["ledger"]

    def importing(name, *args, **kwargs):
        if name == "ledger":
            raise ModuleNotFoundError(name=missing[0])
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    first = llm.call(**request())
    assert first.response == {"ok": True}
    assert len((root / ".local" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    missing[0] = "ledger_dependency"
    with pytest.raises(ModuleNotFoundError) as exc:
        llm.call(**request())
    assert exc.value.name == "ledger_dependency"


def test_measuring_batch_copies_scope_and_bounds_concurrency(boundary, monkeypatch):
    import threading
    root, _, records = boundary
    barrier = threading.Barrier(2)
    original = llm.subprocess.Popen

    def simultaneous(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(llm.subprocess, "Popen", simultaneous)
    with llm.run_scope(measuring=True):
        results = llm.map_calls([request(model="one"), request(model="two")], parallel=2)
    assert [result.model for result in results] == ["one", "two"]
    assert records == []
    assert len((root / ".local" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_shadow_refuses_mixed_backend(boundary, api_transport):
    llm.call(**request(purpose="shadow"))
    set_override("LLM_BACKEND", "api")
    with pytest.raises(ValueError, match="mix backends"):
        llm.call(**request(purpose="shadow"))
    assert api_transport[0] == []
    set_override("OFFLINE", 1)
    assert llm.call(**request(purpose="shadow")).error.kind == "off"
    set_override("LLM_BACKEND", "off")
    assert llm.call(**request(purpose="shadow")).error.detail == "backend off"


def test_api_missing_key_is_safe_error(boundary, api_transport):
    set_override("OPENAI_API_KEY", None)
    set_override("LLM_BACKEND", "api")
    result = llm.call(**request())
    assert result.error.kind == "http"
    assert result.error.detail == "API key is not configured"
    assert api_transport[0] == []


def test_backend_off_and_public_mode_never_call(boundary):
    root, calls, records = boundary
    set_override("LLM_BACKEND", "off")
    assert llm.call(**request()).error.detail == "backend off"
    set_override("LLM_BACKEND", "codex")
    set_override("PUBLIC_MODE", 1)
    assert llm.call(**request()).error.kind == "off"
    assert calls == records == [] and rows(root) == []


def test_command_and_cache_path_reject_unsafe_inputs():
    for command in ('"unfinished', "", '""'):
        with pytest.raises(ValueError):
            llm.command_args(command)
    with pytest.raises(ValueError):
        llm.cache_path(Path("cache"), "../bad")


def test_timeout_terminates_only_its_process_tree(boundary, monkeypatch):
    import os
    calls = []
    if os.name == "nt":
        monkeypatch.setattr(llm.subprocess, "run", lambda args, **kwargs: calls.append(args))
        llm._terminate_tree(SimpleNamespace(pid=87654))
        assert calls == [["taskkill", "/PID", "87654", "/T", "/F"]]
    else:
        monkeypatch.setattr(llm.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
        llm._terminate_tree(SimpleNamespace(pid=87654))
        assert calls == [(87654, llm.signal.SIGKILL)]


def test_schema_validates_nested_objects_arrays_null_and_enums(boundary, monkeypatch, tmp_path):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    schema = dict(type="object", required=["items"], additionalProperties=False, properties={
        "items": dict(type="array", items=dict(type="object", required=["value"],
            additionalProperties=False, properties={"value": dict(type=["string", "null"], enum=["yes", None])}))})
    (schemas / "nested.json").write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(llm, "SCHEMA_DIR", schemas)
    contents = [dict(items=[dict(value=None)]), dict(items=[dict(value="no")]),
                dict(items=[dict(value="yes", extra=True)]), dict(items=[dict(value=1)]),
                dict(items=[{}])]

    def fake(args, **kwargs):
        output = Path(args[args.index("-o") + 1])

        class Process:
            returncode = 0

            def communicate(self, **kwargs):
                output.write_text(json.dumps(contents.pop(0)), encoding="utf-8")
                return b"", b""

        return Process()

    monkeypatch.setattr(llm.subprocess, "Popen", fake)
    assert llm.call(**request(schema_name="nested")).response == {"items": [{"value": None}]}
    for _ in range(4):
        assert llm.call(**request(schema_name="nested", cache_read=False)).error.kind == "schema"


@pytest.fixture
def committed_shadow(tmp_path, monkeypatch):
    import shutil

    root = tmp_path / "committed repo space.dot"
    root.mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache/llm")
    monkeypatch.setattr(paths, "LOCAL_DIR", root / ".local")
    set_override("LLM_LOCAL_CACHE_DIR", ".local/cache")
    set_override("LEDGER_DB", ".local/absent.db")
    set_override("LLM_CACHE_DIR", None)
    set_override("LLM_BACKEND", "codex")
    set_override("OFFLINE", 0)
    calls = []

    def transport(*args):
        calls.append(args)
        return llm._Transport(response={"ok": True})

    monkeypatch.setattr(llm, "_codex", transport)
    monkeypatch.setattr(llm, "_api", transport)

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                               "-c", "commit.gpgsign=false", *args], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", check=True, timeout=10).stdout

    git("init")
    args = request(purpose="shadow")
    first = llm.call(**args)
    location = llm.cache_path(paths.CACHE_DIR, first.request_sha256)
    git("add", location.relative_to(root).as_posix())
    git("commit", "-m", "Synthetic cache")
    shutil.rmtree(paths.LOCAL_DIR)
    return SimpleNamespace(root=root, calls=calls, args=args, first=first, location=location, git=git)


def shadow_snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts}


def test_shadow_read_only_keeps_normal_request_identity_and_default_logging(committed_shadow):
    state = committed_shadow
    before = shadow_snapshot(state.root)
    with llm.run_scope(shadow_read_only=True):
        hit = llm.call(**state.args)
    assert hit.cache_hit and hit.response == state.first.response
    assert hit.request_sha256 == state.first.request_sha256
    assert shadow_snapshot(state.root) == before and not paths.LOCAL_DIR.exists()
    normal = llm.call(**state.args)
    assert normal.cache_hit and normal.request_sha256 == hit.request_sha256
    assert (paths.LOCAL_DIR / "llm_calls.jsonl").is_file()
    assert len(state.calls) == 1


def test_shadow_read_only_uses_head_blob_not_modified_worktree(committed_shadow, monkeypatch):
    state = committed_shadow
    state.location.write_text("not a cache row", encoding="utf-8")
    before = shadow_snapshot(state.root)
    status = state.git("status", "--porcelain", "-uall")
    monkeypatch.setattr(paths, "local_cache_dir", lambda **kwargs: pytest.fail("read-only resolved local cache"))
    monkeypatch.setattr(paths, "resolve_path", lambda *args: pytest.fail("read-only resolved override"))
    monkeypatch.setattr(llm, "_record", lambda *args: pytest.fail("read-only built a log or promotion record"))
    with llm.run_scope(shadow_read_only=True):
        hit = llm.call(**state.args)
    assert hit.cache_hit and hit.response == {"ok": True}
    assert len(state.calls) == 1 and shadow_snapshot(state.root) == before
    assert state.git("status", "--porcelain", "-uall") == status


@pytest.mark.parametrize("source", ["untracked", "staged", "local", "override", "absent", "fresh"])
def test_shadow_read_only_misses_never_read_local_promote_log_or_call_transport(committed_shadow, monkeypatch, source, tmp_path):
    state = committed_shadow
    args = request(purpose="live" if source == "local" else "shadow",
                   messages=[{"role": "user", "content": "Other synthetic request"}])
    if source == "fresh":
        args = state.args | {"cache_read": False}
    elif source != "absent":
        if source == "override":
            set_override("LLM_CACHE_DIR", str(tmp_path / "external cache"))
        with llm.run_scope(measuring=True):
            seeded = llm.call(**args)
        if source == "staged":
            location = llm.cache_path(paths.CACHE_DIR, seeded.request_sha256)
            state.git("add", location.relative_to(state.root).as_posix())
    args["purpose"] = "shadow"
    before = shadow_snapshot(tmp_path)
    status = state.git("status", "--porcelain", "-uall")
    calls = len(state.calls)
    monkeypatch.setattr(paths, "local_cache_dir", lambda **kwargs: pytest.fail("read-only resolved local cache"))
    monkeypatch.setattr(llm, "_record", lambda *args: pytest.fail("read-only built a log or promotion record"))
    with llm.run_scope(shadow_read_only=True):
        result = llm.call(**args)
    assert not result.cache_hit and result.error.kind == "off"
    assert len(state.calls) == calls and shadow_snapshot(tmp_path) == before
    assert state.git("status", "--porcelain", "-uall") == status


def test_shadow_read_only_nested_scope_tightens_and_resets_after_exception(committed_shadow):
    state = committed_shadow
    with pytest.raises(RuntimeError, match="synthetic scope failure"):
        with llm.run_scope(shadow_read_only=True):
            with llm.run_scope(shadow_read_only=False, local_writes=True):
                assert llm.call(**state.args).cache_hit
                miss = llm.call(**request(purpose="shadow", messages=[{"role": "user", "content": "Nested miss"}]))
                assert miss.error.kind == "off"
            raise RuntimeError("synthetic scope failure")
    assert not paths.LOCAL_DIR.exists() and len(state.calls) == 1
    normal = llm.call(**request(purpose="shadow", messages=[{"role": "user", "content": "After scope"}]))
    assert normal.error is None and len(state.calls) == 2
    assert llm.cache_path(paths.CACHE_DIR, normal.request_sha256).is_file()
    assert (paths.LOCAL_DIR / "llm_calls.jsonl").is_file()


def test_shadow_read_only_follows_batch_context_and_stays_thread_local(committed_shadow, monkeypatch):
    from threading import Barrier

    state = committed_shadow
    before = shadow_snapshot(state.root)
    with monkeypatch.context() as patch:
        patch.setattr(paths, "local_cache_dir", lambda **kwargs: pytest.fail("batch resolved local cache"))
        with llm.run_scope(shadow_read_only=True):
            results = llm.map_calls([state.args] * 3, parallel=3)
    assert all(result.cache_hit for result in results) and shadow_snapshot(state.root) == before
    barrier = Barrier(2)

    def readonly():
        with llm.run_scope(shadow_read_only=True):
            barrier.wait(timeout=5)
            return llm.call(**request(purpose="shadow", messages=[{"role": "user", "content": "Thread miss"}]))

    def normal():
        barrier.wait(timeout=5)
        return llm.call(**request(purpose="shadow", messages=[{"role": "user", "content": "Other thread"}]))

    with ThreadPoolExecutor(2) as pool:
        left, right = pool.submit(readonly), pool.submit(normal)
        assert left.result(timeout=10).error.kind == "off"
        assert right.result(timeout=10).error is None
    assert len(state.calls) == 2


def test_shadow_read_only_git_failure_is_an_offline_miss(committed_shadow, monkeypatch):
    state = committed_shadow
    before = shadow_snapshot(state.root)
    def unavailable(*args, **kwargs):
        raise OSError("synthetic Git failure")
    monkeypatch.setattr(llm.subprocess, "run", unavailable)
    with llm.run_scope(shadow_read_only=True):
        result = llm.call(**state.args)
    assert not result.cache_hit and result.error.kind == "off"
    assert len(state.calls) == 1 and shadow_snapshot(state.root) == before


def test_shadow_read_only_does_not_suppress_external_oracle_logs(committed_shadow, tmp_path):
    state = committed_shadow
    set_override("LLM_BACKEND", "api")
    cache = tmp_path / "external oracle cache"
    set_override("LLM_CACHE_DIR", str(cache))
    original = llm.call(**request(purpose="oracle"))
    log = cache / "llm_calls.jsonl"
    log.unlink()
    with llm.run_scope(shadow_read_only=True):
        hit = llm.call(**request(purpose="oracle"))
    assert hit.cache_hit and hit.request_sha256 == original.request_sha256
    assert log.is_file() and not paths.LOCAL_DIR.exists()
