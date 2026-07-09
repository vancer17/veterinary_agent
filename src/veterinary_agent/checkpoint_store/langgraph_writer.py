##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/langgraph_writer.py
# 作用: 封装 LangGraph checkpointer 的 checkpoint 写入能力，为 CheckpointStore.save_checkpoint
#       提供项目级 metadata envelope 与 graph/business state 持久化适配。
# 边界: 仅调用 LangGraph checkpointer 公共 API；不访问 LangGraph 物理表、不推进项目控制面版本。
##################################################################################################

from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    empty_checkpoint,
)

from veterinary_agent.checkpoint_store.checkpoint_mapper import (
    CHECKPOINT_BUSINESS_STATE_CHANNEL,
    CHECKPOINT_GRAPH_STATE_CHANNEL,
    CHECKPOINT_METADATA_ENVELOPE_KEY,
    CHECKPOINT_METADATA_MANAGED_FLAG,
)
from veterinary_agent.checkpoint_store.dto import SaveCheckpointCommandDto
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.langgraph_provider import (
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)


class LangGraphCheckpointWriterBackend(Protocol):
    """LangGraph checkpoint 写入后端协议。"""

    async def aput(
        self,
        config: LangGraphRunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> LangGraphRunnableConfig:
        """异步写入单个 LangGraph checkpoint。

        :param config: LangGraph thread 运行配置。
        :param checkpoint: 需要持久化的 checkpoint 状态体。
        :param metadata: 需要随 checkpoint 一起保存的 metadata。
        :param new_versions: 本次写入更新的 channel version 映射。
        :return: LangGraph checkpointer 返回的下一步运行配置。
        """

        ...


def _build_langgraph_write_error(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 LangGraph 写入适配层领域错误。

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


def _build_checkpoint_id() -> str:
    """构建项目级 checkpoint ID。

    :return: 带业务前缀的 checkpoint ID。
    """

    return f"checkpoint_{uuid4().hex}"


def _build_channel_version(*, version: int, channel_name: str) -> str:
    """构建 LangGraph channel version。

    :param version: 项目级 checkpoint 版本号。
    :param channel_name: 本次写入的 channel 名称。
    :return: 可传递给 LangGraph checkpointer 的 channel version。
    """

    return f"{version}:{channel_name}"


def _build_checkpoint(
    *,
    command: SaveCheckpointCommandDto,
    checkpoint_id: str,
    created_at: datetime,
    new_version: int,
) -> Checkpoint:
    """根据 SaveCheckpoint 命令构建 LangGraph checkpoint 状态体。

    :param command: 保存 checkpoint 的命令 DTO。
    :param checkpoint_id: 本次保存使用的 checkpoint ID。
    :param created_at: 本次保存使用的创建时间。
    :param new_version: 保存成功后对应的项目级 checkpoint 版本号。
    :return: 可传递给 LangGraph checkpointer 的 checkpoint 状态体。
    """

    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    checkpoint["ts"] = created_at.isoformat()
    checkpoint["channel_values"] = {
        CHECKPOINT_GRAPH_STATE_CHANNEL: command.graph_state.model_dump(mode="json"),
        CHECKPOINT_BUSINESS_STATE_CHANNEL: command.business_state.model_dump(
            mode="json"
        ),
    }
    checkpoint["channel_versions"] = {
        CHECKPOINT_GRAPH_STATE_CHANNEL: _build_channel_version(
            version=new_version,
            channel_name=CHECKPOINT_GRAPH_STATE_CHANNEL,
        ),
        CHECKPOINT_BUSINESS_STATE_CHANNEL: _build_channel_version(
            version=new_version,
            channel_name=CHECKPOINT_BUSINESS_STATE_CHANNEL,
        ),
    }
    checkpoint["versions_seen"] = {}
    checkpoint["updated_channels"] = [
        CHECKPOINT_GRAPH_STATE_CHANNEL,
        CHECKPOINT_BUSINESS_STATE_CHANNEL,
    ]
    return checkpoint


def _build_checkpoint_metadata(
    *,
    command: SaveCheckpointCommandDto,
    created_at: datetime,
    new_version: int,
    state_size_bytes: int,
) -> CheckpointMetadata:
    """根据 SaveCheckpoint 命令构建项目级 LangGraph checkpoint metadata。

    :param command: 保存 checkpoint 的命令 DTO。
    :param created_at: 本次保存使用的创建时间。
    :param new_version: 保存成功后对应的项目级 checkpoint 版本号。
    :param state_size_bytes: checkpoint 状态体序列化字节数。
    :return: 可传递给 LangGraph checkpointer 的 metadata。
    """

    metadata: dict[str, Any] = {
        CHECKPOINT_METADATA_ENVELOPE_KEY: {
            CHECKPOINT_METADATA_MANAGED_FLAG: True,
            "version": new_version,
            "run_id": command.run_id,
            "graph_name": command.graph_name,
            "graph_version": command.graph_version,
            "state_schema_version": command.state_schema_version,
            "status": command.status.value,
            "current_node": command.current_node,
            "state_size_bytes": state_size_bytes,
            "created_at": created_at.isoformat(),
            "metadata": command.metadata,
        }
    }
    return cast(CheckpointMetadata, metadata)


def _extract_checkpoint_id(config: LangGraphRunnableConfig) -> str | None:
    """从 LangGraph 返回配置中提取 checkpoint ID。

    :param config: LangGraph checkpointer 返回的运行配置。
    :return: checkpoint ID；无法提取时返回 None。
    """

    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    checkpoint_id = configurable.get("checkpoint_id")
    if isinstance(checkpoint_id, str) and checkpoint_id.strip():
        return checkpoint_id
    return None


class LangGraphCheckpointWriter:
    """LangGraph checkpoint 写入适配器。"""

    def __init__(self, backend: LangGraphCheckpointWriterBackend) -> None:
        """初始化 LangGraph checkpoint 写入适配器。

        :param backend: 实际执行写入的 LangGraph checkpointer。
        :return: None。
        """

        self._backend = backend

    async def save_checkpoint(
        self,
        *,
        command: SaveCheckpointCommandDto,
        state_size_bytes: int,
    ) -> str:
        """保存单个项目托管的 LangGraph checkpoint。

        :param command: 保存 checkpoint 的命令 DTO。
        :param state_size_bytes: checkpoint 状态体序列化字节数。
        :return: LangGraph 最终保存的 checkpoint ID。
        :raises CheckpointStoreError: 当 LangGraph 写入失败或返回配置异常时抛出。
        """

        operation = CheckpointOperation.SAVE_CHECKPOINT
        new_version = command.expected_version + 1
        created_at = datetime.now(UTC)
        checkpoint_id = _build_checkpoint_id()
        checkpoint = _build_checkpoint(
            command=command,
            checkpoint_id=checkpoint_id,
            created_at=created_at,
            new_version=new_version,
        )
        metadata = _build_checkpoint_metadata(
            command=command,
            created_at=created_at,
            new_version=new_version,
            state_size_bytes=state_size_bytes,
        )
        new_versions: ChannelVersions = {
            CHECKPOINT_GRAPH_STATE_CHANNEL: _build_channel_version(
                version=new_version,
                channel_name=CHECKPOINT_GRAPH_STATE_CHANNEL,
            ),
            CHECKPOINT_BUSINESS_STATE_CHANNEL: _build_channel_version(
                version=new_version,
                channel_name=CHECKPOINT_BUSINESS_STATE_CHANNEL,
            ),
        }
        try:
            next_config = await self._backend.aput(
                build_langgraph_thread_config(thread_id=command.thread_id),
                checkpoint,
                metadata,
                new_versions,
            )
        except TimeoutError as exc:
            raise _build_langgraph_write_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="LangGraph checkpoint 写入超时",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
                conflict_with={"thread_id": command.thread_id},
            ) from exc
        except CheckpointStoreError:
            raise
        except Exception as exc:
            raise _build_langgraph_write_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=operation,
                message="LangGraph checkpoint 写入失败",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
                conflict_with={"thread_id": command.thread_id},
            ) from exc

        saved_checkpoint_id = _extract_checkpoint_id(next_config)
        if saved_checkpoint_id == checkpoint_id:
            return checkpoint_id
        raise _build_langgraph_write_error(
            code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
            operation=operation,
            message="LangGraph checkpoint 写入返回的 checkpoint_id 与请求不一致",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={
                "thread_id": command.thread_id,
                "expected_checkpoint_id": checkpoint_id,
                "actual_checkpoint_id": saved_checkpoint_id,
            },
        )


__all__: tuple[str, ...] = (
    "LangGraphCheckpointWriter",
    "LangGraphCheckpointWriterBackend",
)
