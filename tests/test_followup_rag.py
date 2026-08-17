"""
=============================================================================
文件：tests/test_followup_rag.py
作用：验证追问相关 RAG 服务的结构化契约与 Fail Fast 行为。
范围：仅覆盖 FollowupRagService 对检索器、结构化规划器和业务校验的编排；
      不启动真实数据库、LlamaIndex 向量库或 LiteLLM 服务。
说明：测试替身显式继承协议，用于追溯生产服务对外依赖边界。
=============================================================================
"""

from __future__ import annotations

import asyncio
import json

import pytest

from vet_agent.followup_rag import (
    FollowupQuestionItem,
    FollowupQuestionPlanner,
    FollowupQuestionPlannerOutput,
    FollowupRagContractError,
    FollowupRagDependencyError,
    FollowupRagQueryBuilder,
    FollowupRagRequest,
    FollowupRagRetrievalResult,
    FollowupRagRetriever,
    FollowupRagService,
    FollowupRagStrategy,
)
from vet_agent.repositories import KnowledgeHit


class StaticRetriever(FollowupRagRetriever):
    """为追问 RAG 服务测试提供静态检索器。

    :return: 无返回值；该替身显式继承检索协议以保留依赖边界。
    """

    def __init__(self) -> None:
        """初始化静态检索器。

        :return: 无返回值。
        """
        self.last_allowed_chunk_types: tuple[str, ...] = ()

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
    ) -> FollowupRagRetrievalResult:
        """返回静态已审核知识命中。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与追问召回的知识 chunk 类型。
        :return: 返回静态检索结果。
        """
        self.last_allowed_chunk_types = allowed_chunk_types
        hit = KnowledgeHit(
            title="消化道追问要点",
            summary="饭后蜷缩需要进一步确认腹痛、呕吐、粪便和精神食欲变化。",
            source="static_followup_rag_unit_test",
            public_citation=True,
            score=max(min_score, 0.91),
            metadata={
                "chunk_id": "knowledge_chunk:unit_followup_1",
                "chunk_type": "followup_questions",
            },
        )
        return FollowupRagRetrievalResult(
            query=query,
            hits=[hit],
            node_count=1,
            backend="static_unit_retriever",
            min_score=min_score,
            top_k=limit,
        )

    def is_ready(self) -> bool:
        """检查静态检索器是否就绪。

        :return: 始终返回 True。
        """
        return True


class FailingRetriever(FollowupRagRetriever):
    """为追问 RAG 服务测试提供显式失败检索器。

    :return: 无返回值；该替身用于验证治理上下文补齐。
    """

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
    ) -> FollowupRagRetrievalResult:
        """始终抛出追问 RAG 检索无命中异常。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与追问召回的知识 chunk 类型。
        :return: 不返回；该方法始终抛出依赖异常。
        :raises FollowupRagDependencyError: 始终抛出以模拟无命中。
        """
        del query, limit, min_score, allowed_chunk_types
        raise FollowupRagDependencyError(
            "followup RAG retrieval returned no approved vector hits",
            details={"reason": "no_approved_vector_hits"},
        )

    def is_ready(self) -> bool:
        """检查失败检索器是否就绪。

        :return: 始终返回 True。
        """
        return True


class StaticPlanner(FollowupQuestionPlanner):
    """为追问 RAG 服务测试提供结构化规划器替身。

    :return: 无返回值；该替身返回 Pydantic 结构化规划结果。
    """

    def __init__(self, questions: list[FollowupQuestionItem]) -> None:
        """初始化静态结构化规划器。

        :param questions: 待返回的结构化问题候选。
        :return: 无返回值。
        """
        self.questions = questions

    async def generate(
        self,
        *,
        request: FollowupRagRequest,
        retrieval: FollowupRagRetrievalResult,
    ) -> FollowupQuestionPlannerOutput:
        """返回静态结构化追问规划结果。

        :param request: 追问 RAG 结构化请求。
        :param retrieval: 本轮已审核知识召回结果。
        :return: 返回结构化追问规划候选。
        """
        del request, retrieval
        return FollowupQuestionPlannerOutput(
            questions=self.questions,
            rationale="单元测试结构化规划摘要。",
        )

    def is_ready(self) -> bool:
        """检查静态结构化规划器是否就绪。

        :return: 始终返回 True。
        """
        return True


