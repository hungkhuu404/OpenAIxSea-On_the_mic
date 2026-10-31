"""Wire values from contracts section 4; rule families may also be T5 ids."""

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints


class Verdict(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    ABSTAIN = "abstain"


class UILabel(StrEnum):
    GREEN = "Within file"
    YELLOW = "Beyond file"
    RED = "Prohibited"
    ABSTAIN = "Needs human"


class Axis(StrEnum):
    CONG_DUNG = "cong_dung"
    THANH_PHAN = "thanh_phan"
    CHAT_LUONG = "chat_luong"
    DOI_TUONG = "doi_tuong"
    LIEU_DUNG = "lieu_dung"
    XUAT_XU = "xuat_xu"
    HO_SO = "ho_so"
    KHUYEN_CAO = "khuyen_cao"
    VIEN_DAN = "vien_dan"
    SO_SANH = "so_sanh"
    TUYET_DOI = "tuyet_doi"
    GIA = "gia"
    KHUYEN_MAI = "khuyen_mai"
    BAO_HANH = "bao_hanh"


class AxisGroup(StrEnum):
    CONG_DUNG = "cong_dung"
    THANH_PHAN = "thanh_phan"
    LIEU_DUNG = "lieu_dung"
    DOI_TUONG = "doi_tuong"
    HO_SO = "ho_so"
    XUAT_XU = "xuat_xu"


class SpeakerRole(StrEnum):
    BAC_SI = "bac_si"
    DUOC_SI = "duoc_si"
    NGUOI_BENH = "nguoi_benh"
    KOL = "kol"
    NGUOI_BAN = "nguoi_ban"
    KHONG_RO = "khong_ro"


class Attribution(StrEnum):
    PRESS_ATTRIBUTED = "press_attributed"
    PRESS_UNKNOWN = "press_unknown"
    CONSTRUCTED = "constructed"
    NOT_APPLICABLE = "not_applicable"


class Channel(StrEnum):
    LIVESTREAM = "livestream"
    WRITTEN = "written"
    VIDEO = "video"


class CategoryNorm(StrEnum):
    TPBVSK = "TPBVSK"
    THUC_PHAM_BO_SUNG = "thuc_pham_bo_sung"
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    SKU_UNRESOLVED = "SKU_UNRESOLVED"
    ASR_LOW = "ASR_LOW"
    SUBJECT_AMBIGUOUS = "SUBJECT_AMBIGUOUS"
    DOSSIER_EMPTY = "DOSSIER_EMPTY"
    DOSSIER_FIELD_MISSING = "DOSSIER_FIELD_MISSING"
    RULE_AMBIGUOUS = "RULE_AMBIGUOUS"
    VOTES_SPLIT = "VOTES_SPLIT"
    LLM_NEI = "LLM_NEI"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_OFF = "LLM_OFF"
    LAW_NOT_EFFECTIVE = "LAW_NOT_EFFECTIVE"
    RULE_QUARANTINED = "RULE_QUARANTINED"


# The suffixes are open sets (including human-approved T5 rule ids).
DecidedBy = Annotated[str, StringConstraints(
    pattern=r"^(allow_list|llm_votes|rule:[A-Za-z0-9_]+|abstain:(SKU_UNRESOLVED|ASR_LOW|SUBJECT_AMBIGUOUS|DOSSIER_EMPTY|DOSSIER_FIELD_MISSING|RULE_AMBIGUOUS|VOTES_SPLIT|LLM_NEI|LLM_TIMEOUT|LLM_OFF|LAW_NOT_EFFECTIVE|RULE_QUARANTINED))$"
)]


class SessionFlagCode(StrEnum):
    NO_SELF_DECLARATION_AT_AS_OF = "NO_SELF_DECLARATION_AT_AS_OF"
    NO_AD_CONFIRMATION = "NO_AD_CONFIRMATION"
    RECALLED_AT_AS_OF = "RECALLED_AT_AS_OF"


class AtomFlag(StrEnum):
    RECALLED_AFTER = "RECALLED_AFTER"
    PREV_RED = "PREV_RED"


class Purpose(StrEnum):
    LIVE = "live"
    SHADOW = "shadow"
    ORACLE = "oracle"


class Backend(StrEnum):
    API = "api"
    CODEX = "codex"
    OFF = "off"


class CallKind(StrEnum):
    S1 = "s1"
    T1 = "t1"
    T2 = "t2"
    NEGQ = "negq"


class RuleOrigin(StrEnum):
    LAW_COMPILED = "law_compiled"
    T5 = "t5"


class RuleStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    QUARANTINED = "quarantined"


class PackMode(StrEnum):
    LIVE = "live"
    MEASURE = "measure"


class LegalBasisFamily(StrEnum):
    R04 = "R04"
    R05 = "R05"
    R06 = "R06"
    R08 = "R08"
    R09 = "R09"
    L1_PREDECL = "L1_PREDECL"
    L1_RECALL = "L1_RECALL"
    STANDARD = "STANDARD"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class ErrorScale(StrEnum):
    CORRECT = "correct"
    ABSTAIN = "abstain"
    LIGHTER_THAN_LABEL = "lighter than label"
    HEAVIER_THAN_LABEL = "heavier than label"
