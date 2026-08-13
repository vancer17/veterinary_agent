"""
文件：src/vet_agent/clinical_safety/semantic_extractor.py
作用：通过 LiteLLM response_format 将临床安全相关输入抽取为可信结构化语义。
范围：位于宠物上下文加载之后、临床安全候选召回之前；本层只负责语义结构化，不生成候选、不执行裁决。
说明：抽取失败或低置信时仅返回显式降级状态，不使用关键词、正则或文件短语补造临床语义事实。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vet_agent import Settings
from vet_agent.runtime import QwenClient

from .fallback import ClinicalSafetySemanticFallbackState


ClinicalSafetySpecies = Literal["dog", "cat", "unknown"]
ClinicalSafetySex = Literal["male", "female", "unknown"]
ClinicalSafetyAgeGroup = Literal["juvenile", "adult", "senior", "unknown"]
ClinicalSafetyExposureState = Literal["confirmed", "possible", "denied", "unknown"]
ClinicalSafetySymptomState = Literal["present", "denied", "unknown"]
ClinicalSafetyTemporalState = Literal["current", "past", "unclear", "unknown"]
ClinicalSafetyTemporalScope = Literal["ongoing", "recent_past", "remote_past", "unclear"]
ClinicalSafetyResolutionState = Literal["ongoing", "resolved", "unknown"]
ClinicalSafetyIntentType = Literal["toxicity", "symptom", "prevention", "knowledge", "triage", "other"]
ClinicalSafetySemanticStrategy = Literal[
    "litellm_response_format",
    "litellm_response_format_low_confidence",
    "semantic_extraction_disabled",
    "semantic_extraction_unavailable",
    "semantic_extraction_failed",
    "semantic_extraction_invalid_schema",
    "skipped",
]


@dataclass(frozen=True)
class ClinicalSafetySemanticResult:
    """表示临床安全场景下抽取到的结构化语义结果。

    :param species: 物种归一结果。
    :param sex: 性别归一结果。
    :param age_group: 年龄阶段归一结果。
    :param age_text: 年龄原文片段。
    :param exposure_state: 暴露状态。
    :param symptom_state: 症状状态。
    :param temporal_state: 时间状态。
    :param temporal_scope: 时间范围，包括正在发生、近期既往、远期既往和不明确。
    :param resolution_state: 当前事件是否已经明确缓解或结束。
    :param temporal_text: 用户表达时间范围的原文片段。
    :param intent_type: 用户意图类型。
    :param high_risk_terms: LLM 从用户输入中抽取的正向高风险线索。
    :param negated_terms: LLM 从用户输入中抽取的明确否定线索。
    :param confidence: 整体置信度。
    :param strategy: 语义抽取策略或失败状态。
    :param fallback_reason: 抽取失败、不可用或低置信原因。
    :param source_text: 原始来源文本摘要。
    :return: 无返回值。
    """

    species: ClinicalSafetySpecies = "unknown"
    sex: ClinicalSafetySex = "unknown"
    age_group: ClinicalSafetyAgeGroup = "unknown"
    age_text: str = ""
    exposure_state: ClinicalSafetyExposureState = "unknown"
    symptom_state: ClinicalSafetySymptomState = "unknown"
    temporal_state: ClinicalSafetyTemporalState = "unknown"
    temporal_scope: ClinicalSafetyTemporalScope = "unclear"
    resolution_state: ClinicalSafetyResolutionState = "unknown"
    temporal_text: str = ""
    intent_type: ClinicalSafetyIntentType = "other"
    high_risk_terms: tuple[str, ...] = field(default_factory=tuple)
    negated_terms: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    strategy: ClinicalSafetySemanticStrategy = "skipped"
    fallback_reason: str | None = None
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典，供响应 metadata 和审计留痕使用。

        :return: 返回临床安全结构化语义字典。
        """
        return {
            "species": self.species,
            "sex": self.sex,
            "age_group": self.age_group,
            "age_text": self.age_text,
            "exposure_state": self.exposure_state,
            "symptom_state": self.symptom_state,
            "temporal_state": self.temporal_state,
            "temporal_scope": self.temporal_scope,
            "resolution_state": self.resolution_state,
            "temporal_text": self.temporal_text,
            "intent_type": self.intent_type,
            "high_risk_terms": list(self.high_risk_terms),
            "negated_terms": list(self.negated_terms),
            "confidence": self.confidence,
            "strategy": self.strategy,
            "fallback_reason": self.fallback_reason,
            "source_text": self.source_text,
        }

    def to_metadata(self) -> dict[str, Any]:
        """转换为运行时 metadata 使用的字典。

        :return: 返回结构化语义 metadata。
        """
        return {
            "agent": "ClinicalSafetySemanticExtractorAgent",
            **self.to_dict(),
            "fallback_state": self.to_fallback_state().to_dict(),
        }

    def to_fallback_state(self) -> ClinicalSafetySemanticFallbackState:
        """转换为结构化语义层的显式回退状态。

        :return: 返回结构化语义层回退状态。
        """
        return ClinicalSafetySemanticFallbackState.from_parts(
            strategy=self.strategy,
            confidence=self.confidence,
            fallback_reason=self.fallback_reason,
        )

    def is_low_confidence(self) -> bool:
        """判断当前结果是否来自低置信度 LLM 输出。

        :return: 低置信度时返回 True，否则返回 False。
        """
        return self.strategy == "litellm_response_format_low_confidence"

    def is_trusted(self) -> bool:
        """判断当前结果是否为可进入召回增强与裁决输入的可信结构化语义。

        :return: 结构化语义可被后续链路信任时返回 True。
        """
        return self.strategy == "litellm_response_format"

    def to_query_hints(self) -> str:
        """转换为用于向量召回增强的查询提示词。

        :return: 返回临床安全语义提示文本；语义不可信时返回空字符串。
        """
        if not self.is_trusted():
            return ""
        lines: list[str] = []
        if self.species != "unknown":
            lines.append(f"物种={self.species}")
        if self.sex != "unknown":
            lines.append(f"性别={self.sex}")
        if self.age_group != "unknown":
            lines.append(f"年龄阶段={self.age_group}")
        if self.age_text:
            lines.append(f"年龄原文={self.age_text}")
        if self.exposure_state != "unknown":
            lines.append(f"暴露状态={self.exposure_state}")
        if self.symptom_state != "unknown":
            lines.append(f"症状状态={self.symptom_state}")
        if self.temporal_state != "unknown":
            lines.append(f"时间状态={self.temporal_state}")
        if self.temporal_scope != "unclear":
            lines.append(f"时间范围={self.temporal_scope}")
        if self.resolution_state != "unknown":
            lines.append(f"恢复状态={self.resolution_state}")
        if self.intent_type != "other":
            lines.append(f"意图类型={self.intent_type}")
        if self.temporal_text:
            lines.append(f"时间原文={self.temporal_text}")
        if self.high_risk_terms:
            lines.append(f"高风险线索={'、'.join(self.high_risk_terms)}")
        return "\n".join(lines)


