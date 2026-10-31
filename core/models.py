"""Pydantic v2 wire records from contracts section 5, without product logic."""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, JsonValue

from core import enums as e


def _utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if "T" not in value or parsed.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must be an ISO-8601 UTC string")
    return value


Timestamp = Annotated[str, AfterValidator(_utc_timestamp)]


class Turn(BaseModel):
    turn_id: str
    speaker_label: str
    attribution: e.Attribution
    source_claim_ids: list[str] = Field(default_factory=list)
    text: str
    start_s: float | None = None
    end_s: float | None = None


class Session(BaseModel):
    session_id: str
    sku_pin: str | None = None
    as_of: date
    channel: e.Channel
    source: str
    turns: list[Turn] = Field(default_factory=list)


class NearHit(BaseModel):
    pattern: str
    gap: Literal[1, 2]
    law_anchor: str
    law_span: str
    span: str


class Atom(BaseModel):
    atom_id: str
    turn_id: str
    span: str
    start: int
    end: int
    axis: e.Axis
    speaker_role: e.SpeakerRole
    role_span: str | None = None
    subject_is_product: bool
    near_hits: list[NearHit] = Field(default_factory=list)
    s1_call_id: str | None = None


class AtomView(BaseModel):
    text: str
    key: str
    axis: e.Axis
    speaker_role: e.SpeakerRole
    role_span: str | None = None
    category_norm: e.CategoryNorm
    as_of: date
    turn_text: str


class DossierView(BaseModel):
    sku_id: str
    category_norm: e.CategoryNorm
    ad_confirmation_status: Literal["present", "absent", "unknown"] | None = None
    declaration_date: date | None = None
    recall_date: date | None = None


class PermittedItem(BaseModel):
    permitted_id: str
    axis_group: e.AxisGroup
    text: str
    confidence: Literal["high", "medium"]
    source_field: str


class SessionFlag(BaseModel):
    flag_code: e.SessionFlagCode
    rule_id: str
    verdict: Literal["red"] = "red"
    legal_basis: list[str] = Field(default_factory=list)
    message: str


class Context(BaseModel):
    sku_id: str | None = None
    category_norm: e.CategoryNorm
    as_of: date
    dossier_state: Literal["CHƯA_CÓ_HỒ_SƠ", "ĐÃ_KÝ_CHƯA_NỘP", "HIỆU_LỰC",
                           "ĐÃ_THU_HỒI", "PRESENT", "NONE"]
    P: list[PermittedItem] = Field(default_factory=list)
    reference_only: list[dict[str, JsonValue]] = Field(default_factory=list)
    null_fields: list[e.AxisGroup] = Field(default_factory=list)
    law_set: list[str] = Field(default_factory=list)
    session_flags: list[SessionFlag] = Field(default_factory=list)
    dossier_hash: str


class RuleHit(BaseModel):
    rule_id: str
    family: str
    matched: str
    law_anchor: str
    law_span: str
    precedence: int
    ambiguous_negation: bool = False


class Numbers(BaseModel):
    claim: float | str | None = None
    dossier: float | str | None = None
    consistent: bool | None = None


class Verdict(BaseModel):
    verdict_id: int | None = None
    atom_key: str
    rule_pack_version: int
    verdict: e.Verdict
    decided_by: e.DecidedBy
    reason_code: e.ReasonCode | None = None
    fired_rules: list[str] = Field(default_factory=list)
    permitted_id: str | None = None
    closest_permitted_id: str | None = None
    evidence_span: str | None = None
    law_span: str | None = None
    numbers: Numbers | None = None
    legal_basis: list[str] = Field(default_factory=list)
    as_of: date
    dossier_hash: str
    llm_call_ids: list[str] = Field(default_factory=list)
    supersedes: int | None = None
    flags: list[e.AtomFlag] = Field(default_factory=list)
    qlnd_category: str | None = None
    action: str | None = None
    ts: Timestamp


class RuleAttempt(BaseModel):
    ts: Timestamp
    gate_result: e.GateResult
    error_lines: list[str] = Field(default_factory=list)
    files_read: list[str] = Field(default_factory=list)


class RuleMeta(BaseModel):
    id: str
    family: str
    origin: e.RuleOrigin
    article_id: str
    anchors: list[str] = Field(default_factory=list)
    law_span: str | list[str]
    source_case_ids: list[str] = Field(default_factory=list)
    trigger: str
    precedence: int
    attempts: list[RuleAttempt] = Field(default_factory=list)
    revealed_case_ids: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: Timestamp | None = None
    status: e.RuleStatus


class RulePack(BaseModel):
    version: int
    active: list[str] = Field(default_factory=list)
    quarantined: list[str] = Field(default_factory=list)
    change: Literal["init", "merge", "quarantine", "unquarantine"]
    rule_id: str | None = None
    approved_by: str | None = None
    ts: Timestamp


