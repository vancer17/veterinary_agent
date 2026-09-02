"""Synchronous quality gates for V5 thin claims and enrichments."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .v5_contracts import (
    GovernedThinUserClaim,
    V5CanonicalMappingStatus,
    V5ClaimStateStatus,
    V5EntityType,
    V5InputAnalysisResult,
    V5QualityGateAction,
    V5QualityGateResult,
    V5QualityGateStatus,
    V5ResolutionStatus,
    V5UserStatementType,
)

_ACTION_AGENT_TYPES = {
    V5EntityType.USER,
    V5EntityType.CAREGIVER,
    V5EntityType.MEDICAL_ACTOR,
}
_PET_TYPES = {V5EntityType.CURRENT_PET, V5EntityType.OTHER_PET}


def evaluate_v5_quality_gates(
    *,
    result: V5InputAnalysisResult,
) -> list[V5QualityGateResult]:
    """Evaluate structural gates without medical text rules."""

    return [
        _turn_context_gate(result.turn_context),
        _thin_schema_gate(result),
        _quote_gate(result.claims),
        _statement_gate(result),
        _subject_participant_gate(result.claims),
        _enrichment_gate(result.claims),
        _canonical_gate(result.claims),
        _duplicate_gate(result.claims),
        _suspicious_empty_gate(result),
        _projection_boundary_gate(result),
    ]


def _turn_context_gate(turn_context: Any) -> V5QualityGateResult:
    references = turn_context.entity_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.entity_type != V5EntityType.CURRENT_PET:
        errors.append("current_pet_entity_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_entity_reference")
    if any(not item.trusted for item in references.values()):
        errors.append("untrusted_entity_reference")
    return _gate(
        gate_id="v5_turn_context",
        status=(V5QualityGateStatus.FAILED if errors else V5QualityGateStatus.PASSED),
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=(V5QualityGateAction.FAIL_TURN if errors else V5QualityGateAction.PASS),
        metadata={"errors": errors, "entity_count": len(references)},
    )


def _thin_schema_gate(result: V5InputAnalysisResult) -> V5QualityGateResult:
    errors: list[str] = []
    raw_ids = [item.claim_id for item in result.raw_claims]
    governed_ids = [item.raw.claim_id for item in result.claims]
    if len(raw_ids) != len(set(raw_ids)):
        errors.append("duplicate_raw_claim_id")
    if len(governed_ids) != len(set(governed_ids)):
        errors.append("duplicate_governed_claim_id")
    if set(raw_ids) != set(governed_ids):
        errors.append("raw_governed_claim_id_mismatch")
    if any(item.source_id != "current_turn" for item in result.raw_claims):
        errors.append("invalid_source_id")
    if any(not item.source_block_id for item in result.raw_claims):
        errors.append("invalid_source_block_id")
    return _gate(
        gate_id="v5_thin_schema",
        status=(V5QualityGateStatus.FAILED if errors else V5QualityGateStatus.PASSED),
        reason_code="thin_schema_invalid" if errors else "thin_schema_valid",
        stage="thin_schema",
        severity="blocking",
        action=(V5QualityGateAction.FAIL_TURN if errors else V5QualityGateAction.PASS),
        metadata={"errors": errors, "claim_count": len(raw_ids)},
    )


def _quote_gate(claims: list[GovernedThinUserClaim]) -> V5QualityGateResult:
    errors: list[str] = []
    for claim in claims:
        owner = claim.raw.claim_id
        if claim.evidence_quote.status != "resolved":
            errors.append(f"evidence_quote_{claim.evidence_quote.status}:{owner}")
        if claim.target_quote.status != "resolved":
            errors.append(f"target_quote_{claim.target_quote.status}:{owner}")
        if (
            claim.temporal_quote is not None
            and claim.temporal_quote.status != "resolved"
        ):
            errors.append(f"temporal_quote_{claim.temporal_quote.status}:{owner}")
        if (
            claim.measurement_quote is not None
            and claim.measurement_quote.status != "resolved"
        ):
            errors.append(f"measurement_quote_{claim.measurement_quote.status}:{owner}")
    return _gate(
        gate_id="v5_quote_anchor",
        status=(V5QualityGateStatus.FAILED if errors else V5QualityGateStatus.PASSED),
        reason_code="quote_anchor_failed" if errors else "quote_anchors_valid",
        stage="quote",
        severity="major",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW if errors else V5QualityGateAction.PASS
        ),
        evidence_refs=[claim.raw.claim_id for claim in claims],
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _statement_gate(result: V5InputAnalysisResult) -> V5QualityGateResult:
    errors: list[str] = []
    for claim in result.claims:
        owner = claim.raw.claim_id
        if claim.state.statement_state == V5ClaimStateStatus.BLOCKED:
            errors.append(f"statement_state_invalid:{owner}")
        if (
            result.intent.answer_now
            and claim.raw.user_statement_type == V5UserStatementType.ASKS
            # An answer-now request may coexist with a factual question; only
            # report mixing when no evidence-backed target is present.
            and claim.raw.target_quote == claim.raw.evidence_quote
        ):
            errors.append(f"control_intent_possible_fact_claim_mixing:{owner}")
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for claim in result.claims:
        grouped[
            (
                claim.raw.evidence_quote,
                claim.raw.target_quote,
            )
        ].add(claim.raw.user_statement_type.value)
    for key, values in grouped.items():
        contradictory = {
            V5UserStatementType.DENIES.value,
            V5UserStatementType.REPORTS.value,
            V5UserStatementType.REPORTS_ABNORMAL.value,
        }
        if len(values & contradictory) > 1:
            errors.append(f"statement_conflict:{key[1]}:{sorted(values)}")
    return _gate(
        gate_id="v5_statement",
        status=(V5QualityGateStatus.FAILED if errors else V5QualityGateStatus.PASSED),
        reason_code="statement_gate_failed" if errors else "statements_valid",
        stage="statement",
        severity="major",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW if errors else V5QualityGateAction.PASS
        ),
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _subject_participant_gate(
    claims: list[GovernedThinUserClaim],
) -> V5QualityGateResult:
    errors: list[str] = []
    for claim in claims:
        owner = claim.raw.claim_id
        if claim.subject is not None:
            subject = claim.subject.subject
            if subject.resolution_status == V5ResolutionStatus.RESOLVED:
                if not subject.reference_id:
                    errors.append(f"resolved_subject_missing_reference:{owner}")
            elif subject.resolution_status == V5ResolutionStatus.AMBIGUOUS:
                if len(subject.subject_candidates) < 2:
                    errors.append(f"ambiguous_subject_candidates_missing:{owner}")
            elif claim.state.subject_state not in {
                V5ClaimStateStatus.PENDING,
                V5ClaimStateStatus.NOT_REQUIRED,
            }:
                errors.append(f"subject_unresolved:{owner}")
        if claim.participants is not None:
            for participant in claim.participants.participants:
                entity = participant.entity
                if participant.role == "action_agent":
                    if entity.resolution_status == V5ResolutionStatus.RESOLVED and (
                        entity.entity_type not in _ACTION_AGENT_TYPES
                    ):
                        errors.append(f"action_agent_type_mismatch:{owner}")
                elif (
                    participant.role in {"action_recipient", "experiencer"}
                    and entity.resolution_status == V5ResolutionStatus.RESOLVED
                    and entity.entity_type not in _PET_TYPES
                ):
                    errors.append(f"pet_participant_type_mismatch:{owner}")
    return _gate(
        gate_id="v5_subject_participant",
        status=(V5QualityGateStatus.FAILED if errors else V5QualityGateStatus.PASSED),
        reason_code="subject_participant_failed"
        if errors
        else "subjects_participants_valid",
        stage="subject_participant",
        severity="major",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW if errors else V5QualityGateAction.PASS
        ),
        evidence_refs=[claim.raw.claim_id for claim in claims],
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _enrichment_gate(claims: list[GovernedThinUserClaim]) -> V5QualityGateResult:
    errors: list[str] = []
    review_count = 0
    for claim in claims:
        owner = claim.raw.claim_id
        for enrichment in (
            claim.subject,
            claim.participants,
            claim.temporal,
            claim.measurement,
            claim.assertion,
        ):
            if enrichment is None:
                continue
            if getattr(enrichment, "review_required", False):
                review_count += 1
            if getattr(enrichment, "status", None) == V5ClaimStateStatus.BLOCKED:
                errors.append(f"enrichment_blocked:{owner}")
    return _gate(
        gate_id="v5_enrichment",
        status=(
            V5QualityGateStatus.NEEDS_REVIEW
            if review_count
            else V5QualityGateStatus.FAILED
            if errors
            else V5QualityGateStatus.PASSED
        ),
        reason_code="enrichment_review_required"
        if review_count
        else "enrichment_failed"
        if errors
        else "enrichments_valid",
        stage="enrichment",
        severity="major",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW
            if review_count or errors
            else V5QualityGateAction.PASS
        ),
        metadata={"errors": errors, "review_count": review_count},
        review_required=bool(review_count or errors),
    )


def _canonical_gate(claims: list[GovernedThinUserClaim]) -> V5QualityGateResult:
    errors: list[str] = []
    review_count = 0
    for claim in claims:
        owner = claim.raw.claim_id
        canonical = claim.canonical
        if canonical is None:
            if claim.state.canonical_state == V5ClaimStateStatus.READY:
                errors.append(f"canonical_ready_without_mapping:{owner}")
            continue
        if canonical.mapping_status == V5CanonicalMappingStatus.CONFIRMED:
            if not canonical.candidate_set.candidates:
                errors.append(f"confirmed_without_candidates:{owner}")
            selected = next(
                (
                    candidate
                    for candidate in canonical.candidate_set.candidates
                    if candidate.candidate_id == canonical.selected_candidate_id
                ),
                None,
            )
            if selected is None or selected.canonical_id != canonical.canonical_id:
                errors.append(f"selected_candidate_invalid:{owner}")
        else:
            review_count += 1
            if canonical.canonical_id is not None:
                errors.append(f"unresolved_mapping_with_canonical_id:{owner}")
            if not canonical.review_required:
                errors.append(f"unmapped_review_missing:{owner}")
    return _gate(
        gate_id="v5_canonical_registry",
        status=(
            V5QualityGateStatus.NEEDS_REVIEW
            if review_count
            else V5QualityGateStatus.FAILED
            if errors
            else V5QualityGateStatus.PASSED
        ),
        reason_code="canonical_review_required"
        if review_count
        else "canonical_gate_failed"
        if errors
        else "canonical_mappings_valid",
        stage="canonical",
        severity="major",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW
            if review_count or errors
            else V5QualityGateAction.PASS
        ),
        metadata={"errors": errors, "review_count": review_count},
        review_required=bool(review_count or errors),
    )


def _duplicate_gate(claims: list[GovernedThinUserClaim]) -> V5QualityGateResult:
    grouped: dict[tuple[str, str, str], int] = defaultdict(int)
    for claim in claims:
        grouped[
            (
                claim.evidence_quote.normalized_quote,
                claim.target_quote.normalized_quote,
                claim.raw.user_statement_type.value,
            )
        ] += 1
    duplicates = [key for key, count in grouped.items() if count > 1]
    return _gate(
        gate_id="v5_duplicate_claim",
        status=(
            V5QualityGateStatus.WARNING if duplicates else V5QualityGateStatus.PASSED
        ),
        reason_code="duplicate_claims" if duplicates else "claims_unique",
        stage="dedup",
        severity="observability_only",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW
            if duplicates
            else V5QualityGateAction.PASS
        ),
        metadata={"duplicates": [list(item) for item in duplicates]},
        review_required=bool(duplicates),
    )


def _suspicious_empty_gate(result: V5InputAnalysisResult) -> V5QualityGateResult:
    failed = result.intent.fact_path_required and not result.claims
    return _gate(
        gate_id="v5_suspicious_empty",
        status=(V5QualityGateStatus.FAILED if failed else V5QualityGateStatus.PASSED),
        reason_code="thin_extraction_suspicious_empty"
        if failed
        else "claim_coverage_observed",
        stage="coverage",
        severity="blocking",
        action=(
            V5QualityGateAction.ROUTE_TO_REVIEW if failed else V5QualityGateAction.PASS
        ),
        metadata={
            "fact_path_required": result.intent.fact_path_required,
            "claim_count": len(result.claims),
        },
        review_required=failed,
    )


def _projection_boundary_gate(result: V5InputAnalysisResult) -> V5QualityGateResult:
    blocked_consumed = [
        claim.raw.claim_id
        for claim in result.claims
        if claim.state.projection_state == V5ClaimStateStatus.BLOCKED
    ]
    return _gate(
        gate_id="v5_projection_boundary",
        status=(
            V5QualityGateStatus.FAILED
            if blocked_consumed
            else V5QualityGateStatus.PASSED
        ),
        reason_code="blocked_claim_consumed"
        if blocked_consumed
        else "report_only_projection_boundary_valid",
        stage="projection",
        severity="blocking",
        action=(
            V5QualityGateAction.FAIL_TURN
            if blocked_consumed
            else V5QualityGateAction.PASS
        ),
        evidence_refs=blocked_consumed,
        metadata={
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
        },
    )


def _gate(
    *,
    gate_id: str,
    status: V5QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: V5QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> V5QualityGateResult:
    return V5QualityGateResult(
        gate_id=gate_id,
        status=status,
        severity=severity,  # type: ignore[arg-type]
        reason_code=reason_code,
        stage=stage,
        evidence_refs=evidence_refs or [],
        metadata=metadata or {},
        action=action,
        review_required=review_required,
    )
