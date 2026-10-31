import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def registry():
    try:
        importlib.import_module("core.models")
    except ModuleNotFoundError as error:
        if error.name not in ("core", "core.models"):
            raise
        pytest.skip("not ready: core.models")
    from rules import registry
    return registry


def write_rule(directory, rule_id, *, approved_by="hungkhuu404", status="active",
               precedence=4, session=False, filename=None):
    directory.mkdir(parents=True, exist_ok=True)
    meta = json.loads((Path(__file__).resolve().parents[1] / "rules/R04.meta.json").read_text(encoding="utf-8"))
    meta.update(id=rule_id, family=rule_id, approved_by=approved_by, status=status, precedence=precedence)
    path = directory / f"{filename or rule_id}.py"
    path.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    constants = (f'RULE_ID = {rule_id!r}\nFAMILY = {rule_id!r}\nORIGIN = "law_compiled"\n'
                 f'ANCHORS = {meta["anchors"]!r}\nLAW_SPAN = {meta["law_span"]!r}\n'
                 f'PRECEDENCE = {precedence}\n')
    if session:
        source = ('from rules._common import make_session_flag\n'
                  'def check_session(dossier_view, as_of):\n'
                  '    return [make_session_flag("NO_AD_CONFIRMATION", RULE_ID, ANCHORS, "Missing confirmation")]\n')
    else:
        source = ('from rules._common import make_hit\n'
                  'def check(atom_view):\n'
                  '    return [make_hit(RULE_ID, FAMILY, atom_view.text, ANCHORS[0], LAW_SPAN, PRECEDENCE)]\n')
    path.write_text(constants + source, encoding="utf-8")
    return path


def use_directory(monkeypatch, registry, directory):
    monkeypatch.setattr(registry, "__file__", str(directory / "registry.py"))


def test_discovery_matches_constant_and_meta(registry, tmp_path, monkeypatch):
    write_rule(tmp_path, "R04", filename="different_name")
    (tmp_path / "different_name.meta.json").rename(tmp_path / "metadata.meta.json")
    (tmp_path / "nearhit.py").write_text('raise RuntimeError("not a rule")\n', encoding="utf-8")
    assert list(registry._discover(tmp_path)) == ["R04"]
    use_directory(monkeypatch, registry, tmp_path)
    assert registry.load_pack(["R04"]).active[0].rule_id == "R04"
    meta_path = tmp_path / "metadata.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "R09"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatched"):
        registry._discover(tmp_path)


def test_live_refuses_unapproved_copy(registry, tmp_path, monkeypatch):
    write_rule(tmp_path, "R04", approved_by=None)
    use_directory(monkeypatch, registry, tmp_path)
    with pytest.raises(ValueError, match="approval"):
        registry.load_pack(["R04"])
    assert len(registry.load_pack(["R04"], mode="measure").active) == 1


def test_only_one_unapproved(registry, tmp_path, monkeypatch):
    write_rule(tmp_path, "R04", approved_by=None, status="proposed")
    write_rule(tmp_path, "R09", approved_by=None, status="proposed")
    use_directory(monkeypatch, registry, tmp_path)
    assert registry.load_pack(["R04"], mode="measure").check_atom(SimpleNamespace(text="test"))
    with pytest.raises(ValueError, match="one unapproved"):
        registry.load_pack(["R04", "R09"], mode="measure")


def test_pack_routes_sorts_and_quarantines(registry, tmp_path, monkeypatch):
    write_rule(tmp_path, "R04", precedence=4)
    write_rule(tmp_path, "R09", precedence=9)
    write_rule(tmp_path, "R08", status="quarantined")
    write_rule(tmp_path, "R06", session=True)
    write_rule(tmp_path, "R05", status="proposed")
    use_directory(monkeypatch, registry, tmp_path)
    pack = registry.load_pack(["R09", "R04", "R06", "R05"], quarantined=["R08"])
    assert [hit.rule_id for hit in pack.check_atom(SimpleNamespace(text="test"))] == ["R04", "R09"]
    assert [hit.rule_id for hit in pack.quarantined_hits(SimpleNamespace(text="test"))] == ["R08"]
    assert [flag.rule_id for flag in pack.check_session(None, "2026-10-21")] == ["R06"]


def test_candidate_path_with_spaces(registry, tmp_path, monkeypatch):
    main_rules = tmp_path / "main rules"
    write_rule(main_rules, "R04")
    use_directory(monkeypatch, registry, main_rules)
    candidate = tmp_path / "candidate tree with spaces"
    write_rule(candidate / "rules", "R09", approved_by=None, status="proposed")
    pack = registry.load_pack(["R04", "R09"], mode="measure", candidate_dir=candidate)
    assert [hit.rule_id for hit in pack.check_atom(SimpleNamespace(text="test"))] == ["R04", "R09"]
    with pytest.raises(ValueError, match="only in measure"):
        registry.load_pack(["R09"], candidate_dir=candidate)


