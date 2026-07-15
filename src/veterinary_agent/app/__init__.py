##################################################################################################
# 文件: src/veterinary_agent/app/__init__.py
# 作用: 作为 ASGI 应用包的统一出口，集中暴露 FastAPI 应用工厂与框架级状态模型。
# 边界: 外部包应从本文件导入 ASGI 应用能力，避免跨包直接引用实现模块。
##################################################################################################

from veterinary_agent.app.dependencies import get_app_state
from veterinary_agent.app.factory import create_app
from veterinary_agent.app.bootstrap import (
    AgentApplicationServiceFactory,
    AgentGraphRuntimeFactory,
    AgentRunnerFactory,
    CheckpointProviderFactory,
    ConversationStoreFactory,
    LlmGatewayFactory,
    LogicTraceStoreFactory,
    RuntimeGraphComponentBundle,
    TodoCheckpointProvider,
    create_default_agent_application_service,
    create_langgraph_postgres_saver_provider,
    create_runtime_checkpoint_store,
    create_runtime_agent_runner,
    create_runtime_conversation_store,
    create_runtime_llm_gateway,
    create_runtime_logic_trace_store,
    create_todo_agent_graph_runtime,
    create_todo_checkpoint_provider,
    create_todo_conversation_store,
    create_todo_logic_trace_store,
    has_runtime_database_url,
)
from veterinary_agent.app.state import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
)

__all__: tuple[str, ...] = (
    "AgentApplicationServiceFactory",
    "AgentGraphRuntimeFactory",
    "AgentRunnerFactory",
    "CheckpointProviderFactory",
    "CheckpointProviderLifecycle",
    "ConversationStoreFactory",
    "LlmGatewayFactory",
    "LogicTraceStoreFactory",
    "RuntimeGraphComponentBundle",
    "TodoCheckpointProvider",
    "VeterinaryAgentAppState",
    "create_app",
    "create_default_agent_application_service",
    "create_langgraph_postgres_saver_provider",
    "create_runtime_checkpoint_store",
    "create_runtime_agent_runner",
    "create_runtime_conversation_store",
    "create_runtime_llm_gateway",
    "create_runtime_logic_trace_store",
    "create_todo_agent_graph_runtime",
    "create_todo_checkpoint_provider",
    "create_todo_conversation_store",
    "create_todo_logic_trace_store",
    "get_app_state",
    "has_runtime_database_url",
)
