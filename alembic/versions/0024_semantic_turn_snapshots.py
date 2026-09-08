"""
=============================================================================
文件：alembic/versions/0024_semantic_turn_snapshots.py
作用：建立受限语义协作 DAG M02 的不可变 TurnSnapshot 存档表。
范围：覆盖 context digest 主键、turn 身份唯一约束、可信范围索引以及
      M04 任务终态 clarification_required 契约扩展。
说明：本迁移不回填旧会话、不把旧问诊状态转换为快照，也不修改既有语义
      artifact 版本链。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_semantic_turn_snapshots"
down_revision = "0023_semantic_artifact_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 TurnSnapshot 存档并扩展 M04 任务终态契约。

    :return: 无返回值。
    """
    op.create_table(
        "semantic_turn_snapshots",
        sa.Column(
            "context_digest",
            sa.Text(),
            primary_key=True,
            comment="TurnSnapshot 权威字段的 canonical SHA-256 digest。",
        ),
        sa.Column(
            "turn_id",
            sa.Text(),
            nullable=False,
            comment="当前回合稳定标识；禁止同 turn_id 写入不同 snapshot。",
        ),
        sa.Column(
            "turn_index",
            sa.Integer(),
            nullable=False,
            comment="当前回合在会话中的零基序号。",
        ),
        sa.Column("user_id", sa.Text(), nullable=False, comment="快照构建时的可信用户范围。"),
        sa.Column(
            "session_id", sa.Text(), nullable=False, comment="快照构建时的可信会话范围。"
        ),
        sa.Column("pet_id", sa.Text(), nullable=False, comment="快照构建时的可信宠物范围。"),
        sa.Column(
            "snapshot_version",
            sa.Text(),
            nullable=False,
            comment="TurnSnapshot 契约精确版本。",
        ),
        sa.Column(
            "snapshot_json",
            sa.Text(),
            nullable=False,
            comment="通过 strict Pydantic 校验的 TurnSnapshot canonical JSON；不包含 prompt 或模型输出。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="快照写入时间。",
        ),
        sa.CheckConstraint(
            "turn_index >= 0",
            name="ck_semantic_turn_snapshots_turn_index",
        ),
        sa.CheckConstraint(
            "snapshot_version = '1.0.0'",
            name="ck_semantic_turn_snapshots_version",
        ),
        sa.UniqueConstraint(
            "turn_id",
            name="uq_semantic_turn_snapshots_turn_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "session_id",
            "pet_id",
            "turn_index",
            name="uq_semantic_turn_snapshots_scope_turn_index",
        ),
        comment="受限语义协作 TurnSnapshot 不可变存档表；context digest 是读取身份，不保存模型输出或领域状态。",
    )
    op.create_index(
        "idx_semantic_turn_snapshots_scope_turn",
        "semantic_turn_snapshots",
        ["user_id", "session_id", "pet_id", "turn_index"],
    )
    op.drop_constraint(
        "ck_semantic_dag_task_projections_terminal_state",
        "semantic_dag_task_projections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_semantic_dag_task_projections_terminal_state",
        "semantic_dag_task_projections",
        "terminal_state IN ('verified', 'repair_verified', 'clarification_required', 'not_applicable', 'blocked', 'disagreement', 'repair_exhausted', 'repair_failed', 'dependency_failed', 'review_failed', 'context_budget_exceeded', 'timeout')",
    )


def downgrade() -> None:
    """回滚 TurnSnapshot 存档与 M04 终态扩展。

    :return: 无返回值。
    """
    op.drop_constraint(
        "ck_semantic_dag_task_projections_terminal_state",
        "semantic_dag_task_projections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_semantic_dag_task_projections_terminal_state",
        "semantic_dag_task_projections",
        "terminal_state IN ('verified', 'repair_verified', 'not_applicable', 'blocked', 'disagreement', 'repair_exhausted', 'repair_failed', 'dependency_failed', 'review_failed', 'context_budget_exceeded', 'timeout')",
    )
    op.drop_index(
        "idx_semantic_turn_snapshots_scope_turn",
        table_name="semantic_turn_snapshots",
    )
    op.drop_table("semantic_turn_snapshots")
