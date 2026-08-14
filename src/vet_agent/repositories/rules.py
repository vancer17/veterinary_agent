"""
=============================================================================
文件：src/vet_agent/repositories/rules.py
作用：提供问诊领域、槽位展示与追问文案的数据访问能力。
范围：本仓储只暴露问诊状态与追问规划所需的结构化目录信息；
      不保存、不读取、不编译自然语言关键词或正则抽取规则。
说明：问诊语义抽取已迁移至 LiteLLM response_format 与 Pydantic 契约，
      运行时不得通过本仓储恢复旧版关键词/正则事实抽取路径。
=============================================================================
"""


from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from vet_agent.db import (
    ConsultationDomainModel,
    ConsultationSlotModel,
    make_session_factory,
)


@dataclass(frozen=True)
class ConsultationDomainRule:
    """表示问诊领域目录中的领域配置。

    :param domain: 问诊领域稳定标识。
    :param required_slots: 当前领域建议关注的问诊事实槽位。
    :param priority: 领域排序优先级。
    :return: 无返回值。
    """

    domain: str
    required_slots: list[str]
    priority: int = 100


@dataclass(frozen=True)
class ConsultationSlotRule:
    """表示问诊槽位展示与追问文案配置。

    :param slot_name: 问诊槽位稳定标识。
    :param question: 面向用户的默认追问文案。
    :param label: 面向用户展示的槽位标签。
    :param priority: 槽位排序优先级。
    :return: 无返回值。
    """

    slot_name: str
    question: str
    label: str
    priority: int = 100


@dataclass(frozen=True)
class ConsultationRuleSet:
    """表示问诊状态和追问规划可消费的规则目录快照。

    :param domains: 问诊领域目录。
    :param slots: 问诊槽位展示和追问文案目录。
    :param safety_net_text: 默认安全兜底文案。
    :return: 无返回值。
    """

    domains: dict[str, ConsultationDomainRule]
    slots: dict[str, ConsultationSlotRule]
    safety_net_text: str


class RuleRepository(Protocol):
    """定义问诊目录读取仓储协议。

    :return: 无返回值。
    """

    def consultation_rules(self) -> ConsultationRuleSet:
        """读取问诊领域与追问文案目录。

        :return: 返回问诊规则目录快照。
        """
        ...

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        ...


class FileRuleRepository:
    """基于 seed JSON 文件的问诊目录仓储。

    :return: 无返回值。
    """

    def __init__(self, seed_dir: Path) -> None:
        """初始化当前对象。

        :param seed_dir: 参数 seed_dir。
        :return: 无返回值。
        """
        self.seed_dir = seed_dir

    def consultation_rules(self) -> ConsultationRuleSet:
        """从开发或测试 seed 文件读取问诊目录。

        :return: 返回问诊规则目录快照。
        """
        raw = json.loads((self.seed_dir / "consultation_rules.json").read_text(encoding="utf-8"))
        domains = {
            item["domain"]: ConsultationDomainRule(
                domain=item["domain"],
                required_slots=list(item.get("required_slots", [])),
                priority=int(item.get("priority", 100)),
            )
            for item in raw.get("domains", [])
        }
        slots = {
            item["slot_name"]: ConsultationSlotRule(
                slot_name=item["slot_name"],
                question=item["question"],
                label=item["label"],
                priority=int(item.get("priority", 100)),
            )
            for item in raw.get("slots", [])
        }
        return ConsultationRuleSet(
            domains=domains,
            slots=slots,
            safety_net_text=raw.get("safety_net_text", ""),
        )

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return (self.seed_dir / "consultation_rules.json").exists()


class PostgresRuleRepository:
    """基于 SQLAlchemy 的 PostgreSQL 问诊目录仓储。

    :return: 无返回值。
    """

    def __init__(self, database_url: str) -> None:
        """初始化当前对象。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.database_url = database_url
        self.session_factory = make_session_factory(database_url)

    def consultation_rules(self) -> ConsultationRuleSet:
        """从 PostgreSQL 读取问诊目录。

        :return: 返回问诊规则目录快照。
        """
        with self.session_factory() as session:
            domain_rows = session.scalars(
                select(ConsultationDomainModel)
                .where(ConsultationDomainModel.enabled.is_(True))
                .order_by(ConsultationDomainModel.priority, ConsultationDomainModel.domain)
            ).all()
            slot_rows = session.scalars(
                select(ConsultationSlotModel)
                .where(ConsultationSlotModel.enabled.is_(True))
                .order_by(ConsultationSlotModel.priority, ConsultationSlotModel.slot_name)
            ).all()
        domains = {
            row.domain: ConsultationDomainRule(
                domain=row.domain,
                required_slots=list(row.required_slots or []),
                priority=int(row.priority or 100),
            )
            for row in domain_rows
        }
        slots = {
            row.slot_name: ConsultationSlotRule(
                slot_name=row.slot_name,
                question=row.question,
                label=row.label,
                priority=int(row.priority or 100),
            )
            for row in slot_rows
        }
        return ConsultationRuleSet(domains=domains, slots=slots, safety_net_text="")

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        try:
            with self.session_factory() as session:
                domain_count = _count_enabled(session, ConsultationDomainModel)
                slot_count = _count_enabled(session, ConsultationSlotModel)
            return domain_count > 0 and slot_count > 0
        except SQLAlchemyError:
            return False


class FallbackRuleRepository:
    """组合主仓储和备用仓储的问诊目录读取仓储。

    :return: 无返回值。
    """

    def __init__(self, primary: RuleRepository, fallback: RuleRepository) -> None:
        """初始化当前对象。

        :param primary: 参数 primary。
        :param fallback: 参数 fallback。
        :return: 无返回值。
        """
        self.primary = primary
        self.fallback = fallback

    def consultation_rules(self) -> ConsultationRuleSet:
        """读取主仓储问诊目录，并在显式配置的兼容场景下使用 fallback。

        :return: 返回问诊规则目录快照。
        """
        try:
            rules = self.primary.consultation_rules()
            if rules.domains and rules.slots:
                return rules
            return self.fallback.consultation_rules()
        except Exception:
            return self.fallback.consultation_rules()

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return self.primary.is_ready() or self.fallback.is_ready()


def _count_enabled(session: Session, model: type) -> int:
    """执行 _count_enabled 内部辅助逻辑。

    :param session: 数据库会话。
    :param model: 模型名称。
    :return: 返回函数执行结果。
    """
    return int(session.scalar(select(func.count()).select_from(model).where(model.enabled.is_(True))) or 0)
