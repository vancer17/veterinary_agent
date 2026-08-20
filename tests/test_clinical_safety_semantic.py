"""
文件：tests/test_clinical_safety_semantic.py
作用：验证临床安全结构化语义抽取契约、可信状态门控与显式降级行为。
说明：本文件只覆盖语义抽取层，不断言临床安全裁决、严重级别或候选归一行为。
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from vet_agent import Settings
from vet_agent.clinical_safety import (
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetySemanticResult,
)


class FakeQwenClient:
    """提供固定返回值的测试客户端。"""

    def __init__(self, raw_response: str) -> None:
        """初始化测试客户端。

        :param raw_response: 模拟模型返回文本。
        :return: 无返回值。
        """
        self.raw_response = raw_response

    @property
    def available(self) -> bool:
        """声明测试客户端始终可用。

        :return: 始终返回 True。
        """
        return True

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """返回预设模型输出。

        :param messages: 传入的消息列表。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回固定响应。
        """
        del messages, model, temperature
        return self.raw_response

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """返回预设结构化模型输出。

        :param messages: 传入的消息列表。
        :param response_model: 结构化响应模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回固定结构化响应。
        """
        del messages, model, temperature
        return response_model.model_validate_json(self.raw_response)


def test_clinical_safety_semantic_extractor_parses_llm_json() -> None:
    """验证 LLM 结构化语义输出可以被稳定解析。

    :return: 无返回值；断言通过表示结构化语义抽取可用。
    """
    settings = Settings()
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "sex": "female",
              "age_group": "senior",
              "age_text": "8岁",
              "exposure_state": "confirmed",
              "symptom_state": "present",
              "temporal_state": "current",
              "temporal_scope": "ongoing",
              "resolution_state": "ongoing",
              "temporal_text": "现在",
              "intent_type": "toxicity",
              "risk_evidence_state": "sufficient",
              "observed_features": [
                {
                  "feature_kind": "symptom",
                  "state": "present",
                  "normalized_text": "呕吐",
                  "temporal_scope": "ongoing",
                  "resolution_state": "ongoing"
                }
              ],
              "high_risk_terms": ["泰诺", "呕吐"],
              "negated_terms": [],
              "confidence": 0.92,
              "rationale": "用户明确描述猫误食泰诺并出现呕吐。"
            }
            """
        ),
        settings,
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家猫误食泰诺后开始呕吐，想先确认要不要急诊。",
            pet_context_summary="宠物画像: 物种=猫, 年龄=8岁, 性别=母。",
            model="qwen-plus",
        )
    )

    assert result.strategy == "litellm_response_format"
    assert result.is_trusted()
    assert result.species == "cat"
    assert result.sex == "female"
    assert result.age_group == "senior"
    assert result.exposure_state == "confirmed"
    assert result.intent_type == "toxicity"
    assert result.risk_evidence_state == "sufficient"
    assert result.observed_features[0].feature_id == "f1"
    assert result.observed_features[0].state == "present"
    assert result.observed_features[0].normalized_text == "呕吐"
    assert result.to_dict()["observed_features"][0]["normalized_text"] == "呕吐"
    assert "泰诺" in result.high_risk_terms


