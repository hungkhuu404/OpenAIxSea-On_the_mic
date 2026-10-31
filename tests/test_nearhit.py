import builtins
import json
import re
from pathlib import Path

import pytest

from core.models import AtomView, NearHit
from rules.nearhit import detect
from textnorm import law_normalize, match_key, nfc

ROOT = Path(__file__).resolve().parents[1]


def atom(text, as_of="2026-10-21"):
    text = nfc(text)
    return AtomView(text=text, key=match_key(text)[0], axis="cong_dung",
                    speaker_role="khong_ro", role_span=None,
                    category_norm="TPBVSK", as_of=as_of, turn_text=text)


@pytest.mark.parametrize("left, right, law_span", [
    ("phòng", "bệnh", "phòng bệnh"), ("chữa", "bệnh", "chữa bệnh"),
    ("trị", "bệnh", "điều trị bệnh"), ("ngừa", "bệnh", "phòng bệnh"),
    ("giảm nhẹ", "bệnh", "giảm nhẹ bệnh"), ("thay", "thuốc", "chữa bệnh"),
])
@pytest.mark.parametrize("middle, gap", [("các", 1), ("rất nhiều", 2)])
def test_patterns_and_gaps(left, right, law_span, middle, gap):
    text = f"Nội dung thử: {left} {middle} {right} ngoài da."
    hits = detect(atom(text))
    assert len(hits) == 1
    assert hits[0].model_dump() == dict(pattern=f"{left} … {right}", gap=gap,
                                      law_anchor="LDUOC-2016/D6/K15", law_span=law_span,
                                      span=f"{left} {middle} {right}")


@pytest.mark.parametrize("text", [
    "phòng bệnh", "chữa bệnh", "trị bệnh", "ngừa bệnh", "giảm nhẹ bệnh", "thay thuốc",
    "phòng một hai ba bệnh", "phòng khám bệnh", "dọn phòng cho bệnh nhân",
    "phòng các bệnh viện", "phòng nhiều bệnh án", "phòng các bệnh xá",
    "phòng các bệnhnhân", "phòngnhiều bệnh", "phong cac benh",
    "không phòng các bệnh", "chẳng chữa các bệnh", "chưa trị các bệnh",
    "đừng ngừa các bệnh", "không giảm nhẹ các bệnh", "không thay các thuốc",
])
def test_exclusions(text):
    assert detect(atom(text)) == []


@pytest.mark.parametrize("text, pattern", [
    ("chữa các bệnh nhân", "chữa … bệnh"),
    ("ngừa các bệnh viện", "ngừa … bệnh"),
    ("không bán, phòng các bệnh", "phòng … bệnh"),
    ("không cần dùng thêm gì để phòng các bệnh", "phòng … bệnh"),
])
def test_guards_are_narrow(text, pattern):
    assert [hit.pattern for hit in detect(atom(text))] == [pattern]


def test_all_occurrences_and_source_spans():
    text = "✨ PHÒNG   nhiều BỆNH; phòng nhiều bệnh; phòng SP bệnh."
    hits = detect(atom(text))
    assert [(hit.span, hit.gap) for hit in hits] == [
        ("PHÒNG   nhiều BỆNH", 1), ("phòng nhiều bệnh", 1), ("phòng SP bệnh", 2),
    ]
    assert all(hit.span in text and nfc(hit.span) == hit.span for hit in hits)


def test_overlapping_occurrences_are_not_duplicated():
    assert [(hit.span, hit.gap) for hit in detect(atom("phòng phòng nhiều bệnh"))] == [
        ("phòng phòng nhiều bệnh", 2), ("phòng nhiều bệnh", 1),
    ]
    assert len(detect(atom("phòng nhiều bệnh, phòng nhiều bệnh"))) == 2


def test_disclaimer_exemption():
    text = "Thực phẩm này không phải là thuốc, không có tác dụng thay thế thuốc chữa bệnh"
    assert detect(atom(text)) == []


def test_golden():
    cases = [json.loads(line) for line in (ROOT / "data/cases/golden.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    for case in cases:
        as_of = "2025-01-08" if case["sku_id"] == "kera" else case.get("source_date") or "2026-10-21"
        if case["case_id"] == "non_alco-021":
            hits = detect(atom(case["text"], as_of))
            assert [(hit.pattern, hit.gap) for hit in hits] == [("phòng … bệnh", 1)]
        elif case["case_id"] in ("dk_betics_gold-018", "dk_betics_gold-029"):
            assert detect(atom(case["text"], as_of)) == []


def test_reenactment():
    lines = (ROOT / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8").splitlines()
    turns = [line.split("`", 1)[0] for line in lines if re.match(r"\*\*\[KOL \d+\]\*\*", line)]
    assert len(turns) == 24
    assert all(detect(atom(text, "2025-01-08")) == [] for text in turns)


def test_legal_spans():
    anchors = json.loads((ROOT / "data/law/index.json").read_text(encoding="utf-8"))
    anchor = next(row for row in anchors if row["id"] == "LDUOC-2016/D6/K15")
    for text in ("phòng các bệnh", "chữa các bệnh", "trị các bệnh", "ngừa các bệnh",
                 "giảm nhẹ các bệnh", "thay các thuốc"):
        for hit in detect(atom(text)):
            assert hit.span in text
            assert law_normalize(hit.law_span) in law_normalize(anchor["text"])


def test_never_changes_verdict_or_input():
    from rules.R04 import check
    view = atom("phòng các bệnh")
    before = view.model_dump()
    verdicts = check(view)
    hits = detect(view)
    assert hits and all(type(hit) is NearHit for hit in hits)
    assert all(set(hit.model_dump()) == {"pattern", "gap", "law_anchor", "law_span", "span"} for hit in hits)
    assert view.model_dump() == before
    assert check(view) == verdicts == []


def test_collision_file():
    content = (ROOT / "rules/collisions.txt").read_bytes()
    assert not content.startswith(b"\xef\xbb\xbf") and b"\r" not in content
    phrases = content.decode("utf-8").splitlines()
    assert len(phrases) == len(set(phrases)) == 23
    assert all(phrase == phrase.strip() and phrase and "ngừa" not in phrase for phrase in phrases)
    assert all(detect(atom(phrase)) == [] for phrase in phrases)


def test_missing_collaborator_is_not_hidden(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "textnorm":
            raise ModuleNotFoundError("not ready: textnorm", name="textnorm")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="not ready: textnorm"):
        detect(atom_view=type("View", (), {"text": "phòng các bệnh"})())
