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
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyObservedFeature,
    ClinicalSafetyPolicyAction,
    ClinicalSafetyPolicyDecision,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyPolicyRequestContext,
    ClinicalSafetyPreconditionAssessment,
    ClinicalSafetyRetrievalState,
    ClinicalSafetySemanticResult,
    ClinicalSafetySignal,
    ClinicalSafetyThresholds,
    OpaClinicalSafetyPolicyClient,
    clinical_safety_required_context_hash,
    clinical_safety_semantic_premise_hash,
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
        return

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

    async def __aenter__(self) -> _MockAsyncClient:
        """进入异步上下文。

        :return: 返回客户端自身。
        """
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """退出异步上下文。

        :return: 无返回值。
        """
        return

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _MockResponse:
        """记录请求并返回预置响应。

        :param url: 请求地址。
        :param headers: 请求头。
        :param json: 请求体。
        :return: 返回响应替身。
        """
        self.requests.append({"url": url, "headers": headers, "json": json})
        return _MockResponse(self.response_payload)


def test_opa_clinical_safety_policy_client_builds_prefixed_data_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert (
        client._decision_url()
        == "http://example.test/opa/v1/data/vet_agent/clinical_safety/decision"
    )


def test_opa_clinical_safety_policy_client_parses_precondition_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 OPA 前提评估计划客户端会请求独立规则并解析资产列表。

    :param monkeypatch: pytest 替换工具。
    :return: 无返回值；断言通过表示前提计划与最终裁决共用客户端但使用不同规则。
    """
    mock_client = _MockAsyncClient(
        {"result": {"asset_ids": ["safety_a", "safety_a", "safety_b"]}}
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: mock_client)
    client = OpaClinicalSafetyPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
    )
    policy_input = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(),
        semantic_result=None,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(),
        thresholds=ClinicalSafetyThresholds(),
    )

    planned_asset_ids = asyncio.run(client.plan_preconditions(policy_input))

    assert planned_asset_ids == ("safety_a", "safety_b")
    assert mock_client.requests[0]["url"] == (
        "http://example.test/opa/v1/data/vet_agent/clinical_safety/precondition_plan"
    )


def test_clinical_safety_policy_input_passes_required_context_to_opa() -> None:
    """验证策略输入会将候选 required_context 传递给 OPA。

    :return: 无返回值；断言通过表示候选前置上下文不会在 Python 到 OPA 边界丢失。
    """
    asset = ClinicalSafetyAsset(
        asset_id="safety_policy_required_context",
        asset_type="emergency_red_flag",
        canonical_name="呼吸循环测试风险",
        category="呼吸循环",
        species_scope=("cat", "dog"),
        sex_scope=(),
        age_scope=(),
        severity="urgent",
        action_class="emergency",
        code="CYANOSIS_RISK_PATTERN",
        triage_message="呼吸循环测试风险需要优先线下处理。",
        symptoms=("呼吸困难",),
        recognition_phrases=("呼吸困难",),
        required_context={"species": ("cat", "dog"), "symptoms": ("呼吸困难",)},
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_policy_required_context.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="呼吸循环测试风险 风险识别",
        embedding_text="呼吸困难",
        metadata={},
        review_status="approved",
    )
    policy_input = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(),
        semantic_result=None,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(
            ClinicalSafetyCandidate(
                asset=asset,
                score=0.92,
                chunk_hits=(
                    ClinicalSafetyChunkHit(
                        chunk=chunk,
                        score=0.92,
                        matched_terms=("呼吸困难",),
                    ),
                ),
            ),
        ),
        thresholds=ClinicalSafetyThresholds(),
    )

    payload = policy_input.to_payload()

    assert payload["candidates"][0]["required_context"] == {
        "species": ["cat", "dog"],
        "symptoms": ["呼吸困难"],
    }


def test_clinical_safety_policy_input_projects_precondition_evidence_without_text() -> (
    None
):
    """验证策略输入保留事实引用和前提哈希但移除自然语言事实文本。

    :return: 无返回值；断言通过表示 OPA 不获得可被用于文本匹配的语义正文。
    """
    asset = ClinicalSafetyAsset(
        asset_id="safety_precondition_projection",
        asset_type="emergency_red_flag",
        canonical_name="呼吸循环测试风险",
        category="呼吸循环",
        species_scope=("cat", "dog"),
        sex_scope=(),
        age_scope=(),
        severity="urgent",
        action_class="emergency",
        code="CYANOSIS_RISK_PATTERN",
        triage_message="呼吸循环测试风险需要优先线下处理。",
        required_context={"species": ("cat", "dog"), "symptoms": ("呼吸困难",)},
    )
    candidate = ClinicalSafetyCandidate(asset=asset, score=0.92, chunk_hits=())
    required_context_hash = clinical_safety_required_context_hash(
        asset.required_context
    )
    semantic = ClinicalSafetySemanticResult(
        symptom_state="present",
        risk_evidence_state="sufficient",
        observed_features=(
            ClinicalSafetyObservedFeature(
                feature_id="f1",
                feature_kind="symptom",
                state="present",
                normalized_text="呼吸很快",
                temporal_scope="ongoing",
                resolution_state="ongoing",
            ),
        ),
        confidence=0.95,
        strategy="litellm_response_format",
    )
    assessment = ClinicalSafetyPreconditionAssessment(
        asset_id=asset.asset_id,
        required_context_hash=required_context_hash,
        semantic_premise_hash=clinical_safety_semantic_premise_hash(
            asset.required_context
        ),
        status="satisfied",
        evidence_ids=("f1",),
        confidence=0.93,
        strategy="qwen_response_format",
    )

    payload = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(),
        semantic_result=semantic,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(candidate,),
        thresholds=ClinicalSafetyThresholds(),
        precondition_assessments={asset.asset_id: assessment},
    ).to_payload()

    assert payload["semantic"]["observed_features"] == [
        {"id": "f1", "kind": "symptom", "state": "present"}
    ]
    assert payload["candidates"][0]["required_context_hash"] == required_context_hash
    assert payload["precondition_assessments"][asset.asset_id] == {
        "required_context_hash": required_context_hash,
        "status": "satisfied",
        "evidence_ids": ["f1"],
        "confidence": 0.93,
        "trusted": True,
    }


def test_opa_clinical_safety_policy_client_parses_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                        "asset_id": "safety_human_drug_001",
                        "canonical_name": "对乙酰氨基酚",
                        "code": "TOXIC_SUBSTANCE",
                        "severity": "urgent",
                        "message": "需要线下处理。",
                        "matched_terms": ["泰诺"],
                    }
                ],
                "primary_signal": {
                    "asset_id": "safety_human_drug_001",
                    "canonical_name": "对乙酰氨基酚",
                    "code": "TOXIC_SUBSTANCE",
                    "severity": "urgent",
                    "message": "需要线下处理。",
                    "matched_terms": ["泰诺"],
                },
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
    assert decision.primary_signal is not None
    assert decision.primary_signal.asset_id == "safety_human_drug_001"
    assert mock_client.requests[0]["headers"]["X-Request-ID"] == "req_test"


def test_opa_clinical_safety_policy_client_rejects_missing_primary_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证升级决策缺少主信号时策略客户端快速失败。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值；断言通过表示 Python 不使用本地排序兜底。
    """
    payload = _escalation_result_payload()
    del payload["result"]["primary_signal"]
    mock_client = _MockAsyncClient(payload)

    def async_client_factory(timeout: float) -> _MockAsyncClient:
        """构造缺失主信号的策略响应客户端替身。

        :param timeout: httpx 客户端超时时间。
        :return: 返回预置响应的异步客户端替身。
        """
        del timeout
        return mock_client

    monkeypatch.setattr(httpx, "AsyncClient", async_client_factory)
    client = OpaClinicalSafetyPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
    )

    with pytest.raises(RuntimeError, match="primary_signal field is required"):
        asyncio.run(client.decide(_empty_policy_input()))


