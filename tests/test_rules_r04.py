import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rule():
    for name in ("textnorm", "core.models"):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name not in (name, name.split(".")[0]):
                raise
            pytest.skip(f"not ready: {name}")
    from rules import R04
    return R04


def atom(text):
    from textnorm import match_key
    return SimpleNamespace(text=text, key=match_key(text)[0], axis="cong_dung",
                           speaker_role="khong_ro", role_span=None,
                           category_norm="TPBVSK", as_of="2026-10-21", turn_text=text)


@pytest.mark.parametrize("text", [
    "uống một tháng là chữa khỏi hẳn tiểu đường", "hỗ trợ điều trị tiểu đường",
    "giảm dần phụ thuộc thuốc tây", "chữa bệnh nhân", "phòng bệnh tiểu đường",
    "chẩn đoán bệnh", "đặc trị tiểu đường", "trị bệnh", "giảm nhẹ bệnh", "thay thế thuốc",
])
def test_positive(rule, text):
    hits = rule.check(atom(text))
    assert hits
    assert all(hit.matched in text and not hit.ambiguous_negation for hit in hits)


@pytest.mark.parametrize("text", [
    "giá trị dinh dưỡng", "trị số đường huyết", "trị giá", "quản trị", "sửa chữa",
    "có chứa", "duy trì", "phòng bệnh viện", "phòng bệnh nhân", "phòng bệnh án",
    "phòng bệnh xá", "trị", "chữa", "khỏi", "chữa bệnhnhân", "dieu tri",
    "không chữa bệnh", "chẳng hỗ trợ điều trị", "chưa dùng để chữa khỏi",
    "đừng dùng để chữa bệnh", "không phải thuốc tây",
])
def test_negative(rule, text):
    assert rule.check(atom(text)) == []


@pytest.mark.parametrize("separator", [",", ";", ".", "nhưng", "mà"])
def test_negation_does_not_cross_clause(rule, separator):
    hits = rule.check(atom(f"không bán {separator} chữa bệnh"))
    assert len(hits) == 1 and not hits[0].ambiguous_negation


def test_ambiguous_negation(rule):
    hits = rule.check(atom("không cần dùng thêm thứ gì để chữa bệnh"))
    assert len(hits) == 1 and hits[0].ambiguous_negation
    hits = rule.check(atom("chữa bệnh không cần thuốc"))
    assert hits[0].ambiguous_negation


def test_source_offsets_and_whole_syllables(rule):
    text = "✨ CHỮA   BỆNH rồi điều trị!"
    assert [hit.matched for hit in rule.check(atom(text))] == ["CHỮA   BỆNH", "điều trị"]


def test_disclaimer_lcs(rule):
    from rules._common import is_disclaimer
    assert is_disclaimer("Thực phẩm không phải là thuốc và không có tác dụng thay thế thuốc chữa bệnh")
    assert is_disclaimer("Thực phẩm này không phải là thuốc, không có tác dụng thay thế thuốc chữa bệnh")
    assert not is_disclaimer("thuốc chữa bệnh")
    # Reversed words cannot satisfy an in-order subsequence.
    assert not is_disclaimer("bệnh chữa thuốc thế thay dụng tác có không và thuốc là phải không này phẩm thực")


def golden_cases():
    return [json.loads(line) for line in (ROOT / "data/cases/golden.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]


def test_golden(rule):
    cases = golden_cases()
    positives = [case for case in cases if "R04" in case["rule_ids"]]
    negatives = [case for case in cases if case["label"] != "red"]
    misses = [case["case_id"] for case in positives if not rule.check(atom(case["text"]))]
    false_hits = [case["case_id"] for case in negatives if rule.check(atom(case["text"]))]
    print(f"R04 golden: {len(positives) - len(misses)}/{len(positives)}; "
          f"false hits: {len(false_hits)}/{len(negatives)}; misses: {misses}")
    assert (len(positives), len(negatives), len(misses), false_hits) == (11, 37, 3, [])
    for case in cases:
        if case["case_id"] in ("dk_betics_gold-018", "dk_betics_gold-029"):
            assert rule.check(atom(case["text"])) == []


def test_reenactment(rule):
    lines = (ROOT / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8").splitlines()
    turns = [line.split("`", 1)[0] for line in lines if re.match(r"\*\*\[KOL \d+\]\*\*", line)]
    assert len(turns) == 24
    assert all(not rule.check(atom(text)) for text in turns)


def test_law_verbatim_and_meta(rule):
    from textnorm import law_normalize
    meta = json.loads((ROOT / "rules/R04.meta.json").read_text(encoding="utf-8"))
    anchors = json.loads((ROOT / "data/law/index.json").read_text(encoding="utf-8"))
    anchor = next(row for row in anchors if row["id"] == rule.ANCHORS[0])
    body = (ROOT / "data/law" / anchor["file"]).read_text(encoding="utf-8").split("---", 2)[2]
    assert law_normalize(rule.__doc__) in law_normalize(body)
    assert rule.LAW_SPAN == meta["law_span"]
    assert law_normalize(rule.LAW_SPAN) in law_normalize(anchor["text"])


def test_role_words():
    from rules.roles import ROLE_WORDS
    assert set(ROLE_WORDS) == {"bac_si", "duoc_si", "nguoi_benh"}
    assert {"Giáo sư", "Phó Giáo sư", "Tiến sĩ", "Đại học Dược", "ĐH Dược"} <= set(ROLE_WORDS["duoc_si"])
