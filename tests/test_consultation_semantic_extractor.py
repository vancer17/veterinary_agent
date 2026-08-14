"""
=============================================================================
文件：tests/test_consultation_semantic_extractor.py
作用：验证问诊语义抽取的 LiteLLM response_format 契约、显式失败状态和低置信门控。
范围：仅覆盖问诊语义抽取层，不断言问诊状态合并、回答充分性、长期记忆写入或临床安全裁决。
说明：测试客户端只提供结构化输出，不提供关键词、正则或手写 JSON 回退能力。
=============================================================================
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from vet_agent import Settings
from vet_agent.agents import ConsultationSemanticExtractorAgent


class FakeStructuredQwenClient:
    """提供固定结构化输出的问诊语义抽取测试客户端。

    :param payload: 模拟 LiteLLM response_format 返回的结构化对象。
    :param available: 模拟客户端是否可用。
    :return: 无返回值。
    """

    def __init__(self, payload: dict[str, object], *, available: bool = True) -> None:
        """初始化测试客户端。

        :param payload: 模拟结构化模型返回值。
        :param available: 模拟客户端是否可用。
        :return: 无返回值。
        """
        self.payload = payload
        self._available = available

    @property
    def available(self) -> bool:
        """返回测试客户端可用状态。

        :return: 可用时返回 True，否则返回 False。
        """
        return self._available

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[BaseModel],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> BaseModel:
        """返回通过 Pydantic 校验的结构化测试响应。

        :param messages: 结构化模型消息。
        :param response_model: 结构化响应模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回通过 Pydantic 校验后的响应对象。
        """
        del messages, model, temperature
        return response_model.model_validate(self.payload)


def test_consultation_semantic_extractor_uses_structured_output() -> None:
    """验证问诊语义抽取通过结构化输出生成可信事实。

    :return: 无返回值；断言通过表示结构化问诊语义可进入状态合并。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient(
            {
                "facts": [
                    {
                        "key": "appetite",
                        "value": "仍会进食但主动性下降",
                        "status": "confirmed",
                        "confidence": 0.91,
                        "source_text": "饭还是吃的，就是没以前积极",
                        "category": "intake_output",
                    }
                ],
                "intent": {
                    "answer_now": True,
                    "wants_triage": True,
                    "correction": False,
                    "raw_intent": "用户希望根据现有信息先判断。",
                },
                "confidence": 0.92,
                "rationale": "用户明确描述食欲变化并要求先判断。",
            }
        ),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="饭还是吃的，就是没以前积极，先告诉我需不需要检查。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "litellm_response_format"
    assert result.is_trusted()
    assert result.intent.answer_now is True
    assert result.facts[0].key.value == "appetite"
    assert result.to_metadata()["trusted"] is True


def test_consultation_semantic_extractor_keeps_open_observations() -> None:
    """验证问诊语义抽取可以保留核心槽位外的开放观察。

    :return: 无返回值；断言通过表示开放观察不会被迫映射为核心槽位。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient(
            {
                "facts": [],
                "observations": [
                    {
                        "category": "urinary",
                        "label": "排尿异常",
                        "value": "频繁去猫砂盆但尿量很少",
                        "status": "confirmed",
                        "confidence": 0.93,
                        "source_text": "一直去猫砂盆但尿量很少",
                        "temporal_text": "今天",
                    }
                ],
                "intent": {
                    "answer_now": True,
                    "wants_triage": True,
                    "correction": False,
                    "raw_intent": "用户希望先了解排尿异常的风险范围。",
                },
                "confidence": 0.94,
                "rationale": "用户明确描述排尿异常且要求先回答。",
            }
        ),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="今天一直去猫砂盆但尿量很少，先告诉我需要注意什么。",
            pet_context_summary="宠物画像: 物种=猫。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "litellm_response_format"
    assert result.facts == []
    assert result.observations[0].category == "urinary"
    assert result.observations[0].label == "排尿异常"
    assert result.to_metadata()["observations"][0]["value"] == "频繁去猫砂盆但尿量很少"


def test_consultation_semantic_low_confidence_does_not_create_trusted_facts() -> None:
    """验证低置信结构化输出不会进入可信问诊事实。

    :return: 无返回值；断言通过表示低置信结果不会被状态层当作事实使用。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient(
            {
                "facts": [
                    {
                        "key": "vomiting",
                        "value": "可能呕吐",
                        "status": "uncertain",
                        "confidence": 0.4,
                        "source_text": "像是吐了",
                        "category": "intake_output",
                    }
                ],
                "intent": {
                    "answer_now": False,
                    "wants_triage": False,
                    "correction": False,
                    "raw_intent": "",
                },
                "confidence": 0.4,
                "rationale": "表达不够明确。",
            }
        ),
        Settings(semantic_extraction_min_confidence=0.65),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="像是吐了。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "litellm_response_format_low_confidence"
    assert not result.is_trusted()
    assert result.facts == []
    assert result.fallback_reason == "consultation_semantic_low_confidence:0.40"


