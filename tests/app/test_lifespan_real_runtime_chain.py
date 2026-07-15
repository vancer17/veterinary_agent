##################################################################################################
# 文件: tests/app/test_lifespan_real_runtime_chain.py
# 作用: 验证 FastAPI lifespan 在真实数据库配置存在时会装配真实主业务图运行链路并承载同步 API 请求。
# 边界: 使用临时 SQLite 控制面数据库与 InMemorySaver；不连接外部 PostgreSQL、不调用真实 LLM/RAG。
##################################################################################################

from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from veterinary_agent.app import VeterinaryAgentAppState, create_app
from veterinary_agent.checkpoint_store import (
    DATABASE_URL_ENV_NAME,
    LangGraphCheckpointer,
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)
from veterinary_agent.graph_runtime import DefaultGraphRuntime


class _InMemoryCheckpointProvider:
    """真实 lifespan 测试使用的内存 LangGraph checkpoint provider。"""

    def __init__(self) -> None:
        """初始化内存 checkpoint provider。

        :return: None。
        """

        self.started = False
        self.stopped = False
        self._checkpointer = InMemorySaver()

    async def start(self) -> None:
        """启动内存 checkpoint provider。

        :return: None。
        """

        self.started = True
        self.stopped = False

    async def stop(self) -> None:
        """停止内存 checkpoint provider。

        :return: None。
        """

        self.started = False
        self.stopped = True

    def is_ready(self) -> bool:
        """判断内存 checkpoint provider 是否已启动。

        :return: 若 provider 已启动且未停止，则返回 True。
        """

        return self.started and not self.stopped

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取内存 LangGraph checkpointer。

        :return: 可供 GraphRuntime 编译真实业务图的内存 checkpointer。
        """

        return cast(LangGraphCheckpointer, self._checkpointer)

    def build_config(
        self,
        *,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> LangGraphRunnableConfig:
        """构建 LangGraph thread 运行配置。

        :param thread_id: LangGraph checkpointer 使用的 thread ID。
        :param checkpoint_id: 可选 checkpoint ID。
        :return: 可传递给 LangGraph 的运行配置。
        """

        return build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )


def _build_sqlite_database_url(database_path: Path) -> str:
    """构建测试用 SQLite 数据库连接地址。

    :param database_path: SQLite 数据库文件路径。
    :return: SQLAlchemy 可消费的 SQLite 连接地址。
    """

    return f"sqlite:///{database_path}"


def _build_alembic_config() -> Config:
    """构建 Alembic 测试配置。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def _upgrade_to_head(
    *,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """将临时数据库迁移到最新 schema。

    :param monkeypatch: pytest 环境变量补丁工具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
    command.upgrade(_build_alembic_config(), "head")


def _state_from_app(app: FastAPI) -> VeterinaryAgentAppState:
    """从 FastAPI app.state 读取兽医 Agent 应用状态。

    :param app: FastAPI 应用实例。
    :return: 兽医 Agent 应用状态。
    """

    state = getattr(app.state, "veterinary_agent_state")
    assert isinstance(state, VeterinaryAgentAppState)
    return state


def _valid_payload() -> dict[str, object]:
    """构建可进入真实主业务图的最小同步请求。

    :return: 最小合法一轮兽医对话请求体。
    """

    return {
        "request_id": "req_real_lifespan",
        "trace_id": "trace_real_lifespan",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "小狗今天精神一般，需要先观察哪些症状？",
                    }
                ],
            }
        ],
        "vet_context": {
            "user_id": "user_real",
            "session_id": "session_real",
            "pet_id": "pet_real",
        },
    }


def test_lifespan_builds_real_graph_runtime_and_handles_sync_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证存在 DATABASE_URL 时默认 lifespan 会接入真实主业务图并承载同步请求。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest 环境变量补丁工具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "real_runtime_chain.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    checkpoint_provider = _InMemoryCheckpointProvider()
    app = create_app(checkpoint_provider_factory=lambda: checkpoint_provider)

    with TestClient(app) as client:
        state = _state_from_app(cast(FastAPI, client.app))
        assert state.checkpoint_store_ready is True
        assert state.graph_runtime_ready is True
        assert state.agent_application_service_ready is True
        assert isinstance(state.graph_runtime, DefaultGraphRuntime)
        ready_response = client.get("/ready")
        turn_response = client.post("/agent/turns", json=_valid_payload())

    assert ready_response.status_code == 200
    assert turn_response.status_code == 200
    body = turn_response.json()
    assert body["object"] == "agent.turn"
    assert body["status"] == "completed"
    assert isinstance(body["output"], list)
    assert checkpoint_provider.stopped is True
