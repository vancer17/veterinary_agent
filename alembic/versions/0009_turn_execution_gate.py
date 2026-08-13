"""
文件：alembic/versions/0009_turn_execution_gate.py
作用：为 Agent 单回合执行门禁补充幂等记录请求哈希、失败类型、状态约束和数据库说明。
范围：仅修改 idempotency_records 表，以支持 turn lock 与幂等数据链迁移后的 PostgreSQL 仓储。
说明：本迁移不实现业务状态机，仅提供基础设施级 claim、冲突检测、响应重放与失败审计所需字段。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_turn_execution_gate"
down_revision = "0008_scope_table_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，补充 turn execution 幂等表结构。

    :return: 无返回值。
    """
    op.add_column(
        "idempotency_records",
        sa.Column(
            "request_hash",
            sa.Text(),
            server_default="",
            nullable=False,
            comment="去除 request_id、trace_id 与 idempotency_key 后的请求语义哈希，用于检测同 key 不同请求冲突。",
        ),
    )
    op.add_column(
        "idempotency_records",
        sa.Column(
            "error_type",
            sa.Text(),
            nullable=True,
            comment="最近一次执行失败的异常类型，用于排障和审计。",
        ),
    )
    op.create_check_constraint(
        "ck_idempotency_records_status",
        "idempotency_records",
        "status IN ('processing', 'completed', 'failed')",
    )
    _comment_idempotency_records()


def downgrade() -> None:
    """执行 Alembic 回滚迁移，移除 turn execution 新增结构。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE idempotency_records IS NULL")
    for column in (
        "id",
        "user_id",
        "pet_id",
        "session_id",
        "idempotency_key",
        "request_id",
        "trace_id",
        "request_hash",
        "response_id",
        "status",
        "response_snapshot",
        "error_type",
        "created_at",
        "updated_at",
    ):
        _comment(f"COMMENT ON COLUMN idempotency_records.{column} IS NULL")
    op.drop_constraint("ck_idempotency_records_status", "idempotency_records", type_="check")
    op.drop_column("idempotency_records", "error_type")
    op.drop_column("idempotency_records", "request_hash")


def _comment_idempotency_records() -> None:
    """补充幂等记录表与字段说明。

    :return: 无返回值。
    """
    _comment("COMMENT ON TABLE idempotency_records IS 'Agent 单回合幂等记录表，用于 turn execution 门禁的 claim、响应重放与失败追踪。'")
    _comment("COMMENT ON COLUMN idempotency_records.id IS '幂等记录内部主键。'")
    _comment("COMMENT ON COLUMN idempotency_records.user_id IS '本轮可信身份范围中的用户标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.pet_id IS '本轮可信身份范围中的宠物标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.session_id IS '本轮可信身份范围中的会话标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.idempotency_key IS '调用方提交的幂等键，在同一用户、宠物、会话范围内唯一。'")
    _comment("COMMENT ON COLUMN idempotency_records.request_id IS '最近一次声明或完成该幂等记录的请求标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.trace_id IS '最近一次声明或完成该幂等记录的链路追踪标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.request_hash IS '去除 request_id、trace_id 与 idempotency_key 后的请求语义哈希，用于检测同 key 不同请求冲突。'")
    _comment("COMMENT ON COLUMN idempotency_records.response_id IS '首个成功响应的 Agent turn 标识。'")
    _comment("COMMENT ON COLUMN idempotency_records.status IS '幂等记录状态，仅允许 processing、completed、failed。'")
    _comment("COMMENT ON COLUMN idempotency_records.response_snapshot IS '首个成功响应的 JSON 快照，用于后续同语义请求重放。'")
    _comment("COMMENT ON COLUMN idempotency_records.error_type IS '最近一次执行失败的异常类型，用于排障和审计。'")
    _comment("COMMENT ON COLUMN idempotency_records.created_at IS '幂等记录创建时间。'")
    _comment("COMMENT ON COLUMN idempotency_records.updated_at IS '幂等记录最近更新时间。'")


def _comment(statement: str) -> None:
    """执行数据库注释 SQL。

    :param statement: PostgreSQL COMMENT 语句。
    :return: 无返回值。
    """
    op.execute(statement)
