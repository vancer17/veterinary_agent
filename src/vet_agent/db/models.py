"""
文件：src/vet_agent/db/models.py
作用：提供数据库模型、连接与会话管理能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint, func
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


class ConsultationDomainModel(Base):
    __tablename__ = "consultation_domains"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    required_slots: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    classifier_keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ConsultationSlotModel(Base):
    __tablename__ = "consultation_slots"

    slot_name: Mapped[str] = mapped_column(Text, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class KnowledgeChunkModel(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    public_citation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    copyright_risk: Mapped[str] = mapped_column(Text, nullable=False, default="low", server_default="low")
    domain: Mapped[str | None] = mapped_column(Text)
    species: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="v1", server_default="v1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="approved", server_default="approved")
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    ingestion_batch: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicalSafetyAssetModel(Base):
    __tablename__ = "clinical_safety_assets"

    asset_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="临床安全资产稳定标识。")
    code: Mapped[str] = mapped_column(Text, nullable=False, comment="对外安全信号编码，用于审计、策略和响应输出。")
    asset_type: Mapped[str] = mapped_column(Text, nullable=False, comment="安全资产类型，例如毒物、人用药、急症红旗或隐匿风险模式。")
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, comment="资产规范名称。")
    category: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", comment="资产所属临床分类或原始资料分类。")
    species_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用物种范围。")
    sex_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用性别范围。")
    age_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}", comment="资产适用年龄阶段范围。")
    severity: Mapped[str] = mapped_column(Text, nullable=False, default="caution", server_default="caution", comment="资产默认安全严重级别。")
    action_class: Mapped[str] = mapped_column(Text, nullable=False, default="safety_warning", server_default="safety_warning", comment="资产默认分诊动作分类。")
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
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", comment="资产是否允许运行时使用。")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="approved", server_default="approved", comment="资产审核状态，线上召回仅使用 approved。")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="资产发布时间。")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}", comment="资产附加审计元数据。")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="资产创建时间。")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), comment="资产最近更新时间。")


class ClinicalSafetyChunkModel(Base):
    __tablename__ = "clinical_safety_chunks"

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
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", comment="chunk 是否允许运行时召回。")
    review_status: Mapped[str] = mapped_column(Text, nullable=False, default="approved", server_default="approved", comment="chunk 审核状态，线上召回仅使用 approved。")
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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    pet_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="completed", server_default="completed")
    medical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    pet_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_key: Mapped[str] = mapped_column(Text, nullable=False, default="__default__", server_default="__default__")
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PetMemoryFactModel(Base):
    __tablename__ = "pet_memory_facts"
    __table_args__ = (
        UniqueConstraint("user_id", "pet_id", "fact_type", "fact_key", name="uq_pet_memory_facts_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    pet_id: Mapped[str] = mapped_column(Text, nullable=False)
    fact_type: Mapped[str] = mapped_column(Text, nullable=False)
    fact_key: Mapped[str] = mapped_column(Text, nullable=False)
    fact_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8, server_default="0.8")
    source_turn_id: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PetMemoryEpisodeModel(Base):
    __tablename__ = "pet_memory_episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    pet_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    memory_scope: Mapped[str] = mapped_column(Text, nullable=False, default="medium", server_default="medium")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


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
