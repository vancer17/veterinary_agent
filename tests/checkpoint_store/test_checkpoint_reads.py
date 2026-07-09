##################################################################################################
# 文件: tests/checkpoint_store/test_checkpoint_reads.py
# 作用: 验证 CheckpointStore LoadLatestCheckpoint / GetCheckpoint / ListCheckpoints 的读取侧实现。
# 边界: 使用临时 SQLite 验证项目控制面读取，使用 fake LangGraph backend，避免连接真实 PostgreSQL。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, CheckpointTuple

from veterinary_agent.checkpoint_store import (
    CHECKPOINT_SEGMENT_PUBLISH_TABLE,
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
    CheckpointStoreError,
    EnsureThreadCommandDto,
    GetCheckpointQueryDto,
    GraphExecutionStateDto,
    LangGraphCheckpointReader,
    LangGraphRunnableConfig,
    ListCheckpointsQueryDto,
    LoadLatestCheckpointQueryDto,
    LoadSessionStateQueryDto,
    SaveCheckpointCommandDto,
    SegmentPublishStatus,
    SessionBusinessStateDto,
    SqlAlchemyCheckpointStore,
    SqlAlchemyCheckpointVersionRepository,
    build_checkpoint_thread_id,
    create_sqlalchemy_checkpoint_store,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _tuple_configurable(checkpoint_tuple: CheckpointTuple) -> dict[str, str]:
    """读取测试 checkpoint tuple 的 configurable 配置。

    :param checkpoint_tuple: 测试用 LangGraph checkpoint tuple。
    :return: 字符串键值形式的 configurable 配置。
    """

    return cast(dict[str, str], checkpoint_tuple.config.get("configurable", {}))


class _FakeLangGraphBackend:
    """测试用 LangGraph checkpoint 读取后端。"""

    def __init__(self, tuples: list[CheckpointTuple]) -> None:
        """初始化测试用 LangGraph checkpoint 读取后端。

        :param tuples: 可被 fake 后端读取的 checkpoint tuple 列表。
        :return: None。
        """

        self._tuples = tuples
        self.last_filter: dict[str, Any] | None = None
        self.last_before: LangGraphRunnableConfig | None = None

    async def aget_tuple(
        self,
        config: LangGraphRunnableConfig,
    ) -> CheckpointTuple | None:
        """按配置读取单个 checkpoint tuple。

        :param config: LangGraph thread/checkpoint 运行配置。
        :return: 命中的 checkpoint tuple；不存在时返回 None。
        """

        configurable = config["configurable"]
        expected_thread_id = configurable["thread_id"]
        expected_checkpoint_id = configurable.get("checkpoint_id")
        for checkpoint_tuple in self._tuples:
            tuple_configurable = _tuple_configurable(checkpoint_tuple)
            if tuple_configurable["thread_id"] != expected_thread_id:
                continue
            if expected_checkpoint_id is None:
                return checkpoint_tuple
            if tuple_configurable.get("checkpoint_id") == expected_checkpoint_id:
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

        self.last_filter = filter
        self.last_before = before
        expected_thread_id = (
            None if config is None else config["configurable"]["thread_id"]
        )
        before_checkpoint_id = (
            None if before is None else before["configurable"]["checkpoint_id"]
        )
        count = 0
        for checkpoint_tuple in self._tuples:
            tuple_configurable = _tuple_configurable(checkpoint_tuple)
            if (
                expected_thread_id is not None
                and tuple_configurable["thread_id"] != expected_thread_id
            ):
                continue
            if (
                before_checkpoint_id is not None
                and tuple_configurable["checkpoint_id"] >= before_checkpoint_id
            ):
                continue
            if not self._matches_filter(
                checkpoint_tuple=checkpoint_tuple,
                filter=filter,
            ):
                continue
            if limit is not None and count >= limit:
                return
            count += 1
            yield checkpoint_tuple

    def _matches_filter(
        self,
        *,
        checkpoint_tuple: CheckpointTuple,
        filter: dict[str, Any] | None,
    ) -> bool:
        """判断 checkpoint tuple 是否匹配 fake metadata filter。

        :param checkpoint_tuple: 待判断的 checkpoint tuple。
        :param filter: 可选 metadata 过滤条件。
        :return: 若匹配过滤条件则返回 True。
        """

        if filter is None:
            return True
        expected_store = filter.get("checkpoint_store")
        if not isinstance(expected_store, dict):
            return True
        actual_store = checkpoint_tuple.metadata.get("checkpoint_store")
        if not isinstance(actual_store, dict):
            return False
        return all(
            actual_store.get(key) == value for key, value in expected_store.items()
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


def _build_save_command(
    *,
    expected_version: int,
    status: CheckpointRecordStatus = CheckpointRecordStatus.RECOVERABLE,
) -> SaveCheckpointCommandDto:
    """构建测试用 SaveCheckpoint 命令。

    :param expected_version: 调用方预期的 thread 最新版本。
    :param status: checkpoint 快照状态。
    :return: 测试用 SaveCheckpoint 命令 DTO。
    """

    return SaveCheckpointCommandDto(
        request_id="req_save",
        trace_id="trace_save",
        session_id="session_1",
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


def _open_engine(database_url: str) -> Engine:
    """打开测试数据库引擎。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 SQLAlchemy Engine。
    """

    return create_engine(database_url)


def _ensure_thread_exists(database_url: str) -> str:
    """通过真实 CheckpointStore 创建测试 thread。

    :param database_url: SQLAlchemy 数据库连接地址。
    :return: 已创建的 checkpoint thread ID。
    """

    store = create_sqlalchemy_checkpoint_store(database_url)
    try:
        result = asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
        return result.thread.thread_id
    finally:
        store.dispose()


def _advance_thread_version(
    *,
    database_url: str,
    checkpoint_id: str,
    expected_version: int,
    status: CheckpointRecordStatus = CheckpointRecordStatus.RECOVERABLE,
) -> None:
    """推进测试 thread 的项目级 checkpoint 版本。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param checkpoint_id: 需要写入 latest_checkpoint_id 的 checkpoint ID。
    :param expected_version: 调用方预期的 thread 最新版本。
    :param status: checkpoint 快照状态。
    :return: None。
    """

    engine = _open_engine(database_url)
    try:
        repository = SqlAlchemyCheckpointVersionRepository(engine=engine)
        repository.advance_thread_version(
            command=_build_save_command(
                expected_version=expected_version,
                status=status,
            ),
            checkpoint_id=checkpoint_id,
            state_size_bytes=100 + expected_version,
        )
    finally:
        engine.dispose()


def _insert_published_segment(
    *,
    database_url: str,
    thread_id: str,
) -> None:
    """插入测试用 segment 发布事实。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param thread_id: segment 所属 checkpoint thread ID。
    :return: None。
    """

    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    engine = _open_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                CHECKPOINT_SEGMENT_PUBLISH_TABLE.insert().values(
                    thread_id=thread_id,
                    segment_id="segment_1",
                    run_id="run_1",
                    task_id="task_1",
                    status=SegmentPublishStatus.PUBLISHED.value,
                    published_at=now,
                    metadata={"message_ref": "message_1"},
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        engine.dispose()


def _build_checkpoint_tuple(
    *,
    thread_id: str,
    checkpoint_id: str,
    version: int,
    status: CheckpointRecordStatus = CheckpointRecordStatus.RECOVERABLE,
    current_node: str = "node_a",
    created_at: datetime | None = None,
    business_pet_id: str | None = "pet_1",
) -> CheckpointTuple:
    """构建测试用 LangGraph checkpoint tuple。

    :param thread_id: checkpoint 所属 thread ID。
    :param checkpoint_id: checkpoint ID。
    :param version: 项目级 checkpoint 版本。
    :param status: checkpoint 快照状态。
    :param current_node: 当前节点名称。
    :param created_at: checkpoint 创建时间；为空时使用固定测试时间。
    :param business_pet_id: checkpoint business_state 中的 pet_id；为空表示未写入宠物锚点。
    :return: 测试用 LangGraph checkpoint tuple。
    """

    resolved_created_at = created_at or datetime(2026, 7, 8, 12, version, tzinfo=UTC)
    metadata: CheckpointMetadata = cast(
        CheckpointMetadata,
        {
            "checkpoint_store": {
                "checkpoint_store_managed": True,
                "version": version,
                "run_id": "run_1",
                "graph_name": "vet_main_graph",
                "graph_version": "graph.v1",
                "state_schema_version": "checkpoint.v1",
                "status": status.value,
                "current_node": current_node,
                "state_size_bytes": 100 + version,
                "created_at": resolved_created_at.isoformat(),
                "metadata": {"state_hash": f"hash_{version}"},
            }
        },
    )
    checkpoint: Checkpoint = cast(
        Checkpoint,
        {
            "v": 4,
            "id": checkpoint_id,
            "ts": resolved_created_at.isoformat(),
            "channel_values": {
                "graph_state": {
                    "current_node": current_node,
                    "completed_nodes": ["policy"],
                    "pending_nodes": ["composer"],
                    "node_outputs": {"policy": {"hash": "abc"}},
                    "retry_state": {},
                    "recoverable_from": current_node,
                },
                "business_state": {
                    "params_version": "params.v1",
                    "pet_id": business_pet_id,
                    "current_complaint_type": "skin",
                    "slot_progress": {"itching": "asked"},
                    "tasks": [
                        {
                            "task_id": "task_1",
                            "task_type": "standard_consultation",
                            "generation_profile": "standard",
                            "status": "ready",
                        }
                    ],
                    "segments": [
                        {
                            "segment_id": "segment_1",
                            "task_id": "task_1",
                            "status": SegmentPublishStatus.PUBLISHED.value,
                            "published_at": resolved_created_at.isoformat(),
                            "metadata": {"message_ref": "message_1"},
                        }
                    ],
                    "rolling_summary_ref": "summary_1",
                },
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


def _build_store_with_fake_reader(
    *,
    database_url: str,
    tuples: list[CheckpointTuple],
) -> tuple[SqlAlchemyCheckpointStore, _FakeLangGraphBackend]:
    """构建接入 fake LangGraph reader 的 SQLAlchemy CheckpointStore。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param tuples: fake 后端可读取的 checkpoint tuple 列表。
    :return: CheckpointStore 与 fake 后端。
    """

    backend = _FakeLangGraphBackend(tuples=tuples)
    store = create_sqlalchemy_checkpoint_store(
        database_url,
        checkpoint_reader=LangGraphCheckpointReader(backend),
    )
    return store, backend


def test_load_latest_checkpoint_returns_empty_when_thread_has_no_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 尚无 checkpoint 时读取最新 checkpoint 返回空快照。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "latest_empty.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        result = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                )
            )
        )

        assert result.thread_id == thread_id
        assert result.latest_version == 0
        assert result.checkpoint is None
        assert result.published_segments == []
    finally:
        store.dispose()


def test_load_latest_checkpoint_reads_control_plane_pointer_and_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证读取最新 checkpoint 会使用控制面 latest 指针并补充已发布 segment。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "latest.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_1",
        expected_version=0,
    )
    _insert_published_segment(database_url=database_url, thread_id=thread_id)
    checkpoint_tuple = _build_checkpoint_tuple(
        thread_id=thread_id,
        checkpoint_id="checkpoint_1",
        version=1,
    )
    store, _backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=[checkpoint_tuple],
    )
    try:
        result = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                )
            )
        )

        assert result.latest_version == 1
        assert result.checkpoint is not None
        assert result.checkpoint.checkpoint_id == "checkpoint_1"
        assert result.checkpoint.graph_state.current_node == "node_a"
        assert result.checkpoint.business_state.pet_id == "pet_1"
        assert len(result.published_segments) == 1
        assert result.published_segments[0].segment_id == "segment_1"
    finally:
        store.dispose()


