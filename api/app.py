"""HTTP reads and event streaming; later tasks replace entries in STUB_ROUTES."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.paths import REPO_ROOT
import ledger
from api.present import router as present_router
from api.ui import router as ui_router
from api.preflight import PublicRateLimit, router as preflight_router
from api.public import ledger_lifespan


STUB_ROUTES = {
    ("POST", "/sessions"): "P4",
    ("POST", "/sessions/{id}/process"): "P4",
    ("GET", "/queue"): "P5",
    ("POST", "/labels"): "P5",
    ("POST", "/report-wrong"): "P5",
    ("GET", "/jobs"): "P5",
    ("GET", "/jobs/{id}"): "P5",
    ("GET", "/rules"): "P5",
    ("GET", "/rules/{id}"): "P5",
    ("GET", "/rules/{id}/dryrun"): "P5",
    ("POST", "/rules/{id}/merge"): "P5",
    ("POST", "/dossier-events/recall"): "P4",
    ("POST", "/dossier-events/update"): "P5",
    ("POST", "/pending/{id}/approve"): "P5",
    ("GET", "/who-decided"): "P4",
}


def _session_payload(session, presenter):
    data = session.model_dump(mode="json")
    settings = config.settings
    names = {}
    if presenter == "1" and settings.PRESENT_DIR and not settings.PUBLIC_MODE and not settings.VIDEO_MODE:
        try:
            value = json.loads((Path(settings.PRESENT_DIR) / "names.json").read_text(encoding="utf-8"))
            if isinstance(value, dict):
                names = value
        except (OSError, ValueError):
            pass
    for turn in data["turns"]:
        role = turn["speaker_label"]
        attribution = turn["attribution"]
        if attribution == "press_unknown":
            speaker = "Speaker unknown"
        elif attribution == "constructed":
            speaker = f"Reenactment line (team-written) · {role}"
        elif attribution == "not_applicable" and data["channel"] == "video":
            speaker = "Speaker: unidentified"
        elif attribution == "press_attributed":
            speaker = names.get(role, role)
            if not isinstance(speaker, str):
                speaker = role
        else:
            speaker = role
        turn["display_speaker"] = speaker
    return data


async def event_stream(request, since):
    """Replay durable rows, then poll for committed events without holding a lock."""
    heartbeat = time.monotonic()
    while not await request.is_disconnected():
        rows = await asyncio.to_thread(ledger.events_since, since)
        for event in rows:
            since = event.id
            yield f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
        if time.monotonic() - heartbeat >= 15:
            yield ": heartbeat\n\n"
            heartbeat = time.monotonic()
        if not rows:
            await asyncio.sleep(0.1)


def create_app(public=False):
    public = bool(public or config.settings.PUBLIC_MODE)
    if public:
        config.set_override("PUBLIC_MODE", 1)
        config.set_override("OFFLINE", 1)

    @asynccontextmanager
    async def lifespan(app):
        async with ledger_lifespan(public):
            fields = config.config_fields()
            app.state.config_fields = fields
            print(" ".join(f"{key}={value}" for key, value in fields.items()), flush=True)
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.public = public
    app.state.preflight_limit = PublicRateLimit()
    app.include_router(preflight_router)
    app.include_router(present_router)
    app.include_router(ui_router)

    @app.middleware("http")
    async def read_only_public(request, call_next):
        if public:
            path = request.url.path
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not (
                    request.method == "POST" and path == "/api/preflight"):
                return JSONResponse(status_code=403, content={"detail": "Public demo is read-only"})
            if path == "/api/present/manifest" or path == "/present" or path.startswith("/present/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
        return await call_next(request)

    @app.exception_handler(ModuleNotFoundError)
    async def not_ready(request, exc):
        if exc.name in {"textnorm", "rules.registry"}:
            return JSONResponse({"todo": exc.name}, status_code=503)
        raise exc

    @app.get("/api/health")
    def health():
        return {**app.state.config_fields, "ok": True, "pack_version": ledger.current_pack().version,
                "stage": config.settings.UI_STAGE, "palette": config.settings.UI_PALETTE}

    @app.get("/api/sessions")
    def sessions(presenter: str | None = None):
        return [_session_payload(session, presenter) for session in ledger.list_sessions()]

    @app.get("/api/sessions/{id}")
    def session(id: str, presenter: str | None = None):
        stored = ledger.get_session(id)
        if stored is None:
            raise HTTPException(404, "Session not found")
        return _session_payload(stored, presenter)

    @app.get("/api/events")
    def events(since: int = 0):
        return ledger.events_since(since)

    @app.get("/api/events/stream")
    async def stream(request: Request, since: int = 0):
        last_id = request.headers.get("last-event-id")
        if last_id is not None:
            try:
                since = int(last_id)
            except ValueError:
                raise HTTPException(400, "Last-Event-ID must be an integer")
        return StreamingResponse(event_stream(request, since), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/search")
    def search(q: str, session_id: str | None = None):
        return ledger.search(q, session_id)

    def stub(task):
        def endpoint():
            return JSONResponse({"todo": task}, status_code=501)
        return endpoint

    for (method, path), task in STUB_ROUTES.items():
        app.add_api_route("/api" + path, stub(task), methods=[method])
    app.mount("/", StaticFiles(directory=REPO_ROOT / "ui", html=True), name="ui")
    return app
