##################################################################################################
# 文件: tests/conftest.py
# 作用: 定义测试级通用夹具，为默认 FastAPI lifespan 注入 checkpoint provider TODO 空壳，避免测试连接真实 PostgreSQL。
# 边界: 仅影响测试装配；不修改生产代码、不访问数据库、不调用 LangGraph 或其他领域组件。
##################################################################################################

from collections.abc import Iterator
from typing import cast

import pytest

from veterinary_agent.app import CheckpointProviderLifecycle
from veterinary_agent.checkpoint_store import (
    LangGraphCheckpointer,
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)


class _TodoCheckpointProvider:
    """测试用 checkpoint provider TODO 空壳。"""

    def __init__(self) -> None:
        """初始化测试用 checkpoint provider。

        :return: None。
        """

        self.started = False
        self.checkpointer = cast(LangGraphCheckpointer, object())

    async def start(self) -> None:
        """启动测试用 checkpoint provider。

        :return: None。
        """

        self.started = True

    async def stop(self) -> None:
        """停止测试用 checkpoint provider。

        :return: None。
        """

        self.started = False

    def is_ready(self) -> bool:
        """判断测试用 checkpoint provider 是否就绪。

        :return: 若测试用 provider 已启动，则返回 True。
        """

        return self.started

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


def _create_todo_checkpoint_provider() -> CheckpointProviderLifecycle:
    """创建测试用 checkpoint provider TODO 空壳。

    :return: 测试用 checkpoint provider。
    """

    return _TodoCheckpointProvider()


@pytest.fixture(autouse=True)
def _patch_default_checkpoint_provider_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """将默认 checkpoint provider 工厂替换为测试 TODO 空壳。

    :param monkeypatch: pytest monkeypatch 夹具。
    :return: pytest 夹具迭代器。
    """

    monkeypatch.setattr(
        "veterinary_agent.app.bootstrap.create_langgraph_postgres_saver_provider",
        _create_todo_checkpoint_provider,
    )
    yield
