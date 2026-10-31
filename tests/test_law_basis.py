"""Validate the static legal-basis table against the imported law corpus."""

import json
from datetime import date
from pathlib import Path
import re
import unicodedata

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _law_normalize(text):
    # Local until the textnorm owner supplies the shared implementation.
    translation = str.maketrans({
        **dict.fromkeys("“”„‟″«»", '"'),
        **dict.fromkeys("‘’‚‛′", "'"),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("\u200b\u200c\u200d\u2060\ufeff\u00ad", None),
    })
    text = unicodedata.normalize("NFC", text).translate(translation)
    return re.sub(r"\s+", " ", text.replace("…", "...")).strip()


def _is_forbidden(anchor, forbidden):
    return anchor in forbidden["exact"] or any(
        anchor == prefix or anchor.startswith(prefix + "/")
        for prefix in forbidden["prefixes"]
    )


def test_all_basis_anchors_exist_and_are_allowed():
    index = {entry["id"]: entry["text"] for entry in _load("data/law/index.json")}
    forbidden = _load("gate/data/forbidden_anchors.json")
    rows = _load("rules/law_basis.json")["rows"]
    keys = [(row["family"], row["category"], row["anchor"]) for row in rows]
    assert len(keys) == len(set(keys)), "Duplicate family/category/anchor row"
    for row in rows:
        assert set(row) == {"family", "category", "anchor", "role", "valid_from", "valid_to"}
        assert row["category"] in {"TPBVSK", "thuc_pham_bo_sung", "any"}
        assert row["role"] in {"primary", "secondary"}
        assert row["anchor"] in index
        assert not _is_forbidden(row["anchor"], forbidden), row
        for field in ("valid_from", "valid_to"):
            if row[field] is not None:
                assert date.fromisoformat(row[field]).isoformat() == row[field]
        if row["valid_from"] and row["valid_to"]:
            assert row["valid_from"] <= row["valid_to"]


def test_family_coverage_and_anchor_order():
    expected = {
        ("R04", "any"): ["LDUOC-2016/D6/K15"],
        ("R05", "TPBVSK"): ["ND15-2018/D27/K6", "ND15-2018/D27/K2"],
        ("R05", "thuc_pham_bo_sung"): ["LQC-2012/D8/K9", "ND15-2018/D27/K2"],
        ("R06", "any"): ["ND15-2018/D26/K1", "ND15-2018/D27/K1"],
        ("R08", "any"): ["ND15-2018/D27/K2", "ND87-2026/D71/K4"],
        ("R09", "any"): ["LQC-2012/D8/K11", "ND87-2026/D50/K2/a"],
        ("L1_PREDECL", "any"): ["ND15-2018/D4/K1", "ND15-2018/D5/K2/b"],
        ("L1_RECALL", "any"): ["ND15-2018/D4/K1", "ND15-2018/D5/K2/b"],
        ("STANDARD", "thuc_pham_bo_sung"): ["ND15-2018/D27/K2"],
        ("STANDARD", "TPBVSK"): ["ND15-2018/D27/K6"],
    }
    grouped = {}
    for row in _load("rules/law_basis.json")["rows"]:
        grouped.setdefault((row["family"], row["category"]), []).append(row)
    assert grouped.keys() == expected.keys()
    for key, anchors in expected.items():
        assert [row["anchor"] for row in grouped[key]] == anchors
        expected_roles = ["primary"] + ["secondary"] * (len(anchors) - 1)
        assert [row["role"] for row in grouped[key]] == expected_roles


def test_applicability_windows():
    windows = {
        ("R08", "ND15-2018/D27/K2"): (None, "2026-05-14"),
        ("R08", "ND87-2026/D71/K4"): ("2026-05-15", None),
        ("R09", "ND87-2026/D50/K2/a"): ("2026-05-15", None),
    }
    for row in _load("rules/law_basis.json")["rows"]:
        assert (row["valid_from"], row["valid_to"]) == windows.get(
            (row["family"], row["anchor"]), (None, None)
        )


def test_category_law_spans_are_verbatim():
    basis = _load("rules/law_basis.json")
    index = {entry["id"]: entry["text"] for entry in _load("data/law/index.json")}
    expected = {
        "thuc_pham_bo_sung": "phù hợp với công dụng, tác dụng của sản phẩm đã được công bố",
        "TPBVSK": "chỉ được quảng cáo phù hợp với nội dung đã được xác nhận",
    }
    assert basis["law_span_by_family_category"] == {"R05": expected, "STANDARD": expected}
    for family, categories in basis["law_span_by_family_category"].items():
        for category, span in categories.items():
            anchors = [row["anchor"] for row in basis["rows"]
                       if row["family"] == family and row["category"] == category]
            assert any(_law_normalize(span) in _law_normalize(index[anchor])
                       for anchor in anchors), (family, category)


def test_forbidden_exact_ids_exist():
    forbidden = _load("gate/data/forbidden_anchors.json")
    index = {entry["id"] for entry in _load("data/law/index.json")}
    assert set(forbidden["exact"]) == {
        "LQC-2012/D8/K3", "LQC-2012/D8/K10", "LQC-2012/D8/K13",
        "LDUOC-2016/D6/K2", "LDUOC-2016/D6/K5/i", "LDUOC-2016/D6/K8",
        "LDUOC-2016/D6/K17", "LDUOC-2016/D6/K18", "LDUOC-2016/D6/K19",
    }
    assert forbidden["prefixes"] == ["L122-2025/D22", "L122-2025/D23", "L122-2025/D24"]
    assert set(forbidden["exact"]) <= index
    assert set(forbidden["prefixes"]) <= index
    assert len(forbidden["exact"]) == len(set(forbidden["exact"]))


@pytest.mark.parametrize(("anchor", "expected"), [
    ("L122-2025/D22", True),
    ("L122-2025/D22/K1", True),
    ("L122-2025/D23/K2/a", True),
    ("L122-2025/D24/K1", True),
    ("L122-2025/D220/K1", False),
    ("L122-2025/D2", False),
    ("LQC-2012/D8/K3", True),
    ("LQC-2012/D8/K30", False),
])
def test_forbidden_matching_uses_whole_path_segments(anchor, expected):
    assert _is_forbidden(anchor, _load("gate/data/forbidden_anchors.json")) is expected


def test_private_normalization_preserves_case_and_diacritics():
    assert _law_normalize('  “đa\u0301m”\u00a0‘mây’—x\u200b\u200c\u200d\u2060\ufeff\u00ad…\n') == '"đám" \'mây\'-x...'
    assert _law_normalize("“”„‟″«» ‘’‚‛′ ‐‑‒–—―−") == '\"\"\"\"\"\"\" \'\'\'\'\' -------'
    assert _law_normalize("Thông báo") != _law_normalize("thông báo")
    assert _law_normalize("hóa") != _law_normalize("hoá")
