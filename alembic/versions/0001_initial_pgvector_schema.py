"""initial pgvector schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    op.create_table(
        "safety_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="caution"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("response_template", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_safety_rules_type_enabled", "safety_rules", ["rule_type", "enabled"])

    op.create_table(
        "consultation_domains",
        sa.Column("domain", sa.Text(), primary_key=True),
        sa.Column("required_slots", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("classifier_keywords", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "consultation_slots",
        sa.Column("slot_name", sa.Text(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("extraction_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("public_citation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("copyright_risk", sa.Text(), nullable=False, server_default="low"),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("species", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_knowledge_chunks_enabled", "knowledge_chunks", ["enabled"])
    op.create_index("idx_knowledge_chunks_domain", "knowledge_chunks", ["domain"])


def downgrade() -> None:
    op.drop_index("idx_knowledge_chunks_domain", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_enabled", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_table("consultation_slots")
    op.drop_table("consultation_domains")
    op.drop_index("idx_safety_rules_type_enabled", table_name="safety_rules")
    op.drop_table("safety_rules")
