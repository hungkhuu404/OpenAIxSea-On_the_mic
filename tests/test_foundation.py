"""Contract coverage for the shared foundation and its process boundaries."""

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from pydantic import ValidationError
import pytest

from core import config, enums, models, paths
from conftest import pin_test_settings

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "LLM_BACKEND": "off", "OPENAI_API_KEY": None,
    "S1_MODEL": "gpt-6-luna", "S1_EFFORT": "none",
    "JUDGE_MODEL": "gpt-6.1-sol", "JUDGE_EFFORT": "low", "JUDGE_MODE": "two",
    "DRAFT_MODEL": "gpt-6.1-sol", "DRAFT_EFFORT": "low",
    "LLM_TIMEOUT_S": 6, "LLM_PARALLEL": 8, "OFFLINE": 0,
    "LLM_CACHE_DIR": None, "LLM_LOCAL_CACHE_DIR": ".local/cache",
    "LEDGER_DB": ".local/ledger.db", "EVAL_DIR": "eval", "PUBLIC_MODE": 0,
    "RATE_LIMIT_PER_MIN": 20, "PRESENT_DIR": None, "VIDEO_MODE": 0,
    "DEMO_EXPECTED": None, "REGRESSION_AUTOCOMMIT": 0, "LOOP_OPEN_PR": 0,
    "LOOP_DRY": 0, "CODEX_CMD": "codex", "WORKTREE_ROOT": "../_worktrees",
    "RULE_KEEPER": "hungkhuu404", "T5_TAU": 0.6, "UI_PALETTE": "oil", "UI_STAGE": 0,
}

RECORD_FIELDS = {
    "Session": "session_id sku_pin as_of channel source turns",
    "Turn": "turn_id speaker_label attribution source_claim_ids text start_s end_s",
    "Atom": "atom_id turn_id span start end axis speaker_role role_span subject_is_product near_hits s1_call_id",
    "AtomView": "text key axis speaker_role role_span category_norm as_of turn_text",
    "DossierView": "sku_id category_norm ad_confirmation_status declaration_date recall_date",
    "NearHit": "pattern gap law_anchor law_span span",
    "PermittedItem": "permitted_id axis_group text confidence source_field",
    "Context": "sku_id category_norm as_of dossier_state P reference_only null_fields law_set session_flags dossier_hash",
    "SessionFlag": "flag_code rule_id verdict legal_basis message",
    "RuleHit": "rule_id family matched law_anchor law_span precedence ambiguous_negation",
    "Verdict": "verdict_id atom_key rule_pack_version verdict decided_by reason_code fired_rules permitted_id closest_permitted_id evidence_span law_span numbers legal_basis as_of dossier_hash llm_call_ids supersedes flags qlnd_category action ts",
    "RuleMeta": "id family origin article_id anchors law_span source_case_ids trigger precedence attempts revealed_case_ids approved_by approved_at status",
    "RulePack": "version active quarantined change rule_id approved_by ts",
    "LLMCall": "request_sha256 kind purpose backend model effort usage latency_ms response error ts",
    "Event": "id ts type payload",
    "RegressionItem": "item_id level text sku_id as_of label rule_ids labeler ts kind origin_verdict near_hit_patterns source_note session_id atom_id",
    "ClusterFile": "name item_ids texts labels cluster_hash created_at commit runs",
    "Job": "job_id kind rule_id branch cluster_key cluster_item_ids cluster_hash status pid attempts pr_url dry config ts",
    "ReplayResult": "status verdicts case_verdict negq_miss",
    "DryRun": "rule_id changed_cases changed_atoms changed_golden_outside_sources flag_many observed_changes",
    "GateResult": "passed checks error_lines observed_changes revealed_case_ids",
    "OracleReport": "measured_at n_cases cache_miss negq_miss pack_v1 pack_v2 transitions abstain_to_correct correct_to_wrong red_wrong correct_refusals who_decided_v1 who_decided_v2 mcnemar_p overlap_flags standin",
    "WhoDecided": "code llm_votes human denominator measured_at source",
}

