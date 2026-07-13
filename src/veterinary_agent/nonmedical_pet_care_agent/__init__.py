##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/__init__.py
# 作用: 作为 NonmedicalPetCareAgent 一级包统一出口，集中暴露 DTO、枚举、错误、端口、服务与节点。
# 边界: 其他包必须从本文件导入非医疗组件能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.nonmedical_pet_care_agent.contract import NonmedicalPetCareAgent
from veterinary_agent.nonmedical_pet_care_agent.dto import (
    AdviceConstraintDto,
    AdviceDimensionDto,
    AdvicePlanDto,
    EvidenceCardDto,
    EvidenceHintDto,
    InputSafetySignalDto,
    JsonMap,
    KnowledgeRetrievalPlanDto,
    NonmedicalAdviceDraftDto,
    NonmedicalAdviceRequestDto,
    NonmedicalPetCareAgentDto,
    NonmedicalRagResultDto,
    NonmedicalTracePatchDto,
    NonmedicalTraceRecordDto,
    NonmedicalTraceWriteResultDto,
    PersonalizationFactorDto,
    PersonalizationPlanDto,
    PetCareBriefDto,
    RagUsageSummaryDto,
    RetrievalFacetDto,
    SafetySelfCheckSummaryDto,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import (
    AdviceDimensionCode,
    CareDomain,
    NonmedicalAgentErrorCode,
    NonmedicalAgentOperation,
    NonmedicalDraftStatus,
    NonmedicalRetrievalPurpose,
    NonmedicalTraceWriteStatus,
    PersonalizationLevel,
)
from veterinary_agent.nonmedical_pet_care_agent.errors import (
    NonmedicalAgentError,
    NonmedicalAgentErrorDto,
    build_nonmedical_agent_error_dto,
    is_nonmedical_agent_error_retryable_by_default,
)
from veterinary_agent.nonmedical_pet_care_agent.factory import (
    create_default_nonmedical_pet_care_agent,
)
from veterinary_agent.nonmedical_pet_care_agent.node import (
    NonmedicalPetCareAgentGraphNode,
)
from veterinary_agent.nonmedical_pet_care_agent.ports import (
    NonmedicalPetCareRagPort,
    TODO_NONMEDICAL_RAG_ERROR_CODE,
    TodoNonmedicalPetCareRagPort,
)
from veterinary_agent.nonmedical_pet_care_agent.service import (
    DefaultNonmedicalPetCareAgent,
)
from veterinary_agent.nonmedical_pet_care_agent.trace import (
    LogicTraceNonmedicalPetCareTraceSink,
    NonmedicalPetCareTraceSink,
    TODO_NONMEDICAL_TRACE_ERROR_CODE,
    TodoNonmedicalPetCareTraceSink,
)

__all__: tuple[str, ...] = (
    "AdviceConstraintDto",
    "AdviceDimensionCode",
    "AdviceDimensionDto",
    "AdvicePlanDto",
    "CareDomain",
    "DefaultNonmedicalPetCareAgent",
    "EvidenceCardDto",
    "EvidenceHintDto",
    "InputSafetySignalDto",
    "JsonMap",
    "KnowledgeRetrievalPlanDto",
    "LogicTraceNonmedicalPetCareTraceSink",
    "NonmedicalAdviceDraftDto",
    "NonmedicalAdviceRequestDto",
    "NonmedicalAgentError",
    "NonmedicalAgentErrorCode",
    "NonmedicalAgentErrorDto",
    "NonmedicalAgentOperation",
    "NonmedicalDraftStatus",
    "NonmedicalPetCareAgent",
    "NonmedicalPetCareAgentDto",
    "NonmedicalPetCareAgentGraphNode",
    "NonmedicalPetCareRagPort",
    "NonmedicalPetCareTraceSink",
    "NonmedicalRagResultDto",
    "NonmedicalRetrievalPurpose",
    "NonmedicalTracePatchDto",
    "NonmedicalTraceRecordDto",
    "NonmedicalTraceWriteResultDto",
    "NonmedicalTraceWriteStatus",
    "PersonalizationFactorDto",
    "PersonalizationLevel",
    "PersonalizationPlanDto",
    "PetCareBriefDto",
    "RagUsageSummaryDto",
    "RetrievalFacetDto",
    "SafetySelfCheckSummaryDto",
    "TODO_NONMEDICAL_RAG_ERROR_CODE",
    "TODO_NONMEDICAL_TRACE_ERROR_CODE",
    "TodoNonmedicalPetCareRagPort",
    "TodoNonmedicalPetCareTraceSink",
    "build_nonmedical_agent_error_dto",
    "create_default_nonmedical_pet_care_agent",
    "is_nonmedical_agent_error_retryable_by_default",
)
