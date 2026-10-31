from dataclasses import dataclass
import unicodedata

from rules.R04b import check


@dataclass
class View:
    text: str


def test_sources_and_source_spans():
    sources = (
        "Kiên trì dùng giúp ngăn ngừa các bệnh tim mạch",
        "Sản phẩm giúp ngăn ngừa nhiều bệnh do đường huyết cao",
        "Dùng lâu dài để ngăn ngừa tái phát bệnh tiểu đường",
    )
    for text in sources:
        hits = check(View(text))
        assert len(hits) == 1
        hit = hits[0]
        assert hit.rule_id == "R04b"
        assert hit.family == "R04"
        assert hit.matched in text
        assert hit.law_anchor == "LDUOC-2016/D6/K15"
        assert hit.law_span == "phòng bệnh"
        assert not hit.ambiguous_negation


def test_near_misses_and_mutations():
    for text in (
        "Ngăn ngừa các nguy cơ gây bệnh",
        "Ngăn ngừa oxy hóa và giúp người bệnh",
        "Ngăn ngứa các bệnh tim mạch",
        "Ngăn ngừa các bềnh tim mạch",
        "Ngăn ngừa các bênh tim mạch",
        "Có chứa dưỡng chất thuộc nhóm thực phẩm",
        "Ngăn ngừa nhiều, bệnh do đường huyết cao",
        "Ngăn ngừa. Các bệnh tim mạch",
        "Ngăn ngừa các bệnh viện quá tải",
        "Ngăn ngừa nhiều bệnh nhân nhập viện",
    ):
        assert not check(View(text)), text
    for text in (
        "NGĂN NGỪA CÁC BỆNH tim mạch",
        unicodedata.normalize("NFD", "ngăn ngừa tái phát bệnh"),
    ):
        hits = check(View(text))
        assert len(hits) == 1
        assert hits[0].matched in text


def test_negation_and_disclaimer():
    disclaimer = "Thực phẩm này không phải là thuốc và không có tác dụng thay thế thuốc chữa bệnh"
    assert not check(View(disclaimer))
    assert not check(View("Không ngăn ngừa các bệnh tim mạch"))
    assert not check(View("Sản phẩm chẳng ngăn ngừa nhiều bệnh"))
    hits = check(View("Không rõ sản phẩm có thể ngăn ngừa các bệnh tim mạch"))
    assert len(hits) == 1
    assert hits[0].ambiguous_negation
    hits = check(View(disclaimer + ". Sản phẩm ngăn ngừa các bệnh tim mạch"))
    assert len(hits) == 1
    assert not hits[0].ambiguous_negation


def test_all_supplied_collisions():
    for text in (
        "giá trị dinh dưỡng", "trị số đường huyết", "trị giá", "quản trị",
        "chính trị", "sửa chữa", "chữa cháy", "có chứa", "duy trì",
        "phòng bệnh viện", "phòng bệnh nhân", "phòng khám bệnh", "phòng live",
        "phòng chờ", "bệnh viện", "bệnh nhân", "bệnh án", "thuốc lá",
        "thuốc nhuộm", "khỏi lo", "khỏi phải", "thay thế bữa ăn", "chẩn đoán lỗi",
    ):
        assert not check(View(text)), text


def test_all_supplied_permitted_items():
    for text in (
        "hỗ trợ giảm triệu chứng ợ hơi, ợ chua do viêm loét dạ dày, tá tràng, hỗ trợ giảm acid dịch vị, hỗ trợ bảo vệ niêm mạc dạ dày...",
        "hỗ trợ giảm và duy trì đường huyết ổn định; giúp giảm nguy cơ biến chứng do tiểu đường.",
        "Hỗ trợ giảm và duy trì đường huyết ổn định. Giúp giảm nguy cơ biến chứng do tiểu đường.",
        "Mỗi viên nang cứng 500mg có chứa: Cao Dây thìa canh lá to 350mg",
        "KERA — Thực phẩm bổ sung SUPERGREENS GUMMIES", "thực phẩm bổ sung",
        "30 viên | 96 g (3,2 g x 30 viên)",
        "22 nguyên liệu, trong đó 10 loại bột rau củ quả chiếm 28,13%",
        "cải bó xôi", "rau má", "chùm ngây", "diếp cá", "cần tây", "chuối xanh",
        "cà rốt", "bí ngô", "khoai lang tím", "táo xanh", "chiết xuất từ lá cỏ ngọt",
        "Maxicuma (Curcumin 40%)", "Vitamin A, B6, B12",
        "hương táo (nhãn không ghi rõ tự nhiên/giống tự nhiên/tổng hợp/nhân tạo)",
        "phụ gia acid citric (nhãn chưa ghi tên nhóm chất phụ gia)",
        "Năng lượng (Energy): 10 kcal / 3,2 g (1 viên)",
        "Chất đạm (Protein): 0,287 g / 3,2 g (1 viên)",
        "Carbohydrate: 2,29 g / 3,2 g (1 viên)",
        "Đường tổng số (Total sugar): 0,995 g / 3,2 g (1 viên)",
        "Chất béo (Total fat): 0,011 g / 3,2 g (1 viên)",
        "Natri (Sodium): 6,43 mg / 3,2 g (1 viên)",
        "hỗ trợ thanh nhiệt, giải độc gan; hỗ trợ giảm các triệu chứng vàng da, mụn nhọt, nổi mề đay do chức năng gan kém; hỗ trợ giảm các biểu hiện nôn, khát, khô cổ ở người uống bia, rượu.",
    ):
        assert not check(View(text)), text
