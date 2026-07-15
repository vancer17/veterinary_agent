from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.vet_agent.db.models import (
    ConsultationDomainModel,
    ConsultationSlotModel,
    SafetyRuleModel,
)
from src.vet_agent.db.session import make_session_factory


@dataclass(frozen=True)
class SafetyRule:
    code: str
    rule_type: str
    match_type: str
    pattern: str
    severity: str
    message: str
    response_template: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
    def safety_rules(self) -> list[SafetyRule]:
        ...

    def consultation_rules(self) -> ConsultationRuleSet:
        ...

    def is_ready(self) -> bool:
        ...


class FileRuleRepository:
    def __init__(self, seed_dir: Path) -> None:
        self.seed_dir = seed_dir

    def safety_rules(self) -> list[SafetyRule]:
        raw = json.loads((self.seed_dir / "safety_rules.json").read_text(encoding="utf-8"))
        rules: list[SafetyRule] = []
        for item in raw:
            for pattern in item.get("patterns", []):
                rules.append(
                    SafetyRule(
                        code=item["code"],
                        rule_type=item["rule_type"],
                        match_type=item["match_type"],
                        pattern=pattern,
                        severity=item.get("severity", "caution"),
                        message=item["message"],
                        response_template=item.get("response_template"),
                        metadata=item.get("metadata", {}),
                    )
                )
        return rules

    def consultation_rules(self) -> ConsultationRuleSet:
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
        return (self.seed_dir / "safety_rules.json").exists() and (self.seed_dir / "consultation_rules.json").exists()


class PostgresRuleRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.session_factory = make_session_factory(database_url)

    def safety_rules(self) -> list[SafetyRule]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(SafetyRuleModel)
                .where(SafetyRuleModel.enabled.is_(True))
                .order_by(SafetyRuleModel.id)
            ).all()
        return [
            SafetyRule(
                code=row.code,
                rule_type=row.rule_type,
                match_type=row.match_type,
                pattern=row.pattern,
                severity=row.severity,
                message=row.message,
                response_template=row.response_template,
                metadata=row.metadata_json or {},
            )
            for row in rows
        ]

    def consultation_rules(self) -> ConsultationRuleSet:
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
        try:
            with self.session_factory() as session:
                safety_count = _count_enabled(session, SafetyRuleModel)
                domain_count = _count_enabled(session, ConsultationDomainModel)
                slot_count = _count_enabled(session, ConsultationSlotModel)
            return safety_count > 0 and domain_count > 0 and slot_count > 0
        except SQLAlchemyError:
            return False


class FallbackRuleRepository:
    def __init__(self, primary: RuleRepository, fallback: RuleRepository) -> None:
        self.primary = primary
        self.fallback = fallback

    def safety_rules(self) -> list[SafetyRule]:
        try:
            rules = self.primary.safety_rules()
            return rules or self.fallback.safety_rules()
        except Exception:
            return self.fallback.safety_rules()

    def consultation_rules(self) -> ConsultationRuleSet:
        try:
            rules = self.primary.consultation_rules()
            if rules.domains and rules.slots:
                return rules
            return self.fallback.consultation_rules()
        except Exception:
            return self.fallback.consultation_rules()

    def is_ready(self) -> bool:
        return self.primary.is_ready() or self.fallback.is_ready()


def compile_regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _count_enabled(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(model.enabled.is_(True))) or 0)
