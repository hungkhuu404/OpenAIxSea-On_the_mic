"""Decisions passed from S2/E to F, before F supplies legal and dossier fields."""

from dataclasses import dataclass, field

from core.models import Numbers


@dataclass
class VerdictDraft:
    verdict: str
    decided_by: str
    atom_key: str = ""
    family: str | None = None
    reason_code: str | None = None
    fired_rules: list[str] = field(default_factory=list)
    permitted_id: str | None = None
    closest_permitted_id: str | None = None
    evidence_span: str | None = None
    numbers: Numbers | None = None
    llm_call_ids: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
