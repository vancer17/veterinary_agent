##################################################################################################
# 文件: tests/checkpoint_store/test_save_checkpoint.py
# 作用: 验证 CheckpointStore.save_checkpoint 的服务级闭环，包括 LangGraph 写入、运行锁校验、
#       thread 版本推进与读回兼容。
# 边界: 使用临时 SQLite 验证项目控制面，使用 fake LangGraph backend，避免连接真实 PostgreSQL。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator
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
    CheckpointStoreError,
    EnsureThreadCommandDto,
    GraphExecutionStateDto,
    LangGraphCheckpointReader,
    LangGraphCheckpointWriter,
    LangGraphRunnableConfig,
    LoadLatestCheckpointQueryDto,
    SaveCheckpointCommandDto,
    SessionBusinessStateDto,
    SqlAlchemyCheckpointStore,
    build_checkpoint_thread_id,
    create_sqlalchemy_checkpoint_store,
)


class _FakeLangGraphBackend:
    """测试用 LangGraph checkpoint 读写后端。"""

    def __init__(self, *, fail_write: bool = False) -> None:
        """初始化测试用 LangGraph checkpoint 读写后端。

        :param fail_write: 是否在写入时模拟 LangGraph 后端失败。
        :return: None。
        """

        self._tuples: list[CheckpointTuple] = []
        self._fail_write = fail_write

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
        :raises RuntimeError: 当 fail_write 为真时抛出模拟写入失败。
        """

        if self._fail_write:
            raise RuntimeError("fake write failed")
        configurable = config["configurable"]
        checkpoint_id = str(checkpoint["id"])
        tuple_config = {
            "configurable": {
                "thread_id": configurable["thread_id"],
                "checkpoint_ns": configurable.get("checkpoint_ns", ""),
                "checkpoint_id": checkpoint_id,
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
        """按配置读取测试用 checkpoint tuple。

        :param config: LangGraph thread/checkpoint 运行配置。
        :return: 命中的 checkpoint tuple；不存在时返回 None。
        """

        configurable = config["configurable"]
        expected_thread_id = configurable["thread_id"]
        expected_checkpoint_id = configurable.get("checkpoint_id")
        for checkpoint_tuple in self._tuples:
            tuple_config = checkpoint_tuple.config["configurable"]
            if tuple_config["thread_id"] != expected_thread_id:
                continue
            if expected_checkpoint_id is None:
                return checkpoint_tuple
            if tuple_config.get("checkpoint_id") == expected_checkpoint_id:
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
        """按配置列出测试用 checkpoint tuple。

        :param config: LangGraph thread 运行配置。
        :param filter: 可选 metadata 过滤条件。
        :param before: 可选 checkpoint 游标配置。
        :param limit: 可选最大返回条数。
        :return: checkpoint tuple 异步迭代器。
        """

        expected_thread_id = (
            None if config is None else config["configurable"]["thread_id"]
        )
        count = 0
        for checkpoint_tuple in self._tuples:
            tuple_config = checkpoint_tuple.config["configurable"]
            if (
                expected_thread_id is not None
                and tuple_config["thread_id"] != expected_thread_id
            ):
                continue
            if not self._matches_filter(checkpoint_tuple=checkpoint_tuple, filter=filter):
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
        """判断 checkpoint tuple 是否匹配测试 metadata filter。

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


def _build_save_checkpoint_command(
    *,
    expected_version: int = 0,
    session_id: str = "session_1",
    run_id: str = "run_1",
    state_schema_version: str = "checkpoint.v1",
    business_pet_id: str | None = "pet_1",
) -> SaveCheckpointCommandDto:
    """构建测试用 SaveCheckpoint 命令。

    :param expected_version: 调用方预期的 thread 最新版本。
    :param session_id: 命令携带的 session ID。
    :param run_id: 命令携带的 run ID。
    :param state_schema_version: 命令携带的 checkpoint 状态 schema 版本。
    :param business_pet_id: 命令业务状态携带的 pet ID。
    :return: 测试用 SaveCheckpoint 命令 DTO。
    """

    return SaveCheckpointCommandDto(
        request_id="req_save",
        trace_id="trace_save",
        session_id=session_id,
        thread_id=build_checkpoint_thread_id(session_id="session_1"),
        run_id=run_id,
        expected_version=expected_version,
        graph_name="vet_main_graph",
        graph_version="graph.v1",
        state_schema_version=state_schema_version,
        status=CheckpointRecordStatus.RECOVERABLE,
        current_node="node_a",
        graph_state=GraphExecutionStateDto(
            current_node="node_a",
            completed_nodes=["policy"],
            pending_nodes=["composer"],
            node_outputs={"policy": {"hash": "abc"}},
            recoverable_from="node_a",
        ),
        business_state=SessionBusinessStateDto(
            params_version="params.v1",
            pet_id=business_pet_id,
            current_complaint_type="skin",
            slot_progress={"itching": "asked"},
        ),
        metadata={"state_hash": "hash_1"},
    )


