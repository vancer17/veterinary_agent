##################################################################################################
# 文件: tests/conversation_store/test_conversation_todo_store.py
# 作用: 验证 ConversationStore TODO 空壳实现会以稳定领域错误显式失败。
# 边界: 仅测试占位实现的公共行为，不接入数据库、RuntimeConfig、ApiIngress 或业务组件。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.conversation_store import (
    ConversationErrorCode,
    ConversationOperation,
    ConversationStoreError,
    EnsureSessionCommandDto,
    TodoConversationStore,
)


def test_todo_conversation_store_raises_unavailable_error() -> None:
    """验证 TODO ConversationStore 不会伪造成功结果。

    :return: None。
    """

    store = TodoConversationStore()
    command = EnsureSessionCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
    )

    with pytest.raises(ConversationStoreError) as exc_info:
        asyncio.run(store.ensure_session(command))

    error = exc_info.value.to_dto()
    assert error.code is ConversationErrorCode.STORE_UNAVAILABLE
    assert error.operation is ConversationOperation.ENSURE_SESSION
    assert error.retryable is True
    assert error.request_id == "req_1"
