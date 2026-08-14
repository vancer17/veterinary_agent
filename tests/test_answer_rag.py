"""
=============================================================================
文件：tests/test_answer_rag.py
作用：验证回答相关 RAG 的结构化查询、服务契约、证据转换与 Fail Fast 语义。
范围：覆盖回答 RAG 的纯业务编排和协议替身，不连接真实 PostgreSQL、LiteLLM 或外部模型。
说明：测试使用显式协议实现，不使用 seed 知识、关键词匹配或旧版 KnowledgeService 兼容路径。
=============================================================================
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from vet_agent.answer_rag import (
    AnswerRagContractError,
    AnswerRagDependencyError,
    AnswerRagKnowledgeRepository,
    AnswerRagQueryBuilder,
    AnswerRagRequest,
    AnswerRagResult,
    AnswerRagRetrievalResult,
    AnswerRagRetriever,
    AnswerRagService,
    AnswerRagStrategy,
)
from vet_agent.repositories import KnowledgeHit


class StaticAnswerRagRetriever(AnswerRagRetriever):
    """为回答 RAG 单元测试提供显式注入的检索器替身。

    :return: 无返回值；该替身只记录服务传入的结构化检索参数。
    """

    def __init__(self, hits: list[KnowledgeHit]) -> None:
        """初始化回答 RAG 检索器测试替身。

        :param hits: 本轮测试应返回的知识命中列表。
        :return: 无返回值。
        """
        self.hits = list(hits)
        self.calls: list[dict[str, Any]] = []

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
        domain: str | None,
    ) -> AnswerRagRetrievalResult:
        """记录并返回回答 RAG 测试检索结果。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与召回的知识 chunk 类型。
        :param domain: 可选任务域硬过滤条件。
        :return: 返回测试检索结果。
        """
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "min_score": min_score,
                "allowed_chunk_types": allowed_chunk_types,
                "domain": domain,
            }
        )
        selected_hits = self.hits[:limit]
        return AnswerRagRetrievalResult(
            query=query,
            hits=selected_hits,
            node_count=len(selected_hits),
            backend="static_answer_rag_unit_test",
            min_score=min_score,
            top_k=limit,
        )

    def is_ready(self) -> bool:
        """检查回答 RAG 测试检索器是否就绪。

        :return: 始终返回 True。
        """
        return True


class StaticAnswerRagKnowledgeRepository(AnswerRagKnowledgeRepository):
    """为协议暴露测试提供显式仓储实现。

    :return: 无返回值；该类用于确认仓储协议可通过包级入口访问。
    """

    def retrieve_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
        domain: str | None,
    ) -> list[KnowledgeHit]:
        """返回空的协议测试结果。

        :param query_embedding: 查询 embedding。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许的知识 chunk 类型。
        :param domain: 可选任务域过滤条件。
        :return: 返回空知识命中列表。
        """
        del query_embedding, limit, min_score, allowed_chunk_types, domain
        return []

    def is_ready(self) -> bool:
        """检查协议测试仓储是否就绪。

        :return: 始终返回 True。
        """
        return True


def _request(
    *,
    decision: str = "answer",
    consultation_state: dict[str, Any] | None = None,
    answerability: dict[str, Any] | None = None,
    semantic_extraction: dict[str, Any] | None = None,
    task_domain: str = "digestive",
) -> AnswerRagRequest:
    """构造回答 RAG 单元测试请求。

    :param decision: 回答充分性策略决定。
    :param consultation_state: 问诊状态快照。
    :param answerability: 回答充分性策略摘要。
    :param semantic_extraction: 问诊语义抽取结果。
    :param task_domain: 当前任务域。
    :return: 返回回答 RAG 测试请求。
    """
    return AnswerRagRequest(
        user_text="宠物今天出现轻微呕吐，应该如何观察？",
        pet_context_summary="已验证宠物：犬，3 岁，当前无已知用药禁忌。",
        consultation_state=consultation_state
        or {
            "domain": task_domain,
            "slots": {"onset": "今天"},
            "evidence_profile": {"known_categories": ["digestive"]},
        },
        answerability=answerability or {"decision": decision, "answer_scope": "阶段性建议"},
        semantic_extraction=semantic_extraction
        or {"intent": "health_consultation", "chief_complaint": "呕吐"},
        task_domain=task_domain,
    )


def _retriever(*hits: KnowledgeHit) -> StaticAnswerRagRetriever:
    """构造回答 RAG 测试检索器。

    :param hits: 测试检索器应返回的知识命中。
    :return: 返回回答 RAG 检索器测试替身。
    """
    return StaticAnswerRagRetriever(list(hits))


def _hit() -> KnowledgeHit:
    """构造一条可公开展示的测试知识命中。

    :return: 返回带有 chunk 标识的测试知识命中。
    """
    return KnowledgeHit(
        title="呕吐观察建议",
        summary="应观察呕吐频率、精神状态、饮水情况和是否出现腹部明显疼痛。",
        source="answer_rag_unit_test",
        public_citation=True,
        score=0.92,
        source_url="https://example.invalid/answer-rag",
        metadata={"chunk_id": "knowledge_chunk:answer_rag_test", "chunk_type": "home_advice"},
    )


def test_query_builder_projects_structured_fields_without_unknown_fields() -> None:
    """验证查询构造器只投影允许的结构化字段。

    :return: 无返回值；断言通过表示查询构造器未把未知内部字段带入检索查询。
    """
    request = _request(
        answerability={
            "decision": "answer",
            "answer_scope": "阶段性建议",
            "internal_debug": "不得进入检索查询",
        },
        semantic_extraction={
            "intent": "health_consultation",
            "chief_complaint": "呕吐",
            "unexpected_field": "不得进入检索查询",
        },
    )

    payload = json.loads(AnswerRagQueryBuilder().build(request))

    assert payload["answerability"] == {
        "answer_scope": "阶段性建议",
        "decision": "answer",
    }
    assert payload["semantic_extraction"] == {
        "chief_complaint": "呕吐",
        "intent": "health_consultation",
    }


def test_answer_rag_service_returns_evidence_and_metadata() -> None:
    """验证回答 RAG 服务返回结构化结果、证据和审计 metadata。

    :return: 无返回值；断言通过表示服务主路径契约成立。
    """
    retriever = _retriever(_hit())
    service = AnswerRagService(
        retriever=retriever,
        query_builder=AnswerRagQueryBuilder(),
        top_k=3,
        min_score=0.35,
    )

    result = asyncio.run(service.retrieve(_request()))

    assert isinstance(result, AnswerRagResult)
    assert result.strategy is AnswerRagStrategy.LLAMA_INDEX_PGVECTOR
    assert len(result.hits) == 1
    assert result.to_evidence()[0].metadata["type"] == "answer_rag_knowledge"
    assert result.to_evidence()[0].metadata["evidence_id"] == "knowledge_chunk:answer_rag_test"
    assert retriever.calls[0]["domain"] is None


def test_answer_rag_service_can_apply_explicit_domain_filter() -> None:
    """验证领域过滤只有在显式配置开启时才进入仓储协议。

    :return: 无返回值；断言通过表示任务域默认不会成为硬过滤条件。
    """
    retriever = _retriever(_hit())
    service = AnswerRagService(
        retriever=retriever,
        query_builder=AnswerRagQueryBuilder(),
        top_k=3,
        min_score=0.35,
        filter_by_domain=True,
    )

    asyncio.run(service.retrieve(_request(task_domain="digestive")))

    assert retriever.calls[0]["domain"] == "digestive"


def test_answer_rag_service_rejects_non_answer_decision() -> None:
    """验证回答 RAG 不会绕过回答充分性裁决直接执行。

    :return: 无返回值；断言通过表示 ask 分支不会误用回答 RAG。
    """
    retriever = _retriever(_hit())
    service = AnswerRagService(
        retriever=retriever,
        query_builder=AnswerRagQueryBuilder(),
        top_k=3,
        min_score=0.35,
    )

    with pytest.raises(AnswerRagContractError):
        asyncio.run(service.retrieve(_request(decision="ask")))

    assert retriever.calls == []


def test_answer_rag_service_fails_fast_when_retrieval_has_no_hits() -> None:
    """验证回答 RAG 无有效知识命中时直接失败。

    :return: 无返回值；断言通过表示服务没有默认知识或空结果回退。
    """
    service = AnswerRagService(
        retriever=_retriever(),
        query_builder=AnswerRagQueryBuilder(),
        top_k=3,
        min_score=0.35,
    )

    with pytest.raises(AnswerRagDependencyError):
        asyncio.run(service.retrieve(_request()))


def test_answer_rag_query_builder_rejects_malformed_structured_fields() -> None:
    """验证查询构造器不会把非法结构化字段静默转换为空对象。

    :return: 无返回值；断言通过表示 malformed structured input 遵循 Fail Fast。
    """
    request = _request(
        consultation_state={
            "domain": "digestive",
            "slots": {"onset": "今天"},
            "evidence_profile": ["malformed"],
        }
    )
    malformed_request = AnswerRagRequest(
        user_text=request.user_text,
        pet_context_summary=request.pet_context_summary,
        consultation_state=request.consultation_state,
        answerability=request.answerability,
        semantic_extraction=request.semantic_extraction,
        task_domain=request.task_domain,
    )

    with pytest.raises(AnswerRagContractError):
        AnswerRagQueryBuilder().build(malformed_request)


def test_answer_rag_repository_protocol_is_explicitly_implemented() -> None:
    """验证回答 RAG 仓储协议可由显式实现类承载。

    :return: 无返回值；断言通过表示鸭子类型边界仍具备可追溯的显式协议继承。
    """
    repository = StaticAnswerRagKnowledgeRepository()

    assert repository.is_ready() is True
    assert repository.retrieve_by_embedding(
        [0.1, 0.9],
        limit=1,
        min_score=0.35,
        allowed_chunk_types=("condition_overview",),
        domain=None,
    ) == []
