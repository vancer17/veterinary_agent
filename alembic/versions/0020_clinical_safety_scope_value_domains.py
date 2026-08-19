"""
=============================================================================
文件：alembic/versions/0020_clinical_safety_scope_value_domains.py
作用：为临床安全资产的结构化适用范围增加数据库层值域约束。
范围：约束 clinical_safety_assets 的 species_scope、sex_scope 和 age_scope
      只能包含运行时受控枚举值，防止资产治理域外的直接写入制造静默失配。
说明：空数组表示维度不限制，继续允许；本迁移不改变召回过滤语义，也不引入
      任何运行时回退路径。
=============================================================================
"""

from __future__ import annotations

from alembic import op


revision = "0020_clinical_safety_scope_value_domains"
down_revision = "0019_followup_rag_miss_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行临床安全资产范围值域约束的正向迁移。

    :return: 无返回值；存在越界存量数据时迁移失败，由资产治理域显式修复。
    """
    op.create_check_constraint(
        "ck_clinical_safety_assets_species_scope_domain",
        "clinical_safety_assets",
        "species_scope <@ ARRAY['dog', 'cat']::text[]",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_sex_scope_domain",
        "clinical_safety_assets",
        "sex_scope <@ ARRAY['male', 'female']::text[]",
    )
    op.create_check_constraint(
        "ck_clinical_safety_assets_age_scope_domain",
        "clinical_safety_assets",
        "age_scope <@ ARRAY['juvenile', 'adult', 'senior']::text[]",
    )


def downgrade() -> None:
    """移除临床安全资产范围值域约束。

    :return: 无返回值。
    """
    op.drop_constraint(
        "ck_clinical_safety_assets_age_scope_domain",
        "clinical_safety_assets",
        type_="check",
    )
    op.drop_constraint(
        "ck_clinical_safety_assets_sex_scope_domain",
        "clinical_safety_assets",
        type_="check",
    )
    op.drop_constraint(
        "ck_clinical_safety_assets_species_scope_domain",
        "clinical_safety_assets",
        type_="check",
    )
