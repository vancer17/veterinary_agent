"""
=============================================================================
文件：src/vet_agent/answer_rag/service.py
作用：编排回答相关 RAG 的结构化查询、向量召回与业务契约校验。
范围：仅在 OPA 回答充分性裁决为 answer 后执行；本服务不决定是否回答，
      不读取或写入问诊状态，不提供默认模板、关键词、正则或 seed 回退。
说明：本服务是旧版 KnowledgeService.retrieve 及低质量知识回退路径的替代实现。
      任一依赖不可用、无已审核知识命中或请求不符合契约均 Fail Fast。
=============================================================================
"""

from __future__ import annotations

from vet_agent import DEFAULT_ANSWER_RAG_ALLOWED_CHUNK_TYPES

from .errors import AnswerRagContractError, AnswerRagDependencyError
from .models import AnswerRagRequest, AnswerRagResult, AnswerRagStrategy
from .ports import AnswerRagRetriever, AnswerRagServiceProtocol
from .query_builder import AnswerRagQueryBuilder


DEFAULT_ANSWER_RAG_CHUNK_TYPES: tuple[str, ...] = DEFAULT_ANSWER_RAG_ALLOWED_CHUNK_TYPES


class AnswerRagService(AnswerRagServiceProtocol):
    """执行回答相关 RAG 的生产服务编排。

    :return: 无返回值；该服务将 OPA answer 裁决转换为证据驱动的回答上下文。
    """

    def __init__(
        self,
        *,
        retriever: AnswerRagRetriever,
        query_builder: AnswerRagQueryBuilder,
        top_k: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...] = DEFAULT_ANSWER_RAG_CHUNK_TYPES,
        filter_by_domain: bool = False,
    ) -> None:
        """初始化回答相关 RAG 服务。

        :param retriever: 已审核知识检索器。
        :param query_builder: 结构化检索查询构造器。
        :param top_k: 本轮最大召回数量。
        :param min_score: 本轮最低向量相似度阈值。
        :param allowed_chunk_types: 允许用于回答的知识 chunk 类型。
        :param filter_by_domain: 是否将任务域作为硬过滤；默认只进入结构化 query。
        :return: 无返回值。
        :raises AnswerRagContractError: 配置不符合回答 RAG 契约时抛出。
        """
        if top_k < 1:
            raise AnswerRagContractError(
                "answer RAG top_k must be positive",
                details={"top_k": top_k},
            )
        if min_score < 0 or min_score > 1:
            raise AnswerRagContractError(
                "answer RAG min_score must be between 0 and 1",
                details={"min_score": min_score},
            )
        normalized_chunk_types = tuple(item.strip() for item in allowed_chunk_types if item.strip())
        if not normalized_chunk_types:
            raise AnswerRagContractError(
                "answer RAG allowed chunk types are empty",
                details={"allowed_chunk_types": list(allowed_chunk_types)},
            )
        self.retriever = retriever
        self.query_builder = query_builder
        self.top_k = top_k
        self.min_score = min_score
        self.allowed_chunk_types = normalized_chunk_types
        self.filter_by_domain = filter_by_domain

    async def retrieve(self, request: AnswerRagRequest) -> AnswerRagResult:
        """在回答充分性 answer 分支生成回答证据上下文。

        :param request: 回答 RAG 结构化请求。
        :return: 返回已通过业务契约校验的回答 RAG 结果。
        :raises AnswerRagContractError: 请求不符合契约时抛出。
        :raises AnswerRagDependencyError: 检索依赖不可用或无有效命中时抛出。
        """
        self._validate_request(request)
        query = self.query_builder.build(request)
        retrieval = self.retriever.retrieve(
            query,
            limit=self.top_k,
            min_score=self.min_score,
            allowed_chunk_types=self.allowed_chunk_types,
            domain=self._domain_filter(request),
        )
        if not retrieval.hits:
            raise AnswerRagDependencyError(
                "answer RAG retrieval returned no hits",
                details={"query": query, "top_k": self.top_k, "min_score": self.min_score},
            )
        self._validate_retrieval_hits(retrieval.hits)
        return AnswerRagResult(
            strategy=AnswerRagStrategy.LLAMA_INDEX_PGVECTOR,
            retrieval=retrieval,
        )

    def is_ready(self) -> bool:
        """检查回答相关 RAG 服务是否就绪。

        :return: 检索器就绪时返回 True。
        """
        return self.retriever.is_ready()

    def _validate_request(self, request: AnswerRagRequest) -> None:
        """校验回答相关 RAG 的入口请求。

        :param request: 回答 RAG 结构化请求。
        :return: 无返回值。
        :raises AnswerRagContractError: 请求缺少 answer 分支必要上下文时抛出。
        """
        if not isinstance(request.user_text, str) or not request.user_text.strip():
            raise AnswerRagContractError(
                "answer RAG request user_text is empty",
                details={"reason": "empty_user_text"},
            )
        if not isinstance(request.pet_context_summary, str):
            raise AnswerRagContractError(
                "answer RAG request pet_context_summary must be a string",
                details={"value_type": type(request.pet_context_summary).__name__},
            )
        if not isinstance(request.consultation_state, dict):
            raise AnswerRagContractError(
                "answer RAG request consultation_state must be an object",
                details={"value_type": type(request.consultation_state).__name__},
            )
        if not isinstance(request.answerability, dict):
            raise AnswerRagContractError(
                "answer RAG request answerability must be an object",
                details={"value_type": type(request.answerability).__name__},
            )
        if not isinstance(request.semantic_extraction, dict):
            raise AnswerRagContractError(
                "answer RAG request semantic_extraction must be an object",
                details={"value_type": type(request.semantic_extraction).__name__},
            )
        if not isinstance(request.task_domain, str):
            raise AnswerRagContractError(
                "answer RAG request task_domain must be a string",
                details={"value_type": type(request.task_domain).__name__},
            )
        if str(request.answerability.get("decision") or "").strip() != "answer":
            raise AnswerRagContractError(
                "answer RAG can only run after answer decision",
                details={"decision": request.answerability.get("decision")},
            )

    def _domain_filter(self, request: AnswerRagRequest) -> str | None:
        """按配置读取回答 RAG 的硬领域过滤条件。

        :param request: 回答 RAG 结构化请求。
        :return: 启用领域硬过滤时返回任务域，否则返回 None。
        """
        if not self.filter_by_domain:
            return None
        domain = str(request.task_domain or request.consultation_state.get("domain") or "").strip()
        return domain or None

    def _validate_retrieval_hits(self, hits: list[object]) -> None:
        """校验回答 RAG 返回的命中是否满足回复生成最小证据契约。

        :param hits: 回答 RAG 检索器返回的知识命中列表。
        :return: 无返回值。
        :raises AnswerRagDependencyError: 任一命中缺少标题、摘要、来源或分数非法时抛出。
        """
        for index, hit in enumerate(hits):
            title = str(getattr(hit, "title", "") or "").strip()
            summary = str(getattr(hit, "summary", "") or "").strip()
            source = str(getattr(hit, "source", "") or "").strip()
            score = getattr(hit, "score", None)
            if not title or not summary or not source:
                raise AnswerRagDependencyError(
                    "answer RAG retrieval returned incomplete evidence",
                    details={
                        "index": index,
                        "has_title": bool(title),
                        "has_summary": bool(summary),
                        "has_source": bool(source),
                    },
                )
            if not isinstance(score, int | float) or score < 0 or score > 1:
                raise AnswerRagDependencyError(
                    "answer RAG retrieval returned invalid evidence score",
                    details={"index": index, "score": score},
                )
