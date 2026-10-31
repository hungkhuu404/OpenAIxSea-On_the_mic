import ast
from datetime import date
import importlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
RULE_IDS = ("R05", "R06", "R08", "R09", "L1_PREDECL", "L1_RECALL")


def ready(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name or name.startswith(exc.name + "."):
            pytest.skip(f"not ready: {name}")
        raise


def rule(rule_id):
    return importlib.import_module(f"rules.{rule_id}")


def dossier(**changes):
    fields = dict(sku_id="demo_sku", category_norm="TPBVSK",
                  ad_confirmation_status="present", declaration_date=None,
                  recall_date=None)
    fields.update(changes)
    return SimpleNamespace(**fields)


def atom(text, **changes):
    textnorm = ready("textnorm")
    fields = dict(text=text, key=textnorm.match_key(text)[0], axis="tuyet_doi",
                  speaker_role="khong_ro", role_span=None,
                  category_norm="TPBVSK", as_of=date(2026, 10, 21),
                  turn_text=text)
    fields.update(changes)
    return SimpleNamespace(**fields)


@pytest.fixture
def records(monkeypatch):
    # Exercise rule decisions independently while the shared records are landing.
    models = ready("core.models")
    monkeypatch.setitem(sys.modules, "rules._common", SimpleNamespace(
        make_hit=models.RuleHit,
        make_session_flag=lambda **fields: models.SessionFlag(**fields),
    ))


@pytest.mark.parametrize("category", ["TPBVSK", "thuc_pham_bo_sung", "unknown"])
@pytest.mark.parametrize("status", ["present", "absent", "unknown", None])
def test_r06_category_and_status(records, category, status):
    hits = rule("R06").check_session(
        dossier(category_norm=category, ad_confirmation_status=status),
        date(2026, 10, 21),
    )
    assert bool(hits) == (category == "TPBVSK" and status == "absent")
    if hits:
        assert hits[0].flag_code == "NO_AD_CONFIRMATION"
        assert hits[0].verdict == "red"


@pytest.mark.parametrize("rule_id,field,day,expected", [
    ("L1_PREDECL", "declaration_date", date(2024, 12, 22), True),
    ("L1_PREDECL", "declaration_date", date(2024, 12, 23), False),
    ("L1_PREDECL", "declaration_date", date(2025, 1, 8), False),
    ("L1_RECALL", "recall_date", date(2025, 3, 23), False),
    ("L1_RECALL", "recall_date", date(2025, 3, 24), True),
    ("L1_RECALL", "recall_date", date(2025, 3, 25), True),
])
def test_session_date_boundaries(records, rule_id, field, day, expected):
    event = date(2024, 12, 23) if field == "declaration_date" else date(2025, 3, 24)
    hits = rule(rule_id).check_session(dossier(**{field: event}), day)
    assert bool(hits) == expected
    if hits:
        assert hits[0].verdict == "red"
        assert hits[0].legal_basis == rule(rule_id).ANCHORS


@pytest.mark.parametrize("rule_id", ["L1_PREDECL", "L1_RECALL"])
def test_missing_dossier_or_date(records, rule_id):
    assert rule(rule_id).check_session(None, date(2025, 1, 8)) == []
    assert rule(rule_id).check_session(dossier(), date(2025, 1, 8)) == []


@pytest.mark.parametrize("role,expected", [
    ("bac_si", True), ("duoc_si", True), ("nguoi_benh", True),
    ("khong_ro", False), ("kol", False), ("nguoi_ban", False),
])
def test_r08_uses_only_verified_role(records, role, expected):
    # No text/key/role_span attribute: consulting those fields would fail.
    hits = rule("R08").check(SimpleNamespace(speaker_role=role))
    assert bool(hits) == expected
    if hits:
        assert hits[0].matched == ""


def test_r05_is_metadata_only():
    assert rule("R05").check(object()) == []


@pytest.mark.parametrize("text", [
    "sản phẩm duy nhất hiện nay trên thị trường", "số 1 Việt Nam",
    "số một Việt Nam", "hàng đầu", "đầu tiên tại Việt Nam",
    "đầu tiên ở Việt Nam", "đầu tiên trên thị trường",
    "tin dùng nhất hiện nay", "tốt nhất thị trường", "tốt nhất Việt Nam",
    "tốt nhất thế giới", "tốt nhất trên thị trường",
])
def test_r09_positive(text):
    ready("rules._common")
    hits = rule("R09").check(atom(text))
    assert hits
    assert all(h.matched and h.matched in text for h in hits)


@pytest.mark.parametrize("text", [
    "Uống ít nhất 2 viên mỗi ngày", "nhất là người lớn tuổi", "thống nhất",
    "nhất định phải dùng", "sản phẩm tốt nhất", "đầu tiên dùng thử",
    "ít nhất hiện nay", "thống nhất trên thị trường", "số 10", "số 11",
    "duy nhấtđịnh", "đầu tiên tạigia", "tốt nhất hiệnđại", "so 1 Viet Nam",
])
def test_r09_negative(text):
    ready("rules._common")
    assert rule("R09").check(atom(text)) == []


def test_r09_source_span_preserves_original():
    ready("rules._common")
    text = "Đây là SỐ   1 Việt Nam!"
    hits = rule("R09").check(atom(text))
    assert len(hits) == 1
    assert hits[0].matched == "SỐ   1"


def test_r09_repeated_phrase_keeps_scope():
    ready("rules._common")
    hits = rule("R09").check(atom("tốt nhất hiện nay, hãy chọn thứ tốt nhất"))
    assert len(hits) == 1
    assert hits[0].matched == "tốt nhất hiện"


def test_golden_r09_and_reenactment():
    ready("rules._common")
    cases = [json.loads(line) for line in
             (ROOT / "data/cases/golden.jsonl").read_text(encoding="utf-8").splitlines()]
    targets = [c for c in cases if "R09" in c["rule_ids"]]
    non_red = [c for c in cases if c["label"] != "red"]
    def fired(case):
        as_of = ("2025-01-08" if case["sku_id"] == "kera"
                 else case.get("source_date") or "2026-10-21")
        return bool(rule("R09").check(atom(case["text"], as_of=date.fromisoformat(as_of))))
    misses = [c["case_id"] for c in targets if not fired(c)]
    false_hits = [c["case_id"] for c in non_red if fired(c)]
    print(f"R09 golden: {len(targets) - len(misses)}/{len(targets)}; "
          f"false fires: {len(false_hits)}/{len(non_red)}; "
          f"misses={misses}; false_hits={false_hits}")
    assert len(targets) == 8 and len(non_red) == 37
    assert misses == false_hits == []
    negative = next(c for c in cases if c["case_id"] == "dk_betics_gold-028")
    assert not fired(negative)
    lines = (ROOT / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8").splitlines()
    turns = [line.split("`", 1)[0] for line in lines if re.match(r"\*\*\[KOL \d+\]\*\*", line)]
    assert len(turns) == 24
    assert all(not rule("R09").check(atom(text, as_of=date(2025, 1, 8))) for text in turns)


def test_real_dossiers(records):
    for sku, expected in [("non_alco", True), ("dk_betics_gold", False), ("kera", False)]:
        source = json.loads((ROOT / f"data/evidence/{sku}.json").read_text(encoding="utf-8"))
        view = dossier(sku_id=sku, category_norm=source["category"],
                       ad_confirmation_status=source["ad_confirmation"]["status"])
        assert bool(rule("R06").check_session(view, date(2026, 10, 21))) == expected
    source = json.loads((ROOT / "data/demo/kera/dossier.json").read_text(encoding="utf-8"))
    view = dossier(sku_id="kera", category_norm="thuc_pham_bo_sung",
                   declaration_date=date.fromisoformat(source["validity"]["valid_from"]["value"]),
                   recall_date=date.fromisoformat(source["validity"]["recalled_on"]["value"]))
    assert rule("L1_PREDECL").check_session(view, date(2024, 12, 12))
    assert not rule("L1_PREDECL").check_session(view, date(2025, 1, 8))
    assert rule("L1_RECALL").check_session(view, date(2025, 3, 25))


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_metadata_and_verbatim_law(rule_id):
    normalize = ready("textnorm").law_normalize
    module = rule(rule_id)
    meta = json.loads((ROOT / f"rules/{rule_id}.meta.json").read_text(encoding="utf-8"))
    for const, field in [("RULE_ID", "id"), ("FAMILY", "family"), ("ORIGIN", "origin"),
                         ("ANCHORS", "anchors"), ("LAW_SPAN", "law_span"),
                         ("PRECEDENCE", "precedence")]:
        assert vars(module)[const] == meta[field]
    anchors = {row["id"]: row for row in json.loads(
        (ROOT / "data/law/index.json").read_text(encoding="utf-8"))}
    texts = [normalize(anchors[a]["text"]) for a in module.ANCHORS]
    phrases = module.LAW_SPAN if isinstance(module.LAW_SPAN, list) else [module.LAW_SPAN]
    assert all(any(normalize(p) in t for t in texts) for p in phrases)
    for piece in re.split(r"…|\.\.\.", module.__doc__):
        quote = normalize(piece)
        assert any(quote in t for t in texts)
        assert any(quote in normalize((ROOT / "data/law" / anchors[a]["file"])
                   .read_text(encoding="utf-8").split("---", 2)[2]) for a in module.ANCHORS)
    basis_path = ROOT / "rules/law_basis.json"
    if not basis_path.exists():
        pytest.skip("not ready: rules/law_basis.json")
    basis = json.loads(basis_path.read_text(encoding="utf-8"))
    assert set(module.ANCHORS) <= {r["anchor"] for r in basis["rows"]
                                    if r["family"] == module.FAMILY}


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_rule_imports_are_pure(rule_id):
    tree = ast.parse((ROOT / f"rules/{rule_id}.py").read_text(encoding="utf-8"))
    allowed = {"re", "unicodedata", "typing", "dataclasses", "functools",
               "itertools", "textnorm", "rules._common"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "compile"


def test_runtime_records_and_registry():
    ready("rules._common")
    registry = ready("rules.registry")
    models = ready("core.models")
    view = models.DossierView(sku_id="demo_sku", category_norm="TPBVSK",
                             ad_confirmation_status="absent",
                             declaration_date=date(2027, 1, 1),
                             recall_date=date(2025, 3, 24))
    for rule_id in RULE_IDS:
        pack = registry.load_pack([rule_id], mode="measure")
        if rule_id in ("R06", "L1_PREDECL", "L1_RECALL"):
            hits = pack.check_session(view, date(2026, 10, 21))
            assert len(hits) == 1 and isinstance(hits[0], models.SessionFlag)
        elif rule_id in ("R08", "R09"):
            av = models.AtomView(**vars(atom("số 1 Việt Nam", speaker_role="duoc_si")))
            hits = pack.check_atom(av)
            assert len(hits) == 1 and isinstance(hits[0], models.RuleHit)
        else:
            assert pack.check_atom(atom("Example")) == []
            assert pack.check_session(view, date(2026, 10, 21)) == []
