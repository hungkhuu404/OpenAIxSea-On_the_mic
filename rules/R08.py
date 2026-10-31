"""Không sử dụng hình ảnh, thiết bị, trang phục, tên, thư tín của các đơn vị, cơ sở y tế, bác sỹ, dược sỹ, nhân viên y tế, thư cảm ơn của người bệnh, bài viết của bác sỹ, dược sỹ, nhân viên y tế để quảng cáo thực phẩm."""

RULE_ID = "R08"
FAMILY = "R08"
ORIGIN = "law_compiled"
ANCHORS = ["ND15-2018/D27/K2", "ND87-2026/D71/K4"]
LAW_SPAN = ["bác sỹ, dược sỹ", "người bệnh"]
PRECEDENCE = 8
ROLES = ("bac_si", "duoc_si", "nguoi_benh")


def check(atom_view):
    if atom_view.speaker_role not in ROLES:
        return []
    from rules._common import make_hit

    # The verified role may come from the previous turn, outside this atom.
    return [make_hit(
        rule_id=RULE_ID, family=FAMILY, matched="", law_anchor=ANCHORS[0],
        law_span=LAW_SPAN[0], precedence=PRECEDENCE, ambiguous_negation=False,
    )]