def test_rule_may_use_dataclasses(registry, tmp_path):
    path = write_rule(tmp_path, "R04")
    path.write_text('from __future__ import annotations\nfrom dataclasses import dataclass\n'
                    '@dataclass\nclass Marker:\n    text: str\n' + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert registry._discover(tmp_path)["R04"].module.Marker("test").text == "test"


def test_bad_pack_inputs(registry, tmp_path, monkeypatch):
    write_rule(tmp_path, "R04")
    use_directory(monkeypatch, registry, tmp_path)
    with pytest.raises(ValueError, match="mode"):
        registry.load_pack(["R04"], mode="other")
    with pytest.raises(ValueError, match="overlapping"):
        registry.load_pack(["R04"], quarantined=["R04"])
    with pytest.raises(KeyError):
        registry.load_pack(["MISSING"])


def test_missing_collaborator_is_not_hidden(registry, monkeypatch):
    import builtins
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "core.models":
            raise ModuleNotFoundError("not ready", name="core.models")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="not ready"):
        registry.all_rules()


@pytest.mark.parametrize("malformed", [False, True])
def test_unselected_import_failure_is_isolated_but_full_inspection_fails(registry, tmp_path, monkeypatch, malformed):
    write_rule(tmp_path, "R04")
    broken = write_rule(tmp_path, "R09")
    failure = "not valid Python!" if malformed else 'raise RuntimeError("synthetic R09 import failure")'
    broken.write_text(broken.read_text(encoding="utf-8") + "\n" + failure + "\n", encoding="utf-8")
    use_directory(monkeypatch, registry, tmp_path)
    assert [rule.rule_id for rule in registry.load_pack(["R04"]).active] == ["R04"]
    error = SyntaxError if malformed else RuntimeError
    with pytest.raises(error):
        registry.load_pack(["R09"])
    with pytest.raises(error):
        registry.all_rules()


@pytest.mark.parametrize("status", ["proposed", "quarantined"])
def test_inactive_rule_is_not_imported_as_active(registry, tmp_path, monkeypatch, status):
    write_rule(tmp_path, "R04")
    broken = write_rule(tmp_path, "R09", status=status)
    broken.write_text(broken.read_text(encoding="utf-8") + '\nraise RuntimeError("disabled import failure")\n', encoding="utf-8")
    use_directory(monkeypatch, registry, tmp_path)
    assert [rule.rule_id for rule in registry.load_pack(["R04", "R09"]).active] == ["R04"]
    with pytest.raises(RuntimeError, match="disabled import failure"):
        registry.load_pack(["R04"], quarantined=["R09"])


def test_approval_is_checked_before_import(registry, tmp_path, monkeypatch):
    broken = write_rule(tmp_path, "R04", approved_by=None)
    broken.write_text(broken.read_text(encoding="utf-8") + '\nraise RuntimeError("unapproved import failure")\n', encoding="utf-8")
    use_directory(monkeypatch, registry, tmp_path)
    with pytest.raises(ValueError, match="approval"):
        registry.load_pack(["R04"])
    with pytest.raises(RuntimeError, match="unapproved import failure"):
        registry.load_pack(["R04"], mode="measure")


def test_candidate_replaces_main_before_import_and_ignores_other_candidates(registry, tmp_path, monkeypatch):
    main = tmp_path / "main"
    broken = write_rule(main, "R04")
    broken.write_text(broken.read_text(encoding="utf-8") + '\nraise RuntimeError("replaced main import failure")\n', encoding="utf-8")
    use_directory(monkeypatch, registry, main)
    candidate = tmp_path / "candidate with spaces"
    write_rule(candidate / "rules", "R04", approved_by=None, status="proposed")
    other = write_rule(candidate / "rules", "R09", approved_by=None, status="proposed")
    other.write_text(other.read_text(encoding="utf-8") + '\nraise RuntimeError("unselected candidate import failure")\n', encoding="utf-8")
    pack = registry.load_pack(["R04"], mode="measure", candidate_dir=candidate)
    assert [rule.rule_id for rule in pack.active] == ["R04"]
    assert pack.active[0].meta.approved_by is None


@pytest.mark.parametrize("duplicate", ["metadata", "module"])
def test_duplicate_id_validation_survives_pack_filtering(registry, tmp_path, monkeypatch, duplicate):
    write_rule(tmp_path, "R04")
    path = write_rule(tmp_path, "R09")
    source = path.with_suffix(".meta.json") if duplicate == "metadata" else path
    target = tmp_path / ("duplicate.meta.json" if duplicate == "metadata" else "duplicate.py")
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    use_directory(monkeypatch, registry, tmp_path)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.load_pack(["R04"])
    with pytest.raises(ValueError, match="Duplicate"):
        registry.all_rules()


def test_selected_module_still_requires_matching_metadata(registry, tmp_path, monkeypatch):
    path = write_rule(tmp_path, "R04")
    path.write_text(path.read_text(encoding="utf-8").replace("FAMILY = 'R04'", "FAMILY = 'R09'"), encoding="utf-8")
    use_directory(monkeypatch, registry, tmp_path)
    with pytest.raises(ValueError, match="Rule/meta mismatch: R04.FAMILY"):
        registry.load_pack(["R04"])
