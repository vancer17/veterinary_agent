"""Policy-driven enrichment routing for V5 thin claims."""

from __future__ import annotations

from dataclasses import dataclass

from .v5_contracts import (
    GovernedThinUserClaim,
    V5ClaimStateStatus,
    V5ClaimStateVector,
)


@dataclass(frozen=True)
class V5EnrichmentDecision:
    claim_id: str
    reference: bool
    participant: bool
    temporal: bool
    measurement: bool
    assertion: bool
    canonical: bool
    reason_codes: tuple[str, ...]


class V5EnrichmentPolicy:
    """Route only structurally required enrichment nodes.

    This router is intentionally not an OPA client and contains no medical
    rules.  It consumes only thin-claim state and coarse type for experiments.
    """

    def decide(
        self,
        claim: GovernedThinUserClaim,
        *,
        always_enrich: bool = False,
    ) -> V5EnrichmentDecision:
        raw = claim.raw
        state = claim.state
        reasons: list[str] = []
        reference = always_enrich or state.subject_state != V5ClaimStateStatus.READY
        if reference:
            reasons.append("reference_resolution_required")

        action_claim = raw.coarse_type.value in {
            "action",
            "food",
            "medication",
        }
        participant = always_enrich or (
            action_claim
            and state.participant_state
            not in {V5ClaimStateStatus.READY, V5ClaimStateStatus.NOT_REQUIRED}
        )
        if participant:
            reasons.append("participant_enrichment_required")

        temporal = always_enrich or bool(raw.temporal_quote)
        if temporal:
            reasons.append("temporal_enrichment_required")
        elif not raw.temporal_quote:
            state.temporal_state = V5ClaimStateStatus.NOT_REQUIRED

        measurement = always_enrich or bool(raw.measurement_quote)
        if measurement:
            reasons.append("measurement_enrichment_required")
        elif not raw.measurement_quote:
            state.measurement_state = V5ClaimStateStatus.NOT_REQUIRED

        assertion = always_enrich or state.assertion_state not in {
            V5ClaimStateStatus.VERIFIED,
            V5ClaimStateStatus.NOT_REQUIRED,
        }
        if assertion:
            reasons.append("assertion_verification_required")
        else:
            state.assertion_state = V5ClaimStateStatus.NOT_REQUIRED

        canonical = always_enrich or state.canonical_state == V5ClaimStateStatus.PENDING
        if canonical:
            reasons.append("canonical_link_required")

        if not action_claim and state.participant_state == V5ClaimStateStatus.PENDING:
            state.participant_state = V5ClaimStateStatus.NOT_REQUIRED

        return V5EnrichmentDecision(
            claim_id=raw.claim_id,
            reference=reference,
            participant=participant,
            temporal=temporal,
            measurement=measurement,
            assertion=assertion,
            canonical=canonical,
            reason_codes=tuple(reasons),
        )


def projection_readiness(state: V5ClaimStateVector) -> tuple[bool, list[str]]:
    """Return whether a claim is consumable and which dimensions are missing."""

    missing: list[str] = []
    for dimension, required in (
        ("quote_state", True),
        ("statement_state", True),
        ("subject_state", True),
        ("participant_state", True),
        ("temporal_state", True),
        ("measurement_state", True),
        ("assertion_state", True),
        ("canonical_state", True),
    ):
        if not required:
            continue
        value = getattr(state, dimension)
        if value not in {
            V5ClaimStateStatus.VERIFIED,
            V5ClaimStateStatus.READY,
            V5ClaimStateStatus.NOT_REQUIRED,
        }:
            missing.append(dimension)
    return not missing, missing
