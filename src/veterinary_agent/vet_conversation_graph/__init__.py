##################################################################################################
# 文件: src/veterinary_agent/vet_conversation_graph/__init__.py
# 作用: 作为兽医会话业务图包统一出口，暴露当前 TODO 降级图注册能力。
# 边界: L2 真实业务组件尚未实现；本包仅提供明确降级空壳，避免在 GraphRuntime 内跨领域补业务。
##################################################################################################

from veterinary_agent.vet_conversation_graph.todo_graph import (
    TODO_VET_GRAPH_NODE_ID,
    VET_CONVERSATION_GRAPH_ID,
    VET_CONVERSATION_GRAPH_VERSION,
    TodoVetConversationGraphNode,
    build_default_graph_registry,
    build_todo_vet_conversation_graph_definition,
)

__all__: tuple[str, ...] = (
    "TODO_VET_GRAPH_NODE_ID",
    "VET_CONVERSATION_GRAPH_ID",
    "VET_CONVERSATION_GRAPH_VERSION",
    "TodoVetConversationGraphNode",
    "build_default_graph_registry",
    "build_todo_vet_conversation_graph_definition",
)
