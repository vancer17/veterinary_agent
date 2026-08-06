"""
文件：alembic/versions/0002_memory_concurrency_schema.py
作用：提供数据库迁移环境与版本脚本。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_memory_concurrency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移。

    :return: 返回函数执行结果。
    """
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="completed"),
        sa.Column("medical", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("request_id", name="uq_conversation_turns_request_id"),
        sa.UniqueConstraint("turn_id", name="uq_conversation_turns_turn_id"),
    )
    op.create_index("idx_conversation_turns_identity", "conversation_turns", ["user_id", "pet_id", "session_id"])
    op.create_index("idx_conversation_turns_created_at", "conversation_turns", ["created_at"])

    op.create_table(
        "consultation_states",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False, server_default="__default__"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "pet_id", "session_id", "task_key", name="uq_consultation_states_scope"),
    )
    op.create_index("idx_consultation_states_identity", "consultation_states", ["user_id", "pet_id", "session_id"])

    op.create_table(
        "pet_memory_facts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("fact_type", sa.Text(), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("fact_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("source_turn_id", sa.Text(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "pet_id", "fact_type", "fact_key", name="uq_pet_memory_facts_key"),
    )
    op.create_index("idx_pet_memory_facts_identity", "pet_memory_facts", ["user_id", "pet_id", "is_active"])

    op.create_table(
        "pet_memory_episodes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("memory_scope", sa.Text(), nullable=False, server_default="medium"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_pet_memory_episodes_identity", "pet_memory_episodes", ["user_id", "pet_id", "session_id"])
    op.create_index("idx_pet_memory_episodes_created_at", "pet_memory_episodes", ["created_at"])

    op.create_table(
        "logic_traces",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("pet_id", sa.Text(), nullable=True),
        sa.Column("medical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_logic_traces_trace_id", "logic_traces", ["trace_id"])
    op.create_index("idx_logic_traces_identity", "logic_traces", ["user_id", "pet_id", "session_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("response_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "pet_id", "session_id", "idempotency_key", name="uq_idempotency_scope_key"),
    )
    op.create_index("idx_idempotency_records_request_id", "idempotency_records", ["request_id"])


def downgrade() -> None:
    """执行 Alembic 回滚迁移。

    :return: 返回函数执行结果。
    """
    op.drop_index("idx_idempotency_records_request_id", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("idx_logic_traces_identity", table_name="logic_traces")
    op.drop_index("idx_logic_traces_trace_id", table_name="logic_traces")
    op.drop_table("logic_traces")
    op.drop_index("idx_pet_memory_episodes_created_at", table_name="pet_memory_episodes")
    op.drop_index("idx_pet_memory_episodes_identity", table_name="pet_memory_episodes")
    op.drop_table("pet_memory_episodes")
    op.drop_index("idx_pet_memory_facts_identity", table_name="pet_memory_facts")
    op.drop_table("pet_memory_facts")
    op.drop_index("idx_consultation_states_identity", table_name="consultation_states")
    op.drop_table("consultation_states")
    op.drop_index("idx_conversation_turns_created_at", table_name="conversation_turns")
    op.drop_index("idx_conversation_turns_identity", table_name="conversation_turns")
    op.drop_table("conversation_turns")
