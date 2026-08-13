"""
文件：tests/test_clinical_safety_policy.py
作用：验证临床安全 OPA 策略客户端的 URL 组装、决策契约和响应校验。
说明：本文件不依赖真实 OPA 服务；通过 httpx 替身验证策略输入输出边界。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from vet_agent.clinical_safety import (
    ClinicalSafetyPolicyAction,
    ClinicalSafetyPolicyDecision,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyPolicyRequestContext,
    ClinicalSafetyRetrievalState,
    ClinicalSafetyThresholds,
    OpaClinicalSafetyPolicyClient,
)


class _MockResponse:
    """提供最小 httpx 响应替身。

    :param payload: 返回给客户端的 JSON 负载。
    :return: 无返回值。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化响应替身。

        :param payload: 返回给客户端的 JSON 负载。
        :return: 无返回值。
        """
        self._payload = payload

    def raise_for_status(self) -> None:
        """模拟成功响应。

        :return: 无返回值。
        """
        return None

    def json(self) -> dict[str, Any]:
        """返回预置 JSON。

        :return: 返回响应 JSON。
        """
        return self._payload


class _MockAsyncClient:
    """提供最小 httpx.AsyncClient 替身。

    :param response_payload: 返回给 OPA 客户端的 payload。
    :return: 无返回值。
    """

    def __init__(self, response_payload: dict[str, Any]) -> None:
        """初始化异步客户端替身。

        :param response_payload: 返回给 OPA 客户端的 payload。
        :return: 无返回值。
        """
        self.response_payload = response_payload
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_MockAsyncClient":
        """进入异步上下文。

        :return: 返回客户端自身。
        """
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出异步上下文。

        :return: 无返回值。
        """
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _MockResponse:
        """记录请求并返回预置响应。

        :param url: 请求地址。
        :param headers: 请求头。
        :param json: 请求体。
        :return: 返回响应替身。
        """
        self.requests.append({"url": url, "headers": headers, "json": json})
        return _MockResponse(self.response_payload)


def test_opa_clinical_safety_policy_client_builds_prefixed_data_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 OPA 临床安全客户端会正确拼接 Data API URL。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值。
    """
    client = OpaClinicalSafetyPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
    )
    assert client._decision_url() == "http://example.test/opa/v1/data/vet_agent/clinical_safety/decision"


def test_opa_clinical_safety_policy_client_parses_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 OPA 临床安全客户端能解析结构化裁决结果。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值。
    """
    mock_client = _MockAsyncClient(
        {
            "result": {
                "action": "escalate",
                "allow": True,
                "message": "需要线下处理。",
                "reasons": ["clinical_safety_candidate:TOXIC_SUBSTANCE:emergency"],
                "signals": [
                    {
                        "code": "TOXIC_SUBSTANCE",
                        "severity": "urgent",
                        "message": "需要线下处理。",
                        "matched_terms": ["泰诺"],
                    }
                ],
            }
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: mock_client)
    client = OpaClinicalSafetyPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
    )
    policy_input = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(
            request_id="req_test",
            trace_id="tr_test",
            user_id="user_test",
            pet_id="pet_test",
            session_id="session_test",
        ),
        semantic_result=None,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(),
        thresholds=ClinicalSafetyThresholds(),
    )
    decision = asyncio.run(client.decide(policy_input))
    assert decision.action == ClinicalSafetyPolicyAction.ESCALATE
    assert decision.allow is True
    assert decision.signals[0].code == "TOXIC_SUBSTANCE"
    assert mock_client.requests[0]["headers"]["X-Request-ID"] == "req_test"
