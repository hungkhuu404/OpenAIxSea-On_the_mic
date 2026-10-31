"""Synthetic UI preview and filtered law display endpoints."""

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.paths import REPO_ROOT

router = APIRouter()


@router.get("/api/ui/fixture")
def fixture():
    def read(name):
        return json.loads((REPO_ROOT / "fixtures" / name).read_text(encoding="utf-8"))
    session = read("session_fake.json")
    for turn in session["turns"]:
        turn["display_speaker"] = (
            "Speaker unknown" if turn["attribution"] == "press_unknown" else
            f"Reenactment line (team-written) · {turn['speaker_label']}"
            if turn["attribution"] == "constructed" else turn["speaker_label"])
    session.update(atoms=read("atoms_fake.json"), verdicts=read("verdicts_fake.json"),
                   context=read("context_fake.json"), fixture=True,
                   events=[json.loads(line) for line in
                           (REPO_ROOT / "fixtures/events_fake.jsonl").read_text(encoding="utf-8").splitlines()
                           if line.strip()])
    return session


@router.get("/api/ui/law/{anchor:path}")
def law(anchor: str):
    try:
        from pipeline.guardrail import display_text
    except ModuleNotFoundError as error:
        if error.name != "pipeline.guardrail":
            raise
        return JSONResponse(status_code=503, content={"todo": "pipeline.guardrail"})
    return {"display_text": display_text(anchor)}
