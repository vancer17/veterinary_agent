from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from vet_agent.repositories.rules import ConsultationRuleSet, RuleRepository, compile_regex
from vet_agent.services.context import PetContext


SlotValue = str | bool | None


@dataclass
class ConsultationState:
    chief_complaint: str | None = None
    domain: str = "general"
    phase: str = "collecting_info"
    slots: dict[str, SlotValue] = field(default_factory=dict)
    asked_questions: list[str] = field(default_factory=list)
    followup_rounds: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConsultationState":
        if not data:
            return cls()
        return cls(
            chief_complaint=data.get("chief_complaint"),
            domain=data.get("domain") or "general",
            phase=data.get("phase") or "collecting_info",
            slots=dict(data.get("slots") or {}),
            asked_questions=list(data.get("asked_questions") or []),
            followup_rounds=int(data.get("followup_rounds") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chief_complaint": self.chief_complaint,
            "domain": self.domain,
            "phase": self.phase,
            "slots": self.slots,
            "asked_questions": self.asked_questions,
            "followup_rounds": self.followup_rounds,
        }


@dataclass(frozen=True)
class ConsultationDecision:
    state: ConsultationState
    ready: bool
    missing_slots: list[str]
    questions: list[str]


class ConsultationStateAgent:
    """Builds structured consultation context across turns before final advice."""

    def __init__(self, rule_repository: RuleRepository) -> None:
        self.rule_repository = rule_repository

    def update(
        self,
        previous: dict[str, Any] | None,
        user_text: str,
        pet_context: PetContext,
        *,
        max_questions: int,
    ) -> ConsultationDecision:
        state = ConsultationState.from_dict(previous)
        text = user_text.strip()
        if text and not state.chief_complaint:
            state.chief_complaint = text[:200]

        domain = self._classify_domain(text, state.domain)
        state.domain = domain
        self._prefill_from_pet_context(state, pet_context)
        self._extract_slots(state, text)

        rules = self.rule_repository.consultation_rules()
        required = self._required_slots(rules, state.domain)
        missing = [slot for slot in required if not state.slots.get(slot)]
        ready = not missing
        state.phase = "ready_to_answer" if ready else "collecting_info"

        questions = [] if ready else self._questions_for_missing(missing, state, max_questions)
        if questions:
            state.followup_rounds += 1
            state.asked_questions.extend(questions)
        return ConsultationDecision(state=state, ready=ready, missing_slots=missing, questions=questions)

    def format_followup_response(self, decision: ConsultationDecision) -> str:
        rules = self.rule_repository.consultation_rules()
        known = self._known_lines(decision.state)
        missing = "、".join(self._question_for(rules, slot) for slot in decision.missing_slots[:5])
        questions = "\n".join(f"{index + 1}. {question}" for index, question in enumerate(decision.questions))
        return (
            "我先不武断下结论，先把关键问诊信息补齐。这样可以避免把普通护理问题误判成疾病，"
            "也避免在信息不足时给出不可靠建议。\n\n"
            f"已知信息:\n{known or '- 目前只有你的主诉，还缺关键问诊信息。'}\n\n"
            f"还缺的关键点: {missing}\n\n"
            f"请先回答这几个问题:\n{questions}\n\n"
            f"{rules.safety_net_text}"
        )

    def format_state_for_prompt(self, state: ConsultationState) -> str:
        lines = [f"主诉: {state.chief_complaint or '未知'}", f"方向: {state.domain}"]
        for slot, value in state.slots.items():
            if value:
                lines.append(f"{slot}: {value}")
        return "\n".join(lines)

    def _classify_domain(self, text: str, previous_domain: str) -> str:
        rules = self.rule_repository.consultation_rules()
        for domain_rule in sorted(rules.domains.values(), key=lambda item: item.priority):
            if domain_rule.domain == "general":
                continue
            if any(keyword in text for keyword in domain_rule.classifier_keywords):
                return domain_rule.domain
        return previous_domain if previous_domain != "general" else "general"

    def _prefill_from_pet_context(self, state: ConsultationState, pet_context: PetContext) -> None:
        profile = pet_context.profile
        if profile.get("species") and profile["species"] != "未知":
            state.slots.setdefault("species", str(profile["species"]))
        if profile.get("age") and profile["age"] != "未知":
            state.slots.setdefault("life_stage_or_age", str(profile["age"]))
        if profile.get("weight_kg"):
            state.slots.setdefault("weight", f"{profile['weight_kg']}kg")

    def _extract_slots(self, state: ConsultationState, text: str) -> None:
        rules = self.rule_repository.consultation_rules()
        for slot_rule in rules.slots.values():
            value = self._extract_slot_value(slot_rule.extraction_rules, text)
            if value:
                state.slots[slot_rule.slot_name] = value

    def _questions_for_missing(self, missing: list[str], state: ConsultationState, max_questions: int) -> list[str]:
        questions: list[str] = []
        asked = set(state.asked_questions)
        rules = self.rule_repository.consultation_rules()
        for slot in missing:
            question = self._question_for(rules, slot)
            if question not in asked:
                questions.append(question)
            if len(questions) >= max_questions:
                break
        if not questions and missing:
            questions.append(self._question_for(rules, missing[0]))
        return questions

    def _known_lines(self, state: ConsultationState) -> str:
        rules = self.rule_repository.consultation_rules()
        lines = []
        for slot, value in state.slots.items():
            if value:
                label = rules.slots[slot].label if slot in rules.slots else slot
                lines.append(f"- {label}: {value}")
        return "\n".join(lines)

    def _required_slots(self, rules: ConsultationRuleSet, domain: str) -> list[str]:
        if domain in rules.domains:
            return rules.domains[domain].required_slots
        return rules.domains.get("general").required_slots if "general" in rules.domains else []

    def _question_for(self, rules: ConsultationRuleSet, slot: str) -> str:
        return rules.slots[slot].question if slot in rules.slots else slot

    def _extract_slot_value(self, extraction_rules: list[dict[str, Any]], text: str) -> str | None:
        for rule in extraction_rules:
            match_type = rule.get("match_type")
            if match_type == "keyword":
                patterns = rule.get("patterns", [])
                if any(pattern in text for pattern in patterns):
                    return str(rule.get("value") or patterns[0])
            if match_type == "keyword_value":
                for pattern in rule.get("patterns", []):
                    if pattern in text:
                        return str(rule.get("value") or pattern)
            if match_type in {"regex", "regex_value"}:
                match = compile_regex(rule["pattern"]).search(text)
                if match:
                    return str(rule.get("value") or match.group(0))
            if match_type == "text_if_keyword":
                if any(pattern in text for pattern in rule.get("patterns", [])):
                    return text[:120]
            if match_type == "text" and text:
                return text[:160]
        return None
