"""
文件：alembic/versions/0016_output_safety_candidate_definitions.py
作用：新增输出安全结构化候选定义表。
范围：建立 output_safety_candidate_definitions 表，并写入 Guardrails 输出复核候选的默认定义。
说明：本迁移服务于“候选发现 + 策略裁决”的输出安全链路，不保存正则替换规则、关键词链或回退响应模板。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0016_output_safety_candidate_definitions"
down_revision = "0015_consultation_semantic_extraction_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行 Alembic 正向迁移，创建输出安全候选定义表。

    :return: 无返回值。
    """
    op.create_table(
        "output_safety_candidate_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="候选定义内部主键。"),
        sa.Column("code", sa.Text(), nullable=False, comment="候选编码，用于 OPA 策略、审计和安全信号关联。"),
        sa.Column("category", sa.Text(), nullable=False, comment="候选类别，例如系统提示泄露、PII、密钥、剂量、药物、主题边界或格式候选。"),
        sa.Column(
            "default_severity",
            sa.Text(),
            nullable=False,
            server_default="caution",
            comment="候选默认严重级别；最终动作和信号级别由 OPA 裁决覆盖。",
        ),
        sa.Column("message", sa.Text(), nullable=False, comment="候选默认说明，用于审计和默认策略原因。"),
        sa.Column("detector", sa.Text(), nullable=False, comment="候选来源检测器标识。"),
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
            name="ck_output_safety_candidate_definitions_severity",
        ),
        sa.UniqueConstraint("code", name="uq_output_safety_candidate_definitions_code"),
        comment="输出安全候选定义表，用于描述 Guardrails 等结构化检测器输出的策略语义，不保存文本替换规则或回退响应模板。",
    )
    op.create_index(
        "idx_output_safety_candidate_definitions_enabled_priority",
        "output_safety_candidate_definitions",
        ["enabled", "priority"],
    )
    _seed_default_definitions()


def downgrade() -> None:
    """执行 Alembic 回滚迁移，删除输出安全候选定义表。

    :return: 无返回值。
    """
    op.drop_index(
        "idx_output_safety_candidate_definitions_enabled_priority",
        table_name="output_safety_candidate_definitions",
    )
    op.drop_table("output_safety_candidate_definitions")


def _seed_default_definitions() -> None:
    """写入输出安全结构化候选定义。

    :return: 无返回值。
    """
    table = sa.table(
        "output_safety_candidate_definitions",
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
                "code": "OUTPUT_SYSTEM_PROMPT_LEAKAGE",
                "category": "prompt_leakage",
                "default_severity": "blocked",
                "message": "输出可能泄露系统提示或内部指令。",
                "detector": "guardrails_detect_system_prompt_leakage",
                "priority": 10,
                "metadata": {"candidate_family": "prompt_security"},
            },
            {
                "code": "OUTPUT_PII_DETECTED",
                "category": "pii",
                "default_severity": "blocked",
                "message": "输出可能包含个人身份信息。",
                "detector": "guardrails_detect_pii",
                "priority": 20,
                "metadata": {"candidate_family": "privacy"},
            },
            {
                "code": "OUTPUT_SECRET_DETECTED",
                "category": "secret",
                "default_severity": "blocked",
                "message": "输出可能包含密钥、令牌或密码。",
                "detector": "guardrails_secrets_present",
                "priority": 30,
                "metadata": {"candidate_family": "secret_leakage"},
            },
            {
                "code": "OUTPUT_DOSAGE_EXPRESSION",
                "category": "dosage",
                "default_severity": "caution",
                "message": "输出出现具体剂量表达，需要策略层裁决是否允许交付。",
                "detector": "guardrails_regex_match_no_dosage_expression",
                "priority": 40,
                "metadata": {"candidate_family": "clinical_output_boundary"},
            },
            {
                "code": "OUTPUT_MEDICATION_MENTIONED",
                "category": "medication",
                "default_severity": "caution",
                "message": "输出涉及药物名称，需要策略层裁决用药边界。",
                "detector": "guardrails_mentions_drugs",
                "priority": 50,
                "metadata": {"candidate_family": "clinical_output_boundary"},
            },
            {
                "code": "OUTPUT_TOPIC_BOUNDARY",
                "category": "topic_boundary",
                "default_severity": "caution",
                "message": "输出主题可能偏离宠物健康咨询范围。",
                "detector": "guardrails_restrict_to_topic",
                "priority": 60,
                "metadata": {"candidate_family": "scope_boundary"},
            },
            {
                "code": "OUTPUT_LENGTH_EXCEEDED",
                "category": "format",
                "default_severity": "caution",
                "message": "输出长度超过当前服务允许的最大字符数。",
                "detector": "guardrails_valid_length",
                "priority": 70,
                "metadata": {"candidate_family": "format_budget"},
            },
        ],
    )
