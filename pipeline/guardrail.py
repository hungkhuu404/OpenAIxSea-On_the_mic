"""Step F: source-checked quotations, dated legal basis, and safe card text.

This module reads only local law and basis files. Decisions arrive from the
router; F can retain them or abstain, and never promotes an LLM result to red.
"""

from collections.abc import Mapping
from datetime import date, datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re

from pydantic import BaseModel

from core.enums import Verdict as Colour
from core.models import Context, SessionFlag, Verdict
from core.paths import LAW_DIR, REPO_ROOT


LAW_INDEX_FILE = LAW_DIR / "index.json"
LAW_BASIS_FILE = REPO_ROOT / "rules" / "law_basis.json"
_EFFECTIVE_DATES: dict[Path, date] = {}
_AMOUNT = r"\d[\d.,\s]*\s*đồng"
_CATEGORIES = {
    "R04": "Disease-treatment claim for a non-drug",
    "R08": "Expert or patient endorsement",
    "R09": "Superlative without proof",
    "R05": "Overclaim beyond the dossier",
    "R06": "No ad-content confirmation",
    "L1_PREDECL": "Sold before self-declaration",
    "L1_RECALL": "Dossier recalled",
}
_FLAG_DETAILS = {
    "NO_SELF_DECLARATION_AT_AS_OF": (
        "L1_PREDECL", "No self-declaration on file at the broadcast date",
        "Sold before self-declaration", "Trust & Safety review"),
    "NO_AD_CONFIRMATION": (
        "R06", "Health-protection food advertised without an ad-content confirmation",
        "No ad-content confirmation", "Stop the stream (suggested)"),
    "RECALLED_AT_AS_OF": (
        "L1_RECALL", "Dossier recalled before the broadcast date",
        "Dossier recalled", "Trust & Safety review"),
}


def _record(value: Mapping | BaseModel) -> dict:
    return dict(value) if isinstance(value, Mapping) else value.model_dump()


@lru_cache(maxsize=None)
def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _index(path: Path) -> dict[str, dict]:
    return {row["id"]: row for row in _read_json(path)}


def display_text(anchor_id: str) -> str:
    """Return law text with penalty lead-ins and every monetary amount removed."""
    text = _index(LAW_INDEX_FILE)[anchor_id]["text"]
    opening = (rf"Phạt\s+tiền\s+từ\s+{_AMOUNT}\s+đến\s+{_AMOUNT}\s+đối\s+với\s+"
               r"(?:một\s+trong\s+các\s+hành\s+vi\s+sau\s+đây\s*:|hành\s+vi)\s*")
    text = re.sub(opening, "", text, flags=re.IGNORECASE)
    text = re.sub(_AMOUNT, "", text, flags=re.IGNORECASE)
    return re.sub(r"^\s*\d+\.\s*", "", text).strip()


def is_effective(anchor_id: str, as_of: date) -> bool:
    """Read and cache only the top-level effective_from front-matter scalar."""
    law_file = LAW_DIR / _index(LAW_INDEX_FILE)[anchor_id]["file"]
    if law_file not in _EFFECTIVE_DATES:
        lines = law_file.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---":
            raise ValueError(f"Missing law front matter for {anchor_id}")
        dates = []
        for line in lines[1:]:
            if line == "---":
                break
            match = re.fullmatch(r"effective_from:[ \t]*(\d{4}-\d{2}-\d{2})[ \t]*", line)
            if match:
                dates.append(date.fromisoformat(match[1]))
        else:
            raise ValueError(f"Unclosed law front matter for {anchor_id}")
        if len(dates) != 1:
            raise ValueError(f"Expected one top-level effective date for {anchor_id}")
        _EFFECTIVE_DATES[law_file] = dates[0]
    return _EFFECTIVE_DATES[law_file] <= as_of


def _legal_basis(family: str, category: str, as_of: date) -> list[str]:
    rows = [row for row in _read_json(LAW_BASIS_FILE)["rows"] if row["family"] == family]
    if not rows:
        raise ValueError(f"No legal-basis rows for family {family}")
    kept = []
    for row in sorted(rows, key=lambda row: row["role"] != "primary"):
        if row["category"] not in ("any", category):
            continue
        if row["valid_from"] and as_of < date.fromisoformat(row["valid_from"]):
            continue
        if row["valid_to"] and as_of > date.fromisoformat(row["valid_to"]):
            continue
        anchor = row["anchor"]
        if is_effective(anchor, as_of) and anchor not in kept:
            kept.append(anchor)
    return kept


