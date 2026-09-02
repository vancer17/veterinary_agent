"""Synchronous V3 architecture gates for the third shadow validation round."""

from __future__ import annotations

from typing import Any

from .v3_contracts import (
    V3AssertionVerification,
    V3AtomicClaimSegment,
    V3CandidateSet,
    V3CanonicalMappingStatus,
    V3EntityType,
    V3ParticipantRole,
    V3ParticipantVerification,
    V3QualityGateAction,
    V3QualityGateResult,
    V3QualityGateStatus,
    V3ResolutionStatus,
    V3SharedAssertionScopeSegment,
    V3Stage1Output,
    V3Stage2Output,
    V3TurnContext,
)
from .v3_stage1_assembler import V3ItemContext, iter_v3_items
from .vocabulary import CanonicalVocabulary

_ROLE_ENTITY_TYPES: dict[V3ParticipantRole, set[V3EntityType]] = {
    V3ParticipantRole.ACTION_AGENT: {
        V3EntityType.USER,
        V3EntityType.CAREGIVER,
        V3EntityType.MEDICAL_ACTOR,
    },
    V3ParticipantRole.ACTION_RECIPIENT: {
        V3EntityType.CURRENT_PET,
        V3EntityType.OTHER_PET,
    },
    V3ParticipantRole.EXPERIENCER: {
        V3EntityType.CURRENT_PET,
        V3EntityType.OTHER_PET,
    },
    V3ParticipantRole.ACTION_OBJECT: {
        V3EntityType.FOOD,
        V3EntityType.MEDICATION,
        V3EntityType.ENVIRONMENT,
        V3EntityType.SAMPLE,
        V3EntityType.UNKNOWN,
    },
    V3ParticipantRole.SOURCE: {
        V3EntityType.USER,
        V3EntityType.CAREGIVER,
        V3EntityType.MEDICAL_ACTOR,
        V3EntityType.ENVIRONMENT,
    },
    V3ParticipantRole.LOCATION: {V3EntityType.ENVIRONMENT},
    V3ParticipantRole.INSTRUMENT: {V3EntityType.ENVIRONMENT, V3EntityType.SAMPLE},
    V3ParticipantRole.GOAL: {
        V3EntityType.FOOD,
        V3EntityType.MEDICATION,
        V3EntityType.ENVIRONMENT,
        V3EntityType.SAMPLE,
        V3EntityType.UNKNOWN,
    },
    V3ParticipantRole.CAUSE: {
        V3EntityType.ENVIRONMENT,
        V3EntityType.FOOD,
        V3EntityType.SAMPLE,
        V3EntityType.UNKNOWN,
    },
}


def evaluate_v3_quality_gates(
    *,
    user_text: str,
    turn_context: V3TurnContext,
    stage1: V3Stage1Output,
    candidate_sets: list[V3CandidateSet],
    stage2: V3Stage2Output,
    vocabulary: CanonicalVocabulary,
) -> list[V3QualityGateResult]:
    """Evaluate structural V3 gates without applying medical text rules."""

    return [
        _turn_context_gate(turn_context),
        _stage1_contract_gate(user_text, turn_context, stage1),
        _entity_subject_role_gate(turn_context, stage1),
        _expected_evidence_coverage_gate(stage1, stage2),
        _participant_inheritance_gate(stage1, stage2),
        _assertion_verification_gate(stage2),
        _canonical_registry_gate(stage1, candidate_sets, stage2, vocabulary),
        _type_compatibility_gate(candidate_sets, stage2),
        _unmapped_review_gate(stage2),
        _suspicious_empty_gate(stage1, stage2),
    ]


def _turn_context_gate(turn_context: V3TurnContext) -> V3QualityGateResult:
    references = turn_context.entity_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.entity_type != V3EntityType.CURRENT_PET:
        errors.append("current_pet_entity_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_entity_reference")
    if any(not item.trusted for item in references.values()):
        errors.append("untrusted_entity_reference")
    return _gate(
        gate_id="v3_turn_context",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=V3QualityGateAction.FAIL_TURN if errors else V3QualityGateAction.PASS,
        metadata={"errors": errors, "entity_count": len(references)},
    )


