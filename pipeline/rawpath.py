"""Immediate code-only checks for typed sentences and the public view."""

from dataclasses import asdict
from datetime import datetime


def _typed_session(sku_id, as_of, text):
    from pipeline.ingest import from_text

    source = f"Constructed sentence, entered by the team at {datetime.now():%H:%M}"
    return from_text(sku_id, as_of, text, source)


def check_sentence(sku_id, as_of, text):
    """Check one NFC atom using the live pack and shared router code steps."""
    import ledger
    from core.config import settings
    from core.models import Atom
    from core.paths import ledger_db
    from pipeline.context import build_context
    from pipeline.guardrail import finalize
    from pipeline import router
    from rules.registry import load_pack

    # Standalone callers get the same initial approved pack as application startup.
    if not ledger_db().is_file():
        ledger.init_db(settings.LEDGER_DB)
    row = ledger.current_pack()
    pack = load_pack(row.active, quarantined=row.quarantined, mode="live")
    session = _typed_session(sku_id, as_of, text)
    ctx = build_context(session.sku_pin, session.as_of, pack=pack)
    turn, = session.turns
    atom = Atom(atom_id="t01.a1", turn_id=turn.turn_id, span=turn.text,
                start=0, end=len(turn.text), axis="cong_dung", speaker_role="khong_ro",
                subject_is_product=True)
    view, _, items = router.code_inputs(atom, ctx, turn.text)
    draft = router.step_a(view.key, items)
    if draft is None:
        draft = router.step_b(view, pack, lambda: "unsure")
    if draft is None:
        draft = router.step_d(atom, ctx, items)
    if draft is None:
        draft = router._abstain("LLM_OFF")
    draft.atom_key = f"{session.session_id}:{atom.atom_id}"
    return finalize(asdict(draft), ctx, row.version, pack=pack)


def preflight(sku_id, as_of, text, full=True):
    """Return code results and optionally schedule the full session path."""
    from core.config import settings

    verdict = check_sentence(sku_id, as_of, text)
    if full and not settings.PUBLIC_MODE:
        import ledger
        from pipeline.dossier_events import auto_run

        session = _typed_session(sku_id, as_of, text)
        stored = ledger.add_session(session)
        auto_run(stored.session_id)
    return verdict
