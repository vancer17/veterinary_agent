"""Framework-independent per-claim state governance for V5 experiments."""

from __future__ import annotations

from collections.abc import Iterable

from .v5_contracts import (
    GovernedThinUserClaim,
    ThinUserClaimRaw,
    V5ClaimStateStatus,
    V5ClaimStateVector,
    V5ClaimTransition,
    V5QuoteAnchor,
)


class ClaimGraphBuilder:
    """Build isolated claim states without a graph-framework dependency."""

    def create_claim(
        self,
        *,
        raw: ThinUserClaimRaw,
        evidence_quote: V5QuoteAnchor,
        target_quote: V5QuoteAnchor,
        temporal_quote: V5QuoteAnchor | None,
        measurement_quote: V5QuoteAnchor | None,
    ) -> tuple[GovernedThinUserClaim, list[V5ClaimTransition]]:
        """Create a claim and its initial quote/statement transitions."""

        state = V5ClaimStateVector()
        transitions: list[V5ClaimTransition] = []
        if evidence_quote.status == "resolved" and target_quote.status == "resolved":
            auxiliary_valid = (
                temporal_quote is None or temporal_quote.status == "resolved"
            ) and (measurement_quote is None or measurement_quote.status == "resolved")
            state.quote_state = (
                V5ClaimStateStatus.VERIFIED
                if auxiliary_valid
                else V5ClaimStateStatus.BLOCKED
            )
            transitions.append(
                self._transition(
                    raw.claim_id,
                    event="QUOTE_VERIFIED" if auxiliary_valid else "QUOTE_GATE_FAILED",
                    dimension="quote_state",
                    from_state="pending",
                    to_state=state.quote_state.value,
                    reason_code=(
                        "quote_anchors_resolved"
                        if auxiliary_valid
                        else "quote_anchor_invalid"
                    ),
                    evidence_refs=[raw.evidence_quote, raw.target_quote],
                )
            )
        else:
            state.quote_state = V5ClaimStateStatus.BLOCKED
            transitions.append(
                self._transition(
                    raw.claim_id,
                    event="QUOTE_GATE_FAILED",
                    dimension="quote_state",
                    from_state="pending",
                    to_state="blocked",
                    reason_code="quote_anchor_invalid",
                    evidence_refs=[raw.evidence_quote, raw.target_quote],
                )
            )

        state.statement_state = V5ClaimStateStatus.VERIFIED
        transitions.append(
            self._transition(
                raw.claim_id,
                event="STATEMENT_VALIDATED",
                dimension="statement_state",
                from_state="pending",
                to_state="verified",
                reason_code="user_statement_type_valid",
                evidence_refs=[raw.evidence_quote],
            )
        )

        if raw.subject_status == "ambiguous":
            state.subject_state = V5ClaimStateStatus.AMBIGUOUS
        else:
            state.subject_state = V5ClaimStateStatus.PENDING
        state.participant_state = V5ClaimStateStatus.PENDING
        state.temporal_state = (
            V5ClaimStateStatus.PENDING
            if raw.temporal_quote
            else V5ClaimStateStatus.NOT_REQUIRED
        )
        state.measurement_state = (
            V5ClaimStateStatus.PENDING
            if raw.measurement_quote
            else V5ClaimStateStatus.NOT_REQUIRED
        )
        state.assertion_state = (
            V5ClaimStateStatus.REVIEW_REQUIRED
            if raw.needs_review or raw.confidence < 0.60
            else V5ClaimStateStatus.NOT_REQUIRED
        )
        state.canonical_state = V5ClaimStateStatus.PENDING
        state.projection_state = V5ClaimStateStatus.PENDING
        return (
            GovernedThinUserClaim(
                raw=raw,
                evidence_quote=evidence_quote,
                target_quote=target_quote,
                temporal_quote=temporal_quote,
                measurement_quote=measurement_quote,
                state=state,
            ),
            transitions,
        )

    def transition(
        self,
        claim: GovernedThinUserClaim,
        *,
        event: str,
        dimension: str,
        to_state: V5ClaimStateStatus,
        reason_code: str,
        evidence_refs: Iterable[str] = (),
    ) -> V5ClaimTransition:
        """Apply one explicit state transition and return its audit record."""

        current = self._dimension_state(claim.state, dimension)
        self._set_dimension_state(claim.state, dimension, to_state)
        return self._transition(
            claim.raw.claim_id,
            event=event,
            dimension=dimension,
            from_state=current.value,
            to_state=to_state.value,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
        )

    def aggregate_states(
        self,
        claims: list[GovernedThinUserClaim],
    ) -> dict[str, list[str]]:
        """Return macro labels derived from each claim state vector."""

        result: dict[str, list[str]] = {}
        for claim in claims:
            labels: list[str] = []
            state = claim.state
            if state.quote_state == V5ClaimStateStatus.VERIFIED:
                labels.append("quote_verified")
            if state.subject_state == V5ClaimStateStatus.READY:
                labels.append("subject_resolved")
            if any(
                getattr(state, dimension) == V5ClaimStateStatus.PENDING
                for dimension in _REQUIRED_DIMENSIONS
            ):
                labels.append("enrichment_required")
            if all(
                getattr(state, dimension)
                not in {V5ClaimStateStatus.PENDING, V5ClaimStateStatus.BLOCKED}
                for dimension in _REQUIRED_DIMENSIONS
            ):
                labels.append("enriched")
            if state.canonical_state == V5ClaimStateStatus.READY:
                labels.append("canonical_confirmed")
            if state.projection_state == V5ClaimStateStatus.READY:
                labels.append("projection_ready")
            if state.projection_state == V5ClaimStateStatus.READY:
                labels.append("projected")
            if any(
                getattr(state, dimension)
                in {V5ClaimStateStatus.REVIEW_REQUIRED, V5ClaimStateStatus.AMBIGUOUS}
                for dimension in _ALL_DIMENSIONS
            ):
                labels.append("review_required")
            if any(
                getattr(state, dimension) == V5ClaimStateStatus.BLOCKED
                for dimension in _ALL_DIMENSIONS
            ):
                labels.append("blocked")
            if not labels:
                labels.append("extracted")
            result[claim.raw.claim_id] = labels
        return result

    def _dimension_state(
        self,
        state: V5ClaimStateVector,
        dimension: str,
    ) -> V5ClaimStateStatus:
        value = getattr(state, dimension)
        if not isinstance(value, V5ClaimStateStatus):
            raise TypeError(f"invalid_claim_state_dimension:{dimension}")
        return value

    def _set_dimension_state(
        self,
        state: V5ClaimStateVector,
        dimension: str,
        value: V5ClaimStateStatus,
    ) -> None:
        if not hasattr(state, dimension):
            raise ValueError(f"invalid_claim_state_dimension:{dimension}")
        setattr(state, dimension, value)

    def _transition(
        self,
        claim_id: str,
        *,
        event: str,
        dimension: str,
        from_state: str,
        to_state: str,
        reason_code: str,
        evidence_refs: Iterable[str] = (),
    ) -> V5ClaimTransition:
        return V5ClaimTransition(
            claim_id=claim_id,
            event=event,
            dimension=dimension,
            from_state=from_state,
            to_state=to_state,
            reason_code=reason_code,
            evidence_refs=list(evidence_refs)[:16],
        )


_REQUIRED_DIMENSIONS = (
    "subject_state",
    "participant_state",
    "temporal_state",
    "measurement_state",
    "assertion_state",
    "canonical_state",
)
_ALL_DIMENSIONS = (
    "quote_state",
    *_REQUIRED_DIMENSIONS,
    "projection_state",
)
