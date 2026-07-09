##################################################################################################
# 文件: migrations/versions/20260708_0001_checkpoint_store_control_plane.py
# 作用: 创建 CheckpointStore 控制平面表，覆盖 thread 映射、运行锁和 segment 发布幂等事实。
# 边界: 仅定义项目级 checkpoint 控制表结构；不创建 LangGraph checkpoint 表，不写入业务数据。
##################################################################################################
"""create checkpoint store control plane

Revision ID: 20260708_0001
Revises:
Create Date: 2026-07-08 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260708_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THREAD_STATUS_VALUES: tuple[str, ...] = (
    "initialized",
    "running",
    "recoverable",
    "completed",
    "failed",
    "cancelled",
)
SEGMENT_PUBLISH_STATUS_VALUES: tuple[str, ...] = (
    "ready",
    "published",
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
    """应用 CheckpointStore 控制平面表结构升级。

    :return: None。
    """

    op.create_table(
        "checkpoint_thread",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'initialized'"),
        ),
        sa.Column(
            "latest_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latest_checkpoint_id", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("thread_id", name="pk_checkpoint_thread"),
        sa.UniqueConstraint("session_id", name="uq_checkpoint_thread_session_id"),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(THREAD_STATUS_VALUES)})",
            name="ck_checkpoint_thread_status",
        ),
        sa.CheckConstraint(
            "latest_version >= 0",
            name="ck_checkpoint_thread_latest_version_non_negative",
        ),
    )
    op.create_index(
        "ix_checkpoint_thread_user_id",
        "checkpoint_thread",
        ["user_id"],
    )
    op.create_index(
        "ix_checkpoint_thread_user_pet",
        "checkpoint_thread",
        ["user_id", "pet_id"],
    )
    op.create_index(
        "ix_checkpoint_thread_status_updated_at",
        "checkpoint_thread",
        ["status", "updated_at"],
    )

    op.create_table(
        "checkpoint_run_lock",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "acquired_at",
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
        sa.PrimaryKeyConstraint("thread_id", name="pk_checkpoint_run_lock"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["checkpoint_thread.thread_id"],
            name="fk_checkpoint_run_lock_thread_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_checkpoint_run_lock_expires_at",
        "checkpoint_run_lock",
        ["expires_at"],
    )
    op.create_index(
        "ix_checkpoint_run_lock_run_id",
        "checkpoint_run_lock",
        ["run_id"],
    )

    op.create_table(
        "checkpoint_segment_publish",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("segment_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["checkpoint_thread.thread_id"],
            name="fk_checkpoint_segment_publish_thread_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "segment_id",
            name="uq_checkpoint_segment_publish_thread_segment",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(SEGMENT_PUBLISH_STATUS_VALUES)})",
            name="ck_checkpoint_segment_publish_status",
        ),
    )
    op.create_index(
        "ix_checkpoint_segment_publish_thread_status",
        "checkpoint_segment_publish",
        ["thread_id", "status"],
    )
    op.create_index(
        "ix_checkpoint_segment_publish_thread_task",
        "checkpoint_segment_publish",
        ["thread_id", "task_id"],
    )
    op.create_index(
        "ix_checkpoint_segment_publish_published_at",
        "checkpoint_segment_publish",
        ["published_at"],
    )


def downgrade() -> None:
    """回滚 CheckpointStore 控制平面表结构升级。

    :return: None。
    """

    op.drop_index(
        "ix_checkpoint_segment_publish_published_at",
        table_name="checkpoint_segment_publish",
    )
    op.drop_index(
        "ix_checkpoint_segment_publish_thread_task",
        table_name="checkpoint_segment_publish",
    )
    op.drop_index(
        "ix_checkpoint_segment_publish_thread_status",
        table_name="checkpoint_segment_publish",
    )
    op.drop_table("checkpoint_segment_publish")

    op.drop_index("ix_checkpoint_run_lock_run_id", table_name="checkpoint_run_lock")
    op.drop_index(
        "ix_checkpoint_run_lock_expires_at",
        table_name="checkpoint_run_lock",
    )
    op.drop_table("checkpoint_run_lock")

    op.drop_index(
        "ix_checkpoint_thread_status_updated_at",
        table_name="checkpoint_thread",
    )
    op.drop_index("ix_checkpoint_thread_user_pet", table_name="checkpoint_thread")
    op.drop_index("ix_checkpoint_thread_user_id", table_name="checkpoint_thread")
    op.drop_table("checkpoint_thread")
