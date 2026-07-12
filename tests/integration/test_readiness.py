##################################################################################################
# 文件: tests/integration/test_readiness.py
# 作用: 验证受控依赖注入下 FastAPI lifespan、应用状态与 /ready 探针的集成行为。
# 边界: 使用 fake 依赖与 TODO GraphRuntime 验证 readiness 契约；不连接真实数据库或真实业务图。
##################################################################################################

from fastapi.testclient import TestClient

from veterinary_agent.agent_application_service import TodoAgentGraphRuntime

from .helpers import (
    FakeGraphRuntime,
    app_state,
    build_harness,
    detail_reasons,
    response_body,
    settings_without_orchestrator_readiness,
)


def test_ready_returns_ok_when_fake_application_dependencies_are_ready() -> None:
    """验证注入 ready fake 依赖后 `/ready` 返回就绪。

    :return: None。
    """

    harness = build_harness()

    with TestClient(harness.app) as client:
        state = app_state(harness.app)
        assert state.ready is True
        assert state.agent_application_service_ready is True
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert isinstance(harness.graph_runtime, FakeGraphRuntime)
    assert harness.graph_runtime.is_ready() is True


def test_ready_reports_unavailable_when_graph_runtime_is_todo() -> None:
    """验证 GraphRuntime TODO 时默认 `/ready` 返回未就绪。

    :return: None。
    """

    harness = build_harness(graph_runtime=TodoAgentGraphRuntime())

    with TestClient(harness.app) as client:
        response = client.get(
            "/ready",
            headers={
                "X-Request-ID": "req_ready_todo_graph",
                "X-Trace-ID": "trace_ready_todo_graph",
            },
        )
    body = response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["request_id"] == "req_ready_todo_graph"
    assert body["trace_id"] == "trace_ready_todo_graph"
    assert "internal_dependency_details_hidden" in detail_reasons(body)


def test_ready_can_skip_orchestrator_dependency_check() -> None:
    """验证关闭 orchestrator readiness 检查后 GraphRuntime TODO 不阻塞 `/ready`。

    :return: None。
    """

    harness = build_harness(
        settings=settings_without_orchestrator_readiness(),
        graph_runtime=TodoAgentGraphRuntime(),
    )

    with TestClient(harness.app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