ENUM_VALUES = {
    "Verdict": "green yellow red abstain",
    "Axis": "cong_dung thanh_phan chat_luong doi_tuong lieu_dung xuat_xu ho_so khuyen_cao vien_dan so_sanh tuyet_doi gia khuyen_mai bao_hanh",
    "AxisGroup": "cong_dung thanh_phan lieu_dung doi_tuong ho_so xuat_xu",
    "SpeakerRole": "bac_si duoc_si nguoi_benh kol nguoi_ban khong_ro",
    "Attribution": "press_attributed press_unknown constructed not_applicable",
    "Channel": "livestream written video",
    "CategoryNorm": "TPBVSK thuc_pham_bo_sung unknown",
    "ReasonCode": "SKU_UNRESOLVED ASR_LOW SUBJECT_AMBIGUOUS DOSSIER_EMPTY DOSSIER_FIELD_MISSING RULE_AMBIGUOUS VOTES_SPLIT LLM_NEI LLM_TIMEOUT LLM_OFF LAW_NOT_EFFECTIVE RULE_QUARANTINED",
    "SessionFlagCode": "NO_SELF_DECLARATION_AT_AS_OF NO_AD_CONFIRMATION RECALLED_AT_AS_OF",
    "AtomFlag": "RECALLED_AFTER PREV_RED", "Purpose": "live shadow oracle",
    "Backend": "api codex off", "CallKind": "s1 t1 t2 negq",
    "RuleOrigin": "law_compiled t5", "RuleStatus": "proposed active quarantined",
    "PackMode": "live measure", "LegalBasisFamily": "R04 R05 R06 R08 R09 L1_PREDECL L1_RECALL STANDARD",
    "JobStatus": "queued running passed failed error", "GateResult": "pass fail",
}

LINE_FIELDS = "cfg head codex backend offline judge s1 judge_model draft timeout loop_dry autocommit open_pr public video present_dir eval_dir ledger local_cache cache_dir api_key".split()


@pytest.mark.parametrize("name,fields", RECORD_FIELDS.items())
def test_record_fields(name, fields):
    model = vars(models)[name]
    assert set(model.model_fields) == set(fields.split())
    model.model_json_schema()


@pytest.mark.parametrize("name,values", ENUM_VALUES.items())
def test_enum_values(name, values):
    assert {member.value for member in vars(enums)[name]} == set(values.split())


def test_ui_labels_and_error_scale():
    assert [e.value for e in enums.UILabel] == ["Within file", "Beyond file", "Prohibited", "Needs human"]
    assert [e.value for e in enums.ErrorScale] == ["correct", "abstain", "lighter than label", "heavier than label"]


def test_all_settings_defaults_are_independent_of_machine(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "api")
    monkeypatch.setenv("CODEX_CMD", "machine-specific")
    monkeypatch.setenv("LLM_TIMEOUT_S", "60")
    fresh = config.Settings(environ={})
    assert set(type(fresh).model_fields) == set(DEFAULTS)
    assert {name: getattr(fresh, name) for name in DEFAULTS} == DEFAULTS


def test_environment_types_and_shared_override():
    fresh = config.Settings(environ={"OFFLINE": "1", "LLM_TIMEOUT_S": "60", "T5_TAU": "0.75"})
    assert fresh.OFFLINE == 1
    assert fresh.LLM_TIMEOUT_S == 60
    assert fresh.T5_TAU == 0.75
    shared = config.settings
    config.set_override("OFFLINE", "0")
    assert shared is config.settings and shared.OFFLINE == 0
    with pytest.raises(ValueError, match="Unknown setting"):
        config.set_override("TYPO", 1)
    with pytest.raises(ValidationError):
        config.set_override("OFFLINE", 2)


