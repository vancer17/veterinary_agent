"""
=============================================================================
文件：src/vet_agent/answer_rag/models.py
作用：定义回答相关 RAG 迁移链路的稳定领域模型。
范围：承载回答 RAG 请求、检索结果、证据上下文、策略名称和响应 metadata 投影。
说明：本文件不访问数据库、不调用模型、不扫描关键词，也不决定回答、追问或临床动作。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from vet_agent import Evidence
from vet_agent.repositories import KnowledgeHit


class AnswerRagStrategy(StrEnum):
    """表示回答相关 RAG 链路允许暴露的召回策略名称。

    :return: 无返回值；枚举值用于响应 metadata、测试断言与链路审计。
    """

    LLAMA_INDEX_PGVECTOR = "llamaindex_pgvector_structured_answer"
    STATIC_TEST = "static_answer_rag_test"


@dataclass(frozen=True)
class AnswerRagRequest:
    """表示回答相关 RAG 在 answer 分支消费的结构化请求。

    :param user_text: 当前任务的用户输入文本。
    :param pet_context_summary: 服务端可信宠物上下文摘要。
    :param consultation_state: 问诊状态与回答充分性阶段输出的状态快照。
    :param answerability: OPA 回答充分性裁决摘要。
    :param semantic_extraction: 本轮问诊语义抽取 metadata。
    :param task_domain: 当前任务域标识，仅作为知识治理过滤和检索提示。
    :return: 无返回值；该对象是 AnswerRagService 的唯一入口契约。
    """

    user_text: str
    pet_context_summary: str
    consultation_state: dict[str, Any]
    answerability: dict[str, Any]
    semantic_extraction: dict[str, Any]
    task_domain: str


@dataclass(frozen=True)
class AnswerRagRetrievalResult:
    """表示回答相关 RAG 的一次知识召回结果。

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
        """返回本轮召回结果中可追溯的证据标识集合。

        :return: 返回证据标识集合。
        """
        return {_knowledge_hit_id(hit, index) for index, hit in enumerate(self.hits)}

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回回答 RAG 检索摘要，不包含完整大段 RAG 原文。
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
class AnswerRagResult:
    """表示回答相关 RAG 生成的最终证据上下文。

    :param strategy: 回答 RAG 检索策略。
    :param retrieval: 本轮知识召回结果摘要。
    :return: 无返回值；该对象进入回复生成前的证据上下文编译阶段。
    """

    strategy: AnswerRagStrategy
    retrieval: AnswerRagRetrievalResult

    @property
    def hits(self) -> list[KnowledgeHit]:
        """返回可提供给回复生成器的知识命中列表。

        :return: 返回回答 RAG 知识命中列表。
        """
        return list(self.retrieval.hits)

    def to_evidence(self) -> list[Evidence]:
        """转换为 Agent 响应中的 Evidence 列表。

        :return: 返回可进入响应、reasoning display 与 trace 的证据列表。
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
                    "type": "answer_rag_knowledge",
                    "evidence_id": _knowledge_hit_id(hit, index),
                },
            )
            for index, hit in enumerate(self.retrieval.hits)
        ]

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 可序列化结构。

        :return: 返回回答 RAG 证据上下文摘要。
        """
        return {
            "strategy": self.strategy.value,
            "retrieval": self.retrieval.to_metadata(),
        }


def _knowledge_hit_id(hit: KnowledgeHit, index: int) -> str:
    """读取或生成本轮回答 RAG 证据标识。

    :param hit: 知识命中结果。
    :param index: 本轮命中序号。
    :return: 返回稳定证据标识。
    """
    metadata = dict(hit.metadata or {})
    for key in ("chunk_id", "knowledge_chunk_id", "id"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return f"answer_hit_{index + 1}"
