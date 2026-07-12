##################################################################################################
# 文件: tests/agent_application_service/test_service.py
# 作用: 验证 AgentApplicationService 胶水层的主链路、错误边界、流式事件、恢复与取消契约。
# 边界: 使用测试替身验证组件级行为；不连接真实数据库、不实现真实 GraphRuntime / LogicTraceStore、不跨包引用内部实现。
##################################################################################################

import asyncio

import pytest

from veterinary_agent.agent_application_service import (
    AgentApplicationErrorCode,
    AgentApplicationPhase,
    AgentApplicationServiceError,
    AgentTraceDeliveryStatus,
    AgentTraceFinalStatus,
    AgentTurnStatus,
)
from veterinary_agent.conversation_store import ConversationSessionStatus

from .helpers import (
    CapturingTraceStore,
    FailingGraphRuntime,
    GraphExecuteFailureMode,
    InMemoryConversationStore,
    SuccessfulGraphRuntime,
    UnavailableRuntimeConfigProvider,
    build_append_failure,
    build_cancel_command,
    build_command,
    build_graph_event,
    build_resume_command,
    build_service,
    collect_async_iterator,
)


def _assert_application_error(
    error: AgentApplicationServiceError,
    *,
    code: AgentApplicationErrorCode,
    phase: AgentApplicationPhase,
) -> None:
    """断言应用服务错误的稳定码与阶段。

    :param error: 捕获到的 AgentApplicationService 领域异常。
    :param code: 期望的应用服务稳定错误码。
    :param phase: 期望的应用编排阶段。
    :return: None。
    """

    error_dto = error.to_dto()
    assert error_dto.code is code
    assert error_dto.phase is phase
    assert error_dto.request_id
    assert error_dto.trace_id


def test_execute_turn_persists_message_and_calls_graph_runtime() -> None:
    """验证同步执行会按顺序保存用户消息并调用 GraphRuntime。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    result = asyncio.run(service.execute_turn(build_command()))

    assert result.status is AgentTurnStatus.COMPLETED
    assert result.output_text == "建议先观察精神、食欲和饮水。"
    assert result.trace_delivery_status is AgentTraceDeliveryStatus.WRITTEN
    assert result.metadata["graph"] == "fake"
    assert len(store.ensure_calls) == 1
    assert len(store.append_calls) == 1
    assert store.append_calls[0].content == "小狗今天精神一般，需要观察什么？"
    assert len(graph_runtime.execute_requests) == 1
    assert graph_runtime.execute_requests[0].context.current_pet_id == "pet_1"
    assert (
        graph_runtime.execute_requests[0].context.user_message_id
        == result.user_message_id
    )
    assert len(trace_store.starts) == 1
    assert len(trace_store.finalizes) == 1
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.COMPLETED


def test_execute_turn_keeps_stable_ids_and_user_message_idempotency() -> None:
    """验证同一幂等键会生成稳定 turn/run ID 并复用用户消息 ID。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )
    command = build_command(idempotency_key="idem_stable")

    first_result = asyncio.run(service.execute_turn(command))
    second_result = asyncio.run(service.execute_turn(command))

    assert second_result.turn_id == first_result.turn_id
    assert second_result.run_id == first_result.run_id
    assert second_result.user_message_id == first_result.user_message_id
    assert len(store.append_calls) == 2
    assert store.append_calls[0].idempotency_key == "idem_stable:user-message"
    assert len(store.messages_by_idempotency_key) == 1
    assert len(graph_runtime.execute_requests) == 2


