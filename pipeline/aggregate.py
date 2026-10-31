"""Pure S3 colour aggregation and attribution of the deciding verdict."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from core.enums import Verdict as Colour
from core.models import SessionFlag, Verdict, WhoDecided


_SEVERITY = {Colour.GREEN: 0, Colour.ABSTAIN: 1, Colour.YELLOW: 2, Colour.RED: 3}


def _field(record, name):
    return record[name] if isinstance(record, Mapping) else getattr(record, name)


def turn_verdict(verdicts: Iterable[Verdict | Mapping]) -> Colour | None:
    """The most severe atom colour; an atomless turn has no colour."""
    return max((Colour(_field(item, "verdict")) for item in verdicts),
               key=_SEVERITY.__getitem__, default=None)


def case_verdict(turn_verdicts: Iterable[Colour | str | None],
                 session_flags: Iterable[SessionFlag | Mapping]) -> Colour | None:
    """Eval colour including flags. Session callers leave flags out."""
    colours = [Colour(value) for value in turn_verdicts if value is not None]
    colours.extend(Colour(_field(flag, "verdict")) for flag in session_flags)
    return max(colours, key=_SEVERITY.__getitem__, default=None)


def case_decided_by(turn_verdicts: Iterable[Colour | str | None],
                    verdicts: Iterable[Verdict | Mapping],
                    session_flags: Iterable[SessionFlag | Mapping]) -> str | None:
    """Pick the first deciding atom in turn/atom input order, then flags.

    A flag decides only if it makes the sentence colour more severe. A red
    sentence already has a deciding atom, so it keeps that attribution.
    """
    turns, flags = list(turn_verdicts), list(session_flags)
    colour = case_verdict(turns, flags)
    if colour is None:
        return None
    sentence = case_verdict(turns, ())
    if sentence is None or _SEVERITY[colour] > _SEVERITY[sentence]:
        flag = next(flag for flag in flags if _field(flag, "verdict") == colour)
        return f"rule:{_field(flag, 'rule_id')}"
    for item in verdicts:
        if _field(item, "verdict") == colour:
            return str(_field(item, "decided_by"))
    raise ValueError("No atom supplies the sentence colour")


def who_decided(verdicts: Iterable[Verdict | Mapping]) -> WhoDecided:
    """Count provenance without recomputing colours or consulting the ledger."""
    code = votes = human = denominator = 0
    for item in verdicts:
        decided_by = _field(item, "decided_by")
        if decided_by == "allow_list" or decided_by.startswith("rule:"):
            code += 1
        elif decided_by == "llm_votes":
            votes += 1
        elif decided_by.startswith("abstain:"):
            human += 1
        else:
            raise ValueError(f"Unknown decision provenance: {decided_by}")
        denominator += 1
    return WhoDecided(code=code, llm_votes=votes, human=human, denominator=denominator,
                      measured_at=datetime.now(timezone.utc).isoformat(), source="ledger")
