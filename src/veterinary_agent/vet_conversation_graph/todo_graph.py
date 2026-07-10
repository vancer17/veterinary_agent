##################################################################################################
# 文件: src/veterinary_agent/vet_conversation_graph/todo_graph.py
# 作用: 提供兽医会话业务图的 TODO 降级 LangGraph 注册定义。
# 边界: 不实现 VetTaskDecomposer、VetContextBuilder、AgentRunner、RAG、OCR 或 Guardrail 等 L2 业务依赖。
##################################################################################################

from veterinary_agent.agent_application_service import (
    AgentGraphTurnResultDto,
    AgentResponseSegmentDto,
    AgentVetResultDto,
)
from veterinary_agent.graph_runtime import (
    GraphDefinition,
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphNodeSpec,
    GraphRegistry,
    GraphState,
)

VET_CONVERSATION_GRAPH_ID = "vet_conversation_graph"
VET_CONVERSATION_GRAPH_VERSION = "v2-langgraph"
VET_CONVERSATION_STATE_SCHEMA_VERSION = "graph_runtime.v2"
TODO_VET_GRAPH_NODE_ID = "todo_vet_conversation_graph"


class TodoVetConversationGraphNode:
    """兽医主业务图 TODO 降级节点。"""

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """执行兽医主业务图 TODO 降级节点。

        :param state: 当前图运行状态；TODO 节点不读取业务内容。
        :param context: 当前节点执行上下文。
        :return: 明确标记 L2 业务图尚未接入的节点结果。
        """

        del state
        output_text = (
            "当前兽医业务图节点尚未接入真实 L2 业务组件，"
            "暂时无法生成正式兽医建议。请稍后重试或联系服务管理员。"
        )
        segment = AgentResponseSegmentDto(
            segment_id=f"segment_{context.run_id}_todo",
            type="system_degraded",
            title="业务图未接入",
            status="completed",
            output_text=output_text,
            metadata={
                "graph_runtime_degraded": True,
                "missing_domain_dependencies": [
                    "VetTaskDecomposer",
                    "VetInputSafetyAssessor",
                    "VetContextBuilder",
                    "VetResponseComposer",
                ],
            },
        )
        result = AgentGraphTurnResultDto(
            output_text=output_text,
            segments=[segment],
            vet_result=AgentVetResultDto(
                route="todo_graph_runtime_fallback",
                audit_tier="engineering_degraded",
                metadata={
                    "graph_id": context.graph_id,
                    "graph_version": context.graph_version,
                    "node_id": context.node_id,
                },
            ),
            metadata={
                "graph_runtime": "langgraph",
                "graph_runtime_degraded": True,
                "todo_business_graph": True,
            },
        )
        return GraphNodeResult(
            state_patch={
                "result": result.model_dump(mode="json"),
                "segments": [segment.model_dump(mode="json")],
                "segments_to_publish": [segment.model_dump(mode="json")],
                "graph_runtime_degraded": True,
                "current_complaint_type": "todo_business_graph",
                "slot_progress": {},
            },
        )


def build_todo_vet_conversation_graph_definition() -> GraphDefinition:
    """构建兽医主业务图 TODO 降级定义。

    :return: 兽医主业务图 TODO 降级定义。
    """

    node = GraphNodeSpec(
        node_id=TODO_VET_GRAPH_NODE_ID,
        handler=TodoVetConversationGraphNode(),
    )
    return GraphDefinition(
        graph_id=VET_CONVERSATION_GRAPH_ID,
        graph_version=VET_CONVERSATION_GRAPH_VERSION,
        state_schema_version=VET_CONVERSATION_STATE_SCHEMA_VERSION,
        entry_node=TODO_VET_GRAPH_NODE_ID,
        nodes={TODO_VET_GRAPH_NODE_ID: node},
    )


def build_default_graph_registry() -> GraphRegistry:
    """构建默认兽医会话业务图注册表。

    当前默认注册表只包含 TODO 降级定义，避免在 L2 依赖尚未实现时跨领域补业务逻辑。

    :return: 已注册默认 TODO 图定义的 GraphRegistry。
    """

    registry = GraphRegistry()
    registry.register(build_todo_vet_conversation_graph_definition())
    return registry


__all__: tuple[str, ...] = (
    "TODO_VET_GRAPH_NODE_ID",
    "VET_CONVERSATION_GRAPH_ID",
    "VET_CONVERSATION_GRAPH_VERSION",
    "TodoVetConversationGraphNode",
    "build_default_graph_registry",
    "build_todo_vet_conversation_graph_definition",
)
