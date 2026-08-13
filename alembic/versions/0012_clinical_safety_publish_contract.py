"""
文件：alembic/versions/0012_clinical_safety_publish_contract.py
作用：收紧临床安全资产与向量 chunk 的发布态数据库契约。
范围：调整 clinical_safety_assets 与 clinical_safety_chunks 默认状态以及发布态检查约束。
说明：数据库层禁止资产默认进入 approved/enabled 状态，避免运行时通过缺省值或兜底编码绕过资产治理流程。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_clinical_safety_publish_contract"
down_revision = "0011_clinical_safety_vector_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，建立临床安全发布态数据库约束。

    :return: 无返回值。
    """
    _drop_default("clinical_safety_assets", "enabled")
    _drop_default("clinical_safety_assets", "review_status")
    _drop_default("clinical_safety_assets", "severity")
    _drop_default("clinical_safety_assets", "action_class")
    _drop_default("clinical_safety_chunks", "enabled")
    _drop_default("clinical_safety_chunks", "review_status")

    op.alter_column("clinical_safety_assets", "enabled", server_default=sa.false())
    op.alter_column("clinical_safety_assets", "review_status", server_default="pending")
    op.alter_column("clinical_safety_chunks", "enabled", server_default=sa.false())
    op.alter_column("clinical_safety_chunks", "review_status", server_default="pending")

    op.execute(
        """
        UPDATE clinical_safety_assets
        SET enabled = false,
            review_status = 'pending',
            published_at = NULL
        WHERE review_status = 'approved'
          AND (enabled IS DISTINCT FROM true OR published_at IS NULL)
        """
    )
    op.execute(
        """
        UPDATE clinical_safety_assets
        SET enabled = false,
            review_status = 'pending',
            published_at = NULL
        WHERE review_status = 'approved'
          AND (
              btrim(code) = ''
              OR code = 'CLINICAL_SAFETY_UNKNOWN'
              OR code ~ '^CLINICAL_SAFETY_[0-9_]+$'
          )
        """
    )
    op.execute(
        """
        UPDATE clinical_safety_chunks
        SET enabled = false,
            review_status = 'pending'
        WHERE review_status = 'approved'
          AND (
              enabled IS DISTINCT FROM true
              OR embedding IS NULL
              OR embedding_model IS NULL
              OR embedding_dimension IS NULL
              OR btrim(content_hash) = ''
          )
        """
    )

    op.create_check_constraint(
        "ck_clinical_safety_assets_asset_id_nonempty",
        "clinical_safety_assets",
        "asset_id <> ''",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_code_nonempty",
        "clinical_safety_assets",
        "(review_status <> 'approved') OR btrim(code) <> ''",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_code_not_generated_fallback",
        "clinical_safety_assets",
        "(review_status <> 'approved') OR "
        "(code <> 'CLINICAL_SAFETY_UNKNOWN' AND code !~ '^CLINICAL_SAFETY_[0-9_]+$')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_asset_type",
        "clinical_safety_assets",
        "asset_type IN ('toxin', 'human_drug', 'plant_toxin', 'chemical_toxin', 'emergency_red_flag', 'danger_pattern')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_severity",
        "clinical_safety_assets",
        "severity IN ('info', 'caution', 'urgent', 'blocked')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_action_class",
        "clinical_safety_assets",
        "action_class IN ('emergency', 'same_day_visit', 'urgent_visit', 'safety_warning')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_review_status",
        "clinical_safety_assets",
        "review_status IN ('pending', 'approved', 'rejected', 'quarantined')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_publish_state",
        "clinical_safety_assets",
        "(review_status = 'approved' AND enabled IS TRUE AND published_at IS NOT NULL) OR "
        "(review_status <> 'approved' AND enabled IS FALSE AND published_at IS NULL)",
    )

    op.create_check_constraint(
        "ck_clinical_safety_chunks_chunk_id_nonempty",
        "clinical_safety_chunks",
        "chunk_id <> ''",
    )
    op.create_check_constraint(
        "ck_clinical_safety_chunks_chunk_type",
        "clinical_safety_chunks",
        "chunk_type IN ('recognition', 'clinical_risk', 'triage_action')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_chunks_review_status",
        "clinical_safety_chunks",
        "review_status IN ('pending', 'approved', 'rejected', 'quarantined')",
    )
    op.create_check_constraint(
        "ck_clinical_safety_chunks_publish_state",
        "clinical_safety_chunks",
        "(review_status = 'approved' AND enabled IS TRUE AND embedding IS NOT NULL "
        "AND embedding_model IS NOT NULL AND embedding_dimension IS NOT NULL AND btrim(content_hash) <> '') OR "
        "(review_status <> 'approved' AND enabled IS FALSE)",
    )


def downgrade() -> None:
    """执行 Alembic 回滚迁移，移除临床安全发布态数据库约束。

    :return: 无返回值。
    """
    op.drop_constraint("ck_clinical_safety_chunks_publish_state", "clinical_safety_chunks", type_="check")
    op.drop_constraint("ck_clinical_safety_chunks_review_status", "clinical_safety_chunks", type_="check")
    op.drop_constraint("ck_clinical_safety_chunks_chunk_type", "clinical_safety_chunks", type_="check")
    op.drop_constraint("ck_clinical_safety_chunks_chunk_id_nonempty", "clinical_safety_chunks", type_="check")

    op.drop_constraint("ck_clinical_safety_assets_publish_state", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_review_status", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_action_class", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_severity", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_asset_type", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_code_not_generated_fallback", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_code_nonempty", "clinical_safety_assets", type_="check")
    op.drop_constraint("ck_clinical_safety_assets_asset_id_nonempty", "clinical_safety_assets", type_="check")
    op.alter_column("clinical_safety_assets", "severity", server_default="caution")
    op.alter_column("clinical_safety_assets", "action_class", server_default="safety_warning")
    op.alter_column("clinical_safety_assets", "enabled", server_default=sa.true())
    op.alter_column("clinical_safety_assets", "review_status", server_default="approved")
    op.alter_column("clinical_safety_chunks", "enabled", server_default=sa.true())
    op.alter_column("clinical_safety_chunks", "review_status", server_default="approved")


def _drop_default(table_name: str, column_name: str) -> None:
    """移除指定列旧默认值。

    :param table_name: 数据库表名。
    :param column_name: 数据库列名。
    :return: 无返回值。
    """
    op.execute(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT")
