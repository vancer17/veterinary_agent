"""
=============================================================================
文件：alembic/versions/0017_persistent_background_tasks.py
作用：新增可持久化后台任务表。
范围：建立 background_tasks 表及任务类型、业务幂等键、租约、重试与审计字段；
      不创建业务候选事实表，不实现长期事实治理或 Mem0 投影链路。
说明：本迁移服务于“主回合入队 + worker 异步执行”的后台任务基础设施，
      PostgreSQL 仍是任务状态唯一可信源。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017_persistent_background_tasks"
down_revision = "0016_output_safety_candidate_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行可持久化后台任务表的正向迁移。

    :return: 无返回值。
    """
    op.create_table(
        "background_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="后台任务内部主键。"),
        sa.Column("task_id", sa.Text(), nullable=False, comment="后台任务稳定标识，用于响应 metadata、worker 审计和外部排障。"),
        sa.Column("task_type", sa.Text(), nullable=False, comment="后台任务类型，例如 memory_candidate_extraction。"),
        sa.Column("business_key", sa.Text(), nullable=False, comment="后台任务业务幂等键，同一任务类型下必须唯一。"),
        sa.Column("ordering_key", sa.Text(), nullable=False, comment="后台任务顺序约束键，例如 user_id:pet_id:session_id。"),
        sa.Column("user_id", sa.Text(), nullable=False, comment="任务来源用户标识。"),
        sa.Column("pet_id", sa.Text(), nullable=False, comment="任务来源宠物标识。"),
        sa.Column("session_id", sa.Text(), nullable=False, comment="任务来源会话标识。"),
        sa.Column("source_turn_id", sa.Text(), comment="任务来源回合标识。"),
        sa.Column("source_request_id", sa.Text(), comment="任务来源请求标识。"),
        sa.Column("source_trace_id", sa.Text(), comment="任务来源链路追踪标识。"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending", comment="后台任务状态。"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100", comment="后台任务优先级，数值越小越优先。"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="任务最早可执行时间。"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="任务已执行尝试次数。"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5", comment="任务最大执行尝试次数。"),
        sa.Column("locked_by", sa.Text(), comment="当前持有任务租约的 worker 标识。"),
        sa.Column("locked_until", sa.DateTime(timezone=True), comment="当前任务租约过期时间。"),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="任务执行载荷。",
        ),
        sa.Column("result", postgresql.JSONB(), comment="任务执行结果或失败摘要。"),
        sa.Column("last_error", postgresql.JSONB(), comment="最近一次失败的结构化错误信息。"),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="任务附加审计元数据。",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), comment="任务首次或最近一次开始执行时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), comment="任务最终完成时间。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="任务创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="任务最近更新时间。"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retrying', 'succeeded', 'dead_letter', 'cancelled')",
            name="ck_background_tasks_status",
        ),
        sa.UniqueConstraint("task_id", name="uq_background_tasks_task_id"),
        sa.UniqueConstraint("task_type", "business_key", name="uq_background_tasks_type_business_key"),
        comment="持久化后台任务表，用于保存可重试的异步后置处理任务、业务幂等键与执行审计信息。",
    )
    op.create_index(
        "idx_background_tasks_status_run_after_priority",
        "background_tasks",
        ["status", "run_after", "priority"],
    )
    op.create_index(
        "idx_background_tasks_locked_until",
        "background_tasks",
        ["locked_until"],
    )
    op.create_index(
        "idx_background_tasks_scope",
        "background_tasks",
        ["user_id", "pet_id", "session_id"],
    )
    op.create_index(
        "idx_background_tasks_ordering_key",
        "background_tasks",
        ["ordering_key"],
    )


def downgrade() -> None:
    """执行可持久化后台任务表的回滚迁移。

    :return: 无返回值。
    """
    op.drop_index("idx_background_tasks_ordering_key", table_name="background_tasks")
    op.drop_index("idx_background_tasks_scope", table_name="background_tasks")
    op.drop_index("idx_background_tasks_locked_until", table_name="background_tasks")
    op.drop_index("idx_background_tasks_status_run_after_priority", table_name="background_tasks")
    op.drop_table("background_tasks")
