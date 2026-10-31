"""Build date-specific dossier views from explicitly permitted source fields."""

from collections.abc import Iterable
from datetime import date
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path

from core.enums import AxisGroup, CategoryNorm
from core.models import Context as CoreContext, DossierView, PermittedItem
from core.paths import EVIDENCE_DIR, KERA_DIR, LAW_DIR
from textnorm import match_key, nfc


class Context(CoreContext):
    def prompt_view(self, groups: Iterable[AxisGroup | str]) -> list[dict[str, str]]:
        selected = set(groups)
        return [{"permitted_id": item.permitted_id, "text": item.text}
                for item in self.P if item.axis_group in selected]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence_files() -> dict[str, Path]:
    # Unresolved input is only a lookup key, never a filesystem path.
    return {path.stem: path for path in sorted(EVIDENCE_DIR.glob("*.json"))}


def _category(value: str) -> CategoryNorm:
    return {"TPBVSK": CategoryNorm.TPBVSK,
            "thuc_pham_bo_sung": CategoryNorm.THUC_PHAM_BO_SUNG,
            "thực phẩm bổ sung": CategoryNorm.THUC_PHAM_BO_SUNG}.get(
                value, CategoryNorm.UNKNOWN)


def _kera_items(dossier):
    for field in ("name_on_label", "category", "pack_size"):
        item = dossier["product"][field]
        yield "ho_so", item["value"], item["confidence"], f"product.{field}.value"

    ingredients = dossier["ingredients_declared"]
    item = ingredients["summary"]
    yield "thanh_phan", item["value"], item["confidence"], "ingredients_declared.summary.value"
    vegetables = ingredients["ten_vegetable_powders"]
    for index, text in enumerate(vegetables["value"]):
        yield ("thanh_phan", text, vegetables["confidence"],
               f"ingredients_declared.ten_vegetable_powders.value[{index}]")
    for index, item in enumerate(ingredients["other_declared_ingredients"]):
        yield ("thanh_phan", item["value"], item["confidence"],
               f"ingredients_declared.other_declared_ingredients[{index}].value")
    nutrition = dossier["nutrition_declared"]
    for index, row in enumerate(nutrition["rows"]):
        text = f"{row['name']}: {row['value']} / {nutrition['per']}"
        yield "thanh_phan", text, nutrition["confidence"], f"nutrition_declared.rows[{index}]"


def _evidence_items(evidence):
    for field, group in (("permitted_claims", "cong_dung"),
                         ("ingredients_declared", "thanh_phan"),
                         ("usage_declared", "lieu_dung"), ("usage", "lieu_dung")):
        for index, item in enumerate(evidence.get(field, [])):
            yield group, item["text"], item["confidence"], f"{field}[{index}].text"


def _optional_module(name: str):
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name or (exc.name and name.startswith(exc.name + ".")):
            return None
        raise


def _law_set(as_of: date) -> list[str]:
    guardrail = _optional_module("pipeline.guardrail")
    if guardrail is None:
        return []
    return [row["id"] for row in _read_json(LAW_DIR / "index.json")
            if guardrail.is_effective(row["id"], as_of)]


def _session_flags(ctx: Context, dossier: DossierView, pack):
    guardrail = _optional_module("pipeline.guardrail")
    if guardrail is None:
        return []
    if pack is None:
        registry = _optional_module("rules.registry")
        if registry is None:
            return []
        ledger = _optional_module("ledger")
        if ledger is None:
            return []
        row = ledger.current_pack()
        pack = registry.load_pack(row.active, quarantined=row.quarantined, mode="live")
    return [finalized for flag in pack.check_session(dossier, ctx.as_of)
            if (finalized := guardrail.finalize_flag(flag, ctx)) is not None]


def build_context(sku_id: str | None, as_of: date | str, pack=None) -> Context:
    """Build S0; an explicit runtime pack keeps measuring runs off the ledger."""
    as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    path = _evidence_files().get(sku_id)
    category = CategoryNorm.UNKNOWN
    state = "NONE"
    rows = ()
    null_fields = []
    dossier_view = DossierView(sku_id=sku_id or "", category_norm=category)

    if sku_id == "kera":
        dossier = _read_json(KERA_DIR / "dossier.json")
        evidence = _read_json(EVIDENCE_DIR / "kera.json")
        category = _category(dossier["product"]["category"]["value"])
        state = next(row["state"] for row in dossier["timepoint_states"]
                     if (row["from"] is None or row["from"] <= as_of.isoformat())
                     and (row["to"] is None or as_of.isoformat() <= row["to"]))
        dossier_view = DossierView(
            sku_id=sku_id, category_norm=category,
            ad_confirmation_status=evidence["ad_confirmation"]["status"],
            declaration_date=dossier["validity"]["valid_from"]["value"],
            recall_date=dossier["validity"]["recalled_on"]["value"],
        )
        if state == "HIỆU_LỰC":
            rows = _kera_items(dossier)
            null_fields = [group for field, group in (
                ("target_users", "doi_tuong"), ("usage_instructions", "lieu_dung"),
                ("function_claim_in_dossier", "cong_dung"))
                if dossier["label_claims"][field]["value"] is None]
    elif path is not None:
        evidence = _read_json(path)
        category = _category(evidence["category"])
        state = "PRESENT"
        rows = _evidence_items(evidence)
        recall_dates = [item["date"] for item in evidence.get("recalls", [])
                        if item.get("date") is not None]
        dossier_view = DossierView(
            sku_id=sku_id, category_norm=category,
            ad_confirmation_status=evidence["ad_confirmation"]["status"],
            recall_date=min(recall_dates) if recall_dates else None,
        )

    permitted = []
    reference_only = []
    for group, text, confidence, source_field in rows:
        item = {"axis_group": group, "text": nfc(text),
                "confidence": confidence, "source_field": source_field}
        if confidence == "low":
            reference_only.append(item)
        else:
            number = 1 + sum(previous.axis_group == group for previous in permitted)
            permitted.append(PermittedItem(permitted_id=f"{sku_id}:{group}:{number}", **item))

    canonical = json.dumps([(item.permitted_id, item.text) for item in permitted],
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ctx = Context(sku_id=sku_id, category_norm=category, as_of=as_of,
                  dossier_state=state, P=permitted, reference_only=reference_only,
                  null_fields=null_fields, law_set=_law_set(as_of),
                  dossier_hash=sha256(canonical.encode("utf-8")).hexdigest())
    ctx.session_flags = _session_flags(ctx, dossier_view, pack)
    return ctx


def resolve_sku(text: str) -> str | None:
    """Resolve whole-syllable aliases from the closed set of evidence products."""
    key, _ = match_key(text)
    if not key:
        return None
    aliases = []
    for sku_id, path in _evidence_files().items():
        evidence = _read_json(path)
        names = [evidence["product_name"], *evidence.get("trade_names_seen", [])]
        if sku_id == "kera":
            names.extend(_read_json(KERA_DIR / "dossier.json")["product"]["trade_names_seen"])
        aliases.extend((match_key(name)[0], sku_id) for name in names)
    matches = {sku for alias, sku in aliases if alias and f" {alias} " in f" {key} "}
    return next(iter(matches)) if len(matches) == 1 else None
