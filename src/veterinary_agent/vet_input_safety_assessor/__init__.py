##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/__init__.py
# 作用: 作为 VetInputSafetyAssessor 组件包统一出口，暴露 DTO、枚举、端口、实现、节点、trace 和错误契约。
# 边界: 外部包应从本文件导入输入安全评估能力，避免跨包直接引用组件内部实现模块。
##################################################################################################

from veterinary_agent.vet_input_safety_assessor.dto import (
    AssessmentTraceSummaryDto,
    BatchVetInputAssessmentRequestDto,
    BatchVetInputAssessmentResultDto,
    InputSafetySignalDto,
    JsonMap,
    LightweightAssessmentContextDto,
    LlmArbitrationResultDto,
    ResolvedProfileDecisionDto,
    SemanticRouteCandidateDto,
    StructuredSignalExtractionSummaryDto,
    VetInputAssessmentRequestDto,
    VetInputAssessmentResultDto,
    VetInputAssessmentTraceRecordDto,
    VetInputSafetyAssessorDto,
    VetInputSafetyTraceWriteResultDto,
    build_input_text_hash,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    AssessmentMethod,
    AssessmentStatus,
    DisambiguationMethod,
    RouteLabel,
    SafetySignalCode,
    SignalSource,
    SignalStrength,
    VetInputAssessmentTraceWriteStatus,
    VetInputSafetyAssessorErrorCode,
    VetInputSafetyAssessorOperation,
    VetIntent,
)
from veterinary_agent.vet_input_safety_assessor.errors import (
    VetInputSafetyAssessorError,
    VetInputSafetyAssessorErrorDto,
    build_vet_input_safety_assessor_error_dto,
    is_vet_input_safety_assessor_error_retryable_by_default,
)
from veterinary_agent.vet_input_safety_assessor.matchers import (
    KeywordLexicalSignalMatcher,
    KeywordSemanticRouteClassifier,
    KeywordSignalRule,
)
from veterinary_agent.vet_input_safety_assessor.node import (
    VetInputSafetyAssessorGraphNode,
)
from veterinary_agent.vet_input_safety_assessor.ports import (
    LexicalSignalMatcher,
    SemanticRouteClassifier,
    StructuredSignalExtractor,
    TODO_LOCAL_EXTRACTOR_VERSION,
    TODO_SEMANTIC_ROUTER_VERSION,
    TodoStructuredSignalExtractor,
)
from veterinary_agent.vet_input_safety_assessor.resolver import (
    VetProfileDecisionResolver,
)
from veterinary_agent.vet_input_safety_assessor.service import (
    DefaultVetInputSafetyAssessor,
    VetInputSafetyAssessor,
    create_default_vet_input_safety_assessor,
)
from veterinary_agent.vet_input_safety_assessor.trace import (
    LogicTraceVetInputSafetyTraceSink,
    TODO_INPUT_SAFETY_TRACE_ERROR_CODE,
    TodoVetInputSafetyTraceSink,
    VetInputSafetyTraceSink,
)

__all__: tuple[str, ...] = (
    "AssessmentMethod",
    "AssessmentStatus",
    "AssessmentTraceSummaryDto",
    "BatchVetInputAssessmentRequestDto",
    "BatchVetInputAssessmentResultDto",
    "DefaultVetInputSafetyAssessor",
    "DisambiguationMethod",
    "InputSafetySignalDto",
    "JsonMap",
    "KeywordLexicalSignalMatcher",
    "KeywordSemanticRouteClassifier",
    "KeywordSignalRule",
    "LexicalSignalMatcher",
    "LightweightAssessmentContextDto",
    "LlmArbitrationResultDto",
    "LogicTraceVetInputSafetyTraceSink",
    "ResolvedProfileDecisionDto",
    "RouteLabel",
    "SafetySignalCode",
    "SemanticRouteCandidateDto",
    "SemanticRouteClassifier",
    "SignalSource",
    "SignalStrength",
    "StructuredSignalExtractionSummaryDto",
    "StructuredSignalExtractor",
    "TODO_INPUT_SAFETY_TRACE_ERROR_CODE",
    "TODO_LOCAL_EXTRACTOR_VERSION",
    "TODO_SEMANTIC_ROUTER_VERSION",
    "TodoStructuredSignalExtractor",
    "TodoVetInputSafetyTraceSink",
    "VetInputAssessmentRequestDto",
    "VetInputAssessmentResultDto",
    "VetInputAssessmentTraceRecordDto",
    "VetInputAssessmentTraceWriteStatus",
    "VetInputSafetyAssessor",
    "VetInputSafetyAssessorDto",
    "VetInputSafetyAssessorError",
    "VetInputSafetyAssessorErrorCode",
    "VetInputSafetyAssessorErrorDto",
    "VetInputSafetyAssessorGraphNode",
    "VetInputSafetyAssessorOperation",
    "VetInputSafetyTraceSink",
    "VetInputSafetyTraceWriteResultDto",
    "VetIntent",
    "VetProfileDecisionResolver",
    "build_input_text_hash",
    "build_vet_input_safety_assessor_error_dto",
    "create_default_vet_input_safety_assessor",
    "is_vet_input_safety_assessor_error_retryable_by_default",
)
