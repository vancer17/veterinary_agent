"""
文件：src/vet_agent/agents/semantic_extractor.py
作用：使用 LLM 将用户自然语言归一为稳定问诊事实与对话意图。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vet_agent import Settings
from vet_agent.runtime import QwenClient


ALLOWED_FACT_KEYS = {
    "species",
    "life_stage_or_age",
    "weight",
    "onset",
    "mental_status",
    "appetite",
    "vomiting",
    "stool",
    "breathing",
    "pain_or_mobility",
    "behavior_context",
    "current_food",
    "symptom_detail",
}
ALLOWED_STATUSES = {"confirmed", "negative", "unknown", "uncertain", "contradicted"}
NEGATIVE_VALUE_LABELS = {
    "vomiting": "无呕吐",
    "stool": "未见排便相关异常",
    "breathing": "呼吸未见明显异常",
    "pain_or_mobility": "未见明显疼痛或活动异常",
}


@dataclass(frozen=True)
class SemanticFact:
    key: str
    value: str
    status: str
    confidence: float
    source_text: str = ""
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。

        :return: 返回函数执行结果。
        """
        return {
            "key": self.key,
            "value": self.value,
            "status": self.status,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "category": self.category,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SemanticIntent:
    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。

        :return: 返回函数执行结果。
        """
        return {
            "answer_now": self.answer_now,
            "wants_triage": self.wants_triage,
            "correction": self.correction,
            "raw_intent": self.raw_intent,
        }


@dataclass(frozen=True)
class SemanticExtractionResult:
    facts: list[SemanticFact]
    intent: SemanticIntent = field(default_factory=SemanticIntent)
    strategy: str = "rule_fallback"
    fallback_reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 和状态持久化使用的字典。

        :return: 返回函数执行结果。
        """
        return {
            "agent": "ConsultationSemanticExtractorAgent",
            "strategy": self.strategy,
            "fallback_reason": self.fallback_reason,
            "intent": self.intent.to_dict(),
            "facts": [fact.to_dict() for fact in self.facts],
        }


class SemanticFactItem(BaseModel):
    key: str = Field(min_length=1)
    value: str = Field(default="")
    status: str = Field(default="confirmed")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source_text: str = Field(default="")
    category: str = Field(default="")


class SemanticIntentItem(BaseModel):
    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = ""


class SemanticExtractorOutput(BaseModel):
    facts: list[SemanticFactItem] = Field(default_factory=list, max_length=16)
    intent: SemanticIntentItem = Field(default_factory=SemanticIntentItem)


class ConsultationSemanticExtractorAgent:
    """使用 LLM 抽取自然语言事实，失败时由调用方继续使用规则兜底。"""

    def __init__(self, qwen: QwenClient | None, settings: Settings) -> None:
        """初始化语义抽取 Agent。

        :param qwen: 通义千问兼容客户端。
        :param settings: 应用配置。
        :return: 无返回值。
        """
        self.qwen = qwen
        self.settings = settings

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        previous_state: dict[str, Any] | None,
        model: str,
    ) -> SemanticExtractionResult:
        """从用户本轮输入中抽取结构化问诊事实和控制意图。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param previous_state: 上一轮问诊状态。
        :param model: 模型名称。
        :return: 返回函数执行结果。
        """
        if not self._llm_enabled():
            return SemanticExtractionResult(
                facts=[],
                strategy="rule_fallback",
                fallback_reason="llm_semantic_extraction_disabled",
            )
        try:
            raw = await self.qwen.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are ConsultationSemanticExtractorAgent in a veterinary multi-agent system. "
                            "Extract structured facts and user intent only. Do not diagnose, do not advise treatment, "
                            "and return JSON only."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(user_text, pet_context_summary, previous_state),
                    },
                ],
                model=model,
                temperature=0.0,
            )
            parsed = SemanticExtractorOutput.model_validate(self._extract_json(raw))
            facts = self._normalize_facts(parsed.facts)
            intent = SemanticIntent(
                answer_now=parsed.intent.answer_now,
                wants_triage=parsed.intent.wants_triage,
                correction=parsed.intent.correction,
                raw_intent=parsed.intent.raw_intent.strip()[:120],
            )
            return SemanticExtractionResult(facts=facts, intent=intent, strategy="llm_semantic_extractor")
        except (ValidationError, ValueError, json.JSONDecodeError, RuntimeError):
            return SemanticExtractionResult(
                facts=[],
                strategy="rule_fallback",
                fallback_reason="llm_semantic_extraction_failed",
            )

    def _llm_enabled(self) -> bool:
        """检查 LLM 语义抽取是否可用。

        :return: 返回函数执行结果。
        """
        return bool(
            self.settings.enable_llm_semantic_extraction
            and self.qwen is not None
            and self.qwen.available
        )

    def _prompt(
        self,
        user_text: str,
        pet_context_summary: str,
        previous_state: dict[str, Any] | None,
    ) -> str:
        """构造语义抽取提示词。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param previous_state: 上一轮问诊状态。
        :return: 返回函数执行结果。
        """
        return json.dumps(
            {
                "task": "将用户本轮输入归一为稳定问诊事实与控制意图，只输出 JSON。",
                "rules": [
                    "facts[].key 必须来自 allowed_fact_keys。",
                    "status 只能是 confirmed、negative、unknown、uncertain、contradicted。",
                    "confirmed 表示用户明确确认；negative 表示用户明确否认某项异常；unknown 表示用户不知道；uncertain 表示表达不确定。",
                    "不要把诊断、疾病名、治疗方案作为事实写入。",
                    "不要创造用户没有表达的信息。",
                    "用户要求先给判断、别继续追问、根据现有信息判断时，intent.answer_now=true。",
                    "用户只是问是否严重、是否需要线下检查时，intent.wants_triage=true。",
                ],
                "schema": {
                    "facts": [
                        {
                            "key": "one item from allowed_fact_keys",
                            "value": "归一后的中文事实值",
                            "status": "confirmed|negative|unknown|uncertain|contradicted",
                            "confidence": 0.0,
                            "source_text": "用户原话片段",
                            "category": "patient_identity|time_course|systemic_status|intake_output|domain_specific|symptom_profile",
                        }
                    ],
                    "intent": {
                        "answer_now": False,
                        "wants_triage": False,
                        "correction": False,
                        "raw_intent": "简短中文说明",
                    },
                },
                "allowed_fact_keys": sorted(ALLOWED_FACT_KEYS),
                "pet_context_summary": pet_context_summary,
                "previous_state": previous_state or {},
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _extract_json(self, raw: str) -> dict[str, Any]:
        """从模型原始输出中提取 JSON 对象。

        :param raw: 模型原始输出。
        :return: 返回函数执行结果。
        """
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("semantic extraction output must be a JSON object")
        return data

    def _normalize_facts(self, items: list[SemanticFactItem]) -> list[SemanticFact]:
        """校验、过滤并归一化 LLM 输出事实。

        :param items: LLM 输出事实列表。
        :return: 返回函数执行结果。
        """
        facts: list[SemanticFact] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            key = item.key.strip()
            status = item.status.strip().lower()
            if key not in ALLOWED_FACT_KEYS or status not in ALLOWED_STATUSES:
                continue
            if item.confidence < self.settings.semantic_extraction_min_confidence:
                continue
            value = item.value.strip()[:160]
            if status == "negative" and not value:
                value = NEGATIVE_VALUE_LABELS.get(key, "用户明确否认相关异常")
            if status in {"unknown", "uncertain"} and not value:
                value = status
            if not value:
                continue
            dedupe_key = (key, status, value)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            facts.append(
                SemanticFact(
                    key=key,
                    value=value,
                    status=status,
                    confidence=float(item.confidence),
                    source_text=item.source_text.strip()[:160],
                    category=item.category.strip()[:80],
                )
            )
        return facts
