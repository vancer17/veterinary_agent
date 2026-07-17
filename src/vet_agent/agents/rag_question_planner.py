"""
文件：src/vet_agent/agents/rag_question_planner.py
作用：基于 RAG 证据生成动态追问问题，减少固定槽位模板带来的机械追问。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vet_agent.repositories import KnowledgeHit
from vet_agent.runtime import QwenClient


@dataclass(frozen=True)
class RagFollowupQuestion:
    """表示一个面向用户的 RAG 追问问题。

    :param slot: 对应的标准问诊槽位。
    :param question: 面向用户的问题文本。
    :param reason: 为什么优先追问该问题。
    :param evidence_titles: 支撑该问题的知识库标题。
    :param priority: 问题优先级，数值越小越靠前。
    :return: 无返回值。
    """

    slot: str
    question: str
    reason: str = ""
    evidence_titles: list[str] = field(default_factory=list)
    priority: int = 100


@dataclass(frozen=True)
class RagFollowupPlan:
    """表示 RAG 动态追问规划结果。

    :param questions: 动态追问问题列表。
    :param strategy: 追问规划策略。
    :param fallback_reason: 回退原因。
    :return: 无返回值。
    """

    questions: list[RagFollowupQuestion]
    strategy: str
    fallback_reason: str | None = None

    def question_texts(self) -> list[str]:
        """返回面向用户的问题文本列表。

        :return: 返回函数执行结果。
        """
        return [item.question for item in self.questions]

    def reason_lines(self) -> list[str]:
        """返回追问问题的用户可见依据说明。

        :return: 返回函数执行结果。
        """
        lines: list[str] = []
        for item in self.questions:
            if not item.reason:
                continue
            evidence = f"（参考：{'、'.join(item.evidence_titles[:2])}）" if item.evidence_titles else ""
            lines.append(f"- {item.reason}{evidence}")
        return lines

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回函数执行结果。
        """
        return {
            "strategy": self.strategy,
            "fallback_reason": self.fallback_reason,
            "questions": [
                {
                    "slot": item.slot,
                    "question": item.question,
                    "reason": item.reason,
                    "evidence_titles": item.evidence_titles,
                    "priority": item.priority,
                }
                for item in self.questions
            ],
        }


class RagQuestionItem(BaseModel):
    """RAG 追问 LLM 输出中的单个问题。

    :return: 无返回值。
    """

    slot: str = Field(min_length=1)
    question: str = Field(min_length=4)
    reason: str = Field(default="")
    evidence_titles: list[str] = Field(default_factory=list, max_length=3)
    priority: int = Field(default=100, ge=1, le=100)


class RagQuestionOutput(BaseModel):
    """RAG 追问 LLM 输出结构。

    :return: 无返回值。
    """

    questions: list[RagQuestionItem] = Field(default_factory=list, max_length=5)


