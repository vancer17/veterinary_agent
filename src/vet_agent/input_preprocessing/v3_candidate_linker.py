"""Constrained canonical candidate recall for the third-round V3 experiments."""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol

from .v3_contracts import (
    V3CandidateSet,
    V3CanonicalCandidate,
    V3EntityType,
    V3ParticipantRole,
    V3SemanticClass,
)
from .v3_stage1_assembler import V3ItemContext
from .vocabulary import CanonicalVocabulary


class V3EmbeddingClient(Protocol):
    """Minimal synchronous embedding interface used by candidate recall."""

    @property
    def available(self) -> bool: ...

    def embed(self, text: str) -> list[float]: ...


_CANONICAL_SEMANTIC_CLASS: dict[str, V3SemanticClass] = {
    "symptom": V3SemanticClass.STATE,
    "status": V3SemanticClass.STATE,
    "intake_output": V3SemanticClass.STATE,
    "behavior": V3SemanticClass.STATE,
    "intervention": V3SemanticClass.ACTION,
    "exposure": V3SemanticClass.EVENT,
    "measurement": V3SemanticClass.MEASUREMENT,
    "question_intent": V3SemanticClass.QUESTION,
}


class V3CandidateRetriever:
    """Recall auditable canonical candidates without inventing facts."""

    def __init__(
        self,
        *,
        vocabulary: CanonicalVocabulary,
        embeddings: V3EmbeddingClient,
        candidate_limit: int = 8,
        minimum_score: float = 0.72,
    ) -> None:
        self.vocabulary = vocabulary
        self.embeddings = embeddings
        self.candidate_limit = max(1, candidate_limit)
        self.minimum_score = minimum_score
        self._alias_vectors: list[tuple[str, str, list[float]]] | None = None

    @property
    def recall_version(self) -> str:
        """Return a versioned recall configuration for reproducible reports."""

        return (
            f"{self.vocabulary.version}:top-{self.candidate_limit}:"
            f"min-{self.minimum_score:.2f}"
        )

    def recall(self, item: V3ItemContext) -> V3CandidateSet:
        """Return candidates for one item; no candidate is an explicit state."""

        if not self.embeddings.available:
            return self._empty(item, "no_candidate")
        if self._alias_vectors is None:
            self._alias_vectors = [
                (
                    term.canonical_id,
                    alias,
                    self.embeddings.embed(alias),
                )
                for term in self.vocabulary.terms
                for alias in term.aliases
            ]

        query = self.embeddings.embed(item.source_text)
        scored = sorted(
            (
                (
                    _cosine(query, vector),
                    canonical_id,
                    alias,
                )
                for canonical_id, alias, vector in self._alias_vectors
            ),
            key=lambda value: (-value[0], value[1], value[2]),
        )
        candidates: list[V3CanonicalCandidate] = []
        seen_canonical_ids: set[str] = set()
        for score, canonical_id, alias in scored:
            if (
                score < self.minimum_score
                or canonical_id in seen_canonical_ids
                or not self._compatible(canonical_id, item)
            ):
                continue
            term = self.vocabulary.term_map()[canonical_id]
            candidates.append(
                V3CanonicalCandidate(
                    candidate_id=f"c-{len(candidates) + 1}",
                    canonical_id=canonical_id,
                    canonical_type=term.canonical_type,
                    semantic_class=_CANONICAL_SEMANTIC_CLASS.get(
                        term.canonical_type, V3SemanticClass.STATE
                    ),
                    surface_form=alias,
                    score=round(score, 6),
                    recall_source="embedding",
                )
            )
            seen_canonical_ids.add(canonical_id)
            if len(candidates) >= self.candidate_limit:
                break

        return V3CandidateSet(
            segment_id=item.segment_id,
            item_id=item.item_id,
            source_text=item.source_text,
            candidates=candidates,
            recall_status="recalled" if candidates else "no_candidate",
            recall_version=self.recall_version,
        )

    def selected_candidate(
        self,
        candidate_set: V3CandidateSet,
        selected_candidate_id: str | None,
    ) -> V3CanonicalCandidate | None:
        """Return a selected candidate only when it belongs to the item set."""

        if selected_candidate_id is None:
            return None
        return next(
            (
                candidate
                for candidate in candidate_set.candidates
                if candidate.candidate_id == selected_candidate_id
            ),
            None,
        )

    def _compatible(self, canonical_id: str, item: V3ItemContext) -> bool:
        """Apply coarse structural compatibility, never medical semantics."""

        term = self.vocabulary.term_map().get(canonical_id)
        if term is None:
            return False

        resolved_types = {
            reference.entity_type
            for reference in self._resolved_references(item)
        }
        if resolved_types and not resolved_types.issubset(
            set(term.allowed_subject_types)
        ):
            return False

        roles = {
            participant.role: participant.entity.entity_type
            for participant in item.participants
            if participant.entity.resolution_status.value == "resolved"
        }
        if term.canonical_type == "intervention":
            agent = roles.get(V3ParticipantRole.ACTION_AGENT)
            recipient = roles.get(V3ParticipantRole.ACTION_RECIPIENT)
            return agent in {
                V3EntityType.USER,
                V3EntityType.CAREGIVER,
                V3EntityType.MEDICAL_ACTOR,
            } and recipient in {V3EntityType.CURRENT_PET, V3EntityType.OTHER_PET}

        if term.canonical_type in {"symptom", "status", "intake_output", "behavior"}:
            experiencer = roles.get(V3ParticipantRole.EXPERIENCER)
            return item.subject.entity_type in {
                V3EntityType.CURRENT_PET,
                V3EntityType.OTHER_PET,
            } or experiencer in {
                V3EntityType.CURRENT_PET,
                V3EntityType.OTHER_PET,
            }
        return True

    def _resolved_references(self, item: V3ItemContext) -> list[Any]:
        references = [item.subject]
        references.extend(
            participant.entity for participant in item.participants
        )
        return [
            binding
            for binding in references
            if binding.resolution_status.value == "resolved"
            and binding.reference_id is not None
        ]

    def _empty(
        self,
        item: V3ItemContext,
        status: Literal["no_candidate", "not_applicable"],
    ) -> V3CandidateSet:
        return V3CandidateSet(
            segment_id=item.segment_id,
            item_id=item.item_id,
            source_text=item.source_text,
            candidates=[],
            recall_status=status,
            recall_version=self.recall_version,
        )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
