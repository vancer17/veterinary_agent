##################################################################################################
# 文件: tests/education_agent/test_node.py
# 作用: 验证 EducationAgentGraphNode 的 state 读取、默认请求构造和缺失上下文错误。
# 边界: 只测试图节点薄适配行为，不实现真实 GraphRuntime 后继调度、checkpoint 或输出发布。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.education_agent import (
    EducationAgentError,
    EducationAgentErrorCode,
    EducationAgentGraphNode,
    create_default_education_agent,
)
from veterinary_agent.graph_runtime import GraphState

from .helpers import (
    RecordingEducationTraceSink,
    build_graph_context,
    build_provider,
    graph_state_for_education_request,
)


def test_node_builds_request_without_explicit_education_request() -> None:
    """验证图节点可在缺少显式科普请求时从 context bundle 构建请求。

    :return: None。
    """

    provider = build_provider()
    node = EducationAgentGraphNode(
        agent=create_default_education_agent(
            runtime_config_provider=provider,
            trace_sink=RecordingEducationTraceSink(),
        )
    )
    state = GraphState(graph_state_for_education_request(include_request=False))

    result = asyncio.run(node(state, build_graph_context(provider)))

    assert result.state_patch["education_generation_status"] in {
        "INSUFFICIENT_EVIDENCE",
        "RAG_DEGRADED_CONSERVATIVE",
    }
    assert result.state_patch["draft_response_ref"] == (
        "draft:trace_education_1:task_education_1"
    )


def test_node_rejects_missing_context_bundle() -> None:
    """验证图节点在缺少 context bundle 时返回科普错误。

    :return: None。
    """

    provider = build_provider()
    node = EducationAgentGraphNode(
        agent=create_default_education_agent(runtime_config_provider=provider)
    )

    with pytest.raises(EducationAgentError) as exc_info:
        asyncio.run(node(GraphState({}), build_graph_context(provider)))

    assert exc_info.value.code is EducationAgentErrorCode.EDUCATION_CONTEXT_MISSING