def _stage1_contract_gate(
    user_text: str,
    turn_context: V3TurnContext,
    stage1: V3Stage1Output,
) -> V3QualityGateResult:
    errors: list[str] = []
    segment_ids: set[str] = set()
    derived_count = 0
    for segment in stage1.segments:
        if segment.segment_id in segment_ids:
            errors.append(f"duplicate_segment:{segment.segment_id}")
        segment_ids.add(segment.segment_id)
        if segment.source_text not in user_text:
            errors.append(f"segment_not_anchored:{segment.segment_id}")
        if segment.requires_evidence_analysis:
            derived_count += segment.expected_evidence_count
        if isinstance(segment, V3SharedAssertionScopeSegment):
            item_ids: set[str] = set()
            for item in segment.items:
                if item.item_id in item_ids:
                    errors.append(
                        f"duplicate_scope_item:{segment.segment_id}:{item.item_id}"
                    )
                item_ids.add(item.item_id)
                if item.source_text not in user_text:
                    errors.append(
                        f"scope_item_not_anchored:{segment.segment_id}:{item.item_id}"
                    )
                if item.source_text not in segment.source_text:
                    errors.append(
                        f"scope_item_outside_parent:{segment.segment_id}:{item.item_id}"
                    )
            if segment.expected_evidence_count != len(segment.items):
                errors.append(f"expected_count_mismatch:{segment.segment_id}")
        elif isinstance(segment, V3AtomicClaimSegment):
            if segment.expected_evidence_count != 1:
                errors.append(f"atomic_expected_count_invalid:{segment.segment_id}")

    if stage1.profile.expected_fact_candidate_count != derived_count:
        errors.append("profile_expected_count_mismatch")
    if stage1.profile.expected_fact_candidate_count > 0 and not stage1.segments:
        errors.append("expected_facts_but_stage1_empty")
    if stage1.profile.has_control_intent and any(
        segment.discourse_role.value == "control_intent" for segment in stage1.segments
    ):
        errors.append("control_intent_leaked_into_fact_segment")

    return _gate(
        gate_id="v3_stage1_contract",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="stage1_contract_invalid" if errors else "stage1_contract_valid",
        stage="stage1",
        severity="blocking",
        action=V3QualityGateAction.FAIL_TURN if errors else V3QualityGateAction.PASS,
        metadata={
            "errors": errors,
            "derived_expected_count": derived_count,
            "profile_expected_count": stage1.profile.expected_fact_candidate_count,
        },
    )


