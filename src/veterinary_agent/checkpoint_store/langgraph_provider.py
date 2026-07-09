##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/langgraph_provider.py
# 作用: 定义 LangGraph AsyncPostgresSaver provider，负责创建、启动、持有和关闭图状态持久化 checkpointer。
# 边界: 仅管理 LangGraph PostgresSaver 生命周期与 config 构造；不访问控制面表、不实现 GraphRuntime、不读取业务状态。
##################################################################################################

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Final, TypeAlias

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.langgraph_settings import (
    LANGGRAPH_STRICT_MSGPACK_ENV_NAME,
    LangGraphPostgresSaverSettings,
)

LANGGRAPH_THREAD_ID_MAX_LENGTH: Final[int] = 255

LangGraphCheckpointer: TypeAlias = AsyncPostgresSaver
LangGraphRunnableConfig: TypeAlias = dict[str, dict[str, str]]
LangGraphPostgresSaverContextFactory: TypeAlias = Callable[
    [str],
    AbstractAsyncContextManager[LangGraphCheckpointer],
]


def create_async_postgres_saver_context(
    database_url: str,
) -> AbstractAsyncContextManager[LangGraphCheckpointer]:
    """创建 LangGraph AsyncPostgresSaver 异步上下文。

    :param database_url: PostgreSQL 数据库连接地址。
    :return: LangGraph AsyncPostgresSaver 异步上下文管理器。
    """

    return AsyncPostgresSaver.from_conn_string(database_url)


def _ensure_strict_msgpack_enabled(
    settings: LangGraphPostgresSaverSettings,
) -> None:
    """根据配置启用 LangGraph 严格 msgpack 模式。

    :param settings: LangGraph PostgresSaver 配置。
    :return: None。
    """

    if settings.strict_msgpack_enabled:
        os.environ[LANGGRAPH_STRICT_MSGPACK_ENV_NAME] = "true"


def _validate_config_identifier(
    *,
    value: str,
    field_name: str,
) -> str:
    """校验 LangGraph config 中的字符串标识。

    :param value: 需要校验的字符串标识。
    :param field_name: 当前标识对应的字段名。
    :return: 去除首尾空白后的字符串标识。
    :raises CheckpointStoreError: 当标识为空或 thread_id 超长时抛出。
    """

    normalized_value = value.strip()
    if not normalized_value:
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.BUILD_LANGGRAPH_CONFIG,
            message=f"{field_name} 不得为空",
            retryable=False,
        )
    if (
        field_name == "thread_id"
        and len(normalized_value) > LANGGRAPH_THREAD_ID_MAX_LENGTH
    ):
        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT,
            operation=CheckpointOperation.BUILD_LANGGRAPH_CONFIG,
            message=f"thread_id 长度不得超过 {LANGGRAPH_THREAD_ID_MAX_LENGTH}",
            retryable=False,
            conflict_with={
                "field": field_name,
                "max_length": LANGGRAPH_THREAD_ID_MAX_LENGTH,
                "actual_length": len(normalized_value),
            },
        )
    return normalized_value


def build_langgraph_thread_config(
    *,
    thread_id: str,
    checkpoint_id: str | None = None,
) -> LangGraphRunnableConfig:
    """构建 LangGraph thread 运行配置。

    :param thread_id: LangGraph checkpointer 使用的 thread ID。
    :param checkpoint_id: 可选 checkpoint ID，用于读取指定历史快照。
    :return: 可传递给 LangGraph 的运行配置。
    :raises CheckpointStoreError: 当 thread_id 或 checkpoint_id 非法时抛出。
    """

    configurable = {
        "thread_id": _validate_config_identifier(
            value=thread_id,
            field_name="thread_id",
        ),
        "checkpoint_ns": "",
    }
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = _validate_config_identifier(
            value=checkpoint_id,
            field_name="checkpoint_id",
        )
    return {"configurable": configurable}


