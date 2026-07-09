##################################################################################################
# 文件: tests/api_ingress/test_readiness.py
# 作用: 验证 API 接入组件 /ready 探针会消费 readiness 配置并返回统一就绪结果。
# 边界: 仅测试 ApiIngress 配置化探针行为；编排层与可观测性依赖未实现时使用 TODO 空壳占位。
##################################################################################################

from typing import cast

from fastapi.testclient import TestClient

from veterinary_agent import (
    ApiIngressSettings,
    check_api_ingress_readiness,
    create_app,
)


def _settings_with_readiness(**readiness_updates: object) -> ApiIngressSettings:
    """构建带有 readiness 配置覆盖项的 API 接入组件配置。

    :param readiness_updates: readiness 配置字段覆盖值。
    :return: 已合并 readiness 覆盖项的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "readiness": base_settings.readiness.model_copy(update=readiness_updates),
        }
    )


def _response_body(response_json: object) -> dict[str, object]:
    """将响应 JSON 约束为字典。

    :param response_json: HTTP 响应解析后的 JSON 对象。
    :return: 字典形式的响应体。
    """

    assert isinstance(response_json, dict)
    return cast(dict[str, object], response_json)


def _detail_fields(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细字段集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 field 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    fields: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        field = detail.get("field")
        if isinstance(field, str):
            fields.add(field)
    return fields


def _detail_reasons(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细原因集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 reason 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    reasons: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
    return reasons


def test_ready_uses_default_orchestrator_readiness_check() -> None:
    """验证默认 /ready 会消费编排入口就绪检查配置并返回脱敏 TODO 占位。

    :return: 无返回值。
    """

    with TestClient(create_app()) as client:
        response = client.get(
            "/ready",
            headers={
                "X-Request-ID": "req_ready_default",
                "X-Trace-ID": "trace_ready_default",
            },
        )
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["request_id"] == "req_ready_default"
    assert body["trace_id"] == "trace_ready_default"
    assert not _detail_fields(body)
    assert "internal_dependency_details_hidden" in _detail_reasons(body)


def test_ready_can_skip_unimplemented_orchestrator_check_by_settings() -> None:
    """验证配置关闭编排入口检查后 /ready 可返回就绪。

    :return: 无返回值。
    """

    settings = _settings_with_readiness(check_orchestrator=False)

    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_reports_observability_todo_when_degraded_mode_is_disabled() -> None:
    """验证禁止可观测性降级时 /ready 会返回脱敏可观测性 TODO 占位。

    :return: 无返回值。
    """

    settings = _settings_with_readiness(
        check_orchestrator=False,
        allow_degraded_observability=False,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert not _detail_fields(body)
    assert "internal_dependency_details_hidden" in _detail_reasons(body)


def test_ready_reports_missing_checkpoint_store_runtime_config() -> None:
    """验证 /ready 可聚合检查 CheckpointStore RuntimeConfig 是否已装配。

    :return: None。
    """

    settings = _settings_with_readiness(check_orchestrator=False)
    result = check_api_ingress_readiness(
        settings=settings,
        app_ready=True,
        checkpoint_store_runtime_config_ready=False,
    )

    assert result.ready is False
    assert {detail.field for detail in result.details} == {
        "checkpoint_store.runtime_config"
    }
