"""
=============================================================================
文件：src/vet_agent/followup_rag/models.py
作用：定义追问相关 RAG 迁移链路的稳定领域模型。
范围：承载 FollowupRagService 输入、知识召回结果、结构化追问问题、
      追问计划、检索审计摘要和响应 metadata 投影。
说明：本文件不访问数据库、不调用模型、不扫描用户原文关键词，也不更新
      活跃问诊状态；主编排器仅在计划通过校验后保存 planned questions。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vet_agent import Evidence
from vet_agent.repositories import KnowledgeHit


class FollowupRagStrategy(StrEnum):
    """表示追问相关 RAG 链路允许暴露的规划策略名称。

    :return: 无返回值；枚举值用于响应 metadata、测试断言与链路审计。
    """

    LLAMA_INDEX_PGVECTOR_STRUCTURED = "llamaindex_pgvector_structured_followup"


@dataclass(frozen=True)
class FollowupRagRequest:
    """表示追问相关 RAG 在 ask 分支消费的结构化请求。

    :param user_text: 当前任务的用户输入文本。
    :param pet_context_summary: 服务端可信宠物上下文摘要。
    :param consultation_state: 问诊状态与回答充分性阶段输出的状态快照。
    :param missing_slots: OPA 回答充分性策略要求补充的高价值证据槽位。
    :param answerability: OPA 回答充分性裁决摘要。
    :param model: 本轮结构化追问规划使用的模型名称。
    :param max_questions: 本轮最多允许输出的追问问题数量。
    :return: 无返回值；该对象是 FollowupRagService 的唯一入口契约。
    """

    user_text: str
    pet_context_summary: str
    consultation_state: dict[str, Any]
    missing_slots: list[str]
    answerability: dict[str, Any]
    model: str
    max_questions: int


@dataclass(frozen=True)
class FollowupRagRetrievalResult:
    """表示追问相关 RAG 的一次知识召回结果。

    :param query: 本轮结构化检索查询。
    :param hits: 通过业务仓储和 LlamaIndex 节点抽象归一后的知识命中。
    :param node_count: LlamaIndex 节点数量。
    :param backend: 检索后端说明。
    :param min_score: 本轮最低召回分数。
    :param top_k: 本轮召回数量上限。
    :return: 无返回值。
    """

    query: str
    hits: list[KnowledgeHit]
    node_count: int
    backend: str
    min_score: float
    top_k: int

    def evidence_ids(self) -> set[str]:
        """返回本轮召回结果中允许被追问计划引用的证据标识集合。

        :return: 返回证据标识集合。
        """
        return {_knowledge_hit_id(hit, index) for index, hit in enumerate(self.hits)}

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回检索摘要，不包含完整大段 RAG 原文。
        """
        return {
            "backend": self.backend,
            "top_k": self.top_k,
            "min_score": self.min_score,
            "hit_count": len(self.hits),
            "node_count": self.node_count,
            "hits": [
                {
                    "evidence_id": _knowledge_hit_id(hit, index),
                    "title": hit.title,
                    "source": hit.source,
                    "score": hit.score,
                    "source_url": hit.source_url,
                    "metadata": dict(hit.metadata or {}),
                }
                for index, hit in enumerate(self.hits)
            ],
        }


@dataclass(frozen=True)
class FollowupRagQuestion:
    """表示一个通过知识证据支撑的结构化追问问题。

    :param slot: 对应 OPA blocking_slots 或 unresolved_slots 中的标准槽位。
    :param question: 面向宠物主人的问题文本。
    :param reason: 为什么该问题会影响分诊或下一步建议。
    :param evidence_chunk_ids: 本问题引用的本轮 RAG 证据标识。
    :param evidence_titles: 本问题引用的知识标题。
    :param priority: 问题优先级，数值越小越靠前。
    :return: 无返回值。
    """

    slot: str
    question: str
    reason: str
    evidence_chunk_ids: list[str]
    evidence_titles: list[str] = field(default_factory=list)
    priority: int = 100

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的问题摘要。

        :return: 返回可序列化的问题摘要。
        """
        return {
            "slot": self.slot,
            "question": self.question,
            "reason": self.reason,
            "evidence_chunk_ids": list(self.evidence_chunk_ids),
            "evidence_titles": list(self.evidence_titles),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class FollowupRagPlan:
    """表示追问相关 RAG 生成的最终追问计划。

    :param questions: 通过结构化模型输出和业务契约校验的问题列表。
    :param strategy: 追问规划策略。
    :param retrieval: 本轮知识召回结果摘要。
    :param rationale: 模型或测试替身给出的规划摘要。
    :return: 无返回值。
    """

    questions: list[FollowupRagQuestion]
    strategy: FollowupRagStrategy
    retrieval: FollowupRagRetrievalResult
    rationale: str = ""

    def question_texts(self) -> list[str]:
        """返回面向用户展示的问题文本列表。

        :return: 返回追问问题文本列表。
        """
        return [item.question for item in self.questions]

    def reason_lines(self) -> list[str]:
        """返回追问问题的用户可见依据说明。

        :return: 返回用于追问响应展示的依据说明列表。
        """
        lines: list[str] = []
        for item in self.questions:
            if not item.reason:
                continue
            evidence = f"（参考：{'、'.join(item.evidence_titles[:2])}）" if item.evidence_titles else ""
            lines.append(f"- {item.reason}{evidence}")
        return lines

    def to_evidence(self) -> list[Evidence]:
        """转换为 Agent 响应中的 Evidence 列表。

        :return: 返回可进入响应与 reasoning display 的证据列表。
        """
        return [
            Evidence(
                source=hit.source,
                detail=hit.summary,
                public_citation=hit.public_citation,
                metadata={
                    **dict(hit.metadata or {}),
                    "score": hit.score,
                    "title": hit.title,
                    "source_url": hit.source_url,
                    "type": "followup_rag_knowledge",
                    "evidence_id": _knowledge_hit_id(hit, index),
                },
            )
            for index, hit in enumerate(self.retrieval.hits)
        ]

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回追问计划摘要。
        """
        return {
            "strategy": self.strategy.value,
            "rationale": self.rationale,
            "retrieval": self.retrieval.to_metadata(),
            "questions": [item.to_metadata() for item in self.questions],
        }


def _knowledge_hit_id(hit: KnowledgeHit, index: int) -> str:
    """读取或生成本轮 RAG 证据标识。

    :param hit: 知识命中结果。
    :param index: 本轮命中序号。
    :return: 返回稳定证据标识。
    """
    metadata = dict(hit.metadata or {})
    for key in ("chunk_id", "knowledge_chunk_id", "id"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return f"followup_hit_{index + 1}"