def test_pinning_repairs_drift_and_restores_environment(monkeypatch, tmp_path):
    drifted = {"LOOP_DRY": 1, "VIDEO_MODE": 1, "REGRESSION_AUTOCOMMIT": 1,
               "S1_MODEL": "wrong-model", "PRESENT_DIR": "elsewhere", "EVAL_DIR": "elsewhere",
               "OFFLINE": 0, "UI_STAGE": 1, "UI_PALETTE": "graphite",
               "LLM_CACHE_DIR": "wrong-cache", "WORKTREE_ROOT": "wrong-root"}
    for name, value in drifted.items():
        monkeypatch.setenv(name, str(value))
        config.set_override(name, value)
    with pytest.MonkeyPatch.context() as patch:
        pinned = pin_test_settings(patch, tmp_path / "pinned")
        for name, value in pinned.items():
            assert getattr(config.settings, name) == value
            assert os.environ.get(name) == (str(value) if value is not None else None)
        assert config.settings.OFFLINE == 1
        assert config.settings.S1_MODEL == DEFAULTS["S1_MODEL"]
        assert config.settings.PRESENT_DIR is None
        assert Path(config.settings.EVAL_DIR) == tmp_path / "pinned" / "eval"
        assert not list(Path(config.settings.LLM_LOCAL_CACHE_DIR).iterdir())
    for name, value in drifted.items():
        assert os.environ[name] == str(value)


