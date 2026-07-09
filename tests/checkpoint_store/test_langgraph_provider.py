##################################################################################################
# 文件: tests/checkpoint_store/test_langgraph_provider.py
# 作用: 验证 LangGraph AsyncPostgresSaver provider 的生命周期、config 构造和错误映射。
# 边界: 仅使用测试空壳 context，不连接 PostgreSQL、不调用真实 LangGraph setup、不访问控制面表。
##################################################################################################

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    LANGGRAPH_STRICT_MSGPACK_ENV_NAME,
    LANGGRAPH_THREAD_ID_MAX_LENGTH,
    LangGraphPostgresSaverContextFactory,
    LangGraphPostgresSaverProvider,
    LangGraphPostgresSaverSettings,
    build_langgraph_thread_config,
)


class _FakeAsyncPostgresSaver:
    """测试用 AsyncPostgresSaver 空壳。"""

    def __init__(self) -> None:
        """初始化测试用 AsyncPostgresSaver 空壳。

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
    """测试用 AsyncPostgresSaver 异步上下文。"""

    def __init__(self, saver: _FakeAsyncPostgresSaver) -> None:
        """初始化测试用 AsyncPostgresSaver 异步上下文。

        :param saver: 测试用 AsyncPostgresSaver 空壳。
        :return: None。
        """

        self.saver = saver
        self.database_url: str | None = None
        self.enter_calls = 0
        self.exit_calls = 0
        self.fail_enter = False

    async def __aenter__(self) -> AsyncPostgresSaver:
        """进入测试用 saver 上下文。

        :return: 测试用 saver，类型上转换为 AsyncPostgresSaver。
        :raises RuntimeError: 当 fail_enter 为 True 时抛出。
        """

        self.enter_calls += 1
        if self.fail_enter:
            raise RuntimeError("enter failed")
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


def _build_settings(
    *,
    setup_on_startup: bool = True,
    strict_msgpack_enabled: bool = True,
) -> LangGraphPostgresSaverSettings:
    """构建测试用 LangGraph PostgresSaver 配置。

    :param setup_on_startup: 是否在 provider 启动时执行 setup。
    :param strict_msgpack_enabled: 是否由 provider 启用严格 msgpack。
    :return: 测试用 LangGraph PostgresSaver 配置。
    """

    return LangGraphPostgresSaverSettings(
        database_url="postgresql+psycopg://user:pass@db/app",
        setup_on_startup=setup_on_startup,
        strict_msgpack_enabled=strict_msgpack_enabled,
    )


def _build_context_factory(
    context: _FakeSaverContext,
) -> LangGraphPostgresSaverContextFactory:
    """构建测试用 saver 上下文工厂。

    :param context: 测试用 saver 上下文。
    :return: 可注入 provider 的 saver 上下文工厂。
    """

    def _factory(database_url: str) -> AbstractAsyncContextManager[AsyncPostgresSaver]:
        """返回测试用 saver 上下文并记录数据库地址。

        :param database_url: provider 传入的数据库连接地址。
        :return: 测试用 saver 上下文。
        """

        context.database_url = database_url
        return context

    return _factory


def test_langgraph_provider_start_sets_ready_and_runs_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 provider 启动后进入 ready 状态并按配置执行 setup。

    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    monkeypatch.delenv(LANGGRAPH_STRICT_MSGPACK_ENV_NAME, raising=False)
    saver = _FakeAsyncPostgresSaver()
    context = _FakeSaverContext(saver)
    provider = LangGraphPostgresSaverProvider(
        settings=_build_settings(),
        saver_context_factory=_build_context_factory(context),
    )

    asyncio.run(provider.start())

    assert provider.ready is True
    assert provider.is_ready() is True
    assert provider.setup_completed is True
    assert provider.get_checkpointer() is saver
    assert saver.setup_calls == 1
    assert context.enter_calls == 1
    assert context.database_url == "postgresql+psycopg://user:pass@db/app"
    assert os.environ[LANGGRAPH_STRICT_MSGPACK_ENV_NAME] == "true"

    asyncio.run(provider.stop())
    assert provider.ready is False
    assert context.exit_calls == 1


def test_langgraph_provider_start_can_skip_setup() -> None:
    """验证 provider 可按配置跳过启动时 setup。

    :return: None。
    """

    saver = _FakeAsyncPostgresSaver()
    context = _FakeSaverContext(saver)
    provider = LangGraphPostgresSaverProvider(
        settings=_build_settings(setup_on_startup=False),
        saver_context_factory=_build_context_factory(context),
    )

    asyncio.run(provider.start())

    assert provider.ready is True
    assert provider.setup_completed is False
    assert saver.setup_calls == 0

    asyncio.run(provider.stop())


def test_langgraph_provider_get_before_start_raises_domain_error() -> None:
    """验证 provider 未启动时读取 checkpointer 会抛出领域错误。

    :return: None。
    """

    provider = LangGraphPostgresSaverProvider(settings=_build_settings())

    with pytest.raises(CheckpointStoreError) as exc_info:
        provider.get_checkpointer()

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_GET


def test_langgraph_provider_closes_context_when_setup_fails() -> None:
    """验证 setup 失败时 provider 会关闭已进入的 saver 上下文。

    :return: None。
    """

    saver = _FakeAsyncPostgresSaver()
    saver.fail_setup = True
    context = _FakeSaverContext(saver)
    provider = LangGraphPostgresSaverProvider(
        settings=_build_settings(),
        saver_context_factory=_build_context_factory(context),
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        asyncio.run(provider.start())

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START
    assert provider.ready is False
    assert context.exit_calls == 1


def test_build_langgraph_thread_config_returns_configurable_payload() -> None:
    """验证 LangGraph thread config 构造结果符合预期。

    :return: None。
    """

    config = build_langgraph_thread_config(
        thread_id="thread_1",
        checkpoint_id="checkpoint_1",
    )

    assert config == {
        "configurable": {
            "thread_id": "thread_1",
            "checkpoint_ns": "",
            "checkpoint_id": "checkpoint_1",
        }
    }


def test_build_langgraph_thread_config_rejects_invalid_ids() -> None:
    """验证 LangGraph thread config 会拒绝非法标识。

    :return: None。
    """

    with pytest.raises(CheckpointStoreError) as empty_exc_info:
        build_langgraph_thread_config(thread_id=" ")
    assert empty_exc_info.value.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT

    with pytest.raises(CheckpointStoreError) as long_exc_info:
        build_langgraph_thread_config(
            thread_id="t" * (LANGGRAPH_THREAD_ID_MAX_LENGTH + 1)
        )
    assert long_exc_info.value.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
