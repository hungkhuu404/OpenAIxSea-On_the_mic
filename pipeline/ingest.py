"""Side-effect-free adapters that build sessions without opening the ledger."""

from datetime import date
import hashlib
import json
from pathlib import Path
import re

from core import paths
from core.models import Session, Turn
from textnorm import nfc


def _date(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def _turn(text):
    return Turn(turn_id="t01", speaker_label="[unidentified]", attribution="not_applicable",
                text=nfc(text))


def from_reenactment(path: Path, sku_id, as_of):
    as_of = _date(as_of)
    claims = {}
    for line in (paths.KERA_DIR / "claims_quoted.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            claim = json.loads(line)
            claims[claim["claim_id"]] = claim["speaker_role"]
    turns = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\*\*(\[[^]]+\])\*\*\s*(.*)", line)
        if match is None:
            continue
        speaker, rest = match.groups()
        tags = re.findall(r"`([^`]*)`", rest)
        tag_text = nfc(" ".join(tags))
        source_ids = list(dict.fromkeys(re.findall(r"\bkera-q-\d{3}\b", tag_text)))
        if "dựng thêm" in tag_text.lower():
            attribution = "constructed"
        elif "người nói trong nguồn: không rõ" in tag_text.lower():
            attribution = "press_unknown"
        elif source_ids and all(claims.get(key) == speaker for key in source_ids):
            attribution = "press_attributed"
        else:
            attribution = "press_unknown"
        turns.append(Turn(turn_id=f"t{len(turns) + 1:02}", speaker_label=speaker,
            attribution=attribution, source_claim_ids=source_ids,
            text=nfc(rest.split("`", 1)[0].strip())))
    return Session(session_id=f"kera-reenact-{as_of}", sku_pin=sku_id, as_of=as_of,
        channel="livestream", source="Reenactment from press and court quotes - not an original recording",
        turns=turns)


def from_eval_case(case):
    as_of = "2025-01-08" if case["sku_id"] == "kera" else case.get("source_date") or "2026-10-21"
    return Session(session_id=f"case-{case['case_id']}", sku_pin=case["sku_id"], as_of=_date(as_of),
        channel="written" if case.get("modality") == "written" else "livestream",
        source=f"Public evaluation case {case['case_id']}", turns=[_turn(case["text"])])


def from_text(sku_id, as_of, text, source):
    as_of, text = _date(as_of), nfc(text)
    digest = hashlib.sha256(f"{sku_id}|{as_of}|{text}".encode("utf-8")).hexdigest()[:8]
    return Session(session_id=f"text-{digest}", sku_pin=sku_id, as_of=as_of,
                   channel="written", source=source, turns=[_turn(text)])
