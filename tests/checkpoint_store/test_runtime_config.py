##################################################################################################
# 文件: tests/checkpoint_store/test_runtime_config.py
# 作用: 验证 CheckpointStore RuntimeConfig 加载与已接入配置项的运行效果。
# 边界: 使用临时 SQLite 与 fake LangGraph backend 验证配置约束，不连接真实 PostgreSQL、不执行业务编排。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from veterinary_agent.checkpoint_store import (
    AcquireRunLockCommandDto,
    CheckpointStoreCheckpointConfig,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
    CheckpointStoreError,
    CheckpointStoreHistoryConfig,
    CheckpointStoreRunLockConfig,
    CheckpointStoreSchemaConfig,
    CheckpointStoreSegmentPublishConfig,
    CheckpointStoreSettings,
    EnsureThreadCommandDto,
    GraphExecutionStateDto,
    LangGraphCheckpointReader,
    LangGraphCheckpointWriter,
    LangGraphRunnableConfig,
    ListCheckpointsQueryDto,
    LoadLatestCheckpointQueryDto,
    MarkSegmentPublishedCommandDto,
    SaveCheckpointCommandDto,
    SessionBusinessStateDto,
    SqlAlchemyCheckpointStore,
    SqlAlchemyCheckpointVersionRepository,
    build_checkpoint_thread_id,
    create_sqlalchemy_checkpoint_store,
    load_checkpoint_store_settings,
)
from sqlalchemy import create_engine


