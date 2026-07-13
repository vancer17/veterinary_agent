##################################################################################################
# 文件: tests/standard_consultation_agent/test_component.py
# 作用: 验证 StandardConsultationAgent 的保守降级、profile 校验和 GraphRuntime 节点适配主契约。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、RAG、MedicationPolicy 或 Trace 存储。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.graph_runtime import GraphState
from veterinary_agent.standard_consultation_agent import (
    DraftStatus,
    StandardConsultationAgentGraphNode,
    StandardConsultationError,
    StandardConsultationErrorCode,
    StandardTraceWriteStatus,
    create_default_standard_consultation_agent,
)

from .helpers import (
    RecordingStandardTraceSink,
    build_context_bundle,
    build_graph_context,
    build_provider,
    build_request,
    graph_state_for_standard_request,
)


def test_conservative_draft_when_domain_dependencies_are_todo() -> None:
    """验证 RAG 与 AgentRunner 未接入时返回保守结构化草稿。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingStandardTraceSink()
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is DraftStatus.RAG_DEGRADED_CONSERVATIVE
    assert 1 <= len(result.selected_questions) <= 3
    assert result.trace_delivery_status is StandardTraceWriteStatus.RECORDED
    assert {
        "question_collector_unavailable",
        "triage_unavailable",
        "synthesizer_unavailable",
        "rag_degraded",
    }.issubset(set(result.trace_patch.degraded_flags))
    assert len(trace_sink.records) == 1


def test_profile_mismatch_is_rejected() -> None:
    """验证非 standard 剖面会被稳定错误码拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
    )

    with pytest.raises(StandardConsultationError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, generation_profile="education"),
            )
        )

    assert (
        exc_info.value.code is StandardConsultationErrorCode.STANDARD_PROFILE_MISMATCH
    )


def test_graph_node_writes_standard_state_patch() -> None:
    """验证图节点从 state 构建请求并写回标准问诊状态。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingStandardTraceSink()
    agent = create_default_standard_consultation_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    node = StandardConsultationAgentGraphNode(agent=agent)
    context_bundle = build_context_bundle()
    state: GraphState = graph_state_for_standard_request(context=context_bundle)

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["standard_generation_status"] == (
        DraftStatus.RAG_DEGRADED_CONSERVATIVE.value
    )
    assert result.state_patch["standard_reached_layer"] == "L1_TRIAGE"
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_standard_1:task_standard_1"
    )
    selected_questions = result.state_patch["standard_selected_questions"]
    assert isinstance(selected_questions, list)
    assert len(selected_questions) <= 3
