##################################################################################################
# 文件: tests/pet_session_policy/test_service_block.py
# 作用: 验证 PetSessionPolicy 的必要字段、存储错误、生命周期、后置条件与配置阻断行为。
# 边界: 使用公开依赖契约的测试替身；不访问真实数据库、不执行 HTTP 映射或后续业务图。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.conversation_store import (
    ConversationErrorCode,
    ConversationOperation,
    ConversationSessionStatus,
    ConversationStoreError,
    EnsureSessionResultDto,
)
from veterinary_agent.pet_session_policy import (
    PetSessionDecision,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionRequestContextDto,
)

from .helpers import (
    FakeConversationStore,
    RecordingTraceSink,
    UnavailableRuntimeConfigProvider,
    build_disabled_runtime_config_provider,
    build_policy,
    build_request,
    build_session,
)


@pytest.mark.parametrize(
    ("field_name", "request_context", "expected_decision"),
    [
        (
            "user_id",
            build_request(user_id=None),
            PetSessionDecision.BLOCK_MISSING_USER_ID,
        ),
        (
            "session_id",
            build_request(session_id="   "),
            PetSessionDecision.BLOCK_MISSING_SESSION_ID,
        ),
        (
            "pet_id",
            build_request(pet_id=None),
            PetSessionDecision.BLOCK_MISSING_PET_ID,
        ),
    ],
)
def test_missing_required_field_blocks_without_store_call(
    field_name: str,
    request_context: PetSessionRequestContextDto,
    expected_decision: PetSessionDecision,
) -> None:
    """验证缺少必要字段时在访问存储前阻断。

    :param field_name: 当前测试缺少的字段名称。
    :param request_context: 当前测试请求上下文。
    :param expected_decision: 预期阻断判定。
    :return: None。
    """

    store = FakeConversationStore()
    trace_sink = RecordingTraceSink()
    policy = build_policy(store=store, trace_sink=trace_sink)

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(request_context))

    error = exc_info.value.to_dto()
    assert error.code is PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING
    assert error.decision.decision is expected_decision
    assert error.decision.missing_field == field_name
    assert error.retryable is False
    assert store.ensure_calls == []
    assert trace_sink.records[0].missing_field == field_name


def test_multiple_missing_fields_use_stable_validation_priority() -> None:
    """验证多字段同时缺失时按 user_id、session_id、pet_id 顺序阻断。

    :return: None。
    """

    store = FakeConversationStore()
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(
            policy.ensure_context(
                build_request(
                    user_id=None,
                    session_id=None,
                    pet_id=None,
                )
            )
        )

    error = exc_info.value.to_dto()
    assert error.decision.decision is PetSessionDecision.BLOCK_MISSING_USER_ID
    assert error.decision.missing_field == "user_id"
    assert store.ensure_calls == []


@pytest.mark.parametrize(
    (
        "store_code",
        "expected_code",
        "expected_decision",
        "expected_retryable",
    ),
    [
        (
            ConversationErrorCode.SESSION_PET_CONFLICT,
            PetSessionPolicyErrorCode.PET_MISMATCH,
            PetSessionDecision.BLOCK_SESSION_PET_MISMATCH,
            False,
        ),
        (
            ConversationErrorCode.SESSION_USER_CONFLICT,
            PetSessionPolicyErrorCode.USER_MISMATCH,
            PetSessionDecision.BLOCK_SESSION_USER_MISMATCH,
            False,
        ),
        (
            ConversationErrorCode.SESSION_CLOSED,
            PetSessionPolicyErrorCode.SESSION_CLOSED,
            PetSessionDecision.BLOCK_SESSION_CLOSED,
            False,
        ),
        (
            ConversationErrorCode.SESSION_ARCHIVED,
            PetSessionPolicyErrorCode.SESSION_ARCHIVED,
            PetSessionDecision.BLOCK_SESSION_ARCHIVED,
            False,
        ),
        (
            ConversationErrorCode.STORE_UNAVAILABLE,
            PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
            PetSessionDecision.BLOCK_STORE_UNAVAILABLE,
            True,
        ),
        (
            ConversationErrorCode.OPERATION_TIMEOUT,
            PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
            PetSessionDecision.BLOCK_STORE_UNAVAILABLE,
            True,
        ),
        (
            ConversationErrorCode.INVALID_ARGUMENT,
            PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
            PetSessionDecision.BLOCK_STORE_UNAVAILABLE,
            False,
        ),
    ],
)
def test_conversation_store_error_is_mapped(
    store_code: ConversationErrorCode,
    expected_code: PetSessionPolicyErrorCode,
    expected_decision: PetSessionDecision,
    expected_retryable: bool,
) -> None:
    """验证 ConversationStore 错误映射为稳定 PetSessionPolicy 语义。

    :param store_code: 测试 ConversationStore 错误码。
    :param expected_code: 预期 PetSessionPolicy 错误码。
    :param expected_decision: 预期 PetSessionPolicy 判定。
    :param expected_retryable: 预期重试标记。
    :return: None。
    """

    store = FakeConversationStore(
        error=ConversationStoreError(
            code=store_code,
            operation=ConversationOperation.ENSURE_SESSION,
            message="store error",
            request_id="req_1",
            trace_id="trace_req_1",
        )
    )
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is expected_code
    assert error.decision.decision is expected_decision
    assert error.decision.store_error_code is store_code
    assert error.retryable is expected_retryable
    assert error.conflict_with == {
        "store_error_code": store_code.value,
        "store_operation": ConversationOperation.ENSURE_SESSION.value,
    }
    conflict_with = error.conflict_with
    assert isinstance(conflict_with, dict)
    assert "existing_pet_id" not in conflict_with
    assert "existing_user_id" not in conflict_with


