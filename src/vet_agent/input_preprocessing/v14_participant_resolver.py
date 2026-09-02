"""TurnContext candidate-only participant resolution for V14."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .v8_contracts import V8EntityCandidate

ParticipantRole = Literal[
    "subject",
    "action_agent",
    "action_recipient",
    "experiencer",
    "object",
]

_COMPATIBLE_TYPES: dict[str, set[str]] = {
    "subject": {"current_pet", "other_pet"},
    "experiencer": {"current_pet", "other_pet"},
    "action_agent": {"user", "caregiver", "medical_actor"},
    "action_recipient": {"current_pet", "other_pet"},
    "object": {"food", "medication", "environment", "other_pet", "current_pet"},
}


@dataclass(frozen=True)
class V14ParticipantResolution:
    role: str
    phrase: str
    status: str
    selected_reference_id: str | None
    candidate_reference_ids: list[str]
    reason: str


class V14TurnContextParticipantResolver:
    """Resolve participant phrases only against TurnContext candidates."""

    def resolve(
        self,
        *,
        role: str,
        phrase: str,
        candidates: list[V8EntityCandidate],
    ) -> V14ParticipantResolution:
        if not phrase:
            return V14ParticipantResolution(
                role=role,
                phrase="",
                status="missing",
                selected_reference_id=None,
                candidate_reference_ids=[],
                reason="participant_phrase_missing",
            )
        normalized = phrase.strip()
        compatible = [
            item
            for item in candidates
            if item.entity_type in _COMPATIBLE_TYPES.get(role, set())
        ]
        surface_matches = [
            item
            for item in compatible
            if normalized == item.display_name
            or normalized in item.mention_aliases
        ]
        if not surface_matches:
            return V14ParticipantResolution(
                role=role,
                phrase=phrase,
                status="unresolved",
                selected_reference_id=None,
                candidate_reference_ids=[item.reference_id for item in compatible],
                reason="no_turncontext_surface_match",
            )
        if len(surface_matches) > 1:
            return V14ParticipantResolution(
                role=role,
                phrase=phrase,
                status="ambiguous",
                selected_reference_id=None,
                candidate_reference_ids=sorted(
                    item.reference_id for item in surface_matches
                ),
                reason="multiple_turncontext_candidates",
            )
        selected = surface_matches[0]
        return V14ParticipantResolution(
            role=role,
            phrase=phrase,
            status="resolved",
            selected_reference_id=selected.reference_id,
            candidate_reference_ids=[selected.reference_id],
            reason="turncontext_alias_match",
        )


def resolve_claim_participants(
    governed_claim: Any,
    *,
    candidates: list[V8EntityCandidate],
) -> dict[str, V14ParticipantResolution]:
    resolver = V14TurnContextParticipantResolver()
    result: dict[str, V14ParticipantResolution] = {}
    for role in ("subject", "action_agent", "action_recipient", "experiencer"):
        phrase = str(
            getattr(governed_claim.raw_claim, f"{role}_phrase") or ""
        )
        if phrase:
            result[role] = resolver.resolve(
                role=role,
                phrase=phrase,
                candidates=candidates,
            )
    return result
