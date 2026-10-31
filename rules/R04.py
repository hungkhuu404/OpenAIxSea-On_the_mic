"""15. Thông tin, quảng cáo, tiếp thị, kê đơn, tư vấn, ghi nhãn, hướng dẫn sử dụng có nội dung dùng để phòng bệnh, chữa bệnh, chẩn đoán bệnh, điều trị bệnh, giảm nhẹ bệnh, điều chỉnh chức năng sinh lý cơ thể người đối với sản phẩm không phải là thuốc, trừ trang thiết bị y tế."""

from rules._common import excluded_right, is_disclaimer, make_hit, negation_scope, syllable_matches

RULE_ID = "R04"
FAMILY = "R04"
ORIGIN = "law_compiled"
ANCHORS = ["LDUOC-2016/D6/K15"]
LAW_SPAN = "chữa bệnh"
PRECEDENCE = 4
TRIGGERS = (
    "phòng bệnh", "chữa bệnh", "chữa khỏi", "chẩn đoán bệnh", "điều trị",
    "đặc trị", "trị bệnh", "giảm nhẹ bệnh", "thuốc tây", "thay thế thuốc",
)
ROOM_CONTEXT = ("viện", "nhân", "án", "xá")


def check(atom_view):
    if is_disclaimer(atom_view.text):
        return []
    hits = []
    for match in syllable_matches(atom_view.text, TRIGGERS):
        if match.phrase == "phòng bệnh" and excluded_right(atom_view.text, match, ROOM_CONTEXT):
            continue
        negated, ambiguous = negation_scope(atom_view.text, match.start, match.end)
        if not negated:
            hits.append(make_hit(RULE_ID, FAMILY, match.matched, ANCHORS[0], LAW_SPAN,
                                 PRECEDENCE, ambiguous_negation=ambiguous))
    return hits
