"""
=============================================================================
文件：tests/test_consultation_state_policy.py
作用：验证问诊状态与回答充分性策略客户端的契约解析、失败语义和本地测试后端。
范围：覆盖 OPA Data API URL 组装、结构化裁决解析、缺失策略返回的 Fail Fast 行为，
      以及本地策略客户端对策略摘要模型的最小上下文判断。
说明：本文件不调用真实 OPA 服务；真实 OPA HTTP 链路由 integration 测试和脚本覆盖。
=============================================================================
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from vet_agent.consultation_state import (
    ConsultationStateDependencyError,
    ConsultationStatePolicyContext,
    ConsultationStatePolicyInput,
    ConsultationStatePolicyIntent,
    ConsultationStatePolicyLimits,
    ConsultationStatePolicyState,
    LocalConsultationAnswerabilityPolicyClient,
    OpaConsultationAnswerabilityPolicyClient,
)


class _MockResponse:
    """提供最小 httpx 响应替身。

    :param payload: 返回给策略客户端的 JSON 负载。
    :return: 无返回值。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化响应替身。

        :param payload: 返回给策略客户端的 JSON 负载。
        :return: 无返回值。
        """
        self._payload = payload

    def raise_for_status(self) -> None:
        """模拟成功 HTTP 响应。

        :return: 无返回值。
        """
        return None

    def json(self) -> dict[str, Any]:
        """返回预置 JSON 负载。

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


def test_opa_consultation_answerability_client_builds_prefixed_data_api_url() -> None:
    """验证 OPA 问诊回答充分性客户端会正确拼接 Data API URL。

    :return: 无返回值；断言通过表示客户端支持 Nginx 前缀路径。
    """
    client = OpaConsultationAnswerabilityPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.consultation_state",
        rule_name="decision",
    )

    assert client._decision_url() == "http://example.test/opa/v1/data/vet_agent/consultation_state/decision"


def test_opa_consultation_answerability_client_parses_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 OPA 问诊回答充分性客户端能解析结构化裁决结果。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值；断言通过表示 OPA 响应契约可被业务层消费。
    """
    mock_client = _MockAsyncClient(
        {
            "result": {
                "action": "ask",
                "allow": False,
                "mode": "needs_high_value_evidence",
                "answer_scope": "insufficient",
                "blocking_slots": ["onset"],
                "unresolved_slots": ["onset"],
                "reason": "仍缺少会明显影响分诊建议的高价值信息。",
                "reasons": ["consultation_answerability_more_evidence_needed"],
            }
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: mock_client)
    client = OpaConsultationAnswerabilityPolicyClient(
        base_url="http://example.test/opa/v1",
        version="v1",
        package_path="vet_agent.consultation_state",
        rule_name="decision",
        auth_token="opa-test-token",
    )

    decision = asyncio.run(client.decide(_policy_input()))

    assert decision.decision == "ask"
    assert decision.mode == "needs_high_value_evidence"
    assert decision.policy_backend == "opa"
    assert decision.policy_path == "vet_agent.consultation_state/decision"
    assert mock_client.requests[0]["headers"]["Authorization"] == "Bearer opa-test-token"


def test_opa_consultation_answerability_client_fails_fast_when_policy_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 OPA 未加载策略时不会被误判为允许回答。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值；断言通过表示缺少 result 字段时按依赖错误显式失败。
    """
    mock_client = _MockAsyncClient({"decision_id": "opa-decision-only"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: mock_client)
    client = OpaConsultationAnswerabilityPolicyClient(
        base_url="http://example.test/opa/v1",
        version="v1",
        package_path="vet_agent.consultation_state",
        rule_name="decision",
    )

    with pytest.raises(ConsultationStateDependencyError) as exc_info:
        asyncio.run(client.decide(_policy_input()))

    assert exc_info.value.details["reason"] == "missing_required_fields"
    assert "action" in exc_info.value.details["missing_fields"]
    assert exc_info.value.details["payload_keys"] == ["decision_id"]


def test_local_consultation_answerability_client_uses_policy_state_boolean_summary() -> None:
    """验证本地策略客户端按策略摘要布尔字段判断最低上下文。

    :return: 无返回值；断言通过表示本地测试后端与 OPA 输入模型保持一致。
    """
    client = LocalConsultationAnswerabilityPolicyClient()

    decision = asyncio.run(client.decide(_policy_input(answer_now=True)))

    assert decision.decision == "answer"
    assert decision.mode == "user_requested_answer_now"
    assert decision.policy_backend == "local"


def _policy_input(*, answer_now: bool = False) -> ConsultationStatePolicyInput:
    """构造问诊回答充分性策略客户端单测输入。

    :param answer_now: 是否模拟用户明确要求先给阶段性回答。
    :return: 返回不包含用户原始文本的策略输入对象。
    """
    return ConsultationStatePolicyInput(
        context=ConsultationStatePolicyContext(
            request_id="req_policy_unit",
            trace_id="trace_policy_unit",
            user_id="user_policy_unit",
            pet_id="pet_policy_unit",
            session_id="session_policy_unit",
        ),
        state=ConsultationStatePolicyState(
            domain="gastrointestinal",
            phase="collecting_info",
            followup_rounds=0,
            asked_question_count=0,
            has_chief_complaint=True,
            has_species=True,
        ),
        intent=ConsultationStatePolicyIntent(
            answer_now=answer_now,
            wants_triage=False,
            correction=False,
            raw_intent="先给阶段性判断" if answer_now else "",
        ),
        limits=ConsultationStatePolicyLimits(max_followup_rounds=2, min_known_categories=2, max_questions=3),
        evidence_profile={
            "minimum_context": True,
            "known_category_count": 1,
            "known_categories": ["patient_identity"],
        },
        unresolved_slots=("onset",),
        advisory_slots=("onset",),
    )
