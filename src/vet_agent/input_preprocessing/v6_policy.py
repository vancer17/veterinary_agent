"""Policy evaluation and structural batch planning for V6 thin claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .v6_contracts import (
    EnrichmentBatch,
    EnrichmentPlan,
    EnrichmentRequest,
    GovernedThinUserClaim,
    V6ClaimStateStatus,
    V6ClaimStateVector,
    V6EnrichmentType,
)

V6_POLICY_VERSION = "v6-policy-dev-20260826-1"
V6_ENRICHMENT_PLANNER_VERSION = "v6-enrichment-planner-dev-20260826-1"
_MAX_BATCH_SIZE = 8


@dataclass(frozen=True)
class V6EnrichmentDecision:
    claim_id: str
    reference: bool
    participant: bool
    temporal: bool
    measurement: bool
    assertion: bool
    canonical: bool
    reason_codes: tuple[str, ...]


class V6EnrichmentPolicy:
    """Route only structurally required enrichment requests."""

    def decide(
        self,
        claim: GovernedThinUserClaim,
        *,
        always_enrich: bool = False,
    ) -> V6EnrichmentDecision:
        raw = claim.raw
        state = claim.state
        reasons: list[str] = []

        reference = always_enrich or state.subject_state not in {
            V6ClaimStateStatus.READY,
            V6ClaimStateStatus.NOT_REQUIRED,
        }
        if reference:
            reasons.append("reference_resolution_required")

        action_claim = raw.coarse_type.value in {"action", "food", "medication"}
        participant = always_enrich or (
            action_claim
            and state.participant_state
            not in {V6ClaimStateStatus.READY, V6ClaimStateStatus.NOT_REQUIRED}
        )
        if participant:
            reasons.append("participant_enrichment_required")
        elif not action_claim:
            state.participant_state = V6ClaimStateStatus.NOT_REQUIRED

        temporal = always_enrich or bool(raw.temporal_quote)
        if temporal:
            reasons.append("temporal_enrichment_required")
        else:
            state.temporal_state = V6ClaimStateStatus.NOT_REQUIRED

        measurement = always_enrich or bool(raw.measurement_quote)
        if measurement:
            reasons.append("measurement_enrichment_required")
        else:
            state.measurement_state = V6ClaimStateStatus.NOT_REQUIRED

        assertion = always_enrich or state.assertion_state not in {
            V6ClaimStateStatus.VERIFIED,
            V6ClaimStateStatus.NOT_REQUIRED,
        }
        if assertion:
            reasons.append("assertion_verification_required")
        else:
            state.assertion_state = V6ClaimStateStatus.NOT_REQUIRED

        canonical = always_enrich or state.canonical_state == V6ClaimStateStatus.PENDING
        if canonical:
            reasons.append("canonical_link_required")

        return V6EnrichmentDecision(
            claim_id=raw.claim_id,
            reference=reference,
            participant=participant,
            temporal=temporal,
            measurement=measurement,
            assertion=assertion,
            canonical=canonical,
            reason_codes=tuple(reasons),
        )


class EnrichmentPlanner:
    """Aggregate structural requests before invoking any adapter."""

    def plan(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        decisions: dict[str, V6EnrichmentDecision],
        always_enrich: bool = False,
    ) -> EnrichmentPlan:
        requests: list[EnrichmentRequest] = []
        by_type: dict[V6EnrichmentType, list[EnrichmentRequest]] = {
            item: [] for item in V6EnrichmentType
        }
        for claim in claims:
            decision = decisions[claim.raw.claim_id]
            for enabled, enrichment_type, reason in (
                (
                    decision.reference,
                    V6EnrichmentType.REFERENCE,
                    "reference_resolution_required",
                ),
                (
                    decision.participant,
                    V6EnrichmentType.PARTICIPANT,
                    "participant_enrichment_required",
                ),
                (
                    decision.temporal,
                    V6EnrichmentType.TEMPORAL,
                    "temporal_enrichment_required",
                ),
                (
                    decision.measurement,
                    V6EnrichmentType.MEASUREMENT,
                    "measurement_enrichment_required",
                ),
                (
                    decision.assertion,
                    V6EnrichmentType.ASSERTION,
                    "assertion_verification_required",
                ),
                (
                    decision.canonical,
                    V6EnrichmentType.CANONICAL,
                    "canonical_link_required",
                ),
            ):
                if not enabled:
                    continue
                request = EnrichmentRequest(
                    request_id=f"req-{len(requests) + 1}-{enrichment_type.value}",
                    claim_id=claim.raw.claim_id,
                    enrichment_type=enrichment_type,
                    reason_code=reason,
                    required_for_projection=True,
                    priority=100
                    if enrichment_type == V6EnrichmentType.REFERENCE
                    else 50,
                )
                requests.append(request)
                by_type[enrichment_type].append(request)

        batches: list[EnrichmentBatch] = []
        for enrichment_type, items in by_type.items():
            if not items:
                continue
            for offset in range(0, len(items), _MAX_BATCH_SIZE):
                chunk = items[offset : offset + _MAX_BATCH_SIZE]
                batches.append(
                    EnrichmentBatch(
                        batch_id=(
                            f"batch-{len(batches) + 1}-"
                            f"{enrichment_type.value}-{offset // _MAX_BATCH_SIZE + 1}"
                        ),
                        enrichment_type=enrichment_type,
                        claim_ids=[item.claim_id for item in chunk],
                        execution_strategy=self._strategy(enrichment_type),
                        request_ids=[item.request_id for item in chunk],
                        batch_size=len(chunk),
                    )
                )
        return EnrichmentPlan(
            requests=requests,
            batches=batches,
            policy_version=V6_ENRICHMENT_PLANNER_VERSION,
        )

    @staticmethod
    def _strategy(
        enrichment_type: V6EnrichmentType,
    ) -> Literal["deterministic", "model_batch", "high_risk_singleton"]:
        if enrichment_type in {V6EnrichmentType.TEMPORAL, V6EnrichmentType.MEASUREMENT}:
            return "deterministic"
        if enrichment_type == V6EnrichmentType.CANONICAL:
            return "deterministic"
        return "model_batch"


def projection_readiness(state: V6ClaimStateVector) -> tuple[bool, list[str]]:
    """Return whether a claim is consumable and which dimensions are missing."""

    missing: list[str] = []
    for dimension in (
        "quote_state",
        "statement_state",
        "subject_state",
        "participant_state",
        "temporal_state",
        "measurement_state",
        "assertion_state",
        "canonical_state",
    ):
        if getattr(state, dimension) not in {
            V6ClaimStateStatus.VERIFIED,
            V6ClaimStateStatus.READY,
            V6ClaimStateStatus.NOT_REQUIRED,
        }:
            missing.append(dimension)
    return not missing, missing
