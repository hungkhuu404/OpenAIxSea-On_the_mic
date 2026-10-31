"""Extraction post-processing with fake LLM responses only."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from core.models import Turn
from core import enums, paths
from pipeline import extract, ingest, llm


def item(span, **changes):
    return dict(span=span, axis="cong_dung", speaker_role="khong_ro", role_span=None,
                subject_is_product=True) | changes


@pytest.fixture
def fake_llm(monkeypatch):
    requests = []
    state = dict(atoms=[], error=None)

    def call(**request):
        requests.append(request)
        canonical = json.dumps(request, ensure_ascii=False, sort_keys=True)
        return SimpleNamespace(response={"atoms": state["atoms"]}, error=state["error"],
            request_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    monkeypatch.setattr(llm, "call", call)
    return state, requests


def session(text="Sản phẩm chứa yến mạch."):
    return ingest.from_text("kera", "2025-01-08", text, "Synthetic")


def test_spans_original_slice_order_dedup_and_filtered_axes(fake_llm, capsys):
    state, _ = fake_llm
    sample = session("Chứa yến mạch; giúp tiêu hóa. Giá 10 đồng.")
    state["atoms"] = [item("giúp tiêu hóa"), item("chứa, yến mạch", axis="thanh_phan"),
                      item("Chứa yến mạch", axis="thanh_phan"), item("Giá 10 đồng", axis="gia"),
                      item("Not in this turn"), item("", axis="cong_dung")]
    atoms = extract.extract_atoms(sample, sample.turns[0], None, SimpleNamespace())
    assert [atom.span for atom in atoms] == ["Chứa yến mạch", "giúp tiêu hóa"]
    assert [atom.atom_id for atom in atoms] == ["t01.a1", "t01.a2"]
    assert all(sample.turns[0].text[a.start:a.end] == a.span for a in atoms)
    assert all(a.s1_call_id is not None for a in atoms)
    assert capsys.readouterr().err.count("S1 span not located:") == 2


@pytest.mark.parametrize("purpose", ["live", "shadow", "oracle"])
@pytest.mark.parametrize("cache_read", [True, False])
def test_request_forwarding(fake_llm, purpose, cache_read):
    _, requests = fake_llm
    sample = session()
    extract.extract_atoms(sample, sample.turns[0], None, SimpleNamespace(), purpose, cache_read)
    assert requests[0]["purpose"] == purpose and requests[0]["cache_read"] is cache_read
    assert requests[0]["kind"] == "s1" and requests[0]["schema_name"] == "s1"


def test_date_independent_request_and_no_dossier_context(fake_llm):
    _, requests = fake_llm
    early, late = session(), session()
    early.as_of = "2024-12-12"
    late.as_of = "2025-01-08"
    for sample in (early, late):
        extract.extract_atoms(sample, sample.turns[0], None,
            SimpleNamespace(P=[dict(text="secret date-dependent content")], as_of=sample.as_of))
    assert requests[0] == requests[1]
    payload = json.dumps(requests[0], ensure_ascii=False)
    for excluded in ("2024-12-12", "2025-01-08", "secret date-dependent", "regulator_warnings", "dossier_state"):
        assert excluded not in payload
    assert "SUPERGREENS GUMMIES" in payload


@pytest.mark.parametrize("kind", ["off", "nonzero_exit", "timeout", "schema"])
def test_error_always_falls_back_to_whole_turn(fake_llm, kind):
    state, _ = fake_llm
    state["error"] = SimpleNamespace(kind=kind)
    sample = session()
    atoms = extract.extract_atoms(sample, sample.turns[0], None, None)
    assert len(atoms) == 1
    assert atoms[0].span == sample.turns[0].text and atoms[0].s1_call_id is None
    assert atoms[0].axis == "cong_dung" and atoms[0].subject_is_product
    assert atoms[0].speaker_role == "khong_ro" and atoms[0].role_span is None


def test_empty_result_falls_back_only_for_eval_or_text(fake_llm):
    sample = session("Xin chào.")
    assert len(extract.extract_atoms(sample, sample.turns[0], None, None)) == 1
    sample.session_id = "kera-reenact-2025-01-08"
    assert extract.extract_atoms(sample, sample.turns[0], None, None) == []
    sample.session_id = "clip-a1"
    assert extract.extract_atoms(sample, sample.turns[0], None, None) == []
    sample.session_id = "case-synthetic-1"
    assert len(extract.extract_atoms(sample, sample.turns[0], None, None)) == 1


def test_previous_turn_is_context_only(fake_llm, capsys):
    state, requests = fake_llm
    sample = session("Chứa yến mạch.")
    previous = Turn(turn_id="t00", speaker_label="[KOL 1]", attribution="constructed", text="Tôi là bác sĩ.")
    state["atoms"] = [item(previous.text)]
    assert extract.extract_atoms(sample, sample.turns[0], previous, None) == []
    assert previous.text in requests[0]["messages"][-1]["content"]
    assert "S1 span not located:" in capsys.readouterr().err


@pytest.mark.parametrize("role_span, expected", [
    (None, "khong_ro"), ("Not present", "khong_ro"), ("Chứa yến mạch", "khong_ro"),
    ("Phó Giáo sư Tiến sĩ, Trường Đại học Dược Hà Nội", "duoc_si"),
])
def test_role_is_proved_in_current_or_previous_turn(fake_llm, role_span, expected):
    state, _ = fake_llm
    sample = session("Chứa yến mạch.")
    previous = Turn(turn_id="t00", speaker_label="[KOL 1]", attribution="constructed",
        text="Phó Giáo sư Tiến sĩ, Trường Đại học Dược Hà Nội giới thiệu.")
    state["atoms"] = [item("Chứa yến mạch", speaker_role="duoc_si", role_span=role_span)]
    atom = extract.extract_atoms(sample, sample.turns[0], previous, None)[0]
    assert atom.speaker_role == expected
    assert atom.role_span == (role_span if expected != "khong_ro" else None)


def test_missing_role_module_is_only_missing_collaborator(fake_llm, monkeypatch):
    state, _ = fake_llm
    sample = session("Tôi là bác sĩ.")
    state["atoms"] = [item("Tôi là bác sĩ", speaker_role="bac_si", role_span="bác sĩ")]
    monkeypatch.setitem(sys.modules, "rules.roles", None)
    atom = extract.extract_atoms(sample, sample.turns[0], None, None)[0]
    assert atom.speaker_role == "khong_ro" and atom.role_span is None


def test_extract_batch_uses_map_calls_and_preserves_session_order(fake_llm, monkeypatch):
    state, requests = fake_llm
    samples = [session("Yến mạch."), session("Vitamin.")]
    seen = []

    def batch(values):
        seen.extend(values)
        return [llm.call(**value) for value in values]

    monkeypatch.setattr(llm, "map_calls", batch)
    result = extract.extract_batch(samples, purpose="shadow", cache_read=False)
    assert [[atom.span for atom in atoms] for atoms in result] == [["Yến mạch."], ["Vitamin."]]
    assert len(seen) == 2 and requests == seen
    assert all(value["purpose"] == "shadow" and value["cache_read"] is False for value in seen)


def test_evidence_name_only_and_non_product_subject(fake_llm, monkeypatch, tmp_path):
    state, requests = fake_llm
    monkeypatch.setattr(paths, "EVIDENCE_DIR", tmp_path)
    (tmp_path / "synthetic_sku.json").write_text(json.dumps(dict(product_name="Demo Oats",
        note="Excluded dossier field", regulator_warnings=["Not for the prompt"])), encoding="utf-8")
    sample = ingest.from_text("synthetic_sku", "2025-01-08", "Yến mạch.", "Synthetic")
    state["atoms"] = [item("Yến mạch", subject_is_product=False)]
    atom = extract.extract_atoms(sample, sample.turns[0], None, None)[0]
    assert not atom.subject_is_product
    content = json.loads(requests[0]["messages"][-1]["content"])
    assert content == dict(product="Demo Oats", current_turn="Yến mạch.", previous_turn="")


def test_other_missing_dependency_is_not_hidden(fake_llm, monkeypatch):
    import builtins
    state, _ = fake_llm
    sample = session("Tôi là bác sĩ.")
    state["atoms"] = [item("Tôi là bác sĩ", speaker_role="bac_si", role_span="bác sĩ")]
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "rules.roles":
            raise ModuleNotFoundError(name="broken_role_dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(ModuleNotFoundError) as error:
        extract.extract_atoms(sample, sample.turns[0], None, None)
    assert error.value.name == "broken_role_dependency"


def test_real_offline_boundary_miss_uses_whole_turn():
    sample = ingest.from_text("synthetic_sku", "2025-01-08", "Synthetic oats claim.", "Synthetic")
    with llm.run_scope(offline=True, measuring=True):
        atoms = extract.extract_atoms(sample, sample.turns[0], None, None)
    assert [atom.span for atom in atoms] == ["Synthetic oats claim."]
    assert atoms[0].s1_call_id is None
    assert not paths.ledger_db().exists()


def test_schema_enums_and_required_properties_match_core():
    schema = json.loads((llm.SCHEMA_DIR / "s1.json").read_text(encoding="utf-8"))
    atom_schema = schema["properties"]["atoms"]["items"]
    assert atom_schema["properties"]["axis"]["enum"] == [axis.value for axis in enums.Axis]
    assert atom_schema["properties"]["speaker_role"]["enum"] == [role.value for role in enums.SpeakerRole]
    assert set(atom_schema["required"]) == set(atom_schema["properties"])
    assert atom_schema["additionalProperties"] is False and schema["additionalProperties"] is False


def test_batch_empty_and_previous_turn_context(fake_llm, monkeypatch):
    _, requests = fake_llm
    sample = session("First turn.")
    sample.turns.append(Turn(turn_id="t02", speaker_label="[KOL 1]", attribution="constructed",
                            text="Second turn."))
    monkeypatch.setattr(llm, "map_calls", lambda values: [llm.call(**value) for value in values])
    assert extract.extract_batch([], "live") == []
    result = extract.extract_batch([sample], "live")
    assert [atom.atom_id for atom in result[0]] == ["t01.a1", "t02.a1"]
    assert json.loads(requests[1]["messages"][-1]["content"])["previous_turn"] == "First turn."