class ClinicalSafetySemanticItem(BaseModel):
    """定义 LiteLLM response_format 返回的临床安全结构化语义契约。"""

    model_config = ConfigDict(extra="forbid")

    species: ClinicalSafetySpecies = Field(description="物种归一结果，只能来自用户输入或可信宠物画像。")
    sex: ClinicalSafetySex = Field(description="性别归一结果，只能来自用户输入或可信宠物画像。")
    age_group: ClinicalSafetyAgeGroup = Field(description="年龄阶段归一结果，只能来自用户输入或可信宠物画像。")
    age_text: str = Field(description="年龄原文片段；没有明确年龄时返回空字符串。", max_length=40)
    exposure_state: ClinicalSafetyExposureState = Field(description="本轮是否存在用户明确表达的暴露状态。")
    symptom_state: ClinicalSafetySymptomState = Field(description="本轮是否存在用户明确表达的症状状态。")
    temporal_state: ClinicalSafetyTemporalState = Field(description="本轮事件时间状态。")
    temporal_scope: ClinicalSafetyTemporalScope = Field(description="本轮事件时间范围。")
    resolution_state: ClinicalSafetyResolutionState = Field(description="本轮事件是否已明确缓解或结束。")
    temporal_text: str = Field(description="时间范围或恢复状态的用户原文片段。", max_length=80)
    intent_type: ClinicalSafetyIntentType = Field(description="本轮临床安全相关意图类型。")
    high_risk_terms: list[str] = Field(description="用户明确表达的正向高风险线索。", max_length=12)
    negated_terms: list[str] = Field(description="用户明确否定的高风险线索。", max_length=12)
    confidence: float = Field(description="结构化抽取整体置信度。", ge=0.0, le=1.0)
    rationale: str = Field(description="简短说明抽取依据；仅用于审计，不进入裁决。", max_length=240)


