##################################################################################################
# 文件: migrations/script.py.mako
# 作用: 定义 Alembic 新迁移版本文件模板，确保生成脚本带有项目统一文件头和类型提示。
# 边界: 仅作为迁移脚本生成模板，不承载任何运行时代码。
##################################################################################################
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """应用本次数据库结构升级。

    :return: None。
    """

    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚本次数据库结构升级。

    :return: None。
    """

    ${downgrades if downgrades else "pass"}
