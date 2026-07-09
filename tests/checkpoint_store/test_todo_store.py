##################################################################################################
# 文件: tests/checkpoint_store/test_todo_store.py
# 作用: 验证 CheckpointStore TODO 空壳实现会以稳定领域错误显式失败。
# 边界: 仅测试占位实现的公共行为，不接入数据库、LangGraph 或 RuntimeConfig。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.checkpoint_store import (
    CheckpointErrorCode,
    CheckpointOperation,
    CheckpointStoreError,
    EnsureThreadCommandDto,
    TodoCheckpointStore,
)


def test_todo_checkpoint_store_raises_unavailable_error() -> None:
    """验证 TODO CheckpointStore 不会伪造成功结果。

    :return: None。
    """

    store = TodoCheckpointStore()
    command = EnsureThreadCommandDto(
        request_id="req_1",
        trace_id="trace_1",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
    )

    with pytest.raises(CheckpointStoreError) as exc_info:
        asyncio.run(store.ensure_thread(command))

    error = exc_info.value.to_dto()
    assert error.code is CheckpointErrorCode.CHECKPOINT_STORE_UNAVAILABLE
    assert error.operation is CheckpointOperation.ENSURE_THREAD
    assert error.retryable is True
    assert error.request_id == "req_1"
