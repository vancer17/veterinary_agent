##################################################################################################
# 文件: tests/vet_context_builder/test_mapping_and_node.py
# 作用: 验证领域 prompt 块到 AgentRunner 的映射，以及 GraphRuntime 节点身份字段覆盖与状态输出。
# 边界: 使用进程内假 Builder，不执行真实来源读取、模型调用、checkpoint 或 trace 持久化。
##################################################################################################

import asyncio

from veterinary_agent.graph_runtime import GraphNodeExecutionContext, GraphState
from veterinary_agent.vet_context_builder import (
    VetContextBuildRequestDto,
    VetContextBuilderError,
    VetContextBuilderGraphNode,
    VetContextBundleDto,
    to_agent_prompt_blocks,
)
from tests.vet_context_builder.helpers import build_minimal_bundle


class CapturingVetContextBuilder:
    """记录图节点构建请求并返回固定 bundle 的测试 Builder。"""

    def __init__(self, *, bundle: VetContextBundleDto) -> None:
        """初始化测试 Builder。

        :param bundle: 每次构建固定返回的上下文 bundle。
        :return: None。
        """

        self.bundle = bundle
        self.requests: list[VetContextBuildRequestDto] = []

    def is_ready(self) -> bool:
        """返回测试 Builder 就绪状态。

        :return: 固定返回 True。
        """

        return True

    async def build(
        self,
        request: VetContextBuildRequestDto,
    ) -> VetContextBundleDto:
        """记录构建请求并返回固定 bundle。

        :param request: 图节点构建出的严格请求 DTO。
        :return: 初始化时传入的固定上下文 bundle。
        """

        self.requests.append(request)
        return self.bundle


def _execution_context() -> GraphNodeExecutionContext:
    """构建图节点测试运行上下文。

    :return: 包含可信身份和配置版本的图节点执行上下文。
    """

    return GraphNodeExecutionContext(
        request_id="req_context_1",
        trace_id="trace_context_1",
        run_id="run_context_1",
        graph_id="vet_conversation_graph",
        graph_version="v2-langgraph",
        node_id="context_builder",
        session_id="session_context_1",
        user_id="user_context_1",
        current_pet_id="pet_context_1",
        params_version="params.v1",
        config_snapshot_id="snapshot.context.1",
    )


def test_mapping_preserves_domain_audit_metadata() -> None:
    """验证 AgentRunner 通用块保留领域块 hash、优先级和来源引用摘要。

    :return: None。
    """

    bundle = build_minimal_bundle()

    mapped = to_agent_prompt_blocks(bundle)

    assert mapped[0].block_id == bundle.prompt_blocks[0].block_id
    assert mapped[0].block_type == "task_input"
    assert mapped[0].metadata["required"] is True
    assert mapped[0].metadata["content_hash"] == bundle.prompt_blocks[0].content_hash
    assert mapped[0].metadata["source_refs"]


def test_graph_node_uses_execution_context_as_identity_authority() -> None:
    """验证图节点使用执行上下文覆盖 state 中不可信的身份字段。

    :return: None。
    """

    bundle = build_minimal_bundle()
    builder = CapturingVetContextBuilder(bundle=bundle)
    node = VetContextBuilderGraphNode(builder=builder)
    state: GraphState = {
        "context_build_request": {
            "request_id": "untrusted_request",
            "trace_id": "untrusted_trace",
            "run_id": "untrusted_run",
            "session_id": "untrusted_session",
            "user_id": "untrusted_user",
            "current_pet_id": "untrusted_pet",
            "task_id": "task_context_1",
            "task_type": "EDUCATION_QA",
            "normalized_query": "犬呕吐有哪些常见原因？",
            "generation_profile": "education",
            "route": "normal",
            "executor_key": "education",
            "compression_strategy": "education_light",
            "audit_tier": "B",
            "assessment_summary": {},
            "observed_facts": [],
            "session_state_snapshot": None,
            "params_version": "untrusted_params",
            "config_snapshot_id": "untrusted_snapshot",
        }
    }

    result = asyncio.run(node(state, _execution_context()))

    captured = builder.requests[0]
    assert captured.request_id == "req_context_1"
    assert captured.current_pet_id == "pet_context_1"
    assert captured.config_snapshot_id == "snapshot.context.1"
    assert result.state_patch["adapter_invoked"] is True
    assert result.state_patch["context_bundle"]
    assert result.state_patch["prompt_blocks"]


def test_graph_node_rejects_missing_context_build_request() -> None:
    """验证图状态缺少构建请求时返回稳定组件错误。

    :return: None。
    """

    builder = CapturingVetContextBuilder(bundle=build_minimal_bundle())
    node = VetContextBuilderGraphNode(builder=builder)

    try:
        asyncio.run(node({}, _execution_context()))
    except VetContextBuilderError as exc:
        assert exc.code.value == "CONTEXT_INVALID_REQUEST"
    else:
        raise AssertionError("缺少 context_build_request 时应拒绝执行")

    assert builder.requests == []
