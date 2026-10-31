"""Hard assertions for the offline rehearsal; missing owners remain visible."""

import asyncio
from datetime import date
import json
from pathlib import Path
import re
import runpy
import shutil
import sqlite3
import sys
from time import perf_counter, sleep

import pytest

from .harness import git, require
from .support import expected_agreement, read_expected, record, select_pr1_target


pytestmark = pytest.mark.demo
HERE = Path(__file__).resolve().parent
JAN = "kera-reenact-2025-01-08"
DEC = "kera-reenact-2024-12-12"
MODES = ["presenter", "no_query", "public", "video", "no_directory"]


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _presentation(harness, mode):
    directory = harness.root / ".local" / "present-assets"
    _write_json(directory / "names.json", {f"[KOL {i}]": f"TEST NAME {i}" for i in (1, 2, 3)})
    harness.set("PRESENT_DIR", str(directory))
    harness.set("PUBLIC_MODE", int(mode == "public"))
    harness.set("VIDEO_MODE", int(mode == "video"))
    if mode == "no_directory":
        harness.set("PRESENT_DIR", None)
    return directory


def _query(mode):
    return "" if mode == "no_query" else "?presenter=1"


def _assert_no_marker(value, marker, condition):
    assert marker not in json.dumps(value, ensure_ascii=False), f"{condition}: protected presenter text was exposed"


def test_expected_loaded(demo_environment, expected_path):
    read_expected(expected_path)