def _rule_meta(rule_id: str):
    try:
        from rules.registry import all_rules
    except ModuleNotFoundError as exc:
        if exc.name not in {"rules", "rules.registry"}:
            raise
        return None
    for info in all_rules():
        if info.rule_id == rule_id:
            return info.meta
    raise ValueError(f"Deciding rule is missing from the registry: {rule_id}")


def _checked_law_span(span: str | list[str] | None, anchors: list[str]) -> str | None:
    if not span:
        return None
    try:
        from textnorm import law_normalize
    except ModuleNotFoundError as exc:
        if exc.name != "textnorm":
            raise
        return None
    phrases = [span] if isinstance(span, str) else span
    texts = [law_normalize(display_text(anchor)) for anchor in anchors]
    return next((phrase for phrase in phrases if law_normalize(phrase)
                 and any(law_normalize(phrase) in text for text in texts)), None)


def finalize(draft: Mapping | BaseModel, ctx: Context | Mapping,
             rule_pack_version: int) -> Verdict:
    """Validate a router draft against its dated law and dossier sources."""
    source, context = _record(draft), _record(ctx)
    values = {name: value for name, value in source.items() if name in Verdict.model_fields}
    colour = Colour(values["verdict"])
    if colour == Colour.RED and not values["decided_by"].startswith("rule:"):
        raise ValueError("A red draft must be decided by a rule")
    as_of = context["as_of"]
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    values.update(as_of=as_of, dossier_hash=context["dossier_hash"],
                  rule_pack_version=rule_pack_version, legal_basis=[], law_span=None,
                  qlnd_category=None, action=None)
    values.setdefault("ts", datetime.now(timezone.utc).isoformat())

    permitted_id = (values.get("permitted_id") if colour == Colour.GREEN
                    else values.get("closest_permitted_id") if colour == Colour.YELLOW else None)
    if colour == Colour.GREEN or permitted_id is not None:
        item = next((item for item in context["P"] if item["permitted_id"] == permitted_id), None)
        if item is None:
            raise ValueError(f"Unknown permitted item: {permitted_id}")
        if not values.get("evidence_span") or values["evidence_span"] not in item["text"]:
            values["evidence_span"] = item["text"]
    elif colour == Colour.YELLOW:
        # A yellow draft without a closest item has no dossier quote to show.
        values["evidence_span"] = None

    if colour != Colour.ABSTAIN:
        family = source.get("family")
        if not family:
            raise ValueError("A coloured draft requires a legal-basis family")
        expected_family = {Colour.GREEN: "STANDARD", Colour.YELLOW: "R05"}.get(colour)
        if expected_family is not None and family != expected_family:
            raise ValueError(f"{colour} requires legal-basis family {expected_family}")
        anchors = _legal_basis(family, context["category_norm"], as_of)
        if not anchors:
            colour = Colour.ABSTAIN
            values.update(verdict=colour, reason_code="LAW_NOT_EFFECTIVE",
                          decided_by="abstain:LAW_NOT_EFFECTIVE")
        else:
            values["legal_basis"] = anchors
            if colour == Colour.RED:
                meta = _rule_meta(values["decided_by"].removeprefix("rule:"))
                if meta is not None and meta.family != family:
                    raise ValueError("Draft family disagrees with the deciding rule")
                span = meta.law_span if meta is not None else None
                values.update(qlnd_category=_CATEGORIES.get(meta.family if meta is not None else family),
                              action="Remove or warn")
            else:
                span = _read_json(LAW_BASIS_FILE)["law_span_by_family_category"].get(
                    family, {}).get(context["category_norm"])
            values["law_span"] = _checked_law_span(span, anchors)

    if colour == Colour.YELLOW:
        values.update(qlnd_category=_CATEGORIES["R05"], action="Trust & Safety review")
    elif colour == Colour.ABSTAIN:
        values["action"] = "Send to review queue"
    return Verdict.model_validate(values)


def finalize_flag(flag: SessionFlag | Mapping, ctx: Context | Mapping) -> SessionFlag | None:
    """Return a dated flag, or suppress it when no legal basis remains."""
    values, context = _record(flag), _record(ctx)
    family, message, _, _ = _FLAG_DETAILS[values["flag_code"]]
    if values["rule_id"] != family:
        raise ValueError("Session flag code and rule id disagree")
    as_of = context["as_of"]
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    anchors = _legal_basis(family, context["category_norm"], as_of)
    if not anchors:
        return None
    values.update(legal_basis=anchors, message=message)
    return SessionFlag.model_validate(values)


def flag_card_suggestions(flag: SessionFlag | Mapping) -> dict[str, str]:
    """UI suggestions kept separate from the fixed SessionFlag wire record."""
    _, _, category, action = _FLAG_DETAILS[_record(flag)["flag_code"]]
    return {"qlnd_category": category, "action": action}
