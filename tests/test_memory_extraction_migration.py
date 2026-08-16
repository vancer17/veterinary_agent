"""
=============================================================================
文件：tests/test_memory_extraction_migration.py
作用：验证长期记忆候选抽取迁移后的结构化来源边界与结构化输出路径。
范围：仅覆盖 memory_extraction 包本身，不验证数据库写入或回合编排。
说明：测试替身仅提供结构化 response_format 输出，不提供关键词、正则或 JSON
      修复回退能力。
=============================================================================
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from vet_agent import AgentTurnResponse, Settings, TrustedIdentity
from vet_agent.memory_extraction import (
    MemoryExtractionAgent,
    MemoryExtractionRequest,
    MemoryExtractionStrategy,
)


class FakeMemoryExtractionQwenClient:
    """提供固定结构化输出的长期记忆候选抽取测试客户端。

    :param payload: 模拟 LiteLLM response_format 返回的结构化对象。
    :return: 无返回值。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        """初始化测试客户端。

        :param payload: 模拟结构化模型返回值。
        :return: 无返回值。
        """
        self.payload = payload

    @property
    def available(self) -> bool:
        """声明测试客户端始终可用。

        :return: 始终返回 True。
        """
        return True

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
        del model, temperature
        prompt_payload = json.loads(messages[1]["content"])
        assert prompt_payload["task"] == "将显式来源归一为长期记忆候选提议。"
        assert prompt_payload["payload"]["source_count"] == 1
        return response_model.model_validate(self.payload)


def test_memory_extraction_request_preserves_explicit_sources() -> None:
    """验证长期记忆抽取请求会保留显式来源边界。

    :return: 无返回值；断言通过表示来源边界没有被 joined_text 混合。
    """
    identity = TrustedIdentity(user_id="u_bound", pet_id="p_bound", session_id="s_bound")
    response = AgentTurnResponse(
        id="turn_bound",
        request_id="req_bound",
        trace_id="tr_bound",
        model="qwen-plus",
        status="completed",
        output_text="assistant summary",
        metadata={
            "memory_extraction_sources": [
                {
                    "source_id": "task_a",
                    "entry_kind": "task",
                    "user_text": "猫今天呕吐",
                    "assistant_text": "先观察",
                    "task_id": "task_a",
                    "task_key": "gastrointestinal",
                    "task_title": "胃肠道",
                    "task_domain": "gastrointestinal",
                    "consultation_state": {"domain": "gastrointestinal"},
                    "metadata": {"index": 0},
                },
                {
                    "source_id": "task_b",
                    "entry_kind": "task",
                    "user_text": "又开始咳嗽",
                    "assistant_text": "建议就诊",
                    "task_id": "task_b",
                    "task_key": "respiratory",
                    "task_title": "呼吸道",
                    "task_domain": "respiratory",
                    "consultation_state": {"domain": "respiratory"},
                    "metadata": {"index": 1},
                },
            ]
        },
    )

    request = MemoryExtractionRequest.from_turn(
        identity,
        user_text="joined text should not be used when explicit sources exist",
        response=response,
    )

    assert len(request.sources) == 2
    assert request.source_map()["task_a"].task_domain == "gastrointestinal"
    assert request.source_map()["task_b"].task_domain == "respiratory"
    assert request.to_prompt_payload()["source_count"] == 2


def test_memory_extraction_agent_returns_structured_result() -> None:
    """验证长期记忆候选抽取器能够返回结构化结果。

    :return: 无返回值；断言通过表示结构化 response_format 链路可工作。
    """
    identity = TrustedIdentity(user_id="u_extract", pet_id="p_extract", session_id="s_extract")
    response = AgentTurnResponse(
        id="turn_extract",
        request_id="req_extract",
        trace_id="tr_extract",
        model="qwen-plus",
        status="completed",
        output_text="assistant summary",
        metadata={
            "memory_extraction_sources": [
                {
                    "source_id": "task_a",
                    "entry_kind": "task",
                    "user_text": "我家狗对鸡肉过敏",
                    "assistant_text": "记录下来",
                    "task_id": "task_a",
                    "task_key": "medical",
                    "task_title": "过敏史",
                    "task_domain": "medical",
                    "consultation_state": {"domain": "medical"},
                    "metadata": {"index": 0},
                }
            ]
        },
    )
    client = FakeMemoryExtractionQwenClient(
        {
            "proposals": [
                {
                    "source_id": "task_a",
                    "subject_scope": "pet",
                    "fact_type": "medical",
                    "fact_key": "allergy",
                    "fact_value": "鸡肉过敏",
                    "assertion_status": "confirmed",
                    "durability": "durable",
                    "temporal_scope": "historical",
                    "confidence": 0.93,
                    "source_kind": "user_text",
                    "source_text": "我家狗对鸡肉过敏",
                    "rationale": "用户明确陈述。",
                }
            ],
            "confidence": 0.93,
            "rationale": "测试替身返回结构化长期记忆候选。",
        }
    )
    extractor = MemoryExtractionAgent(client, Settings(litellm_api_key="sk-test"))

    result = asyncio.run(
        extractor.extract(
            identity=identity,
            user_text="忽略 joined_text 兜底",
            response=response,
            model="qwen-plus",
        )
    )

    assert result.strategy == MemoryExtractionStrategy.LITELLM_RESPONSE_FORMAT
    assert result.is_trusted()
    assert len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.source_id == "task_a"
    assert proposal.fact_key == "allergy"
    assert proposal.metadata["source_entry"]["task_key"] == "medical"
