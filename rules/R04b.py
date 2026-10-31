"""Thông tin, quảng cáo, tiếp thị, kê đơn, tư vấn, ghi nhãn, hướng dẫn sử dụng có nội dung dùng để phòng bệnh, chữa bệnh, chẩn đoán bệnh, điều trị bệnh, giảm nhẹ bệnh, điều chỉnh chức năng sinh lý cơ thể người đối với sản phẩm không phải là thuốc, trừ trang thiết bị y tế."""

import re

from textnorm import match_key
from rules._common import disclaimer_spans, excluded_right, make_hit, negation_scope, syllable_matches

RULE_ID = "R04b"
FAMILY = "R04"
ORIGIN = "t5"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "phòng bệnh"
PRECEDENCE = 4


def check(atom_view):
    text = atom_view.text
    key, _ = match_key(text)
    phrases = tuple(dict.fromkeys(
        match.group() for match in re.finditer(
            r"(?<!\w)ngăn ngừa (?:\w+ ){0,2}bệnh(?!\w)", key)
    ))
    disclaimers = disclaimer_spans(text)
    scope_text = text
    for start, end in disclaimers:
        scope_text = scope_text[:start] + " " * (end - start) + scope_text[end:]
    hits = []
    for match in syllable_matches(text, phrases):
        # A gap must remain within the same uninterrupted clause.
        if re.search(r"[,;.!?\n]", match.matched):
            continue
        if excluded_right(text, match, ("viện", "nhân", "án", "xá")):
            continue
        if any(start <= match.start and match.end <= end for start, end in disclaimers):
            continue
        negated, ambiguous = negation_scope(
            scope_text, match.start, match.end, affirmative_pairs=True)
        if not negated:
            hits.append(make_hit(
                RULE_ID, FAMILY, match.matched, ANCHORS[0], LAW_SPAN,
                PRECEDENCE, ambiguous_negation=ambiguous))
    return hits