def _entity_subject_role_gate(
    turn_context: V3TurnContext,
    stage1: V3Stage1Output,
) -> V3QualityGateResult:
    trusted = turn_context.entity_references()
    errors: list[str] = []
    refs: list[str] = []
    for item in iter_v3_items(stage1):
        refs.append(item.item_key)
        errors.extend(
            _validate_binding(
                owner=item.item_key,
                role="subject",
                reference_id=item.subject.reference_id,
                entity_type=item.subject.entity_type,
                resolution_status=item.subject.resolution_status,
                candidates=item.subject.subject_candidates,
                trusted=trusted,
            )
        )
        for participant in item.participants:
            errors.extend(
                _validate_binding(
                    owner=item.item_key,
                    role=participant.role.value,
                    reference_id=participant.entity.reference_id,
                    entity_type=participant.entity.entity_type,
                    resolution_status=participant.entity.resolution_status,
                    candidates=participant.entity.subject_candidates,
                    trusted=trusted,
                )
            )
            allowed = _ROLE_ENTITY_TYPES.get(participant.role, set())
            if participant.entity.entity_type not in allowed:
                errors.append(
                    "participant_role_type_mismatch:"
                    f"{item.item_key}:{participant.role.value}"
                )
            if (
                participant.entity.resolution_status == V3ResolutionStatus.MISSING
                and participant.role != V3ParticipantRole.ACTION_OBJECT
            ):
                errors.append(
                    "required_participant_missing:"
                    f"{item.item_key}:{participant.role.value}"
                )

    return _gate(
        gate_id="v3_entity_subject_role",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="entity_subject_role_invalid" if errors else "entity_bindings_valid",
        stage="stage1_participant",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _expected_evidence_coverage_gate(
    stage1: V3Stage1Output,
    stage2: V3Stage2Output,
) -> V3QualityGateResult:
    expected = _expected_keys(stage1)
    actual_values = [(item.segment_id, item.item_id) for item in stage2.observations]
    actual = set(actual_values)
    errors: list[str] = []
    missing = expected - actual
    unexpected = actual - expected
    duplicate = len(actual_values) != len(actual)
    if missing:
        errors.append("expected_item_missing")
    if unexpected:
        errors.append("unexpected_stage2_item")
    if duplicate:
        errors.append("stage2_item_merged_or_duplicated")
    return _gate(
        gate_id="v3_expected_evidence_coverage",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="expected_evidence_coverage_failed"
        if errors
        else "expected_evidence_covered",
        stage="stage2_item",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=sorted(f"{segment}:{item}" for segment, item in expected),
        metadata={
            "errors": errors,
            "expected_count": len(expected),
            "handled_count": len(actual_values),
            "missing": sorted(f"{segment}:{item}" for segment, item in missing),
            "unexpected": sorted(f"{segment}:{item}" for segment, item in unexpected),
        },
        review_required=bool(errors),
    )


def _participant_inheritance_gate(
    stage1: V3Stage1Output,
    stage2: V3Stage2Output,
) -> V3QualityGateResult:
    expected = {item.item_key: _participant_signature(item) for item in iter_v3_items(stage1)}
    errors: list[str] = []
    refs: list[str] = []
    for evidence in stage2.observations:
        key = f"{evidence.segment_id}:{evidence.item_id}"
        refs.append(key)
        actual = {
            "subject": evidence.subject.model_dump(mode="json"),
            "participants": [
                participant.model_dump(mode="json")
                for participant in evidence.participants
            ],
        }
        if expected.get(key) != actual:
            errors.append(f"participants_not_inherited:{key}")
        if evidence.participant_verification not in {
            V3ParticipantVerification.VERIFIED,
            V3ParticipantVerification.NOT_APPLICABLE,
        }:
            errors.append(
                "participant_verification_failed:"
                f"{key}:{evidence.participant_verification.value}"
            )
    return _gate(
        gate_id="v3_participant_inheritance",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="participants_not_inherited" if errors else "participants_inherited",
        stage="stage2_participant",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _assertion_verification_gate(stage2: V3Stage2Output) -> V3QualityGateResult:
    errors = [
        evidence.evidence_id
        for evidence in stage2.observations
        if evidence.assertion_verification != V3AssertionVerification.VERIFIED
    ]
    return _gate(
        gate_id="v3_assertion_verification",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="initial_assertion_not_verified"
        if errors
        else "initial_assertions_verified",
        stage="stage2_assertion",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=errors,
        metadata={"failed_evidence_ids": errors},
        review_required=bool(errors),
    )


def _canonical_registry_gate(
    stage1: V3Stage1Output,
    candidate_sets: list[V3CandidateSet],
    stage2: V3Stage2Output,
    vocabulary: CanonicalVocabulary,
) -> V3QualityGateResult:
    del stage1
    terms = vocabulary.term_map()
    candidate_map = {
        f"{candidate_set.segment_id}:{candidate_set.item_id}": candidate_set
        for candidate_set in candidate_sets
    }
    errors: list[str] = []
    refs: list[str] = []
    for candidate_set in candidate_sets:
        ids = [candidate.candidate_id for candidate in candidate_set.candidates]
        if len(ids) != len(set(ids)):
            errors.append("duplicate_candidate_id")
        if any(candidate.canonical_id not in terms for candidate in candidate_set.candidates):
            errors.append("candidate_not_in_registry")

    for evidence in stage2.observations:
        key = f"{evidence.segment_id}:{evidence.item_id}"
        refs.append(key)
        evidence_candidate_set = candidate_map.get(key)
        if evidence_candidate_set is None:
            errors.append(f"candidate_set_missing:{key}")
            continue
        selected = next(
            (
                candidate
                for candidate in evidence_candidate_set.candidates
                if candidate.candidate_id == evidence.selected_candidate_id
            ),
            None,
        )
        if evidence.mapping_status == V3CanonicalMappingStatus.CONFIRMED:
            if not evidence_candidate_set.candidates:
                errors.append(f"confirmed_without_candidates:{key}")
            if evidence.selected_candidate_id is None or selected is None:
                errors.append(f"selected_candidate_invalid:{key}")
            if evidence.canonical_id is None:
                errors.append(f"confirmed_without_canonical_id:{key}")
            elif selected is not None and selected.canonical_id != evidence.canonical_id:
                errors.append(f"canonical_id_not_selected_candidate:{key}")
        else:
            if evidence.canonical_id is not None:
                errors.append(f"unresolved_mapping_has_canonical_id:{key}")
            if evidence.selected_candidate_id is not None:
                errors.append(f"unresolved_mapping_has_selected_candidate:{key}")
            if not evidence.review_required:
                errors.append(f"unresolved_mapping_review_missing:{key}")

    return _gate(
        gate_id="v3_canonical_registry",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="canonical_registry_invalid" if errors else "canonical_registry_valid",
        stage="canonical_linking",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "candidate_set_count": len(candidate_sets),
            "confirmed_count": sum(
                evidence.mapping_status == V3CanonicalMappingStatus.CONFIRMED
                for evidence in stage2.observations
            ),
        },
        review_required=bool(errors)
        or any(evidence.review_required for evidence in stage2.observations),
    )


def _type_compatibility_gate(
    candidate_sets: list[V3CandidateSet],
    stage2: V3Stage2Output,
) -> V3QualityGateResult:
    candidate_map = {
        f"{candidate_set.segment_id}:{candidate_set.item_id}": candidate_set
        for candidate_set in candidate_sets
    }
    errors: list[str] = []
    refs: list[str] = []
    for evidence in stage2.observations:
        if evidence.mapping_status != V3CanonicalMappingStatus.CONFIRMED:
            continue
        key = f"{evidence.segment_id}:{evidence.item_id}"
        refs.append(key)
        candidate_set = candidate_map.get(key)
        selected = next(
            (
                candidate
                for candidate in (candidate_set.candidates if candidate_set else [])
                if candidate.candidate_id == evidence.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            continue
        roles = {
            participant.role: participant.entity.entity_type
            for participant in evidence.participants
            if participant.entity.resolution_status == V3ResolutionStatus.RESOLVED
        }
        canonical_type = selected.canonical_type
        if canonical_type == "intervention":
            agent = roles.get(V3ParticipantRole.ACTION_AGENT)
            recipient = roles.get(V3ParticipantRole.ACTION_RECIPIENT)
            if agent not in {
                V3EntityType.USER,
                V3EntityType.CAREGIVER,
                V3EntityType.MEDICAL_ACTOR,
            } or recipient not in {
                V3EntityType.CURRENT_PET,
                V3EntityType.OTHER_PET,
            }:
                errors.append(f"intervention_participant_type_mismatch:{key}")
        if canonical_type in {"symptom", "status", "intake_output", "behavior"}:
            experiencer = roles.get(V3ParticipantRole.EXPERIENCER)
            if evidence.subject.entity_type not in {
                V3EntityType.CURRENT_PET,
                V3EntityType.OTHER_PET,
            } and experiencer not in {
                V3EntityType.CURRENT_PET,
                V3EntityType.OTHER_PET,
            }:
                errors.append(f"state_subject_type_mismatch:{key}")

    return _gate(
        gate_id="v3_type_compatibility",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.PASSED,
        reason_code="coarse_type_compatibility_failed"
        if errors
        else "coarse_type_compatibility_valid",
        stage="canonical_type",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V3QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={"errors": errors},
        review_required=bool(errors),
    )


def _unmapped_review_gate(stage2: V3Stage2Output) -> V3QualityGateResult:
    errors = [
        evidence.evidence_id
        for evidence in stage2.observations
        if evidence.mapping_status
        in {
            V3CanonicalMappingStatus.NOT_FOUND,
            V3CanonicalMappingStatus.UNMAPPED_MENTION,
            V3CanonicalMappingStatus.AMBIGUOUS,
            V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
        }
        and not evidence.review_required
    ]
    unmapped = sum(
        evidence.mapping_status
        in {
            V3CanonicalMappingStatus.NOT_FOUND,
            V3CanonicalMappingStatus.UNMAPPED_MENTION,
            V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
        }
        for evidence in stage2.observations
    )
    return _gate(
        gate_id="v3_unmapped_review",
        status=V3QualityGateStatus.FAILED if errors else V3QualityGateStatus.NEEDS_REVIEW,
        reason_code="unmapped_review_missing"
        if errors
        else "unmapped_mentions_routed_to_review",
        stage="canonical_review",
        severity="blocking" if errors else "observability_only",
        action=V3QualityGateAction.ROUTE_TO_REVIEW,
        evidence_refs=errors,
        metadata={"errors": errors, "unmapped_count": unmapped},
        review_required=True,
    )


def _suspicious_empty_gate(
    stage1: V3Stage1Output,
    stage2: V3Stage2Output,
) -> V3QualityGateResult:
    expected_count = len(_expected_keys(stage1))
    failed = expected_count > 0 and not stage2.observations
    return _gate(
        gate_id="v3_suspicious_empty",
        status=V3QualityGateStatus.FAILED if failed else V3QualityGateStatus.PASSED,
        reason_code="stage2_suspicious_empty"
        if failed
        else "semantic_coverage_observed",
        stage="semantic_quality",
        severity="blocking",
        action=V3QualityGateAction.ROUTE_TO_REVIEW
        if failed
        else V3QualityGateAction.PASS,
        metadata={
            "expected_evidence_count": expected_count,
            "handled_evidence_count": len(stage2.observations),
        },
        review_required=failed,
    )


def _validate_binding(
    *,
    owner: str,
    role: str,
    reference_id: str | None,
    entity_type: V3EntityType,
    resolution_status: V3ResolutionStatus,
    candidates: list[str],
    trusted: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if resolution_status == V3ResolutionStatus.RESOLVED:
        reference = trusted.get(reference_id or "")
        if reference is None:
            errors.append(f"entity_not_in_turn_context:{owner}:{role}")
        elif reference.entity_type != entity_type:
            errors.append(f"entity_type_mismatch:{owner}:{role}")
    elif resolution_status == V3ResolutionStatus.AMBIGUOUS:
        if len(candidates) < 2:
            errors.append(f"ambiguous_candidates_missing:{owner}:{role}")
        if any(candidate not in trusted for candidate in candidates):
            errors.append(f"ambiguous_candidate_not_trusted:{owner}:{role}")
    elif role == "subject":
        errors.append(f"subject_missing:{owner}")
    return errors


def _expected_keys(stage1: V3Stage1Output) -> set[tuple[str, str]]:
    return {
        (item.segment_id, item.item_id)
        for item in iter_v3_items(stage1)
    }


def _participant_signature(item: V3ItemContext) -> dict[str, Any]:
    return {
        "subject": item.subject.model_dump(mode="json"),
        "participants": [
            participant.model_dump(mode="json") for participant in item.participants
        ],
    }


def _gate(
    *,
    gate_id: str,
    status: V3QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: V3QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> V3QualityGateResult:
    return V3QualityGateResult(
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
