"""Isolated cache replay for the laptop's public view."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from importlib import import_module
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from core import config, paths
import ledger


def prepare_replay():
    ledger.init_db(config.settings.LEDGER_DB)
    backends = {json.loads(path.read_text(encoding="utf-8"))["backend"]
                for path in paths.CACHE_DIR.glob("*/*.json")}
    if backends - {config.settings.LLM_BACKEND}:
        print("warning: cache backend differs from LLM_BACKEND; replay may show Needs human (LLM_OFF)", flush=True)
    modules = {}
    for name in ("pipeline.ingest", "pipeline.runner", "pipeline.dossier_events"):
        try:
            modules[name] = import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            print(f"not ready: {name}", flush=True)
            return
    for as_of in (date(2025, 1, 8), date(2024, 12, 12)):
        session = modules["pipeline.ingest"].from_reenactment(
            paths.KERA_DIR / "reenactment_script.md", "kera", as_of)
        stored = ledger.add_session(session)
        modules["pipeline.runner"].process_session(stored.session_id, "live")
    modules["pipeline.dossier_events"].apply_recall(
        "kera", date(2025, 3, 24), "Public dossier recall dated 2025-03-24")


@asynccontextmanager
async def ledger_lifespan(public):
    if not public:
        ledger.init_db(config.settings.LEDGER_DB)
        yield
        return
    names = ("LEDGER_DB", "LLM_CACHE_DIR", "LLM_LOCAL_CACHE_DIR")
    previous = {name: getattr(config.settings, name) for name in names}
    with TemporaryDirectory(prefix="public-ledger-") as directory:
        root = Path(directory)
        config.set_override("LEDGER_DB", str(root / "ledger.db"))
        config.set_override("LLM_CACHE_DIR", str(paths.CACHE_DIR))
        config.set_override("LLM_LOCAL_CACHE_DIR", str(root / "empty-cache"))
        try:
            await asyncio.to_thread(prepare_replay)
            yield
        finally:
            for name, value in previous.items():
                config.set_override(name, value)
