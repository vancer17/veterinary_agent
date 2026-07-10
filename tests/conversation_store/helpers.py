##################################################################################################
# 文件: tests/conversation_store/helpers.py
# 作用: 提供 ConversationStore 组件测试内部复用的临时数据库、Alembic 迁移和默认 session 构造辅助函数。
# 边界: 仅服务 tests/conversation_store 测试包；不作为生产代码或跨组件测试公共工具暴露。
##################################################################################################

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from veterinary_agent.conversation_store import (
    ConversationStore,
    ConversationStoreSettings,
    EnsureSessionCommandDto,
    SqlAlchemyConversationStore,
    create_sqlalchemy_conversation_store,
)


def build_alembic_config() -> Config:
    """构建组件测试用 Alembic 配置对象。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def build_sqlite_database_url(database_path: Path) -> str:
    """构建组件测试用 SQLite 数据库连接地址。

    :param database_path: 临时 SQLite 数据库文件路径。
    :return: SQLAlchemy 可使用的 SQLite 数据库 URL。
    """

    return f"sqlite:///{database_path}"


def upgrade_to_head(
    *,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行项目 Alembic migration 到最新版本。

    :param monkeypatch: pytest monkeypatch 夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(build_alembic_config(), "head")


def create_migrated_conversation_store(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_name: str = "conversation.sqlite3",
    settings: ConversationStoreSettings | None = None,
) -> Iterator[ConversationStore]:
    """创建已完成迁移的测试用 SQLAlchemy ConversationStore。

    :param tmp_path: pytest 提供的临时目录。
    :param monkeypatch: pytest monkeypatch 夹具。
    :param database_name: 临时 SQLite 数据库文件名。
    :param settings: 可选 ConversationStore RuntimeConfig；未传入时使用默认配置。
    :return: ConversationStore 迭代器，退出时释放连接池。
    """

    database_url = build_sqlite_database_url(tmp_path / database_name)
    upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_conversation_store(database_url, settings=settings)
    try:
        yield store
    finally:
        if isinstance(store, SqlAlchemyConversationStore):
            store.dispose()


async def ensure_default_session(store: ConversationStore) -> None:
    """创建测试默认 session。

    :param store: ConversationStore 实例。
    :return: None。
    """

    await store.ensure_session(
        EnsureSessionCommandDto(
            request_id="req_1",
            trace_id="trace_1",
            session_id="session_1",
            user_id="user_1",
            pet_id="pet_1",
        )
    )


def ensure_default_session_sync(store: ConversationStore) -> None:
    """同步创建测试默认 session。

    :param store: ConversationStore 实例。
    :return: None。
    """

    asyncio.run(ensure_default_session(store))
