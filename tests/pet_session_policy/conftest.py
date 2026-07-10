##################################################################################################
# 文件: tests/pet_session_policy/conftest.py
# 作用: 定义 PetSessionPolicy 组件级测试使用的真实 ConversationStore 与 RuntimeConfig 夹具。
# 边界: 仅创建临时 SQLite 存储和内存配置快照；不启动 FastAPI、LogicTraceStore 或业务图。
##################################################################################################

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from veterinary_agent.config import (
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.conversation_store import (
    CONVERSATION_STORE_METADATA,
    ConversationStore,
    SqlAlchemyConversationStore,
    create_sqlalchemy_conversation_store,
)


@pytest.fixture()
def runtime_config_provider() -> RuntimeConfigProvider:
    """创建 PetSessionPolicy 测试使用的有效 RuntimeConfig provider。

    :return: 持有默认有效配置快照的 RuntimeConfig provider。
    """

    return create_runtime_config_provider()


@pytest.fixture()
def conversation_store(tmp_path: Path) -> Iterator[ConversationStore]:
    """创建带完整 ConversationStore 表结构的临时 SQLite store。

    :param tmp_path: pytest 提供的临时目录。
    :return: 测试用 ConversationStore 迭代器。
    """

    database_url = f"sqlite:///{tmp_path / 'pet_session_policy.sqlite3'}"
    schema_engine = create_engine(database_url)
    CONVERSATION_STORE_METADATA.create_all(schema_engine)
    schema_engine.dispose()
    store = create_sqlalchemy_conversation_store(database_url)
    try:
        yield store
    finally:
        if isinstance(store, SqlAlchemyConversationStore):
            store.dispose()
