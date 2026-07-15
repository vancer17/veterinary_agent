##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/__init__.py
# 作用: 作为 VetOutputSafetyReviewer 一级包统一出口，集中暴露 DTO、枚举、错误、端口、服务、handler 与 trace。
# 边界: 其他包必须从本文件导入输出安全审查能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.vet_output_safety_reviewer.dto import (
    JsonMap,
    MedicationPolicyAnalysisRequestDto,
    MedicationPolicyDecisionDto,
    MedicationPolicyFindingDto,
    MedicationSpanCandidateDto,
    OutputGuardActionDto,
    OutputReviewTracePatchDto,
    OutputReviewTraceRecordDto,
    OutputReviewTraceWriteResultDto,
    OutputSafetyFindingDto,
    OutputSafetyReviewRequestDto,
    OutputSafetyReviewResultDto,
    ReviewDomainResultDto,
    ReviewInputContextDto,
    RewritePlanDto,
    VetOutputSafetyReviewerDto,
)
from veterinary_agent.vet_output_safety_reviewer.enums import (
    MedicationPolicyDecisionStatus,
    OutputFindingSeverity,
    OutputFindingType,
    OutputReviewTraceWriteStatus,
    ReviewActionType,
    ReviewDomain,
    ReviewStatus,
    VetOutputSafetyReviewerErrorCode,
    VetOutputSafetyReviewerOperation,
)
from veterinary_agent.vet_output_safety_reviewer.errors import (
    VetOutputSafetyReviewerError,
    VetOutputSafetyReviewerErrorDto,
    build_vet_output_safety_reviewer_error_dto,
    is_vet_output_safety_reviewer_error_retryable_by_default,
)
from veterinary_agent.vet_output_safety_reviewer.handler import (
    VetOutputSafetyReviewerGuardrailHandler,
    create_vet_output_safety_reviewer_guardrail_handler,
)
from veterinary_agent.vet_output_safety_reviewer.ports import (
    MedicationPolicyPort,
    TODO_MEDICATION_POLICY_ERROR_CODE,
    TodoMedicationPolicyPort,
)
from veterinary_agent.vet_output_safety_reviewer.service import (
    DefaultVetOutputSafetyReviewer,
    VetOutputSafetyReviewer,
    create_default_vet_output_safety_reviewer,
    request_ref_from_context,
)
from veterinary_agent.vet_output_safety_reviewer.trace import (
    LogicTraceVetOutputSafetyReviewerTraceSink,
    TODO_OUTPUT_REVIEW_TRACE_ERROR_CODE,
    TodoVetOutputSafetyReviewerTraceSink,
    VetOutputSafetyReviewerTraceSink,
)

__all__: tuple[str, ...] = (
    "DefaultVetOutputSafetyReviewer",
    "JsonMap",
    "LogicTraceVetOutputSafetyReviewerTraceSink",
    "MedicationPolicyAnalysisRequestDto",
    "MedicationPolicyDecisionDto",
    "MedicationPolicyDecisionStatus",
    "MedicationPolicyFindingDto",
    "MedicationPolicyPort",
    "MedicationSpanCandidateDto",
    "OutputFindingSeverity",
    "OutputFindingType",
    "OutputGuardActionDto",
    "OutputReviewTracePatchDto",
    "OutputReviewTraceRecordDto",
    "OutputReviewTraceWriteResultDto",
    "OutputReviewTraceWriteStatus",
    "OutputSafetyFindingDto",
    "OutputSafetyReviewRequestDto",
    "OutputSafetyReviewResultDto",
    "ReviewActionType",
    "ReviewDomain",
    "ReviewDomainResultDto",
    "ReviewInputContextDto",
    "ReviewStatus",
    "RewritePlanDto",
    "TODO_MEDICATION_POLICY_ERROR_CODE",
    "TODO_OUTPUT_REVIEW_TRACE_ERROR_CODE",
    "TodoMedicationPolicyPort",
    "TodoVetOutputSafetyReviewerTraceSink",
    "VetOutputSafetyReviewer",
    "VetOutputSafetyReviewerDto",
    "VetOutputSafetyReviewerError",
    "VetOutputSafetyReviewerErrorCode",
    "VetOutputSafetyReviewerErrorDto",
    "VetOutputSafetyReviewerGuardrailHandler",
    "VetOutputSafetyReviewerOperation",
    "VetOutputSafetyReviewerTraceSink",
    "build_vet_output_safety_reviewer_error_dto",
    "create_default_vet_output_safety_reviewer",
    "create_vet_output_safety_reviewer_guardrail_handler",
    "is_vet_output_safety_reviewer_error_retryable_by_default",
    "request_ref_from_context",
)
