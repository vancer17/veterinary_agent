##################################################################################################
# 文件: tests/checkpoint_store/test_thread_version.py
# 作用: 验证 CheckpointStore thread version / optimistic lock 的数据库控制面实现。
# 边界: 仅使用临时 SQLite 数据库验证 checkpoint_thread 版本推进，不调用 LangGraph 或 GraphRuntime。
##################################################################################################

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, RowMapping

from veterinary_agent.checkpoint_store import (
    CHECKPOINT_THREAD_TABLE,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
    CheckpointStoreError,
    EnsureThreadCommandDto,
    GraphExecutionStateDto,
    SaveCheckpointCommandDto,
    SessionBusinessStateDto,
    SqlAlchemyCheckpointVersionRepository,
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


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


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


def _build_save_checkpoint_command(
    *,
    expected_version: int,
    session_id: str = "session_1",
    status: CheckpointRecordStatus = CheckpointRecordStatus.RECOVERABLE,
) -> SaveCheckpointCommandDto:
    """构建测试用 SaveCheckpoint 命令。

    :param expected_version: 调用方预期的 thread 最新版本。
    :param session_id: 命令携带的会话 ID。
    :param status: 本次 checkpoint 快照状态。
    :return: 测试用 SaveCheckpoint 命令 DTO。
    """

    return SaveCheckpointCommandDto(
        request_id="req_save",
        trace_id="trace_save",
        session_id=session_id,
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id="run_1",
        expected_version=expected_version,
        graph_name="vet_main_graph",
        graph_version="graph.v1",
        state_schema_version="checkpoint.v1",
        status=status,
        current_node="node_a",
        graph_state=GraphExecutionStateDto(current_node="node_a"),
        business_state=SessionBusinessStateDto(pet_id="pet_1"),
        metadata={"state_hash": "hash_1"},
    )


def _ensure_thread_exists(database_url: str) -> None:
    """通过真实 CheckpointStore 创建测试 thread。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: None。
    """

    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
    finally:
        store.dispose()


def _fetch_thread_row(
    *,
    engine: Engine,
    thread_id: str,
) -> RowMapping:
    """读取指定 checkpoint thread 数据库行。

    :param engine: SQLAlchemy 数据库引擎。
    :param thread_id: 需要读取的 checkpoint thread ID。
    :return: 命中的 checkpoint_thread 行。
    """

    with engine.begin() as connection:
        return (
            connection.execute(
                CHECKPOINT_THREAD_TABLE.select().where(
                    CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id
                )
            )
            .mappings()
            .one()
        )


def test_advance_thread_version_updates_latest_version_and_checkpoint_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 expected_version 匹配时可推进 latest_version 并更新 checkpoint 指针。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "advance.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    thread_id = build_checkpoint_thread_id(session_id="session_1")
    try:
        result = repository.advance_thread_version(
            command=_build_save_checkpoint_command(
                expected_version=0,
                status=CheckpointRecordStatus.COMPLETED,
            ),
            checkpoint_id="checkpoint_1",
            state_size_bytes=123,
        )
        thread_row = _fetch_thread_row(engine=engine, thread_id=thread_id)

        assert result.new_version == 1
        assert result.checkpoint_id == "checkpoint_1"
        assert result.status is CheckpointRecordStatus.COMPLETED
        assert int(thread_row["latest_version"]) == 1
        assert thread_row["latest_checkpoint_id"] == "checkpoint_1"
        assert thread_row["status"] == CheckpointRecordStatus.COMPLETED.value
    finally:
        engine.dispose()


def test_advance_thread_version_can_increment_multiple_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证连续使用正确 expected_version 可以多次递增版本。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "increment.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        first_result = repository.advance_thread_version(
            command=_build_save_checkpoint_command(expected_version=0),
            checkpoint_id="checkpoint_1",
            state_size_bytes=10,
        )
        second_result = repository.advance_thread_version(
            command=_build_save_checkpoint_command(expected_version=1),
            checkpoint_id="checkpoint_2",
            state_size_bytes=20,
        )

        assert first_result.new_version == 1
        assert second_result.new_version == 2
    finally:
        engine.dispose()


def test_advance_thread_version_rejects_stale_expected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证落后的 expected_version 会触发版本冲突。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "stale_version.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        repository.advance_thread_version(
            command=_build_save_checkpoint_command(expected_version=0),
            checkpoint_id="checkpoint_1",
            state_size_bytes=10,
        )

        with pytest.raises(CheckpointStoreError) as exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(expected_version=0),
                checkpoint_id="checkpoint_2",
                state_size_bytes=10,
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert error.retryable is True
        assert error.conflict_with is not None
        assert error.conflict_with["actual_version"] == 1
    finally:
        engine.dispose()


def test_advance_thread_version_rejects_future_expected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证超前的 expected_version 也会触发版本冲突。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "future_version.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(expected_version=2),
                checkpoint_id="checkpoint_future",
                state_size_bytes=10,
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT
        assert error.conflict_with is not None
        assert error.conflict_with["actual_version"] == 0
    finally:
        engine.dispose()


def test_advance_thread_version_rejects_missing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 不存在时版本推进返回稳定错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_thread.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(expected_version=0),
                checkpoint_id="checkpoint_missing",
                state_size_bytes=10,
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert error.retryable is False
    finally:
        engine.dispose()


def test_advance_thread_version_rejects_session_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证命令 session_id 与 thread 锚定 session 不一致时拒绝版本推进。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_mismatch.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(
                    expected_version=0,
                    session_id="session_2",
                ),
                checkpoint_id="checkpoint_bad_session",
                state_size_bytes=10,
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert error.retryable is False
    finally:
        engine.dispose()


def test_advance_thread_version_rejects_invalid_extra_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证版本推进拒绝非法 checkpoint_id 与状态体大小。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "invalid_args.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    _ensure_thread_exists(database_url)
    engine = _open_engine(database_url)
    repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
    try:
        with pytest.raises(CheckpointStoreError) as empty_checkpoint_exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(expected_version=0),
                checkpoint_id=" ",
                state_size_bytes=10,
            )
        with pytest.raises(CheckpointStoreError) as size_exc_info:
            repository.advance_thread_version(
                command=_build_save_checkpoint_command(expected_version=0),
                checkpoint_id="checkpoint_1",
                state_size_bytes=-1,
            )

        assert empty_checkpoint_exc_info.value.code is (
            CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        )
        assert (
            size_exc_info.value.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        )
    finally:
        engine.dispose()
