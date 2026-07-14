##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/__init__.py
# 作用: 作为 VetResponseComposer 一级包统一出口，集中暴露 DTO、枚举、错误、服务、节点与 trace 端口。
# 边界: 其他包必须从本文件导入回复合成发布能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.vet_response_composer.dto import (
    BranchExecutionStateDto,
    ComposerTracePatchDto,
    ComposerTraceRecordDto,
    ComposerTraceWriteResultDto,
    ComposeTurnRequestDto,
    ComposeTurnResultDto,
    JsonMap,
    PublishDecisionDto,
    PublishableSegmentDto,
    ResponseSegmentDto,
    TurnCompositionStateDto,
    VetResponseComposerDto,
)
from veterinary_agent.vet_response_composer.enums import (
    ComposerBranchType,
    ComposerGuardStatus,
    ComposerPublishDecision,
    ComposerPublishStatus,
    ComposerSegmentType,
    ComposerTraceWriteStatus,
    VetResponseComposerErrorCode,
    VetResponseComposerOperation,
)
from veterinary_agent.vet_response_composer.errors import (
    VetResponseComposerError,
    VetResponseComposerErrorDto,
    build_vet_response_composer_error_dto,
    is_vet_response_composer_error_retryable_by_default,
)
from veterinary_agent.vet_response_composer.node import VetResponseComposerGraphNode
from veterinary_agent.vet_response_composer.service import (
    DefaultVetResponseComposer,
    VetResponseComposer,
    create_default_vet_response_composer,
)
from veterinary_agent.vet_response_composer.trace import (
    LogicTraceVetResponseComposerTraceSink,
    TODO_COMPOSER_TRACE_ERROR_CODE,
    TodoVetResponseComposerTraceSink,
    VetResponseComposerTraceSink,
)

__all__: tuple[str, ...] = (
    "BranchExecutionStateDto",
    "ComposerBranchType",
    "ComposerGuardStatus",
    "ComposerPublishDecision",
    "ComposerPublishStatus",
    "ComposerSegmentType",
    "ComposerTracePatchDto",
    "ComposerTraceRecordDto",
    "ComposerTraceWriteResultDto",
    "ComposerTraceWriteStatus",
    "ComposeTurnRequestDto",
    "ComposeTurnResultDto",
    "DefaultVetResponseComposer",
    "JsonMap",
    "LogicTraceVetResponseComposerTraceSink",
    "PublishDecisionDto",
    "PublishableSegmentDto",
    "ResponseSegmentDto",
    "TODO_COMPOSER_TRACE_ERROR_CODE",
    "TodoVetResponseComposerTraceSink",
    "TurnCompositionStateDto",
    "VetResponseComposer",
    "VetResponseComposerDto",
    "VetResponseComposerError",
    "VetResponseComposerErrorCode",
    "VetResponseComposerErrorDto",
    "VetResponseComposerGraphNode",
    "VetResponseComposerOperation",
    "VetResponseComposerTraceSink",
    "build_vet_response_composer_error_dto",
    "create_default_vet_response_composer",
    "is_vet_response_composer_error_retryable_by_default",
)
