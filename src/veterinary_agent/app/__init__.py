##################################################################################################
# 文件: src/veterinary_agent/app/__init__.py
# 作用: 作为 ASGI 应用包的统一出口，集中暴露 FastAPI 应用工厂与框架级状态模型。
# 边界: 外部包应从本文件导入 ASGI 应用能力，避免跨包直接引用实现模块。
##################################################################################################

from veterinary_agent.app.dependencies import (
    get_agent_application_service,
    get_checkpoint_provider,
    get_checkpoint_store_settings,
    get_conversation_store,
    get_conversation_store_settings,
    get_langgraph_checkpointer,
    get_observability_provider,
    get_pet_session_policy,
    get_runtime_config_provider,
    get_runtime_config_snapshot,
)
from veterinary_agent.app.factory import create_app
from veterinary_agent.app.lifespan import (
    AgentApplicationServiceFactory,
    AgentGraphRuntimeFactory,
    AgentLogicTraceStoreFactory,
    CheckpointProviderFactory,
    ConversationStoreFactory,
    create_default_agent_application_service,
    create_langgraph_postgres_saver_provider,
    create_todo_agent_graph_runtime,
    create_todo_agent_logic_trace_store,
    create_todo_conversation_store,
)
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)

__all__: tuple[str, ...] = (
    "AgentApplicationServiceFactory",
    "AgentGraphRuntimeFactory",
    "AgentLogicTraceStoreFactory",
    "CheckpointProviderFactory",
    "CheckpointProviderLifecycle",
    "ConversationStoreFactory",
    "VeterinaryAgentAppState",
    "create_app",
    "create_default_agent_application_service",
    "create_langgraph_postgres_saver_provider",
    "create_todo_agent_graph_runtime",
    "create_todo_agent_logic_trace_store",
    "create_todo_conversation_store",
    "get_agent_application_service",
    "get_checkpoint_provider",
    "get_checkpoint_store_settings",
    "get_conversation_store",
    "get_conversation_store_settings",
    "get_langgraph_checkpointer",
    "get_observability_provider",
    "get_pet_session_policy",
    "get_runtime_config_provider",
    "get_runtime_config_snapshot",
)
