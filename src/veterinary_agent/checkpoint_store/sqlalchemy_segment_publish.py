##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/sqlalchemy_segment_publish.py
# 作用: 提供基于 SQLAlchemy Core 的 CheckpointStore segment 发布幂等写入仓储。
# 边界: 仅写入 checkpoint_segment_publish 控制面表并校验 checkpoint_thread 存在；
#       不访问 LangGraph checkpoint 内部表、不实现业务发布逻辑。
##################################################################################################

from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError, TimeoutError as SqlAlchemyTimeoutError

from veterinary_agent.checkpoint_store.dto import (
    MarkSegmentPublishedCommandDto,
    MarkSegmentPublishedResultDto,
    SegmentPublishStateDto,
)
from veterinary_agent.checkpoint_store.enums import (
    CheckpointErrorCode,
    CheckpointOperation,
    SegmentPublishStatus,
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


def _normalize_json_map(value: object) -> dict[str, object]:
    """将数据库 JSON 字段归一为字符串键字典。

    :param value: 数据库读取到的 JSON 字段值。
    :return: 归一化后的 JSON map。
    """

    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


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


def _build_segment_publish_error(
    *,
    code: CheckpointErrorCode,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: dict[str, object] | None = None,
) -> CheckpointStoreError:
    """构建 MarkSegmentPublished 领域错误。

    :param code: CheckpointStore 稳定错误码。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 可选重试策略覆盖。
    :param conflict_with: 可选冲突对象摘要。
    :return: CheckpointStore 领域异常对象。
    """

    return CheckpointStoreError(
        code=code,
        operation=CheckpointOperation.MARK_SEGMENT_PUBLISHED,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=retryable,
        conflict_with=conflict_with,
    )


class SqlAlchemyCheckpointSegmentPublishRepository:
    """基于 SQLAlchemy Core 的 checkpoint_segment_publish 仓储。"""

    def __init__(self, engine: Engine) -> None:
        """初始化 checkpoint_segment_publish 仓储。

        :param engine: 已配置好的 SQLAlchemy 同步数据库引擎。
        :return: None。
        """

        self._engine = engine

    def mark_segment_published(
        self,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """幂等标记 segment 已发布。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: segment 发布状态与幂等命中标记。
        :raises CheckpointStoreError: 当 thread 不存在、行结构异常或存储不可用时抛出。
        """

        try:
            try:
                return self._mark_segment_published_once(command=command)
            except IntegrityError as exc:
                return self._mark_segment_published_after_insert_conflict(
                    command=command,
                    insert_error=exc,
                )
        except CheckpointStoreError:
            raise
        except ValidationError as exc:
            raise _build_segment_publish_error(
                code=CheckpointErrorCode.CHECKPOINT_STATE_CORRUPTED,
                message="checkpoint_segment_publish 行结构不符合 DTO 契约",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=False,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except SqlAlchemyTimeoutError as exc:
            raise _build_segment_publish_error(
                code=CheckpointErrorCode.CHECKPOINT_OPERATION_TIMEOUT,
                message="MarkSegmentPublished 数据库操作超时",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise _build_segment_publish_error(
                code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                message="MarkSegmentPublished 数据库操作失败",
                request_id=command.request_id,
                trace_id=command.trace_id,
                retryable=True,
            ) from exc

    def _mark_segment_published_once(
        self,
        *,
        command: MarkSegmentPublishedCommandDto,
    ) -> MarkSegmentPublishedResultDto:
        """执行一次 segment 发布标记事务。

        :param command: 标记 segment 已发布的命令 DTO。
        :return: segment 发布状态与幂等命中标记。
        :raises IntegrityError: 当并发插入命中唯一约束或外键约束时抛出。
        :raises CheckpointStoreError: 当 thread 不存在或新建后无法读取发布记录时抛出。
        """

        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            self._ensure_thread_exists(connection=connection, command=command)
            existing_row = self._fetch_segment_publish_row(
                connection=connection,
                thread_id=command.thread_id,
                segment_id=command.segment_id,
                for_update=True,
            )
            if existing_row is not None:
                return MarkSegmentPublishedResultDto(
                    segment=_segment_row_to_dto(existing_row),
                    idempotent=True,
                )

            connection.execute(
                CHECKPOINT_SEGMENT_PUBLISH_TABLE.insert().values(
                    thread_id=command.thread_id,
                    segment_id=command.segment_id,
                    run_id=command.run_id,
                    task_id=command.task_id,
                    status=SegmentPublishStatus.PUBLISHED.value,
                    published_at=command.published_at,
                    metadata=command.metadata,
                    created_at=now,
                    updated_at=now,
                )
            )
            created_row = self._fetch_segment_publish_row(
                connection=connection,
                thread_id=command.thread_id,
                segment_id=command.segment_id,
                for_update=False,
            )
            if created_row is None:
                raise _build_segment_publish_error(
                    code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
                    message="新建 segment 发布事实后无法读取已创建行",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    retryable=True,
                    conflict_with={
                        "thread_id": command.thread_id,
                        "segment_id": command.segment_id,
                    },
                )
            return MarkSegmentPublishedResultDto(
                segment=_segment_row_to_dto(created_row),
                idempotent=False,
            )

    def _mark_segment_published_after_insert_conflict(
        self,
        *,
        command: MarkSegmentPublishedCommandDto,
        insert_error: IntegrityError,
    ) -> MarkSegmentPublishedResultDto:
        """处理并发插入 segment 发布事实导致的约束冲突。

        :param command: 标记 segment 已发布的命令 DTO。
        :param insert_error: 首次插入时捕获到的 SQLAlchemy 完整性错误。
        :return: 冲突后重读既有发布记录得到的幂等结果。
        :raises CheckpointStoreError: 当 thread 不存在或冲突后仍无法读取既有记录时抛出。
        """

        with self._engine.begin() as connection:
            self._ensure_thread_exists(connection=connection, command=command)
            existing_row = self._fetch_segment_publish_row(
                connection=connection,
                thread_id=command.thread_id,
                segment_id=command.segment_id,
                for_update=True,
            )
            if existing_row is not None:
                return MarkSegmentPublishedResultDto(
                    segment=_segment_row_to_dto(existing_row),
                    idempotent=True,
                )
        raise _build_segment_publish_error(
            code=CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE,
            message="segment 发布事实插入冲突后无法读取既有记录",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=True,
            conflict_with={
                "thread_id": command.thread_id,
                "segment_id": command.segment_id,
            },
        ) from insert_error

    def _ensure_thread_exists(
        self,
        *,
        connection: Connection,
        command: MarkSegmentPublishedCommandDto,
    ) -> None:
        """确认 segment 所属 checkpoint thread 存在。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param command: 标记 segment 已发布的命令 DTO。
        :return: None。
        :raises CheckpointStoreError: 当 thread 不存在时抛出。
        """

        statement = CHECKPOINT_THREAD_TABLE.select().where(
            CHECKPOINT_THREAD_TABLE.c.thread_id == command.thread_id
        )
        if connection.execute(statement).mappings().one_or_none() is not None:
            return
        raise _build_segment_publish_error(
            code=CheckpointErrorCode.CHECKPOINT_THREAD_NOT_FOUND,
            message="checkpoint thread 不存在，无法标记 segment 已发布",
            request_id=command.request_id,
            trace_id=command.trace_id,
            retryable=False,
            conflict_with={"thread_id": command.thread_id},
        )

    def _fetch_segment_publish_row(
        self,
        *,
        connection: Connection,
        thread_id: str,
        segment_id: str,
        for_update: bool,
    ) -> RowMapping | None:
        """按 thread_id 与 segment_id 精确读取 segment 发布事实。

        :param connection: 当前事务中的 SQLAlchemy 数据库连接。
        :param thread_id: segment 所属 checkpoint thread ID。
        :param segment_id: 需要读取的业务分段 ID。
        :param for_update: 是否请求数据库对命中的发布事实行加行级锁。
        :return: 命中的 checkpoint_segment_publish 行；不存在时返回 None。
        """

        statement = CHECKPOINT_SEGMENT_PUBLISH_TABLE.select().where(
            CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.thread_id == thread_id,
            CHECKPOINT_SEGMENT_PUBLISH_TABLE.c.segment_id == segment_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().one_or_none()


__all__: tuple[str, ...] = ("SqlAlchemyCheckpointSegmentPublishRepository",)
