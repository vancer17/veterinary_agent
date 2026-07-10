##################################################################################################
# 文件: tests/graph_runtime/helpers.py
# 作用: 提供 GraphRuntime 组件测试复用的请求构造器、TODO 运行时构造器和测试 CheckpointStore。
# 边界: 仅用于测试 GraphRuntime 公共契约；不连接数据库、不实现真实 L2 兽医业务组件。
##################################################################################################

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.memory import InMemorySaver

from veterinary_agent import (
    AcquireRunLockCommandDto,
    AcquireRunLockResultDto,
    AgentGraphEventDto,
    AgentGraphTurnRequestDto,
    AgentTurnExecutionContextDto,
    AgentTurnExecutionOptionsDto,
    AgentTurnPublishCapabilitiesDto,
    CheckpointThreadDto,
    CheckpointThreadStatus,
    DefaultGraphRuntime,
    EnsureThreadCommandDto,
    EnsureThreadResultDto,
    GetCheckpointQueryDto,
    ListCheckpointsQueryDto,
    ListCheckpointsResultDto,
    LoadLatestCheckpointQueryDto,
    LoadLatestCheckpointResultDto,
    LoadSessionStateQueryDto,
    LoadSessionStateResultDto,
    MarkSegmentPublishedCommandDto,
    MarkSegmentPublishedResultDto,
    ReleaseRunLockCommandDto,
    ReleaseRunLockResultDto,
    SaveCheckpointCommandDto,
    SaveCheckpointResultDto,
    SegmentPublishStateDto,
    SegmentPublishStatus,
    SessionBusinessStateDto,
    build_default_graph_registry,
)


