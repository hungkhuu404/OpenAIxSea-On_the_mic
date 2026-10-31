"""The synchronous/async LLM boundary, isolated cache and transport diagnostics."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import tempfile
import threading
import time

from core import paths
from core.config import settings
from core.models import LLMCall, LLMError, Usage


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_OFFLINE = ContextVar("llm_offline", default=False)
_MEASURING = ContextVar("llm_measuring", default=False)
_BATCH = ContextVar("llm_batch", default=None)
_OBSERVERS = ContextVar("llm_result_observers", default=())
_LOCAL_WRITES = ContextVar("llm_local_writes", default=False)
_SHADOW_READ_ONLY = ContextVar("llm_shadow_read_only", default=False)
_LOG_LOCK = threading.Lock()
_TOKENS = {"s1": 800, "t1": 400, "t2": 600, "negq": 50}
_MINIMAL_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                   "required": ["ok"], "additionalProperties": False}


class Result(LLMCall):
    cache_hit: bool = False


@contextmanager
def observe_results():
    """Collect completed results in this context, including copied batch contexts."""
    results = []
    token = _OBSERVERS.set((*_OBSERVERS.get(), (results, threading.Lock())))
    try:
        yield results
    finally:
        _OBSERVERS.reset(token)


def _completed(result):
    for results, lock in _OBSERVERS.get():
        with lock:
            results.append(result)
    return result


@dataclass
class _Transport:
    response: object = None
    usage: Usage | None = None
    error: LLMError | None = None
    output_file_present: bool = False
    exit_code: int | None = None
    stderr: str = ""


@contextmanager
def run_scope(offline=False, measuring=False, *, local_writes=False, shadow_read_only=False):
    """Only tighten the inherited scope, without mutating process settings."""
    shadow_token = _SHADOW_READ_ONLY.set(_SHADOW_READ_ONLY.get() or shadow_read_only)
    off_token = _OFFLINE.set(_OFFLINE.get() or offline or _SHADOW_READ_ONLY.get())
    measure_token = _MEASURING.set(_MEASURING.get() or measuring)
    local_token = _LOCAL_WRITES.set(_LOCAL_WRITES.get() or local_writes)
    try:
        yield
    finally:
        _LOCAL_WRITES.reset(local_token)
        _MEASURING.reset(measure_token)
        _OFFLINE.reset(off_token)
        _SHADOW_READ_ONLY.reset(shadow_token)


@contextmanager
def batch_scope(parallel=None):
    """Share one transport budget across copied contexts and nested batches."""
    parallel = settings.LLM_PARALLEL if parallel is None else parallel
    if parallel < 1:
        raise ValueError("parallel must be positive")
    token = _BATCH.set(_BATCH.get() or threading.BoundedSemaphore(parallel))
    try:
        yield
    finally:
        _BATCH.reset(token)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_path(directory, request_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
        raise ValueError("request_sha256 must contain 64 lowercase hex characters")
    return Path(directory) / request_sha256[:2] / f"{request_sha256}.json"


def command_args(command):
    """Portable double-quote parser; Windows backslashes are never escapes."""
    result, current, quoted, started = [], [], False, False
    for char in command:
        if char == '"':
            quoted = not quoted
            started = True
        elif char.isspace() and not quoted:
            if started:
                result.append("".join(current))
                current, started = [], False
        else:
            current.append(char)
            started = True
    if quoted:
        raise ValueError("CODEX_CMD contains an unclosed double quote")
    if started:
        result.append("".join(current))
    if not result or not result[0]:
        raise ValueError("CODEX_CMD must name an executable")
    return result


def _masked(text):
    text = str(text or "")
    if settings.OPENAI_API_KEY:
        text = text.replace(settings.OPENAI_API_KEY, "[redacted]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*)[^\s,;]+",
                  r"\1[redacted]", text)
    return text[-2000:]


def _error(kind, detail, exit_code=None, stderr=""):
    return LLMError(kind=kind, exit_code=exit_code, stderr_tail=_masked(stderr),
                    detail=_masked(detail).replace("\n", " ")[:300])


def _schema(schema_name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", schema_name):
        raise ValueError("schema_name must be a simple schema filename without extension")
    return json.loads((SCHEMA_DIR / f"{schema_name}.json").read_text(encoding="utf-8"))


def _validate(value, schema):
    """Validate the contract's JSON Schema subset at the transport boundary."""
    types = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
             "integer": lambda v: type(v) is int, "number": lambda v: type(v) in (int, float),
             "null": lambda v: v is None}
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(types[k](value) for k in kinds):
            raise ValueError("response type does not match schema")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise ValueError("response enum does not match schema")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            raise ValueError("response lacks a required property")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError("response contains an additional property")
        for name, child in properties.items():
            if name in value:
                _validate(value[name], child)
    if isinstance(value, list) and "items" in schema:
        for item in value:
            _validate(item, schema["items"])