def _build_store(
    *,
    database_url: str,
    backend: _FakeLangGraphBackend,
) -> SqlAlchemyCheckpointStore:
    """构建同时接入 fake reader/writer 的 SQLAlchemy CheckpointStore。

    :param database_url: SQLAlchemy 数据库连接地址。
    :param backend: 测试用 fake LangGraph 后端。
    :return: 已接入 fake LangGraph 后端的 CheckpointStore。
    """

    return create_sqlalchemy_checkpoint_store(
        database_url,
        checkpoint_reader=LangGraphCheckpointReader(backend),
        checkpoint_writer=LangGraphCheckpointWriter(backend),
    )


def _prepare_thread_and_lock(
    *,
    store: SqlAlchemyCheckpointStore,
) -> str:
    """创建测试 thread 并获取运行锁。

    :param store: 测试用 CheckpointStore。
    :return: 创建出的 checkpoint thread ID。
    """

    ensure_result = asyncio.run(store.ensure_thread(_build_ensure_thread_command()))
    asyncio.run(
        store.acquire_run_lock(
            AcquireRunLockCommandDto(
                request_id="req_lock",
                trace_id="trace_lock",
                thread_id=ensure_result.thread.thread_id,
                run_id="run_1",
                lock_ttl_seconds=60,
            )
        )
    )
    return ensure_result.thread.thread_id


def test_save_checkpoint_writes_langgraph_and_advances_thread_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SaveCheckpoint 可写入 LangGraph 并推进 thread latest 指针。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "save_success.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        thread_id = _prepare_thread_and_lock(store=store)
        save_result = asyncio.run(
            store.save_checkpoint(_build_save_checkpoint_command())
        )
        latest_result = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_latest",
                    trace_id="trace_latest",
                    thread_id=thread_id,
                )
            )
        )

        assert save_result.new_version == 1
        assert save_result.checkpoint_id.startswith("checkpoint_")
        assert latest_result.latest_version == 1
        assert latest_result.checkpoint is not None
        assert latest_result.checkpoint.checkpoint_id == save_result.checkpoint_id
        assert latest_result.checkpoint.graph_state.current_node == "node_a"
        assert latest_result.checkpoint.business_state.pet_id == "pet_1"
    finally:
        store.dispose()


def test_save_checkpoint_rejects_missing_run_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SaveCheckpoint 会拒绝未持有运行锁的 run。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "missing_lock.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        asyncio.run(store.ensure_thread(_build_ensure_thread_command()))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.save_checkpoint(_build_save_checkpoint_command()))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_LOCK_OWNER_MISMATCH
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
    finally:
        store.dispose()


def test_save_checkpoint_rejects_stale_expected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SaveCheckpoint 会拒绝落后的 expected_version。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "stale_version.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        _prepare_thread_and_lock(store=store)
        asyncio.run(store.save_checkpoint(_build_save_checkpoint_command()))

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.save_checkpoint(_build_save_checkpoint_command()))

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_VERSION_CONFLICT
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert len(backend._tuples) == 1
    finally:
        store.dispose()


def test_save_checkpoint_does_not_advance_version_when_langgraph_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LangGraph 写入失败时不会推进控制面版本。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "write_failed.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend(fail_write=True)
    store = _build_store(database_url=database_url, backend=backend)
    try:
        thread_id = _prepare_thread_and_lock(store=store)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(store.save_checkpoint(_build_save_checkpoint_command()))
        latest_result = asyncio.run(
            store.load_latest_checkpoint(
                LoadLatestCheckpointQueryDto(
                    request_id="req_latest",
                    trace_id="trace_latest",
                    thread_id=thread_id,
                )
            )
        )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
        assert latest_result.latest_version == 0
        assert latest_result.checkpoint is None
    finally:
        store.dispose()


def test_save_checkpoint_rejects_unsupported_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SaveCheckpoint 会拒绝未登记的状态 schema 版本。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "unsupported_schema.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        _prepare_thread_and_lock(store=store)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.save_checkpoint(
                    _build_save_checkpoint_command(
                        state_schema_version="checkpoint.legacy"
                    )
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
    finally:
        store.dispose()


def test_save_checkpoint_rejects_pet_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SaveCheckpoint 会拒绝业务状态中的 pet_id 锚点冲突。

    :param tmp_path: pytest 临时目录夹具。
    :param monkeypatch: pytest 环境变量修改夹具。
    :return: None。
    """

    database_url = _build_sqlite_database_url(tmp_path / "pet_conflict.db")
    _upgrade_to_head(monkeypatch=monkeypatch, database_url=database_url)
    backend = _FakeLangGraphBackend()
    store = _build_store(database_url=database_url, backend=backend)
    try:
        _prepare_thread_and_lock(store=store)

        with pytest.raises(CheckpointStoreError) as exc_info:
            asyncio.run(
                store.save_checkpoint(
                    _build_save_checkpoint_command(business_pet_id="pet_other")
                )
            )

        error = exc_info.value.to_dto()
        assert error.code is CheckpointErrorCode.CHECKPOINT_PET_CONFLICT
        assert error.operation is CheckpointOperation.SAVE_CHECKPOINT
    finally:
        store.dispose()
