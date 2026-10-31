"""6. Tổ chức, cá nhân có sản phẩm quảng cáo; tổ chức, cá nhân phát hành quảng cáo chỉ được tiến hành quảng cáo sản phẩm đã được cấp Giấy xác nhận nội dung quảng cáo và chỉ được quảng cáo phù hợp với nội dung đã được xác nhận.
…
2. Nội dung quảng cáo phải phù hợp với công dụng, tác dụng của sản phẩm đã được công bố trong bản công bố sản phẩm. Không sử dụng hình ảnh, thiết bị, trang phục, tên, thư tín của các đơn vị, cơ sở y tế, bác sỹ, dược sỹ, nhân viên y tế, thư cảm ơn của người bệnh, bài viết của bác sỹ, dược sỹ, nhân viên y tế để quảng cáo thực phẩm.
…
9. Quảng cáo không đúng hoặc gây nhầm lẫn về khả năng kinh doanh, khả năng cung cấp sản phẩm, hàng hóa, dịch vụ của tổ chức, cá nhân kinh doanh sản phẩm, hàng hóa, dịch vụ; về số lượng, chất lượng, giá, công dụng, kiểu dáng, bao bì, nhãn hiệu, xuất xứ, chủng loại, phương thức phục vụ, thời hạn bảo hành của sản phẩm, hàng hóa, dịch vụ đã đăng ký hoặc đã được công bố."""

RULE_ID = "R05"
FAMILY = "R05"
ORIGIN = "law_compiled"
ANCHORS = ['ND15-2018/D27/K6', 'ND15-2018/D27/K2', 'LQC-2012/D8/K9']
LAW_SPAN = ["chỉ được quảng cáo phù hợp với nội dung đã được xác nhận",
            "phù hợp với công dụng, tác dụng của sản phẩm đã được công bố"]
PRECEDENCE = 50


def check(atom_view):
    return []
