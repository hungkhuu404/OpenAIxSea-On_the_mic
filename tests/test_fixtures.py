"""Contract examples use invented products and never reuse evaluation sentences."""

import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
RECORDS = [
    ("session_fake.json", "Session", False),
    ("context_fake.json", "Context", False),
    ("atoms_fake.json", "Atom", True),
    ("verdicts_fake.json", "Verdict", True),
    ("events_fake.jsonl", "Event", True),
    ("rule_meta_fake.json", "RuleMeta", False),
    ("job_fake.json", "Job", False),
    ("cluster_fake.json", "ClusterFile", False),
]


def _load(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    if name.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines()]
    return json.loads(text)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.parametrize(("filename", "record", "many"), RECORDS)
def test_fixture_parses_as_core_record(filename, record, many):
    try:
        import core.models as models
    except ModuleNotFoundError as exc:
        if exc.name in {"core", "core.models"}:
            pytest.skip("not ready: core")
        raise
    model = {
        "Session": models.Session,
        "Context": models.Context,
        "Atom": models.Atom,
        "Verdict": models.Verdict,
        "Event": models.Event,
        "RuleMeta": models.RuleMeta,
        "Job": models.Job,
        "ClusterFile": models.ClusterFile,
    }[record]
    value = _load(filename)
    for item in value if many else [value]:
        parsed = model.model_validate(item)
        # Catch fixture fields silently discarded by a permissive model.
        assert set(parsed.model_dump(mode="json")) == set(item)


def test_fixture_files_are_utf8_nfc_without_bom():
    assert {path.name for path in FIXTURES.iterdir() if path.name != ".gitkeep"} == {
        row[0] for row in RECORDS
    }
    for filename, _, _ in RECORDS:
        raw = (FIXTURES / filename).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.endswith(b"\n")
        text = raw.decode("utf-8")
        assert unicodedata.normalize("NFC", text) == text
        assert _load(filename)


def test_fixture_texts_do_not_reuse_golden_or_reenactment_sentences():
    golden = [json.loads(line)["text"] for line in
              (ROOT / "data/cases/golden.jsonl").read_text(encoding="utf-8").splitlines()]
    script = (ROOT / "data/demo/kera/reenactment_script.md").read_text(encoding="utf-8")
    turns = []
    for line in script.splitlines():
        match = re.match(r"^\*\*\[KOL \d+\]\*\*\s*:?[ \t]*(.*)", line)
        if match:
            turns.append(match[1].split("`", 1)[0].strip())
    assert golden and turns, "The contamination check must inspect both public sources"
    def normalized(text):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    forbidden = set()
    for text in golden + turns:
        forbidden.add(normalized(text))
        forbidden.update(normalized(sentence) for sentence in re.split(r"(?<=[.!?…])\s+", text))
    forbidden.discard("")
    for filename, _, _ in RECORDS:
        for text in _strings(_load(filename)):
            assert not any(sentence in normalized(text) for sentence in forbidden), filename


