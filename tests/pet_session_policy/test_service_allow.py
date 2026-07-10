##################################################################################################
# 文件: tests/pet_session_policy/test_service_allow.py
# 作用: 验证 PetSessionPolicy 对新 session、既有 session 与重复请求的允许继续行为。
# 边界: 使用公开 ConversationStore 测试替身；不访问数据库、不调用 FastAPI、业务图或其他 L2 组件。
##################################################################################################

import asyncio

from veterinary_agent.conversation_store import EnsureSessionResultDto
from veterinary_agent.pet_session_policy import (
    PetSessionDecision,
    PetSessionTraceWriteStatus,
)

from .helpers import (
    FakeConversationStore,
    RecordingTraceSink,
    build_policy,
    build_request,
    build_session,
)


def test_new_session_is_bound_and_allowed() -> None:
    """验证新 session 成功绑定并输出标准 current_pet_id。

    :return: None。
    """

    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=build_session(),
            created_new=True,
        )
    )
    trace_sink = RecordingTraceSink()
    policy = build_policy(store=store, trace_sink=trace_sink)

    context = asyncio.run(policy.ensure_context(build_request()))

    assert context.decision is PetSessionDecision.ALLOW_NEW_SESSION_BOUND
    assert context.current_pet_id == "pet_1"
    assert context.is_new_session is True
    assert context.allow_continue is True
    assert context.trace_delivery_status is PetSessionTraceWriteStatus.RECORDED
    assert len(store.ensure_calls) == 1
    assert trace_sink.records[0].current_pet_id == "pet_1"


def test_existing_session_is_allowed_with_default_todo_trace_sink() -> None:
    """验证既有 active session 同宠请求允许继续并显式标记 trace 降级。

    :return: None。
    """

    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=build_session(),
            created_new=False,
        )
    )
    policy = build_policy(store=store)

    context = asyncio.run(policy.ensure_context(build_request()))

    assert context.decision is PetSessionDecision.ALLOW_EXISTING_SESSION
    assert context.is_new_session is False
    assert context.trace_delivery_status is PetSessionTraceWriteStatus.DEGRADED


def test_policy_forwards_request_anchors_to_conversation_store() -> None:
    """验证策略服务完整传递请求身份与会话锚点。

    :return: None。
    """

    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=build_session(
                session_id="session_forward",
                user_id="user_forward",
                pet_id="pet_forward",
            ),
            created_new=True,
        )
    )
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())
    request_context = build_request(
        request_id="req_forward",
        trace_id="trace_forward",
        user_id="user_forward",
        session_id="session_forward",
        pet_id="pet_forward",
        client_pet_snapshot_ref={
            "pet_id": "pet_snapshot_other",
            "display_name": "仅作展示",
        },
    )

    context = asyncio.run(policy.ensure_context(request_context))

    command = store.ensure_calls[0]
    assert command.request_id == "req_forward"
    assert command.trace_id == "trace_forward"
    assert command.user_id == "user_forward"
    assert command.session_id == "session_forward"
    assert command.pet_id == "pet_forward"
    assert command.metadata == {}
    assert context.current_pet_id == "pet_forward"


def test_client_snapshot_does_not_change_structured_pet_anchor() -> None:
    """验证客户端宠物快照不参与结构化 pet_id 绑定判定。

    :return: None。
    """

    store = FakeConversationStore(
        result=EnsureSessionResultDto(
            session=build_session(),
            created_new=False,
        )
    )
    policy = build_policy(store=store, trace_sink=RecordingTraceSink())

    context = asyncio.run(
        policy.ensure_context(
            build_request(
                client_pet_snapshot_ref={
                    "pet_id": "pet_other",
                    "name": "另一只宠物",
                }
            )
        )
    )

    assert context.current_pet_id == "pet_1"
    assert store.ensure_calls[0].pet_id == "pet_1"
