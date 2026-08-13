"""
文件：src/vet_agent/repositories/rules.py
作用：提供规则库与 RAG 知识库的数据访问能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
    domain: str
    classifier_keywords: list[str]
    required_slots: list[str]
    priority: int = 100


@dataclass(frozen=True)
class ConsultationSlotRule:
    slot_name: str
    question: str
    label: str
    extraction_rules: list[dict[str, Any]]
    priority: int = 100


@dataclass(frozen=True)
class ConsultationRuleSet:
    domains: dict[str, ConsultationDomainRule]
    slots: dict[str, ConsultationSlotRule]
    safety_net_text: str


class RuleRepository(Protocol):
    def consultation_rules(self) -> ConsultationRuleSet:
        """执行 consultation_rules 业务逻辑。

        :return: 返回函数执行结果。
        """
        ...

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        ...


class FileRuleRepository:
    def __init__(self, seed_dir: Path) -> None:
        """初始化当前对象。

        :param seed_dir: 参数 seed_dir。
        :return: 无返回值。
        """
        self.seed_dir = seed_dir

    def consultation_rules(self) -> ConsultationRuleSet:
        """执行 consultation_rules 业务逻辑。

        :return: 返回函数执行结果。
        """
        raw = json.loads((self.seed_dir / "consultation_rules.json").read_text(encoding="utf-8"))
        domains = {
            item["domain"]: ConsultationDomainRule(
                domain=item["domain"],
                classifier_keywords=list(item.get("classifier_keywords", [])),
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
                extraction_rules=list(item.get("extraction_rules", [])),
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
    def __init__(self, database_url: str) -> None:
        """初始化当前对象。

        :param database_url: 数据库连接地址。
        :return: 无返回值。
        """
        self.database_url = database_url
        self.session_factory = make_session_factory(database_url)

    def consultation_rules(self) -> ConsultationRuleSet:
        """执行 consultation_rules 业务逻辑。

        :return: 返回函数执行结果。
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
                classifier_keywords=list(row.classifier_keywords or []),
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
                extraction_rules=list(row.extraction_rules or []),
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
    def __init__(self, primary: RuleRepository, fallback: RuleRepository) -> None:
        """初始化当前对象。

        :param primary: 参数 primary。
        :param fallback: 参数 fallback。
        :return: 无返回值。
        """
        self.primary = primary
        self.fallback = fallback

    def consultation_rules(self) -> ConsultationRuleSet:
        """执行 consultation_rules 业务逻辑。

        :return: 返回函数执行结果。
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


def compile_regex(pattern: str) -> re.Pattern[str]:
    """执行 compile_regex 业务逻辑。

    :param pattern: 参数 pattern。
    :return: 返回函数执行结果。
    """
    return re.compile(pattern, re.IGNORECASE)


def _count_enabled(session: Session, model) -> int:
    """执行 _count_enabled 内部辅助逻辑。

    :param session: 数据库会话。
    :param model: 模型名称。
    :return: 返回函数执行结果。
    """
    return int(session.scalar(select(func.count()).select_from(model).where(model.enabled.is_(True))) or 0)
