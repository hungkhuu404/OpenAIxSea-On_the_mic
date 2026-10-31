"""Synchronous session computation and cache-only replay.

These calls can block for minutes. App callers must use asyncio.to_thread or
a background thread; never call them directly in a running event loop.

Integration questions: the ledger snapshot adapter below assumes a flat
mapping with Session fields plus atoms, verdicts and flags. H2 must confirm
the aggregate signatures and the router.Draft shape. T4 must expose S1 call
metadata: until then an empty extraction or a missing s1_call_id is treated
as not_evaluated in replay, rather than claiming cache completeness.
"""

from dataclasses import dataclass, field
from importlib import import_module
import logging
from time import monotonic

from core.models import Atom, ReplayResult, Session, SessionFlag, Verdict


logger = logging.getLogger(__name__)


@dataclass
class SessionResult:
    """Local computation result; not a new core or ledger record."""

    session: Session
    atoms: list[Atom] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    flags: list[SessionFlag] = field(default_factory=list)
    turn_verdicts: dict[str, str] = field(default_factory=dict)
    case_verdict: str | None = None
    case_decided_by: str | None = None
    negq_miss: int = 0
    cache_missing: bool = False


def _snapshot(row):
    """Provisional P1 seam: flat session mapping, lists of core records."""
    if not isinstance(row, dict) or not {"atoms", "verdicts", "flags"} <= row.keys():
        raise TypeError("ledger.get_session snapshot shape needs P1 confirmation")
    return SessionResult(
        session=Session.model_validate(row),
        atoms=[Atom.model_validate(item) for item in row["atoms"]],
        verdicts=[Verdict.model_validate(item) for item in row["verdicts"]],
        flags=[SessionFlag.model_validate(item) for item in row["flags"]],
    )


def _live_pack():
    import ledger
    from rules.registry import load_pack

    row = ledger.current_pack()
    return load_pack(row.active, quarantined=row.quarantined, mode="live"), row.version


def _select_pack(pack, pack_version):
    if pack is None:
        return _live_pack()
    if not isinstance(pack_version, int):
        raise ValueError("An explicit pack requires the caller's pack_version")
    return pack, pack_version


def _fallback(turn):
    if not turn.text:
        return []
    return [Atom(atom_id=f"{turn.turn_id}.a1", turn_id=turn.turn_id,
                 span=turn.text, start=0, end=len(turn.text), axis="cong_dung",
                 speaker_role="khong_ro", subject_is_product=True)]


def _compute(session, pack, version, purpose, emit=None, cache_read=True):
    from core.config import settings
    from pipeline.context import build_context
    from pipeline.router import _optional_module, _route_atom

    aggregate = import_module("pipeline.aggregate")
    result = SessionResult(session)

    def step(name, outcome, started, turn_id=None):
        if emit is not None:
            emit("step", dict(session_id=session.session_id, turn_id=turn_id,
                              atom_id=None, step=name, outcome=outcome,
                              ms=(monotonic() - started) * 1000))

    started = monotonic()
    ctx = build_context(session.sku_pin, session.as_of, pack)
    result.flags = list(ctx.session_flags)
    step("S0", ctx.dossier_state, started)
    extract = _optional_module("pipeline.extract")
    previous = None
    for turn in session.turns:
        started = monotonic()
        if extract is None:
            if not (settings.OFFLINE or settings.PUBLIC_MODE or settings.LLM_BACKEND == "off"):
                raise ModuleNotFoundError("not ready: pipeline.extract", name="pipeline.extract")
            atoms = _fallback(turn)
        else:
            atoms = extract.extract_atoms(session, turn, previous, ctx, purpose,
                                          cache_read=cache_read)
        # This conservative sentinel is provisional until T4 defines call metadata.
        s1_missing = not atoms or any(atom.s1_call_id is None for atom in atoms)
        result.cache_missing |= s1_missing
        step("S1", "unverified_cache" if s1_missing else "extracted", started, turn.turn_id)
        turn_results = []
        for atom in atoms:
            routed = _route_atom(atom, ctx, pack, purpose, emit,
                                 session_id=session.session_id, turn_text=turn.text,
                                 rule_pack_version=version, cache_read=cache_read)
            turn_results.append(routed.verdict)
            result.negq_miss += routed.negq_miss
            result.cache_missing |= routed.e_miss
        result.atoms.extend(atoms)
        result.verdicts.extend(turn_results)
        started = monotonic()
        result.turn_verdicts[turn.turn_id] = aggregate.turn_verdict(turn_results)
        step("S3", str(result.turn_verdicts[turn.turn_id]), started, turn.turn_id)
        previous = turn
    result.case_verdict = aggregate.case_verdict(list(result.turn_verdicts.values()), result.flags)
    # Provisional H2 signature; ReplayResult has no case_decided_by field.
    result.case_decided_by = aggregate.case_decided_by(result.verdicts, result.flags)
    return result


def _summarize_existing(result):
    aggregate = import_module("pipeline.aggregate")
    for turn in result.session.turns:
        keys = {f"{result.session.session_id}:{atom.atom_id}" for atom in result.atoms
                if atom.turn_id == turn.turn_id}
        result.turn_verdicts[turn.turn_id] = aggregate.turn_verdict(
            [verdict for verdict in result.verdicts if verdict.atom_key in keys])
    return result