class Usage(BaseModel):
    input: int
    output: int
    cached: int
    reasoning: int


class LLMError(BaseModel):
    kind: Literal["off", "timeout", "nonzero_exit", "no_output", "parse", "schema", "http"]
    exit_code: int | None = None
    stderr_tail: str | None = None
    detail: str | None = None


class LLMCall(BaseModel):
    request_sha256: str
    kind: e.CallKind
    purpose: e.Purpose
    backend: e.Backend
    model: str
    effort: str
    usage: Usage | None = None
    latency_ms: float
    response: JsonValue = None
    error: LLMError | None = None
    ts: Timestamp


class Event(BaseModel):
    id: int
    ts: Timestamp
    type: str
    payload: dict[str, JsonValue]


class OriginVerdict(BaseModel):
    verdict: e.Verdict
    reason_code: e.ReasonCode | None = None
    decided_by: e.DecidedBy


class RegressionItem(BaseModel):
    item_id: str
    level: Literal["case", "atom"]
    text: str
    sku_id: str
    as_of: date
    label: e.Verdict
    rule_ids: list[str] = Field(default_factory=list)
    labeler: str
    ts: Timestamp
    kind: Literal["label", "report_wrong"] | None = None
    origin_verdict: OriginVerdict | None = None
    near_hit_patterns: list[str] = Field(default_factory=list)
    source_note: str | None = None
    session_id: str | None = None
    atom_id: str | None = None


class ClusterAttempt(BaseModel):
    attempt_no: int
    gate_result: e.GateResult
    error_lines: list[str] = Field(default_factory=list)


class ClusterRun(BaseModel):
    job_id: str
    rule_id: str
    branch: str
    started: Timestamp
    attempts: list[ClusterAttempt] = Field(default_factory=list)
    result: e.JobStatus
    pr_url: str | None = None


class ClusterFile(BaseModel):
    name: str
    item_ids: list[str] = Field(default_factory=list)
    texts: list[str] = Field(default_factory=list)
    labels: list[e.Verdict] = Field(default_factory=list)
    cluster_hash: str
    created_at: Timestamp
    commit: str | None = None
    runs: list[ClusterRun] = Field(default_factory=list)


class JobAttempt(BaseModel):
    attempt_no: int
    started: Timestamp
    finished: Timestamp | None = None
    gate_result: e.GateResult | None = None
    error_lines: list[str] = Field(default_factory=list)
    files_read: list[str] = Field(default_factory=list)
    log_path: str | None = None


class Job(BaseModel):
    job_id: str
    kind: Literal["draft_rule"] = "draft_rule"
    rule_id: str
    branch: str
    cluster_key: str
    cluster_item_ids: list[str] = Field(default_factory=list)
    cluster_hash: str
    status: e.JobStatus
    pid: int | None = None
    attempts: list[JobAttempt] = Field(default_factory=list)
    pr_url: str | None = None
    dry: bool = False
    config: str
    ts: Timestamp


class ReplayResult(BaseModel):
    status: Literal["ok", "not_evaluated"]
    verdicts: list[Verdict] = Field(default_factory=list)
    case_verdict: e.Verdict | None = None
    negq_miss: int = 0


class DryRun(BaseModel):
    rule_id: str
    changed_cases: int
    changed_atoms: int
    changed_golden_outside_sources: int
    flag_many: bool
    observed_changes: list[dict[str, JsonValue]] = Field(default_factory=list)


class GateCheck(BaseModel):
    id: int
    passed: bool
    detail: str


class GateResult(BaseModel):
    passed: bool
    checks: list[GateCheck] = Field(default_factory=list)
    error_lines: list[str] = Field(default_factory=list)
    observed_changes: list[dict[str, JsonValue]] = Field(default_factory=list)
    revealed_case_ids: list[str] = Field(default_factory=list)


class RedWrong(BaseModel):
    k: int
    n: int


class CorrectRefusals(BaseModel):
    x: int
    n: int


class WhoDecided(BaseModel):
    code: int
    llm_votes: int
    human: int
    denominator: int
    measured_at: Timestamp
    source: Literal["ledger", "oracle"]


class OracleReport(BaseModel):
    measured_at: Timestamp
    n_cases: int
    cache_miss: int
    negq_miss: int
    pack_v1: str
    pack_v2: str | None = None
    transitions: dict[str, int] = Field(default_factory=dict)
    abstain_to_correct: int
    correct_to_wrong: int
    red_wrong: RedWrong
    correct_refusals: CorrectRefusals
    who_decided_v1: WhoDecided
    who_decided_v2: WhoDecided | None = None
    mcnemar_p: float | None = None
    overlap_flags: int
    standin: bool
