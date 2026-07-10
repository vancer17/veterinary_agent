##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/control_plane.py
# 作用: 管理 GraphRuntime 项目控制面的 thread、运行锁和 segment 发布幂等。
# 边界: 不保存图执行 checkpoint；图状态的唯一权威写入者是 LangGraph checkpointer。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.agent_application_service import AgentGraphTurnRequestDto
from veterinary_agent.checkpoint_store import (
    AcquireRunLockCommandDto,
    CheckpointStore,
    EnsureThreadCommandDto,
    MarkSegmentPublishedCommandDto,
    ReleaseRunLockCommandDto,
    SegmentPublishStateDto,
)
from veterinary_agent.graph_runtime.dto import (
    GraphRunControlContext,
    GraphRuntimeSettings,
    JsonMap,
)


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


class GraphRunControlPlane:
    """GraphRuntime 项目控制面协调器。"""

    def __init__(
        self,
        *,
        checkpoint_store: CheckpointStore,
        settings: GraphRuntimeSettings,
    ) -> None:
        """初始化 GraphRuntime 项目控制面协调器。

        :param checkpoint_store: 提供 thread、运行锁和 segment 幂等的项目存储。
        :param settings: GraphRuntime 运行设置。
        :return: None。
        """

        self._checkpoint_store = checkpoint_store
        self._settings = settings

    async def prepare_new_run(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> GraphRunControlContext:
        """获取或创建当前 session 的 checkpoint thread。

        :param request: GraphRuntime 单轮执行请求。
        :return: 已绑定稳定 thread ID 的运行控制面上下文。
        """

        context = request.context
        result = await self._checkpoint_store.ensure_thread(
            EnsureThreadCommandDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                session_id=context.session_id,
                user_id=context.user_id,
                pet_id=context.current_pet_id,
            )
        )
        return GraphRunControlContext(
            request_id=context.request_id,
            trace_id=context.trace_id,
            run_id=context.run_id,
            session_id=context.session_id,
            user_id=context.user_id,
            pet_id=context.current_pet_id,
            thread_id=result.thread.thread_id,
        )

    async def acquire_run_lock(self, context: GraphRunControlContext) -> None:
        """获取当前 thread 的项目级运行互斥锁。

        :param context: 当前图运行控制面上下文。
        :return: None。
        """

        await self._checkpoint_store.acquire_run_lock(
            AcquireRunLockCommandDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                thread_id=context.thread_id,
                run_id=context.run_id,
                lock_ttl_seconds=self._settings.run_lock_ttl_seconds,
            )
        )
        context.lock_acquired = True

    async def release_run_lock(self, context: GraphRunControlContext) -> None:
        """释放当前图运行持有的项目级运行锁。

        :param context: 当前图运行控制面上下文。
        :return: None。
        """

        if not context.lock_acquired:
            return
        await self._checkpoint_store.release_run_lock(
            ReleaseRunLockCommandDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                thread_id=context.thread_id,
                run_id=context.run_id,
            )
        )
        context.lock_acquired = False

    async def mark_segment_published(
        self,
        *,
        context: GraphRunControlContext,
        segment_id: str,
        task_id: str | None = None,
        metadata: JsonMap | None = None,
    ) -> SegmentPublishStateDto:
        """幂等标记业务 segment 已发布。

        :param context: 当前图运行控制面上下文。
        :param segment_id: 已发布的稳定 segment ID。
        :param task_id: 可选业务子任务 ID。
        :param metadata: 可选发布摘要元信息。
        :return: 项目控制面记录的 segment 发布状态。
        """

        result = await self._checkpoint_store.mark_segment_published(
            MarkSegmentPublishedCommandDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                thread_id=context.thread_id,
                run_id=context.run_id,
                segment_id=segment_id,
                task_id=task_id,
                published_at=_now_utc(),
                metadata=metadata or {},
            )
        )
        return result.segment


__all__: tuple[str, ...] = ("GraphRunControlPlane",)