def process_session(session_id, purpose, reprocess=False, *, cache_read=True) -> SessionResult:
    """Compute outside the transaction, then replace/append atomically."""
    if purpose not in ("live", "shadow"):
        raise ValueError("Stored sessions require purpose live or shadow")
    import ledger

    stored = _snapshot(ledger.get_session(session_id))
    # Latest rows include historical atom keys dropped by an earlier reprocess.
    prior = {row.atom_key: row for row in ledger.latest_verdicts(session_id)}
    if prior and not reprocess:
        return _summarize_existing(stored)
    pack, version = _live_pack()
    computed = _compute(stored.session, pack, version, purpose, ledger.append_event, cache_read)
    for verdict in computed.verdicts:
        previous = prior.get(verdict.atom_key)
        verdict.supersedes = previous.verdict_id if previous is not None else None
    with ledger.transaction():
        if reprocess:
            ledger.replace_atoms(session_id, computed.atoms)
        else:
            ledger.add_atoms(session_id, computed.atoms)
        ids = ledger.append_verdicts(computed.verdicts)
        for verdict, verdict_id in zip(computed.verdicts, ids, strict=True):
            verdict.verdict_id = verdict_id
        for turn in computed.session.turns:
            ledger.index_turn(session_id, turn)
        for atom in computed.atoms:
            ledger.index_atom(session_id, atom)
        ledger.add_session_flags(session_id, computed.flags)
    for atom, verdict in zip(computed.atoms, computed.verdicts, strict=True):
        ledger.append_event("verdict", dict(
            verdict_id=verdict.verdict_id, session_id=session_id, turn_id=atom.turn_id,
            atom_id=atom.atom_id, verdict=str(verdict.verdict), decided_by=verdict.decided_by,
            reason_code=str(verdict.reason_code) if verdict.reason_code else None))
    for turn_id, verdict in computed.turn_verdicts.items():
        ledger.append_event("turn.verdict", dict(session_id=session_id, turn_id=turn_id,
                                                 verdict=str(verdict)))
    for flag in computed.flags:
        ledger.append_event("session.flag", dict(session_id=session_id, flag_code=str(flag.flag_code),
                                                 rule_id=flag.rule_id, legal_basis=flag.legal_basis))
    return computed


def _case_session(case):
    from pipeline.ingest import from_eval_case
    from core.models import RegressionItem

    if isinstance(case, RegressionItem):
        case = dict(case_id=case.item_id, sku_id=case.sku_id,
                    source_date=case.as_of.isoformat(), text=case.text)
    return from_eval_case(case)


def _replay_result(computed):
    missing = computed.cache_missing
    return ReplayResult(status="not_evaluated" if missing else "ok",
                        verdicts=[] if missing else computed.verdicts,
                        case_verdict=None if missing else computed.case_verdict,
                        negq_miss=computed.negq_miss)


def run_cases(cases, purpose, pack=None, pack_version=None) -> list[ReplayResult]:
    """In-memory eval path; oracle I/O restrictions are enforced by llm."""
    from pipeline.llm import run_scope

    if purpose == "oracle" and pack is None:
        raise ValueError("Oracle cases require an explicit pack")
    pack, version = _select_pack(pack, pack_version)
    with run_scope(measuring=True):
        return [_replay_result(_compute(_case_session(case), pack, version, purpose))
                for case in cases]


_CHANGED = ("verdict", "decided_by", "reason_code", "permitted_id", "fired_rules", "legal_basis")


def _changed(previous, current):
    return any(previous.model_dump()[key] != current.model_dump()[key] for key in _CHANGED)


def replay(targets, pack=None, persist=False, purpose="live", pack_version=None) -> list[ReplayResult]:
    """Cache-only, with no ledger access for explicit in-memory packs.

    Explicit-pack persistence and explicit-pack session ids are rejected:
    those requests cannot honor the contract's no-ledger guarantee.
    Changed counts and case_decided_by need a ReplayResult contract extension.
    """
    from pipeline.llm import run_scope

    targets = list(targets)
    explicit = pack is not None
    if explicit and (persist or any(isinstance(target, str) for target in targets)):
        raise ValueError("Explicit-pack replay cannot access the ledger; pass eval cases with persist=False")
    if purpose == "oracle" and (not explicit or persist or any(isinstance(t, str) for t in targets)):
        raise ValueError("Oracle replay requires an explicit pack and in-memory cases")
    pack, version = _select_pack(pack, pack_version)
    if persist and (pack.mode != "live" or purpose != "live"):
        raise ValueError("Only a live pack with purpose live may persist")
    computed = []
    with run_scope(offline=True, measuring=not persist):
        for target in targets:
            if isinstance(target, str):
                import ledger
                session = _snapshot(ledger.get_session(target)).session
            else:
                session = _case_session(target)
            computed.append(_compute(session, pack, version, purpose))
    results = [_replay_result(item) for item in computed]
    if persist:
        import ledger
        pending = []
        changed_cases = 0
        for item, result in zip(computed, results, strict=True):
            if result.status != "ok":
                continue
            prior = {row.atom_key: row for row in ledger.latest_verdicts(item.session.session_id)}
            changed = []
            for verdict in item.verdicts:
                previous = prior.get(verdict.atom_key)
                if previous is None or _changed(previous, verdict):
                    if previous is not None:
                        verdict.supersedes = previous.verdict_id
                        verdict.flags = list(dict.fromkeys([*previous.flags, *verdict.flags]))
                    changed.append(verdict)
            changed_cases += bool(changed)
            pending.extend(changed)
        if pending:
            with ledger.transaction():
                ids = ledger.append_verdicts(pending)
                for verdict, verdict_id in zip(pending, ids, strict=True):
                    verdict.verdict_id = verdict_id
        logger.info("backfill changed_cases=%s changed_atoms=%s", changed_cases, len(pending))
    return results
