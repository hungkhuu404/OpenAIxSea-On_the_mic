"""Local clip transcripts become ordinary video sessions, without ASR."""
import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from core.models import Session, Turn
from textnorm import nfc


class TranscriptLine(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Transcript end precedes start")
        return self


def from_clip_transcript(path, sku_id, as_of, source):
    path = Path(path)
    key = path.name.removesuffix(".transcript.json")
    if key not in {"a1", "a2"}:
        raise ValueError("Unknown presentation clip")
    rows = [TranscriptLine.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    return Session(session_id=f"clip-{key}", sku_pin=sku_id, as_of=as_of, channel="video",
        source=source, turns=[Turn(turn_id=f"t{i:02}", speaker_label="[unidentified]",
            attribution="not_applicable", text=nfc(row.text), start_s=row.start, end_s=row.end)
            for i, row in enumerate(rows, 1)])