def _parse(text, schema, transport):
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        transport.error = _error("parse", "output is not valid JSON", stderr=transport.stderr)
        return transport
    try:
        _validate(value, schema)
    except ValueError as exc:
        transport.error = _error("schema", str(exc), stderr=transport.stderr)
        return transport
    transport.response = value
    return transport


def _write_once(location, row):
    """Publish a complete row atomically, without overwriting another writer."""
    location.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".llm-", suffix=".tmp", dir=location.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(row) + "\n")
        try:
            # A hard link is an atomic no-clobber publish on Windows and POSIX.
            os.link(temporary, location)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _record(result):
    return LLMCall.model_validate(result.model_dump(exclude={"cache_hit"})).model_dump(mode="json")


def _log(result, override):
    if result.purpose == "shadow" and _SHADOW_READ_ONLY.get():
        return
    row = _record(result)
    if result.purpose == "oracle":
        target = override / "llm_calls.jsonl"
    elif result.purpose == "shadow" or _MEASURING.get():
        target = paths.LOCAL_DIR / "llm_calls.jsonl"
    else:
        try:
            import ledger
        except ModuleNotFoundError as exc:
            if exc.name != "ledger":
                raise
            target = paths.LOCAL_DIR / "llm_calls.jsonl"
        else:
            ledger.record_llm_call(LLMCall.model_validate(row))
            return
    with _LOG_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(row) + "\n")


def _terminate_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=.4, check=False, shell=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _codex(model, effort, schema, messages, timeout):
    prompt = "\n\n".join(f"--- {message['role']} ---\n" +
                           (message["content"] if isinstance(message["content"], str)
                            else _canonical(message["content"])) for message in messages)
    with tempfile.TemporaryDirectory(prefix="llm ") as directory:
        working = Path(directory)
        schema_file, output = working / "schema.json", working / "output.json"
        schema_file.write_text(_canonical(schema), encoding="utf-8", newline="\n")
        args = command_args(settings.CODEX_CMD) + [
            "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-m", model,
            "-c", f"model_reasoning_effort={effort}", "--output-schema", str(schema_file),
            "-o", str(output), "-",
        ]
        options = dict(cwd=working, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, shell=False)
        if os.name != "nt":
            options["start_new_session"] = True
        try:
            process = subprocess.Popen(args, **options)
        except OSError:
            return _Transport(error=_error("nonzero_exit", "could not start CODEX_CMD"))
        try:
            _, stderr = process.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                _terminate_tree(process)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
            try:
                _, stderr = process.communicate(timeout=.05)
            except subprocess.TimeoutExpired:
                stderr = b""
            tail = _masked(stderr.decode("utf-8", errors="replace"))
            return _Transport(error=_error("timeout", "backend exceeded timeout", stderr=tail),
                              stderr=tail)
        tail = _masked(stderr.decode("utf-8", errors="replace"))
        transport = _Transport(exit_code=process.returncode, stderr=tail,
                               output_file_present=output.is_file())
        if process.returncode != 0:
            transport.error = _error("nonzero_exit", "CODEX_CMD exited unsuccessfully",
                                     process.returncode, tail)
            return transport
        if not output.is_file() or not output.stat().st_size:
            transport.error = _error("no_output", "output file is missing or empty", stderr=tail)
            return transport
        return _parse(output.read_text(encoding="utf-8", errors="replace"), schema, transport)


