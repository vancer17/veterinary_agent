"""
文件：alembic/versions/0005_clinical_condition_assets.py
作用：新增结构化临床病症卡、知识导入批次与字段级 RAG 入库治理结构。
说明：用于承载 common_conditions_handbook.md 对应的结构化临床知识资产，支持后台导入、审核、发布与回滚。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_clinical_assets"
down_revision = "0004_p1_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移。

    :return: 返回函数执行结果。
    """
    op.create_table(
        "knowledge_ingestion_batches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False, server_default="clinical_conditions"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="imported"),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("total_conditions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", name="uq_knowledge_ingestion_batches_batch_id"),
    )
    op.create_index("idx_knowledge_ingestion_batches_status", "knowledge_ingestion_batches", ["asset_type", "status"])
    op.create_index("idx_knowledge_ingestion_batches_created_at", "knowledge_ingestion_batches", ["created_at"])

    op.create_table(
        "clinical_condition_cards",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("condition_key", sa.Text(), nullable=False),
        sa.Column("condition_name", sa.Text(), nullable=False),
        sa.Column("system", sa.Text(), nullable=False),
        sa.Column("presentation", sa.Text(), nullable=False, server_default=""),
        sa.Column("differentials", sa.Text(), nullable=False, server_default=""),
        sa.Column("followup_questions", sa.Text(), nullable=False, server_default=""),
        sa.Column("triage", sa.Text(), nullable=False, server_default=""),
        sa.Column("red_flags_escalate", sa.Text(), nullable=False, server_default=""),
        sa.Column("medication_direction", sa.Text(), nullable=False, server_default=""),
        sa.Column("home_advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("ingestion_batch", sa.Text(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("condition_key", "version", "ingestion_batch", name="uq_clinical_condition_cards_batch_key"),
    )
    op.create_index("idx_clinical_condition_cards_batch", "clinical_condition_cards", ["ingestion_batch"])
    op.create_index("idx_clinical_condition_cards_status", "clinical_condition_cards", ["review_status", "enabled"])
    op.create_index("idx_clinical_condition_cards_system", "clinical_condition_cards", ["system"])


def downgrade() -> None:
    """执行 Alembic 回滚迁移。

    :return: 返回函数执行结果。
    """
    op.drop_index("idx_clinical_condition_cards_system", table_name="clinical_condition_cards")
    op.drop_index("idx_clinical_condition_cards_status", table_name="clinical_condition_cards")
    op.drop_index("idx_clinical_condition_cards_batch", table_name="clinical_condition_cards")
    op.drop_table("clinical_condition_cards")
    op.drop_index("idx_knowledge_ingestion_batches_created_at", table_name="knowledge_ingestion_batches")
    op.drop_index("idx_knowledge_ingestion_batches_status", table_name="knowledge_ingestion_batches")
    op.drop_table("knowledge_ingestion_batches")
