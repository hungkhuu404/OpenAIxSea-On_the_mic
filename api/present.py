"""Local presentation manifest and strictly allow-listed media."""

import json
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from core.config import settings

router = APIRouter()


def _root() -> Path:
    if settings.PUBLIC_MODE or not settings.PRESENT_DIR:
        raise HTTPException(404)
    return Path(settings.PRESENT_DIR).resolve()


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404)
    return path


def _json(root: Path, relative: str) -> dict:
    path = _inside(root, relative)
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


@router.get("/api/present/manifest")
def manifest(request: Request, presenter: int = 0):
    if request.app.state.public:
        raise HTTPException(404)
    root = _root()
    video = bool(settings.VIDEO_MODE)
    slide_dir = _inside(root, "slides")
    slides = [] if video else [
        "/present/" + path.relative_to(root).as_posix()
        for path in sorted(slide_dir.glob("*.png"))
        if path.is_file() and path.resolve().is_relative_to(root)
    ]
    clips = {}
    for key in ("a1", "a2"):
        clip = {"available": not video and _inside(root, f"clip/{key}.mp4").is_file(),
                "subtitles": not video and _inside(root, f"clip/{key}.subs.json").is_file()}
        if presenter == 1 and not video:
            meta = _json(root, f"clip/{key}.meta.json")
            clip.update({field: meta[field] for field in ("source_line", "speaker_line")
                         if isinstance(meta.get(field), str)})
        clips[key] = clip
    present = _json(root, "present.json")
    translations = _json(root, "translations.json")
    return {"slides": slides, "clips": clips,
            "names": not video and _inside(root, "names.json").is_file(),
            "translations": {k: v for k, v in translations.items() if isinstance(v, str)},
            "present": {k: present[k] for k in ("pr1_third_target", "labeler", "approver")
                        if k in present}}


@router.get("/present/{asset:path}")
def media(asset: str, request: Request):
    if request.app.state.public:
        raise HTTPException(404)
    root = _root()
    if settings.VIDEO_MODE or not re.fullmatch(
        r"(?:slides/[^/\\.][^/\\]*\.png|clip/a[12]\.(?:mp4|subs\.json))", asset
    ) or ".." in asset:
        raise HTTPException(404)
    path = _inside(root, asset)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)
