"""
文件：src/vet_agent/clinical_safety/postgres_repository.py
作用：基于 PostgreSQL 与 pgvector 实现 P0 临床安全资产的发布态读取和向量召回。
说明：本仓储只返回 approved 且 enabled 的安全资产，避免未审核资料进入线上高风险裁决。
"""

from __future__ import annotations

from typing import Any, Sequence, cast

from sqlalchemy import ColumnElement, func, literal, or_, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent.db import (
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    make_session_factory,
)

from .models import (
    ClinicalSafetyActionClass,
    ClinicalSafetyAsset,
    ClinicalSafetyAssetType,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    SafetySeverity,
)
from .repository import ClinicalSafetyRepository, PUBLISHED_REVIEW_STATUS


class PostgresClinicalSafetyRepository:
    """从独立临床安全表读取已发布资产，并执行 pgvector 相似度召回。"""

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 临床安全仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.database_url = database_url
        self.session_factory = make_session_factory(database_url)

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取临床安全资产。

        :param published_only: 是否仅返回已审核发布的资产。
        :return: 返回临床安全资产列表。
        """
        statement = select(ClinicalSafetyAssetModel).order_by(ClinicalSafetyAssetModel.asset_id)
        if published_only:
            statement = statement.where(*self._published_asset_filters())
        with self.session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._asset_from_row(row) for row in rows]

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取临床安全 chunk。

        :param chunk_type: 限定读取的 chunk 类型；None 表示全部类型。
        :param published_only: 是否仅返回已审核发布的 chunk。
        :return: 返回临床安全 chunk 列表。
        """
        statement = select(ClinicalSafetyChunkModel).order_by(ClinicalSafetyChunkModel.chunk_id)
        if chunk_type is not None:
            statement = statement.where(ClinicalSafetyChunkModel.chunk_type == chunk_type)
        if published_only:
            statement = statement.where(*self._published_chunk_filters())
        with self.session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._chunk_from_row(row) for row in rows]

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
        statement = select(ClinicalSafetyAssetModel).where(ClinicalSafetyAssetModel.asset_id == asset_id)
        if published_only:
            statement = statement.where(*self._published_asset_filters())
        with self.session_factory() as session:
            row = session.scalar(statement)
        return self._asset_from_row(row) if row is not None else None

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
        statement = (
            select(ClinicalSafetyChunkModel)
            .where(ClinicalSafetyChunkModel.asset_id == asset_id)
            .order_by(ClinicalSafetyChunkModel.chunk_type, ClinicalSafetyChunkModel.chunk_id)
        )
        if published_only:
            statement = statement.where(*self._published_chunk_filters())
        with self.session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._chunk_from_row(row) for row in rows]

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """通过 pgvector 余弦距离召回已发布的临床安全 chunk。

        :param query_embedding: 已生成的查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回按相似度排序的 chunk 命中列表。
        """
        if not query_embedding or not chunk_types or limit <= 0:
            return []
        embedding = [float(value) for value in query_embedding]
        distance = ClinicalSafetyChunkModel.embedding.cosine_distance(embedding)
        score = (literal(1.0) - distance).label("score")
        statement = (
            select(ClinicalSafetyChunkModel, score, distance.label("distance"))
            .join(
                ClinicalSafetyAssetModel,
                ClinicalSafetyAssetModel.asset_id == ClinicalSafetyChunkModel.asset_id,
            )
            .where(
                *self._published_chunk_filters(),
                *self._published_asset_filters(),
                ClinicalSafetyChunkModel.embedding.is_not(None),
                ClinicalSafetyChunkModel.chunk_type.in_(chunk_types),
            )
            .order_by(distance)
            .limit(limit)
        )
        with self.session_factory() as session:
            rows = session.execute(statement).all()
        return [
            ClinicalSafetyChunkHit(
                chunk=self._chunk_from_row(chunk),
                score=float(score_value or 0.0),
                distance=float(distance_value or 0.0),
                score_type="cosine_similarity",
                retrieval_source="clinical_safety_pgvector",
                embedding_model=chunk.embedding_model,
            )
            for chunk, score_value, distance_value in rows
            if float(score_value or 0.0) >= min_score
        ]

    def retrieve_text_chunk_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """在 embedding 不可用时通过 PostgreSQL trigram 执行保守文本召回。

        :param query: 用户输入与可信上下文组成的查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回按文本相关度排序的 chunk 命中列表。
        """
        if not query.strip() or not chunk_types or limit <= 0:
            return []
        query_literal = literal(query)
        title_similarity = func.similarity(func.lower(ClinicalSafetyChunkModel.title), func.lower(query_literal))
        text_similarity = func.similarity(func.lower(ClinicalSafetyChunkModel.embedding_text), func.lower(query_literal))
        score = func.greatest(title_similarity, text_similarity).label("score")
        statement = (
            select(ClinicalSafetyChunkModel, score)
            .join(
                ClinicalSafetyAssetModel,
                ClinicalSafetyAssetModel.asset_id == ClinicalSafetyChunkModel.asset_id,
            )
            .where(
                *self._published_chunk_filters(),
                *self._published_asset_filters(),
                ClinicalSafetyChunkModel.chunk_type.in_(chunk_types),
                or_(
                    func.lower(ClinicalSafetyChunkModel.title).like(func.lower(literal(f"%{query}%"))),
                    func.lower(ClinicalSafetyChunkModel.embedding_text).like(func.lower(literal(f"%{query}%"))),
                    title_similarity > 0.05,
                    text_similarity > 0.05,
                ),
            )
            .order_by(score.desc(), ClinicalSafetyChunkModel.chunk_id)
            .limit(limit)
        )
        with self.session_factory() as session:
            rows = session.execute(statement).all()
        return [
            ClinicalSafetyChunkHit(
                chunk=self._chunk_from_row(chunk),
                score=float(score_value or 0.0),
                score_type="lexical_overlap",
                retrieval_source="clinical_safety_postgres_text",
            )
            for chunk, score_value in rows
        ]

    def is_ready(self) -> bool:
        """检查是否存在可用于线上召回的已发布向量安全 chunk。

        :return: 存在已发布资产和已生成 embedding 的 chunk 时返回 True。
        """
        try:
            statement = (
                select(ClinicalSafetyChunkModel.chunk_id)
                .join(
                    ClinicalSafetyAssetModel,
                    ClinicalSafetyAssetModel.asset_id == ClinicalSafetyChunkModel.asset_id,
                )
                .where(
                    *self._published_chunk_filters(),
                    *self._published_asset_filters(),
                    ClinicalSafetyChunkModel.embedding.is_not(None),
                )
                .limit(1)
            )
            with self.session_factory() as session:
                return session.scalar(statement) is not None
        except SQLAlchemyError:
            return False

    def _published_asset_filters(self) -> tuple[ColumnElement[bool], ...]:
        """构造已发布临床安全资产的 SQL 过滤条件。

        :return: 返回资产过滤条件元组。
        """
        return (
            ClinicalSafetyAssetModel.enabled.is_(True),
            ClinicalSafetyAssetModel.review_status == PUBLISHED_REVIEW_STATUS,
        )

    def _published_chunk_filters(self) -> tuple[ColumnElement[bool], ...]:
        """构造已发布临床安全 chunk 的 SQL 过滤条件。

        :return: 返回 chunk 过滤条件元组。
        """
        return (
            ClinicalSafetyChunkModel.enabled.is_(True),
            ClinicalSafetyChunkModel.review_status == PUBLISHED_REVIEW_STATUS,
        )

    def _asset_from_row(self, row: ClinicalSafetyAssetModel) -> ClinicalSafetyAsset:
        """将数据库资产行转换为临床安全领域模型。

        :param row: 临床安全资产数据库行。
        :return: 返回临床安全资产领域模型。
        """
        return ClinicalSafetyAsset(
            asset_id=row.asset_id,
            asset_type=self._asset_type(row.asset_type),
            canonical_name=row.canonical_name,
            category=row.category,
            species_scope=tuple(row.species_scope or []),
            sex_scope=tuple(row.sex_scope or []),
            age_scope=tuple(row.age_scope or []),
            severity=self._severity(row.severity),
            action_class=self._action_class(row.action_class),
            code=row.code,
            aliases=tuple(row.aliases or []),
            carriers=tuple(row.carriers or []),
            user_expressions=tuple(row.user_expressions or []),
            symptoms=tuple(row.symptoms or []),
            recognition_phrases=tuple(row.recognition_phrases or []),
            required_context=self._required_context(row.required_context or {}),
            decision_hints=self._dict_of_text(row.decision_hints or {}),
            clinical_risk_summary=row.clinical_risk_summary,
            triage_message=row.triage_message,
            source=self._dict_of_text(row.source or {}),
            review_status=row.review_status,
            version=row.version,
            raw_text=self._dict_of_text(row.raw_text or {}),
            metadata=dict(row.metadata_json or {}),
        )

    def _chunk_from_row(self, row: ClinicalSafetyChunkModel) -> ClinicalSafetyChunk:
        """将数据库 chunk 行转换为临床安全领域模型。

        :param row: 临床安全 chunk 数据库行。
        :return: 返回临床安全 chunk 领域模型。
        """
        return ClinicalSafetyChunk(
            chunk_id=row.chunk_id,
            asset_id=row.asset_id,
            chunk_type=self._chunk_type(row.chunk_type),
            title=row.title,
            embedding_text=row.embedding_text,
            metadata=dict(row.metadata_json or {}),
            review_status=row.review_status,
            version=row.version,
            enabled=row.enabled,
            embedding_model=row.embedding_model,
            embedding_dimension=row.embedding_dimension,
            content_hash=row.content_hash,
        )

    def _asset_type(self, value: str) -> ClinicalSafetyAssetType:
        """校验数据库中的安全资产类型。

        :param value: 数据库中保存的资产类型。
        :return: 返回满足领域契约的资产类型。
        """
        if value in {
            "toxin",
            "human_drug",
            "plant_toxin",
            "chemical_toxin",
            "emergency_red_flag",
            "danger_pattern",
        }:
            return cast(ClinicalSafetyAssetType, value)
        return "danger_pattern"

    def _action_class(self, value: str) -> ClinicalSafetyActionClass:
        """校验数据库中的安全动作分类。

        :param value: 数据库中保存的动作分类。
        :return: 返回满足领域契约的动作分类。
        """
        if value in {"emergency", "same_day_visit", "urgent_visit", "safety_warning"}:
            return cast(ClinicalSafetyActionClass, value)
        return "safety_warning"

    def _chunk_type(self, value: str) -> ClinicalSafetyChunkType:
        """校验数据库中的安全 chunk 类型。

        :param value: 数据库中保存的 chunk 类型。
        :return: 返回满足领域契约的 chunk 类型。
        """
        if value in {"recognition", "clinical_risk", "triage_action"}:
            return cast(ClinicalSafetyChunkType, value)
        return "recognition"

    def _severity(self, value: str) -> SafetySeverity:
        """校验数据库中的安全严重级别。

        :param value: 数据库中保存的严重级别。
        :return: 返回满足领域契约的严重级别。
        """
        if value in {"info", "caution", "urgent", "blocked"}:
            return cast(SafetySeverity, value)
        return "caution"

    def _dict_of_text(self, value: dict[str, Any]) -> dict[str, str]:
        """将数据库 JSON 对象转换为字符串字典。

        :param value: 原始 JSON 对象。
        :return: 返回键和值均为字符串的字典。
        """
        return {str(key): str(item) for key, item in value.items()}

    def _required_context(self, value: dict[str, Any]) -> dict[str, tuple[str, ...]]:
        """将数据库 JSON 对象转换为结构化上下文提示。

        :param value: 原始 required_context JSON 对象。
        :return: 返回值为字符串元组的上下文字段字典。
        """
        result: dict[str, tuple[str, ...]] = {}
        for key, item in value.items():
            if isinstance(item, list):
                result[str(key)] = tuple(str(context_value) for context_value in item if str(context_value).strip())
            elif isinstance(item, str) and item.strip():
                result[str(key)] = (item.strip(),)
        return result
