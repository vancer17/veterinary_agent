"""
文件：src/vet_agent/__init__.py
作用：作为 vet_agent 一级包入口，集中暴露跨包调用所需的稳定公共能力。
说明：重型对象通过延迟导入暴露，避免包初始化阶段触发循环依赖。
"""

from .config import Settings
from .contracts import (
    AgentTurnRequest,
    AgentTurnResponse,
    AttachmentRef,
    ErrorResponse,
    Evidence,
    IngressRequest,
    InputItem,
    ReasoningDisplay,
    RequestContext,
    SafetySignal,
    StreamEvent,
    TrustedIdentity,
    TurnOptions,
    VetContext,
    VetSegment,
    now_utc,
)

__all__ = [
    "__version__",
    "AgentTurnRequest",
    "AgentTurnResponse",
    "AttachmentRef",
    "Container",
    "ErrorResponse",
    "Evidence",
    "IngressRequest",
    "InputItem",
    "ReasoningDisplay",
    "RequestContext",
    "SafetySignal",
    "Settings",
    "StreamEvent",
    "TrustedIdentity",
    "TurnOptions",
    "VetAgentIngressOrchestrator",
    "VetContext",
    "VetOrchestrator",
    "VetSegment",
    "get_container",
    "now_utc",
]

__version__ = "0.1.0"


def __getattr__(name: str):
    """按名称延迟解析一级包公共对象。

    :param name: 名称。
    :return: 返回函数执行结果。
    """
    if name in {"Container", "get_container"}:
        from .container import Container, get_container

        values = {"Container": Container, "get_container": get_container}
        return values[name]
    if name == "VetAgentIngressOrchestrator":
        from .ingress_adapter import VetAgentIngressOrchestrator

        return VetAgentIngressOrchestrator
    if name == "VetOrchestrator":
        from .orchestrator import VetOrchestrator

        return VetOrchestrator
    raise AttributeError(f"module 'vet_agent' has no attribute {name!r}")