def test_kera_january(kera, expected_path):
    guardrail = require("pipeline.guardrail")
    textnorm = require("textnorm")
    session = kera.session(JAN)
    assert len(session["turns"]) == 24, f"January: observed {len(session['turns'])} turns"
    source_turns = [line for line in (kera.root / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8").splitlines()
                    if line.startswith("**[KOL ")]
    original = source_turns[5].split("`", 1)[0]
    expected_text = re.sub(r"^\*\*\[KOL \d+\]\*\*\s*:?[ \t]*", "", original).strip()
    assert kera.turn(JAN, "t06")["text"].strip() == expected_text, "t06: annotation text survived ingestion"
    unknown = {"t13", "t19", "t20"}
    constructed = {"t01", "t02", "t03", "t04", "t09", "t14", "t18", "t22", "t23", "t24"}
    for turn in session["turns"]:
        expected = "press_unknown" if turn["turn_id"] in unknown else "constructed" if turn["turn_id"] in constructed else "press_attributed"
        assert turn["attribution"] == expected, f"{turn['turn_id']}: observed attribution {turn['attribution']}"
    observed = kera.colour(JAN, "t07")
    assert observed == "yellow", f"t07: observed verdict {observed}"
    target = [atom for atom in kera.atoms(JAN, "t07") if "tương ứng với một đĩa rau" in atom["span"]]
    assert len(target) == 1, f"t07: observed {len(target)} vegetable-comparison atoms"
    target_id = next(iter(target))["atom_id"]
    verdicts = kera.verdicts(JAN)
    selected = next(row for row in verdicts if row["atom_key"] == f"{JAN}:{target_id}")
    assert selected["verdict"] == "yellow", f"t07 comparison: observed {selected['verdict']} ({selected['reason_code']})"
    assert {"LQC-2012/D8/K9", "ND15-2018/D27/K2"} <= set(selected["legal_basis"]), f"t07 comparison: observed anchors {selected['legal_basis']}"
    assert verdicts and session["atoms"], "January: observed an empty extraction or verdict list"
    for verdict in verdicts:
        assert verdict["verdict"] != "red", f"{verdict['atom_key']}: unexpectedly red"
        for anchor in verdict["legal_basis"]:
            assert guardrail.is_effective(anchor, date(2025, 1, 8)), f"January: ineffective anchor {anchor}"
        if verdict["verdict"] == "green":
            assert verdict["legal_basis"] == ["ND15-2018/D27/K2"], f"Green: observed anchors {verdict['legal_basis']}"
    observed = kera.colour(JAN, "t16")
    assert observed == "abstain", f"t16: observed verdict {observed}"
    reasons = {row["reason_code"] for row in kera.turn_rows(JAN, "t16")}
    assert "DOSSIER_FIELD_MISSING" in reasons, f"t16: observed reasons {sorted(str(reason) for reason in reasons)}"
    for query in ("đĩa rau", "dia rau", "DIA RAU", "dĩa rau"):
        turns = {row["turn_id"] for row in kera.search(query, JAN)}
        assert turns == {"t07", "t20", "t21"}, f"Search variant: observed turn ids {sorted(turns)}"
    assert session["atoms"] == kera.session(DEC)["atoms"], "S1 atoms changed with the broadcast date"
    if expected_path:
        try:
            expected = read_expected(expected_path)
        except AssertionError:
            kera.reporter.soft.append({"agreement": "expected file unavailable"})
        else:
            kera.reporter.soft.append(expected_agreement(expected, session, verdicts, textnorm.match_key))


def test_kera_t06_report(kera):
    observed = kera.colour(JAN, "t06")
    reasons = [row["reason_code"] for row in kera.turn_rows(JAN, "t06")]
    assert observed == "green", f"t06: observed verdict {observed}; reason_code={reasons}"


@pytest.mark.parametrize("mode", MODES)
def test_speaker_display(kera, mode):
    require("api.app")
    _presentation(kera, mode)
    response = asyncio.run(kera.request(f"/api/sessions/{JAN}" + _query(mode), public=mode == "public"))
    turns = {turn["turn_id"]: turn for turn in response["turns"]}
    if mode == "presenter":
        assert turns["t07"]["display_speaker"] == "TEST NAME 1", "presenter: t07 speaker differs"
        assert turns["t20"]["display_speaker"] == "Speaker unknown", "presenter: unknown speaker was attributed"
        assert turns["t01"]["display_speaker"] == "Reenactment line (team-written) · " + turns["t01"]["speaker_label"], "presenter: constructed speaker label differs"
        _assert_no_marker(turns["t01"], "TEST NAME", mode)
    else:
        _assert_no_marker(response, "TEST NAME", mode)


@pytest.mark.parametrize("mode", MODES)
def test_opening_clip(demo_environment, mode):
    app = require("api.app")
    require("pipeline.ingest_clip")
    require("pipeline.runner")
    import ledger
    from core.config import settings

    h = demo_environment
    directory = _presentation(h, "presenter")
    for clip in ("a1", "a2"):
        base = directory / "clip"
        _write_json(base / f"{clip}.transcript.json", [{"start": 0.0, "end": 1.0, "text": "Demo hạt mây có vị dịu."}])
        _write_json(base / f"{clip}.subs.json", [{"start": 0.0, "end": 1.0, "vi": "Dòng mẫu.", "en": "Synthetic subtitle."}])
        _write_json(base / f"{clip}.meta.json", {"speaker_line": "TEST SPEAKER LINE", "source_line": "TEST SOURCE LINE", "as_of_note": "Synthetic fixture date."})
    ledger.init_db(settings.LEDGER_DB)
    app.ensure_clip_session()
    app.ensure_clip_session()
    with sqlite3.connect(settings.LEDGER_DB) as db:
        for clip in ("a1", "a2"):
            count, = db.execute("SELECT COUNT(*) FROM sessions WHERE session_id = ?", (f"clip-{clip}",)).fetchone()
            assert count == 1, f"clip-{clip}: observed {count} stored sessions"
            rows = h.verdicts(f"clip-{clip}")
            assert rows, f"clip-{clip}: observed no offline verdicts"
            for row in rows:
                assert (row["verdict"], row["reason_code"]) == ("abstain", "LLM_OFF"), f"clip-{clip}: observed {row['verdict']} ({row['reason_code']})"
    _presentation(h, mode)
    manifest = asyncio.run(h.request("/api/present/manifest" + _query(mode), public=mode == "public"))
    response = asyncio.run(h.request("/api/sessions/clip-a1" + _query(mode), public=mode == "public"))
    if mode == "presenter":
        for clip in ("a1", "a2"):
            assert manifest["clips"][clip]["speaker_line"] == "TEST SPEAKER LINE", f"{clip}: speaker_line differs"
            assert manifest["clips"][clip]["source_line"] == "TEST SOURCE LINE", f"{clip}: source_line differs"
        assert response["speaker_line"] == "TEST SPEAKER LINE", "clip session: speaker_line differs"
        assert response["source_line"] == "TEST SOURCE LINE", "clip session: source_line differs"
    else:
        _assert_no_marker(manifest, "TEST SPEAKER LINE", mode)
        _assert_no_marker(response, "TEST SPEAKER LINE", mode)
        _assert_no_marker(manifest, "TEST SOURCE LINE", mode)
        _assert_no_marker(response, "TEST SOURCE LINE", mode)
    assert response["turns"], "clip-a1: no transcript turns"
    for turn in response["turns"]:
        assert turn["speaker_label"] == "[unidentified]", "clip transcript: speaker label differs"
        assert turn["attribution"] == "not_applicable", "clip transcript: attribution differs"
        assert turn["display_speaker"] == "Speaker: unidentified", "clip transcript: display speaker differs"


def test_kera_december(kera):
    session = kera.session(DEC)
    flags = session["session_flags"]
    selected = [record(flag) for flag in flags if record(flag)["flag_code"] == "NO_SELF_DECLARATION_AT_AS_OF"]
    assert len(selected) == 1, f"December: observed {len(selected)} declaration flags"
    flag = next(iter(selected))
    assert flag["verdict"] == "red", f"December: observed session flag colour {flag['verdict']}"
    assert set(flag["legal_basis"]) == {"ND15-2018/D4/K1", "ND15-2018/D5/K2/b"}, f"December: observed flag anchors {flag['legal_basis']}"
    verdicts = kera.verdicts(DEC)
    assert verdicts and session["atoms"], "December: observed an empty extraction or verdict list"
    for row in verdicts:
        assert (row["verdict"], row["reason_code"]) == ("abstain", "DOSSIER_EMPTY"), f"December atom: observed {row['verdict']} ({row['reason_code']})"
    for turn_id in {atom["turn_id"] for atom in session["atoms"]}:
        observed = kera.colour(DEC, turn_id)
        assert observed == "abstain", f"December {turn_id}: observed turn colour {observed}"


def test_recall(kera, expected_path):
    events = require("pipeline.dossier_events")
    before = {row["atom_key"]: row["verdict"] for row in kera.verdicts(JAN)}
    green_count = sum(colour == "green" for colour in before.values())
    assert green_count, "Recall: observed no green atoms before applying recall"
    start = perf_counter()
    events.apply_recall("kera", date(2025, 3, 24), "Synthetic recall rehearsal")
    ms = (perf_counter() - start) * 1000
    after = kera.verdicts(JAN)
    assert {row["atom_key"]: row["verdict"] for row in after} == before, "Recall changed broadcast-date colours"
    flagged = [row for row in after if "RECALLED_AFTER" in row["flags"]]
    assert len(flagged) == green_count, f"Recall: observed {len(flagged)} flags for {green_count} green atoms"
    emitted = [event for event in kera.events() if event["type"] == "recall.applied"]
    assert emitted, "Recall: no recall.applied event"
    banner = next(row for row in emitted[-1]["payload"]["sessions"] if row["session_id"] == JAN)
    assert banner["flagged_atoms"] == green_count, f"Recall banner: observed count {banner['flagged_atoms']}"
    kera.reporter.soft.append({"recall_ms": f"{ms:.3f} ms", "budget": "100.000 ms", "within_budget": ms <= 100})
    if expected_path:
        try:
            expected = read_expected(expected_path)
        except AssertionError:
            return
        runs = expected.get("runs", {}) if isinstance(expected, dict) else {}
        march = runs.get("2025-03-25", {}) if isinstance(runs, dict) else {}
        rerun = march.get("t6_rerun", {}) if isinstance(march, dict) else {}
        ids = rerun.get("flag_recalled_after_broadcast") if isinstance(rerun, dict) else None
        observed = {row["atom_key"].split(":", 1)[1] for row in flagged}
        kera.reporter.soft.append({"recall_expected_count": len(ids) if isinstance(ids, list) else None,
                                   "recall_observed_count": len(observed),
                                   "recall_expected_match": set(ids) == observed if isinstance(ids, list) and all(isinstance(i, str) for i in ids) else "expected file shape changed"})


def test_raw_path(demo_environment):
    raw = require("pipeline.rawpath")
    textnorm = require("textnorm")
    h = demo_environment
    prohibited = record(raw.check_sentence("dk_betics_gold", date(2026, 10, 31), "uống một tháng là chữa khỏi hẳn tiểu đường"))
    assert prohibited["verdict"] == "red", f"Raw path: observed {prohibited['verdict']} ({prohibited['reason_code']})"
    assert prohibited["decided_by"] == "rule:R04", f"Raw path: observed decider {prohibited['decided_by']}"
    index = json.loads((h.root / "data/law/index.json").read_text(encoding="utf-8"))
    law = next(row["text"] for row in index if row["id"] == "LDUOC-2016/D6/K15")
    assert prohibited["law_span"] and textnorm.law_normalize(prohibited["law_span"]) in textnorm.law_normalize(law), "Raw path: law span is not verbatim"
    ordinary = record(raw.check_sentence("dk_betics_gold", date(2026, 10, 31), "Giúp giảm nguy cơ biến chứng do tiểu đường"))
    assert ordinary["verdict"] != "red", f"Raw negative: observed {ordinary['verdict']} ({ordinary['decided_by']})"


def test_display_removes_fines(demo_environment):
    guardrail = require("pipeline.guardrail")
    index = json.loads((demo_environment.root / "data/law/index.json").read_text(encoding="utf-8"))
    for row in index:
        text = guardrail.display_text(row["id"])
        assert not re.search(r"\d[\d.,\s]*\s+đồng", text), f"Display text still contains an amount: {row['id']}"


def test_pr1_dry_trigger(demo_environment):
    labels = require("loop.labels")
    require("loop.t5")
    require("loop.draft")
    require("pipeline.ingest")
    from pipeline.ingest import from_text
    import ledger

    h = demo_environment
    h.set("LOOP_DRY", 1)
    inputs = h.prepare_pr1(HERE / "pr1_sentences.json")
    targets = []
    for sentence in inputs["sentences"]:
        session = from_text(inputs["sku_id"], date.fromisoformat(inputs["as_of"]), sentence, inputs["source"])
        session_id = record(session)["session_id"]
        stored = h.session(session_id)
        atom = select_pr1_target(sentence, stored["atoms"], h.verdicts(session_id))
        targets.append(f"{session_id}:{atom['atom_id']}")
    assert ledger.list_jobs() == [], "PR1: fresh ledger already contains jobs"
    for target in targets[:2]:
        labels.record_label(target, "red", "R04", "hungkhuu404", auto_trigger=True)
        assert ledger.list_jobs() == [], "PR1: a job started before the third label"
    start = perf_counter()
    labels.record_label(targets[2], "red", "R04", "hungkhuu404", auto_trigger=True)
    jobs = []
    while perf_counter() - start < 1:
        jobs = ledger.list_jobs()
        event_types = {event["type"] for event in h.events()}
        if len(jobs) == 1 and {"t5.cluster", "job.started"} <= event_types:
            break
        sleep(0.01)
    elapsed = perf_counter() - start
    assert len(jobs) == 1, f"PR1: observed {len(jobs)} jobs after the third label"
    assert elapsed <= 1, f"PR1: trigger took {elapsed * 1000:.3f} ms"
    assert record(next(iter(jobs)))["dry"] is True, "PR1: job is not marked dry"
    event_types = {event["type"] for event in h.events()}
    assert {"t5.cluster", "job.started"} <= event_types, f"PR1: observed event types {sorted(event_types)}"


def test_merge_fresh_ledger(demo_environment):
    merger = require("loop.merge")
    aggregate = require("pipeline.aggregate")
    registry = require("rules.registry")
    require("pipeline.runner")
    import ledger
    from core.config import settings
    from core.models import Atom, Session, Turn, Verdict

    h = demo_environment
    ledger.init_db(settings.LEDGER_DB)
    rule_id = "H3_DEMO_R04"
    for suffix in (".py", ".meta.json"):
        shutil.copyfile(HERE / "fixtures" / (rule_id + suffix), h.root / "rules" / (rule_id + suffix))
    text = "Demo hạt mây làm tan mọi bệnh."
    session_id = "text-h3demo01"
    ledger.add_session(Session(session_id=session_id, sku_pin="seloderma_psc300_plus", as_of=date(2026, 10, 31),
                               channel="written", source="Synthetic merge rehearsal.",
                               turns=[Turn(turn_id="t01", speaker_label="[KOL 1]", attribution="constructed", text=text)]))
    ledger.add_atoms(session_id, [Atom(atom_id="t01.a1", turn_id="t01", span=text, start=0, end=len(text),
                                       axis="cong_dung", speaker_role="kol", subject_is_product=True)])
    old_pack = ledger.current_pack()
    ledger.append_verdicts([Verdict(atom_key=f"{session_id}:t01.a1", rule_pack_version=old_pack.version,
                                   verdict="abstain", decided_by="abstain:DOSSIER_EMPTY", reason_code="DOSSIER_EMPTY",
                                   as_of=date(2026, 10, 31), dossier_hash="0" * 64, ts="2026-10-31T03:00:00Z")])
    before = h.verdicts(session_id)
    before_counts = record(aggregate.who_decided([Verdict.model_validate(row) for row in before]))
    assert ledger.list_jobs(rule_id=rule_id) == [], "Merge fixture unexpectedly has a ledger job"
    pack = registry.load_pack([*old_pack.active, rule_id], mode="measure")
    assert pack.mode == "measure", "Unapproved candidate was not loaded for measurement"
    dry = record(merger.dry_run(rule_id))
    assert dry["changed_atoms"] >= 1 and dry["changed_cases"] >= 1, f"Dry run: observed {dry['changed_atoms']} atoms / {dry['changed_cases']} cases"
    assert ledger.current_pack().version == old_pack.version, "Dry run mutated the live pack"
    assert h.verdicts(session_id) == before, "Dry run stored measure verdicts"
    start = perf_counter()
    merger.merge(rule_id, "hungkhuu404")
    ms = (perf_counter() - start) * 1000
    new_pack = ledger.current_pack()
    assert new_pack.version > old_pack.version, "Merge did not create a new pack version"
    assert new_pack.approved_by == "hungkhuu404", "Merge pack has no human approver"
    after = h.verdicts(session_id)
    old_ids = {row["atom_key"]: row["verdict_id"] for row in before}
    changed = [row for row in after if row["verdict_id"] != old_ids[row["atom_key"]]]
    assert changed, "Merge backfill changed no verdicts"
    for row in changed:
        assert row["supersedes"] == old_ids[row["atom_key"]], f"Backfill: observed supersedes {row['supersedes']}"
    after_counts = record(aggregate.who_decided([Verdict.model_validate(row) for row in after]))
    assert before_counts["code"] != after_counts["code"], "Merge did not change who_decided code counts"
    h.reporter.soft.append({"merge_backfill_ms": f"{ms:.3f} ms", "budget": "1000.000 ms", "within_budget": ms <= 1000})
    # All Git objects, candidate files and any branches belong to this copy.
    assert git(h.root, "rev-parse", "--show-toplevel").decode().strip().replace("\\", "/") == h.root.as_posix()


def _ledger_rows(path):
    with sqlite3.connect(path) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr) for name in names}


