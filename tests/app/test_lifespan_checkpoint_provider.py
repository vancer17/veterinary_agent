##################################################################################################
# 文件: tests/app/test_lifespan_checkpoint_provider.py
# 作用: 验证 FastAPI lifespan 会初始化、挂载并关闭 checkpoint provider。
# 边界: 仅测试 ASGI 应用生命周期装配，不连接 PostgreSQL、不调用真实 LangGraph、不执行 Agent 编排。
##################################################################################################

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreSettings,
    CheckpointStoreError,
    LangGraphCheckpointer,
    LangGraphPostgresSaverProvider,
    LangGraphPostgresSaverSettings,
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)
from veterinary_agent.app import (
    CheckpointProviderLifecycle,
    VeterinaryAgentAppState,
    create_app,
)


class _FakeAsyncPostgresSaver:
    """测试用 LangGraph AsyncPostgresSaver 空壳。"""

    def __init__(self) -> None:
        """初始化测试用 LangGraph AsyncPostgresSaver 空壳。

        :return: None。
        """

        self.setup_calls = 0
        self.fail_setup = False

    async def setup(self) -> None:
        """模拟 LangGraph PostgresSaver setup。

        :return: None。
        :raises RuntimeError: 当 fail_setup 为 True 时抛出。
        """

        self.setup_calls += 1
        if self.fail_setup:
            raise RuntimeError("setup failed")


class _FakeSaverContext(AbstractAsyncContextManager[AsyncPostgresSaver]):
    """测试用 LangGraph saver 异步上下文。"""

    def __init__(self, saver: _FakeAsyncPostgresSaver) -> None:
        """初始化测试用 LangGraph saver 异步上下文。

        :param saver: 测试用 LangGraph AsyncPostgresSaver 空壳。
        :return: None。
        """

        self.saver = saver
        self.database_url: str | None = None
        self.exit_calls = 0

    async def __aenter__(self) -> AsyncPostgresSaver:
        """进入测试用 saver 上下文。

        :return: 测试用 saver，类型上转换为 AsyncPostgresSaver。
        """

        return cast(AsyncPostgresSaver, self.saver)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出测试用 saver 上下文。

        :param exc_type: 触发退出的异常类型。
        :param exc: 触发退出的异常对象。
        :param traceback: 触发退出的异常调用栈。
        :return: None。
        """

        self.exit_calls += 1


def _record_database_url(
    *,
    context: _FakeSaverContext,
    database_url: str,
) -> _FakeSaverContext:
    """记录 provider 传入的数据库地址并返回测试上下文。

    :param context: 测试用 LangGraph saver 异步上下文。
    :param database_url: provider 传入的数据库连接地址。
    :return: 已记录数据库地址的测试上下文。
    """

    context.database_url = database_url
    return context


class _RecordingCheckpointProvider:
    """记录调用次数的测试 checkpoint provider。"""

    def __init__(
        self,
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
        ready_after_start: bool = True,
    ) -> None:
        """初始化测试 checkpoint provider。

        :param fail_start: start 是否抛出领域错误。
        :param fail_stop: stop 是否抛出领域错误。
        :param ready_after_start: start 成功后 is_ready 是否返回 True。
        :return: None。
        """

        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.ready_after_start = ready_after_start
        self.start_calls = 0
        self.stop_calls = 0
        self.started = False
        self.checkpointer = cast(LangGraphCheckpointer, object())

    async def start(self) -> None:
        """启动测试 checkpoint provider。

        :return: None。
        :raises CheckpointStoreError: 当 fail_start 为 True 时抛出。
        """

        self.start_calls += 1
        if self.fail_start:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
                message="provider start failed",
                retryable=True,
            )
        self.started = True

    async def stop(self) -> None:
        """停止测试 checkpoint provider。

        :return: None。
        :raises CheckpointStoreError: 当 fail_stop 为 True 时抛出。
        """

        self.stop_calls += 1
        if self.fail_stop:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_STOP,
                message="provider stop failed",
                retryable=True,
            )
        self.started = False

    def is_ready(self) -> bool:
        """判断测试 checkpoint provider 是否就绪。

        :return: start 后若 ready_after_start 为 True，则返回 True。
        """

        return self.started and self.ready_after_start

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


def _settings_without_orchestrator_readiness() -> ApiIngressSettings:
    """构建关闭编排 TODO readiness 检查的 API 接入配置。

    :return: 已关闭编排 TODO readiness 检查的 API 接入配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "readiness": base_settings.readiness.model_copy(
                update={"check_orchestrator": False}
            ),
        }
    )


def _state_from_app(app: FastAPI) -> VeterinaryAgentAppState:
    """从 FastAPI app.state 中读取兽医 Agent 应用状态。

    :param app: FastAPI 应用实例。
    :return: 兽医 Agent 应用状态。
    """

    state = getattr(app.state, "veterinary_agent_state")
    assert isinstance(state, VeterinaryAgentAppState)
    return state


