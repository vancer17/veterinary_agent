##################################################################################################
# 文件: src/veterinary_agent/app/state.py
# 作用: 定义 ASGI 应用框架级状态对象，用于在 FastAPI 生命周期与依赖注入间传递已加载配置、就绪标记和基础设施 provider。
# 边界: 仅保存框架层状态与基础设施 provider 引用；不创建 provider、不访问数据库、不执行 Agent 编排。
##################################################################################################

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from veterinary_agent.api_ingress import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
)
from veterinary_agent.checkpoint_store import (
    CheckpointStoreErrorDto,
    LangGraphCheckpointer,
    LangGraphRunnableConfig,
)
from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.config import CheckpointStoreSettings
from veterinary_agent.config import RuntimeConfigProvider, RuntimeConfigSnapshot


class CheckpointProviderLifecycle(Protocol):
    """ASGI 应用层可管理的 checkpoint provider 生命周期协议。"""

    async def start(self) -> None:
        """启动 checkpoint provider。

        :return: None。
        """

        ...

    async def stop(self) -> None:
        """停止 checkpoint provider。

        :return: None。
        """

        ...

    def is_ready(self) -> bool:
        """判断 checkpoint provider 是否已就绪。

        :return: 若 checkpoint provider 已可用，则返回 True。
        """

        ...

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取可供 GraphRuntime 编译图使用的 LangGraph checkpointer。

        :return: 已由 FastAPI lifespan 初始化的 LangGraph checkpointer。
        """

        ...

    def build_config(
        self,
        *,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> LangGraphRunnableConfig:
        """构建 LangGraph thread 运行配置。

        :param thread_id: LangGraph checkpointer 使用的 thread ID。
        :param checkpoint_id: 可选 checkpoint ID，用于读取指定历史快照。
        :return: 可传递给 LangGraph 的运行配置。
        """

        ...


@dataclass(slots=True)
class VeterinaryAgentAppState:
    """兽医 Agent ASGI 应用框架级状态。"""

    settings: ApiIngressSettings
    runtime_config_provider: RuntimeConfigProvider | None
    runtime_config_snapshot: RuntimeConfigSnapshot | None
    started_at: datetime
    ready: bool
    orchestrator_concurrency_gate: ApiIngressConcurrencyGate
    rate_limiter: ApiIngressRateLimiter
    checkpoint_store_settings: CheckpointStoreSettings | None
    checkpoint_provider: CheckpointProviderLifecycle | None
    checkpoint_provider_ready: bool
    checkpoint_provider_error: CheckpointStoreErrorDto | None


__all__: tuple[str, ...] = (
    "CheckpointProviderLifecycle",
    "VeterinaryAgentAppState",
)
