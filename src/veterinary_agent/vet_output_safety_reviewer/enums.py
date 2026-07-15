##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/enums.py
# 作用: 定义 VetOutputSafetyReviewer 稳定枚举，覆盖发现项、审查域、动作、状态、错误与 trace 状态。
# 边界: 仅承载枚举定义，不执行输出安全审查、不调用模型、不写入 trace 或护栏结果。
##################################################################################################

from enum import StrEnum


class OutputFindingSeverity(StrEnum):
    """输出安全发现项严重程度。"""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OutputFindingType(StrEnum):
    """输出安全审查发现项类型。"""

    T4_DETECTED = "T4_DETECTED"
    TOXIC_SUBSTANCE_RECOMMENDED = "TOXIC_SUBSTANCE_RECOMMENDED"
    MISSING_MEDICAL_DISCLAIMER = "MISSING_MEDICAL_DISCLAIMER"
    ACUTE_WITHOUT_URGENT_CARE = "ACUTE_WITHOUT_URGENT_CARE"
    DELAYED_CARE_RISK = "DELAYED_CARE_RISK"
    UNSUPPORTED_MEDICAL_CLAIM = "UNSUPPORTED_MEDICAL_CLAIM"
    FABRICATED_LAB_VALUE = "FABRICATED_LAB_VALUE"
    UNCONFIRMED_OCR_USED = "UNCONFIRMED_OCR_USED"
    REF_RANGE_HALLUCINATION = "REF_RANGE_HALLUCINATION"
    PROFILE_BOUNDARY_VIOLATION = "PROFILE_BOUNDARY_VIOLATION"
    RAG_CITATION_POLICY_VIOLATION = "RAG_CITATION_POLICY_VIOLATION"
    OCR_DOSE_REUSED_AS_ADVICE = "OCR_DOSE_REUSED_AS_ADVICE"
    NONMED_CROSS_DOMAIN_SIGNAL_IGNORED = "NONMED_CROSS_DOMAIN_SIGNAL_IGNORED"


class ReviewDomain(StrEnum):
    """输出安全审查风险域。"""

    MEDICATION_SAFETY = "MEDICATION_SAFETY"
    CLINICAL_SAFETY = "CLINICAL_SAFETY"
    EVIDENCE_GROUNDING = "EVIDENCE_GROUNDING"
    PROFILE_BOUNDARY = "PROFILE_BOUNDARY"
    DISCLAIMER_AND_TONE = "DISCLAIMER_AND_TONE"


class ReviewActionType(StrEnum):
    """输出安全审查动作类型。"""

    ALLOW = "ALLOW"
    REMOVE_SPAN = "REMOVE_SPAN"
    REWRITE_SPAN = "REWRITE_SPAN"
    APPEND_DISCLAIMER = "APPEND_DISCLAIMER"
    PREPEND_URGENT_CARE = "PREPEND_URGENT_CARE"
    SOFTEN_CLAIM = "SOFTEN_CLAIM"
    REMOVE_UNSUPPORTED_CLAIM = "REMOVE_UNSUPPORTED_CLAIM"
    FALLBACK_RECOMMENDED = "FALLBACK_RECOMMENDED"
    BLOCK_RECOMMENDED = "BLOCK_RECOMMENDED"


class ReviewStatus(StrEnum):
    """输出安全审查结果状态。"""

    REVIEWED_READY = "REVIEWED_READY"
    REVIEWED_WITH_REWRITE = "REVIEWED_WITH_REWRITE"
    FALLBACK_RECOMMENDED = "FALLBACK_RECOMMENDED"
    BLOCK_RECOMMENDED = "BLOCK_RECOMMENDED"
    DEGRADED_REVIEW = "DEGRADED_REVIEW"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    FAILED = "FAILED"


class MedicationPolicyDecisionStatus(StrEnum):
    """输出审查侧消费的用药策略判定状态。"""

    ALLOW = "allow"
    REWRITE_REQUIRED = "rewrite_required"
    BLOCK = "block"
    FALLBACK_REQUIRED = "fallback_required"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class OutputReviewTraceWriteStatus(StrEnum):
    """输出安全审查 trace 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class VetOutputSafetyReviewerOperation(StrEnum):
    """VetOutputSafetyReviewer 稳定操作名。"""

    REVIEW_DRAFT_RESPONSE_SAFETY = "ReviewDraftResponseSafety"
    REVIEW_OUTPUT_SAFETY_DOMAIN = "ReviewOutputSafetyDomain"
    BUILD_OUTPUT_REWRITE_PLAN = "BuildOutputRewritePlan"
    VALIDATE_OUTPUT_SAFETY_REVIEW_RESULT = "ValidateOutputSafetyReviewResult"
    RUN_GUARDRAIL_HANDLER = "RunGuardrailHandler"
    WRITE_TRACE = "WriteOutputReviewTrace"


class VetOutputSafetyReviewerErrorCode(StrEnum):
    """VetOutputSafetyReviewer 稳定错误码。"""

    OUTPUT_REVIEW_NOT_READY = "OUTPUT_REVIEW_NOT_READY"
    OUTPUT_REVIEW_DRAFT_MISSING = "OUTPUT_REVIEW_DRAFT_MISSING"
    OUTPUT_REVIEW_PROFILE_MISSING = "OUTPUT_REVIEW_PROFILE_MISSING"
    OUTPUT_REVIEW_ASSESSMENT_MISSING = "OUTPUT_REVIEW_ASSESSMENT_MISSING"
    OUTPUT_REVIEW_MED_POLICY_UNAVAILABLE = "OUTPUT_REVIEW_MED_POLICY_UNAVAILABLE"
    OUTPUT_REVIEW_AGENT_UNAVAILABLE = "OUTPUT_REVIEW_AGENT_UNAVAILABLE"
    OUTPUT_REVIEW_PARSE_FAILED = "OUTPUT_REVIEW_PARSE_FAILED"
    OUTPUT_REVIEW_SCHEMA_INVALID = "OUTPUT_REVIEW_SCHEMA_INVALID"
    OUTPUT_REVIEW_REWRITE_MISSING = "OUTPUT_REVIEW_REWRITE_MISSING"
    OUTPUT_REVIEW_P0_ACTION_MISSING = "OUTPUT_REVIEW_P0_ACTION_MISSING"
    OUTPUT_REVIEW_TRACE_PATCH_FAILED = "OUTPUT_REVIEW_TRACE_PATCH_FAILED"
    OUTPUT_REVIEW_RUNTIME_CONFIG_UNAVAILABLE = (
        "OUTPUT_REVIEW_RUNTIME_CONFIG_UNAVAILABLE"
    )
    OUTPUT_REVIEW_INTERNAL_ERROR = "OUTPUT_REVIEW_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "MedicationPolicyDecisionStatus",
    "OutputFindingSeverity",
    "OutputFindingType",
    "OutputReviewTraceWriteStatus",
    "ReviewActionType",
    "ReviewDomain",
    "ReviewStatus",
    "VetOutputSafetyReviewerErrorCode",
    "VetOutputSafetyReviewerOperation",
)
