"""
=============================================================================
文件：alembic/versions/0022_semantic_dag_scheduler.py
作用：新增受限语义协作 DAG M04 的只读投影表。
范围：建立 semantic_dag_run_projections 与 semantic_dag_task_projections 表；
      不创建任务队列、attempt、租约或 worker 调度状态。
说明：durable 执行历史与恢复权威在 Temporal；本迁移仅服务 API 查询、
      审计投影和工程排障。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_semantic_dag_scheduler"
down_revision = "0021_clinical_safety_emergency_asset_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行语义协作 DAG 投影表正向迁移。

    :return: 无返回值。
    """
    op.create_table(
        "semantic_dag_run_projections",
        sa.Column("run_id", sa.Text(), primary_key=True, comment="由权威 Plan IR digest 派生的语义 DAG workflow 稳定标识。"),
        sa.Column("contract_version", sa.Text(), nullable=False, comment="DAG 调度契约版本，用于投影兼容审计。"),
        sa.Column("workflow_id", sa.Text(), nullable=False, comment="对应的 Temporal workflow 标识。"),
        sa.Column("plan_id", sa.Text(), nullable=False, comment="当前 DAG workflow 绑定的 Plan IR canonical digest。"),
        sa.Column("turn_id", sa.Text(), nullable=False, comment="当前 DAG workflow 绑定的 TurnSnapshot 回合标识。"),
        sa.Column("snapshot_digest", sa.Text(), nullable=False, comment="当前 DAG workflow 全部任务共享的 TurnSnapshot digest。"),
        sa.Column("skill_catalog_digest", sa.Text(), nullable=False, comment="创建计划时冻结的 SkillCatalog 契约 digest。"),
        sa.Column("plan_policy_digest", sa.Text(), nullable=False, comment="创建计划时冻结的 PlanPolicy 契约 digest。"),
        sa.Column("status", sa.Text(), nullable=False, comment="workflow 业务状态投影，不是执行队列状态。"),
        sa.Column("policy", postgresql.JSONB(), nullable=False, comment="workflow 启动时固化的并发、超时与重试策略。"),
        sa.Column("task_policies", postgresql.JSONB(), nullable=False, comment="由 SkillCatalog 失败策略投影出的任务语义重试策略集合。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="run 投影创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="run 投影最近更新时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), comment="workflow 业务终态投影时间。"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'completed_with_failures', 'canceled', 'timed_out', 'failed')",
            name="ck_semantic_dag_run_projections_status",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            name="uq_semantic_dag_run_projections_workflow_id",
        ),
        comment="受限语义协作 DAG workflow 只读投影表；执行历史与恢复权威在 Temporal，本表不参与任务队列或租约调度。",
    )
    op.create_index(
        "idx_semantic_dag_run_projections_turn_status",
        "semantic_dag_run_projections",
        ["turn_id", "status"],
    )
    op.create_table(
        "semantic_dag_task_projections",
        sa.Column("run_id", sa.Text(), sa.ForeignKey("semantic_dag_run_projections.run_id", ondelete="CASCADE"), primary_key=True, comment="任务投影所属 DAG workflow 稳定标识。"),
        sa.Column("task_id", sa.Text(), primary_key=True, comment="权威 PlanTask 稳定标识。"),
        sa.Column("skill_id", sa.Text(), nullable=False, comment="任务绑定的 SKILL 稳定标识。"),
        sa.Column("skill_version", sa.Text(), nullable=False, comment="任务绑定的精确 SKILL 版本。"),
        sa.Column("target_envelope_id", sa.Text(), nullable=False, comment="任务绑定的 turn 或 claim envelope 标识。"),
        sa.Column("terminal_state", sa.Text(), comment="任务业务终态；workflow 未完成时可以为空。"),
        sa.Column("artifact_reference", sa.Text(), comment="成功终态绑定的已验证 artifact 引用。"),
        sa.Column("failure_code", sa.Text(), comment="失败终态对应的稳定 SKILL 失败码。"),
        sa.Column("failure_message", sa.Text(), comment="失败终态对应的工程排障说明。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="任务投影创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="任务投影最近更新时间。"),
        sa.CheckConstraint(
            "terminal_state IN ('verified', 'repair_verified', 'not_applicable', 'blocked', 'disagreement', 'repair_exhausted', 'repair_failed', 'dependency_failed', 'review_failed', 'context_budget_exceeded', 'timeout')",
            name="ck_semantic_dag_task_projections_terminal_state",
        ),
        comment="受限语义协作 DAG 任务终态投影表；不保存 ready/running/attempt 或租约等执行队列状态。",
    )


def downgrade() -> None:
    """回滚语义协作 DAG 投影表迁移。

    :return: 无返回值。
    """
    op.drop_table("semantic_dag_task_projections")
    op.drop_index(
        "idx_semantic_dag_run_projections_turn_status",
        table_name="semantic_dag_run_projections",
    )
    op.drop_table("semantic_dag_run_projections")
