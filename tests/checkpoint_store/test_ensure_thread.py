##################################################################################################
# 文件: tests/checkpoint_store/test_ensure_thread.py
# 作用: 验证 CheckpointStore EnsureThread 的数据库控制面实现。
# 边界: 仅使用临时 SQLite 数据库验证 checkpoint_thread 行为，不连接真实 PostgreSQL、不调用 LangGraph。
##################################################################################################

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    EnsureThreadCommandDto,
    build_checkpoint_thread_id,
    create_sqlalchemy_checkpoint_store,
)


def _build_alembic_config() -> Config:
    """构建测试用 Alembic 配置对象。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def _build_sqlite_database_url(database_path: Path) -> str:
    """构建临时 SQLite 数据库连接地址。

    :param database_path: 临时 SQLite 数据库文件路径。
    :return: SQLAlchemy 可使用的 SQLite 数据库 URL。
    """

    return f"sqlite:///{database_path}"


def _upgrade_to_head(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行 Alembic upgrade head。

    :param monkeypatch: pytest 环境变量修改夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(_build_alembic_config(), "head")


def _build_command(
    *,
    session_id: str = "session_1",
    user_id: str = "user_1",
    pet_id: str | None = "pet_1",
) -> EnsureThreadCommandDto:
    """构建测试用 EnsureThread 命令。

    :param session_id: 测试会话 ID。
    :param user_id: 测试用户 ID。
    :param pet_id: 测试宠物 ID；为空表示未锚定宠物。
    :return: 测试用 EnsureThread 命令 DTO。
    """

    return EnsureThreadCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id=session_id,
        user_id=user_id,
        pet_id=pet_id,
    )


def test_ensure_thread_creates_and_reads_existing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 EnsureThread 首次创建 thread，重复调用幂等返回既有 thread。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "ensure_thread.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        command = _build_command()

        first_result = asyncio.run(store.ensure_thread(command))
        second_result = asyncio.run(store.ensure_thread(command))

        assert first_result.created_new is True
        assert second_result.created_new is False
        assert first_result.thread.thread_id == build_checkpoint_thread_id(
            session_id="session_1"
        )
        assert second_result.thread.thread_id == first_result.thread.thread_id
        assert second_result.thread.latest_version == 0
        assert second_result.thread.latest_checkpoint_id is None
    finally:
        store.dispose()


def test_ensure_thread_binds_pet_when_existing_thread_is_unanchored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已有 thread 尚未锚定宠物时，EnsureThread 可绑定本次 pet_id。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "bind_pet.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        first_result = asyncio.run(store.ensure_thread(_build_command(pet_id=None)))
        second_result = asyncio.run(store.ensure_thread(_build_command(pet_id="pet_1")))
        third_result = asyncio.run(store.ensure_thread(_build_command(pet_id=None)))

        assert first_result.created_new is True
        assert first_result.thread.pet_id is None
        assert second_result.created_new is False
        assert second_result.thread.pet_id == "pet_1"
        assert third_result.thread.pet_id == "pet_1"
    finally:
        store.dispose()


def test_ensure_thread_rejects_pet_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 EnsureThread 拒绝同一 session 切换到不同 pet_id。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "pet_conflict.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_command(pet_id="pet_1")))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.ensure_thread(_build_command(pet_id="pet_2")))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_PET_CONFLICT
        assert error.operation is CheckpointOperation.ENSURE_THREAD
        assert error.retryable is False
    finally:
        store.dispose()


def test_ensure_thread_rejects_user_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 EnsureThread 拒绝同一 session 被不同 user_id 复用。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "user_conflict.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_command(user_id="user_1")))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.ensure_thread(_build_command(user_id="user_2")))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        assert error.operation is CheckpointOperation.ENSURE_THREAD
        assert error.retryable is False
    finally:
        store.dispose()
