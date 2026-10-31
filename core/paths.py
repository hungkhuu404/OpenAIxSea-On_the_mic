"""Repository paths. Resolution is read-only; explicit first use creates folders.

Dynamic accessors consult the shared settings so CLI overrides take effect.
Data paths are never created. Eval paths are returned for the app to write.
"""

from pathlib import Path

from core.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
LAW_DIR = DATA_DIR / "law"
EVIDENCE_DIR = DATA_DIR / "evidence"
KERA_DIR = DATA_DIR / "demo" / "kera"
GOLDEN_CASES = DATA_DIR / "cases" / "golden.jsonl"
CACHE_DIR = REPO_ROOT / "cache" / "llm"
REPORTS_DIR = REPO_ROOT / "reports"
LOCAL_DIR = REPO_ROOT / ".local"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else REPO_ROOT / path).resolve()


def llm_cache_dir() -> Path:
    return resolve_path(settings.LLM_CACHE_DIR) if settings.LLM_CACHE_DIR else CACHE_DIR


def local_cache_dir(*, create: bool = False) -> Path:
    path = resolve_path(settings.LLM_LOCAL_CACHE_DIR)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def eval_dir() -> Path:
    return resolve_path(settings.EVAL_DIR)


def regressions_file() -> Path:
    return eval_dir() / "regressions.jsonl"


def clusters_dir() -> Path:
    return eval_dir() / "clusters"


def ledger_db(*, create: bool = False) -> Path:
    path = resolve_path(settings.LEDGER_DB)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def snapshots_dir(*, create: bool = False) -> Path:
    return _local_folder("snapshots", create=create)


def logs_dir(*, create: bool = False) -> Path:
    return _local_folder("logs", create=create)


def jobs_dir(*, create: bool = False) -> Path:
    return _local_folder("jobs", create=create)


def _local_folder(name: str, *, create: bool) -> Path:
    path = LOCAL_DIR / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def worktree_root() -> Path:
    path = resolve_path(settings.WORKTREE_ROOT)
    if path.is_relative_to(REPO_ROOT):
        raise ValueError("WORKTREE_ROOT must resolve outside the repository")
    return path
