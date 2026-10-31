"""Shared normalization must preserve accents and original source boundaries."""

import unicodedata

import pytest

from textnorm import (
    TEENCODE_VERSION,
    fold,
    law_normalize,
    locate,
    match_key,
    nfc,
    syllables,
)


def test_nfc():
    assert nfc("đu\u031bo\u031b\u0323c") == "được"
    assert nfc("") == ""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Khoẻ đẹp", "khỏe đẹp"),
        ("hoá", "hóa"),
        ("hoà", "hòa"),
        ("thuỷ", "thủy"),
        ("luỹ", "lũy"),
        ("HOÁ, KHOẺ! THUỶ", "hóa khỏe thủy"),
    ],
)
def test_open_vowel_cluster_tone_placement(source, expected):
    assert match_key(source)[0] == match_key(expected)[0] == expected
    assert match_key(unicodedata.normalize("NFD", source))[0] == expected


@pytest.mark.parametrize("cluster", ["oa", "oe", "uy"])
@pytest.mark.parametrize("tone", ["\u0300", "\u0301", "\u0303", "\u0309", "\u0323"])
def test_all_tones_on_open_clusters(cluster, tone):
    first, second = cluster
    old = nfc("h" + first + second + tone)
    new = nfc("h" + first + tone + second)
    assert match_key(old)[0] == match_key(new)[0] == new


@pytest.mark.parametrize("word", ["quý", "quỳ", "quỹ", "quỷ", "quỵ", "quy", "hoán", "thuỵt", "hoé2"])
def test_tones_are_not_moved_outside_open_clusters(word):
    assert match_key(word)[0] == word


@pytest.mark.parametrize(("left", "right"), [("chữa", "chứa"), ("thuốc", "thuộc"), ("đĩa", "dia")])
def test_matching_never_folds_diacritics(left, right):
    assert match_key(left)[0] != match_key(right)[0]


def test_punctuation_and_unicode_whitespace():
    assert match_key(" \tA_b—c…d«e»\u00a0\n f! ")[0] == "a b c d e f"
    assert match_key("KERA. Thực phẩm bổ sung SUPERGREENS GUMMIES")[0] == match_key(
        "KERA — Thực phẩm bổ sung SUPERGREENS GUMMIES"
    )[0]
    assert match_key("a+b=2$")[0] == "a+b=2$"


@pytest.mark.parametrize("source", ["", " \t\n", "…—,_!«»"])
def test_empty_keys(source):
    assert match_key(source) == ("", [])


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ko", "không"),
        ("k0", "không"),
        ("hok", "không"),
        ("khong", "không"),
        ("dc", "được"),
        ("đc", "được"),
        ("sp", "sản phẩm"),
        ("vs", "với"),
        ("j", "gì"),
        ("SP này KO tốt", "sản phẩm này không tốt"),
        ("(sp),ko!", "sản phẩm không"),
    ],
)
def test_versioned_whole_token_teencode(source, expected):
    assert TEENCODE_VERSION == "1"
    assert match_key(source)[0] == match_key(expected)[0] == expected


def test_teencode_does_not_change_longer_tokens():
    assert match_key("koko sp2 dcx vsx j2 khongbao")[0] == "koko sp2 dcx vsx j2 khongbao"


@pytest.mark.parametrize("source", ["đĩa rau", "dĩa rau", "DIA RAU", " ĐĨA\t RAU "])
def test_fold_is_for_accent_insensitive_search(source):
    assert fold(source) == "dia rau"
    assert fold(unicodedata.normalize("NFD", source)) == "dia rau"


def test_fold_preserves_punctuation_and_removes_combining_marks():
    assert fold("Đ, A\u0301\u0323!\u00a0B") == "d, a! b"
    assert fold("") == ""


def test_law_normalize():
    assert law_normalize("“x” — y…") == '"x" - y...'
    assert law_normalize("a \n  b") == "a b"
    assert law_normalize(" \u00a0A\u2009\u3000B\t ") == "A B"
    assert law_normalize("“”„‟″«»") == '"' * 7
    assert law_normalize("‘’‚‛′") == "'" * 5
    assert law_normalize("‐‑‒–—―−") == "-" * 7
    assert law_normalize("a\u200b\u200c\u200d\u2060\ufeff\u00adb") == "ab"
    assert law_normalize("") == ""


