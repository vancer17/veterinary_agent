##################################################################################################
# 文件: tests/agent_application_service/test_ports.py
# 作用: 验证 AgentApplicationService 端口 TODO 空壳的不可用与降级契约。
# 边界: 仅测试端口占位行为；不实现 GraphRuntime / LogicTraceStore 真实领域能力。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from veterinary_agent.agent_application_service import (
    TODO_GRAPH_RUNTIME_ERROR_CODE,
    TODO_TRACE_STORE_ERROR_CODE,
    AgentGraphEventDto,
    AgentGraphRuntimeUnavailableError,
    AgentGraphTurnRequestDto,
    AgentTraceDeliveryStatus,
    AgentTraceFinalStatus,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTurnExecutionContextDto,
    TodoAgentGraphRuntime,
    TodoAgentLogicTraceStore,
)

from .helpers import (
    build_cancel_command,
    build_command,
    build_resume_command,
)


def _build_graph_request() -> AgentGraphTurnRequestDto:
    """构建 TODO GraphRuntime 测试请求。

    :return: GraphRuntime 端口请求 DTO。
    """

    command = build_command()
    return AgentGraphTurnRequestDto(
        context=AgentTurnExecutionContextDto(
            request_id=command.request_context.request_id,
            trace_id=command.request_context.trace_id,
            turn_id="turn_todo",
            run_id="run_todo",
            session_id=command.trusted_identity.session_id,
            user_id=command.trusted_identity.user_id,
            current_pet_id=command.trusted_identity.pet_id,
            user_message_id="msg_todo",
            idempotency_key=command.idempotency_key,
            params_version="params_test",
            config_snapshot_id="config_test",
            response_mode=command.request_context.response_mode,
            route_kind=command.request_context.route_kind,
        ),
        input=list(command.input),
        attachments=list(command.attachments),
        metadata=dict(command.metadata),
        model_hint=command.model_hint,
        execution_options=command.execution_options,
        publish_capabilities=command.publish_capabilities,
    )


async def _read_next_graph_event(
    iterator: AsyncIterator[AgentGraphEventDto],
) -> AgentGraphEventDto:
    """读取异步事件流的下一条 GraphRuntime 事件。

    :param iterator: GraphRuntime 事件异步迭代器。
    :return: 下一条 GraphRuntime 事件。
    """

    return await anext(iterator)


def _build_trace_start_command() -> AgentTraceStartCommandDto:
    """构建 TODO LogicTraceStore 启动测试命令。

    :return: Trace 启动命令 DTO。
    """

    return AgentTraceStartCommandDto(
        request_id="req_trace_start",
        trace_id="trace_trace_start",
        turn_id="turn_trace_start",
        run_id="run_trace_start",
        session_id="session_1",
        user_id="user_1",
        pet_id="pet_1",
        params_version="params_test",
        config_snapshot_id="config_test",
        idempotency_key="idem_trace_start",
    )


def _build_trace_finalize_command() -> AgentTraceFinalizeCommandDto:
    """构建 TODO LogicTraceStore 完成测试命令。

    :return: Trace 完成命令 DTO。
    """

    return AgentTraceFinalizeCommandDto(
        request_id="req_trace_finalize",
        trace_id="trace_trace_finalize",
        turn_id="turn_trace_finalize",
        run_id="run_trace_finalize",
        final_status=AgentTraceFinalStatus.COMPLETED,
        user_message_id="msg_trace_finalize",
        summary={"created_at": datetime.now(UTC).isoformat()},
    )


def test_todo_graph_runtime_reports_not_ready() -> None:
    """验证 TODO GraphRuntime 固定报告未就绪。

    :return: None。
    """

    runtime = TodoAgentGraphRuntime()

    assert runtime.is_ready() is False


def test_todo_graph_runtime_rejects_sync_and_cancel_operations() -> None:
    """验证 TODO GraphRuntime 拒绝同步执行与取消操作。

    :return: None。
    """

    runtime = TodoAgentGraphRuntime()

    with pytest.raises(AgentGraphRuntimeUnavailableError) as execute_exc_info:
        asyncio.run(runtime.execute_turn(_build_graph_request()))
    with pytest.raises(AgentGraphRuntimeUnavailableError) as cancel_exc_info:
        asyncio.run(runtime.cancel_turn(build_cancel_command()))

    assert execute_exc_info.value.code == TODO_GRAPH_RUNTIME_ERROR_CODE
    assert cancel_exc_info.value.code == TODO_GRAPH_RUNTIME_ERROR_CODE


def test_todo_graph_runtime_rejects_stream_and_resume_iteration() -> None:
    """验证 TODO GraphRuntime 在流式和恢复事件读取时报告不可用。

    :return: None。
    """

    runtime = TodoAgentGraphRuntime()

    with pytest.raises(AgentGraphRuntimeUnavailableError) as stream_exc_info:
        asyncio.run(_read_next_graph_event(runtime.stream_turn(_build_graph_request())))
    with pytest.raises(AgentGraphRuntimeUnavailableError) as resume_exc_info:
        asyncio.run(_read_next_graph_event(runtime.resume_turn(build_resume_command())))

    assert stream_exc_info.value.code == TODO_GRAPH_RUNTIME_ERROR_CODE
    assert resume_exc_info.value.code == TODO_GRAPH_RUNTIME_ERROR_CODE


def test_todo_logic_trace_store_returns_degraded_results() -> None:
    """验证 TODO LogicTraceStore 返回显式降级写入结果。

    :return: None。
    """

    trace_store = TodoAgentLogicTraceStore()

    start_result = asyncio.run(trace_store.start_trace(_build_trace_start_command()))
    finalize_result = asyncio.run(
        trace_store.finalize_trace(_build_trace_finalize_command())
    )

    assert trace_store.is_ready() is False
    assert start_result.status is AgentTraceDeliveryStatus.DEGRADED
    assert start_result.error_code == TODO_TRACE_STORE_ERROR_CODE
    assert start_result.retryable is True
    assert finalize_result.status is AgentTraceDeliveryStatus.DEGRADED
    assert finalize_result.error_code == TODO_TRACE_STORE_ERROR_CODE
    assert finalize_result.retryable is True
