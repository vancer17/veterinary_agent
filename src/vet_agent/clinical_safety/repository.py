"""
文件：src/vet_agent/clinical_safety/repository.py
作用：定义临床安全资产仓储契约，并提供标准 JSON 文件仓储与文本降级召回。
说明：文件仓储仅服务于离线导入、测试和数据库不可用时的保守降级；生产向量检索由 PostgreSQL 仓储实现。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol, Sequence, cast

from .models import (
    ClinicalSafetyActionClass,
    ClinicalSafetyAsset,
    ClinicalSafetyAssetType,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    SafetySeverity,
)


PUBLISHED_REVIEW_STATUS = "approved"


class ClinicalSafetyRepository(Protocol):
    """定义临床安全资产、chunk 与召回结果的数据访问契约。"""

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取可用于临床安全裁决的资产。

        :param published_only: 是否仅返回已审核发布的资产。
        :return: 返回临床安全资产列表。
        """
        ...

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取可用于候选召回的临床安全 chunk。

        :param chunk_type: 限定读取的 chunk 类型；None 表示全部类型。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回临床安全 chunk 列表。
        """
        ...

    def asset_by_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> ClinicalSafetyAsset | None:
        """按资产标识读取临床安全资产。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅允许读取已审核发布的资产。
        :return: 找到时返回资产，否则返回 None。
        """
        ...

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产关联的全部安全 chunk。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回指定资产的安全 chunk 列表。
        """
        ...

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """按查询向量召回临床安全 chunk。

        :param query_embedding: 已生成的查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回按相似度排序的 chunk 命中列表。
        """
        ...

    def retrieve_text_chunk_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """在无法使用 embedding 时执行保守文本召回。

        :param query: 用户输入与可信上下文组成的查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回按文本相关度排序的 chunk 命中列表。
        """
        ...

    def is_ready(self) -> bool:
        """检查当前临床安全仓储是否具备运行条件。

        :return: 数据可用时返回 True，否则返回 False。
        """
        ...


