##################################################################################################
# 文件: tests/education_agent/test_component.py
# 作用: 验证 EducationAgent 的保守降级、profile 校验、fake 依赖完整路径和 GraphRuntime 节点适配。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、RAG、Trace 存储或输出安全审查。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.education_agent import (
    EducationAgentError,
    EducationAgentErrorCode,
    EducationAgentGraphNode,
    EducationDraftStatus,
    EducationTraceWriteStatus,
    create_default_education_agent,
)
from veterinary_agent.graph_runtime import GraphState

from .helpers import (
    FakeAgentRunner,
    FakeEducationRagPort,
    RecordingEducationTraceSink,
    build_graph_context,
    build_provider,
    build_request,
    build_success_agent_outputs,
    graph_state_for_education_request,
)


def test_conservative_draft_when_domain_dependencies_are_todo() -> None:
    """验证 RAG 与 AgentRunner 未接入时返回保守结构化草稿。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingEducationTraceSink()
    agent = create_default_education_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is EducationDraftStatus.INSUFFICIENT_EVIDENCE
    assert result.trace_delivery_status is EducationTraceWriteStatus.RECORDED
    assert {
        "explanation_planner_unavailable",
        "retrieval_planner_unavailable",
        "rag_degraded",
        "insufficient_evidence",
    }.issubset(set(result.trace_patch.degraded_flags))
    assert len(trace_sink.records) == 1


def test_profile_mismatch_is_rejected() -> None:
    """验证非 education 剖面会被稳定错误码拒绝。

    :return: None。
    """

    provider = build_provider()
    agent = create_default_education_agent(runtime_config_provider=provider)

    with pytest.raises(EducationAgentError) as exc_info:
        asyncio.run(
            agent.generate_draft(
                build_request(provider, generation_profile="standard"),
            )
        )

    assert exc_info.value.code is EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH


def test_full_education_path_with_fake_dependencies() -> None:
    """验证 fake 依赖齐备时可生成证据绑定的 DRAFT_READY 草稿。

    :return: None。
    """

    provider = build_provider()
    agent_runner = FakeAgentRunner(outputs=build_success_agent_outputs())
    rag_port = FakeEducationRagPort()
    trace_sink = RecordingEducationTraceSink()
    agent = create_default_education_agent(
        runtime_config_provider=provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        trace_sink=trace_sink,
    )

    result = asyncio.run(agent.generate_draft(build_request(provider)))

    assert result.status is EducationDraftStatus.DRAFT_READY
    assert result.rag_summary.rag_invoked is True
    assert result.evidence_bindings[0].claim_id == "claim_1"
    assert result.trace_delivery_status is EducationTraceWriteStatus.RECORDED
    assert [request.runtime_options["stage"] for request in agent_runner.requests] == [
        "explanation_planner",
        "rag_query_planner",
        "education_writer",
        "grounding_checker",
    ]
    assert len(rag_port.requests) == 1
    assert len(trace_sink.records) == 1


def test_graph_node_writes_education_state_patch() -> None:
    """验证图节点从 state 构建请求并写回科普状态。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingEducationTraceSink()
    agent = create_default_education_agent(
        runtime_config_provider=provider,
        trace_sink=trace_sink,
    )
    node = EducationAgentGraphNode(agent=agent)
    state: GraphState = GraphState(graph_state_for_education_request())

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["education_generation_status"] == (
        EducationDraftStatus.INSUFFICIENT_EVIDENCE.value
    )
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_education_1:task_education_1"
    )
    assert result.state_patch["education_rag_invoked"] is True
