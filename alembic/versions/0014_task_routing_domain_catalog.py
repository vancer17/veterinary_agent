"""
=============================================================================
文件：alembic/versions/0014_task_routing_domain_catalog.py
作用：建立结构化任务路由使用的任务域目录表，并移除旧问诊域关键词字段。
范围：任务路由只读取 task_routing_domains；旧 consultation_domains 不再承担任务分类。
说明：任务域目录是运行时约束配置，不是用户输入样例或临床知识资产；迁移同时写入
      最小可运行目录，后续可由正式配置发布流程更新。
=============================================================================
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0014_task_routing_domain_catalog"
down_revision = "0013_memory_read_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行任务路由任务域目录正向迁移。

    :return: 无返回值。
    """
    op.drop_column("consultation_domains", "classifier_keywords")
    op.create_table(
        "task_routing_domains",
        sa.Column("domain", sa.Text(), primary_key=True, comment="任务域稳定技术标识。"),
        sa.Column("title", sa.Text(), nullable=False, comment="任务域面向用户展示的标题。"),
        sa.Column("description", sa.Text(), nullable=False, comment="任务域职责说明，仅用于路由上下文和运维理解。"),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="100",
            comment="任务域默认排序优先级，数值越小越优先。",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="任务域是否允许进入任务路由目录。",
        ),
        sa.Column(
            "version",
            sa.Text(),
            nullable=False,
            server_default="v1",
            comment="任务域目录配置版本。",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="任务域附加审计元数据，不承载任务动作规则。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="任务域记录创建时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="任务域记录最近更新时间。",
        ),
        sa.UniqueConstraint("domain", name="uq_task_routing_domains_domain"),
        comment="任务路由任务域目录表，用于约束结构化任务拆分结果，不保存关键词规则或临床动作规则。",
    )
    op.create_index(
        "idx_task_routing_domains_enabled_priority",
        "task_routing_domains",
        ["enabled", "priority", "domain"],
    )
    _seed_task_routing_domains()


def downgrade() -> None:
    """执行任务路由任务域目录回滚迁移。

    :return: 无返回值。
    """
    op.drop_index(
        "idx_task_routing_domains_enabled_priority",
        table_name="task_routing_domains",
    )
    op.drop_table("task_routing_domains")
    op.add_column(
        "consultation_domains",
        sa.Column(
            "classifier_keywords",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="已废弃的旧版关键词分类字段，仅为回滚兼容保留。",
        ),
    )


def _seed_task_routing_domains() -> None:
    """写入初始任务域目录。

    :return: 无返回值。
    """
    table = sa.table(
        "task_routing_domains",
        sa.column("domain", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("priority", sa.Integer()),
        sa.column("version", sa.Text()),
        sa.column("metadata", postgresql.JSONB()),
    )
    op.bulk_insert(
        table,
        [
            {
                "domain": "gastrointestinal",
                "title": "消化道问题",
                "description": "呕吐、腹泻、排便、食欲和胃肠不适等消化相关咨询。",
                "priority": 10,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
            {
                "domain": "respiratory",
                "title": "呼吸问题",
                "description": "咳嗽、喘、呼吸费力、喷嚏、鼻部表现等呼吸相关咨询。",
                "priority": 20,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
            {
                "domain": "mobility",
                "title": "疼痛/活动问题",
                "description": "跛行、站立、行走、触碰疼痛和活动异常等运动相关咨询。",
                "priority": 30,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
            {
                "domain": "behavior",
                "title": "行为问题",
                "description": "叫声、焦虑、护食、拆家、互动变化和环境行为相关咨询。",
                "priority": 40,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
            {
                "domain": "feeding",
                "title": "喂养问题",
                "description": "主粮、换粮、零食、喂养方式和饮食调整相关咨询。",
                "priority": 50,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
            {
                "domain": "general",
                "title": "一般健康问题",
                "description": "无法明确归入专门任务域的一般健康咨询或单一综合咨询。",
                "priority": 100,
                "version": "v1",
                "metadata": {"source": "task_routing_catalog_v1"},
            },
        ],
    )