def test_unmapped_store_exception_becomes_retryable_internal_error() -> None:
    """验证 ConversationStore 未映射异常转换为可重试内部错误。

    :return: None。
    """

    store = FakeConversationStore(error=ValueError("unexpected store failure"))
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is PetSessionPolicyErrorCode.INTERNAL_ERROR
    assert error.decision.decision is PetSessionDecision.BLOCK_INTERNAL_ERROR
    assert error.retryable is True
    assert error.conflict_with == {"exception_type": "ValueError"}


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_decision"),
    [
        (
            ConversationSessionStatus.CLOSED,
            PetSessionPolicyErrorCode.SESSION_CLOSED,
            PetSessionDecision.BLOCK_SESSION_CLOSED,
        ),
        (
            ConversationSessionStatus.ARCHIVED,
            PetSessionPolicyErrorCode.SESSION_ARCHIVED,
            PetSessionDecision.BLOCK_SESSION_ARCHIVED,
        ),
    ],
)
def test_inactive_session_is_blocked(
    status: ConversationSessionStatus,
    expected_code: PetSessionPolicyErrorCode,
    expected_decision: PetSessionDecision,
) -> None:
    """验证关闭或归档 session 不允许继续进入业务图。

    :param status: 测试 session 生命周期状态。
    :param expected_code: 预期 PetSessionPolicy 错误码。
    :param expected_decision: 预期 PetSessionPolicy 判定。
    :return: None。
    """

    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=build_session(status=status),
            created_new=False,
        )
    )
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is expected_code
    assert error.decision.decision is expected_decision
    assert error.decision.session_status is status


@pytest.mark.parametrize(
    ("field_name", "session_update", "expected_code", "expected_reason"),
    [
        (
            "user_id",
            {"user_id": "user_other"},
            PetSessionPolicyErrorCode.USER_MISMATCH,
            "store_user_anchor_postcondition_failed",
        ),
        (
            "pet_id",
            {"pet_id": "pet_other"},
            PetSessionPolicyErrorCode.PET_MISMATCH,
            "store_pet_anchor_postcondition_failed",
        ),
    ],
)
def test_store_anchor_postcondition_mismatch_is_blocked(
    field_name: str,
    session_update: dict[str, object],
    expected_code: PetSessionPolicyErrorCode,
    expected_reason: str,
) -> None:
    """验证 ConversationStore 返回错误锚点时策略层执行后置阻断。

    :param field_name: 当前测试被篡改的锚点字段。
    :param session_update: ConversationSessionDto 测试更新项。
    :param expected_code: 预期 PetSessionPolicy 错误码。
    :param expected_reason: 预期安全冲突原因。
    :return: None。
    """

    session = build_session().model_copy(update=session_update)
    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=session,
            created_new=False,
        )
    )
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert getattr(session, field_name) != getattr(build_session(), field_name)
    assert error.code is expected_code
    assert error.conflict_with == {"reason": expected_reason}


def test_runtime_config_unavailable_blocks_before_store_call() -> None:
    """验证 RuntimeConfig 不可用时 fail-closed 且不访问 ConversationStore。

    :return: None。
    """

    store = FakeConversationStore()
    policy = build_policy(
        store=store,
        trace_sink=RecordingTraceSink(),
        runtime_config_provider=UnavailableRuntimeConfigProvider(ready=True),
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE
    assert (
        error.decision.decision is PetSessionDecision.BLOCK_RUNTIME_CONFIG_UNAVAILABLE
    )
    assert error.retryable is True
    assert store.ensure_calls == []


def test_disabled_policy_safety_lock_blocks_before_store_call() -> None:
    """验证安全锁关闭时策略组件 fail-closed 且不访问 ConversationStore。

    :return: None。
    """

    store = FakeConversationStore()
    policy = build_policy(
        store=store,
        trace_sink=RecordingTraceSink(),
        runtime_config_provider=build_disabled_runtime_config_provider(),
    )

    with pytest.raises(PetSessionPolicyError) as exc_info:
        asyncio.run(policy.ensure_context(build_request()))

    error = exc_info.value.to_dto()
    assert error.code is PetSessionPolicyErrorCode.POLICY_DISABLED
    assert error.decision.decision is PetSessionDecision.BLOCK_POLICY_DISABLED
    assert error.retryable is False
    assert error.conflict_with == {
        "safety_lock": "enforce_pet_session_policy",
    }
    assert store.ensure_calls == []
