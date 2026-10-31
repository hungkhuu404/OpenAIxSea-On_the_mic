"""Quảng cáo có sử dụng các từ ngữ “nhất”, “duy nhất”, “tốt nhất”, “số một” hoặc từ ngữ có ý nghĩa tương tự mà không có tài liệu hợp pháp chứng minh theo quy định của Bộ Văn hóa, Thể thao và Du lịch."""

import re

RULE_ID = "R09"
FAMILY = "R09"
ORIGIN = "law_compiled"
ANCHORS = ["LQC-2012/D8/K11", "ND87-2026/D50/K2/a"]
LAW_SPAN = "duy nhất"
PRECEDENCE = 9
TRIGGER_PATTERN = (
    r"(?<!\w)(?:duy nhất|số một|số 1|hàng đầu"
    r"|đầu tiên (?:tại|ở|trên)"
    r"|(?!(?:ít|thống) nhất(?!\w))\w+ nhất (?:hiện|thị|việt|thế|trên)"
    r")(?!\w)"
)


def check(atom_view):
    from rules._common import make_hit, syllable_matches

    phrases = tuple(dict.fromkeys(
        match.group() for match in re.finditer(TRIGGER_PATTERN, atom_view.key)
    ))
    return [make_hit(
        rule_id=RULE_ID, family=FAMILY,
        matched=match.matched,
        law_anchor=ANCHORS[0], law_span=LAW_SPAN,
        precedence=PRECEDENCE, ambiguous_negation=False,
    ) for match in syllable_matches(atom_view.text, phrases)]
