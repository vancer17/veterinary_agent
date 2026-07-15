from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.vet_agent.contracts import AttachmentRef, SafetySignal
from src.vet_agent.repositories.rules import RuleRepository, SafetyRule, compile_regex


@dataclass(frozen=True)
class SafetyAssessment:
    escalated: bool = False
    blocked: bool = False
    signals: list[SafetySignal] = field(default_factory=list)

    @property
    def highest_status(self) -> str:
        if self.blocked:
            return "blocked"
        if self.escalated:
            return "safety_escalated"
        return "ok"


class SafetyAgent:
    """Deterministic clinical safety gate that runs before and after LLM calls."""

    def __init__(self, rule_repository: RuleRepository) -> None:
        self.rule_repository = rule_repository

    def analyze(self, text: str, attachments: list[AttachmentRef]) -> SafetyAssessment:
        lowered = text.lower()
        signals: list[SafetySignal] = []
        rule_matches: dict[str, tuple[SafetyRule, list[str]]] = {}

        for rule in self.rule_repository.safety_rules():
            if rule.rule_type in {"dosage_output", "medical_output_marker", "medication_output_marker"}:
                continue
            matched_terms = self._match_rule(rule, lowered, attachments)
            if not matched_terms:
                continue
            existing_rule, existing_terms = rule_matches.get(rule.code, (rule, []))
            rule_matches[rule.code] = (existing_rule, [*existing_terms, *matched_terms])

        for rule, matched_terms in rule_matches.values():
            signals.append(
                SafetySignal(
                    code=rule.code,
                    severity=rule.severity,
                    message=rule.message,
                    matched_terms=sorted(set(matched_terms)),
                )
            )

        return SafetyAssessment(
            escalated=any(signal.severity == "urgent" for signal in signals),
            blocked=any(signal.severity == "blocked" for signal in signals),
            signals=signals,
        )

    def sanitize_output(self, text: str) -> tuple[str, list[SafetySignal]]:
        sanitized = text
        signals: list[SafetySignal] = []
        changed = False
        for rule in self._rules_by_type("dosage_output"):
            pattern = compile_regex(rule.pattern)
            sanitized, count = pattern.subn("【剂量已省略:请按药品使用说明书或遵从兽医指导】", sanitized)
            changed = changed or count > 0
        if changed:
            signals.append(
                SafetySignal(
                    code="DOSAGE_REMOVED",
                    severity="caution",
                    message="输出安全审查移除了具体剂量表达。",
                )
            )
        if self._looks_medical(sanitized) and "线下兽医" not in sanitized:
            sanitized = f"{sanitized}\n\n这是辅助参考，请以线下兽医诊断为准。"
        if self._matches_any_keyword(sanitized, "medication_output_marker") and "按药品使用说明书" not in sanitized:
            sanitized = f"{sanitized}\n涉及用药时，请按药品使用说明书或遵从兽医指导，具体药物与剂量由兽医确认。"
        return sanitized, signals

    def _looks_medical(self, text: str) -> bool:
        return self._matches_any_keyword(text, "medical_output_marker")

    def forced_response(self, assessment: SafetyAssessment) -> str:
        codes = {signal.code for signal in assessment.signals}
        for code in ("RADIOLOGY_GATE", "TOXIC_SUBSTANCE", "EMERGENCY_RED_FLAG"):
            if code not in codes:
                continue
            matched = "、".join(
                term
                for signal in assessment.signals
                if signal.code == code
                for term in signal.matched_terms
            )
            template = self._response_template_for(code)
            if template:
                return template.format(matched=matched)
        return "当前信息需要进一步确认。"

    def _match_rule(self, rule: SafetyRule, lowered_text: str, attachments: list[AttachmentRef]) -> list[str]:
        if rule.match_type == "keyword":
            return [rule.pattern for _ in [0] if rule.pattern.lower() in lowered_text]
        if rule.match_type == "regex":
            return [match.group(0) for match in compile_regex(rule.pattern).finditer(lowered_text)]
        if rule.match_type == "attachment_purpose":
            return [
                item.attachment_id
                for item in attachments
                if item.purpose.lower() == rule.pattern.lower()
            ]
        if rule.match_type == "storage_keyword":
            return [
                item.attachment_id
                for item in attachments
                if rule.pattern.lower() in item.storage_ref.lower()
            ]
        return []

    def _rules_by_type(self, rule_type: str) -> list[SafetyRule]:
        return [rule for rule in self.rule_repository.safety_rules() if rule.rule_type == rule_type]

    def _matches_any_keyword(self, text: str, rule_type: str) -> bool:
        lowered = text.lower()
        for rule in self._rules_by_type(rule_type):
            if rule.match_type == "keyword" and rule.pattern.lower() in lowered:
                return True
            if rule.match_type == "regex" and compile_regex(rule.pattern).search(lowered):
                return True
        return False

    def _response_template_for(self, code: str) -> str | None:
        for rule in self.rule_repository.safety_rules():
            if rule.code == code and rule.response_template:
                return rule.response_template
        return None
