"""Trước khi quảng cáo, tổ chức, cá nhân có sản phẩm quảng cáo phải đăng ký nội dung quảng cáo với cơ quan cấp Giấy tiếp nhận bản đăng ký công bố sản phẩm theo quy định hiện hành."""

RULE_ID = "R06"
FAMILY = "R06"
ORIGIN = "law_compiled"
ANCHORS = ["ND15-2018/D26/K1", "ND15-2018/D27/K1"]
LAW_SPAN = "phải đăng ký nội dung quảng cáo"
PRECEDENCE = 6


def check_session(dossier_view, as_of):
    if (dossier_view.category_norm == "TPBVSK"
            and dossier_view.ad_confirmation_status == "absent"):
        from rules._common import SessionFlag

        return [SessionFlag(
            flag_code="NO_AD_CONFIRMATION", rule_id=RULE_ID, verdict="red",
            legal_basis=ANCHORS,
            message="The product has no advertising content confirmation.",
        )]
    return []