class _RuntimeConfigFakeLangGraphBackend:
    """测试 RuntimeConfig 用 LangGraph checkpoint 读取后端。"""

    def __init__(self, tuples: list[CheckpointTuple]) -> None:
        """初始化测试用 LangGraph checkpoint 读取后端。

        :param tuples: 可被 fake 后端读取的 checkpoint tuple 列表。
        :return: None。
        """

        self._tuples = tuples

    async def aput(
        self,
        config: LangGraphRunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> LangGraphRunnableConfig:
        """写入测试用 checkpoint tuple。

        :param config: LangGraph thread 运行配置。
        :param checkpoint: 需要保存的 checkpoint 状态体。
        :param metadata: 需要保存的 checkpoint metadata。
        :param new_versions: 本次写入更新的 channel version 映射。
        :return: 写入后的 LangGraph 运行配置。
        """

        del new_versions
        configurable = config["configurable"]
        tuple_config = {
            "configurable": {
                "thread_id": configurable["thread_id"],
                "checkpoint_ns": configurable.get("checkpoint_ns", ""),
                "checkpoint_id": str(checkpoint["id"]),
            }
        }
        self._tuples.insert(
            0,
            CheckpointTuple(
                config=tuple_config,
                checkpoint=checkpoint,
                metadata=metadata,
            ),
        )
        return cast(LangGraphRunnableConfig, tuple_config)

    async def aget_tuple(
        self,
        config: LangGraphRunnableConfig,
    ) -> CheckpointTuple | None:
        """按配置读取单个 checkpoint tuple。

        :param config: LangGraph thread/checkpoint 运行配置。
        :return: 命中的 checkpoint tuple；不存在时返回 None。
        """

        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        checkpoint_id = configurable.get("checkpoint_id")
        for checkpoint_tuple in self._tuples:
            tuple_configurable = cast(
                dict[str, str],
                checkpoint_tuple.config.get("configurable", {}),
            )
            if tuple_configurable["thread_id"] != thread_id:
                continue
            if checkpoint_id is None or tuple_configurable["checkpoint_id"] == checkpoint_id:
                return checkpoint_tuple
        return None

    async def alist(
        self,
        config: LangGraphRunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: LangGraphRunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """按配置列出 checkpoint tuple。

        :param config: LangGraph thread 运行配置。
        :param filter: 可选 metadata 过滤条件。
        :param before: 可选 checkpoint 游标配置。
        :param limit: 可选最大返回条数。
        :return: checkpoint tuple 异步迭代器。
        """

        del filter, before
        thread_id = None if config is None else config["configurable"]["thread_id"]
        count = 0
        for checkpoint_tuple in self._tuples:
            tuple_configurable = cast(
                dict[str, str],
                checkpoint_tuple.config.get("configurable", {}),
            )
            if thread_id is not None and tuple_configurable["thread_id"] != thread_id:
                continue
            if limit is not None and count >= limit:
                return
            count += 1
            yield checkpoint_tuple


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


def _build_runtime_settings(
    *,
    supported_versions: list[str] | None = None,
    max_list_limit: int = 100,
    min_ttl_seconds: float = 1.0,
    max_ttl_seconds: float = 900.0,
    max_checkpoint_state_bytes: int = 262_144,
    max_segment_metadata_bytes: int = 16_384,
) -> CheckpointStoreSettings:
    """构建测试用 CheckpointStore RuntimeConfig。

    :param supported_versions: 支持的 checkpoint 状态 schema 版本列表。
    :param max_list_limit: checkpoint 历史查询最大 limit。
    :param min_ttl_seconds: 运行锁最小 TTL 秒数。
    :param max_ttl_seconds: 运行锁最大 TTL 秒数。
    :param max_checkpoint_state_bytes: checkpoint 状态体最大字节数。
    :param max_segment_metadata_bytes: segment 发布 metadata 最大字节数。
    :return: 测试用 CheckpointStore RuntimeConfig。
    """

    return CheckpointStoreSettings(
        state_schema=CheckpointStoreSchemaConfig(
            supported_state_schema_versions=supported_versions or ["checkpoint.v1"]
        ),
        run_lock=CheckpointStoreRunLockConfig(
            min_ttl_seconds=min_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
        ),
        history=CheckpointStoreHistoryConfig(max_list_limit=max_list_limit),
        checkpoint=CheckpointStoreCheckpointConfig(
            max_state_bytes=max_checkpoint_state_bytes
        ),
        segment_publish=CheckpointStoreSegmentPublishConfig(
            max_metadata_bytes=max_segment_metadata_bytes
        ),
    )


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


def _build_save_command() -> SaveCheckpointCommandDto:
    """构建测试用 SaveCheckpoint 命令。

    :return: 测试用 SaveCheckpoint 命令 DTO。
    """

    return SaveCheckpointCommandDto(
        request_id="req_save",
        trace_id="trace_save",
        session_id="session_1",
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id="run_1",
        expected_version=0,
        graph_name="vet_main_graph",
        graph_version="graph.v1",
        state_schema_version="checkpoint.v1",
        status=CheckpointRecordStatus.RECOVERABLE,
        current_node="node_a",
        graph_state=GraphExecutionStateDto(current_node="node_a"),
        business_state=SessionBusinessStateDto(pet_id="pet_1"),
    )


def _build_large_save_command() -> SaveCheckpointCommandDto:
    """构建状态体较大的测试用 SaveCheckpoint 命令。

    :return: 状态体较大的 SaveCheckpoint 命令 DTO。
    """

    command = _build_save_command()
    return command.model_copy(
        update={
            "metadata": {
                "large_summary": "x" * 512,
            }
        }
    )


def _acquire_default_run_lock(
    *,
    store: SqlAlchemyCheckpointStore,
    thread_id: str,
) -> None:
    """为测试 thread 获取默认运行锁。

    :param store: 测试用 CheckpointStore。
    :param thread_id: 需要加锁的 checkpoint thread ID。
    :return: None。
    """

    asyncio.run(
        store.acquire_run_lock(
            AcquireRunLockCommandDto(
                request_id="req_lock",
                trace_id="trace_lock",
                thread_id=thread_id,
                run_id="run_1",
                lock_ttl_seconds=60,
            )
        )
    )


def _advance_thread_version(
    *,
    database_url: str,
    checkpoint_id: str,
) -> None:
    """推进测试 thread 的项目级 checkpoint 版本。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param checkpoint_id: 需要写入 latest_checkpoint_id 的 checkpoint ID。
    :return: None。
    """

    engine = create_engine(database_url)
    try:
        SqlAlchemyCheckpointVersionRepository(engine=engine).advance_thread_version(
            command=_build_save_command(),
            checkpoint_id=checkpoint_id,
            state_size_bytes=100,
        )
    finally:
        engine.dispose()


def _build_checkpoint_tuple(
    *,
    thread_id: str,
    checkpoint_id: str,
    state_schema_version: str,
) -> CheckpointTuple:
    """构建测试用 LangGraph checkpoint tuple。

    :param thread_id: checkpoint 所属 thread ID。
    :param checkpoint_id: checkpoint ID。
    :param state_schema_version: checkpoint 状态 schema 版本。
    :return: 测试用 LangGraph checkpoint tuple。
    """

    created_at = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    metadata: CheckpointMetadata = cast(
        CheckpointMetadata,
        {
            "checkpoint_store": {
                "checkpoint_store_managed": True,
                "version": 1,
                "run_id": "run_1",
                "graph_name": "vet_main_graph",
                "graph_version": "graph.v1",
                "state_schema_version": state_schema_version,
                "status": CheckpointRecordStatus.RECOVERABLE.value,
                "current_node": "node_a",
                "state_size_bytes": 100,
                "created_at": created_at.isoformat(),
                "metadata": {},
            }
        },
    )
    checkpoint: Checkpoint = cast(
        Checkpoint,
        {
            "v": 4,
            "id": checkpoint_id,
            "ts": created_at.isoformat(),
            "channel_values": {
                "graph_state": {"current_node": "node_a"},
                "business_state": {"pet_id": "pet_1"},
            },
            "channel_versions": {},
            "versions_seen": {},
            "updated_channels": None,
        },
    )
    return CheckpointTuple(
        config={
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        },
        checkpoint=checkpoint,
        metadata=metadata,
    )


def test_load_checkpoint_store_settings_reads_yaml(
    tmp_path: Path,
) -> None:
    """验证 CheckpointStore RuntimeConfig 可从 YAML 文件加载。

    :param tmp_path: pytest 临时目录夹具。
    :return: None。
    """

    config_path = tmp_path / "checkpoint_store.yaml"
    config_path.write_text(
        "\n".join(
            [
                "operation_timeout_seconds: 3.5",
                "state_schema:",
                "  supported_state_schema_versions:",
                "    - checkpoint.test",
                "run_lock:",
                "  min_ttl_seconds: 2.0",
                "  max_ttl_seconds: 30.0",
                "history:",
                "  max_list_limit: 7",
                "checkpoint:",
                "  max_state_bytes: 256",
                "segment_publish:",
                "  max_metadata_bytes: 128",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_checkpoint_store_settings(config_path)

    assert settings.operation_timeout_seconds == 3.5
    assert settings.state_schema.supported_state_schema_versions == ["checkpoint.test"]
    assert settings.run_lock.min_ttl_seconds == 2.0
    assert settings.history.max_list_limit == 7
    assert settings.checkpoint.max_state_bytes == 256
    assert settings.segment_publish.max_metadata_bytes == 128


def test_checkpoint_store_settings_rejects_invalid_ttl_range() -> None:
    """验证 RuntimeConfig 会拒绝运行锁 TTL 上下界倒挂。

    :return: None。
    """

    with pytest.raises(ValueError, match="min_ttl_seconds"):
        _build_runtime_settings(min_ttl_seconds=60.0, max_ttl_seconds=10.0)


def test_runtime_config_rejects_unsupported_schema_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 会驱动读取侧拒绝未登记的状态 schema 版本。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "schema.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    settings = _build_runtime_settings(supported_versions=["checkpoint.v2"])
    store = create_sqlalchemy_checkpoint_store(database_url, settings=settings)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        _advance_thread_version(
            database_url=database_url,
            checkpoint_id="checkpoint_1",
        )
    finally:
        store.dispose()
    reader = LangGraphCheckpointReader(
        _RuntimeConfigFakeLangGraphBackend(
            [
                _build_checkpoint_tuple(
                    thread_id=thread.thread_id,
                    checkpoint_id="checkpoint_1",
                    state_schema_version="checkpoint.v1",
                )
            ]
        )
    )
    store = create_sqlalchemy_checkpoint_store(
        database_url,
        settings=settings,
        checkpoint_reader=reader,
    )
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.load_latest_checkpoint(
                    LoadLatestCheckpointQueryDto(
                        request_id="req_latest",
                        trace_id="trace_latest",
                        thread_id=thread.thread_id,
                    )
                )
            )

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED
    finally:
        store.dispose()


def test_runtime_config_rejects_run_lock_ttl_outside_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 会拒绝超出允许范围的运行锁 TTL。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "ttl.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    settings = _build_runtime_settings(min_ttl_seconds=10.0, max_ttl_seconds=20.0)
    store = create_sqlalchemy_checkpoint_store(database_url, settings=settings)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.acquire_run_lock(
                    command=AcquireRunLockCommandDto(
                        request_id="req_lock",
                        trace_id="trace_lock",
                        thread_id=build_checkpoint_thread_id(session_id="session_1"),
                        run_id="run_1",
                        lock_ttl_seconds=1.0,
                    )
                )
            )

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        assert exc_info.value.operation is CheckpointOperation.ACQUIRE_RUN_LOCK
    finally:
        store.dispose()


def test_runtime_config_rejects_list_limit_above_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 会拒绝超过配置上限的 checkpoint 历史查询 limit。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "limit.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    settings = _build_runtime_settings(max_list_limit=1)
    store = create_sqlalchemy_checkpoint_store(database_url, settings=settings)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.list_checkpoints(
                    ListCheckpointsQueryDto(
                        request_id="req_list",
                        trace_id="trace_list",
                        thread_id=thread.thread_id,
                        limit=2,
                    )
                )
            )

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        assert exc_info.value.operation is CheckpointOperation.LIST_CHECKPOINTS
    finally:
        store.dispose()


def test_runtime_config_rejects_large_segment_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 会拒绝过大的 segment 发布 metadata。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "metadata.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    settings = _build_runtime_settings(max_segment_metadata_bytes=8)
    store = create_sqlalchemy_checkpoint_store(database_url, settings=settings)
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.mark_segment_published(
                    MarkSegmentPublishedCommandDto(
                        request_id="req_mark",
                        trace_id="trace_mark",
                        thread_id=thread.thread_id,
                        run_id="run_1",
                        segment_id="segment_1",
                        task_id="task_1",
                        published_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
                        metadata={"message_ref": "message_1"},
                    )
                )
            )

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE
        assert exc_info.value.operation is CheckpointOperation.MARK_SEGMENT_PUBLISHED
    finally:
        store.dispose()


def test_runtime_config_rejects_large_checkpoint_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 RuntimeConfig 会拒绝过大的 SaveCheckpoint 状态体。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "checkpoint_size.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    settings = _build_runtime_settings(max_checkpoint_state_bytes=128)
    backend = _RuntimeConfigFakeLangGraphBackend([])
    store = create_sqlalchemy_checkpoint_store(
        database_url,
        settings=settings,
        checkpoint_reader=LangGraphCheckpointReader(backend),
        checkpoint_writer=LangGraphCheckpointWriter(backend),
    )
    try:
        thread = asyncio.run(store.ensure_thread(_build_ensure_thread_command())).thread
        _acquire_default_run_lock(store=store, thread_id=thread.thread_id)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.save_checkpoint(_build_large_save_command()))

        assert exc_info.value.code is CheckpointErrorCode.CHECKPOINT_STATE_TOO_LARGE
        assert exc_info.value.operation is CheckpointOperation.SAVE_CHECKPOINT
    finally:
        store.dispose()
