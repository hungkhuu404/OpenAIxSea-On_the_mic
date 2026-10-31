"""Environment-only settings and the reproducible run configuration.

The application start-up owner in api/ calls core.config.config_line() and
prints it. Importing this module never prints or creates local directories.
Settings never load a dotenv file. CLI overrides mutate the shared object.
"""

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Typed section 3 settings; pass environ={} to use only defaults."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    LLM_BACKEND: Literal["api", "codex", "off"] = "off"
    OPENAI_API_KEY: str | None = Field(default=None, repr=False, exclude=True)
    S1_MODEL: str = "gpt-6-luna"
    S1_EFFORT: str = "none"
    JUDGE_MODEL: str = "gpt-6.1-sol"
    JUDGE_EFFORT: str = "low"
    JUDGE_MODE: Literal["two", "off"] = "two"
    DRAFT_MODEL: str = "gpt-6.1-sol"
    DRAFT_EFFORT: str = "low"
    LLM_TIMEOUT_S: int = 6
    LLM_PARALLEL: int = 8
    OFFLINE: Literal[0, 1] = 0
    LLM_CACHE_DIR: str | None = None
    LLM_LOCAL_CACHE_DIR: str = ".local/cache"
    LEDGER_DB: str = ".local/ledger.db"
    EVAL_DIR: str = "eval"
    PUBLIC_MODE: Literal[0, 1] = 0
    RATE_LIMIT_PER_MIN: int = 20
    PRESENT_DIR: str | None = None
    VIDEO_MODE: Literal[0, 1] = 0
    DEMO_EXPECTED: str | None = None
    REGRESSION_AUTOCOMMIT: Literal[0, 1] = 0
    LOOP_OPEN_PR: Literal[0, 1] = 0
    LOOP_DRY: Literal[0, 1] = 0
    CODEX_CMD: str = "codex"
    WORKTREE_ROOT: str = "../_worktrees"
    RULE_KEEPER: str = "hungkhuu404"
    T5_TAU: float = 0.6
    UI_PALETTE: Literal["oil", "graphite"] = "oil"
    UI_STAGE: Literal[0, 1] = 0

    def __init__(self, *, environ: Mapping[str, str] | None = None, **values):
        source = os.environ if environ is None else environ
        loaded = {name: source[name] for name in type(self).model_fields if name in source}
        # Literal integer flags need conversion before pydantic validates them.
        for name, field in type(self).model_fields.items():
            if type(field.default) is int and field.default in (0, 1) and name in loaded:
                loaded[name] = int(loaded[name])
        loaded.update(values)
        super().__init__(**loaded)


settings = Settings()


def set_override(name: str, value: object) -> None:
    """Update the same settings instance, rejecting misspelled setting names."""
    if name not in Settings.model_fields:
        raise ValueError(f"Unknown setting: {name}")
    field = Settings.model_fields[name]
    if type(field.default) is int and field.default in (0, 1) and isinstance(value, str):
        value = int(value)
    setattr(settings, name, value)


def command_args(command: str) -> list[str]:
    """Parse an executable and arguments without interpreting shell syntax."""
    args = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        args = [arg[1:-1] if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'"
                else arg for arg in args]
    return args


def _first_line(args: list[str], fallback: str) -> str:
    try:
        result = subprocess.run(
            args, cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=True, shell=False,
        )
        lines = result.stdout.splitlines()
        line = lines[0].strip() if lines else ""
        if settings.OPENAI_API_KEY:
            line = line.replace(settings.OPENAI_API_KEY, "[redacted]")
        return line or fallback
    except (OSError, subprocess.SubprocessError, ValueError):
        return fallback


def config_fields() -> dict[str, str | int | float]:
    """Return section 3.1 in wire order; cfg never hashes itself or secrets."""
    try:
        codex_args = command_args(settings.CODEX_CMD) + ["--version"]
        codex = _first_line(codex_args, "unavailable") if len(codex_args) > 1 else "unavailable"
    except ValueError:
        codex = "unavailable"
    fields = {
        "head": _first_line(["git", "rev-parse", "--short", "HEAD"], "nogit"),
        "codex": codex,
        "backend": settings.LLM_BACKEND,
        "offline": settings.OFFLINE,
        "judge": settings.JUDGE_MODE,
        "s1": f"{settings.S1_MODEL}/{settings.S1_EFFORT}",
        "judge_model": f"{settings.JUDGE_MODEL}/{settings.JUDGE_EFFORT}",
        "draft": f"{settings.DRAFT_MODEL}/{settings.DRAFT_EFFORT}",
        "timeout": settings.LLM_TIMEOUT_S,
        "loop_dry": settings.LOOP_DRY,
        "autocommit": settings.REGRESSION_AUTOCOMMIT,
        "open_pr": settings.LOOP_OPEN_PR,
        "public": settings.PUBLIC_MODE,
        "video": settings.VIDEO_MODE,
        "present_dir": "set" if settings.PRESENT_DIR else "unset",
        "eval_dir": settings.EVAL_DIR,
        "ledger": settings.LEDGER_DB,
        "local_cache": settings.LLM_LOCAL_CACHE_DIR,
        "cache_dir": settings.LLM_CACHE_DIR or "unset",
        "api_key": "set" if settings.OPENAI_API_KEY else "unset",
    }
    excluded = {"head", "codex", "api_key", "present_dir", "eval_dir", "ledger",
                "local_cache", "cache_dir"}
    canonical = json.dumps({k: v for k, v in fields.items() if k not in excluded},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    cfg = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    return {"cfg": cfg, **fields}


def config_line() -> str:
    """The one line shared by startup, health and demo-state reporting."""
    return " ".join(f"{name}={value}" for name, value in config_fields().items())


if __name__ == "__main__":
    print(config_line())