def test_get_checkpoint_reads_requested_history_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 GetCheckpoint 可读取指定历史 checkpoint。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "get_checkpoint.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_2",
        expected_version=0,
    )
    tuples = [
        _build_checkpoint_tuple(
            thread_id=thread_id,
            checkpoint_id="checkpoint_2",
            version=1,
            current_node="node_b",
        ),
        _build_checkpoint_tuple(
            thread_id=thread_id,
            checkpoint_id="checkpoint_1",
            version=1,
            current_node="node_a",
        ),
    ]
    store, _backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=tuples,
    )
    try:
        result = asyncio.run(
            store.get_checkpoint(
                GetCheckpointQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                    checkpoint_id="checkpoint_1",
                )
            )
        )

        assert result.latest_version == 1
        assert result.checkpoint is not None
        assert result.checkpoint.checkpoint_id == "checkpoint_1"
        assert result.checkpoint.current_node == "node_a"
    finally:
        store.dispose()


def test_get_checkpoint_returns_not_found_for_missing_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证指定 checkpoint 不存在时返回 CHECKPOINT_NOT_FOUND。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_checkpoint.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.get_checkpoint(
                    GetCheckpointQueryDto(
                        request_id="req_1",
                        trace_id="trace_1",
                        thread_id=thread_id,
                        checkpoint_id="missing_checkpoint",
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_NOT_FOUND
        assert error.operation is CheckpointOperation.GET_CHECKPOINT
    finally:
        store.dispose()


def test_list_checkpoints_returns_summaries_and_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 ListCheckpoints 返回历史摘要、下一页游标与 metadata 过滤条件。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "list_checkpoints.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    tuples = [
        _build_checkpoint_tuple(thread_id=thread_id, checkpoint_id="checkpoint_3", version=3),
        _build_checkpoint_tuple(thread_id=thread_id, checkpoint_id="checkpoint_2", version=2),
        _build_checkpoint_tuple(thread_id=thread_id, checkpoint_id="checkpoint_1", version=1),
    ]
    store, backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=tuples,
    )
    try:
        result = asyncio.run(
            store.list_checkpoints(
                ListCheckpointsQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                    limit=2,
                    status=CheckpointRecordStatus.RECOVERABLE,
                )
            )
        )

        assert [item.checkpoint_id for item in result.items] == [
            "checkpoint_3",
            "checkpoint_2",
        ]
        assert result.next_cursor == "checkpoint_1"
        assert backend.last_filter == {
            "checkpoint_store": {"status": CheckpointRecordStatus.RECOVERABLE.value}
        }
    finally:
        store.dispose()