def test_opa_clinical_safety_policy_client_rejects_missing_allow_primary_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 allow 决策也必须显式返回 null 主信号字段。

    :param monkeypatch: pytest 替身工具。
    :return: 无返回值；断言通过表示字段缺失不会被误当作 null。
    """
    payload = {
        "result": {
            "action": "allow",
            "allow": True,
            "message": "测试临床安全策略允许继续。",
            "reasons": [],
            "signals": [],
        }
    }
    mock_client = _MockAsyncClient(payload)

    def async_client_factory(timeout: float) -> _MockAsyncClient:
        """构造缺失 allow 主信号字段的策略响应客户端替身。

        :param timeout: httpx 客户端超时时间。
        :return: 返回预置响应的异步客户端替身。
        """
        del timeout
        return mock_client

    monkeypatch.setattr(httpx, "AsyncClient", async_client_factory)
    client = OpaClinicalSafetyPolicyClient(
        base_url="http://example.test/opa",
        version="v1",
        package_path="vet_agent.clinical_safety",
        rule_name="decision",
    )

    with pytest.raises(RuntimeError, match="primary_signal field is required"):
        asyncio.run(client.decide(_empty_policy_input()))


def test_clinical_safety_policy_decision_rejects_invalid_primary_contract() -> None:
    """验证策略决策对象在构造阶段拒绝主信号与信号集脱钩。

    :return: 无返回值；断言通过表示自定义策略客户端也不能绕过主信号契约。
    """
    signal = ClinicalSafetySignal(
        asset_id="safety_human_drug_001",
        canonical_name="对乙酰氨基酚",
        code="TOXIC_SUBSTANCE",
        severity="urgent",
        message="需要线下处理。",
    )

    with pytest.raises(
        ValueError, match="primary signal must match exactly one signal"
    ):
        ClinicalSafetyPolicyDecision(
            action=ClinicalSafetyPolicyAction.ESCALATE,
            allow=True,
            message="测试临床安全策略完成结构化候选裁决。",
            primary_signal=signal,
        )


def _escalation_result_payload() -> dict[str, Any]:
    """构造包含有效主信号的最小 OPA 升级结果。

    :return: 返回可被策略客户端解析的响应负载。
    """
    signal = {
        "asset_id": "safety_human_drug_001",
        "canonical_name": "对乙酰氨基酚",
        "code": "TOXIC_SUBSTANCE",
        "severity": "urgent",
        "message": "需要线下处理。",
        "matched_terms": ["泰诺"],
    }
    return {
        "result": {
            "action": "escalate",
            "allow": True,
            "message": "需要线下处理。",
            "reasons": ["clinical_safety_candidate:TOXIC_SUBSTANCE:emergency"],
            "signals": [signal],
            "primary_signal": signal,
        }
    }


def _empty_policy_input() -> ClinicalSafetyPolicyInput:
    """构造不依赖候选和外部服务的策略输入。

    :return: 返回最小临床安全策略输入对象。
    """
    return ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(),
        semantic_result=None,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(),
        thresholds=ClinicalSafetyThresholds(),
    )
