"""
文件：src/vet_agent/db/models.py
作用：提供数据库模型、连接与会话管理能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class InputSafetyCandidateDefinitionModel(Base):
    __tablename__ = "input_safety_candidate_definitions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_input_safety_candidate_definitions_code"),
        CheckConstraint(
            "default_severity IN ('info', 'caution', 'urgent', 'blocked')",
            name="ck_input_safety_candidate_definitions_severity",
        ),
        {
            "comment": "基础输入安全候选定义表，用于描述结构化检测器输出的策略语义，不保存文本关键词规则。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="候选定义内部主键。")
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="候选编码，用于 OPA 策略、审计和安全信号关联。")
    category: Mapped[str] = mapped_column(Text, nullable=False, comment="候选类别，例如完整性、提示注入、未开放能力或业务范围候选。")
    default_severity: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="caution",
        server_default="caution",
        comment="候选默认严重级别；最终动作和信号级别由 OPA 裁决覆盖。",
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="候选默认说明，用于审计和默认安全响应。")
    detector: Mapped[str] = mapped_column(Text, nullable=False, comment="候选来源检测器或结构化字段检查器标识。")
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="候选定义排序优先级，数值越小越优先。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="候选定义是否允许运行时使用。",
    )
    version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="v1",
        server_default="v1",
        comment="候选定义版本。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="候选定义附加审计信息。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="候选定义创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="候选定义最近更新时间。",
    )


class OutputSafetyCandidateDefinitionModel(Base):
    """表示输出安全结构化候选定义表。

    :return: 无返回值；该模型仅供仓储层访问，业务层不得直接操作。
    """

    __tablename__ = "output_safety_candidate_definitions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_output_safety_candidate_definitions_code"),
        CheckConstraint(
            "default_severity IN ('info', 'caution', 'urgent', 'blocked')",
            name="ck_output_safety_candidate_definitions_severity",
        ),
        {
            "comment": "输出安全候选定义表，用于描述 Guardrails 等结构化检测器输出的策略语义，不保存文本替换规则或回退响应模板。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="候选定义内部主键。")
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="候选编码，用于 OPA 策略、审计和安全信号关联。")
    category: Mapped[str] = mapped_column(Text, nullable=False, comment="候选类别，例如系统提示泄露、PII、密钥、剂量、药物、主题边界或格式候选。")
    default_severity: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="caution",
        server_default="caution",
        comment="候选默认严重级别；最终动作和信号级别由 OPA 裁决覆盖。",
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="候选默认说明，用于审计和默认策略原因。")
    detector: Mapped[str] = mapped_column(Text, nullable=False, comment="候选来源检测器标识。")
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="候选定义排序优先级，数值越小越优先。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="候选定义是否允许运行时使用。",
    )
    version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="v1",
        server_default="v1",
        comment="候选定义版本。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="候选定义附加审计信息。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="候选定义创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="候选定义最近更新时间。",
    )


class ConsultationDomainModel(Base):
    __tablename__ = "consultation_domains"
    __table_args__ = (
        {
            "comment": "问诊领域目录表，用于回答充分性策略读取领域所需关注槽位，不保存关键词分类规则。",
        },
    )

    domain: Mapped[str] = mapped_column(Text, primary_key=True, comment="问诊领域稳定技术标识。")
    required_slots: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        comment="当前领域建议关注的问诊事实槽位集合；不承载自然语言抽取规则。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="问诊领域是否允许运行时使用。",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="问诊领域排序优先级，数值越小越优先。",
    )
    version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="v1",
        server_default="v1",
        comment="问诊领域目录版本。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="问诊领域目录最近更新时间。",
    )


class TaskRoutingDomainModel(Base):
    __tablename__ = "task_routing_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_task_routing_domains_domain"),
        {
            "comment": "任务路由任务域目录表，用于约束结构化任务拆分结果，不保存关键词规则或临床动作规则。",
        },
    )

    domain: Mapped[str] = mapped_column(Text, primary_key=True, comment="任务域稳定技术标识。")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="任务域面向用户展示的标题。")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="任务域职责说明，仅用于路由上下文和运维理解。")
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="任务域默认排序优先级，数值越小越优先。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="任务域是否允许进入任务路由目录。",
    )
    version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="v1",
        server_default="v1",
        comment="任务域目录配置版本。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="任务域附加审计元数据，不承载任务动作规则。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="任务域记录创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="任务域记录最近更新时间。",
    )


class ConsultationSlotModel(Base):
    __tablename__ = "consultation_slots"
    __table_args__ = (
        {
            "comment": "问诊槽位展示和追问文案目录表；不保存关键词、正则或文本抽取规则。",
        },
    )

    slot_name: Mapped[str] = mapped_column(Text, primary_key=True, comment="问诊槽位稳定技术标识。")
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="该槽位默认追问文案。")
    label: Mapped[str] = mapped_column(Text, nullable=False, comment="该槽位面向用户展示的中文标签。")
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="问诊槽位排序优先级，数值越小越优先。",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="问诊槽位是否允许运行时使用。",
    )
    version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="v1",
        server_default="v1",
        comment="问诊槽位目录版本。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="问诊槽位目录最近更新时间。",
    )


class KnowledgeChunkModel(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="知识片段内部主键。")
    source: Mapped[str] = mapped_column(Text, nullable=False, comment="知识来源名称或治理来源标识。")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="知识片段标题，用于 RAG 证据展示与审计。")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="知识片段正文内容。")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True, comment="pgvector embedding 向量，生产 RAG 召回主路径。")
    public_citation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", comment="是否允许作为公开引用证据展示。")
    copyright_risk: Mapped[str] = mapped_column(Text, nullable=False, default="low", server_default="low", comment="版权风险级别，用于响应证据展示控制。")
    domain: Mapped[str | None] = mapped_column(Text, comment="知识片段适用问诊领域。")
    species: Mapped[str | None] = mapped_column(Text, comment="知识片段适用物种范围。")
    source_url: Mapped[str | None] = mapped_column(Text, comment="知识来源 URL。")
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1", comment="知识片段版本。")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", comment="知识片段是否允许运行时召回。")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="approved", server_default="approved", comment="知识片段审核状态，线上召回仅使用 approved。")
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8", comment="知识片段治理质量分。")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="知识片段最近审核时间。")
    disabled_reason: Mapped[str | None] = mapped_column(Text, comment="知识片段被停用的运维原因。")
    ingestion_batch: Mapped[str | None] = mapped_column(Text, comment="知识片段导入批次标识。")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}", comment="知识片段附加治理元数据，例如 chunk_type 与条件标识。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="知识片段创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="知识片段最近更新时间。")


class ClinicalSafetyAssetModel(Base):
    __tablename__ = "clinical_safety_assets"
    __table_args__ = (
        CheckConstraint("asset_id <> ''", name="ck_clinical_safety_assets_asset_id_nonempty"),
        CheckConstraint(
            "(review_status <> 'approved') OR btrim(code) <> ''",
            name="ck_clinical_safety_assets_code_nonempty",
        ),
        CheckConstraint(
            "(review_status <> 'approved') OR "
            "(code <> 'CLINICAL_SAFETY_UNKNOWN' AND code !~ '^CLINICAL_SAFETY_[0-9_]+$')",
            name="ck_clinical_safety_assets_code_not_generated_fallback",
        ),
        CheckConstraint(
            "asset_type IN ('toxin', 'human_drug', 'plant_toxin', 'chemical_toxin', 'emergency_red_flag', 'danger_pattern')",
            name="ck_clinical_safety_assets_asset_type",
        ),
        CheckConstraint(
            "severity IN ('info', 'caution', 'urgent', 'blocked')",
            name="ck_clinical_safety_assets_severity",
        ),
        CheckConstraint(
            "action_class IN ('emergency', 'same_day_visit', 'urgent_visit', 'safety_warning')",
            name="ck_clinical_safety_assets_action_class",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'quarantined')",
            name="ck_clinical_safety_assets_review_status",
        ),
        CheckConstraint(
            "(review_status = 'approved' AND enabled IS TRUE AND published_at IS NOT NULL) OR "
            "(review_status <> 'approved' AND enabled IS FALSE AND published_at IS NULL)",
            name="ck_clinical_safety_assets_publish_state",
        ),
        {
            "comment": "临床安全资产表，用于保存经资产治理域审核发布的安全候选资产；运行时不得根据名称补齐编码或枚举。",
        },
    )

    asset_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="临床安全资产稳定标识。")
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="对外安全信号编码，用于审计、策略和响应输出。")
    asset_type: Mapped[str] = mapped_column(Text, nullable=False, comment="安全资产类型，例如毒物、人用药、急症红旗或隐匿风险模式。")
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, comment="资产规范名称。")
    category: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", comment="资产所属临床分类或原始资料分类。")
    species_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用物种范围。")
    sex_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用性别范围。")
    age_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用年龄阶段范围。")
    severity: Mapped[str] = mapped_column(Text, nullable=False, comment="资产默认安全严重级别。")
    action_class: Mapped[str] = mapped_column(Text, nullable=False, comment="资产默认分诊动作分类。")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产别名、英文名、商品名或俗称。")
    carriers: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="风险载体或暴露来源列表。")
    user_expressions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="用户常见表达列表，仅用于资产治理和向量文本生成。")
    symptoms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产相关症状或风险线索。")
    recognition_phrases: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default="{}",
        comment="资产召回短语集合，仅用于离线生成 embedding 文本，不作为运行时关键词规则。",
    )
    required_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", comment="资产需要的结构化上下文提示。")
    decision_hints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", comment="不同语义场景下的策略动作提示。")
    clinical_risk_summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", comment="临床风险摘要。")
    triage_message: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", comment="对外分诊处置口径。")
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", comment="资料来源追踪信息。")
    raw_text: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", comment="原始临床安全文本字段备份。")
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1", comment="资产版本。")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", comment="资产是否允许运行时使用；发布动作必须显式置为 true。")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending", comment="资产审核状态，线上召回仅使用 approved。")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="资产发布时间。")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}", comment="资产附加审计元数据。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="资产创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="资产最近更新时间。")


class ClinicalSafetyChunkModel(Base):
    __tablename__ = "clinical_safety_chunks"
    __table_args__ = (
        CheckConstraint("chunk_id <> ''", name="ck_clinical_safety_chunks_chunk_id_nonempty"),
        CheckConstraint(
            "chunk_type IN ('recognition', 'clinical_risk', 'triage_action')",
            name="ck_clinical_safety_chunks_chunk_type",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'quarantined')",
            name="ck_clinical_safety_chunks_review_status",
        ),
        CheckConstraint(
            "(review_status = 'approved' AND enabled IS TRUE AND embedding IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_dimension IS NOT NULL AND btrim(content_hash) <> '') OR "
            "(review_status <> 'approved' AND enabled IS FALSE)",
            name="ck_clinical_safety_chunks_publish_state",
        ),
        {
            "comment": "临床安全向量 chunk 表，作为线上临床安全候选召回的 pgvector 主路径；发布态必须已完成向量化。",
        },
    )

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="临床安全向量 chunk 稳定标识。")
    asset_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("clinical_safety_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的临床安全资产标识。",
    )
    chunk_type: Mapped[str] = mapped_column(Text, nullable=False, comment="chunk 用途类型，例如识别、临床风险或分诊动作。")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="chunk 标题。")
    embedding_text: Mapped[str] = mapped_column(Text, nullable=False, comment="生成向量使用的标准文本。")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True, comment="pgvector embedding 向量，线上候选召回主路径。")
    embedding_model: Mapped[str | None] = mapped_column(Text, comment="生成 embedding 使用的模型名称。")
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, comment="embedding 向量维度。")
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", comment="embedding_text 的内容哈希。")
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1", comment="chunk 版本。")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false", comment="chunk 是否允许运行时召回；发布动作必须显式置为 true。")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending", comment="chunk 审核状态，线上召回仅使用 approved。")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}", comment="chunk 附加审计元数据。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="chunk 创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="chunk 最近更新时间。")


class KnowledgeIngestionBatchModel(Base):
    __tablename__ = "knowledge_ingestion_batches"
    __table_args__ = (
        UniqueConstraint("batch_id", name="uq_knowledge_ingestion_batches_batch_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False, default="clinical_conditions", server_default="clinical_conditions")
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="imported", server_default="imported")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    total_conditions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[str | None] = mapped_column(Text)
    published_by: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicalConditionCardModel(Base):
    __tablename__ = "clinical_condition_cards"
    __table_args__ = (
        UniqueConstraint("condition_key", "version", "ingestion_batch", name="uq_clinical_condition_cards_batch_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    condition_key: Mapped[str] = mapped_column(Text, nullable=False)
    condition_name: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str] = mapped_column(Text, nullable=False)
    presentation: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    differentials: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    followup_questions: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    triage: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    red_flags_escalate: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    medication_direction: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    home_advice: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    ingestion_batch: Mapped[str] = mapped_column(Text, nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ConversationTurnModel(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_conversation_turns_request_id"),
        {
            "comment": "Agent 对话回合表，用于当前 session 滑动窗口、回合审计、幂等响应快照关联与记忆投影来源。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="对话回合内部主键。")
    turn_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="Agent 生成的稳定回合标识。")
    request_id: Mapped[str] = mapped_column(Text, nullable=False, comment="入口请求标识，用于幂等与 trace 关联。")
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, comment="链路追踪标识。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信用户标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信会话标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信宠物标识。")
    input_text: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮用户输入文本快照。")
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮 Agent 响应摘要或完整输出快照。")
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="completed",
        server_default="completed",
        comment="本轮 Agent 响应状态。",
    )
    medical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="本轮是否属于医疗咨询主链路。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="本轮回合附加审计元数据。",
    )
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="本轮响应结构化快照。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本轮回合创建时间。",
    )


class PetProfileModel(Base):
    __tablename__ = "pet_profiles"
    __table_args__ = (
        UniqueConstraint("pet_id", name="uq_pet_profiles_pet_id"),
        UniqueConstraint("user_id", "pet_id", name="uq_pet_profiles_owner_pet"),
        {
            "comment": "上游已验证宠物画像在 Agent 侧的本地投影表，用于身份、宠物资料与会话范围数据链。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="宠物画像内部主键。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="宠物所属用户标识，来自可信上游或宠物资料领域。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="宠物标识，作为业务侧宠物范围主键。")
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="上游已验证宠物画像 JSON，由可信 BFF 范围声明或受控同步流程写入。",
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, default="api", server_default="api", comment="画像投影来源。")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="宠物画像是否处于启用状态；停用后范围策略应拒绝进入 Agent 主链路。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="宠物画像创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="宠物画像最近更新时间。",
    )


class PetSessionBindingModel(Base):
    __tablename__ = "pet_session_bindings"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_pet_session_bindings_session_id"),
        {
            "comment": "会话与用户、宠物范围绑定表，用于保证一 session 一宠且避免跨宠串话。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="会话绑定内部主键。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="会话标识，一个 session 只能绑定到同一用户与宠物。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="会话绑定的用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="会话绑定的宠物标识。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="会话绑定创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="会话绑定更新时间。",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="会话绑定最近一次通过范围授权的时间。",
    )


class ConsultationStateModel(Base):
    __tablename__ = "consultation_states"
    __table_args__ = (
        UniqueConstraint("user_id", "pet_id", "session_id", "task_key", name="uq_consultation_states_scope"),
        {
            "comment": "活跃问诊状态表，用于当前 session 默认问诊状态与多任务问诊状态持久化。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="问诊状态内部主键。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信宠物标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信会话标识。")
    task_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="__default__",
        server_default="__default__",
        comment="问诊任务键；__default__ 表示默认单任务状态。",
    )
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="结构化活跃问诊状态 JSON。",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="问诊状态更新版本号。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="问诊状态创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="问诊状态最近更新时间。",
    )


class PetMemoryFactModel(Base):
    __tablename__ = "pet_memory_facts"
    __table_args__ = (
        UniqueConstraint("user_id", "pet_id", "fact_type", "fact_key", name="uq_pet_memory_facts_key"),
        {
            "comment": "宠物权威长期事实表，用于保存经抽取、确认或人工纠正后的可信记忆事实。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="长期事实内部主键。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信宠物标识。")
    fact_type: Mapped[str] = mapped_column(Text, nullable=False, comment="事实类型，例如 medical 或 owner_preference。")
    fact_key: Mapped[str] = mapped_column(Text, nullable=False, comment="事实键名。")
    fact_value: Mapped[str] = mapped_column(Text, nullable=False, comment="事实内容。")
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
        server_default="0.8",
        comment="事实置信度，范围应由写入策略控制。",
    )
    source_turn_id: Mapped[str | None] = mapped_column(Text, comment="事实来源回合标识。")
    source_text: Mapped[str | None] = mapped_column(Text, comment="事实来源文本片段。")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="事实生效时间。",
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="事实失效时间。")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="事实是否处于可读状态。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="长期事实附加审计元数据。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="长期事实创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="长期事实最近更新时间。",
    )


class PetMemoryEpisodeModel(Base):
    __tablename__ = "pet_memory_episodes"

    __table_args__ = (
        {
            "comment": "宠物中期历史 episode 表，用于保存跨 session 的历史事件摘要和语义投影来源审计。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="episode 内部主键。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="可信宠物标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="episode 来源会话标识。")
    turn_id: Mapped[str | None] = mapped_column(Text, comment="episode 来源回合标识。")
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="episode 标题。")
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="episode 摘要。")
    memory_scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="medium",
        server_default="medium",
        comment="episode 记忆范围，例如 medium。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="episode 附加审计元数据。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="episode 创建时间。",
    )


class PetReportModel(Base):
    __tablename__ = "pet_reports"
    __table_args__ = (
        UniqueConstraint("report_id", name="uq_pet_reports_report_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    pet_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    report_type: Mapped[str] = mapped_column(Text, nullable=False, default="unknown", server_default="unknown")
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="text", server_default="text")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="parsed", server_default="parsed")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    ocr_engine: Mapped[str] = mapped_column(Text, nullable=False, default="none", server_default="none")
    parser_version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    safety_flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PetReportItemModel(Base):
    __tablename__ = "pet_report_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(Text)
    reference_range: Mapped[str | None] = mapped_column(Text)
    abnormal_flag: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagAuditEventModel(Base):
    __tablename__ = "rag_audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LogicTraceModel(Base):
    """表示 Agent 主链路成功响应或显式错误的审计记录表。

    :return: 无返回值；该模型仅供 trace 仓储访问。
    """

    __tablename__ = "logic_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    pet_id: Mapped[str | None] = mapped_column(Text)
    medical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RagRetrievalMissModel(Base):
    """表示 RAG 无命中知识缺口治理记录表。

    :return: 无返回值；该模型仅供 RAG miss 治理仓储访问，业务层不得直接操作。
    """

    __tablename__ = "rag_retrieval_misses"
    __table_args__ = (
        UniqueConstraint("miss_id", name="uq_rag_retrieval_misses_miss_id"),
        CheckConstraint("rag_scope IN ('answer_rag', 'followup_rag')", name="ck_rag_retrieval_misses_scope"),
        CheckConstraint(
            "status IN ('open', 'triaged', 'asset_drafted', 'published', 'dismissed')",
            name="ck_rag_retrieval_misses_status",
        ),
        Index("idx_rag_retrieval_misses_trace_id", "trace_id"),
        Index("idx_rag_retrieval_misses_scope_status", "rag_scope", "status"),
        Index("idx_rag_retrieval_misses_dedupe_key", "dedupe_key"),
        Index("idx_rag_retrieval_misses_domain_created_at", "task_domain", "created_at"),
        Index("idx_rag_retrieval_misses_identity", "user_id", "pet_id", "session_id"),
        {
            "comment": "RAG 无命中知识缺口治理记录表，用于记录 Fail Fast 后的可治理资产缺口，不参与运行时回答回退。"
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="RAG 无命中治理记录内部主键。")
    miss_id: Mapped[str] = mapped_column(Text, nullable=False, comment="RAG 无命中治理记录稳定标识。")
    request_id: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的 Agent 请求标识。")
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的链路追踪标识。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="当前可信用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="当前可信宠物标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="当前可信会话标识。")
    rag_scope: Mapped[str] = mapped_column(Text, nullable=False, comment="RAG 无命中所属数据链范围，当前允许 answer_rag、followup_rag。")
    task_id: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的当前任务展示标识。")
    task_key: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的当前任务状态键。")
    task_domain: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的当前任务域。")
    task_title: Mapped[str] = mapped_column(Text, nullable=False, comment="触发无命中的当前任务标题。")
    user_text_excerpt: Mapped[str] = mapped_column(Text, nullable=False, comment="经裁剪后的用户任务文本片段，用于人工治理排障。")
    user_text_digest: Mapped[str] = mapped_column(Text, nullable=False, comment="用户任务文本 SHA-256 摘要，用于去重和隐私友好的聚合。")
    structured_query: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="当前 RAG 实际使用的结构化检索 query，不作为运行时规则来源。",
    )
    consultation_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="触发无命中时的问诊状态快照。",
    )
    answerability: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="触发无命中时的回答充分性裁决快照。",
    )
    semantic_extraction: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="触发无命中时的问诊语义抽取快照。",
    )
    retrieval_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="当前 RAG 本轮召回参数摘要，例如 top_k、min_score、chunk 类型、领域过滤和 missing_slots。",
    )
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False, comment="无命中失败原因，例如 no_approved_vector_hits。")
    error_type: Mapped[str] = mapped_column(Text, nullable=False, comment="触发治理记录的原始异常类型。")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, comment="触发治理记录的原始异常消息。")
    error_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="原始异常结构化细节，用于排查过滤条件和依赖状态。",
    )
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, comment="用于后台聚合同类知识缺口的稳定哈希键。")
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="open",
        server_default="open",
        comment="知识缺口治理状态，仅用于后台治理，不参与 Agent 运行时裁决。",
    )
    review_notes: Mapped[str | None] = mapped_column(Text, comment="治理人员处理备注。")
    linked_ingestion_batch: Mapped[str | None] = mapped_column(Text, comment="关联的知识导入批次标识。")
    linked_chunk_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger),
        nullable=False,
        default=list,
        server_default="{}",
        comment="关联的正式知识 chunk 内部主键集合。",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="RAG 无命中治理记录附加审计信息。",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="治理记录创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="治理记录最近更新时间。")


class BackgroundTaskModel(Base):
    __tablename__ = "background_tasks"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_background_tasks_task_id"),
        UniqueConstraint("task_type", "business_key", name="uq_background_tasks_type_business_key"),
        CheckConstraint(
            "status IN ('pending', 'running', 'retrying', 'succeeded', 'dead_letter', 'cancelled')",
            name="ck_background_tasks_status",
        ),
        Index("idx_background_tasks_status_run_after_priority", "status", "run_after", "priority"),
        Index("idx_background_tasks_locked_until", "locked_until"),
        Index("idx_background_tasks_scope", "user_id", "pet_id", "session_id"),
        Index("idx_background_tasks_ordering_key", "ordering_key"),
        {
            "comment": "持久化后台任务表，用于保存可重试的异步后置处理任务、业务幂等键与执行审计信息。",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="后台任务内部主键。")
    task_id: Mapped[str] = mapped_column(Text, nullable=False, comment="后台任务稳定标识，用于响应 metadata、worker 审计和外部排障。")
    task_type: Mapped[str] = mapped_column(Text, nullable=False, comment="后台任务类型，例如 memory_candidate_extraction。")
    business_key: Mapped[str] = mapped_column(Text, nullable=False, comment="后台任务业务幂等键，同一任务类型下必须唯一。")
    ordering_key: Mapped[str] = mapped_column(Text, nullable=False, comment="后台任务顺序约束键，例如 user_id:pet_id:session_id。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="任务来源用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="任务来源宠物标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="任务来源会话标识。")
    source_turn_id: Mapped[str | None] = mapped_column(Text, comment="任务来源回合标识。")
    source_request_id: Mapped[str | None] = mapped_column(Text, comment="任务来源请求标识。")
    source_trace_id: Mapped[str | None] = mapped_column(Text, comment="任务来源链路追踪标识。")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending", comment="后台任务状态。")
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="后台任务优先级，数值越小越优先。",
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="任务最早可执行时间。",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="任务已执行尝试次数。",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        server_default="5",
        comment="任务最大执行尝试次数。",
    )
    locked_by: Mapped[str | None] = mapped_column(Text, comment="当前持有任务租约的 worker 标识。")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="当前任务租约过期时间。")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="任务执行载荷。",
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="任务执行结果或失败摘要。")
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="最近一次失败的结构化错误信息。")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="任务附加审计元数据。",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="任务首次或最近一次开始执行时间。")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="任务最终完成时间。")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="任务创建时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="任务最近更新时间。",
    )


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user_id", "pet_id", "session_id", "idempotency_key", name="uq_idempotency_scope_key"),
        CheckConstraint("status IN ('processing', 'completed', 'failed')", name="ck_idempotency_records_status"),
        {"comment": "Agent 单回合幂等记录表，用于 turn execution 门禁的 claim、响应重放与失败追踪。"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="幂等记录内部主键。")
    user_id: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮可信身份范围中的用户标识。")
    pet_id: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮可信身份范围中的宠物标识。")
    session_id: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮可信身份范围中的会话标识。")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, comment="调用方提交的幂等键，在同一用户、宠物、会话范围内唯一。")
    request_id: Mapped[str] = mapped_column(Text, nullable=False, comment="最近一次声明或完成该幂等记录的请求标识。")
    trace_id: Mapped[str] = mapped_column(Text, nullable=False, comment="最近一次声明或完成该幂等记录的链路追踪标识。")
    request_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
        comment="去除 request_id、trace_id 与 idempotency_key 后的请求语义哈希，用于检测同 key 不同请求冲突。",
    )
    response_id: Mapped[str | None] = mapped_column(Text, comment="首个成功响应的 Agent turn 标识。")
    status: Mapped[str] = mapped_column(Text, nullable=False, comment="幂等记录状态，仅允许 processing、completed、failed。")
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="首个成功响应的 JSON 快照，用于后续同语义请求重放。")
    error_type: Mapped[str | None] = mapped_column(Text, comment="最近一次执行失败的异常类型，用于排障和审计。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="幂等记录创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="幂等记录最近更新时间。")
