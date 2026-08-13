"""
文件：alembic/versions/0010_input_safety_candidates.py
作用：迁移基础输入安全数据结构，移除旧输入规则表并新增结构化候选定义表。
范围：删除旧 safety_rules 关键词规则与 response_template 数据源，建立 input_safety_candidate_definitions 表。
说明：本迁移服务于 Guardrails、结构化字段检查与 OPA 策略裁决的数据链，候选定义不承担文本扫描。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0010_input_safety_candidates"
down_revision = "0009_turn_execution_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，替换旧基础输入安全规则表。

    :return: 无返回值。
    """
    op.drop_index("idx_safety_rules_type_enabled", table_name="safety_rules", if_exists=True)
    op.drop_table("safety_rules", if_exists=True)
    op.create_table(
        "input_safety_candidate_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="候选定义内部主键。"),
        sa.Column("code", sa.Text(), nullable=False, comment="候选编码，用于 OPA 策略、审计和安全信号关联。"),
        sa.Column("category", sa.Text(), nullable=False, comment="候选类别，例如完整性、提示注入、未开放能力或业务范围候选。"),
        sa.Column(
            "default_severity",
            sa.Text(),
            nullable=False,
            server_default="caution",
            comment="候选默认严重级别；最终动作和信号级别由 OPA 裁决覆盖。",
        ),
        sa.Column("message", sa.Text(), nullable=False, comment="候选默认说明，用于审计和默认安全响应。"),
        sa.Column("detector", sa.Text(), nullable=False, comment="候选来源检测器或结构化字段检查器标识。"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100", comment="候选定义排序优先级，数值越小越优先。"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="候选定义是否允许运行时使用。"),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1", comment="候选定义版本。"),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="候选定义附加审计信息。",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="候选定义创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="候选定义最近更新时间。"),
        sa.CheckConstraint(
            "default_severity IN ('info', 'caution', 'urgent', 'blocked')",
            name="ck_input_safety_candidate_definitions_severity",
        ),
        sa.UniqueConstraint("code", name="uq_input_safety_candidate_definitions_code"),
        comment="基础输入安全候选定义表，用于描述结构化检测器输出的策略语义，不保存文本关键词规则。",
    )
    op.create_index(
        "idx_input_safety_candidate_definitions_enabled_priority",
        "input_safety_candidate_definitions",
        ["enabled", "priority"],
    )
    _seed_default_definitions()


def downgrade() -> None:
    """执行 Alembic 回滚迁移，恢复旧基础输入安全规则表。

    :return: 无返回值。
    """
    op.drop_index(
        "idx_input_safety_candidate_definitions_enabled_priority",
        table_name="input_safety_candidate_definitions",
    )
    op.drop_table("input_safety_candidate_definitions")
    op.create_table(
        "safety_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("rule_type", sa.Text(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="caution"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("response_template", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_safety_rules_type_enabled", "safety_rules", ["rule_type", "enabled"])


def _seed_default_definitions() -> None:
    """写入基础输入安全结构化候选定义。

    :return: 无返回值。
    """
    table = sa.table(
        "input_safety_candidate_definitions",
        sa.column("code", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("default_severity", sa.Text()),
        sa.column("message", sa.Text()),
        sa.column("detector", sa.Text()),
        sa.column("priority", sa.Integer()),
        sa.column("metadata", postgresql.JSONB()),
    )
    op.bulk_insert(
        table,
        [
            {
                "code": "EMPTY_INPUT",
                "category": "integrity",
                "default_severity": "blocked",
                "message": "输入文本与附件均为空。",
                "detector": "structured_request",
                "priority": 10,
                "metadata": {"candidate_family": "request_integrity"},
            },
            {
                "code": "INPUT_TOO_LONG",
                "category": "integrity",
                "default_severity": "blocked",
                "message": "输入文本超过当前服务允许的最大长度。",
                "detector": "structured_request",
                "priority": 20,
                "metadata": {"candidate_family": "request_integrity"},
            },
            {
                "code": "TOO_MANY_ATTACHMENTS",
                "category": "integrity",
                "default_severity": "blocked",
                "message": "附件数量超过当前服务允许的最大数量。",
                "detector": "structured_request",
                "priority": 30,
                "metadata": {"candidate_family": "request_integrity"},
            },
            {
                "code": "ATTACHMENT_MIME_TYPE_MISSING",
                "category": "integrity",
                "default_severity": "blocked",
                "message": "附件缺少 MIME 类型。",
                "detector": "structured_request",
                "priority": 40,
                "metadata": {"candidate_family": "request_integrity"},
            },
            {
                "code": "ATTACHMENT_PURPOSE_UNKNOWN",
                "category": "integrity",
                "default_severity": "caution",
                "message": "附件用途未明确声明。",
                "detector": "structured_request",
                "priority": 50,
                "metadata": {"candidate_family": "request_integrity"},
            },
            {
                "code": "PROMPT_INJECTION_ATTEMPT",
                "category": "prompt_attack",
                "default_severity": "blocked",
                "message": "输入存在越权或提示注入风险。",
                "detector": "guardrails_prompt_injection_detector",
                "priority": 100,
                "metadata": {"candidate_family": "prompt_security"},
            },
            {
                "code": "RADIOLOGY_GATE",
                "category": "unopened_capability",
                "default_severity": "blocked",
                "message": "当前服务未开放影像判读能力，不能根据 X 光、B 超、CT 或 MRI 附件给出影像诊断结论。",
                "detector": "structured_request",
                "priority": 60,
                "metadata": {"candidate_family": "capability_boundary"},
            },
        ],
    )