class FileClinicalSafetyRepository:
    """从标准 JSON 文件读取临床安全资产，并提供非生产文本降级召回。"""

    def __init__(self, asset_dir: Path) -> None:
        """初始化文件型临床安全仓储。

        :param asset_dir: 存放标准资产和 chunk JSON 文件的目录。
        :return: 无返回值。
        """
        self.asset_dir = asset_dir
        self._assets: list[ClinicalSafetyAsset] | None = None
        self._chunks: list[ClinicalSafetyChunk] | None = None
        self._asset_index: dict[str, ClinicalSafetyAsset] | None = None

    def assets(self, *, published_only: bool = False) -> list[ClinicalSafetyAsset]:
        """读取临床安全资产。

        :param published_only: 是否仅返回已审核发布的资产。
        :return: 返回临床安全资产列表。
        """
        if self._assets is None:
            raw = self._read_json("vet_safety_assets.v1.json")
            self._assets = [self._asset_from_item(item) for item in self._items(raw, "assets")]
        return self._filter_assets(self._assets, published_only=published_only)

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = False,
    ) -> list[ClinicalSafetyChunk]:
        """读取临床安全 chunk。

        :param chunk_type: 限定读取的 chunk 类型；None 表示全部类型。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回临床安全 chunk 列表。
        """
        if self._chunks is None:
            raw = self._read_json("vet_safety_chunks.v1.json")
            self._chunks = [self._chunk_from_item(item) for item in self._items(raw, "chunks")]
        chunks = self._filter_chunks(self._chunks, published_only=published_only)
        if chunk_type is None:
            return chunks
        return [chunk for chunk in chunks if chunk.chunk_type == chunk_type]

    def asset_by_id(
        self,
        asset_id: str,
        *,
        published_only: bool = False,
    ) -> ClinicalSafetyAsset | None:
        """按资产标识读取临床安全资产。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅允许读取已审核发布的资产。
        :return: 找到时返回资产，否则返回 None。
        """
        if self._asset_index is None:
            self._asset_index = {asset.asset_id: asset for asset in self.assets(published_only=False)}
        asset = self._asset_index.get(asset_id)
        if asset is None:
            return None
        if published_only and asset.review_status != PUBLISHED_REVIEW_STATUS:
            return None
        return asset

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = False,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产关联的全部安全 chunk。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回指定资产的安全 chunk 列表。
        """
        return [
            chunk
            for chunk in self.chunks(published_only=published_only)
            if chunk.asset_id == asset_id
        ]

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """返回空结果，表示文件仓储不承载生产向量检索。

        :param query_embedding: 已生成的查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 始终返回空列表。
        """
        del query_embedding, chunk_types, limit, min_score
        return []

    def retrieve_text_chunk_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """根据规范化短语重叠执行文件仓储文本降级召回。

        :param query: 用户输入与可信上下文组成的查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回按文本相关度排序的 chunk 命中列表。
        """
        normalized_query = self._normalize_text(query)
        if not normalized_query:
            return []
        hits: list[ClinicalSafetyChunkHit] = []
        for chunk in self.chunks(published_only=False):
            if chunk.chunk_type not in chunk_types:
                continue
            terms = self._overlap_terms(normalized_query, chunk)
            if not terms:
                continue
            score = min(1.0, 0.2 + 0.2 * len(terms))
            hits.append(
                ClinicalSafetyChunkHit(
                    chunk=chunk,
                    score=score,
                    score_type="lexical_overlap",
                    retrieval_source="clinical_safety_file_fallback",
                    matched_terms=terms,
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]

    def is_ready(self) -> bool:
        """检查标准资产和 chunk JSON 文件是否存在。

        :return: 两个标准文件均存在时返回 True，否则返回 False。
        """
        return (self.asset_dir / "vet_safety_assets.v1.json").exists() and (
            self.asset_dir / "vet_safety_chunks.v1.json"
        ).exists()

    def _read_json(self, filename: str) -> dict[str, Any]:
        """读取一个标准临床安全 JSON 文件。

        :param filename: 文件名。
        :return: 返回 JSON 对象。
        """
        raw = json.loads((self.asset_dir / filename).read_text(encoding="utf-8"))
        return dict(raw) if isinstance(raw, dict) else {}

    def _items(self, document: dict[str, Any], key: str) -> list[dict[str, Any]]:
        """从 JSON 文档中读取指定字段的对象列表。

        :param document: 已解析的 JSON 文档。
        :param key: 列表字段名称。
        :return: 返回字典条目列表。
        """
        raw_items = document.get(key, [])
        if not isinstance(raw_items, list):
            return []
        return [dict(item) for item in raw_items if isinstance(item, dict)]

    def _asset_from_item(self, item: dict[str, Any]) -> ClinicalSafetyAsset:
        """将 JSON 条目转换为标准临床安全资产模型。

        :param item: 原始 JSON 资产条目。
        :return: 返回标准临床安全资产模型。
        """
        return ClinicalSafetyAsset(
            asset_id=str(item.get("asset_id", "")),
            asset_type=self._asset_type(item.get("asset_type", "danger_pattern")),
            canonical_name=str(item.get("canonical_name", "")),
            category=str(item.get("category", "")),
            species_scope=self._tuple_of_text(item.get("species_scope", [])),
            sex_scope=self._tuple_of_text(item.get("sex_scope", [])),
            age_scope=self._tuple_of_text(item.get("age_scope", [])),
            severity=self._severity(item.get("severity", "caution")),
            action_class=self._action_class(item.get("action_class", "safety_warning")),
            code=str(item.get("code", "")).strip(),
            aliases=self._tuple_of_text(item.get("aliases", [])),
            carriers=self._tuple_of_text(item.get("carriers", [])),
            user_expressions=self._tuple_of_text(item.get("user_expressions", [])),
            symptoms=self._tuple_of_text(item.get("symptoms", [])),
            recognition_phrases=self._tuple_of_text(item.get("recognition_phrases", [])),
            required_context=self._required_context(item.get("required_context", {})),
            decision_hints=self._dict_of_text(item.get("decision_hints", {})),
            clinical_risk_summary=str(item.get("clinical_risk_summary", "")),
            triage_message=str(item.get("triage_message", "")),
            source=self._dict_of_text(item.get("source", {})),
            review_status=str(item.get("review_status", "pending")),
            version=str(item.get("version", "v1")),
            raw_text=self._dict_of_text(item.get("raw_text", {})),
            metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata", {}), dict) else {},
        )

    def _chunk_from_item(self, item: dict[str, Any]) -> ClinicalSafetyChunk:
        """将 JSON 条目转换为标准临床安全 chunk 模型。

        :param item: 原始 JSON chunk 条目。
        :return: 返回标准临床安全 chunk 模型。
        """
        return ClinicalSafetyChunk(
            chunk_id=str(item.get("chunk_id", "")),
            asset_id=str(item.get("asset_id", "")),
            chunk_type=self._chunk_type(item.get("chunk_type", "recognition")),
            title=str(item.get("title", "")),
            embedding_text=str(item.get("embedding_text", "")),
            metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata", {}), dict) else {},
            review_status=str(item.get("review_status", "pending")),
            version=str(item.get("version", "v1")),
            enabled=bool(item.get("enabled", True)),
            embedding_model=self._optional_text(item.get("embedding_model")),
            embedding_dimension=self._optional_int(item.get("embedding_dimension")),
            content_hash=str(item.get("content_hash", "")),
        )

    def _filter_assets(
        self,
        assets: list[ClinicalSafetyAsset],
        *,
        published_only: bool,
    ) -> list[ClinicalSafetyAsset]:
        """按审核状态筛选资产列表。

        :param assets: 原始临床安全资产列表。
        :param published_only: 是否仅保留已审核发布资产。
        :return: 返回筛选后的资产列表。
        """
        if not published_only:
            return list(assets)
        return [asset for asset in assets if asset.review_status == PUBLISHED_REVIEW_STATUS]

    def _filter_chunks(
        self,
        chunks: list[ClinicalSafetyChunk],
        *,
        published_only: bool,
    ) -> list[ClinicalSafetyChunk]:
        """按启用状态和审核状态筛选 chunk 列表。

        :param chunks: 原始临床安全 chunk 列表。
        :param published_only: 是否仅保留已审核发布 chunk。
        :return: 返回筛选后的 chunk 列表。
        """
        if not published_only:
            return list(chunks)
        return [
            chunk
            for chunk in chunks
            if chunk.enabled and chunk.review_status == PUBLISHED_REVIEW_STATUS
        ]

    def _overlap_terms(self, normalized_query: str, chunk: ClinicalSafetyChunk) -> tuple[str, ...]:
        """提取查询文本与文件 chunk 之间的精确短语交集。

        :param normalized_query: 已规范化的查询文本。
        :param chunk: 待比较的临床安全 chunk。
        :return: 返回去重后的命中短语。
        """
        terms = [
            term.strip()
            for term in re.split(r"[；;、，,。|/]+", chunk.embedding_text)
            if term.strip()
        ]
        matches: list[str] = []
        for term in terms:
            normalized_term = self._normalize_text(term)
            if len(normalized_term) >= 2 and normalized_term in normalized_query:
                matches.append(term)
        return tuple(dict.fromkeys(matches[:8]))

    def _normalize_text(self, text: str) -> str:
        """规范化参与文件文本召回的输入。

        :param text: 原始文本。
        :return: 返回小写且移除空白后的文本。
        """
        return re.sub(r"\s+", "", text.lower())

    def _tuple_of_text(self, value: Any) -> tuple[str, ...]:
        """将 JSON 字段转换为字符串元组。

        :param value: 原始 JSON 值。
        :return: 返回字符串元组。
        """
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, str) and value.strip():
            return (value.strip(),)
        return ()

    def _dict_of_text(self, value: Any) -> dict[str, str]:
        """将 JSON 对象转换为字符串字典。

        :param value: 原始 JSON 值。
        :return: 返回键和值均为字符串的字典。
        """
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items()}

    def _required_context(self, value: Any) -> dict[str, tuple[str, ...]]:
        """转换资产所需上下文字段。

        :param value: 原始 required_context JSON 值。
        :return: 返回值为字符串元组的上下文字段字典。
        """
        if not isinstance(value, dict):
            return {}
        return {str(key): self._tuple_of_text(item) for key, item in value.items()}

    def _asset_type(self, value: Any) -> ClinicalSafetyAssetType:
        """校验并返回标准安全资产类型。

        :param value: JSON 中声明的资产类型。
        :return: 返回满足契约的安全资产类型。
        """
        normalized = str(value).strip()
        allowed = {
            "toxin",
            "human_drug",
            "plant_toxin",
            "chemical_toxin",
            "emergency_red_flag",
            "danger_pattern",
        }
        if normalized in allowed:
            return cast(ClinicalSafetyAssetType, normalized)
        return "danger_pattern"

    def _action_class(self, value: Any) -> ClinicalSafetyActionClass:
        """校验并返回标准安全处置动作分类。

        :param value: JSON 中声明的处置动作分类。
        :return: 返回满足契约的安全处置动作分类。
        """
        normalized = str(value).strip()
        allowed = {"emergency", "same_day_visit", "urgent_visit", "safety_warning"}
        if normalized in allowed:
            return cast(ClinicalSafetyActionClass, normalized)
        return "safety_warning"

    def _chunk_type(self, value: Any) -> ClinicalSafetyChunkType:
        """校验并返回标准临床安全 chunk 类型。

        :param value: JSON 中声明的 chunk 类型。
        :return: 返回满足契约的 chunk 类型。
        """
        normalized = str(value).strip()
        if normalized in {"recognition", "clinical_risk", "triage_action"}:
            return cast(ClinicalSafetyChunkType, normalized)
        return "recognition"

    def _severity(self, value: Any) -> SafetySeverity:
        """校验并返回安全规则严重级别。

        :param value: JSON 中声明的严重级别。
        :return: 返回安全严重级别。
        """
        normalized = str(value).strip().lower()
        if normalized in {"info", "caution", "urgent", "blocked"}:
            return cast(SafetySeverity, normalized)
        return "caution"

    def _optional_text(self, value: Any) -> str | None:
        """转换可选文本字段。

        :param value: 原始 JSON 值。
        :return: 有效文本时返回字符串，否则返回 None。
        """
        text = str(value or "").strip()
        return text or None

    def _optional_int(self, value: Any) -> int | None:
        """转换可选整数字段。

        :param value: 原始 JSON 值。
        :return: 可解析时返回整数，否则返回 None。
        """
        if isinstance(value, bool):
            return None
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class FallbackClinicalSafetyRepository:
    """组合数据库仓储与文件仓储，提供数据库优先的安全数据访问。"""

    def __init__(
        self,
        primary: ClinicalSafetyRepository,
        fallback: ClinicalSafetyRepository,
    ) -> None:
        """初始化数据库优先的组合仓储。

        :param primary: 优先使用的生产仓储。
        :param fallback: 主仓储不可用时使用的文件仓储。
        :return: 无返回值。
        """
        self.primary = primary
        self.fallback = fallback

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取临床安全资产，并在主仓储无数据时回退。

        :param published_only: 是否仅返回已审核发布的资产。
        :return: 返回临床安全资产列表。
        """
        try:
            primary_assets = self.primary.assets(published_only=published_only)
            if primary_assets:
                return primary_assets
        except Exception:
            pass
        return self.fallback.assets(published_only=published_only)

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取临床安全 chunk，并在主仓储无数据时回退。

        :param chunk_type: 限定读取的 chunk 类型；None 表示全部类型。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回临床安全 chunk 列表。
        """
        try:
            primary_chunks = self.primary.chunks(
                chunk_type=chunk_type,
                published_only=published_only,
            )
            if primary_chunks:
                return primary_chunks
        except Exception:
            pass
        return self.fallback.chunks(chunk_type=chunk_type, published_only=published_only)

    def asset_by_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> ClinicalSafetyAsset | None:
        """按资产标识读取临床安全资产，并在主仓储无结果时回退。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅允许读取已审核发布的资产。
        :return: 找到时返回资产，否则返回 None。
        """
        try:
            primary_asset = self.primary.asset_by_id(asset_id, published_only=published_only)
            if primary_asset is not None:
                return primary_asset
        except Exception:
            pass
        return self.fallback.asset_by_id(asset_id, published_only=published_only)

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产的 chunk，并在主仓储无结果时回退。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回指定资产的安全 chunk 列表。
        """
        try:
            primary_chunks = self.primary.chunks_by_asset_id(
                asset_id,
                published_only=published_only,
            )
            if primary_chunks:
                return primary_chunks
        except Exception:
            pass
        return self.fallback.chunks_by_asset_id(asset_id, published_only=published_only)

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """从主仓储执行向量召回，不将文件数据伪装成向量结果。

        :param query_embedding: 已生成的查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回主仓储向量召回命中。
        """
        try:
            return self.primary.retrieve_vector_chunk_hits(
                query_embedding,
                chunk_types=chunk_types,
                limit=limit,
                min_score=min_score,
            )
        except Exception:
            return []

    def retrieve_text_chunk_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """执行数据库文本召回，失败或无命中时回退到标准文件。

        :param query: 用户输入与可信上下文组成的查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回文本召回命中。
        """
        try:
            primary_hits = self.primary.retrieve_text_chunk_hits(
                query,
                chunk_types=chunk_types,
                limit=limit,
            )
            if primary_hits:
                return primary_hits
        except Exception:
            pass
        return self.fallback.retrieve_text_chunk_hits(
            query,
            chunk_types=chunk_types,
            limit=limit,
        )

    def is_ready(self) -> bool:
        """检查主仓储或文件回退仓储是否可用。

        :return: 任一仓储具备安全数据时返回 True。
        """
        try:
            if self.primary.is_ready():
                return True
        except Exception:
            pass
        return self.fallback.is_ready()