def test_session_context_and_atom_references():
    session = _load("session_fake.json")
    context = _load("context_fake.json")
    atoms = _load("atoms_fake.json")
    assert session["sku_pin"] == context["sku_id"] == "demo_sku"
    assert session["as_of"] == context["as_of"]
    assert len(session["turns"]) == 3
    assert {turn["attribution"] for turn in session["turns"]} == {
        "press_attributed", "press_unknown", "constructed",
    }
    for turn in session["turns"]:
        assert re.fullmatch(r"\[KOL [1-3]\]", turn["speaker_label"])
        assert turn["start_s"] is turn["end_s"] is None
    assert len(context["P"]) == 3
    assert {item["axis_group"] for item in context["P"]} == {"cong_dung", "thanh_phan", "ho_so"}
    for item in context["P"]:
        assert item["permitted_id"] == f'demo_sku:{item["axis_group"]}:1'
        assert item["confidence"] in {"high", "medium"}
    canonical = json.dumps(context["P"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert context["dossier_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert len(atoms) == 5
    assert len({atom["atom_id"] for atom in atoms}) == 5
    turns = {turn["turn_id"]: turn for turn in session["turns"]}
    for atom in atoms:
        assert atom["atom_id"].startswith(atom["turn_id"] + ".a")
        assert turns[atom["turn_id"]]["text"][atom["start"]:atom["end"]] == atom["span"]
        assert atom["subject_is_product"] is True


def test_five_decision_examples_and_source_spans():
    session = _load("session_fake.json")
    context = _load("context_fake.json")
    atoms = {f'{session["session_id"]}:{atom["atom_id"]}': atom for atom in _load("atoms_fake.json")}
    verdicts = _load("verdicts_fake.json")
    assert len(verdicts) == 5
    assert {row["atom_key"] for row in verdicts} == atoms.keys()
    assert {(row["verdict"], row["decided_by"], row["reason_code"]) for row in verdicts} == {
        ("green", "allow_list", None), ("red", "rule:R04", None),
        ("yellow", "llm_votes", None),
        ("abstain", "abstain:DOSSIER_FIELD_MISSING", "DOSSIER_FIELD_MISSING"),
        ("abstain", "abstain:LLM_NEI", "LLM_NEI"),
    }
    permitted = {item["permitted_id"]: item for item in context["P"]}
    index = {entry["id"]: entry["text"] for entry in json.loads(
        (ROOT / "data/law/index.json").read_text(encoding="utf-8"))}
    for row in verdicts:
        assert row["as_of"] == context["as_of"]
        assert row["dossier_hash"] == context["dossier_hash"]
        if row["evidence_span"]:
            item = permitted[row["permitted_id"] or row["closest_permitted_id"]]
            assert row["evidence_span"] in item["text"]
        if row["law_span"]:
            assert any(row["law_span"] in index[anchor] for anchor in row["legal_basis"])
        if row["verdict"] == "green":
            assert atoms[row["atom_key"]]["span"] == permitted[row["permitted_id"]]["text"]
        if row["reason_code"] == "DOSSIER_FIELD_MISSING":
            assert atoms[row["atom_key"]]["axis"] in context["null_fields"]
        if row["decided_by"] == "llm_votes" or row["reason_code"] == "LLM_NEI":
            assert len(row["llm_call_ids"]) == 2


def test_events_show_one_complete_pipeline():
    events = _load("events_fake.jsonl")
    session = _load("session_fake.json")
    atoms = {atom["atom_id"]: atom for atom in _load("atoms_fake.json")}
    assert [event["id"] for event in events] == list(range(1, 10))
    assert [event["payload"]["step"] for event in events] == ["S0", "S1", "A", "B", "C", "D", "E", "F", "S3"]
    assert len({event["payload"]["atom_id"] for event in events}) == 1
    assert [event["ts"] for event in events] == sorted(event["ts"] for event in events)
    for event in events:
        assert event["type"] == "step"
        payload = event["payload"]
        assert set(payload) == {"session_id", "turn_id", "atom_id", "step", "outcome", "ms"}
        assert payload["session_id"] == session["session_id"]
        assert payload["turn_id"] == atoms[payload["atom_id"]]["turn_id"]
        assert payload["ms"] >= 0


def test_job_cluster_and_rule_provenance_agree():
    job = _load("job_fake.json")
    cluster = _load("cluster_fake.json")
    meta = _load("rule_meta_fake.json")
    assert job["rule_id"] == meta["id"]
    assert job["branch"] == f'rule/{job["rule_id"]}-{job["job_id"]}'
    assert job["cluster_hash"] == cluster["cluster_hash"]
    assert job["cluster_key"] == cluster["name"]
    assert job["cluster_item_ids"] == cluster["item_ids"] == meta["source_case_ids"]
    assert len(cluster["item_ids"]) == len(cluster["texts"]) == len(cluster["labels"])
    assert job["status"] == "passed" and job["dry"] is True
    assert [attempt["attempt_no"] for attempt in job["attempts"]] == [1, 2]
    assert [attempt["gate_result"] for attempt in job["attempts"]] == ["fail", "pass"]
    assert [len(attempt["error_lines"]) for attempt in job["attempts"]] == [1, 0]
    assert len(meta["attempts"]) == 2
    for attempt, meta_attempt in zip(job["attempts"], meta["attempts"]):
        assert attempt["error_lines"] == meta_attempt["error_lines"]
        assert attempt["gate_result"] == meta_attempt["gate_result"]
        assert attempt["files_read"] == meta_attempt["files_read"]
        assert attempt["finished"] == meta_attempt["ts"]
    assert len(cluster["runs"]) == 1
    run = cluster["runs"][0]
    for field in ("job_id", "rule_id", "branch", "pr_url"):
        assert run[field] == job[field]
    assert run["result"] == job["status"]
    assert run["attempts"] == [
        {key: attempt[key] for key in ("attempt_no", "gate_result", "error_lines")}
        for attempt in job["attempts"]
    ]
