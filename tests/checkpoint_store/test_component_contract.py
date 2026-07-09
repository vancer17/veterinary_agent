##################################################################################################
# 文件: tests/checkpoint_store/test_component_contract.py
# 作用: 验证 CheckpointStore 组件级公开契约闭环，覆盖 thread、运行锁、checkpoint 保存读取、
#       session 状态、segment 发布幂等与适配器装配边界。
# 边界: 使用临时 SQLite 验证项目控制面，使用 fake LangGraph backend 代替真实中间件；
#       不接入 GraphRuntime、Observability、ConversationStore 或兽医业务组件。
##################################################################################################

import asyncio
import inspect
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
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointRecordStatus,
    CheckpointStore,
    CheckpointStoreError,
    EnsureThreadCommandDto,
    GetCheckpointQueryDto,
    GraphExecutionStateDto,
    LangGraphCheckpointReader,
    LangGraphCheckpointWriter,
    LangGraphRunnableConfig,
    ListCheckpointsQueryDto,
    LoadLatestCheckpointQueryDto,
    LoadSessionStateQueryDto,
    MarkSegmentPublishedCommandDto,
    ReleaseRunLockCommandDto,
    SaveCheckpointCommandDto,
    SessionBusinessStateDto,
    SqlAlchemyCheckpointStore,
    TaskExecutionStateDto,
    build_checkpoint_thread_id,
    create_sqlalchemy_checkpoint_store,
)


def _get_configurable(config: LangGraphRunnableConfig) -> dict[str, str]:
    """读取测试用 LangGraph config 中的 configurable 字段。

    :param config: LangGraph thread/checkpoint 运行配置。
    :return: 字符串键值形式的 configurable 配置。
    """

    return cast(dict[str, str], config.get("configurable", {}))