def test_load_latest_checkpoint_detects_missing_langgraph_pointer_as_corrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证控制面 latest 指针指向缺失 checkpoint 时返回状态损坏。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_latest.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_missing",
        expected_version=0,
    )
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.load_latest_checkpoint(
                    LoadLatestCheckpointQueryDto(
                        request_id="req_1",
                        trace_id="trace_1",
                        thread_id=thread_id,
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED
        assert error.operation is CheckpointOperation.LOAD_LATEST_CHECKPOINT
    finally:
        store.dispose()


def test_list_checkpoints_rejects_missing_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 不存在时 ListCheckpoints 返回稳定错误。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_thread.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.list_checkpoints(
                    ListCheckpointsQueryDto(
                        request_id="req_1",
                        trace_id="trace_1",
                        thread_id="missing_thread",
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND
        assert error.operation is CheckpointOperation.LIST_CHECKPOINTS
    finally:
        store.dispose()


def test_load_session_state_returns_empty_state_when_thread_has_no_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 thread 尚无 checkpoint 时 LoadSessionState 返回空业务状态并保留 pet 锚点。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_state_empty.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        result = asyncio.run(
            store.load_session_state(
                LoadSessionStateQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                    session_id="session_1",
                )
            )
        )

        assert result.thread_id == thread_id
        assert result.session_id == "session_1"
        assert result.latest_checkpoint_id is None
        assert result.latest_version == 0
        assert result.state.pet_id == "pet_1"
        assert result.state.slot_progress == {}
    finally:
        store.dispose()


def test_load_session_state_rejects_session_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证查询 session_id 与 thread 锚点不一致时 LoadSessionState 拒绝读取。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_state_mismatch.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    store, _backend = _build_store_with_fake_reader(database_url=database_url, tuples=[])
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.load_session_state(
                    LoadSessionStateQueryDto(
                        request_id="req_1",
                        trace_id="trace_1",
                        thread_id=thread_id,
                        session_id="session_other",
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_INVALID_ARGUMENT
        assert error.operation is CheckpointOperation.LOAD_SESSION_STATE
    finally:
        store.dispose()


def test_load_session_state_returns_latest_checkpoint_business_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LoadSessionState 返回最新 checkpoint 中的 business_state。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_state_latest.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_1",
        expected_version=0,
    )
    checkpoint_tuple = _build_checkpoint_tuple(
        thread_id=thread_id,
        checkpoint_id="checkpoint_1",
        version=1,
    )
    store, _backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=[checkpoint_tuple],
    )
    try:
        result = asyncio.run(
            store.load_session_state(
                LoadSessionStateQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                    session_id="session_1",
                )
            )
        )

        assert result.latest_checkpoint_id == "checkpoint_1"
        assert result.latest_version == 1
        assert result.state.pet_id == "pet_1"
        assert result.state.params_version == "params.v1"
        assert result.state.slot_progress == {"itching": "asked"}
        assert result.state.tasks[0].task_id == "task_1"
    finally:
        store.dispose()


def test_load_session_state_fills_missing_business_pet_from_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 checkpoint business_state 未写 pet_id 时由 thread 宠物锚点补齐。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_state_fill_pet.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_1",
        expected_version=0,
    )
    checkpoint_tuple = _build_checkpoint_tuple(
        thread_id=thread_id,
        checkpoint_id="checkpoint_1",
        version=1,
        business_pet_id=None,
    )
    store, _backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=[checkpoint_tuple],
    )
    try:
        result = asyncio.run(
            store.load_session_state(
                LoadSessionStateQueryDto(
                    request_id="req_1",
                    trace_id="trace_1",
                    thread_id=thread_id,
                )
            )
        )

        assert result.state.pet_id == "pet_1"
    finally:
        store.dispose()


def test_load_session_state_rejects_business_pet_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 checkpoint business_state.pet_id 与 thread pet_id 不一致时返回状态损坏。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "session_state_pet_conflict.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    thread_id = _ensure_thread_exists(database_url)
    _advance_thread_version(
        database_url=database_url,
        checkpoint_id="checkpoint_1",
        expected_version=0,
    )
    checkpoint_tuple = _build_checkpoint_tuple(
        thread_id=thread_id,
        checkpoint_id="checkpoint_1",
        version=1,
        business_pet_id="pet_other",
    )
    store, _backend = _build_store_with_fake_reader(
        database_url=database_url,
        tuples=[checkpoint_tuple],
    )
    try:
        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.load_session_state(
                    LoadSessionStateQueryDto(
                        request_id="req_1",
                        trace_id="trace_1",
                        thread_id=thread_id,
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED
        assert error.operation is CheckpointOperation.LOAD_SESSION_STATE
    finally:
        store.dispose()