def test_demo_state_restore(kera):
    require("scripts.demo_state")
    labels = require("loop.labels")
    events = require("pipeline.dossier_events")
    from core.config import config_line, settings

    def state_cli(command):
        with kera.patch.context() as patch:
            patch.setattr(sys, "argv", ["scripts.demo_state", command, "--name", "h3-test"])
            try:
                runpy.run_module("scripts.demo_state", run_name="__main__")
            except SystemExit as exc:
                assert exc.code in (None, 0), f"Demo state {command}: observed exit {exc.code}"

    before_rows = _ledger_rows(settings.LEDGER_DB)
    before_files = kera.protected_state()
    line = config_line()
    state_cli("snapshot")
    snapshots = kera.root / ".local" / "snapshots"
    texts = [path.read_text(encoding="utf-8", errors="ignore") for path in snapshots.rglob("*.json")]
    assert any(line in text for text in texts), "Snapshot does not carry the run configuration line"
    atom = next(iter(kera.session(JAN)["atoms"]))
    labels.record_label(f"{JAN}:{atom['atom_id']}", "abstain", None, "hungkhuu404", auto_trigger=False)
    events.apply_recall("kera", date(2025, 3, 24), "Synthetic snapshot recall")
    assert _ledger_rows(settings.LEDGER_DB) != before_rows, "Snapshot test did not mutate the ledger"
    state_cli("restore")
    assert _ledger_rows(settings.LEDGER_DB) == before_rows, "Restore did not recover all ledger rows"
    assert kera.protected_state() == before_files, "Restore changed eval files or rule metadata"
