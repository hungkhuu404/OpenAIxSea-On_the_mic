import importlib
import json
import re
from pathlib import Path

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


def atom(text, as_of="2026-10-21"):
    from core.models import AtomView
    from textnorm import match_key
    return AtomView(text=text, key=match_key(text)[0], axis="cong_dung",
                    speaker_role="khong_ro", role_span=None,
                    category_norm="TPBVSK", as_of=as_of, turn_text=text)


def case_atom(case):
    as_of = "2025-01-08" if case["sku_id"] == "kera" else case.get("source_date") or "2026-10-21"
    return atom(case["text"], as_of)


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


@pytest.mark.parametrize("phrase, expected", [
    ("sản phẩm", ["SP", "sản phẩm"]), ("sản", ["SP", "sản"]), ("phẩm", ["SP", "phẩm"]),
])
def test_expanded_syllables_keep_full_original_token(rule, phrase, expected):
    from rules._common import syllable_matches
    text = "SP, sản phẩm"
    matches = syllable_matches(text, (phrase,))
    assert [match.matched for match in matches] == expected
    assert all(text[match.start:match.end] == match.matched for match in matches)


@pytest.mark.parametrize("placement", ["before", "after", "both", "same_clause", "no_separator", "fuzzy"])
@pytest.mark.parametrize("trigger", ["chữa khỏi", "chữa bệnh"])
def test_disclaimer_does_not_hide_separate_assertion(rule, placement, trigger):
    from rules._common import DISCLAIMER
    claim = f"Sản phẩm {trigger} tiểu đường"
    texts = {"before": f"{DISCLAIMER}. {claim}.", "after": f"{claim}. {DISCLAIMER}.",
             "both": f"{DISCLAIMER}. {claim}. {DISCLAIMER}.", "same_clause": f"{DISCLAIMER}, {claim}.",
             "no_separator": f"{DISCLAIMER} {claim}.",
             "fuzzy": f"{DISCLAIMER.replace('Thực phẩm này', 'Thực phẩm')}. {claim}."}
    hits = rule.check(atom(texts[placement]))
    assert [hit.matched for hit in hits] == [trigger]
    assert not hits[0].ambiguous_negation


@pytest.mark.parametrize("construction", ["không chỉ", "không những"])
@pytest.mark.parametrize("continuation", ["mà còn", ", mà còn", ", giảm đau mà còn"])
def test_not_only_is_affirmative(rule, construction, continuation):
    hits = rule.check(atom(f"Sản phẩm {construction} chữa bệnh {continuation} giúp ngủ ngon."))
    assert [hit.matched for hit in hits] == ["chữa bệnh"]
    assert not hits[0].ambiguous_negation


@pytest.mark.parametrize("text", [
    "Sản phẩm không chữa bệnh.",
    "Sản phẩm không chỉ không chữa bệnh mà còn gây khó ngủ.",
    "Sản phẩm không những không chữa bệnh mà còn gây khó ngủ.",
    "Sản phẩm không chỉ chữa bệnh. Mà còn giúp ngủ ngon.",
])
def test_real_negation_remains_exempt(rule, text):
    assert rule.check(atom(text)) == []


@pytest.mark.parametrize("prefix", ["Thực phẩm này", "Thực phẩm"])
@pytest.mark.parametrize("separator", [" và ", ", ", ". ", "\n"])
def test_disclaimer_alone_remains_exempt(rule, prefix, separator):
    from rules._common import DISCLAIMER
    text = DISCLAIMER.replace("Thực phẩm này", prefix).replace(" và ", separator)
    assert rule.check(atom(text)) == []


def test_extraction_negation_helper_keeps_default_behaviour():
    from rules._common import negation_scope
    text = "Sản phẩm không chỉ chữa bệnh mà còn giúp ngủ ngon."
    start = len("Sản phẩm không chỉ ")
    end = start + len("chữa bệnh")
    assert negation_scope(text, start, end) == (True, False)
    assert negation_scope(text, start, end, affirmative_pairs=True) == (False, False)


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
    misses = [case["case_id"] for case in positives if not rule.check(case_atom(case))]
    false_hits = [case["case_id"] for case in negatives if rule.check(case_atom(case))]
    print(f"R04 golden: {len(positives) - len(misses)}/{len(positives)}; "
          f"false hits: {len(false_hits)}/{len(negatives)}; misses: {misses}")
    assert (len(positives), len(negatives), len(misses), false_hits) == (11, 37, 3, [])
    for case in cases:
        if case["case_id"] in ("dk_betics_gold-018", "dk_betics_gold-029"):
            assert rule.check(case_atom(case)) == []


def test_reenactment(rule):
    lines = (ROOT / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8").splitlines()
    turns = [line.split("`", 1)[0] for line in lines if re.match(r"\*\*\[KOL \d+\]\*\*", line)]
    assert len(turns) == 24
    assert all(not rule.check(atom(text, "2025-01-08")) for text in turns)


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