def _api(model, effort, schema_name, schema, messages, kind, timeout):
    from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

    if not settings.OPENAI_API_KEY:
        return _Transport(error=_error("http", "API key is not configured"))
    retries = 4 if _BATCH.get() else 1
    with OpenAI(api_key=settings.OPENAI_API_KEY, timeout=timeout, max_retries=0) as client:
        for attempt in range(retries + 1):
            try:
                response = client.responses.create(
                    model=model, reasoning={"effort": effort}, input=messages,
                    max_output_tokens=_TOKENS[kind],
                    text={"format": {"type": "json_schema", "name": schema_name,
                                     "strict": True, "schema": schema}},
                )
                usage = response.usage
                parsed_usage = None if usage is None else Usage(
                    input=usage.input_tokens, output=usage.output_tokens,
                    cached=usage.input_tokens_details.cached_tokens,
                    reasoning=usage.output_tokens_details.reasoning_tokens)
                return _parse(response.output_text, schema, _Transport(usage=parsed_usage))
            except APITimeoutError:
                return _Transport(error=_error("timeout", "backend exceeded timeout"))
            except APIStatusError as exc:
                status = exc.status_code
                if (status == 429 or status >= 500) and attempt < retries:
                    time.sleep(.25 * 2 ** attempt + random.uniform(0, .1))
                    continue
                return _Transport(error=_error("http", f"HTTP status {status}"))
            except APIConnectionError:
                return _Transport(error=_error("http", "API connection failed"))


def _backend(model, effort, schema_name, schema, messages, kind, timeout_s):
    timeout = 60 if _BATCH.get() else (settings.LLM_TIMEOUT_S if timeout_s is None else timeout_s)
    if timeout <= 0:
        raise ValueError("timeout_s must be positive")
    if settings.LLM_BACKEND == "codex":
        return _codex(model, effort, schema, messages, timeout)
    return _api(model, effort, schema_name, schema, messages, kind, timeout)


def _identity(backend, model, effort, schema_name, schema, messages):
    payload = dict(backend=backend, model=model, effort=effort,
                   schema_name=schema_name, schema=schema, messages=messages)
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _off_error():
    if settings.LLM_BACKEND == "off":
        return _error("off", "backend off")
    if settings.OFFLINE or _OFFLINE.get() or settings.PUBLIC_MODE:
        return _error("off", "offline cache miss")
    return None


def _check_shadow_backend(backend):
    for location in paths.CACHE_DIR.glob("*/*.json"):
        row = json.loads(location.read_text(encoding="utf-8"))
        if row["backend"] != backend:
            raise ValueError("shadow refuses to mix backends in cache/llm")


