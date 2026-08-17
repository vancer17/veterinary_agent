"""
=============================================================================
文件：alembic/versions/0019_followup_rag_miss_governance.py
作用：扩展 RAG 无命中治理记录表的可治理范围。
范围：将 rag_retrieval_misses 的 rag_scope 从单一 answer_rag 扩展为
      answer_rag 与 followup_rag，以便追问相关 RAG 无命中可以统一留痕。
说明：本迁移只改变治理范围约束，不引入任何运行时回退、默认问题模板或
      关键词状态机。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0019_followup_rag_miss_governance"
down_revision = "0018_rag_retrieval_misses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 RAG 无命中治理范围扩展的正向迁移。

    :return: 无返回值。
    """
    op.drop_constraint("ck_rag_retrieval_misses_scope", "rag_retrieval_misses", type_="check")
    op.create_check_constraint(
        "ck_rag_retrieval_misses_scope",
        "rag_retrieval_misses",
        "rag_scope IN ('answer_rag', 'followup_rag')",
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN rag_retrieval_misses.rag_scope IS "
            "'RAG 无命中所属数据链范围，当前允许 answer_rag、followup_rag。'"
        )
    )
    op.execute(
        sa.text(
            "COMMENT ON TABLE rag_retrieval_misses IS "
            "'RAG 无命中知识缺口治理记录表，用于记录 Fail Fast 后的可治理资产缺口，不参与运行时回答回退。'"
        )
    )


def downgrade() -> None:
    """执行 RAG 无命中治理范围扩展的回滚迁移。

    :return: 无返回值。
    """
    op.drop_constraint("ck_rag_retrieval_misses_scope", "rag_retrieval_misses", type_="check")
    op.create_check_constraint(
        "ck_rag_retrieval_misses_scope",
        "rag_retrieval_misses",
        "rag_scope IN ('answer_rag')",
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN rag_retrieval_misses.rag_scope IS "
            "'RAG 无命中所属数据链范围，当前仅允许 answer_rag。'"
        )
    )
    op.execute(
        sa.text(
            "COMMENT ON TABLE rag_retrieval_misses IS "
            "'RAG 无命中知识缺口治理记录表，用于记录 Fail Fast 后的可治理资产缺口，不参与运行时回答回退。'"
        )
    )
