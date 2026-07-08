##################################################################################################
# 文件: src/veterinary_agent/app/state.py
# 作用: 定义 ASGI 应用框架级状态对象，用于在 FastAPI 生命周期与依赖注入间传递已加载配置和就绪标记。
# 边界: 仅保存框架层状态；编排层客户端、存储客户端和业务组件依赖后续接入时再扩展。
##################################################################################################

from dataclasses import dataclass
from datetime import datetime

from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.config import ApiIngressSettings


@dataclass(slots=True)
class VeterinaryAgentAppState:
    """兽医 Agent ASGI 应用框架级状态。"""

    settings: ApiIngressSettings
    started_at: datetime
    ready: bool
    orchestrator_concurrency_gate: ApiIngressConcurrencyGate
    rate_limiter: ApiIngressRateLimiter


__all__: tuple[str, ...] = ("VeterinaryAgentAppState",)
