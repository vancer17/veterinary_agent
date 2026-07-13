##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/enums.py
# 作用: 定义 NonmedicalPetCareAgent 的护理领域、建议维度、检索用途、草稿状态、错误码与操作名。
# 边界: 仅承载稳定枚举值，不执行建议规划、RAG 检索、个性化生成或输出安全审查。
##################################################################################################

from enum import StrEnum


class CareDomain(StrEnum):
    """非医疗养宠建议领域。"""

    NUTRITION = "NUTRITION"
    BEHAVIOR = "BEHAVIOR"
    DAILY_CARE = "DAILY_CARE"
    ENVIRONMENT = "ENVIRONMENT"
    EXERCISE = "EXERCISE"
    WEIGHT_MANAGEMENT = "WEIGHT_MANAGEMENT"
    GENERAL_PET_CARE = "GENERAL_PET_CARE"


class AdviceDimensionCode(StrEnum):
    """非医疗建议维度稳定代码。"""

    GOAL_CLARIFICATION = "GOAL_CLARIFICATION"
    APPLICABILITY_CHECK = "APPLICABILITY_CHECK"
    STEPWISE_PLAN = "STEPWISE_PLAN"
    GRADUAL_PACE = "GRADUAL_PACE"
    OBSERVATION_METRICS = "OBSERVATION_METRICS"
    RISK_BOUNDARY = "RISK_BOUNDARY"
    ALTERNATIVE_OPTIONS = "ALTERNATIVE_OPTIONS"
    MISCONCEPTION_WARNING = "MISCONCEPTION_WARNING"
    PROFESSIONAL_ESCALATION = "PROFESSIONAL_ESCALATION"


class PersonalizationLevel(StrEnum):
    """非医疗建议个性化程度。"""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MINIMAL = "MINIMAL"
    UNAVAILABLE = "UNAVAILABLE"


class NonmedicalDraftStatus(StrEnum):
    """NonmedicalPetCareAgent 草稿状态。"""

    DRAFT_READY = "DRAFT_READY"
    CONSERVATIVE_WITH_SIGNAL = "CONSERVATIVE_WITH_SIGNAL"
    NEEDS_SAFETY_ESCALATION = "NEEDS_SAFETY_ESCALATION"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    KNOWLEDGE_DEGRADED_CONSERVATIVE = "KNOWLEDGE_DEGRADED_CONSERVATIVE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    FAILED = "FAILED"


class NonmedicalRetrievalPurpose(StrEnum):
    """非医疗 Advice-RAG 检索用途。"""

    PET_CARE_PRINCIPLE = "PET_CARE_PRINCIPLE"
    BEHAVIOR_GUIDANCE = "BEHAVIOR_GUIDANCE"
    NUTRITION_BOUNDARY = "NUTRITION_BOUNDARY"
    ENVIRONMENT_MANAGEMENT = "ENVIRONMENT_MANAGEMENT"
    WEIGHT_MANAGEMENT_BOUNDARY = "WEIGHT_MANAGEMENT_BOUNDARY"
    RISK_BOUNDARY = "RISK_BOUNDARY"


class NonmedicalTraceWriteStatus(StrEnum):
    """NonmedicalPetCareAgent trace patch 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class NonmedicalAgentOperation(StrEnum):
    """NonmedicalPetCareAgent 对外和内部稳定操作名。"""

    GENERATE_DRAFT = "GenerateNonMedicalAdviceDraft"
    PLAN_ADVICE = "PlanNonMedicalAdvice"
    BUILD_KNOWLEDGE_PLAN = "BuildPetCareKnowledgePlan"
    RETRIEVE_EVIDENCE = "RetrievePetCareEvidence"
    VALIDATE_DRAFT = "ValidateNonMedicalAdviceDraft"
    WRITE_TRACE = "WriteNonMedicalTrace"


class NonmedicalAgentErrorCode(StrEnum):
    """NonmedicalPetCareAgent 稳定错误码。"""

    NONMED_NOT_READY = "NONMED_NOT_READY"
    NONMED_EXECUTOR_MISMATCH = "NONMED_EXECUTOR_MISMATCH"
    NONMED_MISSING_CURRENT_PET_ID = "NONMED_MISSING_CURRENT_PET_ID"
    NONMED_CONTEXT_MISSING = "NONMED_CONTEXT_MISSING"
    NONMED_PET_CONTEXT_INVALID = "NONMED_PET_CONTEXT_INVALID"
    NONMED_ASSESSMENT_MISSING = "NONMED_ASSESSMENT_MISSING"
    NONMED_SAFETY_ESCALATION_REQUIRED = "NONMED_SAFETY_ESCALATION_REQUIRED"
    NONMED_ADVICE_PLAN_FAILED = "NONMED_ADVICE_PLAN_FAILED"
    NONMED_KNOWLEDGE_PLAN_INVALID = "NONMED_KNOWLEDGE_PLAN_INVALID"
    NONMED_RAG_DEGRADED = "NONMED_RAG_DEGRADED"
    NONMED_INSUFFICIENT_EVIDENCE = "NONMED_INSUFFICIENT_EVIDENCE"
    NONMED_PERSONALIZATION_INSUFFICIENT = "NONMED_PERSONALIZATION_INSUFFICIENT"
    NONMED_WRITER_TIMEOUT = "NONMED_WRITER_TIMEOUT"
    NONMED_SELF_CHECK_FAILED = "NONMED_SELF_CHECK_FAILED"
    NONMED_OUTPUT_SCHEMA_INVALID = "NONMED_OUTPUT_SCHEMA_INVALID"
    NONMED_TOKEN_BUDGET_EXCEEDED = "NONMED_TOKEN_BUDGET_EXCEEDED"  # nosec B105
    NONMED_RUNTIME_CONFIG_UNAVAILABLE = "NONMED_RUNTIME_CONFIG_UNAVAILABLE"
    NONMED_INTERNAL_ERROR = "NONMED_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "AdviceDimensionCode",
    "CareDomain",
    "NonmedicalAgentErrorCode",
    "NonmedicalAgentOperation",
    "NonmedicalDraftStatus",
    "NonmedicalRetrievalPurpose",
    "NonmedicalTraceWriteStatus",
    "PersonalizationLevel",
)
