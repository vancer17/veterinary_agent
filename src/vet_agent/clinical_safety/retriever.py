"""
文件：src/vet_agent/clinical_safety/retriever.py
作用：对临床安全 chunk 执行运行时向量召回并聚合为资产候选。
范围：位于临床安全候选召回数据链的运行时入口，只允许使用 pgvector 结果生成候选，不承担文本短语回退或资产短语伪召回。
说明：当 embedding 或数据库不可用时，本层只返回显式降级状态和空候选，避免将静态资产规则伪装为线上召回结果。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from vet_agent.runtime import EmbeddingClient

from .fallback import ClinicalSafetyRetrievalResult, ClinicalSafetyRetrievalState
from .models import (
    ClinicalSafetyCandidate,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    ClinicalSafetyScoreType,
)
from .repository import ClinicalSafetyVectorRepository
from .thresholds import ClinicalSafetyThresholds


DEFAULT_CHUNK_TYPES: tuple[ClinicalSafetyChunkType, ...] = (
    "recognition",
    "clinical_risk",
)


class ClinicalSafetyRetriever:
    """对标准临床安全 chunk 执行运行时向量召回和候选聚合。"""

    def __init__(
        self,
        repository: ClinicalSafetyVectorRepository,
        embedding_client: EmbeddingClient | None = None,
        *,
        thresholds: ClinicalSafetyThresholds | None = None,
        min_score: float | None = None,
    ) -> None:
        """初始化临床安全召回器。

        :param repository: 标准临床安全向量仓储。
        :param embedding_client: 可选的 embedding 客户端。
        :param thresholds: 临床安全阈值对象。
        :param min_score: 向量召回的最低相似度分数；优先级高于 thresholds.retrieval_min_score。
        :return: 无返回值。
        """
        self.repository = repository
        self.embedding_client = embedding_client
        effective_thresholds = thresholds or ClinicalSafetyThresholds()
        if min_score is not None:
            effective_thresholds = ClinicalSafetyThresholds(
                retrieval_min_score=min_score,
                signal_min_score=effective_thresholds.signal_min_score,
                urgent_min_score=effective_thresholds.urgent_min_score,
            )
        self.thresholds = effective_thresholds
        self.min_score = self.thresholds.retrieval_min_score

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 12,
        chunk_types: tuple[ClinicalSafetyChunkType, ...] = DEFAULT_CHUNK_TYPES,
    ) -> list[ClinicalSafetyCandidate]:
        """召回与查询最相关的临床安全资产候选。

        :param query: 已合并用户输入与可信上下文的安全查询文本。
        :param limit: 返回资产候选数量上限。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :return: 返回按相似度排序的临床安全候选列表。
        """
        return self.retrieve_with_resolution(
            query,
            limit=limit,
            chunk_types=chunk_types,
        ).candidates

    def retrieve_with_resolution(
        self,
        query: str,
        *,
        limit: int = 12,
        chunk_types: tuple[ClinicalSafetyChunkType, ...] = DEFAULT_CHUNK_TYPES,
    ) -> ClinicalSafetyRetrievalResult:
        """召回临床安全候选，并显式返回本轮召回状态。

        :param query: 已合并用户输入与可信上下文的安全查询文本。
        :param limit: 返回资产候选数量上限。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :return: 返回候选列表和召回状态。
        """
        if not query.strip() or limit <= 0 or not chunk_types:
            reason = "empty_query" if not query.strip() else "invalid_retrieval_arguments"
            return ClinicalSafetyRetrievalResult(
                candidates=[],
                state=ClinicalSafetyRetrievalState(
                    stage="none",
                    degraded=False,
                    reasons=(reason,),
                ),
            )

        vector_hits, vector_reasons = self._retrieve_vector_hits(
            query,
            chunk_types=chunk_types,
            limit=limit * 3,
        )
        if not vector_hits:
            return ClinicalSafetyRetrievalResult(
                candidates=[],
                state=ClinicalSafetyRetrievalState(
                    stage="none",
                    degraded=bool(vector_reasons),
                    reasons=self._compact_reasons([*vector_reasons, "clinical_safety_retrieval_empty"]),
                    vector_hit_count=0,
                    candidate_count=0,
                ),
            )

        candidates = self._aggregate_candidates(vector_hits, limit=limit)
        if not candidates:
            return ClinicalSafetyRetrievalResult(
                candidates=[],
                state=ClinicalSafetyRetrievalState(
                    stage="none",
                    degraded=True,
                    reasons=self._compact_reasons([*vector_reasons, "vector_candidate_count_zero"]),
                    vector_hit_count=len(vector_hits),
                    candidate_count=0,
                ),
            )

        return ClinicalSafetyRetrievalResult(
            candidates=candidates,
            state=ClinicalSafetyRetrievalState(
                stage="vector",
                degraded=False,
                reasons=(),
                retrieval_source=self._primary_retrieval_source(vector_hits),
                vector_hit_count=len(vector_hits),
                candidate_count=len(candidates),
            ),
        )

    def _retrieve_vector_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> tuple[list[ClinicalSafetyChunkHit], tuple[str, ...]]:
        """调用 embedding 客户端和仓储执行生产向量召回。

        :param query: 待召回的安全查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回向量召回命中和未命中原因。
        """
        if self.embedding_client is None or not self.embedding_client.available:
            return [], ("embedding_client_unavailable",)
        try:
            query_embedding = self.embedding_client.embed(query)
        except Exception as exc:
            return [], (f"embedding_generation_failed:{type(exc).__name__}",)
        if not query_embedding:
            return [], ("query_embedding_empty",)
        try:
            hits = self.repository.retrieve_vector_chunk_hits(
                query_embedding,
                chunk_types=chunk_types,
                limit=limit,
                min_score=self.thresholds.retrieval_min_score,
            )
        except Exception as exc:
            return [], (f"vector_retrieval_failed:{type(exc).__name__}",)
        if not hits:
            return [], ("vector_hit_count_zero",)
        return hits, ()

    def _aggregate_candidates(
        self,
        hits: list[ClinicalSafetyChunkHit],
        *,
        limit: int,
    ) -> list[ClinicalSafetyCandidate]:
        """按安全资产聚合 chunk 命中并补充审计命中词。

        :param hits: chunk 级召回命中。
        :param limit: 返回资产候选数量上限。
        :return: 返回聚合后的临床安全候选。
        """
        grouped_hits: dict[str, list[ClinicalSafetyChunkHit]] = defaultdict(list)
        for hit in hits:
            grouped_hits[hit.chunk.asset_id].append(hit)

        candidates: list[ClinicalSafetyCandidate] = []
        for asset_id, raw_asset_hits in grouped_hits.items():
            try:
                asset = self.repository.asset_by_id(asset_id, published_only=True)
            except Exception:
                asset = None
            if asset is None:
                continue
            asset_hits = [replace(hit, matched_terms=self._matched_terms_from_hit(hit)) for hit in raw_asset_hits]
            candidates.append(
                ClinicalSafetyCandidate(
                    asset=asset,
                    score=max(hit.score for hit in asset_hits),
                    chunk_hits=tuple(sorted(asset_hits, key=lambda item: item.score, reverse=True)),
                    score_type=self._score_type(asset_hits),
                    retrieval_source=self._retrieval_source(asset_hits),
                )
            )
        return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]

    def _matched_terms_from_hit(self, hit: ClinicalSafetyChunkHit) -> tuple[str, ...]:
        """从 chunk 命中中提取审计命中词。

        :param hit: 单个 chunk 命中。
        :return: 返回命中词元组。
        """
        return tuple(dict.fromkeys(term for term in hit.matched_terms if term))

    def _score_type(self, hits: list[ClinicalSafetyChunkHit]) -> ClinicalSafetyScoreType:
        """确定候选使用的主召回分数类型。

        :param hits: 候选下的 chunk 命中列表。
        :return: 返回向量召回类型标识。
        """
        return "cosine_similarity"

    def _retrieval_source(self, hits: list[ClinicalSafetyChunkHit]) -> str:
        """确定候选使用的主召回来源。

        :param hits: 候选下的 chunk 命中列表。
        :return: 返回召回来源标识。
        """
        return next((hit.retrieval_source for hit in hits if hit.retrieval_source), "clinical_safety_pgvector")

    def _primary_retrieval_source(self, hits: list[ClinicalSafetyChunkHit]) -> str:
        """选择本轮召回中最主要的来源标识。

        :param hits: 召回得到的 chunk 命中列表。
        :return: 返回主来源标识。
        """
        return self._retrieval_source(hits)

    def _compact_reasons(self, reasons: list[str]) -> tuple[str, ...]:
        """压缩回退原因并移除重复项。

        :param reasons: 原始原因列表。
        :return: 返回去重后的原因元组。
        """
        return tuple(dict.fromkeys(reason for reason in reasons if reason))
