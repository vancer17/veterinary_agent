##################################################################################################
# 文件: src/veterinary_agent/conversation_store/sqlalchemy_common.py
# 作用: 提供 ConversationStore SQLAlchemy 仓储复用的行转换、JSON 大小计算、错误构建和锚点校验工具。
# 边界: 仅服务于 ConversationStore 包内 SQL 实现，不暴露为跨包公共 API，不承载业务策略。
##################################################################################################

import json
from typing import NoReturn

from sqlalchemy.engine import RowMapping

from veterinary_agent.conversation_store.dto import (
    ConversationMessageDto,
    ConversationSessionDto,
    JsonMap,
    MessageAttachmentRefDto,
    MessageSegmentDto,
)
from veterinary_agent.conversation_store.enums import (
    ConversationErrorCode,
    ConversationOperation,
)
from veterinary_agent.conversation_store.errors import ConversationStoreError


def measure_json_bytes(value: object) -> int:
    """计算 JSON 值序列化后的 UTF-8 字节数。

    :param value: 需要计算大小的 JSON 兼容值。
    :return: 序列化后的 UTF-8 字节数。
    :raises TypeError: 当值无法被 JSON 序列化时抛出。
    :raises ValueError: 当值包含 JSON 不支持的浮点特殊值时抛出。
    """

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(encoded.encode("utf-8"))


def measure_text_bytes(value: str) -> int:
    """计算文本值的 UTF-8 字节数。

    :param value: 需要计算大小的文本。
    :return: 文本 UTF-8 编码后的字节数。
    """

    return len(value.encode("utf-8"))


def row_to_session_dto(row: RowMapping) -> ConversationSessionDto:
    """将 conversation_session 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 conversation_session 行。
    :return: 转换后的 conversation session DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return ConversationSessionDto.model_validate(dict(row))


def row_to_message_dto(
    row: RowMapping,
    *,
    segments: list[MessageSegmentDto] | None = None,
    attachments: list[MessageAttachmentRefDto] | None = None,
) -> ConversationMessageDto:
    """将 conversation_message 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 conversation_message 行。
    :param segments: 可选助手消息分段 DTO 列表。
    :param attachments: 可选附件引用 DTO 列表。
    :return: 转换后的 conversation message DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    payload = dict(row)
    payload["segments"] = [] if segments is None else segments
    payload["attachments"] = [] if attachments is None else attachments
    return ConversationMessageDto.model_validate(payload)


def row_to_segment_dto(row: RowMapping) -> MessageSegmentDto:
    """将 conversation_message_segment 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 conversation_message_segment 行。
    :return: 转换后的 message segment DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return MessageSegmentDto.model_validate(dict(row))


def row_to_attachment_ref_dto(row: RowMapping) -> MessageAttachmentRefDto:
    """将 conversation_attachment_ref 数据库行转换为公共 DTO。

    :param row: SQLAlchemy mappings 查询返回的 conversation_attachment_ref 行。
    :return: 转换后的附件引用 DTO。
    :raises ValidationError: 当数据库行结构无法满足 DTO 契约时抛出。
    """

    return MessageAttachmentRefDto.model_validate(dict(row))


def build_conversation_error(
    *,
    code: ConversationErrorCode,
    operation: ConversationOperation,
    message: str,
    request_id: str,
    trace_id: str,
    retryable: bool | None = None,
    conflict_with: JsonMap | None = None,
) -> ConversationStoreError:
    """构建 ConversationStore 领域错误。

    :param code: ConversationStore 稳定错误码。
    :param operation: 当前 ConversationStore 操作名。
    :param message: 面向工程排障的错误说明。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param retryable: 可选重试策略覆盖。
    :param conflict_with: 可选冲突对象摘要。
    :return: ConversationStore 领域异常对象。
    """

    return ConversationStoreError(
        code=code,
        operation=operation,
        message=message,
        request_id=request_id,
        trace_id=trace_id,
        retryable=retryable,
        conflict_with=conflict_with,
    )


def raise_session_anchor_conflict(
    *,
    operation: ConversationOperation,
    request_id: str,
    trace_id: str,
    session: ConversationSessionDto,
    requested_user_id: str | None,
    requested_pet_id: str | None,
) -> None:
    """校验请求身份锚点与 session 锚点一致。

    :param operation: 当前 ConversationStore 操作名。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param session: 已读取的 conversation session。
    :param requested_user_id: 请求携带的 user_id；为空时不校验。
    :param requested_pet_id: 请求携带的 pet_id；为空时不校验。
    :return: None。
    :raises ConversationStoreError: 当 user_id 或 pet_id 与 session 锚点不一致时抛出。
    """

    if requested_user_id is not None and session.user_id != requested_user_id:
        raise build_conversation_error(
            code=ConversationErrorCode.SESSION_USER_CONFLICT,
            operation=operation,
            message="conversation session 已锚定到不同 user_id",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                "session_id": session.session_id,
                "existing_user_id": session.user_id,
                "requested_user_id": requested_user_id,
            },
        )
    if requested_pet_id is not None and session.pet_id != requested_pet_id:
        raise build_conversation_error(
            code=ConversationErrorCode.SESSION_PET_CONFLICT,
            operation=operation,
            message="conversation session 已锚定到不同 pet_id",
            request_id=request_id,
            trace_id=trace_id,
            retryable=False,
            conflict_with={
                "session_id": session.session_id,
                "existing_pet_id": session.pet_id,
                "requested_pet_id": requested_pet_id,
            },
        )


def raise_not_found(
    *,
    operation: ConversationOperation,
    request_id: str,
    trace_id: str,
    session_id: str,
) -> NoReturn:
    """抛出 session 不存在错误。

    :param operation: 当前 ConversationStore 操作名。
    :param request_id: 本次请求 ID。
    :param trace_id: 本次全链路追踪 ID。
    :param session_id: 未命中的 session ID。
    :return: 该函数总是抛出异常，不会返回。
    :raises ConversationStoreError: 始终抛出 SESSION_NOT_FOUND。
    """

    raise build_conversation_error(
        code=ConversationErrorCode.SESSION_NOT_FOUND,
        operation=operation,
        message="conversation session 不存在",
        request_id=request_id,
        trace_id=trace_id,
        retryable=False,
        conflict_with={"session_id": session_id},
    )


def merge_metadata(
    *,
    original: JsonMap,
    patch: JsonMap,
) -> JsonMap:
    """合并 metadata 映射。

    :param original: 既有 metadata。
    :param patch: 需要追加或覆盖的 metadata。
    :return: 合并后的 metadata 映射。
    """

    return {**original, **patch}


__all__: tuple[str, ...] = (
    "build_conversation_error",
    "measure_json_bytes",
    "measure_text_bytes",
    "merge_metadata",
    "raise_not_found",
    "raise_session_anchor_conflict",
    "row_to_attachment_ref_dto",
    "row_to_message_dto",
    "row_to_segment_dto",
    "row_to_session_dto",
)
