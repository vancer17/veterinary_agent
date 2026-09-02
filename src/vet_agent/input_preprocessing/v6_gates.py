"""Synchronous quality gates for V6 thin claims and batch enrichment."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .v6_contracts import (
    GovernedThinUserClaim,
    V6CanonicalMappingStatus,
    V6ClaimStateStatus,
    V6EntityType,
    V6InputAnalysisResult,
    V6QualityGateAction,
    V6QualityGateResult,
    V6QualityGateStatus,
    V6ResolutionStatus,
)

_ACTION_AGENT_TYPES = {
    V6EntityType.USER,
    V6EntityType.CAREGIVER,
    V6EntityType.MEDICAL_ACTOR,
}
_PET_TYPES = {V6EntityType.CURRENT_PET, V6EntityType.OTHER_PET}


def evaluate_v6_quality_gates(
    *,
    result: V6InputAnalysisResult,
) -> list[V6QualityGateResult]:
    """Evaluate structural gates without medical text rules."""

    return [
        _turn_context_gate(result.turn_context),
        _intent_gate(result),
        _thin_schema_gate(result),
        _quote_gate(result.claims),
        _statement_gate(result),
        _subject_participant_gate(result.claims),
        _enrichment_gate(result),
        _canonical_gate(result.claims),
        _duplicate_gate(result.claims),
        _suspicious_empty_gate(result),
        _projection_boundary_gate(result),
    ]


def _turn_context_gate(turn_context: Any) -> V6QualityGateResult:
    references = turn_context.entity_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.entity_type != V6EntityType.CURRENT_PET:
        errors.append("current_pet_entity_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_entity_reference")
    if any(not item.trusted for item in references.values()):
        errors.append("untrusted_entity_reference")
    return _gate(
        gate_id="v6_turn_context",
        status=(V6QualityGateStatus.FAILED if errors else V6QualityGateStatus.PASSED),
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=(V6QualityGateAction.FAIL_TURN if errors else V6QualityGateAction.PASS),
        metadata={"errors": errors, "entity_count": len(references)},
    )


def _intent_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
    errors: list[str] = []
    intent = result.intent
    explicit_flags = (
        intent.answer_now,
        intent.wants_triage,
        intent.correction,
        intent.clarification_request,
    )
    quotes = {
        item.quote_type: item
        for item in result.intent_quote_anchors
        if item.status == "resolved"
    }
    expected_quote_types = [
        ("answer_now", intent.answer_now),
        ("wants_triage", intent.wants_triage),
        ("correction", intent.correction),
        ("clarification_request", intent.clarification_request),
    ]
    for quote_type, enabled in expected_quote_types:
        if enabled and quote_type not in quotes:
            errors.append(f"intent_quote_invalid:{quote_type}")
    if any(explicit_flags) and not result.intent_quote_anchors:
        errors.append("explicit_intent_quote_missing")
    if result.intent.fact_statement_present and not result.claims:
        # An input can explicitly mention facts but contain no consumable claim.
        errors.append("fact_statement_without_claim_requires_review")
    return _gate(
        gate_id="v6_intent_contract",
        status=(
            V6QualityGateStatus.FAILED
            if any(item.startswith("intent_quote_invalid") for item in errors)
            else V6QualityGateStatus.NEEDS_REVIEW
            if errors
            else V6QualityGateStatus.PASSED
        ),
        reason_code="intent_contract_invalid" if errors else "intent_contract_valid",
        stage="intent",
        severity="blocking",
        action=(
            V6QualityGateAction.FAIL_TURN
            if any(item.startswith("intent_quote_invalid") for item in errors)
            else V6QualityGateAction.ROUTE_TO_REVIEW
            if errors
            else V6QualityGateAction.PASS
        ),
        metadata={
            "errors": errors,
            "answer_now": intent.answer_now,
            "fact_statement_present": intent.fact_statement_present,
            "question_present": intent.question_present,
        },
        review_required=bool(errors),
    )


def _thin_schema_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
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
        gate_id="v6_thin_schema",
        status=(V6QualityGateStatus.FAILED if errors else V6QualityGateStatus.PASSED),
        reason_code="thin_schema_invalid" if errors else "thin_schema_valid",
        stage="thin_schema",
        severity="blocking",
        action=(V6QualityGateAction.FAIL_TURN if errors else V6QualityGateAction.PASS),
        metadata={"errors": errors, "claim_count": len(raw_ids)},
    )


def _quote_gate(claims: list[GovernedThinUserClaim]) -> V6QualityGateResult:
    errors: list[str] = []
    for claim in claims:
        owner = claim.raw.claim_id
        anchors = (
            claim.evidence_quote,
            claim.target_quote,
            claim.temporal_quote,
            claim.measurement_quote,
            claim.relation_quote,
            claim.subject_evidence_quote,
        )
        for anchor in anchors:
            if anchor is not None and anchor.status != "resolved":
                errors.append(f"{anchor.quote_type}_{anchor.status}:{owner}")
    return _gate(
        gate_id="v6_quote_anchor",
        status=(V6QualityGateStatus.FAILED if errors else V6QualityGateStatus.PASSED),
        reason_code="quote_anchor_failed" if errors else "quote_anchors_valid",
        stage="quote",
        severity="blocking",
        action=(V6QualityGateAction.FAIL_TURN if errors else V6QualityGateAction.PASS),
        evidence_refs=[claim.raw.claim_id for claim in claims],
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _statement_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
    errors: list[str] = []
    for claim in result.claims:
        owner = claim.raw.claim_id
        if claim.state.statement_state == V6ClaimStateStatus.BLOCKED:
            errors.append(f"statement_type_invalid:{owner}")
        if (
            claim.raw.relation.value == "no_change"
            and claim.raw.user_statement_type.value == "reports_normal"
        ):
            errors.append(f"no_change_as_normal:{owner}")
    return _gate(
        gate_id="v6_statement_relation",
        status=(V6QualityGateStatus.FAILED if errors else V6QualityGateStatus.PASSED),
        reason_code="statement_relation_failed"
        if errors
        else "statement_relation_valid",
        stage="statement",
        severity="blocking",
        action=(V6QualityGateAction.FAIL_TURN if errors else V6QualityGateAction.PASS),
        metadata={"errors": errors},
    )


def _subject_participant_gate(
    claims: list[GovernedThinUserClaim],
) -> V6QualityGateResult:
    errors: list[str] = []
    review_count = 0
    for claim in claims:
        owner = claim.raw.claim_id
        subject = claim.subject
        if subject is not None:
            if subject.status == V6ClaimStateStatus.BLOCKED:
                errors.append(f"subject_invalid:{owner}")
            elif subject.status != V6ClaimStateStatus.READY:
                review_count += 1
            if subject.subject.resolution_status == V6ResolutionStatus.AMBIGUOUS:
                pet_candidates = [item for item in subject.subject.subject_candidates]
                if len(pet_candidates) < 2:
                    errors.append(f"ambiguous_subject_missing_candidates:{owner}")
        participants = claim.participants
        if participants is not None:
            for participant in participants.participants:
                entity = participant.entity
                if participant.role == "action_agent":
                    if entity.entity_type not in _ACTION_AGENT_TYPES:
                        errors.append(f"action_agent_type_invalid:{owner}")
                elif (
                    participant.role in {"action_recipient", "experiencer"}
                    and entity.entity_type not in _PET_TYPES
                ):
                    errors.append(f"pet_participant_type_invalid:{owner}")
    return _gate(
        gate_id="v6_subject_participant",
        status=(
            V6QualityGateStatus.FAILED
            if errors
            else V6QualityGateStatus.NEEDS_REVIEW
            if review_count
            else V6QualityGateStatus.PASSED
        ),
        reason_code="subject_participant_failed"
        if errors
        else "subject_review_required"
        if review_count
        else "subject_participants_valid",
        stage="subject_participant",
        severity="blocking",
        action=(
            V6QualityGateAction.FAIL_TURN
            if errors
            else V6QualityGateAction.ROUTE_TO_REVIEW
            if review_count
            else V6QualityGateAction.PASS
        ),
        metadata={"errors": errors, "review_count": review_count},
        review_required=bool(errors or review_count),
    )


def _enrichment_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
    errors: list[str] = []
    plan = result.enrichment_plan
    if plan is None:
        errors.append("enrichment_plan_missing")
    else:
        request_ids = [item.request_id for item in plan.requests]
        if len(request_ids) != len(set(request_ids)):
            errors.append("duplicate_request_id")
        for batch in plan.batches:
            expected_requests = {
                item.request_id
                for item in plan.requests
                if item.enrichment_type == batch.enrichment_type
                and item.claim_id in set(batch.claim_ids)
            }
            if expected_requests != set(batch.request_ids):
                errors.append(f"batch_request_coverage_mismatch:{batch.batch_id}")
    for claim in result.claims:
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
            if getattr(enrichment, "claim_id", None) != owner:
                errors.append(f"enrichment_cross_claim_assignment:{owner}")
    return _gate(
        gate_id="v6_enrichment",
        status=(V6QualityGateStatus.FAILED if errors else V6QualityGateStatus.PASSED),
        reason_code="enrichment_gate_failed" if errors else "enrichment_batches_valid",
        stage="enrichment",
        severity="blocking",
        action=(V6QualityGateAction.FAIL_TURN if errors else V6QualityGateAction.PASS),
        metadata={"errors": errors},
    )


def _canonical_gate(claims: list[GovernedThinUserClaim]) -> V6QualityGateResult:
    errors: list[str] = []
    review_count = 0
    for claim in claims:
        owner = claim.raw.claim_id
        canonical = claim.canonical
        if canonical is None:
            continue
        if canonical.mapping_status == V6CanonicalMappingStatus.CONFIRMED:
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
            if canonical.diagnostic.value == "not_applicable":
                errors.append(f"canonical_not_found_without_diagnostic:{owner}")
    return _gate(
        gate_id="v6_canonical_registry",
        status=(
            V6QualityGateStatus.FAILED
            if errors
            else V6QualityGateStatus.NEEDS_REVIEW
            if review_count
            else V6QualityGateStatus.PASSED
        ),
        reason_code="canonical_gate_failed"
        if errors
        else "canonical_review_required"
        if review_count
        else "canonical_mappings_valid",
        stage="canonical",
        severity="blocking",
        action=(
            V6QualityGateAction.FAIL_TURN
            if errors
            else V6QualityGateAction.ROUTE_TO_REVIEW
            if review_count
            else V6QualityGateAction.PASS
        ),
        metadata={"errors": errors, "review_count": review_count},
        review_required=bool(errors or review_count),
    )


def _duplicate_gate(claims: list[GovernedThinUserClaim]) -> V6QualityGateResult:
    grouped: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for claim in claims:
        grouped[
            (
                claim.evidence_quote.normalized_quote,
                claim.target_quote.normalized_quote,
                claim.raw.user_statement_type.value,
                claim.raw.relation.value,
            )
        ] += 1
    duplicates = [key for key, count in grouped.items() if count > 1]
    return _gate(
        gate_id="v6_duplicate_claim",
        status=(
            V6QualityGateStatus.WARNING if duplicates else V6QualityGateStatus.PASSED
        ),
        reason_code="duplicate_claims" if duplicates else "claims_unique",
        stage="dedup",
        severity="observability_only",
        action=(
            V6QualityGateAction.ROUTE_TO_REVIEW
            if duplicates
            else V6QualityGateAction.PASS
        ),
        metadata={"duplicates": [list(item) for item in duplicates]},
        review_required=bool(duplicates),
    )


def _suspicious_empty_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
    failed = result.intent.fact_statement_present and not result.claims
    return _gate(
        gate_id="v6_suspicious_empty",
        status=(V6QualityGateStatus.FAILED if failed else V6QualityGateStatus.PASSED),
        reason_code="thin_extraction_suspicious_empty"
        if failed
        else "claim_coverage_observed",
        stage="coverage",
        severity="blocking",
        action=(
            V6QualityGateAction.ROUTE_TO_REVIEW if failed else V6QualityGateAction.PASS
        ),
        metadata={
            "fact_statement_present": result.intent.fact_statement_present,
            "claim_count": len(result.claims),
        },
        review_required=failed,
    )


def _projection_boundary_gate(result: V6InputAnalysisResult) -> V6QualityGateResult:
    blocked_consumed = [
        claim.raw.claim_id
        for claim in result.claims
        if claim.state.projection_state == V6ClaimStateStatus.BLOCKED
    ]
    return _gate(
        gate_id="v6_projection_boundary",
        status=(
            V6QualityGateStatus.FAILED
            if blocked_consumed
            else V6QualityGateStatus.PASSED
        ),
        reason_code="blocked_claim_consumed"
        if blocked_consumed
        else "report_only_projection_boundary_valid",
        stage="projection",
        severity="blocking",
        action=(
            V6QualityGateAction.FAIL_TURN
            if blocked_consumed
            else V6QualityGateAction.PASS
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
    status: V6QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: V6QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> V6QualityGateResult:
    return V6QualityGateResult(
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
