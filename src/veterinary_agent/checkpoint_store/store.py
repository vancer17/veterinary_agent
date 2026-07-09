##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/store.py
# 作用: 定义 CheckpointStore 应用内服务接口契约，并提供领域外依赖尚未接入时的 TODO 空壳实现。
# 边界: 仅声明状态持久化组件的稳定入口，不实现数据库、LangGraph、RuntimeConfig 或 Observability 集成。
##################################################################################################

from typing import NoReturn, Protocol

from veterinary_agent.checkpoint_store.dto import (
    AcquireRunLockCommandDto,
    AcquireRunLockResultDto,
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
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError


class CheckpointStore(Protocol):
    """CheckpointStore 应用内服务接口契约。"""

    async def ensure_thread(
        self,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """获取或创建 checkpoint thread。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 当前请求命中的 checkpoint thread 与创建标记。
        :raises CheckpointStoreError: 当 pet_id 冲突、入参无效或存储不可用时抛出。
        """

        ...

    async def acquire_run_lock(
        self,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """获取同一 thread 的运行互斥锁。

        :param command: 获取运行锁的命令 DTO。
        :return: 运行锁获取结果。
        :raises CheckpointStoreError: 当锁被其他 run 持有、thread 不存在或存储不可用时抛出。
        """

        ...

    async def release_run_lock(
        self,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """释放当前 run 持有的运行锁。

        :param command: 释放运行锁的命令 DTO。
        :return: 运行锁释放结果。
        :raises CheckpointStoreError: 当释放者不是锁持有者或存储不可用时抛出。
        """

        ...

    async def load_latest_checkpoint(
        self,
        query: LoadLatestCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取指定 thread 的最新 checkpoint。

        :param query: 读取最新 checkpoint 的查询 DTO。
        :return: 最新 checkpoint、版本号与已发布 segment 摘要。
        :raises CheckpointStoreError: 当 thread 不存在、状态损坏、schema 不支持或存储不可用时抛出。
        """

        ...

    async def get_checkpoint(
        self,
        query: GetCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取指定 checkpoint 快照。

        :param query: 读取指定 checkpoint 的查询 DTO。
        :return: 指定 checkpoint 快照与关联发布状态。
        :raises CheckpointStoreError: 当 checkpoint 不存在、跨 thread 读取或状态不可用时抛出。
        """

        ...

    async def list_checkpoints(
        self,
        query: ListCheckpointsQueryDto,
    ) -> ListCheckpointsResultDto:
        """查询指定 thread 的 checkpoint 历史摘要。

        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: checkpoint 历史摘要分页结果。
        :raises CheckpointStoreError: 当 thread 不存在或存储不可用时抛出。
        """

        ...

    async def save_checkpoint(
        self,
        command: SaveCheckpointCommandDto,
    ) -> SaveCheckpointResultDto:
        """保存一次关键边界 checkpoint。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: 保存成功后的 checkpoint ID、版本和状态体大小。
        :raises CheckpointStoreError: 当未持锁、版本冲突、pet_id 冲突、状态过大或存储不可用时抛出。
        """

        ...

    async def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """幂等标记 segment 已发布。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: segment 发布状态与幂等命中标记。
        :raises CheckpointStoreError: 当 thread 不存在或存储不可用时抛出。
        """

        ...

    async def load_session_state(
        self,
        query: LoadSessionStateQueryDto,
    ) -> LoadSessionStateResultDto:
        """读取 session 短期业务状态摘要。

        :param query: 读取 session 状态摘要的查询 DTO。
        :return: session 短期业务状态摘要。
        :raises CheckpointStoreError: 当 thread 不存在、状态损坏或存储不可用时抛出。
        """

        ...


class TodoCheckpointStore:
    """领域外依赖尚未接入时使用的 CheckpointStore TODO 空壳实现。"""

    def _raise_unavailable(
        self,
        *,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> NoReturn:
        """抛出 CheckpointStore 实现尚未接入的占位错误。

        :param operation: 当前被调用的 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 该函数总是抛出异常，不会返回。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        raise CheckpointStoreError(
            code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
            operation=operation,
            message="CheckpointStore 领域外依赖尚未接入",
            request_id=request_id,
            trace_id=trace_id,
            retryable=True,
        )

    async def ensure_thread(
        self,
        command: EnsureThreadCommandDto,
    ) -> EnsureThreadResultDto:
        """获取或创建 checkpoint thread 的 TODO 占位实现。

        :param command: 获取或创建 checkpoint thread 的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.ENSURE_THREAD,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def acquire_run_lock(
        self,
        command: AcquireRunLockCommandDto,
    ) -> AcquireRunLockResultDto:
        """获取运行锁的 TODO 占位实现。

        :param command: 获取运行锁的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.ACQUIRE_RUN_LOCK,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def release_run_lock(
        self,
        command: ReleaseRunLockCommandDto,
    ) -> ReleaseRunLockResultDto:
        """释放运行锁的 TODO 占位实现。

        :param command: 释放运行锁的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.RELEASE_RUN_LOCK,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def load_latest_checkpoint(
        self,
        query: LoadLatestCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取最新 checkpoint 的 TODO 占位实现。

        :param query: 读取最新 checkpoint 的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.LOAD_LATEST_CHECKPOINT,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def get_checkpoint(
        self,
        query: GetCheckpointQueryDto,
    ) -> LoadLatestCheckpointResultDto:
        """读取指定 checkpoint 的 TODO 占位实现。

        :param query: 读取指定 checkpoint 的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.GET_CHECKPOINT,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def list_checkpoints(
        self,
        query: ListCheckpointsQueryDto,
    ) -> ListCheckpointsResultDto:
        """查询 checkpoint 历史的 TODO 占位实现。

        :param query: 查询 checkpoint 历史的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.LIST_CHECKPOINTS,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )

    async def save_checkpoint(
        self,
        command: SaveCheckpointCommandDto,
    ) -> SaveCheckpointResultDto:
        """保存 checkpoint 的 TODO 占位实现。

        :param command: 保存 checkpoint 的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.SAVE_CHECKPOINT,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """标记 segment 已发布的 TODO 占位实现。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.MARK_SEGMENT_PUBLISHED,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )

    async def load_session_state(
        self,
        query: LoadSessionStateQueryDto,
    ) -> LoadSessionStateResultDto:
        """读取 session 状态摘要的 TODO 占位实现。

        :param query: 读取 session 状态摘要的查询 DTO。
        :return: 当前实现不会返回结果。
        :raises CheckpointStoreError: 始终抛出存储不可用错误。
        """

        self._raise_unavailable(
            operation=CheckpointOperation.LOAD_SESSION_STATE,
            request_id=query.request_id,
            trace_id=query.trace_id,
        )


__all__: tuple[str, ...] = (
    "CheckpointStore",
    "TodoCheckpointStore",
)
