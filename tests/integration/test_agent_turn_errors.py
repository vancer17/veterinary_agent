##################################################################################################
# 文件: tests/integration/test_agent_turn_errors.py
# 作用: 验证受控依赖注入下 Agent turn HTTP 链路的策略阻断、依赖失败、Trace 降级与入口治理行为。
# 边界: 使用 fake 依赖与 TODO 空壳验证集成契约；不连接真实数据库、不运行真实 GraphRuntime 或 SSE 输出。
##################################################################################################

from fastapi.testclient import TestClient

from veterinary_agent.agent_application_service import TodoAgentGraphRuntime
from veterinary_agent.checkpoint_store import CheckpointStoreSettings
from veterinary_agent.conversation_store import ConversationStoreSettings
from veterinary_agent.app import create_app
from veterinary_agent.logic_trace_store import LogicTraceWriteStatus

from .helpers import (
    FakeCheckpointProvider,
    FakeCheckpointProviderFactory,
    FakeGraphRuntime,
    FakeGraphRuntimeFactory,
    FakeLogicTraceStore,
    FakeLogicTraceStoreFactory,
    build_harness,
    build_valid_payload,
    detail_reasons,
    response_body,
    settings_with_rate_limit_enabled,
    settings_without_orchestrator_readiness,
)


def test_pet_session_conflict_blocks_message_persist_and_graph_runtime() -> None:
    """验证同一 session 换宠会被策略阻断且不会进入消息落库或 GraphRuntime。

    :return: None。
    """

    harness = build_harness()

    with TestClient(harness.app) as client:
        first_response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_session_first"),
        )
        mismatch_response = client.post(
            "/agent/turns",
            json=build_valid_payload(
                request_id="req_session_mismatch",
                pet_id="pet_002",
            ),
        )
    body = response_body(mismatch_response.json())

    assert first_response.status_code == 200
    assert mismatch_response.status_code == 409
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "session is bound to another pet; create a new session"
    assert "PET_SESSION_PET_MISMATCH" in detail_reasons(body)
    assert "BLOCK_SESSION_PET_MISMATCH" in detail_reasons(body)
    assert len(harness.conversation_store.append_calls) == 1
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert len(harness.graph_runtime.execute_requests) == 1


def test_default_conversation_store_todo_fails_closed_before_graph_runtime() -> None:
    """验证默认 ConversationStore TODO 会使策略 fail-closed。

    :return: None。
    """

    checkpoint_provider = FakeCheckpointProvider()
    graph_runtime = FakeGraphRuntime()
    trace_store = FakeLogicTraceStore()
    app = create_app(
        settings=settings_without_orchestrator_readiness(),
        checkpoint_store_settings=CheckpointStoreSettings(),
        conversation_store_settings=ConversationStoreSettings(),
        checkpoint_provider_factory=FakeCheckpointProviderFactory(checkpoint_provider),
        graph_runtime_factory=FakeGraphRuntimeFactory(graph_runtime),
        logic_trace_store_factory=FakeLogicTraceStoreFactory(trace_store),
    )

    with TestClient(app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_default_todo_store"),
        )
    body = response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "pet session policy is unavailable"
    assert len(graph_runtime.execute_requests) == 0
    assert len(trace_store.finalizes) == 1
    assert checkpoint_provider.stopped is True


def test_graph_runtime_todo_fails_after_user_message_persist() -> None:
    """验证 GraphRuntime TODO 会在用户消息已落库后返回服务不可用。

    :return: None。
    """

    harness = build_harness(graph_runtime=TodoAgentGraphRuntime())

    with TestClient(harness.app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_graph_todo"),
        )
    body = response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "service unavailable"
    assert "internal_dependency_details_hidden" in detail_reasons(body)
    assert len(harness.conversation_store.ensure_calls) == 1
    assert len(harness.conversation_store.append_calls) == 1
    assert len(harness.trace_store.finalizes) == 1


def test_graph_runtime_timeout_maps_to_orchestrator_timeout() -> None:
    """验证 GraphRuntime 超时会映射为入口层 ORCHESTRATOR_TIMEOUT。

    :return: None。
    """

    graph_runtime = FakeGraphRuntime(failure_mode="timeout")
    harness = build_harness(graph_runtime=graph_runtime)

    with TestClient(harness.app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_graph_timeout"),
        )
    body = response_body(response.json())

    assert response.status_code == 504
    assert body["code"] == "ORCHESTRATOR_TIMEOUT"
    assert body["message"] == "orchestrator timeout"
    assert len(harness.conversation_store.append_calls) == 1
    assert len(graph_runtime.execute_requests) == 1
    assert len(harness.trace_store.finalizes) == 1


def test_trace_degraded_keeps_sync_response_successful() -> None:
    """验证 LogicTraceStore 降级不影响同步主响应成功。

    :return: None。
    """

    trace_store = FakeLogicTraceStore(finalize_status=LogicTraceWriteStatus.DEGRADED)
    harness = build_harness(trace_store=trace_store)

    with TestClient(harness.app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_trace_degraded"),
        )
    body = response_body(response.json())
    metadata = body["metadata"]
    assert isinstance(metadata, dict)

    assert response.status_code == 200
    assert metadata["trace_delivery_status"] == "degraded"
    assert len(trace_store.starts) == 1
    assert len(trace_store.finalizes) == 1


def test_stream_request_returns_todo_adapter_error_without_graph_runtime() -> None:
    """验证 HTTP stream 请求当前返回 SSE adapter TODO 错误。

    :return: None。
    """

    harness = build_harness()

    with TestClient(harness.app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(
                request_id="req_stream_todo",
                response_mode="stream",
            ),
        )
    body = response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "service unavailable"
    assert "internal_dependency_details_hidden" in detail_reasons(body)
    assert len(harness.conversation_store.ensure_calls) == 0
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert len(harness.graph_runtime.execute_requests) == 0


def test_rate_limit_rejects_second_request_before_application_service() -> None:
    """验证入口限流会在应用服务前拒绝第二次请求。

    :return: None。
    """

    harness = build_harness(settings=settings_with_rate_limit_enabled())

    with TestClient(harness.app) as client:
        first_response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_rate_first"),
        )
        second_response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id="req_rate_second"),
        )
    body = response_body(second_response.json())

    assert first_response.status_code == 200
    assert second_response.status_code == 429
    assert body["code"] == "RATE_LIMITED"
    assert len(harness.conversation_store.append_calls) == 1
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert len(harness.graph_runtime.execute_requests) == 1
