"""
文件：src/vet_agent/clinical_safety/repository.py
作用：定义临床安全资产读取、离线导入与线上向量召回的数据仓储契约。
范围：位于临床安全候选召回数据链的数据库访问边界；业务层只能依赖本文件暴露的仓储协议。
说明：运行时候选召回只允许通过向量仓储完成；文件仓储仅服务离线导入、转换校验和测试读取，不承担线上回退召回。
"""

from __future__ import annotations

import json
from datetime import datetime
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
from .query import ClinicalSafetyRetrievalScope


PUBLISHED_REVIEW_STATUS = "approved"


class ClinicalSafetyAssetRepository(Protocol):
    """定义临床安全资产与 chunk 的只读数据访问契约。

    :return: 无返回值；该协议用于隔离数据资产读取与运行时召回职责。
    """

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取可用于临床安全裁决或离线导入的资产。

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
        """读取可用于临床安全召回或离线导入的 chunk。

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

    def is_ready(self) -> bool:
        """检查当前临床安全资产仓储是否具备读取条件。

        :return: 数据可用时返回 True，否则返回 False。
        """
        ...


class ClinicalSafetyVectorRepository(ClinicalSafetyAssetRepository, Protocol):
    """定义线上临床安全向量召回仓储契约。

    :return: 无返回值；该协议是候选召回链路唯一允许依赖的运行时数据入口。
    """

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        scope: ClinicalSafetyRetrievalScope,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """按查询向量召回临床安全 chunk。

        :param query_embedding: 已生成的查询 embedding。
        :param scope: 结构化宠物画像范围；仅用于过滤不适用资产。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回按相似度排序的 chunk 命中列表。
        """
        ...


ClinicalSafetyRepository = ClinicalSafetyVectorRepository


class FileClinicalSafetyRepository(ClinicalSafetyAssetRepository):
    """从标准 JSON 文件读取临床安全资产，服务离线导入与测试数据准备。

    :return: 无返回值；运行时召回链路不得依赖本仓储生成候选。
    """

    def __init__(self, asset_dir: Path) -> None:
        """初始化文件型临床安全仓储。

        :param asset_dir: 存放标准资产和 chunk JSON 文件的目录。
        :return: 无返回值。
        """
        self.asset_dir = asset_dir
        self._assets: list[ClinicalSafetyAsset] | None = None
        self._chunks: list[ClinicalSafetyChunk] | None = None
        self._asset_index: dict[str, ClinicalSafetyAsset] | None = None

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取临床安全资产文件内容。

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
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取临床安全 chunk 文件内容。

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
        published_only: bool = True,
    ) -> ClinicalSafetyAsset | None:
        """按资产标识读取文件中的临床安全资产。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅允许读取已审核发布的资产。
        :return: 找到时返回资产，否则返回 None。
        """
        if self._asset_index is None:
            self._asset_index = {asset.asset_id: asset for asset in self.assets(published_only=False)}
        asset = self._asset_index.get(asset_id)
        if asset is None:
            return None
        if published_only and not (
            asset.enabled and asset.review_status == PUBLISHED_REVIEW_STATUS and asset.published_at is not None
        ):
            return None
        return asset

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产关联的文件 chunk。

        :param asset_id: 临床安全资产标识。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回指定资产的安全 chunk 列表。
        """
        return [
            chunk
            for chunk in self.chunks(published_only=published_only)
            if chunk.asset_id == asset_id
        ]

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
            asset_type=self._asset_type(item.get("asset_type")),
            canonical_name=str(item.get("canonical_name", "")),
            category=str(item.get("category", "")),
            species_scope=self._tuple_of_text(item.get("species_scope", [])),
            sex_scope=self._tuple_of_text(item.get("sex_scope", [])),
            age_scope=self._tuple_of_text(item.get("age_scope", [])),
            severity=self._severity(item.get("severity")),
            action_class=self._action_class(item.get("action_class")),
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
            enabled=bool(item.get("enabled", False)),
            published_at=self._optional_datetime(item.get("published_at")),
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
            chunk_type=self._chunk_type(item.get("chunk_type")),
            title=str(item.get("title", "")),
            embedding_text=str(item.get("embedding_text", "")),
            metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata", {}), dict) else {},
            review_status=str(item.get("review_status", "pending")),
            version=str(item.get("version", "v1")),
            enabled=bool(item.get("enabled", False)),
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
        return [
            asset
            for asset in assets
            if asset.enabled and asset.review_status == PUBLISHED_REVIEW_STATUS and asset.published_at is not None
        ]

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
            if (
                chunk.enabled
                and chunk.review_status == PUBLISHED_REVIEW_STATUS
                and chunk.embedding_model is not None
                and chunk.embedding_dimension is not None
                and bool(chunk.content_hash.strip())
            )
        ]

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
        raise ValueError(f"invalid clinical safety asset_type: {value}")

    def _action_class(self, value: Any) -> ClinicalSafetyActionClass:
        """校验并返回标准安全处置动作分类。

        :param value: JSON 中声明的处置动作分类。
        :return: 返回满足契约的安全处置动作分类。
        """
        normalized = str(value).strip()
        allowed = {"emergency", "same_day_visit", "urgent_visit", "safety_warning"}
        if normalized in allowed:
            return cast(ClinicalSafetyActionClass, normalized)
        raise ValueError(f"invalid clinical safety action_class: {value}")

    def _chunk_type(self, value: Any) -> ClinicalSafetyChunkType:
        """校验并返回标准临床安全 chunk 类型。

        :param value: JSON 中声明的 chunk 类型。
        :return: 返回满足契约的 chunk 类型。
        """
        normalized = str(value).strip()
        if normalized in {"recognition", "clinical_risk", "triage_action"}:
            return cast(ClinicalSafetyChunkType, normalized)
        raise ValueError(f"invalid clinical safety chunk_type: {value}")

    def _severity(self, value: Any) -> SafetySeverity:
        """校验并返回安全规则严重级别。

        :param value: JSON 中声明的严重级别。
        :return: 返回安全严重级别。
        """
        normalized = str(value).strip().lower()
        if normalized in {"info", "caution", "urgent", "blocked"}:
            return cast(SafetySeverity, normalized)
        raise ValueError(f"invalid clinical safety severity: {value}")

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

    def _optional_datetime(self, value: Any) -> datetime | None:
        """转换可选发布时间字段。

        :param value: 原始 JSON 值。
        :return: 有效 ISO 时间字符串时返回 datetime，否则返回 None。
        """
        if isinstance(value, datetime):
            return value
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
