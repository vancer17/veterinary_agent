"""
=============================================================================
文件：alembic/versions/0021_clinical_safety_emergency_asset_codes.py
作用：为急诊临床安全资产建立 opaque 资产级信号编码数据库契约。
范围：约束已发布 clinical_safety_assets 的急诊编码命名空间，并保证一条
      enabled emergency_red_flag 资产对应一个独立 code。
说明：本迁移不生成、推断或修复资产编码；存在泛化或重复存量数据时迁移失败，
      必须先由资产治理流程导入已审核的显式 code。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_clinical_safety_emergency_asset_codes"
down_revision = "0020_clinical_safety_scope_value_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行急诊资产编码契约正向迁移。

    :return: 无返回值；存量编码不满足阶段 4 契约时迁移失败。
    """
    invalid_codes = _invalid_approved_emergency_codes()
    if invalid_codes:
        raise RuntimeError(
            "approved clinical safety emergency codes are not stage-4 compliant: "
            f"{invalid_codes[:10]}"
        )
    duplicate_codes = _duplicate_approved_emergency_codes()
    if duplicate_codes:
        raise RuntimeError(
            "approved clinical safety emergency codes are duplicated: "
            f"{duplicate_codes[:10]}"
        )
    op.create_check_constraint(
        "ck_clinical_safety_assets_emergency_code_namespace",
        "clinical_safety_assets",
        "(review_status <> 'approved') OR "
        "(asset_type <> 'emergency_red_flag' OR code ~ '^EMERGENCY_MODE_[A-Z0-9]{10}$')",
    )
    op.create_index(
        "uq_clinical_safety_assets_emergency_code",
        "clinical_safety_assets",
        ["code"],
        unique=True,
        postgresql_where=sa.text(
            "asset_type = 'emergency_red_flag' "
            "AND review_status = 'approved' "
            "AND enabled IS TRUE"
        ),
    )


def downgrade() -> None:
    """移除急诊资产编码契约。

    :return: 无返回值；回滚后数据库不再阻止泛化急诊编码，仅用于开发环境诊断。
    """
    op.drop_index(
        "uq_clinical_safety_assets_emergency_code",
        table_name="clinical_safety_assets",
    )
    op.drop_constraint(
        "ck_clinical_safety_assets_emergency_code_namespace",
        "clinical_safety_assets",
        type_="check",
    )


def _invalid_approved_emergency_codes() -> list[dict[str, object]]:
    """查询不满足阶段 4 命名空间的已发布急诊编码。

    :return: 返回包含资产标识和非法编码的存量数据列表。
    """
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT asset_id, code
            FROM clinical_safety_assets
            WHERE review_status = 'approved'
              AND enabled IS TRUE
              AND asset_type = 'emergency_red_flag'
              AND code !~ '^EMERGENCY_MODE_[A-Z0-9]{10}$'
            ORDER BY asset_id
            """
            )
        )
        .mappings()
    )
    return [dict(row) for row in rows]


def _duplicate_approved_emergency_codes() -> list[dict[str, object]]:
    """查询已发布急诊资产中的重复编码。

    :return: 返回包含重复编码和资产数量的存量数据列表。
    """
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT code, count(*) AS asset_count
            FROM clinical_safety_assets
            WHERE review_status = 'approved'
              AND enabled IS TRUE
              AND asset_type = 'emergency_red_flag'
            GROUP BY code
            HAVING count(*) > 1
            ORDER BY code
            """
            )
        )
        .mappings()
    )
    return [dict(row) for row in rows]
