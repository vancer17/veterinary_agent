##################################################################################################
# 文件: tests/conversation_store/test_conversation_runtime_limits.py
# 作用: 验证 ConversationStore 组件级 RuntimeConfig 限制、分页参数和读取 include 开关实际生效。
# 边界: 使用临时 SQLite 与真实 SQLAlchemyConversationStore；不接入 ApiIngress、GraphRuntime 或业务组件。
##################################################################################################

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from veterinary_agent.conversation_store import (
    AppendAssistantSegmentCommandDto,
    AppendMessageCommandDto,
    ConversationErrorCode,
    ConversationMessageRole,
    ConversationStore,
    ConversationStoreError,
    ConversationStoreHistoryConfig,
    ConversationStoreMessageConfig,
    ConversationStoreSettings,
    CreateAssistantMessageCommandDto,
    GetRecentMessagesQueryDto,
    ListMessagesBySessionQueryDto,
    MessageAttachmentRefInputDto,
)
from .helpers import (
    create_migrated_conversation_store,
    ensure_default_session_sync,
)


def _small_limit_settings() -> ConversationStoreSettings:
    """构建测试用小上限 ConversationStore RuntimeConfig。

    :return: 带有较小内容、metadata、附件和分页上限的 ConversationStoreSettings。
    """

    return ConversationStoreSettings(
        message=ConversationStoreMessageConfig(
            max_message_bytes=8,
            max_segment_bytes=6,
            max_metadata_bytes=10,
            max_attachment_refs_per_message=1,
        ),
        history=ConversationStoreHistoryConfig(
            max_list_limit=2,
            max_recent_messages=1,
        ),
    )


@pytest.fixture()
def limited_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ConversationStore]:
    """创建使用小上限配置的 ConversationStore。

    :param tmp_path: pytest 提供的临时目录。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: 已完成迁移且使用小上限配置的 ConversationStore 实例。
    """

    yield from create_migrated_conversation_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        database_name="conversation_limited.sqlite3",
        settings=_small_limit_settings(),
    )


def _append_short_message(store: ConversationStore, content: str = "ok") -> str:
    """写入一条短用户消息并返回 message_id。

    :param store: ConversationStore 实例。
    :param content: 需要写入的短消息正文。
    :return: 写入后的 message_id。
    """

    result = asyncio.run(
        store.append_message(
            AppendMessageCommandDto(
                request_id=f"req_{content}",
                trace_id=f"trace_{content}",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                role=ConversationMessageRole.USER,
                content=content,
                idempotency_key=f"msg_{content}",
            )
        )
    )
    return result.message.message_id


def _create_assistant_message(store: ConversationStore) -> str:
    """创建助手消息容器并返回 message_id。

    :param store: ConversationStore 实例。
    :return: 创建后的助手消息 ID。
    """

    result = asyncio.run(
        store.create_assistant_message(
            CreateAssistantMessageCommandDto(
                request_id="req_assistant",
                trace_id="trace_assistant",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                idempotency_key="assistant_1",
            )
        )
    )
    return result.message.message_id


def test_append_message_rejects_content_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证普通消息正文超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.append_message(
                AppendMessageCommandDto(
                    request_id="req_large",
                    trace_id="trace_large",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    role=ConversationMessageRole.USER,
                    content="123456789",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.MESSAGE_TOO_LARGE
    assert exc_info.value.retryable is False


def test_append_message_rejects_metadata_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证 message metadata 超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.append_message(
                AppendMessageCommandDto(
                    request_id="req_metadata",
                    trace_id="trace_metadata",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    role=ConversationMessageRole.USER,
                    content="ok",
                    metadata={"long": "1234567890"},
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.METADATA_TOO_LARGE


def test_append_message_rejects_attachment_count_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证附件引用数量超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.append_message(
                AppendMessageCommandDto(
                    request_id="req_attachments",
                    trace_id="trace_attachments",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    role=ConversationMessageRole.USER,
                    content="ok",
                    attachments=[
                        MessageAttachmentRefInputDto(
                            attachment_id="att_1",
                            attachment_type="image",
                        ),
                        MessageAttachmentRefInputDto(
                            attachment_id="att_2",
                            attachment_type="image",
                        ),
                    ],
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.ATTACHMENT_LIMIT_EXCEEDED


def test_append_assistant_segment_rejects_content_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证助手 segment 正文超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)
    assistant_message_id = _create_assistant_message(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.append_assistant_segment(
                AppendAssistantSegmentCommandDto(
                    request_id="req_segment",
                    trace_id="trace_segment",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    message_id=assistant_message_id,
                    segment_order=1,
                    content="1234567",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.MESSAGE_TOO_LARGE


def test_list_messages_rejects_limit_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证消息分页 limit 超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.list_messages_by_session(
                ListMessagesBySessionQueryDto(
                    request_id="req_list",
                    trace_id="trace_list",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    limit=3,
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.INVALID_ARGUMENT


def test_get_recent_messages_rejects_limit_above_runtime_limit(
    limited_store: ConversationStore,
) -> None:
    """验证最近消息 limit 超过 RuntimeConfig 上限时被拒绝。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.get_recent_messages(
                GetRecentMessagesQueryDto(
                    request_id="req_recent",
                    trace_id="trace_recent",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    limit=2,
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.INVALID_ARGUMENT


def test_list_messages_rejects_invalid_cursor(
    limited_store: ConversationStore,
) -> None:
    """验证非法分页 cursor 会映射为稳定入参错误。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            limited_store.list_messages_by_session(
                ListMessagesBySessionQueryDto(
                    request_id="req_cursor",
                    trace_id="trace_cursor",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    limit=1,
                    cursor="not-a-number",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.INVALID_ARGUMENT


def test_list_messages_can_exclude_segments_and_attachments(
    limited_store: ConversationStore,
) -> None:
    """验证读取消息时 include 开关可以排除 segments 与附件引用。

    :param limited_store: 使用小上限配置的 ConversationStore。
    :return: None。
    """

    ensure_default_session_sync(limited_store)
    _append_short_message(limited_store, content="aa")
    page = asyncio.run(
        limited_store.list_messages_by_session(
            ListMessagesBySessionQueryDto(
                request_id="req_include",
                trace_id="trace_include",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                limit=1,
                include_segments=False,
                include_attachments=False,
            )
        )
    )

    assert len(page.items) == 1
    assert page.items[0].segments == []
    assert page.items[0].attachments == []
