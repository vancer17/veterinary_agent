##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/langgraph_reader.py
# 作用: 封装 LangGraph checkpointer 的 checkpoint 读取能力，为 CheckpointStore 提供
#       LoadLatestCheckpoint / GetCheckpoint / ListCheckpoints 所需的底层读取适配。
# 边界: 仅调用 LangGraph checkpointer 公共 API；不访问 LangGraph 物理表、不处理项目控制面表。
##################################################################################################

from collections.abc import AsyncIterator
from typing import Any, Protocol

from langgraph.checkpoint.base import CheckpointTuple

from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.langgraph_provider import (
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)


class LangGraphCheckpointReaderBackend(Protocol):
    """LangGraph checkpoint 读取后端协议。"""

    async def aget_tuple(
        self,
        config: LangGraphRunnableConfig,
    ) -> CheckpointTuple | None:
        """读取单个 LangGraph checkpoint tuple。

        :param config: LangGraph thread/checkpoint 运行配置。
        :return: 命中的 checkpoint tuple；不存在时返回 None。
        """

        ...

    def alist(
        self,
        config: LangGraphRunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: LangGraphRunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """按条件列出 LangGraph checkpoint tuple。

        :param config: LangGraph thread 运行配置；为空时表示不按 thread 过滤。
        :param filter: 可选 metadata 过滤条件。
        :param before: 可选 checkpoint 游标配置。
        :param limit: 可选最大返回条数。
        :return: checkpoint tuple 异步迭代器。
        """

        ...


def _build_langgraph_read_error(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 LangGraph 读取适配层领域错误。

    :param code: CheckpointStore 稳定错误码。
    :param operation: 当前 CheckpointStore 操作名。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 可选重试策略覆盖。
    :param conflict_with: 可选冲突对象摘要。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=code,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=retryable,
        conflict_with=conflict_with,
    )


class LangGraphCheckpointReader:
    """LangGraph checkpoint 读取适配器。"""

    def __init__(self, backend: LangGraphCheckpointReaderBackend) -> None:
        """初始化 LangGraph checkpoint 读取适配器。

        :param backend: 实际执行读取的 LangGraph checkpointer。
        :return: None。
        """

        self._backend = backend

    async def load_checkpoint_tuple(
        self,
        *,
        thread_id: str,
        checkpoint_id: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
        missing_as_corrupted: bool,
    ) -> CheckpointTuple:
        """按 thread_id 与 checkpoint_id 精确读取 checkpoint tuple。

        :param thread_id: checkpoint 所属 thread ID。
        :param checkpoint_id: 需要读取的 checkpoint ID。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param missing_as_corrupted: checkpoint 缺失时是否映射为状态损坏。
        :return: 命中的 LangGraph checkpoint tuple。
        :raises CheckpointStoreError: 当 checkpoint 不存在或读取失败时抛出。
        """

        try:
            checkpoint_tuple = await self._backend.aget_tuple(
                build_langgraph_thread_config(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                )
            )
        except TimeoutError as exc:
            raise _build_langgraph_read_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="LangGraph checkpoint 读取超时",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                },
            ) from exc
        except CheckpointStoreError:
            raise
        except Exception as exc:
            raise _build_langgraph_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=operation,
                message="LangGraph checkpoint 读取失败",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                },
            ) from exc
        if checkpoint_tuple is not None:
            return checkpoint_tuple
        if missing_as_corrupted:
            raise _build_langgraph_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="控制面指向的 LangGraph checkpoint 不存在",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                },
            )
        raise _build_langgraph_read_error(
            code=CheckpointErrorCode.CHECKPOINT_NOT_FOUND,
            operation=operation,
            message="指定 LangGraph checkpoint 不存在",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            },
        )

    async def list_checkpoint_tuples(
        self,
        *,
        thread_id: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
        limit: int,
        cursor: str | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[CheckpointTuple]:
        """列出指定 thread 的 checkpoint tuple。

        :param thread_id: 需要查询 checkpoint 历史的 thread ID。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :param limit: 最大返回数量。
        :param cursor: 可选 checkpoint 游标；返回该 checkpoint 之前的历史。
        :param metadata_filter: 可选 LangGraph metadata 过滤条件。
        :return: checkpoint tuple 列表。
        :raises CheckpointStoreError: 当列表读取失败时抛出。
        """

        before = (
            build_langgraph_thread_config(thread_id=thread_id, checkpoint_id=cursor)
            if cursor is not None
            else None
        )
        try:
            items: list[CheckpointTuple] = []
            async for checkpoint_tuple in self._backend.alist(
                build_langgraph_thread_config(thread_id=thread_id),
                filter=metadata_filter,
                before=before,
                limit=limit,
            ):
                items.append(checkpoint_tuple)
            return items
        except TimeoutError as exc:
            raise _build_langgraph_read_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="LangGraph checkpoint 历史读取超时",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={"thread_id": thread_id},
            ) from exc
        except CheckpointStoreError:
            raise
        except Exception as exc:
            raise _build_langgraph_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=operation,
                message="LangGraph checkpoint 历史读取失败",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
                conflict_with={"thread_id": thread_id},
            ) from exc


__all__: tuple[str, ...] = (
    "LangGraphCheckpointReader",
    "LangGraphCheckpointReaderBackend",
)
