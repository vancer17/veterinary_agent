"""
文件：alembic/versions/0007_clinical_safety_phrases.py
作用：为临床安全资产增加组合症状与原子短语召回字段。
说明：该增量迁移兼容已经执行 0006 的环境；revision 长度需小于 Alembic 默认 version_num 长度。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_clinical_safety_phrases"
down_revision = "0006_clinical_safety_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加临床安全资产的召回短语数组列。

    :return: 无返回值。
    """
    op.add_column(
        "clinical_safety_assets",
        sa.Column("recognition_phrases", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    """删除临床安全资产的召回短语数组列。

    :return: 无返回值。
    """
    op.drop_column("clinical_safety_assets", "recognition_phrases")