def test_consultation_semantic_disabled_does_not_fallback_to_rules() -> None:
    """验证禁用问诊语义抽取时只返回显式禁用状态。

    :return: 无返回值；断言通过表示禁用状态不会启用关键词或正则回退。
    """
    extractor = ConsultationSemanticExtractorAgent(
        qwen=None,
        settings=Settings(enable_llm_semantic_extraction=False),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="别再追问了，直接说目前怎么看。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "semantic_extraction_disabled"
    assert not result.is_trusted()
    assert result.intent.answer_now is False
    assert result.facts == []


def test_consultation_semantic_invalid_schema_is_explicit_failure() -> None:
    """验证结构化响应不符合契约时返回显式 schema 失败状态。

    :return: 无返回值；断言通过表示非法输出不会被宽松修复。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient(
            {
                "facts": [
                    {
                        "key": "diagnosis",
                        "value": "胃肠炎",
                        "status": "confirmed",
                        "confidence": 0.91,
                        "source_text": "胃肠炎",
                        "category": "other",
                    }
                ],
                "intent": {
                    "answer_now": False,
                    "wants_triage": False,
                    "correction": False,
                    "raw_intent": "",
                },
                "confidence": 0.92,
                "rationale": "非法诊断字段。",
            }
        ),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="是不是胃肠炎？",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "semantic_extraction_invalid_schema"
    assert not result.is_trusted()
    assert result.facts == []


def test_consultation_semantic_unavailable_is_explicit_failure() -> None:
    """验证结构化模型客户端不可用时返回显式不可用状态。

    :return: 无返回值；断言通过表示不可用状态不会被伪装为空成功。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient({}, available=False),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="今天没有再吐。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "semantic_extraction_unavailable"
    assert not result.is_trusted()
    assert result.facts == []


def test_consultation_semantic_invalid_schema_raises_no_validation_to_caller() -> None:
    """验证抽取器吞吐 schema 失败为显式状态而非泄漏 ValidationError。

    :return: 无返回值；断言通过表示主链路可获得稳定失败状态。
    """
    extractor = ConsultationSemanticExtractorAgent(
        FakeStructuredQwenClient({"facts": []}),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="今天没有再吐。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "semantic_extraction_invalid_schema"
    assert not result.is_trusted()


def test_consultation_semantic_failed_call_returns_explicit_failure() -> None:
    """验证结构化模型调用失败时返回显式失败状态。

    :return: 无返回值；断言通过表示模型异常不会触发规则回退。
    """
    class FailingStructuredQwenClient(FakeStructuredQwenClient):
        """提供固定失败行为的问诊语义抽取测试客户端。

        :return: 无返回值。
        """

        async def chat_structured(
            self,
            messages: list[dict[str, str]],
            *,
            response_model: type[BaseModel],
            model: str | None = None,
            temperature: float = 0.0,
        ) -> BaseModel:
            """模拟结构化模型调用失败。

            :param messages: 结构化模型消息。
            :param response_model: 结构化响应模型。
            :param model: 模型名称。
            :param temperature: 采样温度。
            :return: 本方法始终抛出异常，不返回结构化对象。
            """
            del messages, response_model, model, temperature
            raise RuntimeError("structured model failed")

    extractor = ConsultationSemanticExtractorAgent(
        FailingStructuredQwenClient({}),
        Settings(),
    )

    result = asyncio.run(
        extractor.extract(
            user_text="今天没有再吐。",
            pet_context_summary="宠物画像: 物种=犬。",
            previous_state=None,
            model="qwen-plus",
        )
    )

    assert result.strategy == "semantic_extraction_failed"
    assert not result.is_trusted()
