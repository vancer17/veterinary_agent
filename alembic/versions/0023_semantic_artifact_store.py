"""
=============================================================================
文件：alembic/versions/0023_semantic_artifact_store.py
作用：建立受限语义协作 DAG M11 的回合级权威 artifact 版本存储。
范围：覆盖 artifact version、gate event、幂等提交请求、stale marker 以及
      claim review / clarification gap 派生索引。
说明：本迁移只创建 append-only 表，不回填历史投影，不把测试报告或 M10
      preview 伪造成权威 artifact。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_semantic_artifact_store"
down_revision = "0022_semantic_dag_scheduler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 M11 回合级权威 artifact 与派生索引表。

    :return: 无返回值。
    """
    op.create_table(
        "semantic_artifact_versions",
        sa.Column(
            "artifact_id",
            sa.Text(),
            primary_key=True,
            comment="同一回合语义结果链的稳定 SHA-256 artifact 身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            primary_key=True,
            comment="回合语义结果链的不可变版本号，从 1 开始递增。",
        ),
        sa.Column(
            "artifact_kind",
            sa.Text(),
            nullable=False,
            comment="权威 artifact 类型；当前生产只允许 semantic_turn_result。",
        ),
        sa.Column(
            "artifact_reference",
            sa.Text(),
            nullable=False,
            comment="M11 稳定 artifact 引用，格式为 semantic-artifact://<id>/<version>。",
        ),
        sa.Column(
            "run_id",
            sa.Text(),
            nullable=False,
            comment="当前语义 artifact 所属的 DAG workflow 稳定标识。",
        ),
        sa.Column(
            "source_task_id",
            sa.Text(),
            nullable=False,
            comment="产生 Claim Inventory 的来源 PlanTask 稳定标识。",
        ),
        sa.Column(
            "turn_snapshot_digest",
            sa.Text(),
            nullable=False,
            comment="当前回合 TurnSnapshot canonical digest。",
        ),
        sa.Column(
            "source_proposal_digest",
            sa.Text(),
            nullable=False,
            comment="被审查或修复的 M06 Claim Inventory proposal digest。",
        ),
        sa.Column(
            "review_bundle_digest",
            sa.Text(),
            comment="产生当前版本的 M08 Review Bundle digest；修复版本可为空。",
        ),
        sa.Column(
            "payload",
            sa.JSONB(),
            nullable=False,
            comment="回合级不可变权威 payload，内嵌 claims、review section 与 repair lineage。",
        ),
        sa.Column(
            "payload_digest",
            sa.Text(),
            nullable=False,
            comment="权威 payload canonical JSON 的 SHA-256 digest。",
        ),
        sa.Column(
            "base_artifact_id",
            sa.Text(),
            comment="当前版本基于的 base artifact 链身份。",
        ),
        sa.Column(
            "base_version", sa.Integer(), comment="当前版本基于的 base artifact 版本。"
        ),
        sa.Column(
            "repair_depth",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="修复深度；生产当前只允许 0 或 1，禁止 repair of repair。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="artifact version 创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["base_artifact_id", "base_version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_artifact_versions_base_version",
        ),
        sa.UniqueConstraint(
            "artifact_reference", name="uq_semantic_artifact_versions_reference"
        ),
        sa.UniqueConstraint(
            "source_proposal_digest",
            "review_bundle_digest",
            name="uq_semantic_artifact_versions_source_review",
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('semantic_turn_result')",
            name="ck_semantic_artifact_versions_kind",
        ),
        sa.CheckConstraint(
            "version >= 1 AND repair_depth >= 0 AND repair_depth <= 1",
            name="ck_semantic_artifact_versions_version_depth",
        ),
        sa.CheckConstraint(
            "(base_artifact_id IS NULL AND base_version IS NULL) OR (base_artifact_id IS NOT NULL AND base_version IS NOT NULL)",
            name="ck_semantic_artifact_versions_base_pair",
        ),
        comment="受限语义协作回合级权威 artifact 版本表；payload 只允许追加新版本，不允许原地覆盖。",
    )
    op.create_index(
        "idx_semantic_artifact_versions_run_source_snapshot",
        "semantic_artifact_versions",
        ["run_id", "source_task_id", "turn_snapshot_digest", "version"],
    )
    op.create_table(
        "semantic_artifact_gate_events",
        sa.Column(
            "artifact_id",
            sa.Text(),
            primary_key=True,
            comment="gate event 绑定的回合语义 artifact 链身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            primary_key=True,
            comment="gate event 绑定的不可变 artifact 版本。",
        ),
        sa.Column(
            "sequence",
            sa.Integer(),
            primary_key=True,
            comment="同一 artifact version 内的状态事件顺序，从 1 开始。",
        ),
        sa.Column("from_gate", sa.Text(), comment="迁移前 gate 状态；初始事件可为空。"),
        sa.Column("to_gate", sa.Text(), nullable=False, comment="迁移后 gate 状态。"),
        sa.Column(
            "reason_code",
            sa.Text(),
            nullable=False,
            comment="gate 迁移的稳定工程原因。",
        ),
        sa.Column(
            "actor_type",
            sa.Text(),
            nullable=False,
            comment="触发 gate 迁移的系统 actor 类型。",
        ),
        sa.Column(
            "decision_digest",
            sa.Text(),
            nullable=False,
            comment="结构化决策 canonical digest，用于幂等和审计。",
        ),
        sa.Column(
            "decision",
            sa.JSONB(),
            nullable=False,
            server_default="{}",
            comment="gate 迁移附带的不可变结构化决策；不保存完整用户原文或 prompt。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="gate event 创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_artifact_gate_events_artifact_version",
        ),
        sa.CheckConstraint(
            "to_gate IN ('schema_valid', 'semantic_review_pending', 'semantic_review_supported', 'repair_required', 'clarification_required', 'repair_then_clarification_required', 'human_review_required', 'patch_applied', 'evidence_binding_pending', 'verified', 'repair_verified', 'disagreement', 'review_failed', 'blocked')",
            name="ck_semantic_artifact_gate_events_to_gate",
        ),
        sa.CheckConstraint(
            "actor_type IN ('system', 'human_reviewer', 'evidence_binding')",
            name="ck_semantic_artifact_gate_events_actor",
        ),
        comment="语义 artifact gate 状态事件表；当前状态由最大 sequence 派得，不修改权威 payload。",
    )
    op.create_index(
        "idx_semantic_artifact_gate_events_current",
        "semantic_artifact_gate_events",
        ["artifact_id", "version", "sequence"],
    )
    op.create_table(
        "semantic_artifact_commit_requests",
        sa.Column(
            "request_key",
            sa.Text(),
            primary_key=True,
            comment="M11 提交幂等键，例如 review digest 或 repair plan id。",
        ),
        sa.Column(
            "request_kind", sa.Text(), nullable=False, comment="M11 提交请求类型。"
        ),
        sa.Column(
            "artifact_id",
            sa.Text(),
            nullable=False,
            comment="请求产生的回合语义 artifact 链身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment="请求产生的不可变 artifact 版本。",
        ),
        sa.Column(
            "payload_digest",
            sa.Text(),
            nullable=False,
            comment="请求绑定 payload 的 canonical digest。",
        ),
        sa.Column(
            "result_reference",
            sa.Text(),
            nullable=False,
            comment="幂等重放时返回的稳定 artifact 引用。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="幂等提交请求创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_artifact_commit_requests_artifact_version",
        ),
        sa.CheckConstraint(
            "request_kind IN ('review_bundle', 'repair_patch_set', 'gate_event')",
            name="ck_semantic_artifact_commit_requests_kind",
        ),
        comment="M11 artifact 幂等提交请求表；同一 request key 重放返回既有结果，不同 payload 冲突。",
    )
    op.create_table(
        "semantic_artifact_stale_markers",
        sa.Column(
            "artifact_id",
            sa.Text(),
            primary_key=True,
            comment="被取代的 artifact 链身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            primary_key=True,
            comment="被取代的 artifact 版本。",
        ),
        sa.Column(
            "stale_reason",
            sa.Text(),
            primary_key=True,
            comment="artifact 被取代的稳定原因。",
        ),
        sa.Column(
            "replaced_by_artifact_id",
            sa.Text(),
            primary_key=True,
            comment="取代旧版本的新 artifact 链身份。",
        ),
        sa.Column(
            "replaced_by_version",
            sa.Integer(),
            primary_key=True,
            comment="取代旧版本的新 artifact 版本。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="stale marker 创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_artifact_stale_markers_artifact_version",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_artifact_id", "replaced_by_version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_artifact_stale_markers_replaced_by",
        ),
        sa.CheckConstraint(
            "stale_reason IN ('superseded_by_review', 'superseded_by_repair')",
            name="ck_semantic_artifact_stale_markers_reason",
        ),
        comment="语义 artifact stale 标记表；stale 只追加记录，不修改旧 payload，也不迁移旧审查结论。",
    )
    op.create_table(
        "semantic_claim_review_index",
        sa.Column(
            "artifact_id",
            sa.Text(),
            primary_key=True,
            comment="索引记录绑定的 artifact 链身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            primary_key=True,
            comment="索引记录绑定的 artifact 版本。",
        ),
        sa.Column(
            "claim_index",
            sa.Integer(),
            primary_key=True,
            comment="artifact payload 内的系统派生 claim 序号。",
        ),
        sa.Column(
            "run_id",
            sa.Text(),
            nullable=False,
            comment="索引记录所属 DAG workflow 标识。",
        ),
        sa.Column(
            "claim_digest",
            sa.Text(),
            nullable=False,
            comment="claim proposition canonical digest。",
        ),
        sa.Column(
            "proposition",
            sa.Text(),
            nullable=False,
            comment="自包含自然语言 claim proposition。",
        ),
        sa.Column(
            "review_status",
            sa.Text(),
            nullable=False,
            comment="该 claim 在当前版本中的派生审查状态。",
        ),
        sa.Column(
            "repair_dimensions",
            sa.JSONB(),
            nullable=False,
            server_default="[]",
            comment="该 claim 需要修复的 M08 维度集合；仅用于查询统计。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="派生索引创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_claim_review_index_artifact_version",
        ),
        sa.CheckConstraint(
            "review_status IN ('supported', 'repair_required', 'clarification_required', 'human_review_required', 'disagreement', 'repair_pending')",
            name="ck_semantic_claim_review_index_status",
        ),
        comment="语义 claim review 派生索引表；仅服务查询统计，可由权威 artifact payload 重建。",
    )
    op.create_index(
        "idx_semantic_claim_review_index_run_status",
        "semantic_claim_review_index",
        ["run_id", "review_status"],
    )
    op.create_index(
        "idx_semantic_claim_review_index_dimension",
        "semantic_claim_review_index",
        ["run_id", "claim_digest"],
    )
    op.create_table(
        "semantic_clarification_gap_index",
        sa.Column(
            "artifact_id",
            sa.Text(),
            primary_key=True,
            comment="gap 索引绑定的 artifact 链身份。",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            primary_key=True,
            comment="gap 索引绑定的 artifact 版本。",
        ),
        sa.Column(
            "gap_id",
            sa.Text(),
            primary_key=True,
            comment="M08 生成的结构化 clarification gap 稳定标识。",
        ),
        sa.Column(
            "run_id", sa.Text(), nullable=False, comment="gap 所属 DAG workflow 标识。"
        ),
        sa.Column(
            "claim_index",
            sa.Integer(),
            nullable=False,
            comment="gap 绑定的 claim 序号。",
        ),
        sa.Column(
            "claim_digest",
            sa.Text(),
            nullable=False,
            comment="gap 绑定的 claim proposition digest。",
        ),
        sa.Column(
            "ambiguous_dimension",
            sa.Text(),
            nullable=False,
            comment="来源绑定缺失的 M08 维度。",
        ),
        sa.Column(
            "required_binding_type",
            sa.Text(),
            nullable=False,
            comment="后续需要消解的结构化绑定类型。",
        ),
        sa.Column("status", sa.Text(), nullable=False, comment="gap 当前派生状态。"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="gap 派生索引创建时间。",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            [
                "semantic_artifact_versions.artifact_id",
                "semantic_artifact_versions.version",
            ],
            name="fk_semantic_clarification_gap_index_artifact_version",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'stale')",
            name="ck_semantic_clarification_gap_index_status",
        ),
        comment="语义 clarification gap 派生索引表；不是自动追问指令，也不是 verified 事实权威。",
    )
    op.create_index(
        "idx_semantic_clarification_gap_index_run_status",
        "semantic_clarification_gap_index",
        ["run_id", "status"],
    )


def downgrade() -> None:
    """回滚 M11 权威 artifact 存储表。

    :return: 无返回值。
    """
    op.drop_table("semantic_clarification_gap_index")
    op.drop_table("semantic_claim_review_index")
    op.drop_table("semantic_artifact_stale_markers")
    op.drop_table("semantic_artifact_commit_requests")
    op.drop_table("semantic_artifact_gate_events")
    op.drop_index(
        "idx_semantic_artifact_versions_run_source_snapshot",
        table_name="semantic_artifact_versions",
    )
    op.drop_table("semantic_artifact_versions")
