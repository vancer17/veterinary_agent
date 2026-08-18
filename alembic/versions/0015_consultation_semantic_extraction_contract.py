"""
=============================================================================
文件：alembic/versions/0015_consultation_semantic_extraction_contract.py
作用：收束问诊语义抽取迁移后的数据库可信源，移除旧槽位抽取规则字段并补充目录注释。
范围：仅调整 consultation_domains 与 consultation_slots 的运行时目录边界；
      问诊语义抽取改由 LiteLLM response_format 与 Pydantic 契约承载。
说明：本迁移不新增长期事实治理表，不修改宠物权威资料表，不引入关键词或正则回退路径。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015_consultation_semantic_extraction_contract"
down_revision = "0014_task_routing_domain_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行问诊语义抽取契约收束的正向迁移。

    :return: 无返回值。
    """
    op.drop_column("consultation_slots", "extraction_rules")
    _comment("COMMENT ON TABLE consultation_domains IS '问诊领域目录表，用于回答充分性策略读取领域所需关注槽位，不保存关键词分类规则。'")
    _comment("COMMENT ON COLUMN consultation_domains.domain IS '问诊领域稳定技术标识。'")
    _comment("COMMENT ON COLUMN consultation_domains.required_slots IS '当前领域建议关注的问诊事实槽位集合；不承载自然语言抽取规则。'")
    _comment("COMMENT ON COLUMN consultation_domains.enabled IS '问诊领域是否允许运行时使用。'")
    _comment("COMMENT ON COLUMN consultation_domains.priority IS '问诊领域排序优先级，数值越小越优先。'")
    _comment("COMMENT ON COLUMN consultation_domains.version IS '问诊领域目录版本。'")
    _comment("COMMENT ON COLUMN consultation_domains.updated_at IS '问诊领域目录最近更新时间。'")

    _comment("COMMENT ON TABLE consultation_slots IS '问诊槽位展示和追问文案目录表；不保存关键词、正则或文本抽取规则。'")
    _comment("COMMENT ON COLUMN consultation_slots.slot_name IS '问诊槽位稳定技术标识。'")
    _comment("COMMENT ON COLUMN consultation_slots.question IS '该槽位默认追问文案。'")
    _comment("COMMENT ON COLUMN consultation_slots.label IS '该槽位面向用户展示的中文标签。'")
    _comment("COMMENT ON COLUMN consultation_slots.priority IS '问诊槽位排序优先级，数值越小越优先。'")
    _comment("COMMENT ON COLUMN consultation_slots.enabled IS '问诊槽位是否允许运行时使用。'")
    _comment("COMMENT ON COLUMN consultation_slots.version IS '问诊槽位目录版本。'")
    _comment("COMMENT ON COLUMN consultation_slots.updated_at IS '问诊槽位目录最近更新时间。'")


def downgrade() -> None:
    """执行问诊语义抽取契约收束的回滚迁移。

    :return: 无返回值。
    """
    op.add_column(
        "consultation_slots",
        sa.Column(
            "extraction_rules",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="已废弃的旧版槽位抽取规则字段，仅为回滚兼容保留，不应作为运行时语义抽取可信源。",
        ),
    )
    _comment("COMMENT ON TABLE consultation_slots IS '问诊槽位目录表；回滚后包含已废弃 extraction_rules 字段。'")


def _comment(statement: str) -> None:
    """执行 PostgreSQL 注释语句。

    :param statement: COMMENT ON SQL 语句。
    :return: 无返回值。
    """
    op.execute(sa.text(statement))
