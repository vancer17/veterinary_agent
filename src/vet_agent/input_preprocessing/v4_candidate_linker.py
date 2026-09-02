"""Constrained canonical candidate recall for the V4 flat branch."""

from __future__ import annotations

import math
from typing import Protocol

from .v4_contracts import (
    V4CandidateSet,
    V4CanonicalCandidate,
    V4EntityType,
    V4SemanticClass,
)
from .vocabulary import CanonicalVocabulary


class V4EmbeddingClient(Protocol):
    """Minimal synchronous embedding interface used by candidate recall."""

    @property
    def available(self) -> bool: ...

    def embed(self, text: str) -> list[float]: ...


_CANONICAL_SEMANTIC_CLASS: dict[str, V4SemanticClass] = {
    "symptom": V4SemanticClass.STATE,
    "status": V4SemanticClass.STATE,
    "intake_output": V4SemanticClass.STATE,
    "behavior": V4SemanticClass.STATE,
    "intervention": V4SemanticClass.ACTION,
    "exposure": V4SemanticClass.EVENT,
    "measurement": V4SemanticClass.MEASUREMENT,
    "question_intent": V4SemanticClass.QUESTION,
}


class V4CandidateRetriever:
    """Recall auditable candidates without turning a neighbor into a fact."""

    def __init__(
        self,
        *,
        vocabulary: CanonicalVocabulary,
        embeddings: V4EmbeddingClient,
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

    def recall(
        self,
        *,
        observation_id: str,
        canonical_surface: str,
        subject_entity_type: V4EntityType,
    ) -> V4CandidateSet:
        """Return candidates for one flat observation, or an explicit empty set."""

        if not self.embeddings.available:
            return self._empty(observation_id, canonical_surface)
        if self._alias_vectors is None:
            self._alias_vectors = [
                (term.canonical_id, alias, self.embeddings.embed(alias))
                for term in self.vocabulary.terms
                for alias in term.aliases
            ]

        query = self.embeddings.embed(canonical_surface)
        scored = sorted(
            (
                (_cosine(query, vector), canonical_id, alias)
                for canonical_id, alias, vector in self._alias_vectors
            ),
            key=lambda value: (-value[0], value[1], value[2]),
        )
        terms = self.vocabulary.term_map()
        candidates: list[V4CanonicalCandidate] = []
        seen_canonical_ids: set[str] = set()
        for score, canonical_id, alias in scored:
            term = terms.get(canonical_id)
            if term is None or score < self.minimum_score:
                continue
            if canonical_id in seen_canonical_ids:
                continue
            if (
                subject_entity_type
                != V4EntityType.UNKNOWN
                and subject_entity_type.value not in term.allowed_subject_types
            ):
                continue
            candidates.append(
                V4CanonicalCandidate(
                    candidate_id=f"c-{len(candidates) + 1}",
                    canonical_id=canonical_id,
                    canonical_type=term.canonical_type,
                    semantic_class=_CANONICAL_SEMANTIC_CLASS.get(
                        term.canonical_type, V4SemanticClass.STATE
                    ),
                    surface_form=alias,
                    score=round(score, 6),
                    recall_source="embedding",
                )
            )
            seen_canonical_ids.add(canonical_id)
            if len(candidates) >= self.candidate_limit:
                break

        return V4CandidateSet(
            observation_id=observation_id,
            canonical_surface=canonical_surface,
            candidates=candidates,
            recall_status="recalled" if candidates else "no_candidate",
            recall_version=self.recall_version,
        )

    def selected_candidate(
        self,
        candidate_set: V4CandidateSet,
        selected_candidate_id: str | None,
    ) -> V4CanonicalCandidate | None:
        """Return a candidate only when its ID belongs to the item set."""

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

    def top_candidate(self, candidate_set: V4CandidateSet) -> V4CanonicalCandidate | None:
        """Return the deterministic top-1 candidate for the FLAT baseline."""

        return candidate_set.candidates[0] if candidate_set.candidates else None

    def _empty(
        self,
        observation_id: str,
        canonical_surface: str,
    ) -> V4CandidateSet:
        return V4CandidateSet(
            observation_id=observation_id,
            canonical_surface=canonical_surface,
            candidates=[],
            recall_status="no_candidate",
            recall_version=self.recall_version,
        )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
