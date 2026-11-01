"""Session commands and the single local clip preparation path."""
import asyncio
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core import config, paths
from api.present import _inside, _json
import ledger

router = APIRouter(prefix="/api")


class NotReady(Exception):
    def __init__(self, module):
        self.module = module
        super().__init__(module)


def dependency(name):
    try:
        return import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise NotReady(name) from error


def store_session(session):
    with ledger.transaction():
        if ledger.get_session(session.session_id) is not None:
            return False
        ledger.add_session(session)
        ledger.append_event("session.ingested", dict(session_id=session.session_id,
            sku_pin=session.sku_pin, as_of=session.as_of.isoformat(), channel=session.channel,
            n_turns=len(session.turns)))
        return True


def ensure_clip_session(reprocess=False):
    """Ingest each available transcript once; run only outside the event loop."""
    settings = config.settings
    if not settings.PRESENT_DIR or settings.PUBLIC_MODE:
        return []
    from pipeline.ingest_clip import from_clip_transcript

    root = Path(settings.PRESENT_DIR).resolve()
    ids = []
    for key in ("a1", "a2"):
        transcript = _inside(root, f"clip/{key}.transcript.json")
        if not transcript.is_file():
            continue
        meta = _json(root, f"clip/{key}.meta.json")
        session = from_clip_transcript(transcript, "kera", date(2025, 1, 8), meta.get("source_line", "Local clip"))
        store_session(session)
        stored = ledger.get_session(session.session_id)
        if reprocess or not stored.verdicts:
            dependency("pipeline.runner").process_session(session.session_id, "live", reprocess=reprocess)
        ids.append(session.session_id)
    return ids


class IngestRequest(BaseModel):
    kind: Literal["text", "reenactment", "clip"]
    sku_id: str = "kera"
    as_of: date = date(2025, 1, 8)
    text: str = Field(default="", max_length=100000)
    source: str = "Typed session"


class ProcessRequest(BaseModel):
    reprocess: bool = False
    turn_id: str | None = Field(default=None, min_length=1, max_length=100)


class RecallRequest(BaseModel):
    sku_id: str
    recall_date: date
    source: str


def ingest(request):
    adapter = dependency("pipeline.ingest")
    session = (adapter.from_text(request.sku_id, request.as_of, request.text, request.source)
        if request.kind == "text" else adapter.from_reenactment(
            paths.KERA_DIR / "reenactment_script.md", request.sku_id, request.as_of))
    if store_session(session):
        try:
            events = dependency("pipeline.dossier_events")
        except NotReady:
            dependency("pipeline.runner").process_session(session.session_id, "live")
        else:
            events.auto_run(session.session_id)
    return ledger.get_session(session.session_id)


@router.post("/sessions")
async def create_session(body: IngestRequest):
    if body.kind == "clip":
        return {"session_ids": await asyncio.to_thread(ensure_clip_session)}
    return await asyncio.to_thread(ingest, body)


@router.post("/sessions/{id}/process")
async def process(id: str, body: ProcessRequest = ProcessRequest()):
    def run():
        session = ledger.get_session(id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if body.turn_id is not None:
            if not any(turn.turn_id == body.turn_id for turn in session.turns):
                raise HTTPException(404, "Turn not found")
            return dependency("pipeline.runner").process_turn(
                id, body.turn_id, "live", reprocess=body.reprocess)
        return dependency("pipeline.runner").process_session(id, "live", reprocess=body.reprocess)
    return await asyncio.to_thread(run)


@router.post("/dossier-events/recall")
async def recall(body: RecallRequest):
    def run():
        return dependency("pipeline.dossier_events").apply_recall(
            body.sku_id, body.recall_date, body.source)
    return await asyncio.to_thread(run)
