##################################################################################################
# 文件: tests/safety_trigger_agent/test_component.py
# 作用: 验证 SafetyTriggerAgent 的保守兜底、writer 路径、自检兜底、profile 校验和 Graph 节点适配。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、ToolRegistry、输出护栏或发布链路。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.graph_runtime import GraphState
from veterinary_agent.safety_trigger_agent import (
    SafetyTraceWriteStatus,
    SafetyTriggerAgentGraphNode,
    SafetyTriggerDraftStatus,
    SafetyTriggerError,
    SafetyTriggerErrorCode,
    create_default_safety_trigger_agent,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetGenerationProfile,
)

from .helpers import (
    AllowingSafetyToolPermissionPort,
    DenyingSafetyToolPermissionPort,
    FakeSafetyAgentRunner,
    RecordingSafetyTraceSink,
    build_assessment_summary,
    build_context_bundle,
    build_context_with_compression_strategy,
    build_context_with_generation_profile,
    build_graph_context,
    build_provider,
    build_request,
    build_writer_outputs,
    graph_state_for_safety_request,
)


def test_fallback_draft_when_domain_dependencies_are_todo() -> None:
    """验证工具权限与 AgentRunner 未接入时返回保守急症兜底草稿。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingSafetyTraceSink()
    agent = create_default_safety_trigger_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is SafetyTriggerDraftStatus.FALLBACK_READY
    assert result.rag_invoked is False
    assert result.retrieval_ids == []
    assert "宠物医院" in result.draft_response
    assert "布洛芬" in result.draft_response
    assert result.trace_patch.template_fallback_used is True
    assert result.trace_delivery_status is SafetyTraceWriteStatus.RECORDED
    assert len(trace_sink.records) == 1


def test_writer_output_can_produce_draft_ready() -> None:
    """验证权限证明和 writer 可用时返回普通急症草稿。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeSafetyAgentRunner(outputs=build_writer_outputs())
    trace_sink = RecordingSafetyTraceSink()
    permission_port = AllowingSafetyToolPermissionPort()
    agent = create_default_safety_trigger_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        tool_permission_port=permission_port,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is SafetyTriggerDraftStatus.DRAFT_READY
    assert result.trace_patch.template_fallback_used is False
    assert result.self_check.fallback_recommended is False
    assert result.rag_invoked is False
    assert result.retrieval_ids == []
    assert len(agent_runner.requests) == 2
    assert len(permission_port.requests) == 1


def test_writer_missing_vet_direction_falls_back() -> None:
    """验证 writer 草稿缺少就医导向时切换急症兜底。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(
        runtime_config_provider=provider,
        agent_runner=FakeSafetyAgentRunner(outputs=build_writer_outputs(safe=False)),
        tool_permission_port=AllowingSafetyToolPermissionPort(),
        trace_sink=RecordingSafetyTraceSink(),
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is SafetyTriggerDraftStatus.FALLBACK_READY
    assert result.trace_patch.template_fallback_used is True
    assert "template_fallback_used" in result.trace_patch.degraded_flags


def test_rag_permission_unverified_blocks_writer_and_falls_back() -> None:
    """验证 RAG 禁用证明未完成时不调用 writer 并返回兜底草稿。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeSafetyAgentRunner(outputs=build_writer_outputs())
    permission_port = DenyingSafetyToolPermissionPort()
    agent = create_default_safety_trigger_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        tool_permission_port=permission_port,
        trace_sink=RecordingSafetyTraceSink(),
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is SafetyTriggerDraftStatus.FALLBACK_READY
    assert result.trace_patch.template_fallback_used is True
    assert "test_rag_permission_unverified" in result.trace_patch.degraded_flags
    assert "writer_blocked_by_rag_permission" in result.trace_patch.degraded_flags
    assert agent_runner.requests == []
    assert len(permission_port.requests) == 1


def test_profile_mismatch_is_rejected() -> None:
    """验证非 safety_trigger 剖面会被稳定错误码拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, generation_profile="standard"),
            )
        )

    assert exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH


def test_context_generation_profile_mismatch_is_rejected() -> None:
    """验证上下文剖面不是 safety_trigger 时被稳定错误码拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(
                    provider,
                    context=build_context_with_generation_profile(
                        VetGenerationProfile.STANDARD
                    ),
                )
            )
        )

    assert exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_PROFILE_MISMATCH


def test_pet_context_mismatch_is_rejected() -> None:
    """验证请求宠物与上下文宠物不一致时被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(
                    provider,
                    current_pet_id="pet_1",
                    context=build_context_bundle(current_pet_id="pet_2"),
                )
            )
        )

    assert (
        exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_PET_CONTEXT_INVALID
    )


def test_non_safety_minimal_context_is_rejected() -> None:
    """验证非 safety_minimal 压缩上下文会被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(
                    provider,
                    context=build_context_with_compression_strategy(
                        ContextCompressionStrategy.SINGLE_FULL
                    ),
                )
            )
        )

    assert exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING


def test_missing_safety_signal_summary_is_rejected() -> None:
    """验证缺少急症或 SAF 信号摘要时被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, assessment_summary={}),
            )
        )

    assert exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_SIGNAL_MISSING


def test_inbound_rag_summary_is_rejected() -> None:
    """验证急症请求携带 RAG 证据时被拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(runtime_config_provider=provider)

    with pytest.raises(SafetyTriggerError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(
                    provider,
                    assessment_summary=build_assessment_summary(include_rag=True),
                )
            )
        )

    assert exc_info.value.code is SafetyTriggerErrorCode.SAFETY_TRIGGER_RAG_FORBIDDEN


def test_graph_node_writes_safety_state_patch() -> None:
    """验证图节点从 state 构建请求并写回急症状态。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_safety_trigger_agent(
        runtime_config_provider=provider,
        trace_sink=RecordingSafetyTraceSink(),
    )
    node = SafetyTriggerAgentGraphNode(agent=agent)
    state: GraphState = graph_state_for_safety_request()

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["safety_trigger_generation_status"] == (
        SafetyTriggerDraftStatus.FALLBACK_READY.value
    )
    assert result.state_patch["safety_trigger_requires_first_segment"] is True
    assert result.state_patch["safety_trigger_rag_invoked"] is False
    assert result.state_patch["safety_trigger_retrieval_ids"] == []
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_safety_1:task_safety_1"
    )