def test_clinical_safety_semantic_low_confidence_returns_explicit_degraded_result() -> None:
    """验证低置信度语义结果会显式降级而不补造语义事实。

    :return: 无返回值；断言通过表示低置信度 LLM 输出不会直接进入裁决面。
    """
    settings = Settings(semantic_extraction_min_confidence=0.9)
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "sex": "female",
              "age_group": "senior",
              "age_text": "8岁",
              "exposure_state": "confirmed",
              "symptom_state": "present",
              "temporal_state": "current",
              "temporal_scope": "ongoing",
              "resolution_state": "ongoing",
              "temporal_text": "现在",
              "intent_type": "toxicity",
              "risk_evidence_state": "sufficient",
              "observed_features": [
                {
                  "feature_kind": "symptom",
                  "state": "present",
                  "normalized_text": "呕吐",
                  "temporal_scope": "ongoing",
                  "resolution_state": "ongoing"
                }
              ],
              "high_risk_terms": ["泰诺", "呕吐"],
              "negated_terms": [],
              "confidence": 0.31,
              "rationale": "看起来像误食泰诺并呕吐。"
            }
            """
        ),
        settings,
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家狗今天有点没精神，想先确认一下。",
            pet_context_summary="宠物画像: 物种=狗, 年龄=8岁, 性别=公。",
            model="qwen-plus",
        )
    )

    assert result.is_low_confidence()
    assert not result.is_trusted()
    assert result.strategy == "litellm_response_format_low_confidence"
    assert result.species == "unknown"
    assert result.sex == "unknown"
    assert result.exposure_state == "unknown"
    assert result.intent_type == "other"
    assert result.risk_evidence_state == "unknown"
    assert not hasattr(result, "to_query_hints")


def test_clinical_safety_semantic_disabled_does_not_create_rule_based_facts() -> None:
    """验证禁用模型抽取时只返回显式失败状态，不生成关键词推断事实。

    :return: 无返回值；断言通过表示禁用状态符合临床安全语义 Fail Fast 约束。
    """
    extractor = ClinicalSafetySemanticExtractorAgent(
        qwen=None,
        settings=Settings(enable_llm_semantic_extraction=False),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家猫误食泰诺后正在呕吐。",
            pet_context_summary="宠物画像: 物种=猫, 年龄=8岁, 性别=母。",
            model="qwen-plus",
        )
    )

    assert not result.is_trusted()
    assert result.strategy == "semantic_extraction_disabled"
    assert result.species == "unknown"
    assert result.exposure_state == "unknown"
    assert result.symptom_state == "unknown"
    assert result.risk_evidence_state == "unknown"
    assert result.high_risk_terms == ()
    assert result.to_fallback_state().stage == "disabled"


def test_clinical_safety_semantic_insufficient_evidence_does_not_emit_query_hints() -> None:
    """验证证据不足时审计短语不会被转化为强召回提示。

    :return: 无返回值；断言通过表示 high_risk_terms 不再承担风险证据门槛职责。
    """
    result = ClinicalSafetySemanticResult(
        species="dog",
        exposure_state="unknown",
        symptom_state="unknown",
        intent_type="triage",
        risk_evidence_state="insufficient",
        high_risk_terms=("呼吸困难",),
        confidence=0.96,
        strategy="litellm_response_format",
        source_text="如果狗呼吸困难，需要急诊吗？",
    )

    assert result.is_trusted()
    assert not hasattr(result, "to_query_hints")


def test_clinical_safety_semantic_missing_evidence_state_fails_fast() -> None:
    """验证缺少证据充分性字段时不会从旧语义字段推导兼容值。

    :return: 无返回值；断言通过表示结构化语义契约缺失新字段时直接进入 schema 降级。
    """
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "sex": "female",
              "age_group": "adult",
              "age_text": "3岁",
              "exposure_state": "confirmed",
              "symptom_state": "present",
              "temporal_state": "current",
              "temporal_scope": "ongoing",
              "resolution_state": "ongoing",
              "temporal_text": "现在",
              "intent_type": "toxicity",
              "high_risk_terms": ["泰诺"],
              "negated_terms": [],
              "confidence": 0.95,
              "rationale": "测试结构化响应故意缺少证据充分性字段。"
            }
            """
        ),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家猫误食泰诺后正在呕吐。",
            pet_context_summary="宠物画像: 物种=猫, 年龄=3岁, 性别=母。",
            model="qwen-plus",
        )
    )

    assert not result.is_trusted()
    assert result.strategy == "semantic_extraction_invalid_schema"
    assert result.risk_evidence_state == "unknown"


def test_clinical_safety_semantic_invalid_schema_does_not_create_rule_based_facts() -> None:
    """验证结构化响应不符合契约时只返回显式失败状态，不宽松修复字段。

    :return: 无返回值；断言通过表示无效 schema 不会进入临床安全语义可信面。
    """
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "confidence": 0.95
            }
            """
        ),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家猫误食泰诺后正在呕吐。",
            pet_context_summary="宠物画像: 物种=猫, 年龄=8岁, 性别=母。",
            model="qwen-plus",
        )
    )

    assert not result.is_trusted()
    assert result.strategy == "semantic_extraction_invalid_schema"
    assert result.species == "unknown"
    assert result.exposure_state == "unknown"
    assert result.risk_evidence_state == "unknown"
    assert result.high_risk_terms == ()
    assert result.to_fallback_state().stage == "invalid_schema"
