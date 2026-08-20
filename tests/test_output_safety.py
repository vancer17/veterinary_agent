"""
文件：tests/test_output_safety.py
作用：验证输出安全复核迁移后的服务、策略和 OPA 客户端行为。
范围：覆盖结构化候选发现、observe/enforce 模式和 Data API URL 构造，不依赖旧输出清洗关键词规则。
说明：本测试不调用真实 Guardrails 模型或外部 OPA 服务，避免将网络状态纳入单元门禁。
"""

from __future__ import annotations

import asyncio

import pytest

from vet_agent import AgentTurnResponse, Settings, VetSegment
from vet_agent.clinical_safety import ClinicalSafetySignal
from vet_agent.output_safety import (
    LocalOutputSafetyPolicyClient,
    OpaOutputSafetyPolicyClient,
    OutputSafetyCandidate,
    OutputSafetyCandidateCategory,
    OutputSafetyCandidateSource,
    OutputSafetyDecision,
    OutputSafetyDecisionAction,
    OutputSafetyPolicyClient,
    OutputSafetyService,
    StaticOutputSafetyRepository,
)
from vet_agent.output_safety.models import OutputSafetyReviewContext


class _StaticOutputSafetyDetector:
    """为单元测试提供显式注入的输出安全检测器替身。

    :return: 无返回值。
    """

    def __init__(self, candidates: tuple[OutputSafetyCandidate, ...]) -> None:
        """初始化测试检测器。

        :param candidates: 预置候选。
        :return: 无返回值。
        """
        self._candidates = candidates

    def collect(
        self, context: OutputSafetyReviewContext
    ) -> tuple[OutputSafetyCandidate, ...]:
        """返回预置的输出安全候选。

        :param context: 输出安全复核上下文。
        :return: 返回候选元组。
        """
        del context
        return self._candidates

    def is_ready(self) -> bool:
        """检查测试检测器是否可用。

        :return: 始终返回 True。
        """
        return True


class _RewritePolicyClient(OutputSafetyPolicyClient):
    """为单元测试提供显式的 rewrite 策略裁决替身。

    :return: 无返回值。
    """

    async def decide(
        self,
        context: OutputSafetyReviewContext,
        candidates: tuple[OutputSafetyCandidate, ...],
    ) -> OutputSafetyDecision:
        """返回 rewrite 裁决。

        :param context: 输出安全复核上下文。
        :param candidates: 输出安全候选。
        :return: 返回 rewrite 裁决。
        """
        del context, candidates
        return OutputSafetyDecision(
            action=OutputSafetyDecisionAction.REWRITE,
            allow=True,
            message="rewrite is requested by policy",
            replacement_text=None,
        )

    def is_ready(self) -> bool:
        """检查测试策略客户端是否可用。

        :return: 始终返回 True。
        """
        return True


def _response(text: str) -> AgentTurnResponse:
    """构造最小可复核的 Agent 响应。

    :param text: 响应文本。
    :return: 返回 Agent 响应对象。
    """
    segment = VetSegment(
        type="medical_consultation",
        title="症状判断与下一步",
        content=text,
        output_text=text,
    )

    return AgentTurnResponse(
        id="turn_test",
        request_id="req_test",
        trace_id="trace_test",
        model="qwen-plus",
        status="completed",
        output_text=text,
        segments=[segment],
    )


def test_output_safety_dedupe_preserves_clinical_asset_identity() -> None:
    """验证输出安全去重不会合并来自不同资产的临床安全信号。

    :return: 无返回值；断言通过表示输出安全复核保留资产级审计身份。
    """
    service = OutputSafetyService(
        Settings(enable_output_safety=False),
        repository=StaticOutputSafetyRepository(),
        detectors=(),
        policy_client=LocalOutputSafetyPolicyClient(),
    )
    signals = [
        ClinicalSafetySignal(
            asset_id="safety_asset_a",
            canonical_name="测试资产 A",
            code="EMERGENCY_MODE_7K4Q9PXRAB",
            severity="urgent",
            message="同一测试分诊口径。",
        ),
        ClinicalSafetySignal(
            asset_id="safety_asset_b",
            canonical_name="测试资产 B",
            code="EMERGENCY_MODE_7K4Q9PXRAB",
            severity="urgent",
            message="同一测试分诊口径。",
        ),
    ]

    result = service._dedupe_signals(signals)

    assert result == signals


def test_output_safety_observe_mode_preserves_dosage_expression() -> None:
    """验证 observe 模式只记录候选，不改写输出文本。

    :return: 无返回值；断言通过表示输出安全已从字符串修补迁移为结构化观测。
    """
    candidate = OutputSafetyCandidate(
        code="OUTPUT_DOSAGE_EXPRESSION",
        category=OutputSafetyCandidateCategory.DOSAGE,
        source=OutputSafetyCandidateSource.GUARDRAILS,
        severity="caution",
        message="输出出现具体剂量表达。",
        matched_terms=("5 mg/kg",),
    )
    service = OutputSafetyService(
        Settings(enable_output_safety=True, output_safety_mode="observe"),
        repository=StaticOutputSafetyRepository(),
        detectors=(_StaticOutputSafetyDetector((candidate,)),),
        policy_client=LocalOutputSafetyPolicyClient(),
    )

    result = asyncio.run(
        service.review_response(_response("You can give 5 mg/kg twice daily."))
    )

    assert "5 mg/kg" in result.output_text
    assert any(
        signal.code == "OUTPUT_DOSAGE_EXPRESSION" for signal in result.safety_signals
    )
    assert result.metadata["output_safety_decision"]["action"] == "observe"
    assert result.metadata["multi_agent_path"][-2:] == [
        "OutputSafetyService",
        "OutputSafetyPolicyLocal",
    ]


def test_output_safety_enforce_rewrite_not_ready_fails_fast() -> None:
    """验证 rewrite 动作未就绪时会直接失败。

    :return: 无返回值；断言通过表示服务不会回退到隐式字符串修补。
    """
    candidate = OutputSafetyCandidate(
        code="OUTPUT_TOPIC_BOUNDARY",
        category=OutputSafetyCandidateCategory.TOPIC_BOUNDARY,
        source=OutputSafetyCandidateSource.GUARDRAILS,
        severity="caution",
        message="输出主题可能越界。",
    )
    service = OutputSafetyService(
        Settings(enable_output_safety=True, output_safety_mode="enforce"),
        repository=StaticOutputSafetyRepository(),
        detectors=(_StaticOutputSafetyDetector((candidate,)),),
        policy_client=_RewritePolicyClient(),
    )

    with pytest.raises(RuntimeError, match="rewrite"):
        asyncio.run(service.review_response(_response("请说明一下宠物的情况。")))


def test_opa_output_safety_client_builds_prefixed_data_api_url() -> None:
    """验证 OPA Data API 客户端支持 Nginx 前缀路径。

    :return: 无返回值；断言通过表示策略客户端不会依赖 host/port 拆分。
    """
    client = OpaOutputSafetyPolicyClient(
        base_url="http://example.test/opa/v1",
        version="v1",
        package_path="vet_agent.output_safety",
        rule_name="decision",
    )

    assert (
        client._decision_url()
        == "http://example.test/opa/v1/data/vet_agent/output_safety/decision"
    )