def test_followup_rag_service_validates_structured_plan() -> None:
    """验证追问 RAG 服务会校验并转换结构化追问计划。

    :return: 无返回值；断言通过表示服务契约符合预期。
    """
    retriever = StaticRetriever()
    service = FollowupRagService(
        retriever=retriever,
        planner=StaticPlanner(
            [
                FollowupQuestionItem(
                    slot="onset",
                    question="这种饭后蜷缩是今天才开始，还是已经持续几天了？",
                    reason="起病时间会影响是否需要更积极线下评估。",
                    evidence_chunk_ids=["knowledge_chunk:unit_followup_1"],
                    priority=10,
                )
            ]
        ),
        query_builder=FollowupRagQueryBuilder(),
        top_k=3,
        min_score=0.4,
    )

    plan = asyncio.run(service.plan(_request()))

    assert plan.strategy == FollowupRagStrategy.LLAMA_INDEX_PGVECTOR_STRUCTURED
    assert plan.question_texts() == ["这种饭后蜷缩是今天才开始，还是已经持续几天了？"]
    assert plan.questions[0].evidence_titles == ["消化道追问要点"]
    assert retriever.last_allowed_chunk_types == ("followup_questions",)


def test_followup_rag_service_rejects_unknown_slot() -> None:
    """验证结构化规划器返回非缺失槽位时直接失败。

    :return: 无返回值；断言通过表示服务不会静默修补非法问题。
    """
    service = FollowupRagService(
        retriever=StaticRetriever(),
        planner=StaticPlanner(
            [
                FollowupQuestionItem(
                    slot="weight",
                    question="体重最近有没有变化？",
                    reason="该问题不属于本轮 OPA 缺失槽位。",
                    evidence_chunk_ids=["knowledge_chunk:unit_followup_1"],
                    priority=10,
                )
            ]
        ),
        query_builder=FollowupRagQueryBuilder(),
        top_k=3,
        min_score=0.4,
    )

    with pytest.raises(FollowupRagContractError):
        asyncio.run(service.plan(_request()))


def test_followup_rag_service_enriches_dependency_error_with_query_context() -> None:
    """验证追问 RAG 依赖异常会补齐治理所需的 query 上下文。

    :return: 无返回值；断言通过表示无命中会留下可治理细节。
    """
    service = FollowupRagService(
        retriever=FailingRetriever(),
        planner=StaticPlanner([]),
        query_builder=FollowupRagQueryBuilder(),
        top_k=3,
        min_score=0.4,
    )

    with pytest.raises(FollowupRagDependencyError) as exc_info:
        asyncio.run(service.plan(_request()))

    details = exc_info.value.details
    structured_query = json.loads(str(details["query"]))
    assert details["reason"] == "no_approved_vector_hits"
    assert details["query"]
    assert structured_query["missing_slots"] == ["onset", "mental_status"]
    assert details["top_k"] == 3
    assert details["min_score"] == 0.4
    assert details["allowed_chunk_types"] == ["followup_questions"]


def _request() -> FollowupRagRequest:
    """构造追问 RAG 服务测试请求。

    :return: 返回结构化测试请求。
    """
    return FollowupRagRequest(
        user_text="饭后总是缩成一团。",
        pet_context_summary="物种: dog；年龄: 3岁；体重: 12kg",
        consultation_state={
            "domain": "gastrointestinal",
            "slots": {"species": "dog", "life_stage_or_age": "3岁", "weight": "12kg"},
            "asked_questions": [],
            "evidence_profile": {"time_course": {"status": "unknown", "slots": []}},
        },
        missing_slots=["onset", "mental_status"],
        answerability={
            "decision": "ask",
            "blocking_slots": ["onset", "mental_status"],
            "reason": "仍缺少高价值分诊证据。",
        },
        model="qwen-plus",
        max_questions=2,
    )
