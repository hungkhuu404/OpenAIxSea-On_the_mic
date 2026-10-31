"""Typed-sentence boundary and per-client public request budget."""

import asyncio
from collections import defaultdict, deque
from datetime import date
from math import ceil
from time import monotonic

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.config import settings

router = APIRouter()


class Sentence(BaseModel):
    sku_id: str
    as_of: date
    text: str = Field(min_length=1)


class PublicRateLimit:
    def __init__(self):
        self.requests = defaultdict(deque)

    def check(self, client):
        now = monotonic()
        recent = self.requests[client]
        while recent and recent[0] <= now - 60:
            recent.popleft()
        if len(recent) >= settings.RATE_LIMIT_PER_MIN:
            raise HTTPException(429, "Please wait before checking another sentence",
                                headers={"Retry-After": str(max(1, ceil(recent[0] + 60 - now)))})
        recent.append(now)


@router.post("/api/preflight")
async def preflight(sentence: Sentence, request: Request):
    public = request.app.state.public
    if public:
        request.app.state.preflight_limit.check(request.client.host if request.client else "unknown")
    try:
        from pipeline.rawpath import preflight as check
    except ModuleNotFoundError as error:
        if error.name != "pipeline.rawpath":
            raise
        return JSONResponse(status_code=503, content={"todo": "rawpath"})
    return await asyncio.to_thread(check, sentence.sku_id, sentence.as_of, sentence.text, full=not public)
