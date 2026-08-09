"""
文件：src/vet_agent/clinical_safety/semantic_extractor.py
作用：使用 LLM 从临床安全相关输入中抽取结构化语义信号。
说明：抽取结果仅用于召回增强与裁决前对齐，不直接生成诊断或处置建议。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

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
    :param high_risk_terms: 正向高风险线索。
    :param negated_terms: 被明确否定的线索。
    :param confidence: 整体置信度。
    :param strategy: 结果来源策略。
    :param fallback_reason: 回退原因。
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
    strategy: str = "rule_fallback"
    fallback_reason: str | None = None
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。

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
        return self.strategy == "llm_semantic_extractor_low_confidence"

    def to_query_hints(self) -> str:
        """转换为用于向量召回增强的查询提示词。

        :return: 返回临床安全语义提示文本。
        """
        if self.is_low_confidence():
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
    """定义 LLM 输出的临床安全结构化语义契约。"""

    species: ClinicalSafetySpecies = Field(default="unknown")
    sex: ClinicalSafetySex = Field(default="unknown")
    age_group: ClinicalSafetyAgeGroup = Field(default="unknown")
    age_text: str = Field(default="")
    exposure_state: ClinicalSafetyExposureState = Field(default="unknown")
    symptom_state: ClinicalSafetySymptomState = Field(default="unknown")
    temporal_state: ClinicalSafetyTemporalState = Field(default="unknown")
    temporal_scope: ClinicalSafetyTemporalScope = Field(default="unclear")
    resolution_state: ClinicalSafetyResolutionState = Field(default="unknown")
    temporal_text: str = Field(default="", max_length=80)
    intent_type: ClinicalSafetyIntentType = Field(default="other")
    high_risk_terms: list[str] = Field(default_factory=list, max_length=12)
    negated_terms: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="")


class ClinicalSafetySemanticExtractorAgent:
    """使用 LLM 抽取临床安全结构化语义，失败时由规则回退。"""

    _DOG_MARKERS: tuple[str, ...] = ("dog", "canine", "犬", "狗", "幼犬")
    _CAT_MARKERS: tuple[str, ...] = ("cat", "feline", "猫", "幼猫")
    _MALE_MARKERS: tuple[str, ...] = ("male", "雄", "公")
    _FEMALE_MARKERS: tuple[str, ...] = ("female", "雌", "母")
    _SENIOR_MARKERS: tuple[str, ...] = ("老年", "高龄", "senior", "中老年")
    _JUVENILE_MARKERS: tuple[str, ...] = ("幼年", "幼犬", "幼猫", "puppy", "kitten")
    _EXPOSURE_MARKERS: tuple[str, ...] = (
        "误食",
        "吃了",
        "吃到",
        "吞了",
        "吞下",
        "咽下",
        "舔了",
        "舔到",
        "喝了",
        "接触",
        "偷吃",
        "喂了",
        "摄入",
        "咬了",
        "可能吃",
        "疑似吃",
    )
    _EXPOSURE_DENIAL_MARKERS: tuple[str, ...] = (
        "没有给",
        "没给",
        "未给",
        "没有吃",
        "没吃",
        "未吃",
        "没有误食",
        "没误食",
        "没有吞",
        "没吞",
        "没有碰",
        "没碰",
        "没有接触",
        "没接触",
    )
    _SYMPTOM_MARKERS: tuple[str, ...] = (
        "呕吐",
        "腹泻",
        "拉稀",
        "软便",
        "血便",
        "便血",
        "黑便",
        "抽搐",
        "昏迷",
        "瘫倒",
        "休克",
        "发绀",
        "发紫",
        "呼吸困难",
        "张口呼吸",
        "尿不出",
        "尿少",
        "尿频",
        "精神差",
        "没精神",
        "嗜睡",
    )
    _HIGH_RISK_MARKERS: tuple[str, ...] = _EXPOSURE_MARKERS + _SYMPTOM_MARKERS + (
        "泰诺",
        "对乙酰氨基酚",
        "布洛芬",
        "萘普生",
        "人药",
        "退烧药",
        "止疼药",
    )
    _ONGOING_TEMPORAL_MARKERS: tuple[str, ...] = (
        "正在",
        "目前",
        "现在",
        "此刻",
        "持续",
        "一直",
        "仍然",
        "还在",
        "反复",
    )
    _RECENT_TEMPORAL_MARKERS: tuple[str, ...] = (
        "刚刚",
        "刚才",
        "今天",
        "今早",
        "今晚",
        "昨晚",
        "昨天",
        "前天",
        "最近",
        "这几天",
        "这两天",
        "几小时前",
        "一天内",
    )
    _REMOTE_TEMPORAL_MARKERS: tuple[str, ...] = (
        "以前",
        "之前",
        "曾经",
        "既往",
        "过去",
        "几个月前",
        "去年",
        "多年前",
        "很久以前",
    )
    _RESOLVED_MARKERS: tuple[str, ...] = (
        "已经好了",
        "已经恢复",
        "已恢复",
        "恢复了",
        "现在没事",
        "目前正常",
        "症状消失",
        "不再",
        "没有了",
    )

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
        """从临床安全相关输入中抽取结构化语义。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param model: 模型名称。
        :return: 返回结构化临床安全语义结果。
        """
        if not self._llm_enabled():
            return self._rule_based_result(
                user_text=user_text,
                pet_context_summary=pet_context_summary,
                fallback_reason="llm_clinical_safety_semantic_extraction_disabled",
            )
        try:
            raw = await self.qwen.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是兽医临床安全语义抽取器。"
                            "只抽取结构化语义，不要诊断，不要治疗建议，不要扩写病情。"
                            "只输出 JSON。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(user_text, pet_context_summary),
                    },
                ],
                model=model,
                temperature=0.0,
            )
            parsed = ClinicalSafetySemanticItem.model_validate(self._extract_json(raw))
            return self._normalize_result(parsed, user_text, pet_context_summary)
        except (ValidationError, ValueError, json.JSONDecodeError, RuntimeError):
            return self._rule_based_result(
                user_text=user_text,
                pet_context_summary=pet_context_summary,
                fallback_reason="llm_clinical_safety_semantic_extraction_failed",
            )

    def _llm_enabled(self) -> bool:
        """判断 LLM 语义抽取是否可用。

        :return: LLM 可用时返回 True。
        """
        return bool(self.settings.enable_llm_semantic_extraction and self.qwen is not None and self.qwen.available)

    def _prompt(self, user_text: str, pet_context_summary: str) -> str:
        """构造临床安全语义抽取提示词。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :return: 返回结构化提示词文本。
        """
        return json.dumps(
            {
                "task": "将用户输入归一为临床安全结构化语义，只输出 JSON。",
                "rules": [
                    "species 只能是 dog、cat、unknown。",
                    "sex 只能是 male、female、unknown。",
                    "age_group 只能是 juvenile、adult、senior、unknown。",
                    "exposure_state 只能是 confirmed、possible、denied、unknown。",
                    "symptom_state 只能是 present、denied、unknown。",
                    "temporal_state 只能是 current、past、unclear、unknown。",
                    "temporal_scope 只能是 ongoing、recent_past、remote_past、unclear。",
                    "resolution_state 只能是 ongoing、resolved、unknown。",
                    "intent_type 只能是 toxicity、symptom、prevention、knowledge、triage、other。",
                    "high_risk_terms 只保留和当前安全判断直接相关的短语。",
                    "不要把诊断、治疗和处置建议写入字段。",
                    "如果用户明确说没有给、没吃、未接触，则 exposure_state 应为 denied。",
                    "如果用户明确说误食、吃了、吞了、舔到，则 exposure_state 应为 confirmed 或 possible。",
                    "正在、目前、持续、仍在发生表示 temporal_scope=ongoing。",
                    "刚刚、今天、昨天、最近、这几天表示 temporal_scope=recent_past。",
                    "以前、既往、曾经、几个月前、去年表示 temporal_scope=remote_past。",
                    "已经恢复、已经好了、现在没事、症状消失表示 resolution_state=resolved。",
                    "只根据用户明确表达判断时间，不要根据知识库或宠物画像推断事件发生时间。",
                    "如果用户在问能不能吃、安全吗、会不会中毒，intent_type 优先标为 knowledge。",
                    "如果用户在问先给判断、别问了、直接回答，intent_type 优先标为 triage。",
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
                    "high_risk_terms": ["短语", "短语"],
                    "negated_terms": ["被否定的短语"],
                    "confidence": 0.0,
                    "rationale": "简短中文说明",
                },
                "pet_context_summary": pet_context_summary,
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _extract_json(self, raw: str) -> dict[str, Any]:
        """从模型原始输出中提取 JSON 对象。

        :param raw: 模型原始输出。
        :return: 返回解析后的 JSON 对象。
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
            raise ValueError("clinical safety semantic output must be a JSON object")
        return data

    def _normalize_result(
        self,
        item: ClinicalSafetySemanticItem,
        user_text: str,
        pet_context_summary: str,
    ) -> ClinicalSafetySemanticResult:
        """将 LLM 输出与规则回退结果归一为稳定结构。

        :param item: LLM 输出对象。
        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :return: 返回结构化临床安全语义结果。
        """
        fallback = self._rule_based_result(user_text=user_text, pet_context_summary=pet_context_summary)
        confidence = float(item.confidence)
        strategy = "llm_semantic_extractor"
        fallback_reason = None
        if confidence < self.settings.semantic_extraction_min_confidence:
            strategy = "llm_semantic_extractor_low_confidence"
            fallback_reason = f"semantic_llm_low_confidence:{confidence:.2f}"
            return ClinicalSafetySemanticResult(
                species=fallback.species,
                sex=fallback.sex,
                age_group=fallback.age_group,
                age_text=fallback.age_text,
                exposure_state=fallback.exposure_state,
                symptom_state=fallback.symptom_state,
                temporal_state=fallback.temporal_state,
                temporal_scope=fallback.temporal_scope,
                resolution_state=fallback.resolution_state,
                temporal_text=fallback.temporal_text,
                intent_type=fallback.intent_type,
                high_risk_terms=fallback.high_risk_terms,
                negated_terms=fallback.negated_terms,
                confidence=confidence,
                strategy=strategy,
                fallback_reason=fallback_reason,
                source_text=self._normalize_text(user_text)[:240],
            )
        return ClinicalSafetySemanticResult(
            species=item.species if item.species != "unknown" else fallback.species,
            sex=item.sex if item.sex != "unknown" else fallback.sex,
            age_group=item.age_group if item.age_group != "unknown" else fallback.age_group,
            age_text=item.age_text.strip()[:40] or fallback.age_text,
            exposure_state=item.exposure_state if item.exposure_state != "unknown" else fallback.exposure_state,
            symptom_state=item.symptom_state if item.symptom_state != "unknown" else fallback.symptom_state,
            temporal_state=item.temporal_state if item.temporal_state != "unknown" else fallback.temporal_state,
            temporal_scope=item.temporal_scope if item.temporal_scope != "unclear" else fallback.temporal_scope,
            resolution_state=(
                item.resolution_state
                if item.resolution_state != "unknown"
                else fallback.resolution_state
            ),
            temporal_text=item.temporal_text.strip()[:80] or fallback.temporal_text,
            intent_type=item.intent_type if item.intent_type != "other" else fallback.intent_type,
            high_risk_terms=self._normalize_terms(item.high_risk_terms) or fallback.high_risk_terms,
            negated_terms=self._normalize_terms(item.negated_terms) or fallback.negated_terms,
            confidence=confidence,
            strategy=strategy,
            source_text=self._normalize_text(user_text)[:240],
            fallback_reason=fallback_reason,
        )

    def _rule_based_result(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        fallback_reason: str = "",
    ) -> ClinicalSafetySemanticResult:
        """使用规则生成保守的临床安全语义结果。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param fallback_reason: 回退原因。
        :return: 返回结构化临床安全语义结果。
        """
        combined_text = f"{user_text}\n{pet_context_summary}"
        species = self._infer_species(combined_text)
        sex = self._infer_sex(combined_text)
        age_text = self._extract_age_text(combined_text)
        age_group = self._infer_age_group(combined_text, age_text)
        exposure_state = self._infer_exposure_state(combined_text)
        symptom_state = self._infer_symptom_state(combined_text)
        temporal_state = self._infer_temporal_state(combined_text)
        temporal_scope = self._infer_temporal_scope(combined_text)
        resolution_state = self._infer_resolution_state(combined_text)
        temporal_text = self._extract_temporal_text(combined_text)
        intent_type = self._infer_intent_type(combined_text, exposure_state=exposure_state)
        high_risk_terms = self._extract_high_risk_terms(combined_text)
        negated_terms = self._extract_negated_terms(combined_text)
        confidence = self._estimate_confidence(
            species=species,
            sex=sex,
            age_group=age_group,
            age_text=age_text,
            exposure_state=exposure_state,
            symptom_state=symptom_state,
            temporal_state=temporal_state,
            temporal_scope=temporal_scope,
            resolution_state=resolution_state,
            temporal_text=temporal_text,
            intent_type=intent_type,
            high_risk_terms=high_risk_terms,
            negated_terms=negated_terms,
        )
        return ClinicalSafetySemanticResult(
            species=species,
            sex=sex,
            age_group=age_group,
            age_text=age_text,
            exposure_state=exposure_state,
            symptom_state=symptom_state,
            temporal_state=temporal_state,
            intent_type=intent_type,
            high_risk_terms=high_risk_terms,
            negated_terms=negated_terms,
            confidence=confidence,
            strategy="rule_fallback",
            fallback_reason=fallback_reason,
            source_text=self._normalize_text(user_text)[:240],
        )

    def _infer_species(self, text: str) -> ClinicalSafetySpecies:
        """从文本中推断物种。

        :param text: 待处理文本。
        :return: 返回物种归一结果。
        """
        normalized = self._normalize_text(text)
        dog_seen = any(marker in normalized for marker in self._DOG_MARKERS)
        cat_seen = any(marker in normalized for marker in self._CAT_MARKERS)
        if dog_seen and not cat_seen:
            return "dog"
        if cat_seen and not dog_seen:
            return "cat"
        return "unknown"

    def _infer_sex(self, text: str) -> ClinicalSafetySex:
        """从文本中推断性别。

        :param text: 待处理文本。
        :return: 返回性别归一结果。
        """
        normalized = self._normalize_text(text)
        male_seen = any(marker in normalized for marker in self._MALE_MARKERS)
        female_seen = any(marker in normalized for marker in self._FEMALE_MARKERS)
        if male_seen and not female_seen:
            return "male"
        if female_seen and not male_seen:
            return "female"
        return "unknown"

    def _infer_age_group(self, text: str, age_text: str) -> ClinicalSafetyAgeGroup:
        """从文本和年龄片段中推断年龄阶段。

        :param text: 待处理文本。
        :param age_text: 年龄原文片段。
        :return: 返回年龄阶段归一结果。
        """
        normalized = self._normalize_text(f"{text}\n{age_text}")
        if any(marker in normalized for marker in self._SENIOR_MARKERS):
            return "senior"
        if any(marker in normalized for marker in self._JUVENILE_MARKERS):
            return "juvenile"
        match = re.search(r"(\d+(?:\.\d+)?)\s*(岁|年)", normalized)
        if match and float(match.group(1)) >= 7:
            return "senior"
        match = re.search(r"(\d+(?:\.\d+)?)\s*(个月|月)", normalized)
        if match and float(match.group(1)) <= 6:
            return "juvenile"
        return "adult" if age_text else "unknown"

    def _infer_exposure_state(self, text: str) -> ClinicalSafetyExposureState:
        """从文本中推断是否存在实际或可能暴露。

        :param text: 待处理文本。
        :return: 返回暴露状态。
        """
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in self._EXPOSURE_DENIAL_MARKERS):
            return "denied"
        if any(marker in normalized for marker in self._EXPOSURE_MARKERS):
            if any(marker in normalized for marker in ("可能", "疑似", "好像", "似乎")):
                return "possible"
            return "confirmed"
        return "unknown"

    def _infer_symptom_state(self, text: str) -> ClinicalSafetySymptomState:
        """从文本中推断症状是否正在发生。

        :param text: 待处理文本。
        :return: 返回症状状态。
        """
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in self._SYMPTOM_MARKERS):
            if any(
                f"{negation}{marker}" in normalized
                for negation in ("没有", "没", "未", "不")
                for marker in self._SYMPTOM_MARKERS
            ):
                return "denied"
            return "present"
        return "unknown"

    def _infer_temporal_state(self, text: str) -> ClinicalSafetyTemporalState:
        """从文本中推断症状时间属性。

        :param text: 待处理文本。
        :return: 返回时间状态。
        """
        scope = self._infer_temporal_scope(text)
        if scope in {"ongoing", "recent_past"}:
            return "current"
        if scope == "remote_past":
            return "past"
        return "unclear"

    def _infer_temporal_scope(self, text: str) -> ClinicalSafetyTemporalScope:
        """从文本中推断事件发生的时间范围。

        :param text: 待处理文本。
        :return: 返回正在发生、近期既往、远期既往或不明确的时间范围。
        """
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in self._ONGOING_TEMPORAL_MARKERS):
            return "ongoing"
        if any(marker in normalized for marker in self._REMOTE_TEMPORAL_MARKERS):
            return "remote_past"
        if any(marker in normalized for marker in self._RECENT_TEMPORAL_MARKERS):
            return "recent_past"
        return "unclear"

    def _infer_resolution_state(self, text: str) -> ClinicalSafetyResolutionState:
        """从文本中推断事件是否已经缓解或结束。

        :param text: 待处理文本。
        :return: 明确恢复时返回 resolved，明确持续时返回 ongoing，否则返回 unknown。
        """
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in self._RESOLVED_MARKERS):
            return "resolved"
        if any(marker in normalized for marker in self._ONGOING_TEMPORAL_MARKERS):
            return "ongoing"
        return "unknown"

    def _extract_temporal_text(self, text: str) -> str:
        """提取用于审计和回答上下文的时间原文片段。

        :param text: 待处理文本。
        :return: 返回首个命中的时间或恢复状态片段，未命中时返回空字符串。
        """
        normalized = self._normalize_text(text)
        markers = (
            *self._ONGOING_TEMPORAL_MARKERS,
            *self._RECENT_TEMPORAL_MARKERS,
            *self._REMOTE_TEMPORAL_MARKERS,
            *self._RESOLVED_MARKERS,
        )
        for marker in markers:
            if marker in normalized:
                return marker
        return ""

    def _infer_intent_type(self, text: str, *, exposure_state: ClinicalSafetyExposureState) -> ClinicalSafetyIntentType:
        """从文本中推断临床安全意图类型。

        :param text: 待处理文本。
        :param exposure_state: 暴露状态。
        :return: 返回意图类型。
        """
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in ("怎么预防", "如何预防", "以后避免", "防止", "预防")):
            return "prevention"
        if any(marker in normalized for marker in ("别问", "不要追问", "直接回答", "先给判断", "先告诉我", "给个判断")):
            return "triage"
        if any(marker in normalized for marker in ("能不能", "安全吗", "会不会", "会中毒吗", "有没有毒", "是什么")):
            return "knowledge"
        if exposure_state in {"confirmed", "possible"}:
            return "toxicity"
        if any(marker in normalized for marker in self._SYMPTOM_MARKERS):
            return "symptom"
        return "other"

    def _extract_high_risk_terms(self, text: str) -> tuple[str, ...]:
        """提取和安全裁决相关的高风险线索。

        :param text: 待处理文本。
        :return: 返回去重后的高风险短语。
        """
        normalized = self._normalize_text(text)
        matches = [term for term in self._HIGH_RISK_MARKERS if term and term in normalized]
        return self._normalize_terms(matches)

    def _extract_negated_terms(self, text: str) -> tuple[str, ...]:
        """提取被明确否定的高风险线索。

        :param text: 待处理文本。
        :return: 返回去重后的否定短语。
        """
        normalized = self._normalize_text(text)
        matches: list[str] = []
        for term in self._HIGH_RISK_MARKERS:
            if not term:
                continue
            if self._term_is_negated(normalized, term):
                matches.append(term)
        return self._normalize_terms(matches)

    def _term_is_negated(self, normalized_text: str, term: str) -> bool:
        """判断某个短语是否被否定表达修饰。

        :param normalized_text: 规范化后的全文。
        :param term: 待判断短语。
        :return: 被否定修饰时返回 True。
        """
        index = normalized_text.find(term)
        while index >= 0:
            prefix_window = normalized_text[max(0, index - 6) : index]
            if any(marker in prefix_window for marker in ("没有", "不是", "并非", "否认", "未见", "不见")):
                return True
            if any(
                prefix_window.endswith(prefix)
                or prefix_window.endswith(f"{prefix}明显")
                or prefix_window.endswith(f"{prefix}完全")
                for prefix in ("没", "无", "未")
            ):
                return True
            index = normalized_text.find(term, index + len(term))
        return False

    def _extract_age_text(self, text: str) -> str:
        """提取年龄原文片段。

        :param text: 待处理文本。
        :return: 返回年龄片段，未命中则返回空字符串。
        """
        normalized = self._normalize_text(text)
        match = re.search(r"(?:\d+(?:\.\d+)?\s*(?:岁|年|个月|月))|(?:[一二两三四五六七八九十]+个?多?月)", normalized)
        return match.group(0) if match else ""

    def _normalize_terms(self, terms: list[str]) -> tuple[str, ...]:
        """归一并去重短语列表。

        :param terms: 原始短语列表。
        :return: 返回去重后的短语元组。
        """
        normalized_terms = [term.strip() for term in terms if term and term.strip()]
        return tuple(dict.fromkeys(normalized_terms))

    def _estimate_confidence(
        self,
        *,
        species: ClinicalSafetySpecies,
        sex: ClinicalSafetySex,
        age_group: ClinicalSafetyAgeGroup,
        age_text: str,
        exposure_state: ClinicalSafetyExposureState,
        symptom_state: ClinicalSafetySymptomState,
        temporal_state: ClinicalSafetyTemporalState,
        temporal_scope: ClinicalSafetyTemporalScope,
        resolution_state: ClinicalSafetyResolutionState,
        temporal_text: str,
        intent_type: ClinicalSafetyIntentType,
        high_risk_terms: tuple[str, ...],
        negated_terms: tuple[str, ...],
    ) -> float:
        """根据抽取到的字段估算回退结果置信度。

        :param species: 物种归一结果。
        :param sex: 性别归一结果。
        :param age_group: 年龄阶段归一结果。
        :param age_text: 年龄原文片段。
        :param exposure_state: 暴露状态。
        :param symptom_state: 症状状态。
        :param temporal_state: 时间状态。
        :param temporal_scope: 时间范围。
        :param resolution_state: 恢复状态。
        :param temporal_text: 时间原文片段。
        :param intent_type: 意图类型。
        :param high_risk_terms: 正向高风险线索。
        :param negated_terms: 被明确否定的线索。
        :return: 返回估算后的置信度。
        """
        score = 0.18
        if species != "unknown":
            score += 0.08
        if sex != "unknown":
            score += 0.05
        if age_group != "unknown" or age_text:
            score += 0.08
        if exposure_state != "unknown":
            score += 0.18
        if symptom_state != "unknown":
            score += 0.1
        if temporal_state != "unclear":
            score += 0.05
        if temporal_scope != "unclear":
            score += 0.04
        if resolution_state != "unknown":
            score += 0.03
        if temporal_text:
            score += 0.02
        if intent_type != "other":
            score += 0.08
        score += min(0.12, 0.03 * len(high_risk_terms))
        score -= min(0.06, 0.02 * len(negated_terms))
        return max(0.0, min(0.95, round(score, 3)))

    def _normalize_text(self, text: str) -> str:
        """规范化参与语义抽取的文本。

        :param text: 原始文本。
        :return: 返回小写且移除空白后的文本。
        """
        return re.sub(r"\s+", "", text.lower())
