"""access control and idempotency hardening

Revision ID: 0003_access_control
Revises: 0002_memory_concurrency
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_access_control"
down_revision = "0002_memory_concurrency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pet_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.Text(), nullable=False, server_default="api"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("pet_id", name="uq_pet_profiles_pet_id"),
        sa.UniqueConstraint("user_id", "pet_id", name="uq_pet_profiles_owner_pet"),
    )
    op.create_index("idx_pet_profiles_owner", "pet_profiles", ["user_id", "is_active"])

    op.create_table(
        "pet_session_bindings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", name="uq_pet_session_bindings_session_id"),
    )
    op.create_index("idx_pet_session_bindings_identity", "pet_session_bindings", ["user_id", "pet_id", "session_id"])

    op.alter_column("idempotency_records", "response_snapshot", existing_type=postgresql.JSONB(), nullable=True)


def downgrade() -> None:
    op.alter_column("idempotency_records", "response_snapshot", existing_type=postgresql.JSONB(), nullable=False)
    op.drop_index("idx_pet_session_bindings_identity", table_name="pet_session_bindings")
    op.drop_table("pet_session_bindings")
    op.drop_index("idx_pet_profiles_owner", table_name="pet_profiles")
    op.drop_table("pet_profiles")
