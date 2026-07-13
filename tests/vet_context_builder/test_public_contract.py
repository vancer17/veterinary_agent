##################################################################################################
# 文件: tests/vet_context_builder/test_public_contract.py
# 作用: 验证 VetContextBuilder 一级包完整暴露稳定 DTO、端口、实现、映射、节点和错误契约。
# 边界: 仅检查包级公开符号，不引用内部模块、不执行来源读取或上下文构建。
##################################################################################################

import veterinary_agent.vet_context_builder as vet_context_builder


def test_vet_context_builder_package_exposes_public_contract() -> None:
    """验证 VetContextBuilder 公共能力均可从一级包导入。

    :return: None。
    """

    expected_names: tuple[str, ...] = (
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

    assert tuple(vet_context_builder.__all__) == expected_names
    for name in expected_names:
        assert hasattr(vet_context_builder, name)
