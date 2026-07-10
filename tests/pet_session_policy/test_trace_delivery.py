##################################################################################################
# 文件: tests/pet_session_policy/test_trace_delivery.py
# 作用: 验证 PetSessionPolicy 允许与阻断判定的 trace 摘要内容、TODO 降级和异常隔离。
# 边界: 使用测试 trace sink 与 LogicTraceStore TODO 空壳；不实现或访问真实 LogicTraceStore。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.conversation_store import (
    ConversationErrorCode,
    ConversationOperation,
    ConversationStoreError,
    EnsureSessionResultDto,
)
from veterinary_agent.pet_session_policy import (
    TODO_TRACE_ERROR_CODE,
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionTraceRecordDto,
    PetSessionTraceWriteStatus,
    TodoPetSessionTraceSink,
)

from .helpers import (
    FakeConversationStore,
    RaisingTraceSink,
    RecordingTraceSink,
    build_policy,
    build_request,
    build_session,
)


def test_allow_decision_writes_complete_trace_summary() -> None:
    """验证允许继续时 trace 摘要包含完整安全判定信息。

    :return: None。
    """

    trace_sink = RecordingTraceSink()
    policy = build_policy(
        store=FakeConversationStore(
            result=EnsureSessionResultDto(
                session=build_session(),
                created_new=True,
            )
        ),
        trace_sink=trace_sink,
    )

    context = asyncio.run(policy.ensure_context(build_request()))

    assert len(trace_sink.records) == 1
    record = trace_sink.records[0]
    assert record.request_id == context.request_id
    assert record.trace_id == context.trace_id
    assert record.requested_pet_id == "pet_1"
    assert record.current_pet_id == "pet_1"
    assert record.decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND
    assert record.policy_action is PetSessionPolicyAction.ALLOW_CONTINUE
    assert record.allow_continue is True
    assert record.error_code is None
    assert record.params_version == context.params_version
    assert record.config_snapshot_id == context.config_snapshot_id


def test_missing_field_writes_block_trace_without_current_pet() -> None:
    """验证缺少 pet_id 时 trace 摘要不产生 current_pet_id。

    :return: None。
    """

    trace_sink = RecordingTraceSink()
    policy = build_policy(
        store=FakeConversationStore(),
        trace_sink=trace_sink,
    )

    with pytest.raises(PetSessionPolicyError):
        asyncio.run(policy.ensure_context(build_request(pet_id=None)))

    assert len(trace_sink.records) == 1
    record = trace_sink.records[0]
    assert record.requested_pet_id is None
    assert record.current_pet_id is None
    assert record.missing_field == "pet_id"
    assert record.decision is PetSessionDecision.BLOCK_MISSING_PET_ID
    assert record.policy_action is PetSessionPolicyAction.BLOCK_REQUEST
    assert record.allow_continue is False


def test_store_conflict_trace_uses_error_code_without_existing_anchor() -> None:
    """验证存储锚点冲突 trace 不包含既有宠物或用户真实标识。

    :return: None。
    """

    trace_sink = RecordingTraceSink()
    policy = build_policy(
        store=FakeConversationStore(
            error=ConversationStoreError(
                code=ConversationErrorCode.SESSION_PET_CONFLICT,
                operation=ConversationOperation.ENSURE_SESSION,
                message="pet conflict",
                conflict_with={"existing_pet_id": "pet_secret"},
            )
        ),
        trace_sink=trace_sink,
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request(pet_id="pet_requested")))

    record_json = trace_sink.records[0].model_dump_json()
    error_json = exc_info.value.to_dto().model_dump_json()
    assert trace_sink.records[0].store_error_code is (
        ConversationErrorCode.SESSION_PET_CONFLICT
    )
    assert "pet_secret" not in record_json
    assert "pet_secret" not in error_json


def test_todo_trace_sink_returns_explicit_degraded_result() -> None:
    """验证 LogicTraceStore TODO 空壳返回显式可重试降级结果。

    :return: None。
    """

    sink = TodoPetSessionTraceSink()
    record = PetSessionTraceRecordDto(
        request_id="req_1",
        trace_id="trace_1",
        decision=PetSessionDecision.ALLOW_EXISTING_SESSION,
        policy_action=PetSessionPolicyAction.ALLOW_CONTINUE,
        allow_continue=True,
        retryable=False,
    )

    result = asyncio.run(sink.write_decision(record))

    assert result.status is PetSessionTraceWriteStatus.DEGRADED
    assert result.error_code == TODO_TRACE_ERROR_CODE
    assert result.retryable is True


def test_trace_sink_exception_does_not_change_allow_decision() -> None:
    """验证 trace sink 异常只标记降级，不改变允许继续判定。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(
            result=EnsureSessionResultDto(
                session=build_session(),
                created_new=True,
            )
        ),
        trace_sink=RaisingTraceSink(),
    )

    context = asyncio.run(policy.ensure_context(build_request()))

    assert context.decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND
    assert context.trace_delivery_status is PetSessionTraceWriteStatus.DEGRADED


def test_trace_sink_exception_preserves_original_block_error() -> None:
    """验证阻断 trace 写入异常不覆盖原始业务错误。

    :return: None。
    """

    policy = build_policy(
        store=FakeConversationStore(
            error=ConversationStoreError(
                code=ConversationErrorCode.SESSION_PET_CONFLICT,
                operation=ConversationOperation.ENSURE_SESSION,
                message="pet conflict",
            )
        ),
        trace_sink=RaisingTraceSink(),
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is PetSessionPolicyErrorCode.PET_MISMATCH
    assert error.decision.decision is PetSessionDecision.BLOCK_SESSION_PET_MISMATCH
    assert error.trace_delivery_status is PetSessionTraceWriteStatus.DEGRADED