class _ComponentFakeLangGraphBackend:
    """CheckpointStore 组件测试用 LangGraph checkpoint 读写后端。"""

    def __init__(
        self,
        *,
        returned_checkpoint_id_override: str | None = None,
    ) -> None:
        """初始化组件测试用 fake LangGraph 后端。

        :param returned_checkpoint_id_override: 可选的写入返回 checkpoint ID 覆盖值。
        :return: None。
        """

        self.tuples: list[CheckpointTuple] = []
        self.returned_checkpoint_id_override = returned_checkpoint_id_override

    async def aput(
        self,
        config: LangGraphRunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> LangGraphRunnableConfig:
        """写入测试用 LangGraph checkpoint tuple。

        :param config: LangGraph thread 运行配置。
        :param checkpoint: 需要保存的 checkpoint 状态体。
        :param metadata: 需要保存的 checkpoint metadata。
        :param new_versions: 本次写入更新的 channel version 映射。
        :return: 写入后的 LangGraph 运行配置。
        """

        del new_versions
        configurable = _get_configurable(config)
        checkpoint_id = str(checkpoint["id"])
        returned_checkpoint_id = self.returned_checkpoint_id_override or checkpoint_id
        tuple_config = cast(
            LangGraphRunnableConfig,
            {
                "configurable": {
                    "thread_id": configurable["thread_id"],
                    "checkpoint_ns": configurable.get("checkpoint_ns", ""),
                    "checkpoint_id": checkpoint_id,
                }
            },
        )
        self.tuples.insert(
            0,
            CheckpointTuple(
                config=tuple_config,
                checkpoint=checkpoint,
                metadata=metadata,
            ),
        )
        return cast(
            LangGraphRunnableConfig,
            {
                "configurable": {
                    "thread_id": configurable["thread_id"],
                    "checkpoint_ns": configurable.get("checkpoint_ns", ""),
                    "checkpoint_id": returned_checkpoint_id,
                }
            },
        )

    async def aget_tuple(
        self,
        config: LangGraphRunnableConfig,
    ) -> CheckpointTuple | None:
        """按 LangGraph config 精确读取测试 checkpoint tuple。

        :param config: LangGraph thread/checkpoint 运行配置。
        :return: 命中的 checkpoint tuple；不存在时返回 None。
        """

        configurable = _get_configurable(config)
        expected_thread_id = configurable["thread_id"]
        expected_checkpoint_id = configurable.get("checkpoint_id")
        for checkpoint_tuple in self.tuples:
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
        """按 LangGraph config 列出测试 checkpoint tuple。

        :param config: LangGraph thread 运行配置。
        :param filter: 可选 metadata 过滤条件。
        :param before: 可选 checkpoint 游标配置。
        :param limit: 可选最大返回条数。
        :return: checkpoint tuple 异步迭代器。
        """

        expected_thread_id = (
            None if config is None else _get_configurable(config)["thread_id"]
        )
        before_checkpoint_id = (
            None if before is None else _get_configurable(before)["checkpoint_id"]
        )
        count = 0
        for checkpoint_tuple in self.tuples:
            tuple_configurable = _tuple_configurable(checkpoint_tuple)
            if (
                expected_thread_id is not None
                and tuple_configurable["thread_id"] != expected_thread_id
            ):
                continue
            if tuple_configurable.get("checkpoint_id") == before_checkpoint_id:
                continue
            if not _matches_metadata_filter(
                checkpoint_tuple=checkpoint_tuple,
                metadata_filter=filter,
            ):
                continue
            if limit is not None and count >= limit:
                return
            count += 1
            yield checkpoint_tuple


def _tuple_configurable(checkpoint_tuple: CheckpointTuple) -> dict[str, str]:
    """读取 checkpoint tuple 的 configurable 配置。

    :param checkpoint_tuple: 测试用 LangGraph checkpoint tuple。
    :return: 字符串键值形式的 configurable 配置。
    """

    return cast(dict[str, str], checkpoint_tuple.config.get("configurable", {}))


def _matches_metadata_filter(
    *,
    checkpoint_tuple: CheckpointTuple,
    metadata_filter: dict[str, Any] | None,
) -> bool:
    """判断 checkpoint tuple 是否匹配 LangGraph metadata 过滤条件。

    :param checkpoint_tuple: 待判断的 checkpoint tuple。
    :param metadata_filter: 可选 metadata 过滤条件。
    :return: 若匹配过滤条件则返回 True。
    """

    if metadata_filter is None:
        return True
    expected_store = metadata_filter.get("checkpoint_store")
    if not isinstance(expected_store, dict):
        return True
    actual_store = checkpoint_tuple.metadata.get("checkpoint_store")
    if not isinstance(actual_store, dict):
        return False
    return all(actual_store.get(key) == value for key, value in expected_store.items())


def _build_alembic_config() -> Config:
    """构建组件测试用 Alembic 配置对象。

    :return: 指向项目 alembic.ini 的 Alembic 配置对象。
    """

    return Config("alembic.ini")


def _build_sqlite_database_url(database_path: Path) -> str:
    """构建组件测试用 SQLite 数据库连接地址。

    :param database_path: 临时 SQLite 数据库文件路径。
    :return: SQLAlchemy 可使用的 SQLite 数据库 URL。
    """

    return f"sqlite:///{database_path}"


def _upgrade_to_head(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    """运行项目 Alembic migration 到最新版本。

    :param monkeypatch: pytest monkeypatch 夹具。
    :param database_url: 本次迁移使用的数据库连接地址。
    :return: None。
    """

    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(_build_alembic_config(), "head")


def _build_store(
    *,
    database_url: str,
    backend: _ComponentFakeLangGraphBackend,
    with_reader: bool = True,
    with_writer: bool = True,
) -> SqlAlchemyCheckpointStore:
    """构建组件测试用 CheckpointStore。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param backend: fake LangGraph checkpoint 读写后端。
    :param with_reader: 是否接入 LangGraph checkpoint reader。
    :param with_writer: 是否接入 LangGraph checkpoint writer。
    :return: 已按参数装配的 SQLAlchemy CheckpointStore。
    """

    return create_sqlalchemy_checkpoint_store(
        database_url,
        checkpoint_reader=LangGraphCheckpointReader(backend) if with_reader else None,
        checkpoint_writer=LangGraphCheckpointWriter(backend) if with_writer else None,
    )


def _build_ensure_thread_command() -> EnsureThreadCommandDto:
    """构建组件测试用 EnsureThread 命令。

    :return: 测试用 EnsureThread 命令 DTO。
    """

    return EnsureThreadCommandDto(
        request_id="req_component_ensure",
        trace_id="trace_component",
        session_id="session_component",
        user_id="user_component",
        pet_id="pet_component",
    )


def _build_acquire_lock_command(
    *,
    thread_id: str,
    run_id: str = "run_component",
) -> AcquireRunLockCommandDto:
    """构建组件测试用 AcquireRunLock 命令。

    :param thread_id: 需要获取运行锁的 checkpoint thread ID。
    :param run_id: 当前图执行轮次 ID。
    :return: 测试用 AcquireRunLock 命令 DTO。
    """

    return AcquireRunLockCommandDto(
        request_id="req_component_lock",
        trace_id="trace_component",
        thread_id=thread_id,
        run_id=run_id,
        lock_ttl_seconds=60.0,
    )


def _build_release_lock_command(
    *,
    thread_id: str,
    run_id: str = "run_component",
) -> ReleaseRunLockCommandDto:
    """构建组件测试用 ReleaseRunLock 命令。

    :param thread_id: 需要释放运行锁的 checkpoint thread ID。
    :param run_id: 当前图执行轮次 ID。
    :return: 测试用 ReleaseRunLock 命令 DTO。
    """

    return ReleaseRunLockCommandDto(
        request_id="req_component_release",
        trace_id="trace_component",
        thread_id=thread_id,
        run_id=run_id,
    )


def _build_save_checkpoint_command(
    *,
    expected_version: int,
    status: CheckpointRecordStatus,
    current_node: str,
) -> SaveCheckpointCommandDto:
    """构建组件测试用 SaveCheckpoint 命令。

    :param expected_version: 调用方预期的 thread 最新版本。
    :param status: 本次 checkpoint 快照状态。
    :param current_node: 本次保存时的当前图节点。
    :return: 测试用 SaveCheckpoint 命令 DTO。
    """

    return SaveCheckpointCommandDto(
        request_id=f"req_component_save_{expected_version}",
        trace_id="trace_component",
        session_id="session_component",
        thread_id=build_checkpoint_thread_id(session_id="session_component"),
        run_id="run_component",
        expected_version=expected_version,
        graph_name="vet_component_graph",
        graph_version="graph.component.v1",
        state_schema_version="checkpoint.v1",
        status=status,
        current_node=current_node,
        graph_state=GraphExecutionStateDto(
            current_node=current_node,
            completed_nodes=["policy", "context"],
            pending_nodes=["composer"],
            node_outputs={"context": {"summary_ref": "summary_component"}},
            retry_state={"composer": {"attempts": 0}},
            recoverable_from=current_node,
        ),
        business_state=SessionBusinessStateDto(
            params_version="params.component.v1",
            pet_id="pet_component",
            current_complaint_type="skin",
            slot_progress={"itching": "asked", "duration": "answered"},
            tasks=[
                TaskExecutionStateDto(
                    task_id="task_component",
                    task_type="standard_consultation",
                    generation_profile="standard",
                    status="running"
                    if status is CheckpointRecordStatus.RECOVERABLE
                    else "done",
                )
            ],
            rolling_summary_ref="rolling_summary_component",
        ),
        metadata={"state_hash": f"hash_{expected_version + 1}"},
    )


def _build_mark_segment_command(
    *,
    thread_id: str,
) -> MarkSegmentPublishedCommandDto:
    """构建组件测试用 MarkSegmentPublished 命令。

    :param thread_id: segment 所属 checkpoint thread ID。
    :return: 测试用 MarkSegmentPublished 命令 DTO。
    """

    return MarkSegmentPublishedCommandDto(
        request_id="req_component_segment",
        trace_id="trace_component",
        thread_id=thread_id,
        run_id="run_component",
        segment_id="segment_component",
        task_id="task_component",
        published_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        metadata={"message_ref": "message_component"},
    )


def _prepare_thread_and_lock(
    *,
    store: SqlAlchemyCheckpointStore,
) -> str:
    """创建组件测试 thread 并获取默认运行锁。

    :param store: 被测 CheckpointStore 实例。
    :return: 已创建并持锁的 checkpoint thread ID。
    """

    ensure_result = asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
    asyncio.run(
        store.acquire_run_lock(
            _build_acquire_lock_command(thread_id=ensure_result.thread.thread_id)
        )
    )
    return ensure_result.thread.thread_id


def test_sqlalchemy_checkpoint_store_implements_public_protocol_methods() -> None:
    """验证 SQLAlchemyCheckpointStore 覆盖 CheckpointStore 协议全部公开方法。

    :return: None。
    """

    protocol_methods = [
        name
        for name, value in CheckpointStore.__dict__.items()
        if inspect.iscoroutinefunction(value)
    ]

    for method_name in protocol_methods:
        implementation = getattr(SqlAlchemyCheckpointStore, method_name)
        assert implementation.__qualname__.startswith("SqlAlchemyCheckpointStore.")


def test_checkpoint_store_component_full_contract_happy_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 CheckpointStore 组件公开契约的完整正常链路。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "component_happy_path.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _ComponentFakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        thread_id = _prepare_thread_and_lock(store=store)
        first_save = asyncio.run(
            store.save_checkpoint(
                _build_save_checkpoint_command(
                    expected_version=0,
                    status=CheckpointRecordStatus.RECOVERABLE,
                    current_node="context_node",
                )
            )
        )
        second_save = asyncio.run(
            store.save_checkpoint(
                _build_save_checkpoint_command(
                    expected_version=1,
                    status=CheckpointRecordStatus.COMPLETED,
                    current_node="completed_node",
                )
            )
        )
        first_segment_mark = asyncio.run(
            store.mark_segment_published(
                _build_mark_segment_command(thread_id=thread_id)
            )
        )
        second_segment_mark = asyncio.run(
            store.mark_segment_published(
                _build_mark_segment_command(thread_id=thread_id)
            )
        )

        latest = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_component_latest",
                    trace_id="trace_component",
                    thread_id=thread_id,
                )
            )
        )
        first_checkpoint = asyncio.run(
            store.get_checkpoint(
                GetCheckpointQueryDto(
                    request_id="req_component_get",
                    trace_id="trace_component",
                    thread_id=thread_id,
                    checkpoint_id=first_save.checkpoint_id,
                )
            )
        )
        completed_history = asyncio.run(
            store.list_checkpoints(
                ListCheckpointsQueryDto(
                    request_id="req_component_list",
                    trace_id="trace_component",
                    thread_id=thread_id,
                    limit=10,
                    status=CheckpointRecordStatus.COMPLETED,
                )
            )
        )
        session_state = asyncio.run(
            store.load_session_state(
                LoadSessionStateQueryDto(
                    request_id="req_component_session_state",
                    trace_id="trace_component",
                    thread_id=thread_id,
                    session_id="session_component",
                )
            )
        )
        first_release = asyncio.run(
            store.release_run_lock(_build_release_lock_command(thread_id=thread_id))
        )
        second_release = asyncio.run(
            store.release_run_lock(_build_release_lock_command(thread_id=thread_id))
        )

        assert first_save.new_version == 1
        assert second_save.new_version == 2
        assert latest.latest_version == 2
        assert latest.checkpoint is not None
        assert latest.checkpoint.checkpoint_id == second_save.checkpoint_id
        assert latest.checkpoint.status is CheckpointRecordStatus.COMPLETED
        assert latest.checkpoint.graph_state.current_node == "completed_node"
        assert len(latest.published_segments) == 1
        assert first_checkpoint.checkpoint is not None
        assert first_checkpoint.checkpoint.checkpoint_id == first_save.checkpoint_id
        assert first_checkpoint.checkpoint.version == 1
        assert completed_history.items[0].checkpoint_id == second_save.checkpoint_id
        assert session_state.latest_checkpoint_id == second_save.checkpoint_id
        assert session_state.state.pet_id == "pet_component"
        assert session_state.state.current_complaint_type == "skin"
        assert first_segment_mark.idempotent is False
        assert second_segment_mark.idempotent is True
        assert second_segment_mark.segment.segment_id == "segment_component"
        assert first_release.released is True
        assert second_release.idempotent is True
    finally:
        store.dispose()