class CapturingCheckpointStore:
    """GraphRuntime 测试用 CheckpointStore。"""

    def __init__(self) -> None:
        """初始化测试 CheckpointStore。

        :return: None。
        """

        now = datetime.now(UTC)
        self.thread = CheckpointThreadDto(
            thread_id="checkpoint_thread_test",
            session_id="session_1",
            user_id="user_1",
            pet_id="pet_1",
            status=CheckpointThreadStatus.INITIALIZED,
            latest_version=0,
            latest_checkpoint_id=None,
            created_at=now,
            updated_at=now,
        )
        self.ensure_calls: list[EnsureThreadCommandDto] = []
        self.acquire_calls: list[AcquireRunLockCommandDto] = []
        self.release_calls: list[ReleaseRunLockCommandDto] = []
        self.save_calls: list[SaveCheckpointCommandDto] = []
        self.publish_calls: list[MarkSegmentPublishedCommandDto] = []

    async def ensure_thread(
        self,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """返回固定 checkpoint thread。

        :param command: 获取或创建 checkpoint thread 的命令。
        :return: 固定 checkpoint thread 结果。
        """

        self.ensure_calls.append(command)
        return EnsureThreadResultDto(thread=self.thread, created_new=False)

    async def acquire_run_lock(
        self,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """记录运行锁获取请求。

        :param command: 获取运行锁命令。
        :return: 运行锁获取结果。
        """

        self.acquire_calls.append(command)
        return AcquireRunLockResultDto(
            thread_id=command.thread_id,
            run_id=command.run_id,
            lock_acquired=True,
            expires_at=datetime.now(UTC) + timedelta(seconds=command.lock_ttl_seconds),
            idempotent=False,
            stale_lock_replaced=False,
        )

    async def release_run_lock(
        self,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """记录运行锁释放请求。

        :param command: 释放运行锁命令。
        :return: 运行锁释放结果。
        """

        self.release_calls.append(command)
        return ReleaseRunLockResultDto(
            thread_id=command.thread_id,
            run_id=command.run_id,
            released=True,
            idempotent=False,
        )

    async def save_checkpoint(
        self,
        command: SaveCheckpointCommandDto,
    ) -> SaveCheckpointResultDto:
        """记录遗留 checkpoint 保存请求。

        :param command: 保存 checkpoint 命令。
        :return: checkpoint 保存结果。
        """

        self.save_calls.append(command)
        new_version = len(self.save_calls)
        self.thread.latest_version = new_version
        self.thread.latest_checkpoint_id = f"checkpoint_{new_version}"
        return SaveCheckpointResultDto(
            checkpoint_id=f"checkpoint_{new_version}",
            thread_id=command.thread_id,
            new_version=new_version,
            status=command.status,
            state_size_bytes=128,
            saved_at=datetime.now(UTC),
        )

    async def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """记录 segment 发布标记请求。

        :param command: 标记 segment 已发布命令。
        :return: segment 发布标记结果。
        """

        self.publish_calls.append(command)
        return MarkSegmentPublishedResultDto(
            segment=SegmentPublishStateDto(
                segment_id=command.segment_id,
                task_id=command.task_id,
                status=SegmentPublishStatus.PUBLISHED,
                published_at=command.published_at,
                metadata=dict(command.metadata),
            ),
            idempotent=False,
        )

    async def load_latest_checkpoint(
        self,
        query: LoadLatestCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """返回空最新 checkpoint 查询结果。

        :param query: 读取最新 checkpoint 查询。
        :return: 空 checkpoint 查询结果。
        """

        return LoadLatestCheckpointResultDto(
            thread_id=query.thread_id,
            latest_version=self.thread.latest_version,
            checkpoint=None,
        )

    async def get_checkpoint(
        self,
        query: GetCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """返回空指定 checkpoint 查询结果。

        :param query: 读取指定 checkpoint 查询。
        :return: 空 checkpoint 查询结果。
        """

        return LoadLatestCheckpointResultDto(
            thread_id=query.thread_id,
            latest_version=self.thread.latest_version,
            checkpoint=None,
        )

    async def list_checkpoints(
        self,
        query: ListCheckpointsQueryDto,
    ) -> ListCheckpointsResultDto:
        """返回空 checkpoint 历史。

        :param query: checkpoint 历史查询。
        :return: 空 checkpoint 历史结果。
        """

        return ListCheckpointsResultDto(thread_id=query.thread_id, items=[])

    async def load_session_state(
        self,
        query: LoadSessionStateQueryDto,
    ) -> LoadSessionStateResultDto:
        """返回空 session 业务状态。

        :param query: session 状态查询。
        :return: 空 session 业务状态结果。
        """

        return LoadSessionStateResultDto(
            thread_id=query.thread_id,
            session_id=query.session_id or "session_1",
            latest_checkpoint_id=self.thread.latest_checkpoint_id,
            latest_version=self.thread.latest_version,
            state=SessionBusinessStateDto(),
        )


def build_todo_runtime(
    checkpoint_store: CapturingCheckpointStore,
) -> DefaultGraphRuntime:
    """构建使用 LangGraph InMemorySaver 的默认 TODO 图运行时。

    :param checkpoint_store: 测试用项目控制面存储。
    :return: 已注册默认 TODO 图并注入 InMemorySaver 的 GraphRuntime。
    """

    return DefaultGraphRuntime(
        checkpoint_store=checkpoint_store,
        checkpointer=InMemorySaver(),
        graph_registry=build_default_graph_registry(),
    )


def build_graph_request(
    *,
    run_id: str = "run_1",
) -> AgentGraphTurnRequestDto:
    """构建 GraphRuntime 测试请求。

    :param run_id: 本次请求绑定的图运行 ID。
    :return: GraphRuntime 测试请求。
    """

    return AgentGraphTurnRequestDto(
        context=AgentTurnExecutionContextDto(
            request_id="req_1",
            trace_id="trace_1",
            turn_id="turn_1",
            run_id=run_id,
            session_id="session_1",
            user_id="user_1",
            current_pet_id="pet_1",
            user_message_id="msg_1",
            idempotency_key="idem_1",
            params_version="params.v1",
            config_snapshot_id="config_1",
            response_mode="sync",
            route_kind="agent_turns",
        ),
        input=[],
        attachments=[],
        metadata={},
        execution_options=AgentTurnExecutionOptionsDto(
            orchestrator_target="local",
            connect_timeout_seconds=1,
            request_timeout_seconds=10,
            stream_first_event_timeout_seconds=1,
            stream_total_timeout_seconds=10,
            heartbeat_enabled=True,
            heartbeat_interval_seconds=1,
            stream_idle_timeout_seconds=10,
            max_stream_duration_seconds=30,
            max_event_bytes=8192,
            client_cancel_notify_timeout_seconds=1,
        ),
        publish_capabilities=AgentTurnPublishCapabilitiesDto(
            supports_segments=True,
            supports_reasoning_display=True,
            supports_sse_events=True,
        ),
    )


async def collect_events(
    events: AsyncIterator[AgentGraphEventDto],
) -> list[AgentGraphEventDto]:
    """收集 GraphRuntime 异步事件。

    :param events: GraphRuntime 事件异步迭代器。
    :return: 事件列表。
    """

    return [event async for event in events]


__all__: tuple[str, ...] = (
    "CapturingCheckpointStore",
    "build_graph_request",
    "build_todo_runtime",
    "collect_events",
)
