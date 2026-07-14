##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/enums.py
# 作用: 定义 VetInputSafetyAssessor 的意图、安全信号、路由、裁决方法、状态、错误码与操作名枚举。
# 边界: 仅承载稳定枚举值，不执行安全评估、弱依赖调用、业务裁决或 trace 写入。
##################################################################################################

from enum import StrEnum


class VetIntent(StrEnum):
    """兽医输入侧意图。"""

    ACUTE_EVENT = "ACUTE_EVENT"
    EDUCATION = "EDUCATION"
    HYPOTHETICAL = "HYPOTHETICAL"
    SYMPTOM_TRIAGE = "SYMPTOM_TRIAGE"
    NONMED_NUTRITION = "NONMED_NUTRITION"
    NONMED_BEHAVIOR = "NONMED_BEHAVIOR"
    NONMED_CARE = "NONMED_CARE"
    REPORT_INTERPRETATION = "REPORT_INTERPRETATION"
    GENERAL_QA = "GENERAL_QA"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class SafetySignalCode(StrEnum):
    """输入侧安全信号码。"""

    SAF_01_TOXIC_SUBSTANCE = "SAF_01_TOXIC_SUBSTANCE"
    SAF_03_ACUTE_RED_FLAG = "SAF_03_ACUTE_RED_FLAG"
    CROSS_DOMAIN_SYMPTOM = "CROSS_DOMAIN_SYMPTOM"
    REALTIME_MARKER = "REALTIME_MARKER"
    EDUCATION_MARKER = "EDUCATION_MARKER"
    HYPOTHETICAL_MARKER = "HYPOTHETICAL_MARKER"


class SignalStrength(StrEnum):
    """输入侧安全信号强度。"""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SignalSource(StrEnum):
    """输入侧安全信号来源。"""

    LEXICAL = "lexical"
    SEMANTIC_ROUTER = "semantic_router"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LLM_ARBITRATED = "llm_arbitrated"
    DETERMINISTIC = "deterministic"


class RouteLabel(StrEnum):
    """输入安全路由标签。"""

    NORMAL = "normal"
    SAFETY_TRIGGER = "safety_trigger"


class AssessmentStatus(StrEnum):
    """输入安全评估结果状态。"""

    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"


class AssessmentMethod(StrEnum):
    """输入安全评估采用的最终方法。"""

    DETERMINISTIC = "deterministic"
    SEMANTIC_ROUTER = "semantic_router"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LLM_ARBITRATED = "llm_arbitrated"
    FALLBACK_DEFAULT = "fallback_default"


class DisambiguationMethod(StrEnum):
    """模糊输入消歧方法。"""

    EXPLICIT = "explicit"
    SEMANTIC_ROUTER = "semantic_router"
    STRUCTURED_EXTRACTION = "structured_extraction"
    LLM_ARBITRATED = "llm_arbitrated"
    MEMORY_PUSHED = "memory_pushed"
    COLD_START_DOWNGRADE = "cold_start_downgrade"
    DETERMINISTIC_OVERRIDE = "deterministic_override"
    FALLBACK_DEFAULT = "fallback_default"


class VetInputAssessmentTraceWriteStatus(StrEnum):
    """输入安全评估摘要 trace 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class VetInputSafetyAssessorOperation(StrEnum):
    """VetInputSafetyAssessor 对外和内部稳定操作名。"""

    ASSESS_INPUT = "AssessVetInputSafety"
    BATCH_ASSESS_INPUT = "BatchAssessVetInputSafety"
    VALIDATE_INPUT = "ValidateVetInputAssessmentInput"
    MATCH_SIGNALS = "MatchVetInputSafetySignals"
    SCORE_SEMANTIC_ROUTE = "ScoreVetInputSemanticRoute"
    EXTRACT_STRUCTURED_SIGNALS = "ExtractVetInputStructuredSignals"
    RUN_LLM_ARBITRATION = "RunVetInputSafetyArbitration"
    RESOLVE_DECISION = "ResolveVetInputSafetyDecision"
    WRITE_TRACE = "WriteVetInputSafetyTrace"


class VetInputSafetyAssessorErrorCode(StrEnum):
    """VetInputSafetyAssessor 稳定错误码。"""

    INPUT_ASSESS_NOT_READY = "INPUT_ASSESS_NOT_READY"
    INPUT_ASSESS_INVALID_REQUEST = "INPUT_ASSESS_INVALID_REQUEST"
    INPUT_ASSESS_CURRENT_PET_INVALID = "INPUT_ASSESS_CURRENT_PET_INVALID"
    INPUT_ASSESS_EMPTY_TASK_TEXT = "INPUT_ASSESS_EMPTY_TASK_TEXT"
    INPUT_ASSESS_SIGNAL_DICTIONARY_UNAVAILABLE = (
        "INPUT_ASSESS_SIGNAL_DICTIONARY_UNAVAILABLE"
    )
    INPUT_ASSESS_SEMANTIC_ROUTER_UNAVAILABLE = (
        "INPUT_ASSESS_SEMANTIC_ROUTER_UNAVAILABLE"
    )
    INPUT_ASSESS_LOCAL_EXTRACTOR_UNAVAILABLE = (
        "INPUT_ASSESS_LOCAL_EXTRACTOR_UNAVAILABLE"
    )
    INPUT_ASSESS_LLM_UNAVAILABLE = "INPUT_ASSESS_LLM_UNAVAILABLE"
    INPUT_ASSESS_OUTPUT_SCHEMA_INVALID = "INPUT_ASSESS_OUTPUT_SCHEMA_INVALID"
    INPUT_ASSESS_LOW_CONFIDENCE = "INPUT_ASSESS_LOW_CONFIDENCE"
    INPUT_ASSESS_RUNTIME_CONFIG_UNAVAILABLE = "INPUT_ASSESS_RUNTIME_CONFIG_UNAVAILABLE"
    INPUT_ASSESS_INTERNAL_ERROR = "INPUT_ASSESS_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "AssessmentMethod",
    "AssessmentStatus",
    "DisambiguationMethod",
    "RouteLabel",
    "SafetySignalCode",
    "SignalSource",
    "SignalStrength",
    "VetInputAssessmentTraceWriteStatus",
    "VetInputSafetyAssessorErrorCode",
    "VetInputSafetyAssessorOperation",
    "VetIntent",
)