def _run_config(*, cwd=ROOT, **updates):
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.update(PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8",
               CODEX_CMD="foundation-command-that-does-not-exist")
    env.update({name: str(value) for name, value in updates.items()})
    result = subprocess.run([sys.executable, "-m", "core.config"], cwd=cwd,
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    line = result.stdout.strip()
    assert re.findall(r"(?:^| )([a-z0-9_]+)=", line) == LINE_FIELDS
    pairs = re.split(r"(?:^| )([a-z0-9_]+)=", line)[1:]
    return line, dict(zip(pairs[::2], pairs[1::2]))


def test_config_command_order_secret_and_missing_codex():
    sentinel = "foundation-secret-sentinel"
    line, fields = _run_config(OPENAI_API_KEY=sentinel)
    assert sentinel not in line
    assert fields["api_key"] == "set"
    assert fields["codex"] == "unavailable"
    assert re.fullmatch(r"[0-9a-f]{8}", fields["cfg"])
    assert re.fullmatch(r"[0-9a-f]+|nogit", fields["head"])


def test_config_hash_excludes_machine_paths_and_key(tmp_path):
    _, first = _run_config(EVAL_DIR=str(tmp_path / "first"), OFFLINE=1)
    _, second = _run_config(EVAL_DIR=str(tmp_path / "second"), OFFLINE=1,
                            LEDGER_DB="different.db", LLM_LOCAL_CACHE_DIR="different cache",
                            LLM_CACHE_DIR="different committed cache", PRESENT_DIR="present assets",
                            OPENAI_API_KEY="another-sentinel")
    _, changed = _run_config(EVAL_DIR=str(tmp_path / "second"), OFFLINE=0)
    assert first["cfg"] == second["cfg"]
    assert second["cfg"] != changed["cfg"]


def test_canonical_config_hash(monkeypatch):
    monkeypatch.setattr(config, "_first_line", lambda args, fallback: fallback)
    fields = config.config_fields()
    assert list(fields) == LINE_FIELDS
    omitted = {"cfg", "head", "codex", "api_key", "present_dir", "eval_dir", "ledger", "local_cache", "cache_dir"}
    canonical = json.dumps({k: v for k, v in fields.items() if k not in omitted},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert fields["cfg"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def test_config_ignores_dotenv_in_working_directory(tmp_path):
    (tmp_path / ".env").write_text("LLM_BACKEND=api\nOFFLINE=0\n", encoding="utf-8")
    _, fields = _run_config(cwd=tmp_path, LLM_BACKEND="off", OFFLINE=1)
    assert fields["backend"] == "off" and fields["offline"] == "1"
    # No env input, even when a dotenv file exists beside the process.
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("LLM_BACKEND", None)
    result = subprocess.run([sys.executable, "-c",
                             "from core.config import settings; print(settings.LLM_BACKEND)"],
                            cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == "off"


def test_codex_command_quoted_paths_and_first_line(tmp_path):
    script = tmp_path / "version script.py"
    script.write_text("import sys\nassert sys.argv[1:] == ['--version']\nprint('test-cli 1.0')\nprint('extra')\n", encoding="utf-8")
    command = f'"{sys.executable}" "{script}"'
    assert config.command_args(command) == [sys.executable, str(script)]
    _, fields = _run_config(CODEX_CMD=command)
    assert fields["codex"] == "test-cli 1.0"


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("command", 10),
                                   subprocess.CalledProcessError(1, "command")])
def test_missing_commands_and_timeout_never_fail(monkeypatch, error):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 10
        assert kwargs["shell"] is False
        raise error
    monkeypatch.setattr(config.subprocess, "run", fail)
    fields = config.config_fields()
    assert fields["head"] == "nogit" and fields["codex"] == "unavailable"


def test_models_dates_timestamps_and_separate_defaults():
    args = dict(session_id="synthetic", as_of="2025-01-08", channel="written", source="Synthetic")
    first, second = models.Session(**args), models.Session(**args)
    first.turns.append(models.Turn(turn_id="t01", speaker_label="[seller]", attribution="constructed", text="Example"))
    assert second.turns == [] and first.as_of == date(2025, 1, 8)
    event = models.Event(id=1, ts="2026-10-31T00:00:00Z", type="example", payload={})
    assert event.model_dump()["ts"] == "2026-10-31T00:00:00Z"
    for bad in ["2026-10-31", "2026-10-31T00:00:00", "2026-10-31T00:00:00+07:00"]:
        with pytest.raises(ValidationError):
            models.Event(id=1, ts=bad, type="example", payload={})
    with pytest.raises(ValidationError):
        models.PermittedItem(permitted_id="example", axis_group="cong_dung", text="Example",
                             confidence="low", source_field="example")


def test_paths_overrides_lazy_creation_and_worktree_boundary(monkeypatch, tmp_path):
    fake_root = tmp_path / "project with spaces.and.dots"
    fake_root.mkdir()
    monkeypatch.setattr(paths, "REPO_ROOT", fake_root)
    monkeypatch.setattr(paths, "LOCAL_DIR", fake_root / ".local")
    config.set_override("LLM_LOCAL_CACHE_DIR", ".local/custom cache")
    config.set_override("LEDGER_DB", ".local/custom ledger.db")
    config.set_override("EVAL_DIR", "eval")
    assert paths.local_cache_dir() == fake_root / ".local" / "custom cache"
    assert paths.ledger_db() == fake_root / ".local" / "custom ledger.db"
    assert paths.regressions_file() == fake_root / "eval" / "regressions.jsonl"
    assert paths.clusters_dir() == fake_root / "eval" / "clusters"
    assert paths.worktree_root() == tmp_path / "_worktrees"
    assert not list(fake_root.iterdir())
    for folder in (paths.snapshots_dir, paths.logs_dir, paths.jobs_dir, paths.local_cache_dir):
        assert not folder().exists()
        assert folder(create=True).is_dir()
    assert not paths.ledger_db(create=True).exists()
    assert not paths.eval_dir().exists()
    for invalid in [".", "nested/worktrees", str(fake_root / "absolute nested"), "../project with spaces.and.dots"]:
        config.set_override("WORKTREE_ROOT", invalid)
        with pytest.raises(ValueError, match="outside"):
            paths.worktree_root()
    outside = tmp_path / "outside.with spaces"
    config.set_override("WORKTREE_ROOT", str(outside))
    assert paths.worktree_root() == outside
    assert not outside.exists()


def test_required_contracts_and_prebuilt_data():
    assert 0 < (ROOT / "AGENTS.md").stat().st_size < 32768
    assert (ROOT / "docs" / "contracts.md").is_file()
    assert paths.LAW_DIR / "index.json" == ROOT / "data" / "law" / "index.json"
    assert (paths.LAW_DIR / "index.json").is_file()
    assert paths.GOLDEN_CASES.is_file()
    assert (paths.KERA_DIR / "dossier.json").is_file()
    assert {p.name for p in paths.GOLDEN_CASES.parent.iterdir()} == {"FREEZE.json", "SOURCES.md", "golden.jsonl"}
