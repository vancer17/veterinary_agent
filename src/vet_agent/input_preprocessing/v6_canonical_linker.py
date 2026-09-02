"""Constrained V6 canonical recall and under-confirmation diagnostics."""

from __future__ import annotations

import math
from typing import Literal, Protocol

from .v6_contracts import (
    GovernedThinUserClaim,
    V6CandidateSet,
    V6CanonicalCandidate,
    V6CanonicalDiagnostic,
    V6CanonicalMapping,
    V6CanonicalMappingStatus,
    V6ClaimStateStatus,
    V6EntityType,
)
from .vocabulary import CanonicalVocabulary


class V6EmbeddingClient(Protocol):
    """Minimal synchronous embedding interface used by candidate recall."""

    @property
    def available(self) -> bool: ...

    def embed(self, text: str) -> list[float]: ...


class V6CandidateRetriever:
    """Recall canonical candidates without subject-stage filtering."""

    def __init__(
        self,
        *,
        vocabulary: CanonicalVocabulary,
        embeddings: V6EmbeddingClient,
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
            f"{self.vocabulary.version}:target-quote-unfiltered-subject:top-"
            f"{self.candidate_limit}:min-{self.minimum_score:.2f}"
        )

    def recall(
        self,
        *,
        claim_id: str,
        target_quote: str,
        retrieval_quote: str | None = None,
        retrieval_context: Literal[
            "direct_target_quote", "previous_question_target", "hybrid"
        ] = "direct_target_quote",
        coarse_type: str | None = None,
    ) -> V6CandidateSet:
        if not self.embeddings.available:
            return V6CandidateSet(
                claim_id=claim_id,
                target_quote=target_quote,
                retrieval_query=target_quote,
                retrieval_context=retrieval_context,
                candidates=[],
                recalled_candidates=[],
                filtered_candidates=[],
                filter_reasons=[V6CanonicalDiagnostic.NO_CANDIDATE_RECALLED.value],
                recall_status="no_candidate",
                recall_version=self.recall_version,
            )
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
        recalled: list[V6CanonicalCandidate] = []
        filtered: list[V6CanonicalCandidate] = []
        filter_reasons: list[str] = []
        seen: set[str] = set()
        for score, canonical_id, alias in scored:
            term = terms.get(canonical_id)
            if term is None or canonical_id in seen:
                continue
            candidate = V6CanonicalCandidate(
                candidate_id=f"c-{len(recalled) + len(filtered) + 1}",
                canonical_id=canonical_id,
                canonical_type=term.canonical_type,
                surface_form=alias,
                score=round(score, 6),
                recall_source="embedding",
            )
            if score < self.minimum_score:
                continue
            if coarse_type and not coarse_type_compatible(
                coarse_type,
                term.canonical_type,
            ):
                filtered.append(candidate)
                if "candidate_filtered_by_coarse_type" not in filter_reasons:
                    filter_reasons.append("candidate_filtered_by_coarse_type")
                seen.add(canonical_id)
                continue
            recalled.append(candidate)
            seen.add(canonical_id)
            if len(recalled) >= self.candidate_limit:
                break

        return V6CandidateSet(
            claim_id=claim_id,
            target_quote=target_quote,
            retrieval_query=query_text,
            retrieval_context=retrieval_context,  # type: ignore[arg-type]
            candidates=recalled[: self.candidate_limit],
            recalled_candidates=recalled,
            filtered_candidates=filtered,
            filter_reasons=filter_reasons,
            recall_status="recalled" if recalled else "no_candidate",
            recall_version=self.recall_version,
        )

    def selected_candidate(
        self,
        candidate_set: V6CandidateSet,
        selected_candidate_id: str | None,
    ) -> V6CanonicalCandidate | None:
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

    def top_candidate(
        self,
        candidate_set: V6CandidateSet,
    ) -> V6CanonicalCandidate | None:
        return candidate_set.candidates[0] if candidate_set.candidates else None

    def mapping(
        self,
        *,
        claim: GovernedThinUserClaim,
        subject_entity_type: V6EntityType = V6EntityType.UNKNOWN,
        previous_question_target: str | None = None,
    ) -> V6CanonicalMapping:
        retrieval_quote = claim.raw.target_quote
        retrieval_context: Literal[
            "direct_target_quote", "previous_question_target", "hybrid"
        ] = "direct_target_quote"
        if (
            previous_question_target
            and claim.raw.target_quote == claim.raw.evidence_quote
        ):
            retrieval_quote = previous_question_target
            retrieval_context = "previous_question_target"
        candidate_set = self.recall(
            claim_id=claim.raw.claim_id,
            target_quote=claim.raw.target_quote,
            retrieval_quote=retrieval_quote,
            retrieval_context=retrieval_context,
            coarse_type=claim.raw.coarse_type.value,
        )
        selected = self.top_candidate(candidate_set)
        if selected is not None:
            second_score = (
                candidate_set.candidates[1].score
                if len(candidate_set.candidates) > 1
                else 0.0
            )
            margin = max(0.0, selected.score - second_score)
            status = V6ClaimStateStatus.READY
            mapping_status = V6CanonicalMappingStatus.CONFIRMED
            diagnostic = V6CanonicalDiagnostic.NOT_APPLICABLE
            review = False
        else:
            margin = 0.0
            status = V6ClaimStateStatus.REVIEW_REQUIRED
            mapping_status = V6CanonicalMappingStatus.NOT_FOUND
            diagnostic = _infer_not_found_diagnostic(
                candidate_set=candidate_set,
            )
            review = True
        return V6CanonicalMapping(
            claim_id=claim.raw.claim_id,
            status=status,
            candidate_set=candidate_set,
            selected_candidate_id=selected.candidate_id if selected else None,
            canonical_id=selected.canonical_id if selected else None,
            mapping_status=mapping_status,
            diagnostic=diagnostic,
            selection_margin=round(margin, 6),
            review_required=review,
            failure_reason="" if selected else diagnostic.value,
        )


def _infer_not_found_diagnostic(
    *,
    candidate_set: V6CandidateSet,
) -> V6CanonicalDiagnostic:
    if candidate_set.filtered_candidates:
        return V6CanonicalDiagnostic.FILTERED_BY_COARSE_TYPE
    if candidate_set.filter_reasons:
        reason = candidate_set.filter_reasons[0]
        try:
            return V6CanonicalDiagnostic(reason)
        except ValueError:
            return V6CanonicalDiagnostic.NO_CANDIDATE_RECALLED
    return V6CanonicalDiagnostic.NO_CANDIDATE_RECALLED


def coarse_type_compatible(coarse_type: str, canonical_type: str) -> bool:
    if coarse_type in {"action", "food", "medication"}:
        return canonical_type in {"intervention", "exposure", "context"}
    if coarse_type == "symptom":
        return canonical_type in {"symptom", "status"}
    if coarse_type == "state":
        return canonical_type in {"status", "intake_output", "behavior"}
    if coarse_type == "measurement":
        return canonical_type in {"measurement", "intake_output"}
    if coarse_type == "time":
        return canonical_type in {"measurement", "context"}
    return True


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
