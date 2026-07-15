##################################################################################################
# 文件: src/veterinary_agent/vet_conversation_graph/__init__.py
# 作用: 作为兽医会话业务图包统一出口，暴露 TODO 降级图与真实主业务图接线构建能力。
# 边界: 只暴露图定义、注册表和图内状态适配节点；不创建基础设施、不执行业务图运行。
##################################################################################################

from veterinary_agent.vet_conversation_graph.business_graph import (
    BRANCH_STATE_BUILDER_NODE_ID,
    CONTEXT_BUILDER_NODE_ID,
    DETERMINISTIC_GATE_NODE_ID,
    DETERMINISTIC_GATE_REQUEST_NODE_ID,
    EDUCATION_NODE_ID,
    EXECUTOR_ROUTER_NODE_ID,
    INPUT_SAFETY_NODE_ID,
    NONMEDICAL_PET_CARE_NODE_ID,
    POST_GENERATION_GUARD_REQUEST_NODE_ID,
    POST_GENERATION_REVIEW_NODE_ID,
    RESPONSE_COMPOSER_NODE_ID,
    SAFETY_TRIGGER_NODE_ID,
    STANDARD_CONSULTATION_NODE_ID,
    TASK_DECOMPOSER_NODE_ID,
    TASK_LANE_SELECTOR_NODE_ID,
    build_vet_conversation_graph_definition,
    build_vet_conversation_graph_registry,
)
from veterinary_agent.vet_conversation_graph.state_adapters import (
    BranchStateBuilderGraphNode,
    ExecutorRouterGraphNode,
    GuardrailRequestBuilderGraphNode,
    TaskLaneSelectorGraphNode,
)
from veterinary_agent.vet_conversation_graph.todo_graph import (
    TODO_VET_GRAPH_NODE_ID,
    VET_CONVERSATION_GRAPH_ID,
    VET_CONVERSATION_GRAPH_VERSION,
    TodoVetConversationGraphNode,
    build_default_graph_registry,
    build_todo_vet_conversation_graph_definition,
)

__all__: tuple[str, ...] = (
    "BRANCH_STATE_BUILDER_NODE_ID",
    "BranchStateBuilderGraphNode",
    "CONTEXT_BUILDER_NODE_ID",
    "DETERMINISTIC_GATE_NODE_ID",
    "DETERMINISTIC_GATE_REQUEST_NODE_ID",
    "EDUCATION_NODE_ID",
    "EXECUTOR_ROUTER_NODE_ID",
    "ExecutorRouterGraphNode",
    "GuardrailRequestBuilderGraphNode",
    "INPUT_SAFETY_NODE_ID",
    "NONMEDICAL_PET_CARE_NODE_ID",
    "POST_GENERATION_GUARD_REQUEST_NODE_ID",
    "POST_GENERATION_REVIEW_NODE_ID",
    "RESPONSE_COMPOSER_NODE_ID",
    "SAFETY_TRIGGER_NODE_ID",
    "STANDARD_CONSULTATION_NODE_ID",
    "TASK_DECOMPOSER_NODE_ID",
    "TASK_LANE_SELECTOR_NODE_ID",
    "TODO_VET_GRAPH_NODE_ID",
    "TaskLaneSelectorGraphNode",
    "VET_CONVERSATION_GRAPH_ID",
    "VET_CONVERSATION_GRAPH_VERSION",
    "TodoVetConversationGraphNode",
    "build_default_graph_registry",
    "build_todo_vet_conversation_graph_definition",
    "build_vet_conversation_graph_definition",
    "build_vet_conversation_graph_registry",
)
