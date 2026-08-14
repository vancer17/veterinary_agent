"""
=============================================================================
文件：src/vet_agent/followup_rag/service.py
作用：编排追问相关 RAG 的检索、结构化规划与业务契约校验。
范围：仅在 OPA 回答充分性裁决为 ask 后执行；本服务不决定是否追问，
      不读取或写入问诊状态，不提供默认模板、关键词、正则或 seed 回退。
说明：本服务是旧版手写追问规划与默认回退路径的替代实现。
      任一依赖不可用、无已审核知识命中或结构化输出不符合契约均 Fail Fast。
=============================================================================
"""

from __future__ import annotations

from vet_agent.repositories import KnowledgeHit

from .errors import FollowupRagContractError, FollowupRagDependencyError
from .models import (
    FollowupRagPlan,
    FollowupRagQuestion,
    FollowupRagRequest,
    FollowupRagStrategy,
    FollowupRagRetrievalResult,
)
from .planner import FollowupQuestionPlannerOutput
from .ports import FollowupQuestionPlanner, FollowupRagRetriever, FollowupRagServiceProtocol
from .query_builder import FollowupRagQueryBuilder


DEFAULT_FOLLOWUP_RAG_CHUNK_TYPES: tuple[str, ...] = ("followup_questions",)


class FollowupRagService(FollowupRagServiceProtocol):
    """执行追问相关 RAG 的生产服务编排。

    :return: 无返回值；该服务将 OPA ask 裁决转换为证据驱动的追问计划。
    """

    def __init__(
        self,
        *,
        retriever: FollowupRagRetriever,
        planner: FollowupQuestionPlanner,
        query_builder: FollowupRagQueryBuilder,
        top_k: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...] = DEFAULT_FOLLOWUP_RAG_CHUNK_TYPES,
    ) -> None:
        """初始化追问相关 RAG 服务。

        :param retriever: 已审核知识检索器。
        :param planner: 结构化追问规划器。
        :param query_builder: 结构化检索查询构造器。
        :param top_k: 本轮最大召回数量。
        :param min_score: 本轮最低向量相似度阈值。
        :param allowed_chunk_types: 允许用于追问的知识 chunk 类型。
        :return: 无返回值。
        :raises FollowupRagContractError: 配置不符合追问 RAG 契约时抛出。
        """
        if top_k < 1:
            raise FollowupRagContractError(
                "followup RAG top_k must be positive",
                details={"top_k": top_k},
            )
        if min_score < 0 or min_score > 1:
            raise FollowupRagContractError(
                "followup RAG min_score must be between 0 and 1",
                details={"min_score": min_score},
            )
        normalized_chunk_types = tuple(item.strip() for item in allowed_chunk_types if item.strip())
        if not normalized_chunk_types:
            raise FollowupRagContractError(
                "followup RAG allowed chunk types are empty",
                details={"allowed_chunk_types": list(allowed_chunk_types)},
            )
        self.retriever = retriever
        self.planner = planner
        self.query_builder = query_builder
        self.top_k = top_k
        self.min_score = min_score
        self.allowed_chunk_types = normalized_chunk_types

    async def plan(self, request: FollowupRagRequest) -> FollowupRagPlan:
        """在回答充分性 ask 分支生成追问计划。

        :param request: 追问 RAG 结构化请求。
        :return: 返回已通过业务契约校验的追问计划。
        :raises FollowupRagContractError: 请求或模型输出不符合契约时抛出。
        :raises FollowupRagDependencyError: 检索或模型依赖不可用时抛出。
        """
        self._validate_request(request)
        query = self.query_builder.build(request)
        retrieval = self.retriever.retrieve(
            query,
            limit=self.top_k,
            min_score=self.min_score,
            allowed_chunk_types=self.allowed_chunk_types,
        )
        if not retrieval.hits:
            raise FollowupRagDependencyError(
                "followup RAG retrieval returned no hits",
                details={"query": query, "top_k": self.top_k, "min_score": self.min_score},
            )
        output = await self.planner.generate(request=request, retrieval=retrieval)
        return self._validated_plan(request, retrieval, output)

    def is_ready(self) -> bool:
        """检查追问相关 RAG 服务是否就绪。

        :return: 检索器与结构化规划器均就绪时返回 True。
        """
        return self.retriever.is_ready() and self.planner.is_ready()

    def _validate_request(self, request: FollowupRagRequest) -> None:
        """校验追问相关 RAG 的入口请求。

        :param request: 追问 RAG 结构化请求。
        :return: 无返回值。
        :raises FollowupRagContractError: 请求缺少 ask 分支必要上下文时抛出。
        """
        if not request.user_text.strip():
            raise FollowupRagContractError(
                "followup RAG request user_text is empty",
                details={"reason": "empty_user_text"},
            )
        if not request.missing_slots:
            raise FollowupRagContractError(
                "followup RAG requires missing slots from answerability policy",
                details={"reason": "empty_missing_slots"},
            )
        if request.max_questions < 1:
            raise FollowupRagContractError(
                "followup RAG max_questions must be positive",
                details={"max_questions": request.max_questions},
            )
        if str(request.answerability.get("decision") or "").strip() != "ask":
            raise FollowupRagContractError(
                "followup RAG can only run after ask decision",
                details={"decision": request.answerability.get("decision")},
            )

    def _validated_plan(
        self,
        request: FollowupRagRequest,
        retrieval: FollowupRagRetrievalResult,
        output: FollowupQuestionPlannerOutput,
    ) -> FollowupRagPlan:
        """校验结构化模型输出并转换为最终追问计划。

        :param request: 追问 RAG 结构化请求。
        :param retrieval: 本轮 RAG 召回结果。
        :param output: LiteLLM response_format 返回的结构化候选。
        :return: 返回最终追问计划。
        :raises FollowupRagContractError: 候选问题不符合追问 RAG 契约时抛出。
        """
        if len(output.questions) > request.max_questions:
            raise FollowupRagContractError(
                "followup RAG planner returned too many questions",
                details={
                    "returned": len(output.questions),
                    "max_questions": request.max_questions,
                },
            )

        allowed_slots = {str(slot) for slot in request.missing_slots}
        evidence_ids = retrieval.evidence_ids()
        title_by_evidence_id = self._title_by_evidence_id(retrieval.hits)
        asked_questions = {
            str(question).strip()
            for question in request.consultation_state.get("asked_questions") or []
            if str(question).strip()
        }
        seen_questions: set[str] = set()
        seen_slots: set[str] = set()
        questions: list[FollowupRagQuestion] = []
        for item in sorted(output.questions, key=lambda question: question.priority):
            slot = item.slot.strip()
            question_text = item.question.strip()
            reason_text = item.reason.strip()
            evidence_chunk_ids = [chunk_id.strip() for chunk_id in item.evidence_chunk_ids if chunk_id.strip()]
            if not slot:
                raise FollowupRagContractError(
                    "followup RAG planner returned empty slot",
                    details={"question": question_text},
                )
            if not question_text:
                raise FollowupRagContractError(
                    "followup RAG planner returned empty question",
                    details={"slot": slot},
                )
            if not reason_text:
                raise FollowupRagContractError(
                    "followup RAG planner returned empty reason",
                    details={"slot": slot, "question": question_text},
                )
            if slot not in allowed_slots:
                raise FollowupRagContractError(
                    "followup RAG planner returned a slot outside missing_slots",
                    details={"slot": slot, "missing_slots": sorted(allowed_slots)},
                )
            if question_text in seen_questions:
                raise FollowupRagContractError(
                    "followup RAG planner returned duplicated questions",
                    details={"question": question_text},
                )
            if question_text in asked_questions:
                raise FollowupRagContractError(
                    "followup RAG planner repeated an already asked question",
                    details={"question": question_text},
                )
            if slot in seen_slots:
                raise FollowupRagContractError(
                    "followup RAG planner returned duplicated slots",
                    details={"slot": slot},
                )
            unknown_evidence_ids = [chunk_id for chunk_id in evidence_chunk_ids if chunk_id not in evidence_ids]
            if unknown_evidence_ids:
                raise FollowupRagContractError(
                    "followup RAG planner referenced evidence outside current retrieval",
                    details={
                        "unknown_evidence_ids": unknown_evidence_ids,
                        "allowed_evidence_ids": sorted(evidence_ids),
                    },
                )
            questions.append(
                FollowupRagQuestion(
                    slot=slot,
                    question=question_text,
                    reason=reason_text,
                    evidence_chunk_ids=evidence_chunk_ids,
                    evidence_titles=[
                        title_by_evidence_id[chunk_id]
                        for chunk_id in evidence_chunk_ids
                        if chunk_id in title_by_evidence_id
                    ],
                    priority=item.priority,
                )
            )
            seen_questions.add(question_text)
            seen_slots.add(slot)

        if not questions:
            raise FollowupRagContractError(
                "followup RAG planner returned no valid questions",
                details={"missing_slots": request.missing_slots},
            )
        return FollowupRagPlan(
            questions=questions,
            strategy=FollowupRagStrategy.LLAMA_INDEX_PGVECTOR_STRUCTURED,
            retrieval=retrieval,
            rationale=output.rationale,
        )

    def _title_by_evidence_id(self, hits: list[KnowledgeHit]) -> dict[str, str]:
        """构造本轮 RAG 证据标识到标题的索引。

        :param hits: 本轮知识命中列表。
        :return: 返回证据标识与标题的映射。
        """
        mapping: dict[str, str] = {}
        for index, hit in enumerate(hits):
            metadata = dict(hit.metadata or {})
            evidence_id = str(metadata.get("chunk_id") or metadata.get("knowledge_chunk_id") or f"followup_hit_{index + 1}")
            mapping[evidence_id] = hit.title
        return mapping
