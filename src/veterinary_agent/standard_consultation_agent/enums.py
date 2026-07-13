##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/enums.py
# 作用: 定义 StandardConsultationAgent 的层级、问题目的、RAG 用途、状态、错误码与操作枚举。
# 边界: 仅承载稳定枚举值，不执行问诊调度、模型调用、RAG 检索或 trace 写入。
##################################################################################################

from enum import StrEnum


class ConsultationLayer(StrEnum):
    """标准问诊内部层级。"""

    L0_COLLECTION = "L0_COLLECTION"
    L1_TRIAGE = "L1_TRIAGE"
    L2_DIRECTION = "L2_DIRECTION"
    L3_DIFFERENTIAL = "L3_DIFFERENTIAL"
    L4_CARE_PLAN = "L4_CARE_PLAN"


class QuestionPurpose(StrEnum):
    """候选追问问题目的。"""

    ACUTE_RULE_OUT = "ACUTE_RULE_OUT"
    CHIEF_COMPLAINT_CHARACTERIZATION = "CHIEF_COMPLAINT_CHARACTERIZATION"
    DIRECTION_DISAMBIGUATION = "DIRECTION_DISAMBIGUATION"
    DIFFERENTIAL_CONVERGENCE = "DIFFERENTIAL_CONVERGENCE"
    CARE_CONTRAINDICATION_CHECK = "CARE_CONTRAINDICATION_CHECK"
    OCR_VALUE_CONFIRMATION = "OCR_VALUE_CONFIRMATION"


class RetrievalPurpose(StrEnum):
    """标准问诊阶段式 RAG 检索用途。"""

    STANDARD_PRESEARCH = "STANDARD_PRESEARCH"
    QUESTION_GENERATION = "QUESTION_GENERATION"
    TRIAGE_SUPPORT = "TRIAGE_SUPPORT"
    DIRECTION_HINT = "DIRECTION_HINT"
    DIFFERENTIAL_SUPPORT = "DIFFERENTIAL_SUPPORT"
    CARE_BOUNDARY = "CARE_BOUNDARY"
    EVIDENCE_CONSISTENCY_CHECK = "EVIDENCE_CONSISTENCY_CHECK"


class DraftStatus(StrEnum):
    """标准问诊草稿状态。"""

    DRAFT_READY = "DRAFT_READY"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"
    NEEDS_SAFETY_ESCALATION = "NEEDS_SAFETY_ESCALATION"
    RAG_DEGRADED_CONSERVATIVE = "RAG_DEGRADED_CONSERVATIVE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    FAILED = "FAILED"


class RiskImpact(StrEnum):
    """追问问题的风险影响等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StandardTraceWriteStatus(StrEnum):
    """标准问诊 trace patch 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class StandardConsultationOperation(StrEnum):
    """StandardConsultationAgent 对外和内部稳定操作名。"""

    GENERATE_DRAFT = "GenerateStandardConsultationDraft"
    EVALUATE_READINESS = "EvaluateStandardReadiness"
    COLLECT_QUESTIONS = "CollectConsultationQuestions"
    RUN_PLAN = "RunStandardConsultationPlan"
    VALIDATE_DRAFT = "ValidateStandardConsultationDraft"
    WRITE_TRACE = "WriteStandardConsultationTrace"


class StandardConsultationErrorCode(StrEnum):
    """StandardConsultationAgent 稳定错误码。"""

    STANDARD_NOT_READY = "STANDARD_NOT_READY"
    STANDARD_PROFILE_MISMATCH = "STANDARD_PROFILE_MISMATCH"
    STANDARD_MISSING_CURRENT_PET_ID = "STANDARD_MISSING_CURRENT_PET_ID"
    STANDARD_CONTEXT_MISSING = "STANDARD_CONTEXT_MISSING"
    STANDARD_PET_CONTEXT_INVALID = "STANDARD_PET_CONTEXT_INVALID"
    STANDARD_RAG_REQUIRED_MISSING = "STANDARD_RAG_REQUIRED_MISSING"
    STANDARD_RAG_DEGRADED = "STANDARD_RAG_DEGRADED"
    STANDARD_FACT_LEDGER_INVALID = "STANDARD_FACT_LEDGER_INVALID"
    STANDARD_SUB_AGENT_SPEC_UNAVAILABLE = "STANDARD_SUB_AGENT_SPEC_UNAVAILABLE"
    STANDARD_SUB_AGENT_TIMEOUT = "STANDARD_SUB_AGENT_TIMEOUT"
    STANDARD_OUTPUT_SCHEMA_INVALID = "STANDARD_OUTPUT_SCHEMA_INVALID"
    STANDARD_ACUTE_ESCALATION_REQUESTED = "STANDARD_ACUTE_ESCALATION_REQUESTED"
    STANDARD_TOKEN_BUDGET_EXCEEDED = "STANDARD_TOKEN_BUDGET_EXCEEDED"  # nosec B105 - 错误码不是凭据。
    STANDARD_RUNTIME_CONFIG_UNAVAILABLE = "STANDARD_RUNTIME_CONFIG_UNAVAILABLE"
    STANDARD_INTERNAL_ERROR = "STANDARD_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "ConsultationLayer",
    "DraftStatus",
    "QuestionPurpose",
    "RetrievalPurpose",
    "RiskImpact",
    "StandardConsultationErrorCode",
    "StandardConsultationOperation",
    "StandardTraceWriteStatus",
)
