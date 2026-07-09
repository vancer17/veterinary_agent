##################################################################################################
# 文件: tests/checkpoint_store/test_run_lock.py
# 作用: 验证 CheckpointStore AcquireRunLock / ReleaseRunLock 的数据库控制面实现。
# 边界: 仅使用临时 SQLite 数据库验证运行锁行为，不连接真实 PostgreSQL、不调用 LangGraph。
##################################################################################################

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from veterinary_agent.checkpoint_store import (
    AcquireRunLockCommandDto,
    CHECKPOINT_RUN_LOCK_TABLE,
    CHECKPOINT_THREAD_TABLE,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    CheckpointThreadStatus,
    EnsureThreadCommandDto,
    ReleaseRunLockCommandDto,
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


def _build_ensure_thread_command() -> EnsureThreadCommandDto:
    """构建测试用 EnsureThread 命令。

    :return: 测试用 EnsureThread 命令 DTO。
    """

    return EnsureThreadCommandDto(
        request_id="req_ensure",
        trace_id="trace_ensure",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
    )


def _build_acquire_command(
    *,
    run_id: str = "run_1",
    ttl_seconds: float = 60.0,
) -> AcquireRunLockCommandDto:
    """构建测试用 AcquireRunLock 命令。

    :param run_id: 测试运行轮次 ID。
    :param ttl_seconds: 测试运行锁 TTL 秒数。
    :return: 测试用 AcquireRunLock 命令 DTO。
    """

    return AcquireRunLockCommandDto(
        request_id="req_lock",
        trace_id="trace_lock",
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id=run_id,
        lock_ttl_seconds=ttl_seconds,
    )


def _build_release_command(
    *,
    run_id: str = "run_1",
) -> ReleaseRunLockCommandDto:
    """构建测试用 ReleaseRunLock 命令。

    :param run_id: 测试运行轮次 ID。
    :return: 测试用 ReleaseRunLock 命令 DTO。
    """

    return ReleaseRunLockCommandDto(
        request_id="req_release",
        trace_id="trace_release",
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id=run_id,
    )


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def _fetch_thread_status(
    *,
    engine: Engine,
    thread_id: str,
) -> str:
    """读取指定 thread 的当前状态。

    :param engine: SQLAlchemy 数据库引擎。
    :param thread_id: 需要读取状态的 checkpoint thread ID。
    :return: checkpoint thread 当前状态字符串。
    """

    with engine.begin() as connection:
        row = (
            connection.execute(
                CHECKPOINT_THREAD_TABLE.select().where(
                    CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id
                )
            )
            .mappings()
            .one()
        )
    return str(row["status"])


def _expire_existing_lock(
    *,
    engine: Engine,
    thread_id: str,
) -> None:
    """将指定 thread 的运行锁改为已过期状态。

    :param engine: SQLAlchemy 数据库引擎。
    :param thread_id: 需要改写运行锁的 checkpoint thread ID。
    :return: None。
    """

    expired_at = datetime.now(UTC) - timedelta(seconds=10)
    with engine.begin() as connection:
        connection.execute(
            CHECKPOINT_RUN_LOCK_TABLE.update()
            .where(CHECKPOINT_RUN_LOCK_TABLE.c.thread_id == thread_id)
            .values(expires_at=expired_at)
        )


def test_acquire_run_lock_creates_lock_and_marks_thread_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首次获取运行锁会创建锁并将 thread 标记为 running。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "acquire.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    engine = _open_engine(database_url)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        result = asyncio.run(store.acquire_run_lock(_build_acquire_command()))

        assert result.lock_acquired is True
        assert result.idempotent is False
        assert result.stale_lock_replaced is False
        assert result.thread_id == thread.thread_id
        assert _fetch_thread_status(
            engine=engine,
            thread_id=thread.thread_id,
        ) == CheckpointThreadStatus.RUNNING.value
    finally:
        engine.dispose()
        store.dispose()


def test_acquire_run_lock_is_idempotent_for_same_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证同一 run 重复获取运行锁会幂等成功并刷新过期时间。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "idempotent.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        first_result = asyncio.run(
            store.acquire_run_lock(_build_acquire_command(ttl_seconds=10.0))
        )
        second_result = asyncio.run(
            store.acquire_run_lock(_build_acquire_command(ttl_seconds=120.0))
        )

        assert first_result.idempotent is False
        assert second_result.idempotent is True
        assert second_result.expires_at > first_result.expires_at
    finally:
        store.dispose()


def test_acquire_run_lock_rejects_other_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证运行锁被其他未过期 run 持有时会拒绝获取。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "locked.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        asyncio.run(store.acquire_run_lock(_build_acquire_command(run_id="run_1")))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.acquire_run_lock(_build_acquire_command(run_id="run_2"))
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_LOCKED
        assert error.operation is CheckpointOperation.ACQUIRE_RUN_LOCK
        assert error.retryable is True
    finally:
        store.dispose()


def test_acquire_run_lock_replaces_stale_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证其他 run 的过期锁可被当前 run 抢占。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "stale.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    engine = _open_engine(database_url)
    thread_id = build_checkpoint_thread_id(session_id="session_1")
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        asyncio.run(store.acquire_run_lock(_build_acquire_command(run_id="run_1")))
        _expire_existing_lock(engine=engine, thread_id=thread_id)

        result = asyncio.run(
            store.acquire_run_lock(_build_acquire_command(run_id="run_2"))
        )

        assert result.lock_acquired is True
        assert result.idempotent is False
        assert result.stale_lock_replaced is True
        assert result.run_id == "run_2"
    finally:
        engine.dispose()
        store.dispose()


def test_acquire_run_lock_rejects_missing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 不存在时获取运行锁会返回稳定错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.acquire_run_lock(_build_acquire_command()))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND
        assert error.operation is CheckpointOperation.ACQUIRE_RUN_LOCK
        assert error.retryable is False
    finally:
        store.dispose()


def test_release_run_lock_releases_owned_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证当前 run 可释放自己持有的运行锁。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "release.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        asyncio.run(store.acquire_run_lock(_build_acquire_command(run_id="run_1")))
        result = asyncio.run(
            store.release_run_lock(_build_release_command(run_id="run_1"))
        )

        assert result.released is True
        assert result.idempotent is False
    finally:
        store.dispose()


def test_release_run_lock_is_idempotent_when_lock_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证运行锁不存在时释放操作幂等成功。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "release_missing.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        result = asyncio.run(
            store.release_run_lock(_build_release_command(run_id="run_1"))
        )

        assert result.released is False
        assert result.idempotent is True
    finally:
        store.dispose()


def test_release_run_lock_rejects_owner_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证旧 run 不能释放其他 run 当前持有的运行锁。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "owner_mismatch.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        asyncio.run(store.acquire_run_lock(_build_acquire_command(run_id="run_2")))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.release_run_lock(_build_release_command(run_id="run_1"))
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_LOCK_OWNER_MISMATCH
        assert error.operation is CheckpointOperation.RELEASE_RUN_LOCK
        assert error.retryable is False
    finally:
        store.dispose()


def test_release_run_lock_rejects_missing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 不存在时释放运行锁会返回稳定错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "release_no_thread.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.release_run_lock(_build_release_command()))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND
        assert error.operation is CheckpointOperation.RELEASE_RUN_LOCK
        assert error.retryable is False
    finally:
        store.dispose()
