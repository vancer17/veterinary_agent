##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_read.py
# 作用: 提供基于 SQLAlchemy Core 的 CheckpointStore 读取侧控制面仓储，覆盖 thread 查询与
#       segment 发布事实查询。
# 边界: 仅访问项目级 checkpoint 控制面表；不访问 LangGraph checkpoint 内部表、不解释兽医业务语义。
##################################################################################################

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SqlAlchemyTimeoutError

from veterinary_agent.checkpoint_store.dto import (
    CheckpointThreadDto,
    SegmentPublishStateDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
)
from veterinary_agent.checkpoint_store.errors import CheckpointStoreError
from veterinary_agent.checkpoint_store.sqlalchemy_tables import (
    CHECKPOINT_SEGMENT_PUBLISH_TABLE,
    CHECKPOINT_THREAD_TABLE,
)


def _normalize_datetime(value: datetime) -> datetime:
    """将数据库返回的 datetime 归一为 UTC aware datetime。

    :param value: 数据库读取到的时间值。
    :return: 带 UTC 时区信息的 datetime。
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _build_read_error(
    *,
    code: CheckpointErrorCode,
    operation: CheckpointOperation,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建读取侧 CheckpointStore 领域错误。

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


def _thread_row_to_dto(row: RowMapping) -> CheckpointThreadDto:
    """将 checkpoint_thread 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 checkpoint_thread 行。
    :return: 转换后的 checkpoint thread DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return CheckpointThreadDto.model_validate(dict(row))


def _segment_row_to_dto(row: RowMapping) -> SegmentPublishStateDto:
    """将 checkpoint_segment_publish 数据库行转换为 segment 发布状态 DTO。

    :param row: SQLAlchemy mappings 查询返回的 checkpoint_segment_publish 行。
    :return: 转换后的 segment 发布状态 DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return SegmentPublishStateDto.model_validate(
        {
            "segment_id": row["segment_id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "published_at": _normalize_datetime(row["published_at"]),
            "metadata": _normalize_json_map(row["metadata"]),
        }
    )


def _normalize_json_map(value: object) -> dict[str, object]:
    """将数据库 JSON 字段归一为字符串键字典。

    :param value: 数据库读取到的 JSON 字段值。
    :return: 归一化后的 JSON map。
    """

    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


class SqlAlchemyCheckpointReadRepository:
    """基于 SQLAlchemy Core 的 CheckpointStore 读取侧控制面仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化读取侧控制面仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def get_thread(
        self,
        *,
        thread_id: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> CheckpointThreadDto:
        """按 thread_id 读取 checkpoint thread。

        :param thread_id: 需要读取的 checkpoint thread ID。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: 命中的 checkpoint thread DTO。
        :raises CheckpointStoreError: 当 thread 不存在、行结构异常或数据库不可用时抛出。
        """

        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        select(CHECKPOINT_THREAD_TABLE).where(
                            CHECKPOINT_THREAD_TABLE.c.thread_id == thread_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                raise _build_read_error(
                    code=CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND,
                    operation=operation,
                    message="checkpoint thread 不存在",
                    request_id=request_id,
                    trace_id=trace_id,
                    retryable=False,
                    conflict_with={"thread_id": thread_id},
                )
            return _thread_row_to_dto(row)
        except CheckpointStoreError:
            raise
        except ValidationError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="checkpoint_thread 行结构不符合 DTO 契约",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="checkpoint thread 读取数据库操作超时",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=operation,
                message="checkpoint thread 读取数据库操作失败",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc

    def list_published_segments(
        self,
        *,
        thread_id: str,
        operation: CheckpointOperation,
        request_id: str,
        trace_id: str,
    ) -> list[SegmentPublishStateDto]:
        """读取指定 thread 下已记录的 segment 发布事实。

        :param thread_id: 需要查询 segment 发布事实的 checkpoint thread ID。
        :param operation: 当前 CheckpointStore 操作名。
        :param request_id: 本次请求 ID。
        :param trace_id: 本次全链路追踪 ID。
        :return: segment 发布状态 DTO 列表。
        :raises CheckpointStoreError: 当行结构异常或数据库不可用时抛出。
        """

        try:
            with self._engine.begin() as connection:
                rows = (
                    connection.execute(
                        select(CHECKPOINT_SEGMENT_PUBLISH_TABLE)
                        .where(
                            CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.thread_id == thread_id
                        )
                        .order_by(
                            CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.published_at.asc(),
                            CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.segment_id.asc(),
                        )
                    )
                    .mappings()
                    .all()
                )
            return [_segment_row_to_dto(row) for row in rows]
        except ValidationError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                operation=operation,
                message="checkpoint_segment_publish 行结构不符合 DTO 契约",
                request_id=request_id,
                trace_id=trace_id,
                retryable=False,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                operation=operation,
                message="segment 发布事实读取数据库操作超时",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_read_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                operation=operation,
                message="segment 发布事实读取数据库操作失败",
                request_id=request_id,
                trace_id=trace_id,
                retryable=True,
            ) from exc


__all__: tuple[str, ...] = ("SqlAlchemyCheckpointReadRepository",)
