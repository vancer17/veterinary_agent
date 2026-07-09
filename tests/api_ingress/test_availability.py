##################################################################################################
# 文件: tests/api_ingress/test_availability.py
# 作用: 验证 API 接入组件启用状态配置会在业务入口最前置生效。
# 边界: 仅测试 ApiIngress enabled 开关；不接入编排层、SSE Mapper 或领域业务组件。
##################################################################################################

from typing import cast

import pytest
from fastapi.testclient import TestClient

from veterinary_agent import ApiIngressSettings, create_app


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


def _settings_with_openai_compatibility_enabled(
    enabled: bool,
) -> ApiIngressSettings:
    """构建带有 OpenAI 兼容入口开关覆盖项的 API 接入组件配置。

    :param enabled: 是否启用 OpenAI Responses 风格兼容入口。
    :return: 已合并兼容入口开关覆盖项的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "openai_compatibility": base_settings.openai_compatibility.model_copy(
                update={"enabled": enabled}
            )
        }
    )


def _valid_payload() -> dict[str, object]:
    """构建可通过 DTO Validation 并抵达 TODO 下游依赖的最小请求。

    :return: 最小合法一轮对话请求体。
    """

    return {
        "request_id": "req_openai_disabled_agent",
        "trace_id": "trace_openai_disabled_agent",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "小狗今天精神一般，需要先观察哪些症状？",
                    }
                ],
            }
        ],
        "vet_context": {
            "user_id": "user_001",
            "session_id": "session_001",
            "pet_id": "pet_001",
        },
    }


@pytest.mark.parametrize("path", ["/agent/turns", "/openai/v1/responses"])
def test_disabled_api_ingress_rejects_business_routes_before_dto_validation(
    path: str,
) -> None:
    """验证 disabled 状态会在 DTO Validation 前拒绝业务入口请求。

    :param path: 需要验证的 API 接入业务路由路径。
    :return: 无返回值。
    """

    settings = ApiIngressSettings(enabled=False)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            path,
            content="{not-json",
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": "req_disabled",
                "X-Trace-ID": "trace_disabled",
            },
        )
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "api ingress is disabled"
    assert body["request_id"] == "req_disabled"
    assert body["trace_id"] == "trace_disabled"
    assert "api_ingress.enabled" in _detail_fields(body)
    assert "disabled" in _detail_reasons(body)


def test_disabled_api_ingress_does_not_block_health_probe() -> None:
    """验证 disabled 状态不影响进程存活探针。

    :return: 无返回值。
    """

    settings = ApiIngressSettings(enabled=False)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_disabled_openai_compatibility_rejects_before_dto_validation() -> None:
    """验证关闭 OpenAI 兼容入口后会在 DTO Validation 前拒绝兼容路由。

    :return: 无返回值。
    """

    settings = _settings_with_openai_compatibility_enabled(enabled=False)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/openai/v1/responses",
            content="{not-json",
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": "req_openai_disabled",
                "X-Trace-ID": "trace_openai_disabled",
            },
        )
    body = _response_body(response.json())

    assert response.status_code == 404
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "openai compatibility endpoint is disabled"
    assert body["request_id"] == "req_openai_disabled"
    assert body["trace_id"] == "trace_openai_disabled"
    assert "openai_compatibility.enabled" in _detail_fields(body)
    assert "disabled" in _detail_reasons(body)


def test_disabled_openai_compatibility_does_not_block_agent_turns() -> None:
    """验证关闭 OpenAI 兼容入口不会影响主业务入口。

    :return: 无返回值。
    """

    settings = _settings_with_openai_compatibility_enabled(enabled=False)

    with TestClient(create_app(settings)) as client:
        response = client.post("/agent/turns", json=_valid_payload())
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["request_id"] == "req_openai_disabled_agent"
    assert body["trace_id"] == "trace_openai_disabled_agent"
