"""Constrained canonical recall from V5 target quotes."""

from __future__ import annotations

import math
from typing import Protocol

from .v5_contracts import (
    V5CandidateSet,
    V5CanonicalCandidate,
    V5EntityType,
)
from .vocabulary import CanonicalVocabulary


class V5EmbeddingClient(Protocol):
    """Minimal synchronous embedding interface used by candidate recall."""

    @property
    def available(self) -> bool: ...

    def embed(self, text: str) -> list[float]: ...


class V5CandidateRetriever:
    """Recall candidates from a target quote without inventing facts."""

    def __init__(
        self,
        *,
        vocabulary: CanonicalVocabulary,
        embeddings: V5EmbeddingClient,
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
        return (
            f"{self.vocabulary.version}:target-quote:top-"
            f"{self.candidate_limit}:min-{self.minimum_score:.2f}"
        )

    def recall(
        self,
        *,
        claim_id: str,
        target_quote: str,
        retrieval_quote: str | None = None,
        retrieval_context: str = "direct_target_quote",
        subject_entity_type: V5EntityType = V5EntityType.UNKNOWN,
        coarse_type: str | None = None,
    ) -> V5CandidateSet:
        if not self.embeddings.available:
            return self._empty(claim_id, target_quote)
        if self._alias_vectors is None:
            self._alias_vectors = [
                (term.canonical_id, alias, self.embeddings.embed(alias))
                for term in self.vocabulary.terms
                for alias in term.aliases
            ]
        query_text = retrieval_quote or target_quote
        query = self.embeddings.embed(query_text)
        scored = sorted(
            (
                (_cosine(query, vector), canonical_id, alias)
                for canonical_id, alias, vector in self._alias_vectors
            ),
            key=lambda value: (-value[0], value[1], value[2]),
        )
        terms = self.vocabulary.term_map()
        candidates: list[V5CanonicalCandidate] = []
        seen: set[str] = set()
        for score, canonical_id, alias in scored:
            term = terms.get(canonical_id)
            if term is None or score < self.minimum_score or canonical_id in seen:
                continue
            if (
                subject_entity_type != V5EntityType.UNKNOWN
                and subject_entity_type.value not in term.allowed_subject_types
            ):
                continue
            if coarse_type and not _coarse_type_compatible(
                coarse_type, term.canonical_type
            ):
                continue
            candidates.append(
                V5CanonicalCandidate(
                    candidate_id=f"c-{len(candidates) + 1}",
                    canonical_id=canonical_id,
                    canonical_type=term.canonical_type,
                    surface_form=alias,
                    score=round(score, 6),
                    recall_source="embedding",
                )
            )
            seen.add(canonical_id)
            if len(candidates) >= self.candidate_limit:
                break
        return V5CandidateSet(
            claim_id=claim_id,
            target_quote=target_quote,
            retrieval_query=query_text,
            retrieval_context=retrieval_context,  # type: ignore[arg-type]
            candidates=candidates,
            recall_status="recalled" if candidates else "no_candidate",
            recall_version=self.recall_version,
        )

    def top_candidate(
        self, candidate_set: V5CandidateSet
    ) -> V5CanonicalCandidate | None:
        return candidate_set.candidates[0] if candidate_set.candidates else None

    def selected_candidate(
        self,
        candidate_set: V5CandidateSet,
        selected_candidate_id: str | None,
    ) -> V5CanonicalCandidate | None:
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

    def _empty(self, claim_id: str, target_quote: str) -> V5CandidateSet:
        return V5CandidateSet(
            claim_id=claim_id,
            target_quote=target_quote,
            retrieval_query=target_quote,
            candidates=[],
            recall_status="no_candidate",
            recall_version=self.recall_version,
        )


def _coarse_type_compatible(coarse_type: str, canonical_type: str) -> bool:
    if coarse_type == "action":
        return canonical_type in {"intervention", "exposure"}
    if coarse_type in {"symptom", "state"}:
        return canonical_type in {
            "symptom",
            "status",
            "intake_output",
            "behavior",
            "measurement",
        }
    if coarse_type == "measurement":
        return canonical_type == "measurement"
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
