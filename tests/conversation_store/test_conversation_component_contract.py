##################################################################################################
# 文件: tests/conversation_store/test_conversation_component_contract.py
# 作用: 验证 ConversationStore 组件级公开契约闭环，覆盖 session、消息、附件、助手 segment、
#       finalize、分页、最近消息、幂等与状态冲突。
# 边界: 使用临时 SQLite 验证 ConversationStore 自有表；不接入 ApiIngress、GraphRuntime、CheckpointStore 或兽医业务组件。
##################################################################################################

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from veterinary_agent.conversation_store import (
    AppendAssistantSegmentCommandDto,
    AppendMessageCommandDto,
    ArchiveSessionCommandDto,
    CloseSessionCommandDto,
    ConversationErrorCode,
    ConversationMessageRole,
    ConversationOperation,
    ConversationStore,
    ConversationStoreError,
    CreateAssistantMessageCommandDto,
    EnsureSessionCommandDto,
    FinalizeAssistantMessageCommandDto,
    GetRecentMessagesQueryDto,
    GetSessionQueryDto,
    ListMessagesBySessionQueryDto,
    MessageAttachmentRefInputDto,
)
from .helpers import (
    create_migrated_conversation_store,
    ensure_default_session,
)


@pytest.fixture()
def conversation_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ConversationStore]:
    """创建测试用 SQLAlchemy ConversationStore。

    :param tmp_path: pytest 提供的临时目录。
    :param monkeypatch: pytest monkeypatch 夹具。
    :return: 已完成迁移的 ConversationStore 实例。
    """

    yield from create_migrated_conversation_store(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


def test_ensure_session_is_idempotent(
    conversation_store: ConversationStore,
) -> None:
    """验证 EnsureSession 创建与重复确认幂等。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    first = asyncio.run(
        conversation_store.ensure_session(
            EnsureSessionCommandDto(
                request_id="req_1",
                trace_id="trace_1",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                metadata={"source": "test"},
            )
        )
    )
    second = asyncio.run(
        conversation_store.ensure_session(
            EnsureSessionCommandDto(
                request_id="req_2",
                trace_id="trace_2",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    assert first.created_new is True
    assert second.created_new is False
    assert second.session.session_id == "session_1"
    assert second.session.pet_id == "pet_1"


def test_ensure_session_rejects_pet_conflict(
    conversation_store: ConversationStore,
) -> None:
    """验证 EnsureSession 拒绝同一 session 绑定不同 pet_id。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            conversation_store.ensure_session(
                EnsureSessionCommandDto(
                    request_id="req_2",
                    trace_id="trace_2",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_2",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.SESSION_PET_CONFLICT
    assert exc_info.value.operation is ConversationOperation.ENSURE_SESSION


def test_ensure_session_rejects_user_conflict(
    conversation_store: ConversationStore,
) -> None:
    """验证 EnsureSession 拒绝同一 session 绑定不同 user_id。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            conversation_store.ensure_session(
                EnsureSessionCommandDto(
                    request_id="req_2",
                    trace_id="trace_2",
                    session_id="session_1",
                    user_id="user_2",
                    pet_id="pet_1",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.SESSION_USER_CONFLICT
    assert exc_info.value.operation is ConversationOperation.ENSURE_SESSION


def test_append_message_assigns_sequence_and_is_idempotent(
    conversation_store: ConversationStore,
) -> None:
    """验证消息写入分配稳定 sequence_no 并支持幂等。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    command = AppendMessageCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
        role=ConversationMessageRole.USER,
        content="猫咪今天不吃饭",
        idempotency_key="turn_1_user_message",
        metadata={"generation_profile": "standard"},
        attachments=[
            MessageAttachmentRefInputDto(
                attachment_id="att_1",
                attachment_type="image",
                metadata={"hash": "abc"},
            )
        ],
    )

    first = asyncio.run(conversation_store.append_message(command))
    second = asyncio.run(conversation_store.append_message(command))

    assert first.idempotent is False
    assert second.idempotent is True
    assert second.message.message_id == first.message.message_id
    assert first.message.sequence_no == 1
    assert first.message.attachments[0].attachment_id == "att_1"


def test_assistant_segment_flow_and_recent_messages(
    conversation_store: ConversationStore,
) -> None:
    """验证助手消息容器、分段追加、完成和最近消息读取流程。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    user_message = asyncio.run(
        conversation_store.append_message(
            AppendMessageCommandDto(
                request_id="req_1",
                trace_id="trace_1",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                role=ConversationMessageRole.USER,
                content="猫咪呕吐两次",
                idempotency_key="turn_1_user",
            )
        )
    )
    assistant = asyncio.run(
        conversation_store.create_assistant_message(
            CreateAssistantMessageCommandDto(
                request_id="req_2",
                trace_id="trace_2",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                reply_to_message_id=user_message.message.message_id,
                idempotency_key="turn_1_assistant",
            )
        )
    )
    first_segment = asyncio.run(
        conversation_store.append_assistant_segment(
            AppendAssistantSegmentCommandDto(
                request_id="req_3",
                trace_id="trace_3",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                message_id=assistant.message.message_id,
                segment_order=1,
                content="先观察精神状态。",
                idempotency_key="turn_1_seg_1",
            )
        )
    )
    duplicate_segment = asyncio.run(
        conversation_store.append_assistant_segment(
            AppendAssistantSegmentCommandDto(
                request_id="req_4",
                trace_id="trace_4",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                message_id=assistant.message.message_id,
                segment_order=1,
                content="先观察精神状态。",
                idempotency_key="turn_1_seg_1",
            )
        )
    )
    asyncio.run(
        conversation_store.append_assistant_segment(
            AppendAssistantSegmentCommandDto(
                request_id="req_5",
                trace_id="trace_5",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                message_id=assistant.message.message_id,
                segment_order=2,
                content="如果持续呕吐请就医。",
                idempotency_key="turn_1_seg_2",
            )
        )
    )
    finalized = asyncio.run(
        conversation_store.finalize_assistant_message(
            FinalizeAssistantMessageCommandDto(
                request_id="req_6",
                trace_id="trace_6",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                message_id=assistant.message.message_id,
            )
        )
    )
    recent = asyncio.run(
        conversation_store.get_recent_messages(
            GetRecentMessagesQueryDto(
                request_id="req_7",
                trace_id="trace_7",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                limit=2,
            )
        )
    )

    assert first_segment.idempotent is False
    assert duplicate_segment.idempotent is True
    assert finalized.message.content == "先观察精神状态。如果持续呕吐请就医。"
    assert len(finalized.message.segments) == 2
    assert [message.sequence_no for message in recent.items] == [1, 2]


def test_append_assistant_segment_rejects_finalized_message(
    conversation_store: ConversationStore,
) -> None:
    """验证 assistant message 完成后拒绝继续追加 segment。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    assistant = asyncio.run(
        conversation_store.create_assistant_message(
            CreateAssistantMessageCommandDto(
                request_id="req_assistant",
                trace_id="trace_assistant",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )
    asyncio.run(
        conversation_store.finalize_assistant_message(
            FinalizeAssistantMessageCommandDto(
                request_id="req_finalize",
                trace_id="trace_finalize",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                message_id=assistant.message.message_id,
                final_content="已完成",
            )
        )
    )

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            conversation_store.append_assistant_segment(
                AppendAssistantSegmentCommandDto(
                    request_id="req_segment",
                    trace_id="trace_segment",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    message_id=assistant.message.message_id,
                    segment_order=1,
                    content="late",
                )
            )
        )

    assert exc_info.value.code is ConversationErrorCode.MESSAGE_ALREADY_FINALIZED


def test_list_messages_paginates_by_sequence(
    conversation_store: ConversationStore,
) -> None:
    """验证按 session 查询消息支持 sequence_no 游标分页。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    for index in range(3):
        asyncio.run(
            conversation_store.append_message(
                AppendMessageCommandDto(
                    request_id=f"req_{index}",
                    trace_id=f"trace_{index}",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    role=ConversationMessageRole.USER,
                    content=f"message {index}",
                    idempotency_key=f"message_{index}",
                )
            )
        )

    first_page = asyncio.run(
        conversation_store.list_messages_by_session(
            ListMessagesBySessionQueryDto(
                request_id="req_list_1",
                trace_id="trace_list_1",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                limit=2,
            )
        )
    )
    second_page = asyncio.run(
        conversation_store.list_messages_by_session(
            ListMessagesBySessionQueryDto(
                request_id="req_list_2",
                trace_id="trace_list_2",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
                limit=2,
                cursor=first_page.next_cursor,
            )
        )
    )

    assert [message.sequence_no for message in first_page.items] == [1, 2]
    assert first_page.next_cursor == "2"
    assert [message.sequence_no for message in second_page.items] == [3]
    assert second_page.next_cursor is None


def test_close_session_rejects_follow_up_writes(
    conversation_store: ConversationStore,
) -> None:
    """验证关闭 session 后拒绝继续写入普通消息。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    closed = asyncio.run(
        conversation_store.close_session(
            CloseSessionCommandDto(
                request_id="req_close",
                trace_id="trace_close",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(
            conversation_store.append_message(
                AppendMessageCommandDto(
                    request_id="req_after_close",
                    trace_id="trace_after_close",
                    session_id="session_1",
                    user_id="user_1",
                    pet_id="pet_1",
                    role=ConversationMessageRole.USER,
                    content="still writing",
                )
            )
        )

    assert closed.session.status.value == "closed"
    assert exc_info.value.code is ConversationErrorCode.SESSION_CLOSED


def test_archive_session_is_readable(
    conversation_store: ConversationStore,
) -> None:
    """验证归档 session 后仍可读取 session 摘要。

    :param conversation_store: 测试用 ConversationStore。
    :return: None。
    """

    asyncio.run(ensure_default_session(conversation_store))
    asyncio.run(
        conversation_store.archive_session(
            ArchiveSessionCommandDto(
                request_id="req_archive",
                trace_id="trace_archive",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    session = asyncio.run(
        conversation_store.get_session(
            GetSessionQueryDto(
                request_id="req_get",
                trace_id="trace_get",
                session_id="session_1",
                user_id="user_1",
                pet_id="pet_1",
            )
        )
    )

    assert session.status.value == "archived"
