"""
=============================================================================
文件：src/vet_agent/task_routing/repository.py
作用：实现任务路由任务域目录的数据仓储。
范围：PostgreSQL 实现负责读取 task_routing_domains 表；静态实现仅供测试或显式
      嵌入场景注入，不作为生产回退路径。
说明：仅本文件允许直接访问任务路由相关 SQLAlchemy 表模型；业务层需通过
      TaskRoutingDomainRepository 协议读取任务域目录。
=============================================================================
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent.db import TaskRoutingDomainModel, make_session_factory

from .errors import TaskRoutingDependencyError
from .models import TaskRoutingDomain, TaskRoutingDomainCatalog
from .ports import TaskRoutingDomainRepository


class PostgresTaskRoutingDomainRepository(TaskRoutingDomainRepository):
    """基于 PostgreSQL 的任务路由任务域目录仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 任务域目录仓储。

        :param database_url: PostgreSQL 数据库连接地址。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def task_routing_domains(self) -> TaskRoutingDomainCatalog:
        """读取当前启用的任务路由任务域目录。

        :return: 返回任务域目录。
        :raises TaskRoutingDependencyError: 数据库不可访问或无启用任务域时抛出。
        """
        try:
            with self.session_factory() as session:
                rows = session.scalars(
                    select(TaskRoutingDomainModel)
                    .where(TaskRoutingDomainModel.enabled.is_(True))
                    .order_by(TaskRoutingDomainModel.priority, TaskRoutingDomainModel.domain)
                ).all()
        except SQLAlchemyError as exc:
            raise TaskRoutingDependencyError(
                "task routing domain repository is unavailable",
                details={"error_type": type(exc).__name__},
            ) from exc
        catalog = TaskRoutingDomainCatalog(tuple(_domain_from_row(row) for row in rows))
        if not catalog.domains:
            raise TaskRoutingDependencyError(
                "task routing domain catalog is empty",
                details={"reason": "empty_task_routing_domain_catalog"},
            )
        return catalog

    def is_ready(self) -> bool:
        """检查任务域目录仓储是否可访问且存在启用域。

        :return: 存在启用任务域时返回 True。
        """
        try:
            with self.session_factory() as session:
                count = session.scalar(
                    select(func.count())
                    .select_from(TaskRoutingDomainModel)
                    .where(TaskRoutingDomainModel.enabled.is_(True))
                )
            return int(count or 0) > 0
        except SQLAlchemyError:
            return False


class StaticTaskRoutingDomainRepository(TaskRoutingDomainRepository):
    """显式注入的静态任务域目录仓储。

    说明：该实现仅用于测试或特殊嵌入场景，不由生产容器自动选择。

    :return: 无返回值。
    """

    def __init__(self, domains: tuple[TaskRoutingDomain, ...]) -> None:
        """初始化静态任务域目录仓储。

        :param domains: 测试或嵌入场景显式提供的任务域集合。
        :return: 无返回值。
        """
        self.catalog = TaskRoutingDomainCatalog(domains)

    @classmethod
    def default(cls) -> "StaticTaskRoutingDomainRepository":
        """构造测试使用的默认任务域目录。

        :return: 返回包含当前业务基础任务域的静态仓储。
        """
        return cls(default_task_routing_domains())

    def task_routing_domains(self) -> TaskRoutingDomainCatalog:
        """读取静态任务域目录。

        :return: 返回静态任务域目录。
        :raises TaskRoutingDependencyError: 目录为空时抛出。
        """
        if not self.catalog.domains:
            raise TaskRoutingDependencyError(
                "task routing domain catalog is empty",
                details={"reason": "empty_static_task_routing_domain_catalog"},
            )
        return self.catalog

    def is_ready(self) -> bool:
        """检查静态任务域目录是否可用。

        :return: 存在至少一个任务域时返回 True。
        """
        return bool(self.catalog.domains)


def default_task_routing_domains() -> tuple[TaskRoutingDomain, ...]:
    """返回当前任务路由基础任务域集合。

    :return: 返回用于测试、初始化迁移和人工排障的任务域元组。
    """
    return (
        TaskRoutingDomain(
            domain="gastrointestinal",
            title="消化道问题",
            description="呕吐、腹泻、排便、食欲和胃肠不适等消化相关咨询。",
            priority=10,
        ),
        TaskRoutingDomain(
            domain="respiratory",
            title="呼吸问题",
            description="咳嗽、喘、呼吸费力、喷嚏、鼻部表现等呼吸相关咨询。",
            priority=20,
        ),
        TaskRoutingDomain(
            domain="mobility",
            title="疼痛/活动问题",
            description="跛行、站立、行走、触碰疼痛和活动异常等运动相关咨询。",
            priority=30,
        ),
        TaskRoutingDomain(
            domain="behavior",
            title="行为问题",
            description="叫声、焦虑、护食、拆家、互动变化和环境行为相关咨询。",
            priority=40,
        ),
        TaskRoutingDomain(
            domain="feeding",
            title="喂养问题",
            description="主粮、换粮、零食、喂养方式和饮食调整相关咨询。",
            priority=50,
        ),
        TaskRoutingDomain(
            domain="general",
            title="一般健康问题",
            description="无法明确归入专门任务域的一般健康咨询或单一综合咨询。",
            priority=100,
        ),
    )


def _domain_from_row(row: Any) -> TaskRoutingDomain:
    """将 SQLAlchemy 行对象转换为任务域领域模型。

    :param row: TaskRoutingDomainModel 数据行。
    :return: 返回任务域领域模型。
    """
    return TaskRoutingDomain(
        domain=str(row.domain),
        title=str(row.title),
        description=str(row.description),
        priority=int(row.priority or 100),
        version=str(row.version or "v1"),
        metadata=dict(row.metadata_json or {}),
    )
