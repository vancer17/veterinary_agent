##################################################################################################
# 文件: tests/conversation_store/test_conversation_dto_contract.py
# 作用: 验证 ConversationStore DTO 与错误契约的严格字段校验、枚举承载和默认重试策略。
# 边界: 仅测试公共 DTO 与领域错误，不测试数据库、FastAPI、GraphRuntime 或业务组件集成。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent.conversation_store import (
    AppendMessageCommandDto,
    ConversationErrorCode,
    ConversationMessageRole,
    ConversationOperation,
    MessageAttachmentRefInputDto,
)
from veterinary_agent.conversation_store import (
    build_conversation_store_error_dto,
    is_conversation_error_retryable_by_default,
)


def test_append_message_command_accepts_attachment_refs() -> None:
    """验证追加消息命令可以承载附件引用输入。

    :return: None。
    """

    command = AppendMessageCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
        role=ConversationMessageRole.USER,
        content="猫咪今天不吃饭",
        metadata={"request_id": "req_1"},
        attachments=[
            MessageAttachmentRefInputDto(
                attachment_id="att_1",
                attachment_type="image",
                metadata={"sha256": "abc"},
            )
        ],
    )

    assert command.role is ConversationMessageRole.USER
    assert command.attachments[0].attachment_id == "att_1"


def test_conversation_dto_rejects_extra_fields() -> None:
    """验证 ConversationStore DTO 拒绝未声明字段。

    :return: None。
    """

    with pytest.raises(ValidationError):
        AppendMessageCommandDto.model_validate(
            {
                "request_id": "req_1",
                "trace_id": "trace_1",
                "session_id": "session_1",
                "user_id": "user_1",
                "pet_id": "pet_1",
                "role": "user",
                "content": "hello",
                "unexpected_field": "not_allowed",
            }
        )


def test_conversation_error_retryable_defaults_are_stable() -> None:
    """验证 ConversationStore 错误码默认重试策略稳定。

    :return: None。
    """

    assert (
        is_conversation_error_retryable_by_default(
            ConversationErrorCode.STORE_UNAVAILABLE
        )
        is True
    )
    assert (
        is_conversation_error_retryable_by_default(
            ConversationErrorCode.SESSION_PET_CONFLICT
        )
        is False
    )
    assert (
        is_conversation_error_retryable_by_default(
            ConversationErrorCode.OPERATION_TIMEOUT
        )
        is True
    )


def test_build_conversation_error_dto_uses_default_retryable() -> None:
    """验证构建错误 DTO 时会按错误码补齐默认重试策略。

    :return: None。
    """

    error = build_conversation_store_error_dto(
        code=ConversationErrorCode.SESSION_NOT_FOUND,
        operation=ConversationOperation.GET_SESSION,
        message="missing",
    )

    assert error.retryable is False
