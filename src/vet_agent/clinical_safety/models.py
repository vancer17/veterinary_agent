"""
文件：src/vet_agent/clinical_safety/models.py
作用：定义 P0 临床安全资产、向量 chunk、召回命中与候选聚合模型。
说明：本文件只承载安全领域数据契约，不承担数据库访问、模型调用或根据医学关键词推导资产编码。
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Literal


SafetySeverity = Literal["info", "caution", "urgent", "blocked"]
ClinicalSafetyAssetType = Literal[
    "toxin",
    "human_drug",
    "plant_toxin",
    "chemical_toxin",
    "emergency_red_flag",
    "danger_pattern",
]
ClinicalSafetyActionClass = Literal[
    "emergency",
    "same_day_visit",
    "urgent_visit",
    "safety_warning",
]
ClinicalSafetyChunkType = Literal["recognition", "clinical_risk", "triage_action"]
ClinicalSafetyScoreType = Literal["cosine_similarity"]


@dataclass(frozen=True)
class ClinicalSafetyAsset:
    """表示标准化后的 P0 临床安全资产。

    :param asset_id: 稳定资产标识。
    :param asset_type: 安全资产类型。
    :param canonical_name: 规范名称。
    :param category: 原始临床分类。
    :param species_scope: 适用物种范围。
    :param sex_scope: 适用性别范围。
    :param age_scope: 适用年龄范围。
    :param severity: 安全严重级别。
    :param action_class: 分诊动作分类。
    :param code: 对外安全信号编码。
    :param aliases: 别名、英文名、商品名或俗称。
    :param carriers: 风险载体或暴露来源。
    :param user_expressions: 用户常见表达。
    :param symptoms: 症状或风险线索。
    :param recognition_phrases: 用于候选召回的完整组合短语与原子短语。
    :param required_context: 用于裁决的结构化上下文提示。
    :param decision_hints: 不同用户意图下的动作提示。
    :param clinical_risk_summary: 临床风险摘要。
    :param triage_message: 分诊处置口径。
    :param source: 来源追踪信息。
    :param review_status: 审核状态。
    :param version: 资产版本。
    :param enabled: 是否允许运行时召回。
    :param published_at: 资产发布时间。
    :param raw_text: 原始长文本字段备份。
    :param metadata: 附加元数据。
    :return: 无返回值。
    """

    asset_id: str
    asset_type: ClinicalSafetyAssetType
    canonical_name: str
    category: str
    species_scope: tuple[str, ...]
    sex_scope: tuple[str, ...]
    age_scope: tuple[str, ...]
    severity: SafetySeverity
    action_class: ClinicalSafetyActionClass
    code: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    carriers: tuple[str, ...] = field(default_factory=tuple)
    user_expressions: tuple[str, ...] = field(default_factory=tuple)
    symptoms: tuple[str, ...] = field(default_factory=tuple)
    recognition_phrases: tuple[str, ...] = field(default_factory=tuple)
    required_context: dict[str, tuple[str, ...]] = field(default_factory=dict)
    decision_hints: dict[str, str] = field(default_factory=dict)
    clinical_risk_summary: str = ""
    triage_message: str = ""
    source: dict[str, str] = field(default_factory=dict)
    review_status: str = "pending"
    version: str = "v1"
    enabled: bool = False
    published_at: datetime | None = None
    raw_text: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验临床安全资产运行时最小契约。

        :return: 无返回值；校验通过表示资产具备候选召回链路所需的稳定标识和编码。
        :raises ValueError: 资产缺少稳定标识或编码时抛出，避免运行时根据医学名称补齐。
        """
        asset_id = self.asset_id.strip()
        if not asset_id:
            raise ValueError("clinical safety asset_id is required")
        code = self.code.strip()
        if not code:
            raise ValueError("clinical safety asset code is required")
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(self, "code", code)

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入 JSON 的标准资产字典。

        :return: 返回标准临床安全资产字典。
        """
        return {
            "asset_id": self.asset_id,
            "code": self.code,
            "asset_type": self.asset_type,
            "canonical_name": self.canonical_name,
            "category": self.category,
            "species_scope": list(self.species_scope),
            "sex_scope": list(self.sex_scope),
            "age_scope": list(self.age_scope),
            "severity": self.severity,
            "action_class": self.action_class,
            "aliases": list(self.aliases),
            "carriers": list(self.carriers),
            "user_expressions": list(self.user_expressions),
            "symptoms": list(self.symptoms),
            "recognition_phrases": list(self.recognition_phrases),
            "required_context": {key: list(value) for key, value in self.required_context.items()},
            "decision_hints": dict(self.decision_hints),
            "clinical_risk_summary": self.clinical_risk_summary,
            "triage_message": self.triage_message,
            "source": dict(self.source),
            "review_status": self.review_status,
            "version": self.version,
            "enabled": self.enabled,
            "published_at": self.published_at.isoformat() if self.published_at is not None else None,
            "raw_text": dict(self.raw_text),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ClinicalSafetyChunk:
    """表示由标准安全资产派生的向量检索片段。

    :param chunk_id: 稳定片段标识。
    :param asset_id: 关联的标准安全资产标识。
    :param chunk_type: 片段用途类型。
    :param title: 片段标题。
    :param embedding_text: 用于生成向量的文本。
    :param metadata: 片段元数据。
    :param review_status: 审核状态。
    :param version: 片段版本。
    :param enabled: 是否允许运行时召回。
    :param embedding_model: 生成向量所用模型名称。
    :param embedding_dimension: 向量维度。
    :param content_hash: embedding 文本内容哈希。
    :return: 无返回值。
    """

    chunk_id: str
    asset_id: str
    chunk_type: ClinicalSafetyChunkType
    title: str
    embedding_text: str
    metadata: dict[str, Any]
    review_status: str = "pending"
    version: str = "v1"
    enabled: bool = False
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入 JSON 的向量检索片段字典。

        :return: 返回标准临床安全向量片段字典。
        """
        return {
            "chunk_id": self.chunk_id,
            "asset_id": self.asset_id,
            "chunk_type": self.chunk_type,
            "title": self.title,
            "embedding_text": self.embedding_text,
            "metadata": dict(self.metadata),
            "review_status": self.review_status,
            "version": self.version,
            "enabled": self.enabled,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class ClinicalSafetyChunkHit:
    """表示一次 pgvector 向量召回命中的安全 chunk。

    :param chunk: 被召回的临床安全向量片段。
    :param score: 查询与片段之间的排序分数。
    :param distance: pgvector 余弦距离。
    :param score_type: 分数计算方式。
    :param retrieval_source: 命中来源。
    :param embedding_model: 查询或 chunk 使用的 embedding 模型。
    :param matched_terms: 用于审计展示的命中特征词。
    :return: 无返回值。
    """

    chunk: ClinicalSafetyChunk
    score: float
    distance: float = 0.0
    score_type: ClinicalSafetyScoreType = "cosine_similarity"
    retrieval_source: str = "clinical_safety_pgvector"
    embedding_model: str | None = None
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ClinicalSafetyCandidate:
    """表示按安全资产聚合后的临床安全候选。

    :param asset: 标准化临床安全资产。
    :param score: 该资产下召回 chunk 的最高分数。
    :param chunk_hits: 该资产关联的召回 chunk 命中列表。
    :param score_type: 候选分数计算方式。
    :param retrieval_source: 候选来源。
    :return: 无返回值。
    """

    asset: ClinicalSafetyAsset
    score: float
    chunk_hits: tuple[ClinicalSafetyChunkHit, ...]
    score_type: ClinicalSafetyScoreType = "cosine_similarity"
    retrieval_source: str = "clinical_safety_pgvector"

    def matched_terms(self) -> tuple[str, ...]:
        """汇总候选资产下所有召回片段的审计命中特征词。

        :return: 返回去重后的命中特征词元组。
        """
        terms: list[str] = []
        for hit in self.chunk_hits:
            terms.extend(hit.matched_terms)
        return tuple(dict.fromkeys(term for term in terms if term))
