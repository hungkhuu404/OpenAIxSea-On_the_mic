"""Runner invariants with synthetic extraction and transport collaborators."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from core import paths
from core.config import settings
from core.enums import AtomFlag
from core.models import Atom, Session, Turn, Verdict
from ledger import SessionView
from pipeline import llm, router, runner
from pipeline.context import Context
from pipeline.drafts import VerdictDraft


def session(text="new claim", session_id="fixture"):
    return Session(session_id=session_id, sku_pin="demo_sku", as_of=date(2025, 1, 8),
                   channel="written", source="Synthetic test", turns=[Turn(
                       turn_id="t01", speaker_label="[KOL 1]", attribution="constructed", text=text)])


def pack(mode="live"):
    return SimpleNamespace(mode=mode, active=(), quarantined=(), check_atom=lambda view: [],
                           quarantined_hits=lambda view: [], check_session=lambda view, day: [])


class Ledger:
    """Fake P1 seam; history and current atom/search views are separate."""

    def __init__(self, value):
        self.session = value
        self.atoms = []
        self.history = []
        self.flags = []
        self.index = {}
        self.events = []
        self.calls = []
        self.transactions = 0
        self.in_transaction = False

    def current_pack(self):
        return SimpleNamespace(active=[], quarantined=[], version=4)

    def get_session(self, session_id):
        assert session_id == self.session.session_id
        keys = {f"{session_id}:{atom.atom_id}" for atom in self.atoms}
        return SessionView(**(self.session.model_dump() | dict(
            atoms=self.atoms, verdicts=[v for v in self.effective_verdicts(session_id)
                                       if v.atom_key in keys], flags=self.flags)))

    def effective_verdicts(self, session_id, pack_version=None):
        version = 4 if pack_version is None else pack_version
        keys = {f"{session_id}:{atom.atom_id}" for atom in self.atoms}
        return list({v.atom_key: v for v in self.history
                     if v.atom_key in keys and v.rule_pack_version <= version}.values())

    def latest_verdicts(self, session_id):
        return list({v.atom_key: v for v in self.history
                     if v.atom_key.startswith(session_id + ":")}.values())

    @contextmanager
    def transaction(self):
        assert not self.in_transaction
        before = deepcopy((self.atoms, self.history, self.flags, self.index, self.events))
        self.in_transaction = True
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.atoms, self.history, self.flags, self.index, self.events = before
            raise
        finally:
            self.in_transaction = False

    def add_atoms(self, session_id, atoms):
        assert self.in_transaction
        self.atoms.extend(deepcopy(atoms))
        for atom in atoms:
            self.index_atom(session_id, atom)

    def replace_atoms(self, session_id, atoms):
        assert self.in_transaction
        self.atoms = []
        self.index.clear()
        self.add_atoms(session_id, atoms)

    def append_verdicts(self, verdicts):
        assert self.in_transaction
        ids = []
        for verdict in verdicts:
            value = verdict.model_copy(deep=True)
            value.verdict_id = len(self.history) + 1
            self.history.append(value)
            ids.append(value.verdict_id)
        return ids

    def index_turn(self, session_id, turn):
        assert self.in_transaction

    def index_atom(self, session_id, atom):
        assert self.in_transaction
        self.index[atom.atom_id] = atom.span

    def replace_session_flags(self, session_id, flags):
        assert self.in_transaction
        self.flags = deepcopy(flags)

    def append_event(self, kind, payload):
        self.events.append((kind, payload))

    def record_llm_call(self, call):
        assert not self.in_transaction
        self.calls.append(call)


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    root = tmp_path / "repo space.dot"
    root.mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", root)
    monkeypatch.setattr(paths, "CACHE_DIR", root / "cache" / "llm")
    monkeypatch.setattr(paths, "LOCAL_DIR", root / ".local")
    monkeypatch.setattr(router, "REPO_ROOT", root)
    monkeypatch.setattr(settings, "LLM_LOCAL_CACHE_DIR", str(root / ".local" / "cache"))
    monkeypatch.setattr(settings, "LEDGER_DB", str(root / ".local" / "ledger.db"))
    monkeypatch.setattr(settings, "LLM_BACKEND", "codex")
    monkeypatch.setattr(settings, "OFFLINE", 0)
    state = SimpleNamespace(spans=None, colour="green", backend=[], purposes=[], extracts=0,
                            gate=None, ledger=None)

    def backend(*args):
        state.backend.append(args)
        response = {"answer": "negated"} if args[5] == "negq" else {"ok": True}
        return SimpleNamespace(response=response, usage=None, error=None)
    monkeypatch.setattr(llm, "_backend", backend)

    def request(kind, text, purpose, cache_read):
        if state.ledger is not None:
            assert not state.ledger.in_transaction
        state.purposes.append(purpose)
        return llm.call(kind, purpose, "synthetic-" + kind, "none", "test_response",
                        [{"role": "user", "content": text}], cache_read=cache_read)

    def extract(value, turn, previous, ctx, purpose="live", cache_read=True):
        state.extracts += 1
        result = request("s1", turn.text, purpose, cache_read)
        if state.gate is not None:
            state.gate[0].set()
            assert state.gate[1].wait(5)
        spans = [turn.text] if result.error is not None or state.spans is None else state.spans
        return [Atom(atom_id=f"{turn.turn_id}.a{i}", turn_id=turn.turn_id, span=text,
                     start=turn.text.index(text), end=turn.text.index(text) + len(text),
                     axis="cong_dung", speaker_role="kol", subject_is_product=True,
                     s1_call_id=result.request_sha256 if result.error is None else None)
                for i, text in enumerate(spans, 1)]

    def judge(value, items, ctx, purpose, cache_read=True):
        result = request("t1", value.span, purpose, cache_read)
        if result.error is not None:
            return router._abstain("LLM_OFF")
        return VerdictDraft(state.colour, "llm_votes", family="STANDARD",
                            permitted_id=items[0]["permitted_id"], llm_call_ids=[result.request_sha256])

    def finalize(draft, ctx, version, *, pack):
        assert isinstance(draft, dict)
        return Verdict(atom_key=draft["atom_key"], rule_pack_version=version,
                       verdict=draft["verdict"], decided_by=draft["decided_by"],
                       reason_code=draft["reason_code"], fired_rules=draft["fired_rules"],
                       permitted_id=draft["permitted_id"], flags=draft["flags"],
                       legal_basis=["synthetic:" + draft["family"]] if draft["family"] else [],
                       llm_call_ids=draft["llm_call_ids"], as_of=ctx.as_of,
                       dossier_hash=ctx.dossier_hash, action="review", ts="2026-01-01T00:00:00Z")

    def build(sku, day, rules):
        assert rules is not None
        return Context(sku_id=sku, as_of=day, category_norm="thuc_pham_bo_sung",
                       dossier_state="PRESENT", dossier_hash="synthetic", P=[dict(
                           permitted_id="demo_sku:cong_dung:1", axis_group="cong_dung",
                           text="permitted", confidence="high", source_field="test")])

    import pipeline.context
    import rules.registry
    monkeypatch.setattr(pipeline.context, "build_context", build)
    monkeypatch.setattr(rules.registry, "load_pack", lambda *args, **kw: pack())
    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=extract))
    monkeypatch.setitem(sys.modules, "pipeline.judge", SimpleNamespace(judge=judge))
    monkeypatch.setitem(sys.modules, "pipeline.guardrail", SimpleNamespace(finalize=finalize))
    monkeypatch.setitem(sys.modules, "rules.nearhit", SimpleNamespace(detect=lambda view: []))

    monkeypatch.setitem(sys.modules, "pipeline.ingest", SimpleNamespace(
        from_eval_case=lambda row: session(row["text"], "case-" + row["case_id"])))

    def forbidden(*args, **kwargs):
        pytest.fail("explicit-pack computation accessed ledger")
    monkeypatch.setitem(sys.modules, "ledger", SimpleNamespace(
        current_pack=forbidden, get_session=forbidden, transaction=forbidden,
        record_llm_call=forbidden))
    return root, state


def install_ledger(monkeypatch, state, value=None):
    fake = Ledger(value or session())
    state.ledger = fake
    monkeypatch.setitem(sys.modules, "ledger", fake)
    return fake


def test_missing_cache_is_not_evaluated_and_never_calls_backend(boundary):
    root, state = boundary
    result, = runner.replay([dict(case_id="one", text="new claim")], pack("measure"), pack_version=1)
    assert (result.status, result.verdicts, result.case_verdict) == ("not_evaluated", [], None)
    assert state.backend == []
    assert not paths.ledger_db().exists() and not (root / ".local").exists()


def test_replay_hit_logs_only_to_local_jsonl_and_passes_purpose(boundary):
    root, state = boundary
    runner.run_cases([dict(case_id="one", text="new claim")], "shadow", pack(), 1)
    before = len(state.backend)
    result, = runner.replay([dict(case_id="one", text="new claim")], pack(), purpose="shadow", pack_version=2)
    assert result.status == "ok" and result.case_verdict == "green"
    assert [v.rule_pack_version for v in result.verdicts] == [2]
    assert len(state.backend) == before == 2
    assert state.purposes == ["shadow"] * 4
    log = root / ".local" / "llm_calls.jsonl"
    assert len(log.read_text(encoding="utf-8").splitlines()) == 4
    assert not paths.ledger_db().exists()


def test_cached_negation_and_separate_negq_miss(boundary):
    _, state = boundary
    from core.models import RuleHit

    rules = pack()
    rules.check_atom = lambda view: [RuleHit(
        rule_id="R04", family="R04", matched="chữa bệnh", law_anchor="synthetic",
        law_span="synthetic", precedence=1, ambiguous_negation=True)]
    case = dict(case_id="negation", text="không chữa bệnh")
    runner.run_cases([case], "shadow", rules, 1)
    count = len(state.backend)
    result, = runner.replay([case], rules, pack_version=1)
    assert result.status == "ok" and result.negq_miss == 0
    assert result.verdicts[0].decided_by == "llm_votes" and len(state.backend) == count == 3
    negq = next(json.loads(path.read_text(encoding="utf-8"))
                for path in paths.CACHE_DIR.glob("*/*.json")
                if json.loads(path.read_text(encoding="utf-8"))["kind"] == "negq")
    llm.cache_path(paths.CACHE_DIR, negq["request_sha256"]).unlink()
    missed, = runner.replay([case], rules, pack_version=1)
    assert missed.status == "ok" and missed.negq_miss == 1
    assert missed.verdicts[0].reason_code == "RULE_AMBIGUOUS"
    assert len(state.backend) == count


def test_offline_process_falls_back_and_finishes(boundary, monkeypatch):
    _, state = boundary
    fake = install_ledger(monkeypatch, state)
    monkeypatch.setattr(settings, "OFFLINE", 1)
    result = runner.process_session("fixture", "live")
    assert [a.span for a in result.atoms] == ["new claim"]
    assert [(v.verdict, v.reason_code) for v in result.verdicts] == [("abstain", "LLM_OFF")]
    assert fake.transactions == 1 and len(fake.history) == 1 and state.backend == []
    steps = [payload["step"] for kind, payload in fake.events if kind == "step"]
    assert steps == ["S0", "S1", *list("ABCDEF"), "S3"]
    assert [kind for kind, _ in fake.events][-2:] == ["verdict", "turn.verdict"]


def test_missing_extract_offline_falls_back_without_module_probe(boundary, monkeypatch):
    _, state = boundary
    install_ledger(monkeypatch, state)
    monkeypatch.setattr(settings, "OFFLINE", 1)
    original = router.import_module
    def load(name):
        if name == "pipeline.extract":
            raise ModuleNotFoundError(name=name)
        return original(name)
    monkeypatch.setattr(router, "import_module", load)
    value = runner.process_session("fixture", "live")
    assert [a.span for a in value.atoms] == ["new claim"]
    assert value.verdicts[0].reason_code == "LLM_OFF"
    assert state.extracts == 0 and state.backend == []


def test_fresh_reprocess_forwards_cache_read_to_every_lookup(boundary, monkeypatch):
    _, state = boundary
    fake = install_ledger(monkeypatch, state, session("không chữa bệnh"))
    from core.models import RuleHit
    import rules.registry

    runtime_rules = pack()
    runtime_rules.check_atom = lambda view: [RuleHit(
        rule_id="R04", family="R04", matched="chữa bệnh", law_anchor="synthetic",
        law_span="synthetic", precedence=1, ambiguous_negation=True)]
    monkeypatch.setattr(rules.registry, "load_pack", lambda *args, **kw: runtime_rules)
    original = llm.call
    observed = []
    def lookup(*args, **kwargs):
        observed.append(kwargs["cache_read"])
        return original(*args, **kwargs)
    monkeypatch.setattr(llm, "call", lookup)
    runner.process_session("fixture", "live", reprocess=True, cache_read=False)
    assert observed == [False, False, False]
    assert len(fake.history) == 1


def test_reprocess_replaces_view_and_search_but_keeps_history(boundary, monkeypatch):
    _, state = boundary
    fake = install_ledger(monkeypatch, state, session("alpha beta gamma"))
    state.spans = ["alpha", "beta"]
    first = runner.process_session("fixture", "live")
    assert [a.span for a in first.atoms] == ["alpha", "beta"]
    count = state.extracts
    state.spans = ["gamma"]
    skipped = runner.process_session("fixture", "live")
    assert [a.span for a in skipped.atoms] == ["alpha", "beta"]
    assert state.extracts == count and len(fake.history) == 2
    new = runner.process_session("fixture", "live", reprocess=True)
    assert [a.span for a in new.atoms] == ["gamma"]
    assert fake.index == {"t01.a1": "gamma"}
    assert len(fake.history) == 3 and new.verdicts[0].supersedes == 1
    again = runner.process_session("fixture", "live", reprocess=True)
    assert [a.span for a in again.atoms] == ["gamma"]
    assert fake.index == {"t01.a1": "gamma"}
    assert len(fake.history) == 4 and again.verdicts[0].supersedes == 3
    assert [v.verdict_id for v in fake.history] == [1, 2, 3, 4]
    assert fake.transactions == 3


def test_transaction_rolls_back_partial_write(boundary, monkeypatch):
    _, state = boundary
    fake = install_ledger(monkeypatch, state)
    def fail(verdicts):
        raise RuntimeError("synthetic storage failure")
    monkeypatch.setattr(fake, "append_verdicts", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        runner.process_session("fixture", "live")
    assert fake.atoms == fake.history == [] and fake.index == {}
    assert fake.events == []


def test_backfill_drops_old_only_flags_and_preserves_history(boundary, monkeypatch):
    _, state = boundary
    fake = install_ledger(monkeypatch, state)
    first = runner.process_session("fixture", "live")
    fake.history[0].flags = [AtomFlag.RECALLED_AFTER]
    original = deepcopy(fake.history[0])
    cleared = runner.replay(["fixture"], persist=True)
    assert cleared[0].status == "ok" and len(fake.history) == 2 and fake.transactions == 2
    assert (cleared.changed_cases, cleared.changed_atoms) == (1, 1)
    assert fake.history[-1].supersedes == first.verdicts[0].verdict_id
    assert fake.history[-1].flags == [] and fake.history[-1].verdict == "green"
    assert fake.history[0] == original
    before = deepcopy((fake.atoms, fake.history, fake.flags, fake.index, fake.events))
    unchanged = runner.replay(["fixture"], persist=True)
    assert (unchanged.changed_cases, unchanged.changed_atoms) == (0, 0)
    assert (fake.atoms, fake.history, fake.flags, fake.index, fake.events) == before
    # Even a no-change replay takes the writer lock to validate its snapshot.
    assert len(fake.history) == 2 and fake.transactions == 3
    state.colour = "yellow"
    changed = runner.replay(["fixture"], persist=True)
    assert changed[0].status == "ok" and len(fake.history) == 3 and fake.transactions == 4
    assert (changed.changed_cases, changed.changed_atoms) == (1, 1)
    assert fake.history[-1].supersedes == fake.history[-2].verdict_id
    assert fake.history[-1].flags == [] and fake.history[0] == original
    assert fake.history[-1].verdict == "yellow"
    assert [row.verdict_id for row in fake.history] == [1, 2, 3]
    assert [row.supersedes for row in fake.history] == [None, 1, 2]


def test_explicit_measure_pack_never_opens_ledger(boundary):
    with pytest.raises(ValueError, match="Only a live pack"):
        runner.replay([], pack("measure"), persist=True, pack_version=1)
    with pytest.raises(ValueError, match="pack_version"):
        runner.run_cases([], "live", pack("measure"))
    with pytest.raises(ValueError, match="explicit pack"):
        runner.run_cases([], "oracle")
    assert not paths.ledger_db().exists()


def test_successful_empty_s1_is_evaluated(boundary):
    _, state = boundary
    state.spans = []
    result, = runner.run_cases([dict(case_id="empty", text="greeting")], "shadow", pack(), 1)
    assert result.status == "ok" and result.verdicts == []
    assert result.case_verdict is None and len(state.backend) == 1


def test_successful_case_fallback_without_s1_id_is_evaluated(boundary, monkeypatch):
    _, state = boundary
    original = sys.modules["pipeline.extract"].extract_atoms
    def extract(*args, **kwargs):
        atoms = original(*args, **kwargs)
        for atom in atoms:
            atom.s1_call_id = None
        return atoms
    monkeypatch.setitem(sys.modules, "pipeline.extract", SimpleNamespace(extract_atoms=extract))
    result, = runner.run_cases([dict(case_id="fallback", text="new claim")], "shadow", pack(), 1)
    assert result.status == "ok" and result.verdicts[0].verdict == "green"


def test_replay_scope_is_thread_local(boundary):
    _, state = boundary
    entered, release = threading.Event(), threading.Event()
    state.gate = (entered, release)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.replay, [dict(case_id="one", text="new claim")], pack(),
                             pack_version=1)
        assert entered.wait(5)
        with llm.run_scope(measuring=True):
            live = llm.call("s1", "live", "synthetic", "none", "test_response",
                            [{"role": "user", "content": "parallel live"}])
        release.set()
        result, = future.result(timeout=5)
    assert live.error is None and result.status == "not_evaluated"
    assert len(state.backend) == 1 and settings.OFFLINE == 0


def test_event_loop_requires_to_thread(boundary):
    async def check():
        with pytest.raises(RuntimeError, match="pipeline.llm.call is blocking"):
            runner.replay([dict(case_id="one", text="new claim")], pack(), pack_version=1)
        result, = await asyncio.to_thread(runner.replay,
                                         [dict(case_id="one", text="new claim")], pack(), pack_version=1)
        assert result.status == "not_evaluated"
    asyncio.run(check())


def test_oracle_does_not_write_under_repo(boundary, monkeypatch, tmp_path):
    root, state = boundary
    monkeypatch.setattr(settings, "LLM_BACKEND", "api")
    monkeypatch.setattr(settings, "LLM_CACHE_DIR", str(tmp_path / "oracle-cache"))
    result, = runner.run_cases([dict(case_id="one", text="new claim")], "oracle", pack(), 2)
    assert result.status == "ok" and result.verdicts[0].rule_pack_version == 2
    assert state.purposes == ["oracle", "oracle"]
    assert list(root.iterdir()) == [] and not paths.ledger_db().exists()
    assert len((tmp_path / "oracle-cache" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2
