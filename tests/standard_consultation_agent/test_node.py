##################################################################################################
# 文件: tests/standard_consultation_agent/test_node.py
# 作用: 验证 StandardConsultationAgentGraphNode 的 state 读取、默认请求构造和缺失上下文错误。
# 边界: 只测试图节点薄适配行为，不实现真实 GraphRuntime 后继调度、checkpoint 或输出发布。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.graph_runtime import GraphState
from veterinary_agent.standard_consultation_agent import (
    StandardConsultationAgentGraphNode,
    StandardConsultationError,
    StandardConsultationErrorCode,
    create_default_standard_consultation_agent,
)

from .helpers import (
    RecordingStandardTraceSink,
    build_graph_context,
    build_provider,
    graph_state_for_standard_request,
)


def test_node_builds_request_without_explicit_standard_request() -> None:
    """验证图节点可在缺少显式标准请求时从 context bundle 构建请求。

    :return: None。
    """

    provider = build_provider()
    node = StandardConsultationAgentGraphNode(
        agent=create_default_standard_consultation_agent(
            runtime_config_provider=provider,
            trace_sink=RecordingStandardTraceSink(),
        )
    )
    state = GraphState(graph_state_for_standard_request(include_request=False))

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["standard_generation_status"] in {
        "NEEDS_MORE_INFO",
        "RAG_DEGRADED_CONSERVATIVE",
    }
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_standard_1:task_standard_1"
    )


def test_node_rejects_missing_context_bundle() -> None:
    """验证图节点在缺少 context bundle 时返回标准问诊错误。

    :return: None。
    """

    provider = build_provider()
    node = StandardConsultationAgentGraphNode(
        agent=create_default_standard_consultation_agent(
            runtime_config_provider=provider,
        )
    )

    with pytest.raises(StandardConsultationError) as exc_info:
        asyncio.run(node(GraphState({}), build_graph_context(provider)))

    assert exc_info.value.code is StandardConsultationErrorCode.STANDARD_CONTEXT_MISSING