def test_execute_turn_maps_pet_session_conflict_before_graph_runtime() -> None:
    """验证宠物会话冲突会在 GraphRuntime 前被映射为应用错误。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )
    asyncio.run(service.execute_turn(build_command(request_id="req_first")))

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(
            service.execute_turn(
                build_command(
                    request_id="req_mismatch",
                    pet_id="pet_2",
                    idempotency_key="idem_mismatch",
                )
            )
        )

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.PET_SESSION_CONFLICT,
        phase=AgentApplicationPhase.PET_SESSION_POLICY,
    )
    error = exc_info.value.to_dto()
    assert error.dependency_error_code == "PET_SESSION_PET_MISMATCH"
    assert len(graph_runtime.execute_requests) == 1
    assert len(store.append_calls) == 1
    assert trace_store.finalizes[-1].final_status is AgentTraceFinalStatus.FAILED


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (ConversationSessionStatus.CLOSED, AgentApplicationErrorCode.SESSION_CLOSED),
        (
            ConversationSessionStatus.ARCHIVED,
            AgentApplicationErrorCode.SESSION_ARCHIVED,
        ),
    ],
)
def test_execute_turn_blocks_closed_or_archived_session_before_message_persist(
    status: ConversationSessionStatus,
    expected_code: AgentApplicationErrorCode,
) -> None:
    """验证关闭或归档 session 会在用户消息落库前被阻断。

    :param status: 预置 session 状态。
    :param expected_code: 期望的应用服务错误码。
    :return: None。
    """

    store = InMemoryConversationStore()
    store.seed_session(status=status)
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.execute_turn(build_command()))

    _assert_application_error(
        exc_info.value,
        code=expected_code,
        phase=AgentApplicationPhase.PET_SESSION_POLICY,
    )
    assert len(store.append_calls) == 0
    assert len(graph_runtime.execute_requests) == 0
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.FAILED


def test_execute_turn_maps_runtime_config_unavailable_before_trace_start() -> None:
    """验证 RuntimeConfig 不可用会在策略和图执行前被映射为依赖错误。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
        runtime_config_provider=UnavailableRuntimeConfigProvider(),
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.execute_turn(build_command()))

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE,
        phase=AgentApplicationPhase.PREPARING,
    )
    assert exc_info.value.to_dto().dependency == "RuntimeConfig"
    assert len(trace_store.starts) == 0
    assert len(store.ensure_calls) == 0
    assert len(store.append_calls) == 0
    assert len(graph_runtime.execute_requests) == 0


def test_execute_turn_maps_user_message_persist_failure() -> None:
    """验证用户消息落库失败不会调用 GraphRuntime。

    :return: None。
    """

    store = InMemoryConversationStore(append_failure=build_append_failure())
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.execute_turn(build_command(request_id="req_append_failed")))

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.USER_MESSAGE_PERSIST_FAILED,
        phase=AgentApplicationPhase.USER_MESSAGE_PERSISTING,
    )
    assert exc_info.value.to_dto().dependency == "ConversationStore"
    assert len(store.ensure_calls) == 1
    assert len(store.append_calls) == 1
    assert len(graph_runtime.execute_requests) == 0
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.FAILED


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_dependency_error"),
    [
        (
            "unavailable",
            AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
            "AGENT_GRAPH_RUNTIME_NOT_IMPLEMENTED",
        ),
        ("timeout", AgentApplicationErrorCode.GRAPH_EXECUTION_TIMEOUT, "TimeoutError"),
        (
            "exception",
            AgentApplicationErrorCode.GRAPH_EXECUTION_FAILED,
            "RuntimeError",
        ),
    ],
)
def test_execute_turn_maps_graph_runtime_failures_after_message_persist(
    failure: GraphExecuteFailureMode,
    expected_code: AgentApplicationErrorCode,
    expected_dependency_error: str,
) -> None:
    """验证 GraphRuntime 失败会在用户消息已落库后映射为应用错误。

    :param failure: GraphRuntime 同步执行失败模式。
    :param expected_code: 期望的应用服务错误码。
    :param expected_dependency_error: 期望的下游错误码或异常类型。
    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = FailingGraphRuntime(execute_failure=failure)
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.execute_turn(build_command()))

    _assert_application_error(
        exc_info.value,
        code=expected_code,
        phase=AgentApplicationPhase.GRAPH_EXECUTING,
    )
    error = exc_info.value.to_dto()
    assert error.dependency == "GraphRuntime"
    assert error.dependency_error_code == expected_dependency_error
    assert len(store.append_calls) == 1
    assert len(graph_runtime.execute_requests) == 1
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.FAILED


def test_execute_turn_maps_trace_start_failure_before_policy() -> None:
    """验证 Trace 启动失败会阻断策略、消息落库和 GraphRuntime。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore(fail_start=True)
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.execute_turn(build_command()))

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.TRACE_START_FAILED,
        phase=AgentApplicationPhase.TRACE_STARTING,
    )
    assert len(store.ensure_calls) == 0
    assert len(store.append_calls) == 0
    assert len(graph_runtime.execute_requests) == 0
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.FAILED


