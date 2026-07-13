##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/enums.py
# 作用: 定义 SafetyTriggerAgent 的确认模式、急症 hint、草稿状态、错误码与操作枚举。
# 边界: 仅承载稳定枚举值，不执行急症生成、输入判决、RAG 检索或 trace 写入。
##################################################################################################

from enum import StrEnum


class ConfirmationMode(StrEnum):
    """急症关键确认模式。"""

    NO_QUESTION = "NO_QUESTION"
    RECORD_AND_GO = "RECORD_AND_GO"
    ONE_CONFIRMATION = "ONE_CONFIRMATION"


class EmergencyHintCode(StrEnum):
    """急症生成弱提示类型。"""

    TOXIC_EXPOSURE_HINT = "TOXIC_EXPOSURE_HINT"
    SEIZURE_HINT = "SEIZURE_HINT"
    BREATHING_DISTRESS_HINT = "BREATHING_DISTRESS_HINT"
    BLEEDING_OR_TRAUMA_HINT = "BLEEDING_OR_TRAUMA_HINT"
    COLLAPSE_HINT = "COLLAPSE_HINT"
    PERSISTENT_GI_HINT = "PERSISTENT_GI_HINT"
    URINARY_BLOCKAGE_HINT = "URINARY_BLOCKAGE_HINT"
    UNKNOWN_RED_FLAG_HINT = "UNKNOWN_RED_FLAG_HINT"


class SafetyTriggerDraftStatus(StrEnum):
    """急症草稿状态。"""

    DRAFT_READY = "DRAFT_READY"
    FALLBACK_READY = "FALLBACK_READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    FAILED = "FAILED"


class SafetyTraceWriteStatus(StrEnum):
    """急症 trace patch 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class SafetyTriggerOperation(StrEnum):
    """SafetyTriggerAgent 对外和内部稳定操作名。"""

    GENERATE_DRAFT = "GenerateSafetyTriggerDraft"
    PLAN_CONFIRMATION = "PlanSafetyKeyConfirmation"
    VALIDATE_DRAFT = "ValidateSafetyTriggerDraft"
    BUILD_FALLBACK = "BuildSafetyFallbackDraft"
    VERIFY_RAG_DISABLED = "VerifySafetyRagDisabled"
    WRITE_TRACE = "WriteSafetyTriggerTrace"


class SafetyTriggerErrorCode(StrEnum):
    """SafetyTriggerAgent 稳定错误码。"""

    SAFETY_TRIGGER_NOT_READY = "SAFETY_TRIGGER_NOT_READY"
    SAFETY_TRIGGER_PROFILE_MISMATCH = "SAFETY_TRIGGER_PROFILE_MISMATCH"
    SAFETY_TRIGGER_MISSING_CURRENT_PET_ID = "SAFETY_TRIGGER_MISSING_CURRENT_PET_ID"
    SAFETY_TRIGGER_CONTEXT_MISSING = "SAFETY_TRIGGER_CONTEXT_MISSING"
    SAFETY_TRIGGER_PET_CONTEXT_INVALID = "SAFETY_TRIGGER_PET_CONTEXT_INVALID"
    SAFETY_TRIGGER_SIGNAL_MISSING = "SAFETY_TRIGGER_SIGNAL_MISSING"
    SAFETY_TRIGGER_RAG_FORBIDDEN = "SAFETY_TRIGGER_RAG_FORBIDDEN"
    SAFETY_TRIGGER_CONFIRMATION_PLAN_FAILED = "SAFETY_TRIGGER_CONFIRMATION_PLAN_FAILED"
    SAFETY_TRIGGER_WRITER_TIMEOUT = "SAFETY_TRIGGER_WRITER_TIMEOUT"
    SAFETY_TRIGGER_VET_DIRECTION_MISSING = "SAFETY_TRIGGER_VET_DIRECTION_MISSING"
    SAFETY_TRIGGER_CONFIRMATION_LIMIT_EXCEEDED = (
        "SAFETY_TRIGGER_CONFIRMATION_LIMIT_EXCEEDED"
    )
    SAFETY_TRIGGER_DRAFT_UNSAFE = "SAFETY_TRIGGER_DRAFT_UNSAFE"
    SAFETY_TRIGGER_OUTPUT_SCHEMA_INVALID = "SAFETY_TRIGGER_OUTPUT_SCHEMA_INVALID"
    SAFETY_TRIGGER_RUNTIME_CONFIG_UNAVAILABLE = (
        "SAFETY_TRIGGER_RUNTIME_CONFIG_UNAVAILABLE"
    )
    SAFETY_TRIGGER_INTERNAL_ERROR = "SAFETY_TRIGGER_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "ConfirmationMode",
    "EmergencyHintCode",
    "SafetyTraceWriteStatus",
    "SafetyTriggerDraftStatus",
    "SafetyTriggerErrorCode",
    "SafetyTriggerOperation",
)
