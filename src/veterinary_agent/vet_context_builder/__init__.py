##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/__init__.py
# 作用: 作为 VetContextBuilder 一级包统一出口，集中暴露 DTO、端口、默认实现、节点与错误契约。
# 边界: 其他包必须从本文件导入 VetContextBuilder 能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.vet_context_builder.blocks import (
    BlockCompilationResult,
    VetPromptBlockCompiler,
)
from veterinary_agent.vet_context_builder.compression import (
    ContextBudgetManager,
    ContextCompressionResult,
)
from veterinary_agent.vet_context_builder.dto import (
    CompressionAuditDto,
    ContextFactDto,
    ContextMessageDto,
    ContextSourceLoadRequestDto,
    ContextSourceReadResultDto,
    ContextSourceRefDto,
    ContextSummaryDto,
    ContextTraceRecordDto,
    ContextTraceWriteResultDto,
    JsonMap,
    ResolvedContextFactDto,
    SessionContextStateDto,
    SlotCoverageDto,
    VetContextBuildRequestDto,
    VetContextBuilderDto,
    VetContextBundleDto,
    VetPromptBlockDto,
)
from veterinary_agent.vet_context_builder.enums import (
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextFactState,
    ContextSourceFreshness,
    ContextSourceStatus,
    ContextSourceType,
    ContextTraceWriteStatus,
    VetAuditTier,
    VetContextBuilderErrorCode,
    VetContextBuilderOperation,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockPriority,
    VetPromptBlockType,
)
from veterinary_agent.vet_context_builder.errors import (
    VetContextBuilderError,
    VetContextBuilderErrorDto,
    build_vet_context_builder_error_dto,
    is_vet_context_builder_error_retryable_by_default,
)
from veterinary_agent.vet_context_builder.facts import (
    evaluate_slot_coverage,
    facts_from_session_state,
    required_slots_for_task,
    resolve_context_facts,
)
from veterinary_agent.vet_context_builder.mapping import (
    to_agent_prompt_block,
    to_agent_prompt_blocks,
)
from veterinary_agent.vet_context_builder.node import VetContextBuilderGraphNode
from veterinary_agent.vet_context_builder.ports import (
    CheckpointStoreContextSourcePort,
    ContextSourcePort,
    ConversationStoreContextSourcePort,
    TodoContextSourcePort,
    build_default_context_source_ports,
    build_todo_context_source_ports,
)
from veterinary_agent.vet_context_builder.service import (
    DefaultVetContextBuilder,
    VetContextBuilder,
    create_default_vet_context_builder,
)
from veterinary_agent.vet_context_builder.trace import (
    TODO_CONTEXT_TRACE_ERROR_CODE,
    LogicTraceVetContextTraceSink,
    TodoVetContextTraceSink,
    VetContextTraceSink,
)

__all__: tuple[str, ...] = (
    "BlockCompilationResult",
    "CheckpointStoreContextSourcePort",
    "CompressionAuditDto",
    "ContextBudgetManager",
    "ContextBuildStatus",
    "ContextCompressionResult",
    "ContextCompressionStrategy",
    "ContextFactDto",
    "ContextFactState",
    "ContextMessageDto",
    "ContextSourceFreshness",
    "ContextSourceLoadRequestDto",
    "ContextSourcePort",
    "ContextSourceReadResultDto",
    "ContextSourceRefDto",
    "ContextSourceStatus",
    "ContextSourceType",
    "ContextSummaryDto",
    "ContextTraceRecordDto",
    "ContextTraceWriteResultDto",
    "ContextTraceWriteStatus",
    "ConversationStoreContextSourcePort",
    "DefaultVetContextBuilder",
    "JsonMap",
    "LogicTraceVetContextTraceSink",
    "ResolvedContextFactDto",
    "SessionContextStateDto",
    "SlotCoverageDto",
    "TODO_CONTEXT_TRACE_ERROR_CODE",
    "TodoContextSourcePort",
    "TodoVetContextTraceSink",
    "VetAuditTier",
    "VetContextBuildRequestDto",
    "VetContextBuilder",
    "VetContextBuilderDto",
    "VetContextBuilderError",
    "VetContextBuilderErrorCode",
    "VetContextBuilderErrorDto",
    "VetContextBuilderGraphNode",
    "VetContextBuilderOperation",
    "VetContextBundleDto",
    "VetExecutorKey",
    "VetGenerationProfile",
    "VetPromptBlockCompiler",
    "VetPromptBlockDto",
    "VetPromptBlockPriority",
    "VetPromptBlockType",
    "VetContextTraceSink",
    "build_default_context_source_ports",
    "build_todo_context_source_ports",
    "build_vet_context_builder_error_dto",
    "create_default_vet_context_builder",
    "evaluate_slot_coverage",
    "facts_from_session_state",
    "is_vet_context_builder_error_retryable_by_default",
    "required_slots_for_task",
    "resolve_context_facts",
    "to_agent_prompt_block",
    "to_agent_prompt_blocks",
)
