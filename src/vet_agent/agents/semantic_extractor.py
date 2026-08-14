"""
=============================================================================
文件：src/vet_agent/agents/semantic_extractor.py
作用：使用 LiteLLM response_format 将用户本轮输入归一为结构化问诊事实、开放观察与意图信号。
范围：位于多任务拆分之后、问诊状态合并之前；本层只负责语义结构化，
      不更新问诊状态、不生成追问、不执行长期事实治理和安全动作裁决。
说明：抽取失败、低置信、禁用或不可用时仅返回显式状态，不使用关键词、
      正则、静态 seed 或手写 JSON 修复路径补造问诊事实；核心槽位用于
      兼容问诊状态视图，开放观察用于保留枚举外事实。
=============================================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vet_agent import Settings
from vet_agent.runtime import QwenClient


class ConsultationFactKey(StrEnum):
    """定义问诊语义抽取允许输出的事实类型。

    说明：枚举只作为结构化模型输出和问诊状态存储的数据契约，
    不承载关键词、正则、追问动作或长期事实写入策略。

    :return: 无返回值。
    """

    SPECIES = "species"
    LIFE_STAGE_OR_AGE = "life_stage_or_age"
    WEIGHT = "weight"
    ONSET = "onset"
    MENTAL_STATUS = "mental_status"
    APPETITE = "appetite"
    VOMITING = "vomiting"
    STOOL = "stool"
    BREATHING = "breathing"
    PAIN_OR_MOBILITY = "pain_or_mobility"
    BEHAVIOR_CONTEXT = "behavior_context"
    CURRENT_FOOD = "current_food"
    SYMPTOM_DETAIL = "symptom_detail"


class ConsultationFactStatus(StrEnum):
    """定义问诊事实的结构化确认状态。

    说明：状态由结构化语义抽取输出，问诊状态层仅按该状态执行
    当前会话范围内的确定性合并，不重新解析用户原文。

    :return: 无返回值。
    """

    CONFIRMED = "confirmed"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"
    UNCERTAIN = "uncertain"
    CONTRADICTED = "contradicted"


class ConsultationFactCategory(StrEnum):
    """定义问诊事实所属的证据画像维度。

    说明：分类仅用于构建回答充分性需要的结构化证据画像，
    不直接决定是否回答、追问或升级。

    :return: 无返回值。
    """

    PATIENT_IDENTITY = "patient_identity"
    TIME_COURSE = "time_course"
    SYSTEMIC_STATUS = "systemic_status"
    INTAKE_OUTPUT = "intake_output"
    DOMAIN_SPECIFIC = "domain_specific"
    SYMPTOM_PROFILE = "symptom_profile"
    OTHER = "other"


ConsultationSemanticStrategy = Literal[
    "litellm_response_format",
    "litellm_response_format_low_confidence",
    "semantic_extraction_disabled",
    "semantic_extraction_unavailable",
    "semantic_extraction_failed",
    "semantic_extraction_invalid_schema",
    "skipped",
]


@dataclass(frozen=True)
class SemanticFact:
    """表示本轮问诊语义抽取产生的单条结构化事实。

    :param key: 问诊事实类型。
    :param value: 用户明确表达并归一后的事实值。
    :param status: 事实确认状态。
    :param confidence: 单条事实置信度。
    :param source_text: 用户原文来源片段。
    :param category: 事实所属证据画像维度。
    :param metadata: 附加审计信息；不得承载长期事实写入策略。
    :return: 无返回值。
    """

    key: ConsultationFactKey
    value: str
    status: ConsultationFactStatus
    confidence: float
    source_text: str = ""
    category: ConsultationFactCategory = ConsultationFactCategory.OTHER
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典，供问诊状态和响应 metadata 使用。

        :return: 返回结构化问诊事实字典。
        """
        return {
            "key": self.key.value,
            "value": self.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "category": self.category.value,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SemanticObservation:
    """表示用户本轮输入中无法归入核心槽位的开放式结构化观察。

    说明：开放观察只进入当前会话工作记忆和证据画像，不直接驱动固定槽位
    状态机，不直接写入长期记忆事实。

    :param category: 观察所属的宽泛业务类别。
    :param label: 面向运维和提示词使用的中文观察标签。
    :param value: 用户明确表达并归一后的观察值。
    :param status: 观察确认状态。
    :param confidence: 单条观察置信度。
    :param source_text: 用户原文来源片段。
    :param temporal_text: 用户明确表达的时间范围原文；没有时为空字符串。
    :param metadata: 附加审计信息；不得承载长期事实写入策略。
    :return: 无返回值。
    """

    category: str
    label: str
    value: str
    status: ConsultationFactStatus
    confidence: float
    source_text: str = ""
    temporal_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典，供问诊状态和响应 metadata 使用。

        :return: 返回结构化开放观察字典。
        """
        return {
            "category": self.category,
            "label": self.label,
            "value": self.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "temporal_text": self.temporal_text,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SemanticIntent:
    """表示用户本轮输入中的问诊控制意图信号。

    说明：意图只作为回答充分性策略输入，不直接表示系统必须执行的业务动作。

    :param answer_now: 用户是否明确要求根据现有信息先答。
    :param wants_triage: 用户是否明确希望获得紧急度或分诊判断。
    :param correction: 用户是否表达正在纠正当前问诊事实。
    :param raw_intent: 用户意图简短摘要。
    :return: 无返回值。
    """

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典，供问诊状态和响应 metadata 使用。

        :return: 返回结构化意图字典。
        """
        return {
            "answer_now": self.answer_now,
            "wants_triage": self.wants_triage,
            "correction": self.correction,
            "raw_intent": self.raw_intent,
        }


@dataclass(frozen=True)
class SemanticExtractionResult:
    """表示问诊语义抽取阶段的结构化结果或显式失败状态。

    :param facts: 通过结构化契约校验且达到可信阈值的核心问诊事实。
    :param observations: 通过结构化契约校验且达到可信阈值的开放式观察。
    :param intent: 通过结构化契约校验且达到可信阈值的用户意图信号。
    :param strategy: 语义抽取策略或失败状态。
    :param fallback_reason: 禁用、不可用、失败、低置信或 schema 非法原因。
    :param confidence: 本轮语义抽取整体置信度。
    :param source_text: 用户输入摘要。
    :return: 无返回值。
    """

    facts: list[SemanticFact]
    observations: list[SemanticObservation] = field(default_factory=list)
    intent: SemanticIntent = field(default_factory=SemanticIntent)
    strategy: ConsultationSemanticStrategy = "skipped"
    fallback_reason: str | None = None
    confidence: float = 0.0
    source_text: str = ""

    def is_trusted(self) -> bool:
        """判断当前结果是否可进入问诊状态合并。

        :return: 结果可信时返回 True，否则返回 False。
        """
        return self.strategy == "litellm_response_format"

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 和状态持久化使用的字典。

        :return: 返回问诊语义抽取 metadata。
        """
        return {
            "agent": "ConsultationSemanticExtractorAgent",
            "strategy": self.strategy,
            "fallback_reason": self.fallback_reason,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "trusted": self.is_trusted(),
            "intent": self.intent.to_dict(),
            "facts": [fact.to_dict() for fact in self.facts],
            "observations": [observation.to_dict() for observation in self.observations],
        }


class SemanticFactItem(BaseModel):
    """定义 LiteLLM response_format 返回的问诊事实条目契约。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    key: ConsultationFactKey = Field(description="问诊事实类型，只能来自 ConsultationFactKey。")
    value: str = Field(description="用户明确表达并归一后的中文事实值。", max_length=160)
    status: ConsultationFactStatus = Field(description="事实确认状态。")
    confidence: float = Field(description="单条事实置信度。", ge=0.0, le=1.0)
    source_text: str = Field(
        description="用户原文来源片段；没有明确片段时返回空字符串。",
        max_length=160,
    )
    category: ConsultationFactCategory = Field(description="事实所属证据画像维度。")


class SemanticIntentItem(BaseModel):
    """定义 LiteLLM response_format 返回的问诊意图契约。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = Field(default="", max_length=120)


class SemanticObservationItem(BaseModel):
    """定义 LiteLLM response_format 返回的开放式结构化观察契约。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    category: str = Field(
        description="观察所属宽泛业务类别，使用稳定英文 snake_case；不得表示业务动作。",
        min_length=1,
        max_length=48,
    )
    label: str = Field(description="面向运维和提示词使用的中文观察标签。", min_length=1, max_length=48)
    value: str = Field(description="用户明确表达并归一后的中文观察值。", min_length=1, max_length=180)
    status: ConsultationFactStatus = Field(description="观察确认状态。")
    confidence: float = Field(description="单条观察置信度。", ge=0.0, le=1.0)
    source_text: str = Field(
        description="用户原文来源片段；没有明确片段时返回空字符串。",
        max_length=160,
    )
    temporal_text: str = Field(
        default="",
        description="用户明确表达的时间范围原文；没有明确时间时返回空字符串。",
        max_length=80,
    )


class SemanticExtractorOutput(BaseModel):
    """定义问诊语义抽取的完整结构化输出契约。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    facts: list[SemanticFactItem] = Field(default_factory=list, max_length=16)
    observations: list[SemanticObservationItem] = Field(default_factory=list, max_length=16)
    intent: SemanticIntentItem = Field(default_factory=SemanticIntentItem)
    confidence: float = Field(description="本轮结构化问诊语义抽取整体置信度。", ge=0.0, le=1.0)
    rationale: str = Field(
        description="简短说明抽取依据，仅用于审计，不进入状态合并。",
        max_length=240,
    )


class ConsultationSemanticExtractorAgent:
    """使用 LiteLLM response_format 抽取结构化问诊事实。

    :return: 无返回值。
    """

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
        :return: 返回结构化问诊语义结果或显式失败状态。
        """
        source_text = self._clip_text(self._normalize_text(user_text), limit=240)
        if not self.settings.enable_llm_semantic_extraction:
            return self._empty_result(
                strategy="semantic_extraction_disabled",
                fallback_reason="llm_consultation_semantic_extraction_disabled",
                source_text=source_text,
            )
        if self.qwen is None or not self.qwen.available:
            return self._empty_result(
                strategy="semantic_extraction_unavailable",
                fallback_reason="llm_consultation_semantic_extraction_unavailable",
                source_text=source_text,
            )
        try:
            parsed = await self.qwen.chat_structured(
                self._messages(user_text, pet_context_summary, previous_state),
                response_model=SemanticExtractorOutput,
                model=model,
                temperature=0.0,
            )
        except ValidationError:
            return self._empty_result(
                strategy="semantic_extraction_invalid_schema",
                fallback_reason="llm_consultation_semantic_invalid_schema",
                source_text=source_text,
            )
        except (RuntimeError, ValueError, TypeError, AttributeError):
            return self._empty_result(
                strategy="semantic_extraction_failed",
                fallback_reason="llm_consultation_semantic_extraction_failed",
                source_text=source_text,
            )
        return self._normalize_result(parsed, source_text=source_text)

    def _messages(
        self,
        user_text: str,
        pet_context_summary: str,
        previous_state: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        """构造结构化抽取使用的 OpenAI 兼容消息列表。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param previous_state: 上一轮问诊状态。
        :return: 返回传给 LiteLLM 的消息列表。
        """
        return [
            {
                "role": "system",
                "content": (
                    "你是兽医问诊语义抽取器。"
                    "只抽取用户本轮输入和可信宠物画像中明确表达的问诊事实、开放观察与意图信号。"
                    "不要诊断，不要给治疗建议，不要生成追问，不要决定是否回答。"
                    "无法确认的字段必须使用 unknown、uncertain 或空集合表达。"
                ),
            },
            {
                "role": "user",
                "content": self._prompt(user_text, pet_context_summary, previous_state),
            },
        ]

    def _prompt(
        self,
        user_text: str,
        pet_context_summary: str,
        previous_state: dict[str, Any] | None,
    ) -> str:
        """构造问诊语义抽取提示词。

        :param user_text: 用户本轮输入。
        :param pet_context_summary: 宠物上下文摘要。
        :param previous_state: 上一轮问诊状态；仅用于理解当前会话范围内的纠正和补充。
        :return: 返回结构化提示词文本。
        """
        return json.dumps(
            {
                "task": "将用户本轮输入归一为结构化问诊事实、开放观察与意图信号。",
                "rules": [
                    "只根据用户本轮输入、可信宠物画像和上一轮问诊状态填写字段。",
                    "facts[].key 必须来自 consultation_fact_keys。",
                    "facts 只填写可落入核心槽位的事实，不能为了覆盖所有描述而创造新的 key。",
                    (
                        "observations 用于保留无法自然归入核心槽位、但用户明确表达的观察；"
                        "不得把 observations 作为追问动作、诊断结论或长期记忆写入命令。"
                    ),
                    "facts[].status 只能是 confirmed、negative、unknown、uncertain、contradicted。",
                    "observations[].status 只能是 confirmed、negative、unknown、uncertain、contradicted。",
                    (
                        "confirmed 表示用户明确确认；negative 表示用户明确否认；"
                        "unknown 表示用户不知道；uncertain 表示用户表达不确定。"
                    ),
                    "不要把诊断、疾病名、治疗方案、用药方案或安全动作写入 facts。",
                    "不要创造用户没有表达的信息，不要从常识推断缺失事实。",
                    (
                        "用户明确要求先判断、别继续追问、根据现有信息回答时，"
                        "intent.answer_now=true。"
                    ),
                    (
                        "用户询问严重程度、是否急诊、是否需要线下检查时，"
                        "intent.wants_triage=true。"
                    ),
                    (
                        "用户明确更正此前事实时，intent.correction=true；"
                        "该字段不是长期资料更新命令。"
                    ),
                    (
                        "confidence 表示本轮事实和意图整体可信度，"
                        "不能因症状风险较高而人为提高。"
                    ),
                ],
                "schema": {
                    "facts": [
                        {
                            "key": "one item from consultation_fact_keys",
                            "value": "归一后的中文事实值",
                            "status": "confirmed|negative|unknown|uncertain|contradicted",
                            "confidence": 0.0,
                            "source_text": "用户原话片段",
                            "category": (
                                "patient_identity|time_course|systemic_status|"
                                "intake_output|domain_specific|symptom_profile|other"
                            ),
                        }
                    ],
                    "observations": [
                        {
                            "category": "stable snake_case observation category",
                            "label": "中文观察标签",
                            "value": "归一后的中文观察值",
                            "status": "confirmed|negative|unknown|uncertain|contradicted",
                            "confidence": 0.0,
                            "source_text": "用户原话片段",
                            "temporal_text": "明确时间原文或空字符串",
                        }
                    ],
                    "intent": {
                        "answer_now": False,
                        "wants_triage": False,
                        "correction": False,
                        "raw_intent": "简短中文说明",
                    },
                    "confidence": 0.0,
                    "rationale": "简短中文说明抽取依据",
                },
                "consultation_fact_keys": [item.value for item in ConsultationFactKey],
                "fact_statuses": [item.value for item in ConsultationFactStatus],
                "fact_categories": [item.value for item in ConsultationFactCategory],
                "pet_context_summary": pet_context_summary,
                "previous_state": previous_state or {},
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _normalize_result(
        self,
        output: SemanticExtractorOutput,
        *,
        source_text: str,
    ) -> SemanticExtractionResult:
        """将结构化 LLM 输出归一为稳定问诊语义结果。

        :param output: 通过 Pydantic 校验的结构化模型输出。
        :param source_text: 用户输入摘要。
        :return: 返回结构化问诊语义结果。
        """
        confidence = float(output.confidence)
        if confidence < self.settings.semantic_extraction_min_confidence:
            return self._empty_result(
                strategy="litellm_response_format_low_confidence",
                fallback_reason=f"consultation_semantic_low_confidence:{confidence:.2f}",
                confidence=confidence,
                source_text=source_text,
            )
        return SemanticExtractionResult(
            facts=self._normalize_facts(output.facts),
            observations=self._normalize_observations(output.observations),
            intent=SemanticIntent(
                answer_now=output.intent.answer_now,
                wants_triage=output.intent.wants_triage,
                correction=output.intent.correction,
                raw_intent=output.intent.raw_intent.strip()[:120],
            ),
            strategy="litellm_response_format",
            confidence=confidence,
            source_text=source_text,
        )

    def _normalize_facts(self, items: list[SemanticFactItem]) -> list[SemanticFact]:
        """校验、过滤并归一化 LLM 输出事实。

        :param items: 通过 Pydantic 校验的 LLM 输出事实列表。
        :return: 返回问诊状态合并可消费的结构化事实列表。
        """
        facts: list[SemanticFact] = []
        seen: set[tuple[ConsultationFactKey, ConsultationFactStatus, str]] = set()
        for item in items:
            if item.confidence < self.settings.semantic_extraction_min_confidence:
                continue
            value = item.value.strip()[:160]
            if not value:
                continue
            dedupe_key = (item.key, item.status, value)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            facts.append(
                SemanticFact(
                    key=item.key,
                    value=value,
                    status=item.status,
                    confidence=float(item.confidence),
                    source_text=item.source_text.strip()[:160],
                    category=item.category,
                )
            )
        return facts

    def _normalize_observations(self, items: list[SemanticObservationItem]) -> list[SemanticObservation]:
        """校验、过滤并归一化 LLM 输出的开放式结构化观察。

        :param items: 通过 Pydantic 校验的 LLM 输出观察列表。
        :return: 返回问诊工作记忆可消费的结构化观察列表。
        """
        observations: list[SemanticObservation] = []
        seen: set[tuple[str, ConsultationFactStatus, str]] = set()
        for item in items:
            if item.confidence < self.settings.semantic_extraction_min_confidence:
                continue
            category = self._normalize_observation_token(item.category, limit=48)
            label = item.label.strip()[:48]
            value = item.value.strip()[:180]
            if not category or not label or not value:
                continue
            dedupe_key = (category, item.status, value)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            observations.append(
                SemanticObservation(
                    category=category,
                    label=label,
                    value=value,
                    status=item.status,
                    confidence=float(item.confidence),
                    source_text=item.source_text.strip()[:160],
                    temporal_text=item.temporal_text.strip()[:80],
                )
            )
        return observations

    def _empty_result(
        self,
        *,
        strategy: ConsultationSemanticStrategy,
        fallback_reason: str,
        source_text: str,
        confidence: float = 0.0,
    ) -> SemanticExtractionResult:
        """构造显式失败、禁用、不可用或低置信的空语义结果。

        :param strategy: 语义抽取策略或失败状态。
        :param fallback_reason: 失败、禁用、不可用或低置信原因。
        :param source_text: 用户输入摘要。
        :param confidence: 已知整体置信度；无结构化输出时为 0。
        :return: 返回不携带可信事实的问诊语义结果。
        """
        return SemanticExtractionResult(
            facts=[],
            observations=[],
            strategy=strategy,
            fallback_reason=fallback_reason,
            confidence=confidence,
            source_text=source_text,
        )

    def _normalize_text(self, value: str) -> str:
        """归一化输入摘要文本，供显式失败状态和 metadata 使用。

        :param value: 原始用户输入。
        :return: 返回去除首尾空白后的文本。
        """
        return value.strip()

    def _clip_text(self, value: str, *, limit: int) -> str:
        """截断审计文本，避免 metadata 写入过长用户输入。

        :param value: 待截断文本。
        :param limit: 最大保留字符数。
        :return: 返回截断后的文本。
        """
        return value[:limit]

    def _normalize_observation_token(self, value: str, *, limit: int) -> str:
        """归一化开放观察类别标识，避免自由文本污染结构化键名。

        :param value: 原始类别标识。
        :param limit: 最大保留字符数。
        :return: 返回 snake_case 风格类别标识；无法归一时返回 other。
        """
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        allowed = [char for char in normalized if char.isascii() and (char.isalnum() or char == "_")]
        token = "".join(allowed).strip("_")[:limit]
        return token or "other"