def _committed_cache_row(request_sha256):
    """Read HEAD, so staged/untracked rows and working-tree edits cannot enter a stand-in."""
    try:
        relative = cache_path(paths.CACHE_DIR, request_sha256).relative_to(paths.REPO_ROOT).as_posix()
        result = subprocess.run(["git", "show", f"HEAD:{relative}"], cwd=paths.REPO_ROOT,
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=10, check=False, shell=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def call(kind, purpose, model, effort, schema_name, messages, timeout_s=None, cache_read=True):
    """Make or replay one request. Never block an event-loop thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("pipeline.llm.call is blocking: use acall, or run the caller "
                           "in a worker thread (asyncio.to_thread)")
    if kind not in _TOKENS or purpose not in ("live", "shadow", "oracle"):
        raise ValueError("invalid LLM kind or purpose")
    schema = _schema(schema_name)
    backend = settings.LLM_BACKEND
    readonly = purpose == "shadow" and _SHADOW_READ_ONLY.get()
    override = paths.resolve_path(settings.LLM_CACHE_DIR) if settings.LLM_CACHE_DIR and not readonly else None
    if purpose == "oracle":
        if override is None or override.is_relative_to(paths.REPO_ROOT.resolve()):
            raise ValueError("oracle requires LLM_CACHE_DIR outside the repository")
        if backend == "codex":
            raise ValueError("oracle refuses backend codex")
    if purpose == "shadow" and override is None and _off_error() is None:
        _check_shadow_backend(backend)
    sha = _identity(backend, model, effort, schema_name, schema, messages)
    started = time.monotonic()
    base = dict(request_sha256=sha, kind=kind, purpose=purpose, backend=backend, model=model,
                effort=effort, ts=datetime.now(timezone.utc).isoformat(), latency_ms=0)
    local = None if readonly else paths.local_cache_dir()
    # Oracle lookup is confined to its external directory, not repository caches.
    directories = ([paths.CACHE_DIR] if readonly else
                   [override] if purpose == "oracle" else [override, paths.CACHE_DIR, local])
    if cache_read:
        for directory in directories:
            if directory is None:
                continue
            if readonly:
                content = _committed_cache_row(sha)
                if content is None:
                    continue
            else:
                location = cache_path(directory, sha)
                if not location.is_file():
                    continue
                content = location.read_text(encoding="utf-8")
            cached = LLMCall.model_validate_json(content)
            if cached.error is not None or cached.request_sha256 != sha:
                continue
            result = Result(**(cached.model_dump() | base), cache_hit=True)
            try:
                _validate(cached.response, schema)
            except ValueError as exc:
                result.response = None
                result.error = _error("schema", str(exc))
            result.latency_ms = (time.monotonic() - started) * 1000
            if (result.error is None and purpose == "shadow" and directory == local and override is None
                    and not _LOCAL_WRITES.get()):
                _check_shadow_backend(backend)
                _write_once(cache_path(paths.CACHE_DIR, sha), _record(result))
            _log(result, override)
            return _completed(result)
    error = _off_error()
    if error:
        return _completed(Result(**base, error=error))
    with _BATCH.get() or nullcontext():
        transport = _backend(model, effort, schema_name, schema, messages, kind, timeout_s)
    result = Result(**(base | {"latency_ms": (time.monotonic() - started) * 1000}),
                    response=transport.response, usage=transport.usage, error=transport.error)
    if result.error is None:
        destination = override or (paths.CACHE_DIR if purpose == "shadow" else local)
        if _LOCAL_WRITES.get() and purpose != "oracle":
            destination = local
        _write_once(cache_path(destination, sha), _record(result))
    _log(result, override)
    return _completed(result)


async def acall(*args, **kwargs):
    return await asyncio.to_thread(call, *args, **kwargs)


def map_calls(requests, parallel=None):
    """Bounded synchronous batch, with caller context copied into each worker."""
    parallel = settings.LLM_PARALLEL if parallel is None else parallel
    with batch_scope(parallel):
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(copy_context().run, call, **request) for request in requests]
            return [future.result() for future in futures]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    diagnose = sub.add_parser("diagnose")
    diagnose.add_argument("--kind", choices=("s1", "t1"), required=True)
    args = parser.parse_args(argv)
    kind = args.kind
    model = settings.S1_MODEL if kind == "s1" else settings.JUDGE_MODEL
    effort = settings.S1_EFFORT if kind == "s1" else settings.JUDGE_EFFORT
    exists = (SCHEMA_DIR / f"{kind}.json").is_file()
    schema = _schema(kind) if exists else _MINIMAL_SCHEMA
    messages = [dict(role="system", content="Return a JSON object matching the supplied schema."),
                dict(role="user", content="Invented product DemoBean is a fictional food. "
                     "It contains oats. This is a synthetic diagnostic, not a real product.")]
    started = time.monotonic()
    error = _off_error()
    transport = _Transport(error=error) if error else _backend(
        model, effort, kind, schema, messages, kind, None)
    fields = dict(backend=settings.LLM_BACKEND, model=model, effort=effort,
                  schema=str(SCHEMA_DIR / f"{kind}.json") if exists else "built-in minimal",
                  request_sha256=_identity(settings.LLM_BACKEND, model, effort, kind, schema, messages),
                  exit_code=transport.exit_code, error_kind=transport.error.kind if transport.error else "",
                  output_file_present="yes" if transport.output_file_present else "no",
                  parse_result="OK" if not transport.error else transport.error.kind,
                  stderr_tail=transport.stderr, latency_ms=round((time.monotonic() - started) * 1000, 3))
    for name, value in fields.items():
        print(f"{name}={value}")
    return 1 if transport.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
