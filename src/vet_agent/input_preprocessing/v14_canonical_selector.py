"""Dual-query, candidate-only canonical selection for V14."""

from __future__ import annotations

from dataclasses import dataclass

from .v6_canonical_linker import V6CandidateRetriever, coarse_type_compatible


@dataclass(frozen=True)
class V14CanonicalCandidate:
    candidate_id: str
    canonical_id: str
    canonical_type: str
    alias: str
    score: float
    source: str


@dataclass(frozen=True)
class V14CanonicalSelection:
    status: str
    selected_candidate_id: str | None
    selected_canonical_id: str | None
    candidates: list[V14CanonicalCandidate]
    reason: str


class V14ConstrainedCanonicalSelector:
    """Merge target/descriptor recall, then select only from candidates."""

    def __init__(
        self,
        *,
        retriever: V6CandidateRetriever,
        score_margin: float = 0.03,
    ) -> None:
        self.retriever = retriever
        self.score_margin = score_margin

    def recall_candidates(
        self,
        *,
        claim_id: str,
        target_phrase: str,
        descriptor: str | None,
        coarse_type: str,
    ) -> list[V14CanonicalCandidate]:
        candidate_map: dict[str, V14CanonicalCandidate] = {}
        queries = [("target", target_phrase)]
        if descriptor:
            queries.append(("descriptor", descriptor))
        for source, query in queries:
            recalled = self.retriever.recall(
                claim_id=claim_id,
                target_quote=target_phrase,
                retrieval_quote=query,
                coarse_type=coarse_type,
            ).candidates
            for item in recalled:
                if not coarse_type_compatible(coarse_type, item.canonical_type):
                    continue
                candidate_id = f"{source}:{item.canonical_id}"
                candidate_map[candidate_id] = V14CanonicalCandidate(
                    candidate_id=candidate_id,
                    canonical_id=item.canonical_id,
                    canonical_type=item.canonical_type,
                    alias=item.surface_form,
                    score=float(item.score),
                    source=source,
                )
            for term in self.retriever.vocabulary.terms:
                if not coarse_type_compatible(coarse_type, term.canonical_type):
                    continue
                if query not in term.aliases:
                    continue
                candidate_id = f"alias:{term.canonical_id}"
                candidate_map[candidate_id] = V14CanonicalCandidate(
                    candidate_id=candidate_id,
                    canonical_id=term.canonical_id,
                    canonical_type=term.canonical_type,
                    alias=query,
                    score=1.0,
                    source="alias",
                )
        return sorted(
            candidate_map.values(),
            key=lambda item: (-item.score, item.canonical_id, item.candidate_id),
        )

    def select(
        self,
        *,
        claim_id: str,
        target_phrase: str,
        descriptor: str | None,
        coarse_type: str,
    ) -> V14CanonicalSelection:
        candidates = self.recall_candidates(
            claim_id=claim_id,
            target_phrase=target_phrase,
            descriptor=descriptor,
            coarse_type=coarse_type,
        )
        if not candidates:
            return V14CanonicalSelection(
                status="not_found",
                selected_candidate_id=None,
                selected_canonical_id=None,
                candidates=[],
                reason="no_type_compatible_candidate",
            )
        exact_alias_candidates = [
            item for item in candidates if item.source == "alias"
        ]
        if len({item.canonical_id for item in exact_alias_candidates}) == 1:
            exact = exact_alias_candidates[0]
            return V14CanonicalSelection(
                status="confirmed",
                selected_candidate_id=exact.candidate_id,
                selected_canonical_id=exact.canonical_id,
                candidates=candidates,
                reason="exact_alias_candidate_only",
            )
        # Prefer an exact target-source alias score when descriptor recall is
        # slightly broader; ties keep ambiguity explicit instead of inventing a
        # canonical ID.
        best = candidates[0]
        tied = [
            item
            for item in candidates
            if best.score - item.score < self.score_margin
            and item.canonical_id != best.canonical_id
        ]
        if tied:
            return V14CanonicalSelection(
                status="ambiguous",
                selected_candidate_id=None,
                selected_canonical_id=None,
                candidates=candidates,
                reason="candidate_score_margin_below_threshold",
            )
        return V14CanonicalSelection(
            status="confirmed",
            selected_candidate_id=best.candidate_id,
            selected_canonical_id=best.canonical_id,
            candidates=candidates,
            reason="candidate_only_constrained_selection",
        )
