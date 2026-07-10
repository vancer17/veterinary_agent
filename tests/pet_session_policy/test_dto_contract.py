##################################################################################################
# 文件: tests/pet_session_policy/test_dto_contract.py
# 作用: 验证 PetSessionPolicy DTO、错误 DTO、稳定枚举、字面量约束与默认重试策略。
# 边界: 仅测试组件公开契约，不访问数据库、不调用 FastAPI、不执行真实 ConversationStore。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent.conversation_store import ConversationSessionStatus
from veterinary_agent.pet_session_policy import (
    PetSessionContextDto,
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionPolicyDecisionDto,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionRequestContextDto,
    PetSessionTraceRecordDto,
    PetSessionTraceWriteStatus,
    build_pet_session_policy_error_dto,
    is_pet_session_policy_error_retryable_by_default,
)


def test_request_context_rejects_extra_fields() -> None:
    """验证 PetSessionPolicy 请求上下文拒绝未声明字段。

    :return: None。
    """

    with pytest.raises(ValidationError):
        PetSessionRequestContextDto.model_validate(
            {
                "request_id": "req_1",
                "trace_id": "trace_1",
                "user_id": "user_1",
                "session_id": "session_1",
                "pet_id": "pet_1",
                "unexpected_field": "not_allowed",
            }
        )


def test_request_context_strips_optional_anchor_whitespace() -> None:
    """验证请求上下文清理可选锚点字段的首尾空白。

    :return: None。
    """

    request_context = PetSessionRequestContextDto(
        request_id=" req_1 ",
        trace_id=" trace_1 ",
        user_id=" user_1 ",
        session_id=" session_1 ",
        pet_id=" pet_1 ",
    )

    assert request_context.request_id == "req_1"
    assert request_context.trace_id == "trace_1"
    assert request_context.user_id == "user_1"
    assert request_context.session_id == "session_1"
    assert request_context.pet_id == "pet_1"


@pytest.mark.parametrize("field_name", ["request_id", "trace_id"])
def test_request_context_rejects_empty_required_identity(
    field_name: str,
) -> None:
    """验证请求上下文拒绝空 request_id 或 trace_id。

    :param field_name: 当前测试置空的必要身份字段。
    :return: None。
    """

    payload: dict[str, object] = {
        "request_id": "req_1",
        "trace_id": "trace_1",
    }
    payload[field_name] = "   "

    with pytest.raises(ValidationError):
        PetSessionRequestContextDto.model_validate(payload)


def test_success_context_enforces_allow_and_active_literals() -> None:
    """验证成功上下文固定为允许继续且 session 状态为 active。

    :return: None。
    """

    context = PetSessionContextDto(
        request_id="req_1",
        trace_id="trace_1",
        user_id="user_1",
        session_id="session_1",
        current_pet_id="pet_1",
        is_new_session=True,
        decision=PetSessionDecision.ALLOW_NEW_SESSION_BOUND,
        params_version="params.v1",
        config_snapshot_id="snapshot_1",
        trace_delivery_status=PetSessionTraceWriteStatus.RECORDED,
    )

    assert context.allow_continue is True
    assert context.session_status is ConversationSessionStatus.ACTIVE

    with pytest.raises(ValidationError):
        context.model_copy(update={"allow_continue": False}, deep=True).model_validate(
            {
                **context.model_dump(),
                "allow_continue": False,
            }
        )

    with pytest.raises(ValidationError):
        PetSessionContextDto.model_validate(
            {
                **context.model_dump(),
                "session_status": ConversationSessionStatus.CLOSED,
            }
        )


def test_trace_record_uses_stable_schema_version() -> None:
    """验证策略 trace 摘要使用稳定结构版本。

    :return: None。
    """

    record = PetSessionTraceRecordDto(
        request_id="req_1",
        trace_id="trace_1",
        user_id="user_1",
        session_id="session_1",
        requested_pet_id="pet_1",
        current_pet_id="pet_1",
        decision=PetSessionDecision.ALLOW_EXISTING_SESSION,
        policy_action=PetSessionPolicyAction.ALLOW_CONTINUE,
        allow_continue=True,
        retryable=False,
    )

    assert record.schema_version == "pet-session-policy.trace.v1"


@pytest.mark.parametrize(
    ("error_code", "expected_retryable"),
    [
        (PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING, False),
        (PetSessionPolicyErrorCode.PET_MISMATCH, False),
        (PetSessionPolicyErrorCode.USER_MISMATCH, False),
        (PetSessionPolicyErrorCode.SESSION_CLOSED, False),
        (PetSessionPolicyErrorCode.SESSION_ARCHIVED, False),
        (PetSessionPolicyErrorCode.STORE_UNAVAILABLE, True),
        (PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE, True),
        (PetSessionPolicyErrorCode.POLICY_DISABLED, False),
        (PetSessionPolicyErrorCode.INTERNAL_ERROR, True),
    ],
)
def test_error_retryable_defaults_are_stable(
    error_code: PetSessionPolicyErrorCode,
    expected_retryable: bool,
) -> None:
    """验证每个 PetSessionPolicy 错误码的默认重试策略稳定。

    :param error_code: 当前测试错误码。
    :param expected_retryable: 预期默认重试标记。
    :return: None。
    """

    assert (
        is_pet_session_policy_error_retryable_by_default(error_code)
        is expected_retryable
    )


def test_build_error_dto_carries_decision_and_trace_status() -> None:
    """验证 PetSessionPolicy 错误 DTO 承载完整策略判定与 trace 状态。

    :return: None。
    """

    decision = PetSessionPolicyDecisionDto(
        decision=PetSessionDecision.BLOCK_SESSION_PET_MISMATCH,
        policy_action=PetSessionPolicyAction.BLOCK_REQUEST,
        allow_continue=False,
        error_code=PetSessionPolicyErrorCode.PET_MISMATCH,
        retryable=False,
        reason="session pet mismatch",
    )

    error = build_pet_session_policy_error_dto(
        code=PetSessionPolicyErrorCode.PET_MISMATCH,
        message="session pet mismatch",
        request_id="req_1",
        trace_id="trace_1",
        decision=decision,
        trace_delivery_status=PetSessionTraceWriteStatus.DEGRADED,
    )

    assert error.retryable is False
    assert error.decision.decision is PetSessionDecision.BLOCK_SESSION_PET_MISMATCH
    assert error.trace_delivery_status is PetSessionTraceWriteStatus.DEGRADED


def test_policy_error_exposes_stable_properties_and_string() -> None:
    """验证 PetSessionPolicy 领域异常公开稳定属性、DTO 与日志字符串。

    :return: None。
    """

    decision = PetSessionPolicyDecisionDto(
        decision=PetSessionDecision.BLOCK_STORE_UNAVAILABLE,
        policy_action=PetSessionPolicyAction.BLOCK_REQUEST,
        allow_continue=False,
        error_code=PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
        retryable=True,
        reason="store unavailable",
    )
    error = PetSessionPolicyError(
        code=PetSessionPolicyErrorCode.STORE_UNAVAILABLE,
        message="store unavailable",
        request_id="req_1",
        trace_id="trace_1",
        decision=decision,
        trace_delivery_status=PetSessionTraceWriteStatus.DEGRADED,
    )

    assert error.code is PetSessionPolicyErrorCode.STORE_UNAVAILABLE
    assert error.retryable is True
    assert error.to_dto().decision is decision
    assert str(error) == (
        "BLOCK_STORE_UNAVAILABLE:PET_SESSION_STORE_UNAVAILABLE:store unavailable"
    )
