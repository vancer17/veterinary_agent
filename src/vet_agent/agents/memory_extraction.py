"""
文件：src/vet_agent/agents/memory_extraction.py
作用：提供多 Agent 协作中的任务拆分、安全、问诊、记忆抽取与回答生成能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vet_agent import Settings
from vet_agent import AgentTurnResponse, TrustedIdentity, VetContext
from vet_agent.runtime import QwenClient


ALLOWED_FACT_TYPES = {
    "profile",
    "medical",
    "medication",
    "diet",
    "behavior",
    "owner_preference",
}


@dataclass(frozen=True)
class MemoryFactCandidate:
    fact_type: str
    fact_key: str
    fact_value: str
    confidence: float
    source_text: str
    requires_confirmation: bool = False
    metadata: dict[str, Any] | None = None


class MemoryFactItem(BaseModel):
    fact_type: str = Field(min_length=1)
    fact_key: str = Field(min_length=1)
    fact_value: str = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    source_text: str = Field(default="")
    requires_confirmation: bool = False
    rationale: str = ""


class MemoryExtractionOutput(BaseModel):
    facts: list[MemoryFactItem] = Field(default_factory=list, max_length=12)


class MemoryWritePolicy:
    def __init__(self, *, min_confidence: float) -> None:
        """初始化当前对象。

        :param min_confidence: 参数 min_confidence。
        :return: 无返回值。
        """
        self.min_confidence = min_confidence

    def filter(self, candidates: list[MemoryFactCandidate]) -> list[MemoryFactCandidate]:
        """执行 filter 业务逻辑。

        :param candidates: 参数 candidates。
        :return: 返回函数执行结果。
        """
        filtered: list[MemoryFactCandidate] = []
        seen: set[tuple[str, str]] = set()
        for item in candidates:
            if item.fact_type not in ALLOWED_FACT_TYPES:
                continue
            if not item.fact_key.strip() or not item.fact_value.strip():
                continue
            if item.requires_confirmation:
                continue
            if item.confidence < self.min_confidence:
                continue
            key = (item.fact_type, item.fact_key)
            if key in seen:
                continue
            seen.add(key)
            filtered.append(item)
        return filtered


class MemoryExtractionAgent:
    """Extracts durable pet facts; PostgreSQL remains the authoritative fact store."""

    def __init__(self, qwen: QwenClient, settings: Settings) -> None:
        """初始化当前对象。

        :param qwen: 参数 qwen。
        :param settings: 应用配置对象。
        :return: 无返回值。
        """
        self.qwen = qwen
        self.settings = settings
        self.policy = MemoryWritePolicy(min_confidence=settings.memory_extraction_min_confidence)

    async def extract(
        self,
        *,
        identity: TrustedIdentity,
        user_text: str,
        response: AgentTurnResponse,
        vet_context: VetContext,
        model: str,
    ) -> list[MemoryFactCandidate]:
        """抽取可持久化的宠物事实。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param response: 响应对象。
        :param vet_context: 兽医业务上下文。
        :param model: 模型名称。
        :return: 返回函数执行结果。
        """
        if not self.settings.enable_memory_extraction:
            return []
        candidates = [*self._from_pet_info(vet_context), *self._rule_candidates(user_text)]
        if self._llm_enabled():
            candidates.extend(await self._llm_candidates(identity, user_text, response, vet_context, model))
        return self.policy.filter(candidates)

    def _llm_enabled(self) -> bool:
        """执行 _llm_enabled 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        return self.settings.enable_llm_memory_extraction and self.qwen.available

    def _from_pet_info(self, vet_context: VetContext) -> list[MemoryFactCandidate]:
        """执行 _from_pet_info 内部辅助逻辑。

        :param vet_context: 兽医业务上下文。
        :return: 返回函数执行结果。
        """
        profile = vet_context.pet_info or {}
        mapping = {
            "species": profile.get("species"),
            "breed": profile.get("breed"),
            "age": profile.get("age"),
            "weight_kg": profile.get("weight_kg") or profile.get("weight"),
            "sex": profile.get("sex"),
            "neutered": profile.get("neutered"),
        }
        candidates: list[MemoryFactCandidate] = []
        for key, value in mapping.items():
            if value is None or value == "":
                continue
            candidates.append(
                MemoryFactCandidate(
                    fact_type="profile",
                    fact_key=key,
                    fact_value=str(value),
                    confidence=0.95,
                    source_text="vet_context.pet_info",
                    metadata={"source": "vet_context"},
                )
            )
        return candidates

    def _rule_candidates(self, user_text: str) -> list[MemoryFactCandidate]:
        """执行 _rule_candidates 内部辅助逻辑。

        :param user_text: 用户输入文本。
        :return: 返回函数执行结果。
        """
        text = user_text.strip()
        if not text:
            return []
        candidates: list[MemoryFactCandidate] = []
        weight = re.search(r"(\d+(?:\.\d+)?)\s*(kg|公斤|千克)", text, flags=re.I)
        if weight:
            candidates.append(self._candidate("profile", "weight_kg", weight.group(1), 0.78, weight.group(0)))
        if re.search(r"(过敏|allerg)", text, flags=re.I):
            candidates.append(self._candidate("medical", "allergy", self._snippet(text, "过敏"), 0.82, text))
        if re.search(r"(绝育|未绝育|neuter|spay)", text, flags=re.I):
            candidates.append(self._candidate("profile", "neutered", self._snippet(text, "绝育"), 0.78, text))
        if re.search(r"(疫苗|免疫|vaccine)", text, flags=re.I):
            candidates.append(self._candidate("medical", "vaccination", self._snippet(text, "疫苗"), 0.76, text))
        if re.search(r"(驱虫|deworm)", text, flags=re.I):
            candidates.append(self._candidate("medical", "deworming", self._snippet(text, "驱虫"), 0.76, text))
        if re.search(r"(长期|慢性|既往|病史|chronic)", text, flags=re.I):
            candidates.append(self._candidate("medical", "history", self._snippet(text, "病史"), 0.74, text))
        if re.search(r"(正在吃|正在用|服用|用药|medication)", text, flags=re.I):
            candidates.append(self._candidate("medication", "current", self._snippet(text, "用药"), 0.74, text))
        if re.search(r"(主粮|猫粮|狗粮|处方粮|换粮|diet)", text, flags=re.I):
            candidates.append(self._candidate("diet", "current_food", self._snippet(text, "粮"), 0.74, text))
        return candidates

    async def _llm_candidates(
        self,
        identity: TrustedIdentity,
        user_text: str,
        response: AgentTurnResponse,
        vet_context: VetContext,
        model: str,
    ) -> list[MemoryFactCandidate]:
        """执行 _llm_candidates 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param response: 响应对象。
        :param vet_context: 兽医业务上下文。
        :param model: 模型名称。
        :return: 返回函数执行结果。
        """
        try:
            raw = await self.qwen.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are MemoryExtractionAgent for a veterinary assistant. "
                            "Return only JSON. Extract durable pet facts, not acute one-off symptoms."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(identity, user_text, response, vet_context),
                    },
                ],
                model=model,
                temperature=0.0,
            )
            parsed = MemoryExtractionOutput.model_validate(self._extract_json(raw))
        except (ValidationError, ValueError, json.JSONDecodeError, RuntimeError):
            return []
        return [
            MemoryFactCandidate(
                fact_type=item.fact_type.strip(),
                fact_key=item.fact_key.strip(),
                fact_value=item.fact_value.strip(),
                confidence=item.confidence,
                source_text=item.source_text[:500] or user_text[:500],
                requires_confirmation=item.requires_confirmation,
                metadata={"source": "MemoryExtractionAgent", "rationale": item.rationale[:200]},
            )
            for item in parsed.facts
        ]

    def _prompt(
        self,
        identity: TrustedIdentity,
        user_text: str,
        response: AgentTurnResponse,
        vet_context: VetContext,
    ) -> str:
        """执行 _prompt 内部辅助逻辑。

        :param identity: 可信身份信息。
        :param user_text: 用户输入文本。
        :param response: 响应对象。
        :param vet_context: 兽医业务上下文。
        :return: 返回函数执行结果。
        """
        return json.dumps(
            {
                "scope": {
                    "user_id": identity.user_id,
                    "pet_id": identity.pet_id,
                    "session_id": identity.session_id,
                },
                "allowed_fact_types": sorted(ALLOWED_FACT_TYPES),
                "rules": [
                    "Extract only durable facts about this pet or owner preference.",
                    "Do not store transient symptoms unless the user says they are chronic or historical.",
                    "Mark requires_confirmation=true when the fact is uncertain, corrected, or conflicts with context.",
                    "Never change user_id or pet_id.",
                ],
                "schema": {
                    "facts": [
                        {
                            "fact_type": "profile|medical|medication|diet|behavior|owner_preference",
                            "fact_key": "short_snake_case",
                            "fact_value": "user-grounded value",
                            "confidence": 0.0,
                            "source_text": "short quote or summary from user text",
                            "requires_confirmation": False,
                            "rationale": "brief reason",
                        }
                    ]
                },
                "vet_context_pet_info": vet_context.pet_info,
                "user_text": user_text[:3000],
                "assistant_status": response.status,
            },
            ensure_ascii=False,
        )

    def _extract_json(self, raw: str) -> dict[str, Any]:
        """执行内部抽取逻辑。

        :param raw: 原始文本或响应内容。
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
            raise ValueError("memory extraction output must be a JSON object")
        return data

    def _candidate(
        self,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_text: str,
    ) -> MemoryFactCandidate:
        """执行 _candidate 内部辅助逻辑。

        :param fact_type: 事实类型。
        :param fact_key: 事实键名。
        :param fact_value: 事实内容。
        :param confidence: 置信度。
        :param source_text: 事实来源文本。
        :return: 返回函数执行结果。
        """
        return MemoryFactCandidate(
            fact_type=fact_type,
            fact_key=fact_key,
            fact_value=fact_value[:500],
            confidence=confidence,
            source_text=source_text[:500],
            metadata={"source": "rule_memory_extraction"},
        )

    def _snippet(self, text: str, keyword: str) -> str:
        """执行 _snippet 内部辅助逻辑。

        :param text: 待处理文本。
        :param keyword: 参数 keyword。
        :return: 返回函数执行结果。
        """
        index = text.find(keyword)
        if index < 0:
            return text[:160]
        start = max(0, index - 60)
        end = min(len(text), index + 120)
        return text[start:end].strip()
