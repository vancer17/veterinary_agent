"""
=============================================================================
文件：alembic/versions/0018_rag_retrieval_misses.py
作用：新增 RAG 无命中知识缺口治理记录表。
范围：建立 rag_retrieval_misses 表及请求范围、任务上下文、结构化 query、
      召回参数、失败原因、聚合键和人工治理状态字段。
说明：本迁移只提供无命中可治理留痕，不创建运行时回退、关键词规则或默认
      回答模板。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0018_rag_retrieval_misses"
down_revision = "0017_persistent_background_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 RAG 无命中治理记录表的正向迁移。

    :return: 无返回值。
    """
    op.create_table(
        "rag_retrieval_misses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="RAG 无命中治理记录内部主键。"),
        sa.Column("miss_id", sa.Text(), nullable=False, comment="RAG 无命中治理记录稳定标识。"),
        sa.Column("request_id", sa.Text(), nullable=False, comment="触发无命中的 Agent 请求标识。"),
        sa.Column("trace_id", sa.Text(), nullable=False, comment="触发无命中的链路追踪标识。"),
        sa.Column("user_id", sa.Text(), nullable=False, comment="当前可信用户标识。"),
        sa.Column("pet_id", sa.Text(), nullable=False, comment="当前可信宠物标识。"),
        sa.Column("session_id", sa.Text(), nullable=False, comment="当前可信会话标识。"),
        sa.Column("rag_scope", sa.Text(), nullable=False, comment="RAG 无命中所属数据链范围，当前仅允许 answer_rag。"),
        sa.Column("task_id", sa.Text(), nullable=False, comment="触发无命中的当前任务展示标识。"),
        sa.Column("task_key", sa.Text(), nullable=False, comment="触发无命中的当前任务状态键。"),
        sa.Column("task_domain", sa.Text(), nullable=False, comment="触发无命中的当前任务域。"),
        sa.Column("task_title", sa.Text(), nullable=False, comment="触发无命中的当前任务标题。"),
        sa.Column("user_text_excerpt", sa.Text(), nullable=False, comment="经裁剪后的用户任务文本片段，用于人工治理排障。"),
        sa.Column("user_text_digest", sa.Text(), nullable=False, comment="用户任务文本 SHA-256 摘要，用于去重和隐私友好的聚合。"),
        sa.Column(
            "structured_query",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="回答 RAG 实际使用的结构化检索 query，不作为运行时规则来源。",
        ),
        sa.Column(
            "consultation_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="触发无命中时的问诊状态快照。",
        ),
        sa.Column(
            "answerability",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="触发无命中时的回答充分性裁决快照。",
        ),
        sa.Column(
            "semantic_extraction",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="触发无命中时的问诊语义抽取快照。",
        ),
        sa.Column(
            "retrieval_parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="回答 RAG 本轮召回参数摘要，例如 top_k、min_score、chunk 类型和领域过滤。",
        ),
        sa.Column("failure_reason", sa.Text(), nullable=False, comment="无命中失败原因，例如 no_approved_vector_hits。"),
        sa.Column("error_type", sa.Text(), nullable=False, comment="触发治理记录的原始异常类型。"),
        sa.Column("error_message", sa.Text(), nullable=False, comment="触发治理记录的原始异常消息。"),
        sa.Column(
            "error_details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="原始异常结构化细节，用于排查过滤条件和依赖状态。",
        ),
        sa.Column("dedupe_key", sa.Text(), nullable=False, comment="用于后台聚合同类知识缺口的稳定哈希键。"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="open",
            comment="知识缺口治理状态，仅用于后台治理，不参与 Agent 运行时裁决。",
        ),
        sa.Column("review_notes", sa.Text(), comment="治理人员处理备注。"),
        sa.Column("linked_ingestion_batch", sa.Text(), comment="关联的知识导入批次标识。"),
        sa.Column(
            "linked_chunk_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
            comment="关联的正式知识 chunk 内部主键集合。",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="RAG 无命中治理记录附加审计信息。",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="治理记录创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="治理记录最近更新时间。"),
        sa.CheckConstraint("rag_scope IN ('answer_rag')", name="ck_rag_retrieval_misses_scope"),
        sa.CheckConstraint(
            "status IN ('open', 'triaged', 'asset_drafted', 'published', 'dismissed')",
            name="ck_rag_retrieval_misses_status",
        ),
        sa.UniqueConstraint("miss_id", name="uq_rag_retrieval_misses_miss_id"),
        comment="RAG 无命中知识缺口治理记录表，用于记录 Fail Fast 后的可治理资产缺口，不参与运行时回答回退。",
    )
    op.create_index("idx_rag_retrieval_misses_trace_id", "rag_retrieval_misses", ["trace_id"])
    op.create_index("idx_rag_retrieval_misses_scope_status", "rag_retrieval_misses", ["rag_scope", "status"])
    op.create_index("idx_rag_retrieval_misses_dedupe_key", "rag_retrieval_misses", ["dedupe_key"])
    op.create_index("idx_rag_retrieval_misses_domain_created_at", "rag_retrieval_misses", ["task_domain", "created_at"])
    op.create_index("idx_rag_retrieval_misses_identity", "rag_retrieval_misses", ["user_id", "pet_id", "session_id"])


def downgrade() -> None:
    """执行 RAG 无命中治理记录表的回滚迁移。

    :return: 无返回值。
    """
    op.drop_index("idx_rag_retrieval_misses_identity", table_name="rag_retrieval_misses")
    op.drop_index("idx_rag_retrieval_misses_domain_created_at", table_name="rag_retrieval_misses")
    op.drop_index("idx_rag_retrieval_misses_dedupe_key", table_name="rag_retrieval_misses")
    op.drop_index("idx_rag_retrieval_misses_scope_status", table_name="rag_retrieval_misses")
    op.drop_index("idx_rag_retrieval_misses_trace_id", table_name="rag_retrieval_misses")
    op.drop_table("rag_retrieval_misses")
