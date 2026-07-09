##################################################################################################
# 文件: tests/checkpoint_store/test_segment_publish.py
# 作用: 验证 CheckpointStore MarkSegmentPublished 的数据库控制面实现。
# 边界: 仅使用临时 SQLite 数据库验证 segment 发布幂等行为，不连接真实 PostgreSQL、不调用 LangGraph。
##################################################################################################

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, RowMapping

from veterinary_agent.checkpoint_store import (
    CHECKPOINT_SEGMENT_PUBLISH_TABLE,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    DATABASE_URL_ENV_NAME,
    EnsureThreadCommandDto,
    LoadLatestCheckpointQueryDto,
    MarkSegmentPublishedCommandDto,
    SegmentPublishStatus,
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

    monkeypatch.setenv(DATABASE_URL_ENV_NAME, database_url)
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


def _build_mark_segment_command(
    *,
    run_id: str = "run_1",
    segment_id: str = "segment_1",
    task_id: str | None = "task_1",
    published_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> MarkSegmentPublishedCommandDto:
    """构建测试用 MarkSegmentPublished 命令。

    :param run_id: 测试图执行轮次 ID。
    :param segment_id: 测试业务分段 ID。
    :param task_id: 测试业务子任务 ID。
    :param published_at: segment 发布成功时间；为空时使用固定测试时间。
    :param metadata: segment 发布元信息；为空时使用固定测试元信息。
    :return: 测试用 MarkSegmentPublished 命令 DTO。
    """

    return MarkSegmentPublishedCommandDto(
        request_id="req_mark",
        trace_id="trace_mark",
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id=run_id,
        segment_id=segment_id,
        task_id=task_id,
        published_at=published_at or datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        metadata=metadata or {"message_ref": "message_1"},
    )


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def _fetch_segment_row(
    *,
    engine: Engine,
    thread_id: str,
    segment_id: str,
) -> RowMapping:
    """读取测试数据库中的 segment 发布事实行。

    :param engine: SQLAlchemy 数据库引擎。
    :param thread_id: segment 所属 checkpoint thread ID。
    :param segment_id: 需要读取的业务分段 ID。
    :return: 命中的 checkpoint_segment_publish 行。
    """

    with engine.begin() as connection:
        return (
            connection.execute(
                CHECKPOINT_SEGMENT_PUBLISH_TABLE.select().where(
                    CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.thread_id == thread_id,
                    CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.segment_id == segment_id,
                )
            )
            .mappings()
            .one()
        )


def test_mark_segment_published_inserts_first_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首次标记 segment 已发布会插入发布事实。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "mark_insert.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        result = asyncio.run(
            store.mark_segment_published(_build_mark_segment_command())
        )

        assert result.idempotent is False
        assert result.segment.segment_id == "segment_1"
        assert result.segment.task_id == "task_1"
        assert result.segment.status is SegmentPublishStatus.PUBLISHED
        assert result.segment.metadata == {"message_ref": "message_1"}
        assert result.segment.published_at == datetime(
            2026,
            7,
            8,
            12,
            0,
            tzinfo=UTC,
        )
        assert thread.thread_id == build_checkpoint_thread_id(session_id="session_1")
    finally:
        store.dispose()


def test_mark_segment_published_is_idempotent_for_existing_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证重复标记同一 segment 会返回既有发布事实且不覆盖原始记录。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "mark_idempotent.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    engine = _open_engine(database_url)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        first = asyncio.run(store.mark_segment_published(_build_mark_segment_command()))
        second = asyncio.run(
            store.mark_segment_published(
                _build_mark_segment_command(
                    run_id="run_2",
                    published_at=datetime(2026, 7, 8, 12, 5, tzinfo=UTC),
                    metadata={"message_ref": "message_2"},
                )
            )
        )
        row = _fetch_segment_row(
            engine=engine,
            thread_id=build_checkpoint_thread_id(session_id="session_1"),
            segment_id="segment_1",
        )

        assert first.idempotent is False
        assert second.idempotent is True
        assert second.segment == first.segment
        assert row["run_id"] == "run_1"
        assert row["metadata"] == {"message_ref": "message_1"}
    finally:
        engine.dispose()
        store.dispose()


def test_mark_segment_published_rejects_missing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 不存在时不能标记 segment 已发布。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_thread.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.mark_segment_published(_build_mark_segment_command()))

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND
        assert exc_info.value.operation is CheckpointOperation.MARK_SEGMENT_PUBLISHED
        assert exc_info.value.retryable is False
    finally:
        store.dispose()


def test_mark_segment_published_is_visible_to_latest_checkpoint_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证写入发布事实后读取最新 checkpoint 可返回已发布 segment。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "latest_segments.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        asyncio.run(store.mark_segment_published(_build_mark_segment_command()))
        result = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_latest",
                    trace_id="trace_latest",
                    thread_id=thread.thread_id,
                )
            )
        )

        assert result.checkpoint is None
        assert result.latest_version == 0
        assert [segment.segment_id for segment in result.published_segments] == [
            "segment_1"
        ]
        assert result.published_segments[0].status is SegmentPublishStatus.PUBLISHED
    finally:
        store.dispose()