class LangGraphPostgresSaverProvider:
    """LangGraph AsyncPostgresSaver 生命周期 provider。"""

    def __init__(
        self,
        settings: LangGraphPostgresSaverSettings,
        saver_context_factory: LangGraphPostgresSaverContextFactory | None = None,
    ) -> None:
        """初始化 LangGraph AsyncPostgresSaver provider。

        :param settings: LangGraph PostgresSaver 配置。
        :param saver_context_factory: 可选 saver 上下文工厂；测试可用空壳替代真实数据库连接。
        :return: None。
        """

        self._settings = settings
        self._saver_context_factory = (
            create_async_postgres_saver_context
            if saver_context_factory is None
            else saver_context_factory
        )
        self._saver_context: AbstractAsyncContextManager[LangGraphCheckpointer] | None = (
            None
        )
        self._checkpointer: LangGraphCheckpointer | None = None
        self._ready = False
        self._setup_completed = False

    @property
    def settings(self) -> LangGraphPostgresSaverSettings:
        """读取 provider 当前使用的配置。

        :return: LangGraph PostgresSaver 配置。
        """

        return self._settings

    @property
    def ready(self) -> bool:
        """读取 provider 是否已经完成启动。

        :return: 若 provider 已完成启动并持有 checkpointer，则返回 True。
        """

        return self._ready

    @property
    def setup_completed(self) -> bool:
        """读取本次启动是否已经执行 LangGraph setup。

        :return: 若配置要求启动时 setup 且 setup 已成功执行，则返回 True。
        """

        return self._setup_completed

    def is_ready(self) -> bool:
        """判断 provider 是否可提供 checkpointer。

        :return: 若 provider 已完成启动并持有 checkpointer，则返回 True。
        """

        return self._ready

    async def start(self) -> None:
        """启动 LangGraph AsyncPostgresSaver provider。

        :return: None。
        :raises CheckpointStoreError: 当创建 saver、连接数据库或 setup 失败时抛出。
        """

        if self._ready:
            return

        _ensure_strict_msgpack_enabled(self._settings)
        saver_context = self._saver_context_factory(self._settings.database_url)
        try:
            checkpointer = await saver_context.__aenter__()
            try:
                if self._settings.setup_on_startup:
                    await checkpointer.setup()
                    self._setup_completed = True
            except Exception as exc:
                await saver_context.__aexit__(type(exc), exc, exc.__traceback__)
                raise
        except TimeoutError as exc:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
                message="LangGraph AsyncPostgresSaver 启动超时",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_START,
                message="LangGraph AsyncPostgresSaver 启动失败",
                retryable=True,
            ) from exc

        self._saver_context = saver_context
        self._checkpointer = checkpointer
        self._ready = True

    async def stop(self) -> None:
        """停止 LangGraph AsyncPostgresSaver provider 并释放底层资源。

        :return: None。
        :raises CheckpointStoreError: 当关闭 saver 上下文失败时抛出。
        """

        saver_context = self._saver_context
        if saver_context is None:
            self._checkpointer = None
            self._ready = False
            self._setup_completed = False
            return

        self._saver_context = None
        self._checkpointer = None
        self._ready = False
        self._setup_completed = False
        try:
            await saver_context.__aexit__(None, None, None)
        except TimeoutError as exc:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_STOP,
                message="LangGraph AsyncPostgresSaver 停止超时",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_STOP,
                message="LangGraph AsyncPostgresSaver 停止失败",
                retryable=True,
            ) from exc

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取已启动的 LangGraph AsyncPostgresSaver。

        :return: 已初始化并可传递给 LangGraph compile 的 AsyncPostgresSaver。
        :raises CheckpointStoreError: 当 provider 尚未启动时抛出。
        """

        if not self._ready or self._checkpointer is None:
            raise CheckpointStoreError(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=CheckpointOperation.LANGGRAPH_POSTGRES_SAVER_GET,
                message="LangGraph AsyncPostgresSaver 尚未启动",
                retryable=True,
            )
        return self._checkpointer

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
        :raises CheckpointStoreError: 当 thread_id 或 checkpoint_id 非法时抛出。
        """

        return build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )

    async def __aenter__(self) -> "LangGraphPostgresSaverProvider":
        """进入异步上下文并启动 provider。

        :return: 已启动的当前 provider。
        :raises CheckpointStoreError: 当 provider 启动失败时抛出。
        """

        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出异步上下文并停止 provider。

        :param exc_type: 触发退出的异常类型。
        :param exc: 触发退出的异常对象。
        :param traceback: 触发退出的异常调用栈。
        :return: None。
        :raises CheckpointStoreError: 当 provider 停止失败时抛出。
        """

        await self.stop()


__all__: tuple[str, ...] = (
    "LANGGRAPH_THREAD_ID_MAX_LENGTH",
    "LangGraphCheckpointer",
    "LangGraphPostgresSaverContextFactory",
    "LangGraphPostgresSaverProvider",
    "LangGraphRunnableConfig",
    "build_langgraph_thread_config",
    "create_async_postgres_saver_context",
)
