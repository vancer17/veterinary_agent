"""
文件：alembic/versions/0011_clinical_safety_vector_comments.py
作用：为临床安全向量召回表补充 PostgreSQL 表和字段说明。
范围：仅更新 clinical_safety_assets 与 clinical_safety_chunks 的数据库注释，不改变表结构和业务数据。
说明：字段说明用于运维、审计和后续数据治理理解临床安全候选召回数据链边界。
"""

from __future__ import annotations

from alembic import op


revision = "0011_clinical_safety_vector_comments"
down_revision = "0010_input_safety_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，补充临床安全向量召回表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE clinical_safety_assets IS '临床安全资产表，用于保存已审核发布的毒物、人用药、急症红旗和隐匿风险模式。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.asset_id IS '临床安全资产稳定标识。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.code IS '对外安全信号编码，用于审计、策略和响应输出。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.asset_type IS '安全资产类型，例如毒物、人用药、急症红旗或隐匿风险模式。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.canonical_name IS '资产规范名称。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.category IS '资产所属临床分类或原始资料分类。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.species_scope IS '资产适用物种范围。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.sex_scope IS '资产适用性别范围。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.age_scope IS '资产适用年龄阶段范围。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.severity IS '资产默认安全严重级别。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.action_class IS '资产默认分诊动作分类。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.aliases IS '资产别名、英文名、商品名或俗称。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.carriers IS '风险载体或暴露来源列表。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.user_expressions IS '用户常见表达列表，仅用于资产治理和向量文本生成。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.symptoms IS '资产相关症状或风险线索。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.recognition_phrases IS '资产召回短语集合，仅用于离线生成 embedding 文本，不作为运行时关键词规则。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.required_context IS '资产需要的结构化上下文提示。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.decision_hints IS '不同语义场景下的策略动作提示。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.clinical_risk_summary IS '临床风险摘要。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.triage_message IS '对外分诊处置口径。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.source IS '资料来源追踪信息。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.raw_text IS '原始临床安全文本字段备份。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.version IS '资产版本。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.enabled IS '资产是否允许运行时使用。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.review_status IS '资产审核状态，线上召回仅使用 approved。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.published_at IS '资产发布时间。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.metadata IS '资产附加审计元数据。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.created_at IS '资产创建时间。'")
    _comment("COMMENT ON COLUMN clinical_safety_assets.updated_at IS '资产最近更新时间。'")

    _comment("COMMENT ON TABLE clinical_safety_chunks IS '临床安全向量 chunk 表，作为线上临床安全候选召回的 pgvector 主路径。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.chunk_id IS '临床安全向量 chunk 稳定标识。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.asset_id IS '关联的临床安全资产标识。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.chunk_type IS 'chunk 用途类型，例如识别、临床风险或分诊动作。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.title IS 'chunk 标题。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.embedding_text IS '生成向量使用的标准文本。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.embedding IS 'pgvector embedding 向量，线上候选召回主路径。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.embedding_model IS '生成 embedding 使用的模型名称。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.embedding_dimension IS 'embedding 向量维度。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.content_hash IS 'embedding_text 的内容哈希。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.version IS 'chunk 版本。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.enabled IS 'chunk 是否允许运行时召回。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.review_status IS 'chunk 审核状态，线上召回仅使用 approved。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.metadata IS 'chunk 附加审计元数据。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.created_at IS 'chunk 创建时间。'")
    _comment("COMMENT ON COLUMN clinical_safety_chunks.updated_at IS 'chunk 最近更新时间。'")


def downgrade() -> None:
    """执行 Alembic 回滚迁移，移除临床安全向量召回表说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE clinical_safety_assets IS NULL")
    for column in (
        "asset_id",
        "code",
        "asset_type",
        "canonical_name",
        "category",
        "species_scope",
        "sex_scope",
        "age_scope",
        "severity",
        "action_class",
        "aliases",
        "carriers",
        "user_expressions",
        "symptoms",
        "recognition_phrases",
        "required_context",
        "decision_hints",
        "clinical_risk_summary",
        "triage_message",
        "source",
        "raw_text",
        "version",
        "enabled",
        "review_status",
        "published_at",
        "metadata",
        "created_at",
        "updated_at",
    ):
        _comment(f"COMMENT ON COLUMN clinical_safety_assets.{column} IS NULL")

    _comment("COMMENT ON TABLE clinical_safety_chunks IS NULL")
    for column in (
        "chunk_id",
        "asset_id",
        "chunk_type",
        "title",
        "embedding_text",
        "embedding",
        "embedding_model",
        "embedding_dimension",
        "content_hash",
        "version",
        "enabled",
        "review_status",
        "metadata",
        "created_at",
        "updated_at",
    ):
        _comment(f"COMMENT ON COLUMN clinical_safety_chunks.{column} IS NULL")


def _comment(statement: str) -> None:
    """执行数据库注释 SQL。

    :param statement: PostgreSQL COMMENT 语句。
    :return: 无返回值。
    """
    op.execute(statement)
