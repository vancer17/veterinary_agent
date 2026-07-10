##################################################################################################
# 文件: migrations/versions/20260709_0002_conversation_store.py
# 作用: 创建 ConversationStore 对话事实表，覆盖 session、message、assistant segment 和附件引用。
# 边界: 仅定义 ConversationStore 自有表结构；不写入业务数据，不创建 CheckpointStore 或 LangGraph 内部表。
##################################################################################################
"""create conversation store tables

Revision ID: 20260709_0002
Revises: 20260708_0001
Create Date: 2026-07-09 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260709_0002"
down_revision: str | None = "20260708_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSION_STATUS_VALUES: tuple[str, ...] = (
    "active",
    "closed",
    "archived",
)
MESSAGE_ROLE_VALUES: tuple[str, ...] = (
    "user",
    "assistant",
    "system",
    "tool",
)
MESSAGE_STATUS_VALUES: tuple[str, ...] = (
    "finalized",
    "streaming",
    "cancelled",
)


def _quoted_values(values: tuple[str, ...]) -> str:
    """构建 check constraint 使用的单引号字符串列表。

    :param values: 允许写入数据库状态字段的稳定字符串值。
    :return: 适合放入 SQL IN 表达式的字符串片段。
    """

    return ", ".join(f"'{value}'" for value in values)


def _json_metadata_type() -> sa.types.TypeEngine[object]:
    """构建 metadata 字段使用的跨方言 JSON 类型。

    :return: PostgreSQL 下使用 JSONB、其他测试方言下使用通用 JSON 的 SQLAlchemy 类型。
    """

    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()),
        "postgresql",
    )


def upgrade() -> None:
    """应用 ConversationStore 表结构升级。

    :return: None。
    """

    op.create_table(
        "conversation_session",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "metadata",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "next_sequence_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("session_id", name="pk_conversation_session"),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(SESSION_STATUS_VALUES)})",
            name="ck_conversation_session_status",
        ),
        sa.CheckConstraint(
            "next_sequence_no >= 1",
            name="ck_conversation_session_next_sequence_no_positive",
        ),
    )
    op.create_index(
        "ix_conversation_session_user_id",
        "conversation_session",
        ["user_id"],
    )
    op.create_index(
        "ix_conversation_session_user_pet",
        "conversation_session",
        ["user_id", "pet_id"],
    )
    op.create_index(
        "ix_conversation_session_status_updated_at",
        "conversation_session",
        ["status", "updated_at"],
    )

    op.create_table(
        "conversation_message",
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'finalized'"),
        ),
        sa.Column("reply_to_message_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("message_id", name="pk_conversation_message"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_session.session_id"],
            name="fk_conversation_message_session_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["conversation_message.message_id"],
            name="fk_conversation_message_reply_to_message_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "session_id",
            "sequence_no",
            name="uq_conversation_message_session_sequence",
        ),
        sa.UniqueConstraint(
            "session_id",
            "idempotency_key",
            name="uq_conversation_message_session_idempotency",
        ),
        sa.CheckConstraint(
            f"role IN ({_quoted_values(MESSAGE_ROLE_VALUES)})",
            name="ck_conversation_message_role",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(MESSAGE_STATUS_VALUES)})",
            name="ck_conversation_message_status",
        ),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name="ck_conversation_message_sequence_no_positive",
        ),
    )
    op.create_index(
        "ix_conversation_message_session_sequence",
        "conversation_message",
        ["session_id", "sequence_no"],
    )
    op.create_index(
        "ix_conversation_message_session_created_at",
        "conversation_message",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_message_user_pet",
        "conversation_message",
        ["user_id", "pet_id"],
    )

    op.create_table(
        "conversation_message_segment",
        sa.Column("segment_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("segment_order", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "segment_id",
            name="pk_conversation_message_segment",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["conversation_message.message_id"],
            name="fk_conversation_message_segment_message_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_session.session_id"],
            name="fk_conversation_message_segment_session_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "message_id",
            "segment_order",
            name="uq_conversation_message_segment_order",
        ),
        sa.UniqueConstraint(
            "message_id",
            "idempotency_key",
            name="uq_conversation_message_segment_idempotency",
        ),
        sa.CheckConstraint(
            "segment_order >= 1",
            name="ck_conversation_message_segment_order_positive",
        ),
    )
    op.create_index(
        "ix_conversation_message_segment_message_order",
        "conversation_message_segment",
        ["message_id", "segment_order"],
    )
    op.create_index(
        "ix_conversation_message_segment_session_published_at",
        "conversation_message_segment",
        ["session_id", "published_at"],
    )

    op.create_table(
        "conversation_attachment_ref",
        sa.Column("attachment_ref_id", sa.Text(), nullable=False),
        sa.Column("attachment_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("attachment_type", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "attachment_ref_id",
            name="pk_conversation_attachment_ref",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["conversation_message.message_id"],
            name="fk_conversation_attachment_ref_message_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_session.session_id"],
            name="fk_conversation_attachment_ref_session_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_conversation_attachment_ref_message_id",
        "conversation_attachment_ref",
        ["message_id"],
    )
    op.create_index(
        "ix_conversation_attachment_ref_session_created_at",
        "conversation_attachment_ref",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    """回滚 ConversationStore 表结构升级。

    :return: None。
    """

    op.drop_index(
        "ix_conversation_attachment_ref_session_created_at",
        table_name="conversation_attachment_ref",
    )
    op.drop_index(
        "ix_conversation_attachment_ref_message_id",
        table_name="conversation_attachment_ref",
    )
    op.drop_table("conversation_attachment_ref")

    op.drop_index(
        "ix_conversation_message_segment_session_published_at",
        table_name="conversation_message_segment",
    )
    op.drop_index(
        "ix_conversation_message_segment_message_order",
        table_name="conversation_message_segment",
    )
    op.drop_table("conversation_message_segment")

    op.drop_index(
        "ix_conversation_message_user_pet",
        table_name="conversation_message",
    )
    op.drop_index(
        "ix_conversation_message_session_created_at",
        table_name="conversation_message",
    )
    op.drop_index(
        "ix_conversation_message_session_sequence",
        table_name="conversation_message",
    )
    op.drop_table("conversation_message")

    op.drop_index(
        "ix_conversation_session_status_updated_at",
        table_name="conversation_session",
    )
    op.drop_index("ix_conversation_session_user_pet", table_name="conversation_session")
    op.drop_index("ix_conversation_session_user_id", table_name="conversation_session")
    op.drop_table("conversation_session")
