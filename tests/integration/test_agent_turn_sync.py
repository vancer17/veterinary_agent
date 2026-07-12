##################################################################################################
# 文件: tests/integration/test_agent_turn_sync.py
# 作用: 验证 HTTP 入口经 ApiIngress 与 AgentApplicationService 可在受控依赖注入下完成同步 Agent turn。
# 边界: 使用 fake ConversationStore、GraphRuntime、LogicTraceStore 和 checkpoint provider；不连接真实数据库或真实业务图。
##################################################################################################

from fastapi.testclient import TestClient

from .helpers import (
    FakeGraphRuntime,
    build_harness,
    build_valid_payload,
    response_body,
)


def _assert_success_response(body: dict[str, object], *, request_id: str) -> None:
    """断言同步 Agent turn 成功响应的稳定结构。

    :param body: 响应 JSON 字典。
    :param request_id: 期望的请求 ID。
    :return: None。
    """

    assert body["object"] == "agent.turn"
    assert body["status"] == "completed"
    assert body["request_id"] == request_id
    assert body["trace_id"] == f"trace_{request_id}"
    output = body["output"]
    assert isinstance(output, list)
    assert output
    first_output = output[0]
    assert isinstance(first_output, dict)
    content = first_output["content"]
    assert isinstance(content, list)
    first_content = content[0]
    assert isinstance(first_content, dict)
    assert first_content["text"] == "建议先观察精神、食欲和饮水。"
    metadata = body["metadata"]
    assert isinstance(metadata, dict)
    assert isinstance(metadata["run_id"], str)
    assert isinstance(metadata["user_message_id"], str)
    assert metadata["trace_delivery_status"] == "written"


def test_agent_turns_sync_success_uses_full_application_chain() -> None:
    """验证 `/agent/turns` 能通过完整应用链路返回同步成功响应。

    :return: None。
    """

    harness = build_harness()
    request_id = "req_integration_success"

    with TestClient(harness.app) as client:
        response = client.post(
            "/agent/turns",
            json=build_valid_payload(request_id=request_id),
        )
    body = response_body(response.json())

    assert response.status_code == 200
    _assert_success_response(body, request_id=request_id)
    assert len(harness.conversation_store.ensure_calls) == 1
    assert len(harness.conversation_store.append_calls) == 1
    assert harness.conversation_store.append_calls[0].content == (
        "小狗今天精神一般，需要先观察哪些症状？"
    )
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert len(harness.graph_runtime.execute_requests) == 1
    graph_context = harness.graph_runtime.execute_requests[0].context
    assert graph_context.request_id == request_id
    assert graph_context.trace_id == f"trace_{request_id}"
    assert graph_context.session_id == "session_001"
    assert graph_context.user_id == "user_001"
    assert graph_context.current_pet_id == "pet_001"
    metadata = body["metadata"]
    assert isinstance(metadata, dict)
    assert graph_context.user_message_id == metadata["user_message_id"]
    assert graph_context.params_version
    assert graph_context.config_snapshot_id
    assert len(harness.trace_store.starts) == 1
    assert len(harness.trace_store.finalizes) == 1
    assert harness.trace_store.closed is True
    assert harness.checkpoint_provider.stopped is True


def test_openai_responses_route_uses_same_application_chain() -> None:
    """验证 OpenAI Responses 兼容入口会进入同一应用服务链路。

    :return: None。
    """

    harness = build_harness()
    request_id = "req_integration_openai"

    with TestClient(harness.app) as client:
        response = client.post(
            "/openai/v1/responses",
            json=build_valid_payload(request_id=request_id),
        )
    body = response_body(response.json())

    assert response.status_code == 200
    _assert_success_response(body, request_id=request_id)
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert len(harness.graph_runtime.execute_requests) == 1
    assert harness.graph_runtime.execute_requests[0].context.route_kind == (
        "openai_responses"
    )
