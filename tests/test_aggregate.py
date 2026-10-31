"""S3 aggregation keeps atom colours and the source of each decision visible."""

from datetime import datetime, timedelta
import itertools
import json
from pathlib import Path

import pytest

from core.models import SessionFlag, Verdict
from pipeline import aggregate


ROOT = Path(__file__).resolve().parents[1]


def _atoms():
    return [Verdict.model_validate(row) for row in json.loads(
        (ROOT / "fixtures" / "verdicts_fake.json").read_text(encoding="utf-8"))]


def _flag(rule_id="R06"):
    return SessionFlag(flag_code="NO_AD_CONFIRMATION", rule_id=rule_id,
                       message="Synthetic flag")


@pytest.mark.parametrize("colours", list(itertools.permutations(["red", "yellow", "abstain", "green"])))
def test_turn_severity_is_independent_of_atom_order(colours):
    assert aggregate.turn_verdict([{"verdict": colour} for colour in colours]) == "red"


@pytest.mark.parametrize(("colours", "expected"), [
    ([], None), (["green"], "green"), (["green", "green"], "green"),
    (["abstain", "green"], "abstain"), (["green", "abstain", "yellow"], "yellow"),
    (["yellow", "red"], "red"),
])
def test_turn_severity_and_empty_turn(colours, expected):
    assert aggregate.turn_verdict({"verdict": colour} for colour in colours) == expected


def test_session_flags_do_not_recolour_turns():
    green = _atoms()[0]
    colour = aggregate.turn_verdict([green])
    assert colour == "green"
    assert aggregate.case_verdict([colour], []) == "green"
    assert aggregate.case_verdict([colour], [_flag()]) == "red"
    assert aggregate.case_decided_by([colour], [green], [_flag()]) == "rule:R06"
    assert green.verdict == "green"


def test_empty_cases_and_flag_only_cases():
    assert aggregate.case_verdict([None], []) is None
    assert aggregate.case_decided_by([], [], []) is None
    assert aggregate.case_verdict([], [_flag()]) == "red"
    assert aggregate.case_decided_by([None], [], [_flag()]) == "rule:R06"


def test_attribution_ties_keep_first_atom_in_turn_then_atom_order():
    atoms = _atoms()
    second_red = atoms[1].model_copy(update={"atom_key": "synthetic:t02.a1", "decided_by": "rule:R08"})
    ordered = [atoms[0], atoms[1], second_red]
    assert aggregate.case_decided_by(["red", "red"], ordered, []) == "rule:R04"
    assert aggregate.case_decided_by(["red", "red"], [second_red, atoms[1]], []) == "rule:R08"
    assert aggregate.case_decided_by(["red"], [atoms[1]], [_flag()]) == "rule:R04"
    assert aggregate.case_decided_by(["green"], [atoms[0]], [_flag("R06"), _flag("L1_RECALL")]) == "rule:R06"


def test_case_attribution_for_yellow_abstain_and_green():
    atoms = _atoms()
    assert aggregate.case_decided_by(["green", "yellow", "abstain"],
                                      [atoms[0], atoms[2], atoms[3]], []) == "llm_votes"
    assert aggregate.case_decided_by(["abstain", "abstain"], atoms[3:], []) == "abstain:DOSSIER_FIELD_MISSING"
    assert aggregate.case_decided_by([None, "green"], [atoms[0]], []) == "allow_list"
    with pytest.raises(ValueError, match="No atom"):
        aggregate.case_decided_by(["red"], [atoms[0]], [])


def test_who_decided_fixture_counts_and_timestamp():
    before = datetime.now().astimezone()
    result = aggregate.who_decided(iter(_atoms()))
    assert (result.code, result.llm_votes, result.human, result.denominator) == (2, 1, 2, 5)
    assert result.code + result.llm_votes + result.human == result.denominator
    measured = datetime.fromisoformat(result.measured_at)
    assert measured.utcoffset() == timedelta(0)
    assert before <= measured <= datetime.now().astimezone()
    assert result.source == "ledger"
    empty = aggregate.who_decided([])
    assert (empty.code, empty.llm_votes, empty.human, empty.denominator) == (0, 0, 0, 0)


def test_mapping_records_are_accepted_and_unknown_provenance_is_an_error():
    atoms = [item.model_dump() for item in _atoms()]
    assert aggregate.turn_verdict(atoms) == "red"
    assert aggregate.who_decided(atoms).denominator == 5
    with pytest.raises(ValueError, match="Unknown decision provenance"):
        aggregate.who_decided([{"decided_by": "unknown"}])