class ClinicalSafetySemanticExtractorAgent:
    """通过 LiteLLM response_format 抽取临床安全结构化语义。"""

    def __init__(self, qwen: QwenClient | None, settings: Settings) -> None:
        """初始化临床安全语义抽取器。

        :param qwen: 通义千问兼容客户端。
        :param settings: 应用配置对象。
        :return: 无返回值。
        """
        self.qwen = qwen
        self.settings = settings

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        model: str,
    ) -> ClinicalSafetySemanticResult:
        """从临床安全相关输入中抽取可信结构化语义。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param model: 模型名称。
        :return: 返回结构化临床安全语义结果或显式失败状态。
        """
        source_text = self._clip_text(self._normalize_text(user_text), limit=240)
        if not self.settings.enable_llm_semantic_extraction:
            return self._empty_result(
                strategy="semantic_extraction_disabled",
                fallback_reason="llm_clinical_safety_semantic_extraction_disabled",
                source_text=source_text,
            )
        if self.qwen is None or not self.qwen.available:
            return self._empty_result(
                strategy="semantic_extraction_unavailable",
                fallback_reason="llm_clinical_safety_semantic_extraction_unavailable",
                source_text=source_text,
            )
        try:
            parsed = await self.qwen.chat_structured(
                self._messages(user_text, pet_context_summary),
                response_model=ClinicalSafetySemanticItem,
                model=model,
                temperature=0.0,
            )
        except ValidationError:
            return self._empty_result(
                strategy="semantic_extraction_invalid_schema",
                fallback_reason="llm_clinical_safety_semantic_invalid_schema",
                source_text=source_text,
            )
        except (RuntimeError, ValueError, TypeError, AttributeError):
            return self._empty_result(
                strategy="semantic_extraction_failed",
                fallback_reason="llm_clinical_safety_semantic_extraction_failed",
                source_text=source_text,
            )
        return self._normalize_result(parsed, source_text=source_text)

    def _messages(self, user_text: str, pet_context_summary: str) -> list[dict[str, str]]:
        """构造结构化抽取使用的 OpenAI 兼容消息列表。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :return: 返回传给 LiteLLM 的消息列表。
        """
        return [
            {
                "role": "system",
                "content": (
                    "你是兽医临床安全语义抽取器。"
                    "只抽取用户本轮输入与可信宠物画像中的结构化语义。"
                    "不要诊断，不要治疗建议，不要扩写病情。"
                    "无法确认的字段必须返回 unknown、unclear 或空集合。"
                ),
            },
            {
                "role": "user",
                "content": self._prompt(user_text, pet_context_summary),
            },
        ]

    def _prompt(self, user_text: str, pet_context_summary: str) -> str:
        """构造临床安全语义抽取提示词。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :return: 返回结构化提示词文本。
        """
        return json.dumps(
            {
                "task": "将用户输入归一为临床安全结构化语义。",
                "rules": [
                    "只根据用户本轮输入和可信宠物画像填写字段。",
                    "不要把知识库常识、治疗建议或诊断结论写入字段。",
                    "没有明确表达时，枚举字段必须返回 unknown 或 unclear。",
                    "exposure_state 只表示本轮是否明确表达暴露、可能暴露、否认暴露或未知。",
                    "symptom_state 只表示本轮是否明确表达症状存在、否认症状或未知。",
                    "temporal_state 和 temporal_scope 只表示用户明确表达的时间状态。",
                    "resolution_state 只表示用户是否明确表达事件已经缓解或仍在持续。",
                    "high_risk_terms 和 negated_terms 只保留用户原文中直接出现的短语。",
                    "confidence 必须反映字段整体可信度，不能因医学风险较高而人为提高。",
                ],
                "schema": {
                    "species": "dog|cat|unknown",
                    "sex": "male|female|unknown",
                    "age_group": "juvenile|adult|senior|unknown",
                    "age_text": "年龄原文片段或空字符串",
                    "exposure_state": "confirmed|possible|denied|unknown",
                    "symptom_state": "present|denied|unknown",
                    "temporal_state": "current|past|unclear|unknown",
                    "temporal_scope": "ongoing|recent_past|remote_past|unclear",
                    "resolution_state": "ongoing|resolved|unknown",
                    "temporal_text": "时间范围或恢复状态的用户原文片段",
                    "intent_type": "toxicity|symptom|prevention|knowledge|triage|other",
                    "high_risk_terms": ["用户原文中的短语"],
                    "negated_terms": ["用户原文中被明确否定的短语"],
                    "confidence": 0.0,
                    "rationale": "简短中文说明",
                },
                "pet_context_summary": pet_context_summary,
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _normalize_result(
        self,
        item: ClinicalSafetySemanticItem,
        *,
        source_text: str,
    ) -> ClinicalSafetySemanticResult:
        """将结构化 LLM 输出归一为稳定结果模型。

        :param item: 通过 Pydantic 校验的 LLM 输出对象。
        :param source_text: 用户输入摘要。
        :return: 返回结构化临床安全语义结果。
        """
        confidence = float(item.confidence)
        if confidence < self.settings.semantic_extraction_min_confidence:
            return self._empty_result(
                strategy="litellm_response_format_low_confidence",
                fallback_reason=f"semantic_llm_low_confidence:{confidence:.2f}",
                confidence=confidence,
                source_text=source_text,
            )
        return ClinicalSafetySemanticResult(
            species=item.species,
            sex=item.sex,
            age_group=item.age_group,
            age_text=self._clip_text(item.age_text, limit=40),
            exposure_state=item.exposure_state,
            symptom_state=item.symptom_state,
            temporal_state=item.temporal_state,
            temporal_scope=item.temporal_scope,
            resolution_state=item.resolution_state,
            temporal_text=self._clip_text(item.temporal_text, limit=80),
            intent_type=item.intent_type,
            high_risk_terms=self._normalize_terms(item.high_risk_terms),
            negated_terms=self._normalize_terms(item.negated_terms),
            confidence=confidence,
            strategy="litellm_response_format",
            source_text=source_text,
        )

    def _empty_result(
        self,
        *,
        strategy: ClinicalSafetySemanticStrategy,
        fallback_reason: str,
        source_text: str,
        confidence: float = 0.0,
    ) -> ClinicalSafetySemanticResult:
        """构造无可信语义的显式结果。

        :param strategy: 语义抽取策略或失败状态。
        :param fallback_reason: 失败、不可用或低置信原因。
        :param source_text: 用户输入摘要。
        :param confidence: 已知置信度；不可用时为 0。
        :return: 返回不包含补造临床事实的语义结果。
        """
        return ClinicalSafetySemanticResult(
            confidence=confidence,
            strategy=strategy,
            fallback_reason=fallback_reason,
            source_text=source_text,
        )

    def _normalize_terms(self, terms: list[str]) -> tuple[str, ...]:
        """规范化 LLM 返回的审计短语列表。

        :param terms: LLM 返回的短语列表。
        :return: 返回去空白、去重和截断后的短语元组。
        """
        normalized: list[str] = []
        for term in terms:
            value = self._clip_text(term, limit=40)
            if value and value not in normalized:
                normalized.append(value)
        return tuple(normalized[:12])

    def _normalize_text(self, text: str) -> str:
        """规范化来源文本，仅用于审计展示，不执行关键词推断。

        :param text: 原始文本。
        :return: 返回去除首尾空白并压缩空白字符后的文本。
        """
        return " ".join(text.strip().split())

    def _clip_text(self, text: str, *, limit: int) -> str:
        """截断审计文本，避免 metadata 过大。

        :param text: 待截断文本。
        :param limit: 最大字符数。
        :return: 返回截断后的文本。
        """
        return text.strip()[:limit]
