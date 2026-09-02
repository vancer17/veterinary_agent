"""Framework-independent per-claim state governance for V6 experiments."""

from __future__ import annotations

from collections.abc import Iterable

from .v6_contracts import (
    GovernedThinUserClaim,
    ThinUserClaimRaw,
    V6ClaimStateStatus,
    V6ClaimStateVector,
    V6ClaimTransition,
)
from .v6_quote_governance import ThinClaimQuotes


class ClaimGraphBuilder:
    """Build isolated claim states without a graph-framework dependency."""

    def create_claim(
        self,
        *,
        raw: ThinUserClaimRaw,
        quotes: ThinClaimQuotes,
    ) -> tuple[GovernedThinUserClaim, list[V6ClaimTransition]]:
        """Create a claim and its initial quote/statement transitions."""

        state = V6ClaimStateVector()
        transitions: list[V6ClaimTransition] = []
        anchors = (
            quotes.evidence,
            quotes.target,
            quotes.temporal,
            quotes.measurement,
            quotes.relation,
            quotes.subject_evidence,
        )
        anchors_valid = (
            all(item is None or item.status == "resolved" for item in anchors)
            and quotes.evidence.status == "resolved"
            and quotes.target.status == "resolved"
        )
        state.quote_state = (
            V6ClaimStateStatus.VERIFIED if anchors_valid else V6ClaimStateStatus.BLOCKED
        )
        transitions.append(
            self._transition(
                raw.claim_id,
                event="QUOTE_VERIFIED" if anchors_valid else "QUOTE_GATE_FAILED",
                dimension="quote_state",
                from_state="pending",
                to_state=state.quote_state.value,
                reason_code=(
                    "quote_anchors_resolved"
                    if anchors_valid
                    else "quote_anchor_invalid"
                ),
                evidence_refs=[raw.evidence_quote, raw.target_quote],
            )
        )

        statement_valid = not (
            raw.relation == "no_change"
            and raw.user_statement_type.value == "reports_normal"
        )
        state.statement_state = (
            V6ClaimStateStatus.VERIFIED
            if statement_valid
            else V6ClaimStateStatus.REVIEW_REQUIRED
        )
        transitions.append(
            self._transition(
                raw.claim_id,
                event="STATEMENT_VALIDATED" if statement_valid else "STATEMENT_REVIEW",
                dimension="statement_state",
                from_state="pending",
                to_state=state.statement_state.value,
                reason_code=(
                    "statement_relation_valid"
                    if statement_valid
                    else "normal_conflicts_with_no_change_relation"
                ),
                evidence_refs=[raw.evidence_quote],
            )
        )

        if raw.subject_status == "ambiguous":
            state.subject_state = V6ClaimStateStatus.AMBIGUOUS
        else:
            state.subject_state = V6ClaimStateStatus.PENDING
        state.participant_state = V6ClaimStateStatus.PENDING
        state.temporal_state = (
            V6ClaimStateStatus.PENDING
            if raw.temporal_quote
            else V6ClaimStateStatus.NOT_REQUIRED
        )
        state.measurement_state = (
            V6ClaimStateStatus.PENDING
            if raw.measurement_quote
            else V6ClaimStateStatus.NOT_REQUIRED
        )
        state.assertion_state = (
            V6ClaimStateStatus.REVIEW_REQUIRED
            if (
                raw.needs_review
                or raw.confidence < 0.60
                or raw.user_statement_type.value == "corrects"
            )
            else V6ClaimStateStatus.NOT_REQUIRED
        )
        state.canonical_state = V6ClaimStateStatus.PENDING
        state.projection_state = V6ClaimStateStatus.PENDING
        return (
            GovernedThinUserClaim(
                raw=raw,
                evidence_quote=quotes.evidence,
                target_quote=quotes.target,
                temporal_quote=quotes.temporal,
                measurement_quote=quotes.measurement,
                relation_quote=quotes.relation,
                subject_evidence_quote=quotes.subject_evidence,
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
        to_state: V6ClaimStateStatus,
        reason_code: str,
        evidence_refs: Iterable[str] = (),
    ) -> V6ClaimTransition:
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
            if state.quote_state == V6ClaimStateStatus.VERIFIED:
                labels.append("quote_verified")
            if state.subject_state == V6ClaimStateStatus.READY:
                labels.append("subject_resolved")
            if any(
                getattr(state, dimension)
                in {
                    V6ClaimStateStatus.PENDING,
                    V6ClaimStateStatus.PLANNED,
                    V6ClaimStateStatus.BATCHED,
                }
                for dimension in _REQUIRED_DIMENSIONS
            ):
                labels.append("enrichment_required")
            if all(
                getattr(state, dimension)
                not in {
                    V6ClaimStateStatus.PENDING,
                    V6ClaimStateStatus.PLANNED,
                    V6ClaimStateStatus.BATCHED,
                    V6ClaimStateStatus.BLOCKED,
                }
                for dimension in _REQUIRED_DIMENSIONS
            ):
                labels.append("enriched")
            if state.canonical_state == V6ClaimStateStatus.READY:
                labels.append("canonical_confirmed")
            if state.projection_state == V6ClaimStateStatus.READY:
                labels.append("projection_ready")
                labels.append("projected")
            if any(
                getattr(state, dimension)
                in {V6ClaimStateStatus.REVIEW_REQUIRED, V6ClaimStateStatus.AMBIGUOUS}
                for dimension in _ALL_DIMENSIONS
            ):
                labels.append("review_required")
            if any(
                getattr(state, dimension) == V6ClaimStateStatus.BLOCKED
                for dimension in _ALL_DIMENSIONS
            ):
                labels.append("blocked")
            if not labels:
                labels.append("extracted")
            result[claim.raw.claim_id] = labels
        return result

    def _dimension_state(
        self,
        state: V6ClaimStateVector,
        dimension: str,
    ) -> V6ClaimStateStatus:
        value = getattr(state, dimension)
        if not isinstance(value, V6ClaimStateStatus):
            raise TypeError(f"invalid_claim_state_dimension:{dimension}")
        return value

    def _set_dimension_state(
        self,
        state: V6ClaimStateVector,
        dimension: str,
        value: V6ClaimStateStatus,
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
    ) -> V6ClaimTransition:
        return V6ClaimTransition(
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
