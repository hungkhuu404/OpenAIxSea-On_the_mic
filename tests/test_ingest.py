"""Public source provenance and side-effect-free session builders."""

from collections import Counter
from datetime import date
import hashlib
import json

import pytest

from core import paths
from pipeline import ingest


def test_reenactment_preserves_turn_text_and_attribution():
    session = ingest.from_reenactment(paths.KERA_DIR / "reenactment_script.md", "kera", "2025-01-08")
    assert session.session_id == "kera-reenact-2025-01-08"
    assert session.channel == "livestream" and len(session.turns) == 24
    assert {turn.speaker_label for turn in session.turns} == {"[KOL 1]", "[KOL 2]", "[KOL 3]"}
    assert session.turns[5].text == "Trong mỗi viên kẹo có 10 loại rau củ quả khác nhau cùng với các loại vitamin"
    assert session.turns[13].text == 'Mặt trước lọ có ghi câu: "Khoẻ đẹp tự nhiên từ chất xơ".'
    assert session.turns[10].source_claim_ids == ["kera-q-022"]
    constructed = {1, 2, 3, 4, 9, 14, 18, 22, 23, 24}
    unknown = {13, 19, 20}
    for number, turn in enumerate(session.turns, 1):
        assert turn.turn_id == f"t{number:02}"
        expected = "constructed" if number in constructed else "press_unknown" if number in unknown else "press_attributed"
        assert turn.attribution == expected
        assert "`" not in turn.text
    assert Counter(turn.attribution for turn in session.turns) == {
        "constructed": 10, "press_unknown": 3, "press_attributed": 11}


def test_reenactment_relocated_path_with_spaces(tmp_path):
    folder = tmp_path / "space.dot folder"
    folder.mkdir()
    destination = folder / "script.md"
    destination.write_bytes((paths.KERA_DIR / "reenactment_script.md").read_bytes())
    assert len(ingest.from_reenactment(destination, "kera", date(2025, 1, 8)).turns) == 24


def test_missing_or_mismatched_claim_provenance_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "KERA_DIR", tmp_path)
    claims = [dict(claim_id="kera-q-101", speaker_role="[KOL 2]")]
    (tmp_path / "claims_quoted.jsonl").write_text(json.dumps(claims[0]), encoding="utf-8")
    script = tmp_path / "script.md"
    script.write_text('**[KOL 1]** Synthetic. `[source: kera-q-101]`\n'
                      '**[KOL 1]** Synthetic. `[source: kera-q-999]`\n'
                      '**[KOL 1]** Synthetic.\n', encoding="utf-8")
    result = ingest.from_reenactment(script, "kera", "2025-01-08")
    assert [turn.attribution for turn in result.turns] == ["press_unknown"] * 3


@pytest.mark.parametrize("sku, source_date, expected", [
    ("kera", "2026-01-01", date(2025, 1, 8)),
    ("demo_sku", "2024-01-02", date(2024, 1, 2)),
    ("demo_sku", None, date(2026, 10, 21)),
])
def test_eval_date_and_attribution(sku, source_date, expected):
    session = ingest.from_eval_case(dict(case_id="synthetic-1", sku_id=sku,
        source_date=source_date, text="Chứa yến mạch.", modality="written"))
    assert session.session_id == "case-synthetic-1"
    assert session.sku_pin == sku and session.as_of == expected
    assert session.channel == "written"
    assert session.turns[0].attribution == "not_applicable"


def test_text_is_nfc_deterministic_and_does_not_touch_ledger():
    text = "Sản phẩm chứa yến mạch."
    first = ingest.from_text("demo_sku", "2025-01-08", text, "Synthetic input")
    second = ingest.from_text("demo_sku", date(2025, 1, 8), text, "Synthetic input")
    expected = hashlib.sha256(f"demo_sku|2025-01-08|{text}".encode("utf-8")).hexdigest()[:8]
    assert first == second and first.session_id == f"text-{expected}"
    assert first.turns[0].text == text and first.turns[0].attribution == "not_applicable"
    assert not paths.ledger_db().exists()
