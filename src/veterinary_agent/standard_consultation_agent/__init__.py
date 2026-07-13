##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/__init__.py
# 作用: 作为 StandardConsultationAgent 一级包统一出口，集中暴露 DTO、枚举、错误、端口、服务与节点。
# 边界: 其他包必须从本文件导入标准问诊能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.standard_consultation_agent.dto import (
    CandidateQuestionDto,
    EscalationRequestDto,
    EvidenceBindingDto,
    JsonMap,
    QuestionBudgetDto,
    RagEvidenceBundleDto,
    RagEvidenceHintDto,
    ReadinessProfileDto,
    SlotProgressPatchDto,
    StandardConsultationDraftDto,
    StandardConsultationDto,
    StandardConsultationRequestDto,
    StandardConsultationTraceRecordDto,
    StandardSessionStateDto,
    StandardTracePatchDto,
    StandardTraceWriteResultDto,
)
from veterinary_agent.standard_consultation_agent.enums import (
    ConsultationLayer,
    DraftStatus,
    QuestionPurpose,
    RetrievalPurpose,
    RiskImpact,
    StandardConsultationErrorCode,
    StandardConsultationOperation,
    StandardTraceWriteStatus,
)
from veterinary_agent.standard_consultation_agent.errors import (
    StandardConsultationError,
    StandardConsultationErrorDto,
    build_standard_consultation_error_dto,
    is_standard_consultation_error_retryable_by_default,
)
from veterinary_agent.standard_consultation_agent.node import (
    StandardConsultationAgentGraphNode,
)
from veterinary_agent.standard_consultation_agent.ports import (
    TODO_MEDICATION_POLICY_ERROR_CODE,
    TODO_STANDARD_RAG_ERROR_CODE,
    StandardMedicationPolicyPort,
    StandardRagPort,
    TodoStandardMedicationPolicyPort,
    TodoStandardRagPort,
)
from veterinary_agent.standard_consultation_agent.service import (
    DefaultStandardConsultationAgent,
    StandardConsultationAgent,
    create_default_standard_consultation_agent,
)
from veterinary_agent.standard_consultation_agent.trace import (
    LogicTraceStandardConsultationTraceSink,
    StandardConsultationTraceSink,
    TODO_STANDARD_TRACE_ERROR_CODE,
    TodoStandardConsultationTraceSink,
)

__all__: tuple[str, ...] = (
    "CandidateQuestionDto",
    "ConsultationLayer",
    "DefaultStandardConsultationAgent",
    "DraftStatus",
    "EscalationRequestDto",
    "EvidenceBindingDto",
    "JsonMap",
    "LogicTraceStandardConsultationTraceSink",
    "QuestionBudgetDto",
    "QuestionPurpose",
    "RagEvidenceBundleDto",
    "RagEvidenceHintDto",
    "ReadinessProfileDto",
    "RetrievalPurpose",
    "RiskImpact",
    "SlotProgressPatchDto",
    "StandardConsultationAgent",
    "StandardConsultationAgentGraphNode",
    "StandardConsultationDraftDto",
    "StandardConsultationDto",
    "StandardConsultationError",
    "StandardConsultationErrorCode",
    "StandardConsultationErrorDto",
    "StandardConsultationOperation",
    "StandardConsultationRequestDto",
    "StandardConsultationTraceRecordDto",
    "StandardConsultationTraceSink",
    "StandardMedicationPolicyPort",
    "StandardRagPort",
    "StandardSessionStateDto",
    "StandardTracePatchDto",
    "StandardTraceWriteResultDto",
    "StandardTraceWriteStatus",
    "TODO_MEDICATION_POLICY_ERROR_CODE",
    "TODO_STANDARD_RAG_ERROR_CODE",
    "TODO_STANDARD_TRACE_ERROR_CODE",
    "TodoStandardConsultationTraceSink",
    "TodoStandardMedicationPolicyPort",
    "TodoStandardRagPort",
    "build_standard_consultation_error_dto",
    "create_default_standard_consultation_agent",
    "is_standard_consultation_error_retryable_by_default",
)
