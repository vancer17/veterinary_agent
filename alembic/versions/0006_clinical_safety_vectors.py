"""
文件：alembic/versions/0006_clinical_safety_vectors.py
作用：为 P0 临床安全场景新增独立资产表、向量 chunk 表与运行时检索索引。
说明：安全资产与普通 RAG 知识解耦，便于审核、发布、回滚和高风险召回审计。
"""

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_clinical_safety_vectors"
down_revision = "0005_clinical_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行临床安全向量表的正向迁移。

    :return: 无返回值。
    """
    op.create_table(
        "clinical_safety_assets",
        sa.Column("asset_id", sa.Text(), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default=""),
        sa.Column("species_scope", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("sex_scope", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("age_scope", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="caution"),
        sa.Column("action_class", sa.Text(), nullable=False, server_default="safety_warning"),
        sa.Column("aliases", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("carriers", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("user_expressions", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("symptoms", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("required_context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decision_hints", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("clinical_risk_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("triage_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_text", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="approved"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_clinical_safety_assets_code", "clinical_safety_assets", ["code"])
    op.create_index("idx_clinical_safety_assets_status", "clinical_safety_assets", ["review_status", "enabled"])
    op.create_index("idx_clinical_safety_assets_type", "clinical_safety_assets", ["asset_type"])

    op.create_table(
        "clinical_safety_chunks",
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Text(),
            sa.ForeignKey("clinical_safety_assets.asset_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("embedding_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="approved"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_clinical_safety_chunks_asset", "clinical_safety_chunks", ["asset_id"])
    op.create_index("idx_clinical_safety_chunks_status", "clinical_safety_chunks", ["review_status", "enabled"])
    op.create_index("idx_clinical_safety_chunks_type", "clinical_safety_chunks", ["chunk_type"])
    op.create_index(
        "idx_clinical_safety_chunks_embedding_hnsw",
        "clinical_safety_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )


def downgrade() -> None:
    """执行临床安全向量表的回滚迁移。

    :return: 无返回值。
    """
    op.drop_index("idx_clinical_safety_chunks_embedding_hnsw", table_name="clinical_safety_chunks")
    op.drop_index("idx_clinical_safety_chunks_type", table_name="clinical_safety_chunks")
    op.drop_index("idx_clinical_safety_chunks_status", table_name="clinical_safety_chunks")
    op.drop_index("idx_clinical_safety_chunks_asset", table_name="clinical_safety_chunks")
    op.drop_table("clinical_safety_chunks")
    op.drop_index("idx_clinical_safety_assets_type", table_name="clinical_safety_assets")
    op.drop_index("idx_clinical_safety_assets_status", table_name="clinical_safety_assets")
    op.drop_index("idx_clinical_safety_assets_code", table_name="clinical_safety_assets")
    op.drop_table("clinical_safety_assets")
