"""
文件：src/vet_agent/clinical_safety/retriever.py
作用：基于 embedding 和临床安全独立表执行 P0 安全候选召回。
说明：生产路径优先使用 pgvector；仅在 embedding 或数据库不可用时使用标准语料的保守文本回退。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from vet_agent.runtime import EmbeddingClient

from .fallback import (
    ClinicalSafetyRetrievalResult,
    ClinicalSafetyRetrievalStage,
    ClinicalSafetyRetrievalState,
)
from .models import (
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    ClinicalSafetyScoreType,
)
from .repository import ClinicalSafetyRepository
from .thresholds import ClinicalSafetyThresholds


DEFAULT_CHUNK_TYPES: tuple[ClinicalSafetyChunkType, ...] = (
    "recognition",
    "clinical_risk",
)


class ClinicalSafetyRetriever:
    """对标准临床安全 chunk 执行运行时向量召回和候选聚合。"""

    def __init__(
        self,
        repository: ClinicalSafetyRepository,
        embedding_client: EmbeddingClient | None = None,
        *,
        thresholds: ClinicalSafetyThresholds | None = None,
        min_score: float | None = None,
    ) -> None:
        """初始化临床安全召回器。

        :param repository: 标准临床安全资产仓储。
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
                lexical_min_terms=effective_thresholds.lexical_min_terms,
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
        """召回临床安全候选，并显式返回本轮召回回退状态。

        :param query: 已合并用户输入与可信上下文的安全查询文本。
        :param limit: 返回资产候选数量上限。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :return: 返回候选列表和召回回退状态。
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
        if vector_hits:
            vector_candidates = self._aggregate_candidates(query, vector_hits, limit=limit)
            if vector_candidates:
                return ClinicalSafetyRetrievalResult(
                    candidates=vector_candidates,
                    state=ClinicalSafetyRetrievalState(
                        stage="vector",
                        degraded=False,
                        reasons=(),
                        retrieval_source=self._primary_retrieval_source(vector_hits),
                        vector_hit_count=len(vector_hits),
                        candidate_count=len(vector_candidates),
                    ),
                )
            vector_reasons = self._compact_reasons([*vector_reasons, "vector_candidate_count_zero"])

        fallback_hits, fallback_reasons = self._retrieve_fallback_hits(
            query,
            chunk_types=chunk_types,
            limit=limit * 3,
        )
        reasons = self._compact_reasons([*vector_reasons, *fallback_reasons])
        if not fallback_hits:
            return ClinicalSafetyRetrievalResult(
                candidates=[],
                state=ClinicalSafetyRetrievalState(
                    stage="none",
                    degraded=bool(reasons),
                    reasons=self._compact_reasons([*reasons, "clinical_safety_retrieval_empty"]),
                    vector_hit_count=len(vector_hits),
                    candidate_count=0,
                ),
            )

        candidates = self._aggregate_candidates(query, fallback_hits, limit=limit)
        stage = self._retrieval_stage_from_hits(fallback_hits)
        candidate_reasons = (
            reasons
            if candidates
            else self._compact_reasons([*reasons, "fallback_candidate_count_zero"])
        )
        return ClinicalSafetyRetrievalResult(
            candidates=candidates,
            state=ClinicalSafetyRetrievalState(
                stage=stage,
                degraded=stage != "vector",
                reasons=candidate_reasons,
                retrieval_source=self._primary_retrieval_source(fallback_hits),
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
        """调用 embedding 客户端和仓储执行生产向量召回，并返回失败原因。

        :param query: 待召回的安全查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回向量召回命中和未命中原因。
        """
        if self.embedding_client is None:
            return [], ("embedding_client_unavailable",)
        if not self.embedding_client.available:
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

    def _retrieve_fallback_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> tuple[list[ClinicalSafetyChunkHit], tuple[str, ...]]:
        """在向量能力不可用时执行标准语料文本回退召回。

        :param query: 待召回的安全查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回文件或数据库文本召回命中和回退原因。
        """
        text_hits: list[ClinicalSafetyChunkHit] = []
        reasons: list[str] = []
        try:
            text_hits = self.repository.retrieve_text_chunk_hits(
                query,
                chunk_types=chunk_types,
                limit=limit,
            )
            if not text_hits:
                reasons.append("text_hit_count_zero")
        except Exception as exc:
            reasons.append(f"text_retrieval_failed:{type(exc).__name__}")
        asset_hits = self._asset_fallback_hits(query, chunk_types=chunk_types, limit=limit)
        if asset_hits:
            reasons.append("asset_fallback_used")
        elif not text_hits:
            reasons.append("asset_fallback_hit_count_zero")
        hit_by_key: dict[tuple[str, str], ClinicalSafetyChunkHit] = {}
        for hit in [*asset_hits, *text_hits]:
            key = (hit.chunk.asset_id, hit.chunk.chunk_id)
            existing = hit_by_key.get(key)
            if existing is None or hit.score > existing.score:
                hit_by_key[key] = hit
        hits = sorted(hit_by_key.values(), key=lambda item: item.score, reverse=True)[:limit]
        return hits, self._compact_reasons(reasons)

    def _asset_fallback_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """按已发布资产的结构化短语生成离线保守回退命中。

        :param query: 待匹配的安全查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回结构化短语回退命中。
        """
        normalized_query = self._normalize_text(query)
        if not normalized_query:
            return []
        hits: list[ClinicalSafetyChunkHit] = []
        try:
            assets = self.repository.assets(published_only=False)
        except Exception:
            return []
        for asset in assets:
            matched_terms = self._asset_matched_terms(asset, normalized_query)
            if not matched_terms:
                continue
            try:
                chunks = self.repository.chunks_by_asset_id(asset.asset_id, published_only=True)
            except Exception:
                continue
            selected = next((chunk for chunk in chunks if chunk.chunk_type in chunk_types), None)
            if selected is None:
                continue
            score = min(0.99, 0.25 + 0.12 * len(matched_terms))
            hits.append(
                ClinicalSafetyChunkHit(
                    chunk=selected,
                    score=score,
                    score_type="lexical_overlap",
                    retrieval_source="clinical_safety_asset_fallback",
                    matched_terms=matched_terms,
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]

    def _aggregate_candidates(
        self,
        query: str,
        hits: list[ClinicalSafetyChunkHit],
        *,
        limit: int,
    ) -> list[ClinicalSafetyCandidate]:
        """按安全资产聚合 chunk 命中并补充审计命中词。

        :param query: 原始安全查询文本。
        :param hits: chunk 级召回命中。
        :param limit: 返回资产候选数量上限。
        :return: 返回聚合后的临床安全候选。
        """
        normalized_query = self._normalize_text(query)
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
            matched_terms = self._asset_matched_terms(asset, normalized_query)
            asset_hits = [replace(hit, matched_terms=matched_terms) for hit in raw_asset_hits]
            vector_hits = [hit for hit in asset_hits if hit.score_type == "cosine_similarity"]
            if not matched_terms and not vector_hits:
                continue
            primary_hits = vector_hits or asset_hits
            candidates.append(
                ClinicalSafetyCandidate(
                    asset=asset,
                    score=max(hit.score for hit in primary_hits),
                    chunk_hits=tuple(sorted(asset_hits, key=lambda item: item.score, reverse=True)),
                    score_type="cosine_similarity" if vector_hits else self._score_type(primary_hits),
                    retrieval_source=self._retrieval_source(primary_hits),
                )
            )
        return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]

    def _asset_matched_terms(
        self,
        asset: ClinicalSafetyAsset,
        normalized_query: str,
    ) -> tuple[str, ...]:
        """提取资产结构化字段与查询之间的命中短语。

        :param asset: 待匹配的临床安全资产。
        :param normalized_query: 已规范化的查询文本。
        :return: 返回去重后的命中短语。
        """
        terms = (
            asset.canonical_name,
            *asset.aliases,
            *asset.carriers,
            *asset.user_expressions,
            *asset.symptoms,
            *asset.recognition_phrases,
        )
        matches = [
            term.strip()
            for term in terms
            if term.strip() and self._term_matches_query(term, normalized_query)
        ]
        return tuple(dict.fromkeys(matches))[:8]

    def _term_matches_query(self, term: str, normalized_query: str) -> bool:
        """判断一个安全短语是否出现在查询或其近似表达中。

        :param term: 待匹配的安全短语。
        :param normalized_query: 已规范化的查询文本。
        :return: 查询包含该短语或短语仅发生语序变化时返回 True。
        """
        normalized_term = self._normalize_text(term)
        if len(normalized_term) < 2:
            return False
        if normalized_term in normalized_query:
            return True
        if len(normalized_term) < 4 or len(normalized_term) > 12 or not self._is_cjk_text(normalized_term):
            return False
        return self._has_reordered_phrase_match(normalized_term, normalized_query)

    def _has_reordered_phrase_match(self, normalized_term: str, normalized_query: str) -> bool:
        """判断连续中文短语是否仅发生有限的词序互换。

        :param normalized_term: 已规范化的资产短语。
        :param normalized_query: 已规范化的查询文本。
        :return: 存在字符多重集合一致的同长度连续窗口时返回 True。
        """
        if len(normalized_query) < len(normalized_term):
            return False
        expected_characters = sorted(normalized_term)
        term_length = len(normalized_term)
        return any(
            sorted(normalized_query[index : index + term_length]) == expected_characters
            for index in range(len(normalized_query) - term_length + 1)
        )

    def _score_type(self, hits: list[ClinicalSafetyChunkHit]) -> ClinicalSafetyScoreType:
        """确定候选使用的主召回分数类型。

        :param hits: 候选下的 chunk 命中列表。
        :return: 返回向量或文本召回类型标识。
        """
        return "cosine_similarity" if any(hit.score_type == "cosine_similarity" for hit in hits) else "lexical_overlap"

    def _retrieval_source(self, hits: list[ClinicalSafetyChunkHit]) -> str:
        """确定候选使用的主召回来源。

        :param hits: 候选下的 chunk 命中列表。
        :return: 返回召回来源标识。
        """
        return next(
            (hit.retrieval_source for hit in hits if hit.score_type == "cosine_similarity"),
            hits[0].retrieval_source,
        )

    def _normalize_text(self, text: str) -> str:
        """规范化参与安全召回的文本。

        :param text: 原始文本。
        :return: 返回小写且移除空白后的文本。
        """
        return re.sub(r"\s+", "", text.lower())

    def _is_cjk_text(self, text: str) -> bool:
        """判断短语是否主要由中文字符构成。

        :param text: 待判断文本。
        :return: 由中文字符构成时返回 True，否则返回 False。
        """
        return bool(text) and all("\u4e00" <= character <= "\u9fff" for character in text)

    def _retrieval_stage_from_hits(self, hits: list[ClinicalSafetyChunkHit]) -> ClinicalSafetyRetrievalStage:
        """根据 chunk 命中来源判断本轮召回档位。

        :param hits: 召回得到的 chunk 命中列表。
        :return: 返回召回档位。
        """
        sources = {hit.retrieval_source for hit in hits if hit.retrieval_source}
        if "clinical_safety_pgvector" in sources:
            return "vector"
        if "clinical_safety_postgres_text" in sources:
            return "postgres_text"
        if "clinical_safety_file_fallback" in sources:
            return "file_text"
        if "clinical_safety_asset_fallback" in sources:
            return "asset_fallback"
        return "none"

    def _primary_retrieval_source(self, hits: list[ClinicalSafetyChunkHit]) -> str:
        """选择本轮召回中最主要的来源标识。

        :param hits: 召回得到的 chunk 命中列表。
        :return: 返回主来源标识。
        """
        priority = (
            "clinical_safety_pgvector",
            "clinical_safety_postgres_text",
            "clinical_safety_file_fallback",
            "clinical_safety_asset_fallback",
        )
        sources = [hit.retrieval_source for hit in hits if hit.retrieval_source]
        for source in priority:
            if source in sources:
                return source
        return sources[0] if sources else ""

    def _compact_reasons(self, reasons: list[str]) -> tuple[str, ...]:
        """压缩回退原因并移除重复项。

        :param reasons: 原始原因列表。
        :return: 返回去重后的原因元组。
        """
        return tuple(dict.fromkeys(reason for reason in reasons if reason))