def test_checkpoint_store_component_explicitly_fails_without_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证未接入 LangGraph writer 时 SaveCheckpoint 显式失败。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_writer.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _ComponentFakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend, with_writer=False)
    try:
        _prepare_thread_and_lock(store=store)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.save_checkpoint(
                    _build_save_checkpoint_command(
                        expected_version=0,
                        status=CheckpointRecordStatus.RECOVERABLE,
                        current_node="context_node",
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert len(backend.tuples) == 0
    finally:
        store.dispose()


def test_checkpoint_store_component_explicitly_fails_without_reader_after_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证未接入 LangGraph reader 时 checkpoint 读取显式失败。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_reader.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _ComponentFakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend, with_reader=False)
    try:
        thread_id = _prepare_thread_and_lock(store=store)
        asyncio.run(
            store.save_checkpoint(
                _build_save_checkpoint_command(
                    expected_version=0,
                    status=CheckpointRecordStatus.RECOVERABLE,
                    current_node="context_node",
                )
            )
        )

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.load_latest_checkpoint(
                    LoadLatestCheckpointQueryDto(
                        request_id="req_component_latest",
                        trace_id="trace_component",
                        thread_id=thread_id,
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
        assert error.operation is CheckpointOperation.LOAD_LATEST_CHECKPOINT
    finally:
        store.dispose()


def test_checkpoint_store_component_rejects_writer_checkpoint_id_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LangGraph writer 返回 checkpoint_id 不一致时组件拒绝推进控制面版本。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "writer_id_mismatch.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _ComponentFakeLangGraphBackend(
        returned_checkpoint_id_override="checkpoint_unexpected"
    )
    store = _build_store(database_url=database_url, backend=backend)
    try:
        thread_id = _prepare_thread_and_lock(store=store)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.save_checkpoint(
                    _build_save_checkpoint_command(
                        expected_version=0,
                        status=CheckpointRecordStatus.RECOVERABLE,
                        current_node="context_node",
                    )
                )
            )
        latest = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_component_latest_after_mismatch",
                    trace_id="trace_component",
                    thread_id=thread_id,
                )
            )
        )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert latest.latest_version == 0
        assert latest.checkpoint is None
    finally:
        store.dispose()