def test_law_normalize_preserves_case_tone_placement_and_punctuation():
    source = "Thông báo: sức khoẻ; hàng hoá, thuỷ."
    assert law_normalize(unicodedata.normalize("NFD", source)) == source
    assert law_normalize("hoá") != law_normalize("hóa")


def test_syllables():
    assert syllables("sản phẩm không tốt") == ["sản", "phẩm", "không", "tốt"]
    assert syllables("") == []


def test_locate_returns_original_case():
    text = "…tương ứng với một ĐĨA RAU luộc của mọi người"
    source = "một ĐĨA RAU luộc"
    start = text.index(source)
    assert locate("một đĩa rau luộc", text) == (start, start + len(source))


def test_locate_prefers_exact_match_even_after_an_earlier_normalized_match():
    assert locate("hóa", "hoá rồi hóa") == (8, 11)
    assert locate("", "abc") == (0, 0)


@pytest.mark.parametrize(
    ("span", "text", "source"),
    [
        ("khỏe đẹp", "!KHOẺ—ĐẸP?", "KHOẺ—ĐẸP"),
        ("hóa", "[hoá]", "hoá"),
        ("thủy", "…thuỷ!", "thuỷ"),
        ("sản phẩm này không tốt", "(SP này KO tốt)!", "SP này KO tốt"),
        ("sản phẩm", "sp!", "sp"),
        ("phẩm", "sp!", "sp"),
        ("sản", "sp!", "sp"),
        ("không", "ko!", "ko"),
        ("được", "dc!", "dc"),
        ("phẩm không", "(SP—KO)!", "SP—KO"),
        ("ĐẸP", "[đe\u0323p]", "đe\u0323p"),
        ("Á", "[a\u0301]", "a\u0301"),
        ("Ạ", "[a\u0323]x", "a\u0323"),
        ("Ế", "[e\u0302\u0301]x", "e\u0302\u0301"),
        ("HÓA", "[hoa\u0301]x", "hoa\u0301"),
        ("KHỎE", "[khoe\u0309]x", "khoe\u0309"),
        ("ỎE", "[khoẻ]", "oẻ"),
        ("X\u0301", "[x\u0301]y", "x\u0301"),
        ("VỚI", "[vo\u031b\u0301i]", "vo\u031b\u0301i"),
    ],
)
def test_locate_tracks_composition_tones_expansions_and_endpoints(span, text, source):
    start = text.index(source)
    result = locate(span, text)
    assert result == (start, start + len(source))
    assert text[slice(*result)] == source


@pytest.mark.parametrize(("span", "text"), [("chữa", "chứa"), ("", ""), ("…", "abc"), ("xyz", "abc")])
def test_locate_empty_and_missing_matches(span, text):
    assert locate(span, text) == ((0, 0) if span == "" else None)


@pytest.mark.parametrize(
    "source",
    ["Khoẻ đẹp", "hoa\u0301", "  a\u0301—B! ", "SP ko dc j vs", "A\u0301\u0323", "İ ΣΟΣ", "각"],
)
def test_offset_maps_are_complete_valid_and_deterministic(source):
    key, offsets = match_key(source)
    assert len(key) == len(offsets)
    assert all(isinstance(offset, int) and 0 <= offset < len(source) for offset in offsets)
    assert match_key(source) == (key, offsets)


@pytest.mark.parametrize(
    ("source", "expected_key", "expected_offsets"),
    [
        (" A—B! ", "a b", [1, 2, 3]),
        ("a\u0301 b", "á b", [0, 2, 3]),
        ("hoa\u0301", "hóa", [0, 1, 2]),
        ("KHOẺ", "khỏe", [0, 1, 2, 3]),
    ],
)
def test_offsets_refer_to_original_characters(source, expected_key, expected_offsets):
    assert match_key(source) == (expected_key, expected_offsets)


def test_offset_slice_for_a_match_with_original_punctuation():
    source = "!Một ĐĨA—RAU luộc?"
    key, offsets = match_key(source)
    start = key.index("đĩa rau")
    end = start + len("đĩa rau")
    assert source[offsets[start] : offsets[end - 1] + 1] == "ĐĨA—RAU"