class RagQuestionPlannerAgent:
    """根据知识库证据和缺失槽位生成动态追问。"""

    def __init__(self, qwen: QwenClient | None = None) -> None:
        """初始化当前对象。

        :param qwen: 通义千问客户端。
        :return: 无返回值。
        """
        self.qwen = qwen

    async def plan(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        consultation_state: dict[str, Any],
        missing_slots: list[str],
        fallback_questions: list[str],
        knowledge_hits: list[KnowledgeHit],
        model: str,
        max_questions: int,
    ) -> RagFollowupPlan:
        """基于知识库命中结果生成动态追问规划。

        :param user_text: 用户输入文本。
        :param pet_context_summary: 宠物上下文摘要。
        :param consultation_state: 问诊状态。
        :param missing_slots: 缺失槽位列表。
        :param fallback_questions: 规则兜底问题列表。
        :param knowledge_hits: 命中的知识片段列表。
        :param model: 模型名称。
        :param max_questions: 最多追问数量。
        :return: 返回函数执行结果。
        """
        if not missing_slots:
            return RagFollowupPlan(questions=[], strategy="no_missing_slots")
        if not self._llm_enabled():
            return self._fallback(fallback_questions, missing_slots, "llm_unavailable")
        if not knowledge_hits:
            return self._fallback(fallback_questions, missing_slots, "no_knowledge_hits")

        try:
            raw = await self.qwen.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are RagQuestionPlannerAgent in a veterinary multi-agent system. "
                            "Generate only follow-up questions. Do not diagnose, do not recommend treatment, "
                            "and return JSON only."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._prompt(
                            user_text,
                            pet_context_summary,
                            consultation_state,
                            missing_slots,
                            fallback_questions,
                            knowledge_hits,
                            max_questions,
                        ),
                    },
                ],
                model=model,
                temperature=0.0,
            )
            parsed = RagQuestionOutput.model_validate(self._extract_json(raw))
            questions = self._normalize_questions(parsed.questions, missing_slots, knowledge_hits, max_questions)
            if not questions:
                return self._fallback(fallback_questions, missing_slots, "llm_returned_no_valid_questions")
            return RagFollowupPlan(questions=questions, strategy="rag_llm_question_planner")
        except (ValidationError, ValueError, json.JSONDecodeError, RuntimeError):
            return self._fallback(fallback_questions, missing_slots, "llm_parse_or_call_failed")

    def _llm_enabled(self) -> bool:
        """检查 LLM 动态追问能力是否可用。

        :return: 返回函数执行结果。
        """
        return bool(self.qwen is not None and self.qwen.available)

    def _fallback(
        self,
        fallback_questions: list[str],
        missing_slots: list[str],
        reason: str,
    ) -> RagFollowupPlan:
        """生成规则兜底追问规划。

        :param fallback_questions: 规则兜底问题列表。
        :param missing_slots: 缺失槽位列表。
        :param reason: 回退原因。
        :return: 返回函数执行结果。
        """
        questions = [
            RagFollowupQuestion(
                slot=missing_slots[index] if index < len(missing_slots) else "unknown",
                question=question,
                reason="该信息仍是完成安全分诊所需的关键上下文。",
                priority=(index + 1) * 10,
            )
            for index, question in enumerate(fallback_questions)
            if question.strip()
        ]
        return RagFollowupPlan(questions=questions, strategy="rule_slot_fallback", fallback_reason=reason)

    def _prompt(
        self,
        user_text: str,
        pet_context_summary: str,
        consultation_state: dict[str, Any],
        missing_slots: list[str],
        fallback_questions: list[str],
        knowledge_hits: list[KnowledgeHit],
        max_questions: int,
    ) -> str:
        """构造 RAG 动态追问提示词。

        :param user_text: 用户输入文本。
        :param pet_context_summary: 宠物上下文摘要。
        :param consultation_state: 问诊状态。
        :param missing_slots: 缺失槽位列表。
        :param fallback_questions: 规则兜底问题列表。
        :param knowledge_hits: 命中的知识片段列表。
        :param max_questions: 最多追问数量。
        :return: 返回函数执行结果。
        """
        knowledge = [
            {
                "title": hit.title,
                "summary": hit.summary[:700],
                "source": hit.source,
                "score": hit.score,
            }
            for hit in knowledge_hits[:4]
        ]
        return json.dumps(
            {
                "task": "请根据知识库证据反推下一轮最值得问的问题。只追问，不给诊断或处理建议。",
                "rules": [
                    "问题必须面向宠物主人，口语化、具体、避免模板腔。",
                    "优先询问能区分风险等级或下一步行动的信息。",
                    "不要重复询问 consultation_state.slots 中已经明确的信息。",
                    "slot 必须来自 missing_slots；如果确实需要问非标准信息，映射到最接近的 missing slot。",
                    "每个问题必须说明 reason，reason 用一句话解释为什么这条证据提示要问这个。",
                    "最多输出 max_questions 个问题。",
                    "禁止给用药剂量、诊断结论、治疗方案。",
                ],
                "schema": {
                    "questions": [
                        {
                            "slot": "one item from missing_slots",
                            "question": "面向用户的一句话问题",
                            "reason": "为什么这比固定模板更值得先问",
                            "evidence_titles": ["引用到的知识库标题"],
                            "priority": 10,
                        }
                    ]
                },
                "max_questions": max_questions,
                "missing_slots": missing_slots,
                "fallback_questions": fallback_questions,
                "consultation_state": consultation_state,
                "pet_context_summary": pet_context_summary,
                "knowledge_hits": knowledge,
                "user_text": user_text,
            },
            ensure_ascii=False,
        )

    def _extract_json(self, raw: str) -> dict[str, Any]:
        """从 LLM 原始输出中提取 JSON 对象。

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
            raise ValueError("RAG question planner output must be a JSON object")
        return data

    def _normalize_questions(
        self,
        items: list[RagQuestionItem],
        missing_slots: list[str],
        knowledge_hits: list[KnowledgeHit],
        max_questions: int,
    ) -> list[RagFollowupQuestion]:
        """清洗并去重 LLM 输出的问题。

        :param items: 数据项列表。
        :param missing_slots: 缺失槽位列表。
        :param knowledge_hits: 命中的知识片段列表。
        :param max_questions: 最多追问数量。
        :return: 返回函数执行结果。
        """
        allowed_slots = set(missing_slots)
        known_titles = {hit.title for hit in knowledge_hits}
        questions: list[RagFollowupQuestion] = []
        seen: set[str] = set()
        for item in sorted(items, key=lambda value: value.priority):
            slot = item.slot.strip()
            if slot not in allowed_slots:
                continue
            question = self._clean_question(item.question)
            if not question or question in seen:
                continue
            seen.add(question)
            evidence_titles = [title for title in item.evidence_titles if title in known_titles]
            questions.append(
                RagFollowupQuestion(
                    slot=slot,
                    question=question,
                    reason=item.reason.strip()[:180],
                    evidence_titles=evidence_titles,
                    priority=item.priority,
                )
            )
            if len(questions) >= max_questions:
                break
        return questions

    def _clean_question(self, question: str) -> str:
        """清洗面向用户的问题文本。

        :param question: 参数 question。
        :return: 返回函数执行结果。
        """
        text = re.sub(r"\s+", " ", question).strip()
        if not text:
            return ""
        if len(text) > 160:
            text = text[:160].rstrip("，,。；; ")
        if text[-1] not in "？?":
            text = f"{text}？"
        return text