def test_execute_turn_degrades_when_trace_finalize_fails() -> None:
    """验证 Trace 完成失败不会覆盖主业务成功结果。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore(fail_finalize=True)
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    result = asyncio.run(service.execute_turn(build_command()))

    assert result.status is AgentTurnStatus.COMPLETED
    assert result.trace_delivery_status is AgentTraceDeliveryStatus.DEGRADED
    assert len(trace_store.finalizes) == 1
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.COMPLETED


def test_stream_turn_maps_graph_events_and_finalizes_trace() -> None:
    """验证流式执行会映射 GraphRuntime 事件并完成 Trace。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime(
        stream_events=[
            build_graph_event(
                event_id="event_1",
                event_type="segment.delta",
                data={"text": "第一段"},
            ),
            build_graph_event(
                event_id="event_2",
                event_type="turn.completed",
                data={"done": True},
            ),
        ]
    )
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    events = asyncio.run(
        collect_async_iterator(
            service.stream_turn(build_command(response_mode="stream"))
        )
    )

    assert [event.sequence_no for event in events] == [1, 2]
    assert [event.event_id for event in events] == ["event_1", "event_2"]
    assert events[0].request_id == "req_agent_service"
    assert len(store.append_calls) == 1
    assert len(graph_runtime.stream_requests) == 1
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.COMPLETED


def test_stream_turn_maps_graph_runtime_unavailable() -> None:
    """验证流式执行中的 GraphRuntime 不可用会被映射为应用错误。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = FailingGraphRuntime(stream_unavailable=True)
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(
            collect_async_iterator(
                service.stream_turn(build_command(response_mode="stream"))
            )
        )

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
        phase=AgentApplicationPhase.GRAPH_EXECUTING,
    )
    assert len(store.append_calls) == 1
    assert trace_store.finalizes[0].final_status is AgentTraceFinalStatus.FAILED


def test_resume_turn_wraps_graph_events() -> None:
    """验证恢复运行会包装 GraphRuntime 事件。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime(
        resume_events=[
            build_graph_event(
                event_id="resume_event_1",
                event_type="turn.resumed",
            )
        ]
    )
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    events = asyncio.run(
        collect_async_iterator(
            service.resume_turn(build_resume_command(run_id="run_1"))
        )
    )

    assert len(events) == 1
    assert events[0].sequence_no == 1
    assert events[0].run_id == "run_1"
    assert events[0].turn_id == "turn_resume_run_1"
    assert len(graph_runtime.resume_commands) == 1


def test_resume_turn_maps_graph_runtime_unavailable() -> None:
    """验证恢复运行遇到 GraphRuntime 不可用时返回应用错误。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = FailingGraphRuntime(resume_unavailable=True)
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(collect_async_iterator(service.resume_turn(build_resume_command())))

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
        phase=AgentApplicationPhase.GRAPH_EXECUTING,
    )


def test_cancel_turn_returns_graph_runtime_result() -> None:
    """验证取消运行会返回 GraphRuntime 取消结果。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = SuccessfulGraphRuntime()
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    result = asyncio.run(
        service.cancel_turn(build_cancel_command(run_id="run_cancel_1"))
    )

    assert result.run_id == "run_cancel_1"
    assert result.cancelled is True
    assert result.idempotent is False
    assert len(graph_runtime.cancel_commands) == 1


def test_cancel_turn_maps_graph_runtime_unavailable() -> None:
    """验证取消运行遇到 GraphRuntime 不可用时返回应用错误。

    :return: None。
    """

    store = InMemoryConversationStore()
    graph_runtime = FailingGraphRuntime(cancel_unavailable=True)
    trace_store = CapturingTraceStore()
    service = build_service(
        store=store,
        graph_runtime=graph_runtime,
        trace_store=trace_store,
    )

    with pytest.raises(AgentApplicationServiceError) as exc_info:
        asyncio.run(service.cancel_turn(build_cancel_command()))

    _assert_application_error(
        exc_info.value,
        code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
        phase=AgentApplicationPhase.CANCELLED,
    )
