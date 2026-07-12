##################################################################################################
# 文件: migrations/versions/20260712_0003_logic_trace_store.py
# 作用: 创建 LogicTraceStore 逻辑链留痕表，覆盖 trace 主记录、事件、调用摘要、artifact、投影和 outbox。
# 边界: 仅定义 LogicTraceStore 自有表结构；不写入业务数据，不创建 VetTraceSchema 或外部事件总线资源。
##################################################################################################
"""create logic trace store tables

Revision ID: 20260712_0003
Revises: 20260709_0002
Create Date: 2026-07-12 00:03:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0003"
down_revision: str | None = "20260709_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRACE_STATUS_VALUES: tuple[str, ...] = ("open", "finalized", "degraded")
TRACE_FINAL_STATUS_VALUES: tuple[str, ...] = (
    "completed",
    "failed",
    "cancelled",
    "recoverable",
)
TRACE_CALL_TYPE_VALUES: tuple[str, ...] = (
    "model",
    "tool",
    "rag",
    "agent_run",
    "policy_decision",
    "graph_event",
)
TRACE_CALL_STATUS_VALUES: tuple[str, ...] = (
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
)
TRACE_ARTIFACT_TYPE_VALUES: tuple[str, ...] = (
    "prompt_summary",
    "output_summary",
    "rag_summary",
    "draft_response",
    "reviewed_draft",
    "final_response",
    "other",
)
TRACE_PROJECTION_TYPE_VALUES: tuple[str, ...] = (
    "timeline_view",
    "decision_view",
    "artifact_view",
    "reasoning_display",
)
TRACE_OUTBOX_STATUS_VALUES: tuple[str, ...] = ("pending", "sent", "failed")


def _quoted_values(values: tuple[str, ...]) -> str:
    """构建 check constraint 使用的单引号字符串列表。

    :param values: 允许写入数据库状态字段的稳定字符串值。
    :return: 适合放入 SQL IN 表达式的字符串片段。
    """

    return ", ".join(f"'{value}'" for value in values)


def _json_metadata_type() -> sa.types.TypeEngine[object]:
    """构建 JSON 字段使用的跨方言类型。

    :return: PostgreSQL 下使用 JSONB、其他测试方言下使用通用 JSON 的 SQLAlchemy 类型。
    """

    return sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()),
        "postgresql",
    )


def upgrade() -> None:
    """应用 LogicTraceStore 表结构升级。

    :return: None。
    """

    op.create_table(
        "logic_trace",
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("pet_id", sa.Text(), nullable=False),
        sa.Column("params_version", sa.Text(), nullable=False),
        sa.Column("config_snapshot_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("capture_policy_ref", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("final_status", sa.Text(), nullable=True),
        sa.Column("user_message_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "summary",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("trace_id", name="pk_logic_trace"),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(TRACE_STATUS_VALUES)})",
            name="ck_logic_trace_status",
        ),
        sa.CheckConstraint(
            (
                "final_status IS NULL OR "
                f"final_status IN ({_quoted_values(TRACE_FINAL_STATUS_VALUES)})"
            ),
            name="ck_logic_trace_final_status",
        ),
    )
    op.create_index("ix_logic_trace_request_id", "logic_trace", ["request_id"])
    op.create_index("ix_logic_trace_run_id", "logic_trace", ["run_id"])
    op.create_index(
        "ix_logic_trace_session_started_at",
        "logic_trace",
        ["session_id", "started_at"],
    )
    op.create_index(
        "ix_logic_trace_idempotency_key",
        "logic_trace",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_logic_trace_status_updated_at",
        "logic_trace",
        ["status", "updated_at"],
    )

    op.create_table(
        "logic_trace_event",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source_component", sa.Text(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=True),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("segment_id", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.Text(), nullable=True),
        sa.Column("output_hash", sa.Text(), nullable=True),
        sa.Column(
            "summary",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "business_payload",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("schema_ref", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_logic_trace_event"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["logic_trace.trace_id"],
            name="fk_logic_trace_event_trace_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_logic_trace_event_trace_created_at",
        "logic_trace_event",
        ["trace_id", "created_at"],
    )
    op.create_index(
        "ix_logic_trace_event_type_created_at",
        "logic_trace_event",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_logic_trace_event_segment",
        "logic_trace_event",
        ["trace_id", "segment_id"],
    )

    op.create_table(
        "logic_trace_call_summary",
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("call_type", sa.Text(), nullable=False),
        sa.Column("source_component", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("input_ref", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column(
            "usage",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "summary",
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
        sa.PrimaryKeyConstraint("call_id", name="pk_logic_trace_call_summary"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["logic_trace.trace_id"],
            name="fk_logic_trace_call_summary_trace_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"call_type IN ({_quoted_values(TRACE_CALL_TYPE_VALUES)})",
            name="ck_logic_trace_call_summary_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(TRACE_CALL_STATUS_VALUES)})",
            name="ck_logic_trace_call_summary_status",
        ),
    )
    op.create_index(
        "ix_logic_trace_call_trace_created_at",
        "logic_trace_call_summary",
        ["trace_id", "created_at"],
    )
    op.create_index(
        "ix_logic_trace_call_type_status",
        "logic_trace_call_summary",
        ["call_type", "status"],
    )

    op.create_table(
        "logic_trace_artifact",
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("visibility_policy", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("artifact_id", name="pk_logic_trace_artifact"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["logic_trace.trace_id"],
            name="fk_logic_trace_artifact_trace_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"artifact_type IN ({_quoted_values(TRACE_ARTIFACT_TYPE_VALUES)})",
            name="ck_logic_trace_artifact_type",
        ),
    )
    op.create_index(
        "ix_logic_trace_artifact_trace_type",
        "logic_trace_artifact",
        ["trace_id", "artifact_type"],
    )

    op.create_table(
        "logic_trace_projection",
        sa.Column("projection_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("projection_type", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column(
            "view_payload",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("projection_id", name="pk_logic_trace_projection"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["logic_trace.trace_id"],
            name="fk_logic_trace_projection_trace_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "trace_id",
            "projection_type",
            "version",
            name="uq_logic_trace_projection_trace_type_version",
        ),
        sa.CheckConstraint(
            f"projection_type IN ({_quoted_values(TRACE_PROJECTION_TYPE_VALUES)})",
            name="ck_logic_trace_projection_type",
        ),
    )
    op.create_index(
        "ix_logic_trace_projection_trace_type",
        "logic_trace_projection",
        ["trace_id", "projection_type"],
    )

    op.create_table(
        "logic_trace_outbox",
        sa.Column("outbox_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            _json_metadata_type(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("outbox_id", name="pk_logic_trace_outbox"),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["logic_trace.trace_id"],
            name="fk_logic_trace_outbox_trace_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted_values(TRACE_OUTBOX_STATUS_VALUES)})",
            name="ck_logic_trace_outbox_status",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_logic_trace_outbox_retry_count_non_negative",
        ),
    )
    op.create_index(
        "ix_logic_trace_outbox_status_next_retry",
        "logic_trace_outbox",
        ["status", "next_retry_at"],
    )
    op.create_index(
        "ix_logic_trace_outbox_trace",
        "logic_trace_outbox",
        ["trace_id"],
    )


def downgrade() -> None:
    """回滚 LogicTraceStore 表结构升级。

    :return: None。
    """

    op.drop_index("ix_logic_trace_outbox_trace", table_name="logic_trace_outbox")
    op.drop_index(
        "ix_logic_trace_outbox_status_next_retry",
        table_name="logic_trace_outbox",
    )
    op.drop_table("logic_trace_outbox")

    op.drop_index(
        "ix_logic_trace_projection_trace_type",
        table_name="logic_trace_projection",
    )
    op.drop_table("logic_trace_projection")

    op.drop_index(
        "ix_logic_trace_artifact_trace_type",
        table_name="logic_trace_artifact",
    )
    op.drop_table("logic_trace_artifact")

    op.drop_index(
        "ix_logic_trace_call_type_status",
        table_name="logic_trace_call_summary",
    )
    op.drop_index(
        "ix_logic_trace_call_trace_created_at",
        table_name="logic_trace_call_summary",
    )
    op.drop_table("logic_trace_call_summary")

    op.drop_index("ix_logic_trace_event_segment", table_name="logic_trace_event")
    op.drop_index(
        "ix_logic_trace_event_type_created_at",
        table_name="logic_trace_event",
    )
    op.drop_index(
        "ix_logic_trace_event_trace_created_at",
        table_name="logic_trace_event",
    )
    op.drop_table("logic_trace_event")

    op.drop_index("ix_logic_trace_status_updated_at", table_name="logic_trace")
    op.drop_index("ix_logic_trace_idempotency_key", table_name="logic_trace")
    op.drop_index("ix_logic_trace_session_started_at", table_name="logic_trace")
    op.drop_index("ix_logic_trace_run_id", table_name="logic_trace")
    op.drop_index("ix_logic_trace_request_id", table_name="logic_trace")
    op.drop_table("logic_trace")
