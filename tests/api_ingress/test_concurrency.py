##################################################################################################
# 文件: tests/api_ingress/test_concurrency.py
# 作用: 验证 API 接入组件编排入口实例级并发闸门会消费 orchestrator.max_concurrency 配置。
# 边界: 仅测试 ApiIngress 入口并发保护；不接入真实编排层、SSE Mapper 或领域业务组件。
##################################################################################################

import asyncio
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from veterinary_agent import (
    ApiIngressConcurrencyGate,
    ApiIngressConcurrencyLease,
    ApiIngressSettings,
    VeterinaryAgentAppState,
    create_app,
)


class _ExhaustedConcurrencyGate(ApiIngressConcurrencyGate):
    """始终返回满载状态的测试并发闸门。"""

    def __init__(self) -> None:
        """初始化测试并发闸门。

        :return: 无返回值。
        """

        super().__init__(max_concurrency=1)

    async def try_acquire(self) -> ApiIngressConcurrencyLease | None:
        """模拟并发容量已满。

        :return: 固定返回 None。
        """

        return None


def _valid_payload() -> dict[str, object]:
    """构建可通过入口校验并抵达编排 TODO 占位的最小请求。

    :return: 最小合法一轮对话请求体。
    """

    return {
        "request_id": "req_concurrency_001",
        "trace_id": "trace_concurrency_001",
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


def _settings_with_max_concurrency(max_concurrency: int) -> ApiIngressSettings:
    """构建覆盖编排入口最大并发数的 API 接入组件配置。

    :param max_concurrency: 编排入口最大并发数。
    :return: 已覆盖编排入口最大并发数的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "orchestrator": base_settings.orchestrator.model_copy(
                update={"max_concurrency": max_concurrency}
            ),
        }
    )


def _app_state(client: TestClient) -> VeterinaryAgentAppState:
    """读取测试客户端中的应用状态。

    :param client: 已启动 lifespan 的 FastAPI 测试客户端。
    :return: 挂载在 app.state 上的应用框架级状态。
    """

    app = cast(FastAPI, client.app)
    state = getattr(app.state, "veterinary_agent_state")
    assert isinstance(state, VeterinaryAgentAppState)
    return state


async def _exercise_concurrency_gate_capacity() -> None:
    """执行并发闸门容量耗尽验证。

    :return: 无返回值。
    """

    gate = ApiIngressConcurrencyGate(max_concurrency=1)
    first_lease = await gate.try_acquire()
    assert first_lease is not None
    try:
        second_lease = await gate.try_acquire()
        assert second_lease is None
        assert await gate.active_count() == 1
    finally:
        await first_lease.release()

    third_lease = await gate.try_acquire()
    assert third_lease is not None
    await third_lease.release()
    assert await gate.active_count() == 0


def test_concurrency_gate_rejects_when_capacity_is_exhausted() -> None:
    """验证并发闸门达到上限后会拒绝新的许可。

    :return: 无返回值。
    """

    asyncio.run(_exercise_concurrency_gate_capacity())


def test_router_rejects_when_orchestrator_concurrency_gate_is_full() -> None:
    """验证业务入口在编排并发闸门已满时返回统一错误响应。

    :return: 无返回值。
    """

    settings = _settings_with_max_concurrency(max_concurrency=1)

    with TestClient(create_app(settings)) as client:
        app_state = _app_state(client)
        app_state.orchestrator_concurrency_gate = _ExhaustedConcurrencyGate()
        response = client.post("/agent/turns", json=_valid_payload())
    body = _response_body(response.json())

    assert response.status_code == 503
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["message"] == "orchestrator concurrency limit exceeded"
    assert "orchestrator.max_concurrency" in _detail_fields(body)
    assert "exceeded" in _detail_reasons(body)
