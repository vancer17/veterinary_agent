##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/__init__.py
# 作用: 作为 VetTaskDecomposer 一级包统一出口，集中暴露 DTO、枚举、错误、fallback、trace、服务与节点契约。
# 边界: 其他包必须从本文件导入 VetTaskDecomposer 能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.vet_task_decomposer.dto import (
    AttachmentBindingDto,
    AttachmentRefDto,
    DecompositionTraceSummaryDto,
    JsonMap,
    LocalFallbackResultDto,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskDecomposeRequestDto,
    VetTaskDecomposeResultDto,
    VetTaskDecomposeTraceRecordDto,
    VetTaskDecomposerDto,
    VetTaskTraceWriteResultDto,
    build_text_hash,
)
from veterinary_agent.vet_task_decomposer.enums import (
    AttachmentRole,
    DecompositionMethod,
    DecompositionStatus,
    TaskPriorityHint,
    VetTaskDecomposerErrorCode,
    VetTaskDecomposerOperation,
    VetTaskTraceWriteStatus,
    VetTaskType,
)
from veterinary_agent.vet_task_decomposer.errors import (
    VetTaskDecomposerError,
    VetTaskDecomposerErrorDto,
    build_vet_task_decomposer_error_dto,
    is_vet_task_decomposer_error_retryable_by_default,
)
from veterinary_agent.vet_task_decomposer.fallback import (
    TODO_LOCAL_FALLBACK_ERROR_CODE,
    TodoVetTaskLocalFallback,
    VetTaskLocalFallback,
)
from veterinary_agent.vet_task_decomposer.node import VetTaskDecomposerGraphNode
from veterinary_agent.vet_task_decomposer.service import (
    DefaultVetTaskDecomposer,
    VetTaskDecomposer,
    create_default_vet_task_decomposer,
)
from veterinary_agent.vet_task_decomposer.trace import (
    LogicTraceVetTaskDecomposerTraceSink,
    TODO_TASK_DECOMPOSER_TRACE_ERROR_CODE,
    TodoVetTaskDecomposerTraceSink,
    VetTaskDecomposerTraceSink,
)

__all__: tuple[str, ...] = (
    "AttachmentBindingDto",
    "AttachmentRefDto",
    "AttachmentRole",
    "DecompositionMethod",
    "DecompositionStatus",
    "DecompositionTraceSummaryDto",
    "DefaultVetTaskDecomposer",
    "JsonMap",
    "LocalFallbackResultDto",
    "LogicTraceVetTaskDecomposerTraceSink",
    "TODO_LOCAL_FALLBACK_ERROR_CODE",
    "TODO_TASK_DECOMPOSER_TRACE_ERROR_CODE",
    "TaskPriorityHint",
    "TextSpanDto",
    "TodoVetTaskDecomposerTraceSink",
    "TodoVetTaskLocalFallback",
    "VetSubTaskDto",
    "VetTaskDecomposeRequestDto",
    "VetTaskDecomposeResultDto",
    "VetTaskDecomposeTraceRecordDto",
    "VetTaskDecomposer",
    "VetTaskDecomposerDto",
    "VetTaskDecomposerError",
    "VetTaskDecomposerErrorCode",
    "VetTaskDecomposerErrorDto",
    "VetTaskDecomposerGraphNode",
    "VetTaskDecomposerOperation",
    "VetTaskDecomposerTraceSink",
    "VetTaskLocalFallback",
    "VetTaskTraceWriteResultDto",
    "VetTaskTraceWriteStatus",
    "VetTaskType",
    "build_text_hash",
    "build_vet_task_decomposer_error_dto",
    "create_default_vet_task_decomposer",
    "is_vet_task_decomposer_error_retryable_by_default",
)