def _factory_for(
    provider: _RecordingCheckpointProvider,
) -> CheckpointProviderLifecycle:
    """返回指定测试 provider。

    :param provider: 需要注入 lifespan 的测试 checkpoint provider。
    :return: 测试 checkpoint provider。
    """

    return provider


def test_lifespan_starts_and_stops_checkpoint_provider() -> None:
    """验证 FastAPI lifespan 会启动、挂载并关闭 checkpoint provider。

    :return: None。
    """

    provider = _RecordingCheckpointProvider()
    app = create_app(
        settings=_settings_without_orchestrator_readiness(),
        checkpoint_provider_factory=lambda: _factory_for(provider),
    )

    with TestClient(app) as client:
        state = _state_from_app(cast(FastAPI, client.app))
        assert state.ready is True
        assert isinstance(state.checkpoint_store_settings, CheckpointStoreSettings)
        assert state.checkpoint_provider is provider
        assert state.checkpoint_provider_ready is True
        assert state.checkpoint_provider_error is None
        assert provider.start_calls == 1
        assert provider.stop_calls == 0
        assert client.get("/ready").status_code == 200

    state = _state_from_app(app)
    assert state.ready is False
    assert state.checkpoint_provider is None
    assert state.checkpoint_provider_ready is False
    assert provider.stop_calls == 1


def test_lifespan_start_failure_blocks_application_startup() -> None:
    """验证 checkpoint provider 启动失败会阻止应用进入 ready。

    :return: None。
    """

    provider = _RecordingCheckpointProvider(fail_start=True)
    app = create_app(
        settings=_settings_without_orchestrator_readiness(),
        checkpoint_provider_factory=lambda: _factory_for(provider),
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        with TestClient(app):
            pass

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START
    state = _state_from_app(app)
    assert state.ready is False
    assert state.checkpoint_provider_ready is False
    assert state.checkpoint_provider_error is not None
    assert provider.start_calls == 1
    assert provider.stop_calls == 1


def test_lifespan_records_checkpoint_provider_stop_failure() -> None:
    """验证 checkpoint provider 关闭失败会被记录但不阻塞 TestClient 退出。

    :return: None。
    """

    provider = _RecordingCheckpointProvider(fail_stop=True)
    app = create_app(
        settings=_settings_without_orchestrator_readiness(),
        checkpoint_provider_factory=lambda: _factory_for(provider),
    )

    with TestClient(app):
        state = _state_from_app(app)
        assert state.ready is True

    state = _state_from_app(app)
    assert state.ready is False
    assert state.checkpoint_provider is None
    assert state.checkpoint_provider_ready is False
    assert state.checkpoint_provider_error is not None
    assert state.checkpoint_provider_error.operation is (
        CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_STOP
    )
    assert provider.stop_calls == 1


def test_lifespan_rejects_provider_that_is_not_ready_after_start() -> None:
    """验证 provider start 成功但 is_ready 为 False 时应用启动失败。

    :return: None。
    """

    provider = _RecordingCheckpointProvider(ready_after_start=False)
    app = create_app(
        settings=_settings_without_orchestrator_readiness(),
        checkpoint_provider_factory=lambda: _factory_for(provider),
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        with TestClient(app):
            pass

    assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    state = _state_from_app(app)
    assert state.ready is False
    assert state.checkpoint_provider is None
    assert state.checkpoint_provider_ready is False
    assert provider.stop_calls == 1


def test_lifespan_rejects_langgraph_provider_when_setup_fails() -> None:
    """验证真实 LangGraph provider 的 setup 失败会阻止应用启动。

    :return: None。
    """

    saver = _FakeAsyncPostgresSaver()
    saver.fail_setup = True
    context = _FakeSaverContext(saver)

    def _create_provider() -> CheckpointProviderLifecycle:
        """创建使用 fake saver context 的 LangGraph provider。

        :return: 使用 fake saver context 的 LangGraph provider。
        """

        return LangGraphPostgresSaverProvider(
            settings=LangGraphPostgresSaverSettings(
                database_url="postgresql+psycopg://user:pass@db/app",
                setup_on_startup=True,
                strict_msgpack_enabled=True,
            ),
            saver_context_factory=lambda database_url: _record_database_url(
                context=context,
                database_url=database_url,
            ),
        )

    app = create_app(
        settings=_settings_without_orchestrator_readiness(),
        checkpoint_provider_factory=_create_provider,
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        with TestClient(app):
            pass

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START
    state = _state_from_app(app)
    assert state.ready is False
    assert state.checkpoint_provider is None
    assert state.checkpoint_provider_ready is False
    assert state.checkpoint_provider_error is not None
    assert saver.setup_calls == 1
    assert context.exit_calls == 1
