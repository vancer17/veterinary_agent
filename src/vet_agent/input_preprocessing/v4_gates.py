"""Synchronous V4 gates for quote-anchored flat observations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .v4_contracts import (
    GovernedFlatObservation,
    V4CanonicalMappingStatus,
    V4EntityType,
    V4InputAnalysisResult,
    V4QualityGateAction,
    V4QualityGateResult,
    V4QualityGateStatus,
    V4ResolutionStatus,
    V4SemanticClass,
    V4TurnContext,
)

_ACTION_AGENT_TYPES = {
    V4EntityType.USER,
    V4EntityType.CAREGIVER,
    V4EntityType.MEDICAL_ACTOR,
}
_PET_TYPES = {V4EntityType.CURRENT_PET, V4EntityType.OTHER_PET}
_ACTION_OBJECT_TYPES = {
    V4EntityType.FOOD,
    V4EntityType.MEDICATION,
    V4EntityType.ENVIRONMENT,
    V4EntityType.SAMPLE,
    V4EntityType.UNKNOWN,
}


def evaluate_v4_quality_gates(
    *,
    result: V4InputAnalysisResult,
) -> list[V4QualityGateResult]:
    """Evaluate structural V4 gates without applying medical text rules."""

    return [
        _turn_context_gate(result.turn_context),
        _flat_schema_gate(result),
        _quote_gate(result.observations),
        _subject_participant_gate(result.turn_context, result.observations),
        _canonical_gate(result.observations),
        _type_compatibility_gate(result.observations),
        _assertion_consistency_gate(result.observations),
        _duplicate_gate(result.observations),
        _suspicious_empty_gate(result),
        _projection_boundary_gate(),
    ]


def _turn_context_gate(turn_context: V4TurnContext) -> V4QualityGateResult:
    references = turn_context.entity_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.entity_type != V4EntityType.CURRENT_PET:
        errors.append("current_pet_entity_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_entity_reference")
    if any(not item.trusted for item in references.values()):
        errors.append("untrusted_entity_reference")
    return _gate(
        gate_id="v4_turn_context",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        metadata={"errors": errors, "entity_count": len(references)},
    )


def _flat_schema_gate(result: V4InputAnalysisResult) -> V4QualityGateResult:
    errors: list[str] = []
    raw_ids = [item.observation_id for item in result.raw_observations]
    governed_ids = [item.observation_id for item in result.observations]
    if len(raw_ids) != len(set(raw_ids)):
        errors.append("duplicate_raw_observation_id")
    if len(governed_ids) != len(set(governed_ids)):
        errors.append("duplicate_governed_observation_id")
    if set(raw_ids) != set(governed_ids):
        errors.append("raw_governed_observation_id_mismatch")
    if any(item.source_id != "current_turn" for item in result.raw_observations):
        errors.append("invalid_source_id")
    if result.intent.answer_now and any(
        item.semantic_class == V4SemanticClass.CONTROL_INTENT
        for item in result.raw_observations
    ):
        errors.append("control_intent_mixed_into_fact_observation")
    return _gate(
        gate_id="v4_flat_schema",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="flat_schema_invalid" if errors else "flat_schema_valid",
        stage="flat_schema",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        metadata={"errors": errors, "observation_count": len(raw_ids)},
    )


def _quote_gate(observations: list[GovernedFlatObservation]) -> V4QualityGateResult:
    errors: list[str] = []
    for observation in observations:
        owner = observation.observation_id
        if observation.evidence_quote.status != "resolved":
            errors.append(f"evidence_quote_{observation.evidence_quote.status}:{owner}")
        if observation.target_quote.status != "resolved":
            errors.append(f"target_quote_{observation.target_quote.status}:{owner}")
        if observation.temporal_quote is not None:
            if observation.temporal_quote.status != "resolved":
                errors.append(
                    f"temporal_quote_{observation.temporal_quote.status}:{owner}"
                )
        elif observation.temporal_status.value == "confirmed_present":
            errors.append(f"temporal_status_without_quote:{owner}")
        if observation.measurement_quote is not None:
            if observation.measurement_quote.status != "resolved":
                errors.append(
                    f"measurement_quote_{observation.measurement_quote.status}:{owner}"
                )
        elif observation.measurement_status.value == "confirmed_present":
            errors.append(f"measurement_status_without_quote:{owner}")
    return _gate(
        gate_id="v4_quote_anchor",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="quote_anchor_failed" if errors else "quote_anchor_valid",
        stage="quote_governance",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"errors": errors},
    )


def _subject_participant_gate(
    turn_context: V4TurnContext,
    observations: list[GovernedFlatObservation],
) -> V4QualityGateResult:
    trusted = turn_context.entity_references()
    errors: list[str] = []
    for observation in observations:
        owner = observation.observation_id
        errors.extend(
            _validate_binding(
                trusted=trusted,
                owner=owner,
                role="subject",
                binding=observation.subject,
            )
        )
        for participant in observation.participants:
            errors.extend(
                _validate_binding(
                    trusted=trusted,
                    owner=owner,
                    role=participant.role,
                    binding=participant.entity,
                )
            )
    return _gate(
        gate_id="v4_subject_participant",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="subject_or_participant_invalid"
        if errors
        else "subject_and_participant_valid",
        stage="subject_governance",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"errors": errors},
    )


def _canonical_gate(observations: list[GovernedFlatObservation]) -> V4QualityGateResult:
    errors: list[str] = []
    for observation in observations:
        owner = observation.observation_id
        selected = next(
            (
                item
                for item in observation.candidate_set.candidates
                if item.candidate_id == observation.selected_candidate_id
            ),
            None,
        )
        if observation.selected_candidate_id is not None and selected is None:
            errors.append(f"selected_candidate_not_in_set:{owner}")
        if (
            observation.mapping_status == V4CanonicalMappingStatus.CONFIRMED
            and (
                selected is None
                or not observation.candidate_set.candidates
                or observation.canonical_id != selected.canonical_id
            )
        ):
            errors.append(f"confirmed_without_valid_candidate:{owner}")
        if (
            not observation.candidate_set.candidates
            and observation.mapping_status == V4CanonicalMappingStatus.CONFIRMED
        ):
            errors.append(f"confirmed_without_candidates:{owner}")
        if observation.canonical_id is not None and selected is None:
            errors.append(f"canonical_without_selected_candidate:{owner}")
        if (
            observation.mapping_status
            in {
                V4CanonicalMappingStatus.NOT_FOUND,
                V4CanonicalMappingStatus.UNMAPPED_MENTION,
                V4CanonicalMappingStatus.AMBIGUOUS,
                V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            and not observation.review_required
        ):
            errors.append(f"unmapped_review_missing:{owner}")
    unmapped = sum(
        observation.mapping_status
        in {
            V4CanonicalMappingStatus.NOT_FOUND,
            V4CanonicalMappingStatus.UNMAPPED_MENTION,
            V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
        }
        for observation in observations
    )
    return _gate(
        gate_id="v4_canonical_registry",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.NEEDS_REVIEW,
        reason_code="canonical_candidate_invalid"
        if errors
        else "canonical_candidates_audited",
        stage="canonical_linking",
        severity="blocking" if errors else "observability_only",
        action=V4QualityGateAction.FAIL_TURN
        if errors
        else V4QualityGateAction.ROUTE_TO_REVIEW,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"errors": errors, "unmapped_count": unmapped},
        review_required=True,
    )


def _type_compatibility_gate(
    observations: list[GovernedFlatObservation],
) -> V4QualityGateResult:
    errors: list[str] = []
    for observation in observations:
        owner = observation.observation_id
        selected = next(
            (
                item
                for item in observation.candidate_set.candidates
                if item.candidate_id == observation.selected_candidate_id
            ),
            None,
        )
        if selected is not None and selected.semantic_class != observation.semantic_class:
            errors.append(f"semantic_class_mismatch:{owner}")
        if observation.semantic_class in {V4SemanticClass.STATE, V4SemanticClass.EVENT}:
            if observation.subject.entity_type not in _PET_TYPES:
                errors.append(f"state_subject_not_pet:{owner}")
            experiencer = _participant(
                observation,
                "experiencer",
            )
            if experiencer is not None and experiencer.entity_type not in _PET_TYPES:
                errors.append(f"experiencer_not_pet:{owner}")
        if observation.semantic_class == V4SemanticClass.ACTION:
            agent = _participant(observation, "action_agent")
            recipient = _participant(observation, "action_recipient")
            if agent is None or agent.entity_type not in _ACTION_AGENT_TYPES:
                errors.append(f"action_agent_type_invalid:{owner}")
            if recipient is None or recipient.entity_type not in _PET_TYPES:
                errors.append(f"action_recipient_type_invalid:{owner}")
            if not observation.object_mention:
                errors.append(f"action_object_missing:{owner}")
        if (
            observation.semantic_class == V4SemanticClass.MEASUREMENT
            and observation.measurement_quote is None
        ):
            errors.append(f"measurement_without_quote:{owner}")
    return _gate(
        gate_id="v4_type_compatibility",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="coarse_type_compatibility_failed"
        if errors
        else "coarse_type_compatibility_valid",
        stage="canonical_type",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"errors": errors},
    )


def _assertion_consistency_gate(
    observations: list[GovernedFlatObservation],
) -> V4QualityGateResult:
    errors: list[str] = []
    for observation in observations:
        if observation.assertion.value == "unknown":
            errors.append(f"assertion_unknown:{observation.observation_id}")
        if observation.certainty.value == "unknown":
            errors.append(f"certainty_unknown:{observation.observation_id}")
    grouped: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    for observation in observations:
        grouped[
            (
                observation.target_quote.normalized_quote,
                observation.subject.reference_id,
            )
        ].add(observation.assertion.value)
    for key, values in grouped.items():
        current_fact_values = values & {
            "present",
            "absent",
            "denied",
            "denied_abnormal",
            "normal",
            "abnormal",
            "resolved",
        }
        if len(current_fact_values) > 1:
            errors.append(f"assertion_conflict:{key[0]}:{sorted(values)}")
    return _gate(
        gate_id="v4_assertion_consistency",
        status=V4QualityGateStatus.FAILED if errors else V4QualityGateStatus.PASSED,
        reason_code="assertion_consistency_failed"
        if errors
        else "assertion_consistency_valid",
        stage="assertion",
        severity="blocking",
        action=V4QualityGateAction.FAIL_TURN if errors else V4QualityGateAction.PASS,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"errors": errors},
    )


def _duplicate_gate(observations: list[GovernedFlatObservation]) -> V4QualityGateResult:
    grouped: dict[tuple[str, str, str | None], int] = defaultdict(int)
    for observation in observations:
        grouped[
            (
                observation.evidence_quote.normalized_quote,
                observation.target_quote.normalized_quote,
                observation.subject.reference_id,
            )
        ] += 1
    duplicates = [key for key, count in grouped.items() if count > 1]
    return _gate(
        gate_id="v4_duplicate_observation",
        status=V4QualityGateStatus.WARNING if duplicates else V4QualityGateStatus.PASSED,
        reason_code="duplicate_observations"
        if duplicates
        else "observations_unique",
        stage="semantic_quality",
        severity="observability_only",
        action=V4QualityGateAction.ROUTE_TO_REVIEW,
        evidence_refs=[item.observation_id for item in observations],
        metadata={"duplicates": [list(item) for item in duplicates]},
        review_required=bool(duplicates),
    )


def _suspicious_empty_gate(result: V4InputAnalysisResult) -> V4QualityGateResult:
    failed = result.profile.has_factual_statements and not result.observations
    return _gate(
        gate_id="v4_suspicious_empty",
        status=V4QualityGateStatus.FAILED if failed else V4QualityGateStatus.PASSED,
        reason_code="flat_extraction_suspicious_empty"
        if failed
        else "semantic_coverage_observed",
        stage="semantic_quality",
        severity="blocking",
        action=V4QualityGateAction.ROUTE_TO_REVIEW
        if failed
        else V4QualityGateAction.PASS,
        metadata={
            "profile_has_factual_statements": result.profile.has_factual_statements,
            "observation_count": len(result.observations),
        },
        review_required=failed,
    )


def _projection_boundary_gate() -> V4QualityGateResult:
    return _gate(
        gate_id="v4_projection_boundary",
        status=V4QualityGateStatus.PASSED,
        reason_code="report_only_projection_boundary_valid",
        stage="projection",
        severity="observability_only",
        action=V4QualityGateAction.PASS,
        metadata={
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
        },
    )


def _validate_binding(
    *,
    trusted: dict[str, Any],
    owner: str,
    role: str,
    binding: Any,
) -> list[str]:
    errors: list[str] = []
    if binding.resolution_status == V4ResolutionStatus.RESOLVED:
        reference = trusted.get(binding.reference_id or "")
        if reference is None:
            errors.append(f"entity_not_in_turn_context:{owner}:{role}")
        elif reference.entity_type != binding.entity_type:
            errors.append(f"entity_type_mismatch:{owner}:{role}")
    elif binding.resolution_status == V4ResolutionStatus.AMBIGUOUS:
        if len(binding.subject_candidates) < 2:
            errors.append(f"ambiguous_candidates_missing:{owner}:{role}")
        if any(candidate not in trusted for candidate in binding.subject_candidates):
            errors.append(f"ambiguous_candidate_not_trusted:{owner}:{role}")
    elif role == "subject":
        errors.append(f"subject_missing:{owner}")
    return errors


def _participant(
    observation: GovernedFlatObservation,
    role: str,
) -> Any | None:
    return next(
        (
            participant.entity
            for participant in observation.participants
            if participant.role == role
        ),
        None,
    )


def _gate(
    *,
    gate_id: str,
    status: V4QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: V4QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> V4QualityGateResult:
    return V4QualityGateResult(
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
