"""p1 reports and rag governance

Revision ID: 0004_p1_governance
Revises: 0003_access_control
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_p1_governance"
down_revision = "0003_access_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("review_status", sa.Text(), nullable=False, server_default="approved"))
    op.add_column("knowledge_chunks", sa.Column("quality_score", sa.Float(), nullable=False, server_default="0.8"))
    op.add_column("knowledge_chunks", sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("disabled_reason", sa.Text(), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("ingestion_batch", sa.Text(), nullable=True))
    op.create_index("idx_knowledge_chunks_review_status", "knowledge_chunks", ["review_status", "enabled"])

    op.create_table(
        "pet_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="text"),
        sa.Column("status", sa.Text(), nullable=False, server_default="parsed"),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_engine", sa.Text(), nullable=False, server_default="none"),
        sa.Column("parser_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("safety_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", name="uq_pet_reports_report_id"),
    )
    op.create_index("idx_pet_reports_identity", "pet_reports", ["user_id", "pet_id", "session_id"])
    op.create_index("idx_pet_reports_created_at", "pet_reports", ["created_at"])

    op.create_table(
        "pet_report_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("reference_range", sa.Text(), nullable=True),
        sa.Column("abnormal_flag", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_pet_report_items_report_id", "pet_report_items", ["report_id"])
    op.create_index("idx_pet_report_items_name", "pet_report_items", ["item_name"])

    op.create_table(
        "rag_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chunk_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_rag_audit_events_chunk_id", "rag_audit_events", ["chunk_id"])
    op.create_index("idx_rag_audit_events_created_at", "rag_audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_rag_audit_events_created_at", table_name="rag_audit_events")
    op.drop_index("idx_rag_audit_events_chunk_id", table_name="rag_audit_events")
    op.drop_table("rag_audit_events")
    op.drop_index("idx_pet_report_items_name", table_name="pet_report_items")
    op.drop_index("idx_pet_report_items_report_id", table_name="pet_report_items")
    op.drop_table("pet_report_items")
    op.drop_index("idx_pet_reports_created_at", table_name="pet_reports")
    op.drop_index("idx_pet_reports_identity", table_name="pet_reports")
    op.drop_table("pet_reports")
    op.drop_index("idx_knowledge_chunks_review_status", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "ingestion_batch")
    op.drop_column("knowledge_chunks", "disabled_reason")
    op.drop_column("knowledge_chunks", "last_reviewed_at")
    op.drop_column("knowledge_chunks", "quality_score")
    op.drop_column("knowledge_chunks", "review_status")
