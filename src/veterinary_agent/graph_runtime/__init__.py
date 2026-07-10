##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/__init__.py
# 作用: 作为 GraphRuntime 组件包统一出口，集中暴露通用运行时、图定义、注册表、DTO、枚举与错误。
# 边界: 外部包应从本文件导入 GraphRuntime 公共契约，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.graph_runtime.control_plane import GraphRunControlPlane
from veterinary_agent.graph_runtime.definition import (
    GraphDefinition,
    GraphEdgeKind,
    GraphEdgeSpec,
    GraphNodeExecutionContext,
    GraphNodeHandler,
    GraphNodeResult,
    GraphNodeSpec,
    GraphState,
)
from veterinary_agent.graph_runtime.dto import (
    GraphResumeRef,
    GraphRunControlContext,
    GraphRunIdentity,
    GraphRuntimeSettings,
    JsonMap,
    parse_graph_checkpoint_ref,
)
from veterinary_agent.graph_runtime.enums import (
    GraphNodeStatus,
    GraphRunStatus,
    GraphRuntimeErrorCode,
    GraphRuntimeEventType,
)
from veterinary_agent.graph_runtime.errors import (
    GraphRuntimeCancelledError,
    GraphRuntimeError,
)
from veterinary_agent.graph_runtime.events import GraphEventFactory
from veterinary_agent.graph_runtime.langgraph_backend import (
    CompiledGraph,
    LangGraphCompiler,
    LangGraphExecutionEngine,
    LangGraphRunContext,
    LangGraphRuntimeState,
)
from veterinary_agent.graph_runtime.registry import GraphRegistry
from veterinary_agent.graph_runtime.runtime import (
    DefaultGraphRuntime,
    create_default_graph_runtime,
)

__all__: tuple[str, ...] = (
    "CompiledGraph",
    "DefaultGraphRuntime",
    "GraphDefinition",
    "GraphEdgeKind",
    "GraphEdgeSpec",
    "GraphEventFactory",
    "GraphNodeExecutionContext",
    "GraphNodeHandler",
    "GraphNodeResult",
    "GraphNodeSpec",
    "GraphNodeStatus",
    "GraphRegistry",
    "GraphResumeRef",
    "GraphRunControlContext",
    "GraphRunControlPlane",
    "GraphRunIdentity",
    "GraphRunStatus",
    "GraphRuntimeCancelledError",
    "GraphRuntimeError",
    "GraphRuntimeErrorCode",
    "GraphRuntimeEventType",
    "GraphRuntimeSettings",
    "GraphState",
    "JsonMap",
    "LangGraphCompiler",
    "LangGraphExecutionEngine",
    "LangGraphRunContext",
    "LangGraphRuntimeState",
    "create_default_graph_runtime",
    "parse_graph_checkpoint_ref",
)
