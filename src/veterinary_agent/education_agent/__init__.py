##################################################################################################
# 文件: src/veterinary_agent/education_agent/__init__.py
# 作用: 作为 EducationAgent 一级包统一出口，集中暴露 DTO、枚举、错误、端口、服务与节点。
# 边界: 其他包必须从本文件导入科普组件能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.education_agent.dto import (
    EducationAgentDto,
    EducationBriefDto,
    EducationContentPlanDto,
    EducationDraftDto,
    EducationGenerationRequestDto,
    EducationRagResultDto,
    EducationRetrievalPlanDto,
    EducationTracePatchDto,
    EducationTraceRecordDto,
    EducationTraceWriteResultDto,
    EvidenceBindingDto,
    EvidenceCardDto,
    EvidenceHintDto,
    EvidenceSufficiencyResultDto,
    ExplanationDimensionDto,
    ExplanationPlanDto,
    GroundingCheckSummaryDto,
    JsonMap,
    RagUsageSummaryDto,
    RetrievalFacetDto,
)
from veterinary_agent.education_agent.contract import EducationAgent
from veterinary_agent.education_agent.enums import (
    EducationAgentErrorCode,
    EducationAgentOperation,
    EducationDraftStatus,
    EducationRetrievalPurpose,
    EducationTraceWriteStatus,
    EvidenceSufficiencyStatus,
    ExplanationDimensionCode,
)
from veterinary_agent.education_agent.errors import (
    EducationAgentError,
    EducationAgentErrorDto,
    build_education_agent_error_dto,
    is_education_agent_error_retryable_by_default,
)
from veterinary_agent.education_agent.factory import create_default_education_agent
from veterinary_agent.education_agent.node import EducationAgentGraphNode
from veterinary_agent.education_agent.ports import (
    EducationRagPort,
    TODO_EDUCATION_RAG_ERROR_CODE,
    TodoEducationRagPort,
)
from veterinary_agent.education_agent.service import (
    DefaultEducationAgent,
)
from veterinary_agent.education_agent.trace import (
    EducationTraceSink,
    LogicTraceEducationTraceSink,
    TODO_EDUCATION_TRACE_ERROR_CODE,
    TodoEducationTraceSink,
)

__all__: tuple[str, ...] = (
    "DefaultEducationAgent",
    "EducationAgent",
    "EducationAgentDto",
    "EducationAgentError",
    "EducationAgentErrorCode",
    "EducationAgentErrorDto",
    "EducationAgentGraphNode",
    "EducationAgentOperation",
    "EducationBriefDto",
    "EducationContentPlanDto",
    "EducationDraftDto",
    "EducationDraftStatus",
    "EducationGenerationRequestDto",
    "EducationRagPort",
    "EducationRagResultDto",
    "EducationRetrievalPlanDto",
    "EducationRetrievalPurpose",
    "EducationTracePatchDto",
    "EducationTraceRecordDto",
    "EducationTraceSink",
    "EducationTraceWriteResultDto",
    "EducationTraceWriteStatus",
    "EvidenceBindingDto",
    "EvidenceCardDto",
    "EvidenceHintDto",
    "EvidenceSufficiencyResultDto",
    "EvidenceSufficiencyStatus",
    "ExplanationDimensionCode",
    "ExplanationDimensionDto",
    "ExplanationPlanDto",
    "GroundingCheckSummaryDto",
    "JsonMap",
    "LogicTraceEducationTraceSink",
    "RagUsageSummaryDto",
    "RetrievalFacetDto",
    "TODO_EDUCATION_RAG_ERROR_CODE",
    "TODO_EDUCATION_TRACE_ERROR_CODE",
    "TodoEducationRagPort",
    "TodoEducationTraceSink",
    "build_education_agent_error_dto",
    "create_default_education_agent",
    "is_education_agent_error_retryable_by_default",
)
