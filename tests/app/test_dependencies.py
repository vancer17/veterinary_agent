##################################################################################################
# 文件: tests/app/test_dependencies.py
# 作用: 验证 ASGI app 依赖层可以安全暴露 checkpoint provider 与 LangGraph checkpointer。
# 边界: 仅测试 FastAPI request/app.state 依赖访问，不连接数据库、不编译真实 LangGraph 图、不执行 Agent 编排。
##################################################################################################

from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI, Request

from veterinary_agent import (
    ApiIngressConcurrencyGate,
    ApiIngressRateLimiter,
    ApiIngressSettings,
    CheckpointStoreSettings,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointProviderLifecycle,
    CheckpointStoreError,
    LangGraphCheckpointer,
    LangGraphRunnableConfig,
    VeterinaryAgentAppState,
    build_langgraph_thread_config,
    get_checkpoint_provider,
    get_checkpoint_store_settings,
    get_langgraph_checkpointer,
)


class _DependencyCheckpointProvider:
    """测试用 checkpoint provider。"""

    def __init__(self, *, ready: bool = True) -> None:
        """初始化测试用 checkpoint provider。

        :param ready: provider 是否报告自身就绪。
        :return: None。
        """

        self.ready = ready
        self.checkpointer = cast(LangGraphCheckpointer, object())

    async def start(self) -> None:
        """启动测试用 checkpoint provider。

        :return: None。
        """

        self.ready = True

    async def stop(self) -> None:
        """停止测试用 checkpoint provider。

        :return: None。
        """

        self.ready = False

    def is_ready(self) -> bool:
        """判断测试用 checkpoint provider 是否就绪。

        :return: 若 provider 当前报告就绪，则返回 True。
        """

        return self.ready

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取测试用 LangGraph checkpointer 空壳。

        :return: 测试用 LangGraph checkpointer 空壳。
        """

        return self.checkpointer

    def build_config(
        self,
        *,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> LangGraphRunnableConfig:
        """构建测试用 LangGraph thread 运行配置。

        :param thread_id: LangGraph checkpointer 使用的 thread ID。
        :param checkpoint_id: 可选 checkpoint ID。
        :return: 可传递给 LangGraph 的运行配置。
        """

        return build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )


def _build_app_state(
    *,
    checkpoint_provider: CheckpointProviderLifecycle | None,
    checkpoint_provider_ready: bool,
) -> VeterinaryAgentAppState:
    """构建测试用 ASGI 应用状态。

    :param checkpoint_provider: 需要挂载到 app state 的 checkpoint provider。
    :param checkpoint_provider_ready: app state 记录的 checkpoint provider 就绪标记。
    :return: 测试用兽医 Agent 应用状态。
    """

    settings = ApiIngressSettings()
    return VeterinaryAgentAppState(
        settings=settings,
        started_at=datetime.now(UTC),
        ready=True,
        orchestrator_concurrency_gate=ApiIngressConcurrencyGate(
            max_concurrency=settings.orchestrator.max_concurrency,
        ),
        rate_limiter=ApiIngressRateLimiter.from_settings(settings),
        checkpoint_store_settings=CheckpointStoreSettings(),
        checkpoint_provider=checkpoint_provider,
        checkpoint_provider_ready=checkpoint_provider_ready,
        checkpoint_provider_error=None,
    )


def _build_request(app_state: VeterinaryAgentAppState) -> Request:
    """构建带有兽医 Agent app state 的 FastAPI 请求对象。

    :param app_state: 需要挂载到 app.state 的兽医 Agent 应用状态。
    :return: 可传入依赖函数的 FastAPI Request。
    """

    app = FastAPI()
    setattr(app.state, "veterinary_agent_state", app_state)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
        }
    )


def test_get_checkpoint_provider_returns_ready_provider() -> None:
    """验证 provider 已就绪时依赖函数返回同一 provider。

    :return: None。
    """

    provider = _DependencyCheckpointProvider()
    request = _build_request(
        _build_app_state(
            checkpoint_provider=provider,
            checkpoint_provider_ready=True,
        )
    )

    assert get_checkpoint_provider(request) is provider


def test_get_langgraph_checkpointer_returns_provider_checkpointer() -> None:
    """验证可从依赖函数读取 provider 暴露的 LangGraph checkpointer。

    :return: None。
    """

    provider = _DependencyCheckpointProvider()
    request = _build_request(
        _build_app_state(
            checkpoint_provider=provider,
            checkpoint_provider_ready=True,
        )
    )

    assert get_langgraph_checkpointer(request) is provider.checkpointer


def test_get_checkpoint_store_settings_returns_runtime_config() -> None:
    """验证依赖函数可从 app state 读取 CheckpointStore RuntimeConfig。

    :return: None。
    """

    app_state = _build_app_state(
        checkpoint_provider=_DependencyCheckpointProvider(),
        checkpoint_provider_ready=True,
    )
    request = _build_request(app_state)

    assert get_checkpoint_store_settings(request) is app_state.checkpoint_store_settings


@pytest.mark.parametrize(
    ("provider", "provider_ready"),
    [
        (None, False),
        (_DependencyCheckpointProvider(), False),
        (_DependencyCheckpointProvider(ready=False), True),
    ],
)
def test_get_checkpoint_provider_rejects_unavailable_provider(
    provider: CheckpointProviderLifecycle | None,
    provider_ready: bool,
) -> None:
    """验证 provider 缺失或未就绪时依赖函数抛出领域错误。

    :param provider: 测试用 checkpoint provider。
    :param provider_ready: app state 记录的 checkpoint provider 就绪标记。
    :return: None。
    """

    request = _build_request(
        _build_app_state(
            checkpoint_provider=provider,
            checkpoint_provider_ready=provider_ready,
        )
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        get_checkpoint_provider(request)

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_GET
