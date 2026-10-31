"""Ngay sau khi tự công bố sản phẩm, tổ chức, cá nhân được quyền sản xuất, kinh doanh sản phẩm và chịu trách nhiệm hoàn toàn về an toàn của sản phẩm đó;"""

RULE_ID = "L1_RECALL"
FAMILY = "L1_RECALL"
ORIGIN = "law_compiled"
ANCHORS = ["ND15-2018/D4/K1", "ND15-2018/D5/K2/b"]
LAW_SPAN = "chịu trách nhiệm hoàn toàn về an toàn của sản phẩm đó"
PRECEDENCE = 0


def check_session(dossier_view, as_of):
    if (dossier_view is not None and dossier_view.recall_date is not None
            and dossier_view.recall_date <= as_of):
        from rules._common import SessionFlag

        return [SessionFlag(
            flag_code="RECALLED_AT_AS_OF", rule_id=RULE_ID, verdict="red",
            legal_basis=ANCHORS,
            message="The product was recalled on or before the broadcast date.",
        )]
    return []
