"""Synchronous V2 architecture gates for second-round shadow validation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .v2_contracts import (
    V2AtomicClaimSegment,
    V2CanonicalMappingStatus,
    V2EntityBinding,
    V2ParticipantRole,
    V2QualityGateAction,
    V2QualityGateResult,
    V2QualityGateStatus,
    V2ResolutionStatus,
    V2Segment,
    V2SharedAssertionScopeSegment,
    V2Stage1Output,
    V2Stage2Output,
    V2TurnContext,
)
from .vocabulary import CanonicalVocabulary

_ROLE_ENTITY_TYPES: dict[V2ParticipantRole, set[str]] = {
    V2ParticipantRole.ACTION_AGENT: {"user", "caregiver", "medical_actor"},
    V2ParticipantRole.ACTION_RECIPIENT: {"current_pet", "other_pet"},
    V2ParticipantRole.EXPERIENCER: {"current_pet", "other_pet"},
    V2ParticipantRole.ACTION_OBJECT: {"food", "environment", "sample", "unknown"},
    V2ParticipantRole.SOURCE: {"user", "caregiver", "medical_actor", "environment"},
    V2ParticipantRole.LOCATION: {"environment"},
    V2ParticipantRole.INSTRUMENT: {"environment", "sample"},
    V2ParticipantRole.GOAL: {"food", "environment", "sample", "unknown"},
    V2ParticipantRole.CAUSE: {"environment", "food", "sample", "unknown"},
}


def evaluate_v2_quality_gates(
    *,
    user_text: str,
    turn_context: V2TurnContext,
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
    vocabulary: CanonicalVocabulary,
) -> list[V2QualityGateResult]:
    """Evaluate structural gates without reading medical terms or raw-text rules."""

    return [
        _turn_context_gate(turn_context),
        _stage1_contract_gate(user_text, turn_context, stage1),
        _entity_subject_role_gate(turn_context, stage1, stage2),
        _stage2_contract_gate(user_text, stage1, stage2),
        _expected_evidence_coverage_gate(stage1, stage2),
        _canonical_registry_gate(stage2, vocabulary),
        _assertion_consistency_gate(stage1, stage2),
        _suspicious_empty_gate(stage1, stage2),
    ]


def _turn_context_gate(turn_context: V2TurnContext) -> V2QualityGateResult:
    references = turn_context.entity_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.entity_type != "current_pet":
        errors.append("current_pet_entity_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_entity_reference")
    if any(not item.trusted for item in references.values()):
        errors.append("untrusted_entity_reference")
    return _gate(
        gate_id="v2_turn_context",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=V2QualityGateAction.FAIL_TURN if errors else V2QualityGateAction.PASS,
        metadata={"errors": errors, "entity_count": len(references)},
    )


def _stage1_contract_gate(
    user_text: str,
    turn_context: V2TurnContext,
    stage1: V2Stage1Output,
) -> V2QualityGateResult:
    errors: list[str] = []
    segment_ids: set[str] = set()
    refs: list[str] = []
    for segment in stage1.segments:
        if segment.segment_id in segment_ids:
            errors.append(f"duplicate_segment:{segment.segment_id}")
        segment_ids.add(segment.segment_id)
        if segment.source_text not in user_text:
            errors.append(f"segment_not_anchored:{segment.segment_id}")
        else:
            refs.append(segment.segment_id)
        if isinstance(segment, V2SharedAssertionScopeSegment):
            item_ids: set[str] = set()
            for item in segment.items:
                if item.item_id in item_ids:
                    errors.append(f"duplicate_scope_item:{segment.segment_id}")
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
                errors.append(
                    f"expected_count_mismatch:{segment.segment_id}",
                )
        elif isinstance(segment, V2AtomicClaimSegment):
            if segment.expected_evidence_count != 1:
                errors.append(f"atomic_expected_count_invalid:{segment.segment_id}")

    expected = stage1.profile.expected_fact_candidate_count
    if expected and not stage1.segments:
        errors.append("expected_facts_but_stage1_empty")
    return _gate(
        gate_id="v2_stage1_contract",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code="stage1_contract_invalid" if errors else "stage1_contract_valid",
        stage="stage1",
        severity="blocking",
        action=V2QualityGateAction.FAIL_TURN if errors else V2QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "segment_count": len(stage1.segments),
            "expected_fact_candidate_count": expected,
            "segment_kinds": _segment_kinds(stage1),
        },
    )


def _entity_subject_role_gate(
    turn_context: V2TurnContext,
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> V2QualityGateResult:
    trusted = turn_context.entity_references()
    errors: list[str] = []
    refs: list[str] = []

    bindings: list[tuple[str, str, V2EntityBinding]] = []
    for segment in stage1.segments:
        bindings.append((segment.segment_id, "segment_subject", segment.subject))
        bindings.extend(
            (segment.segment_id, participant.role.value, participant.entity)
            for participant in segment.participants
        )
        if isinstance(segment, V2SharedAssertionScopeSegment):
            for item in segment.items:
                bindings.append(
                    (
                        f"{segment.segment_id}:{item.item_id}",
                        "item_subject",
                        item.subject,
                    )
                )
                bindings.extend(
                    (
                        f"{segment.segment_id}:{item.item_id}",
                        participant.role.value,
                        participant.entity,
                    )
                    for participant in item.participants
                )

    for evidence in stage2.observations:
        bindings.append(
            (
                f"{evidence.segment_id}:{evidence.item_id}",
                "item_subject",
                evidence.subject,
            )
        )
        bindings.extend(
            (
                f"{evidence.segment_id}:{evidence.item_id}",
                participant.role.value,
                participant.entity,
            )
            for participant in evidence.participants
        )

    for owner, role, binding in bindings:
        refs.append(owner)
        errors.extend(_validate_entity_binding(owner, role, binding, trusted))
        if (
            role == "segment_subject"
            and binding.resolution_status == V2ResolutionStatus.MISSING
        ):
            errors.append(f"subject_missing:{owner}")

    return _gate(
        gate_id="v2_entity_subject_role",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code="entity_subject_role_invalid"
        if errors
        else "entity_subject_role_valid",
        stage="entity_subject_role",
        severity="blocking",
        action=V2QualityGateAction.FAIL_TURN if errors else V2QualityGateAction.PASS,
        evidence_refs=sorted(set(refs)),
        metadata={
            "errors": errors,
            "binding_count": len(bindings),
            "stage2_binding_count": len(stage2.observations)
            + sum(len(item.participants) for item in stage2.observations),
            "ambiguous_binding_count": sum(
                binding.resolution_status == V2ResolutionStatus.AMBIGUOUS
                for _, _, binding in bindings
            ),
        },
    )


def _stage2_contract_gate(
    user_text: str,
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> V2QualityGateResult:
    errors: list[str] = []
    evidence_ids: set[str] = set()
    segment_map = {segment.segment_id: segment for segment in stage1.segments}
    item_texts = _stage1_item_texts(stage1)
    refs: list[str] = []
    for evidence in stage2.observations:
        if evidence.evidence_id in evidence_ids:
            errors.append(f"duplicate_evidence:{evidence.evidence_id}")
        evidence_ids.add(evidence.evidence_id)
        refs.append(evidence.evidence_id)
        segment = segment_map.get(evidence.segment_id)
        if segment is None:
            errors.append(f"unknown_evidence_segment:{evidence.evidence_id}")
            continue
        stage1_binding = _stage1_item_bindings(stage1).get(
            (evidence.segment_id, evidence.item_id)
        )
        if stage1_binding is None:
            errors.append(f"stage1_item_binding_missing:{evidence.evidence_id}")
        else:
            expected_subject, expected_participants = stage1_binding
            if (
                evidence.subject.reference_id != expected_subject.reference_id
                or evidence.subject.entity_type != expected_subject.entity_type
            ):
                errors.append(f"stage2_subject_binding_mismatch:{evidence.evidence_id}")
            if _participant_refs(evidence.participants) != _participant_refs(
                expected_participants
            ):
                errors.append(
                    f"stage2_participants_not_verified:{evidence.evidence_id}"
                )
        expected_item_ids = _segment_item_ids(segment)
        if evidence.item_id not in expected_item_ids:
            errors.append(f"unknown_or_extra_item:{evidence.evidence_id}")
        expected_text = item_texts.get((segment.segment_id, evidence.item_id))
        if expected_text is None or (
            evidence.source_text not in user_text
            and evidence.source_text not in expected_text
            and expected_text not in evidence.source_text
        ):
            errors.append(f"evidence_not_anchored:{evidence.evidence_id}")
        if evidence.source_text not in segment.source_text and (
            expected_text is None or expected_text not in evidence.source_text
        ):
            errors.append(f"evidence_outside_parent:{evidence.evidence_id}")
    return _gate(
        gate_id="v2_stage2_contract",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code="stage2_contract_invalid" if errors else "stage2_contract_valid",
        stage="stage2",
        severity="blocking",
        action=V2QualityGateAction.FAIL_TURN if errors else V2QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "evidence_count": len(stage2.observations),
        },
    )


def _expected_evidence_coverage_gate(
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> V2QualityGateResult:
    expected = _expected_keys(stage1)
    actual: set[tuple[str, str]] = set()
    errors: list[str] = []
    for evidence in stage2.observations:
        key = (evidence.segment_id, evidence.item_id)
        if key in actual:
            errors.append(f"duplicate_expected_item:{evidence.evidence_id}")
        actual.add(key)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.extend(
            f"expected_item_unhandled:{segment_id}:{item_id}"
            for segment_id, item_id in missing
        )
    if extra:
        errors.extend(
            f"unexpected_stage2_item:{segment_id}:{item_id}"
            for segment_id, item_id in extra
        )

    coverage = [
        {
            "segment_id": segment_id,
            "item_id": item_id,
            "handled": (segment_id, item_id) in actual,
        }
        for segment_id, item_id in sorted(expected)
    ]
    return _gate(
        gate_id="v2_expected_evidence_coverage",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code=(
            "expected_evidence_coverage_mismatch"
            if errors
            else "expected_evidence_covered"
        ),
        stage="semantic_quality",
        severity="blocking",
        action=V2QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V2QualityGateAction.PASS,
        evidence_refs=[
            f"{segment_id}:{item_id}" for segment_id, item_id in [*missing, *extra]
        ],
        metadata={
            "errors": errors,
            "expected_count": len(expected),
            "handled_count": len(expected & actual),
            "coverage": coverage,
        },
        review_required=bool(errors),
    )


def _canonical_registry_gate(
    stage2: V2Stage2Output,
    vocabulary: CanonicalVocabulary,
) -> V2QualityGateResult:
    terms = vocabulary.term_map()
    errors: list[str] = []
    refs: list[str] = []
    unresolved = 0
    for evidence in stage2.observations:
        refs.append(evidence.evidence_id)
        if evidence.mapping_status != V2CanonicalMappingStatus.CONFIRMED:
            unresolved += 1
            if evidence.canonical_id is not None:
                errors.append(f"unresolved_mapping_has_id:{evidence.evidence_id}")
            if (
                evidence.mapping_status
                in {
                    V2CanonicalMappingStatus.NOT_FOUND,
                    V2CanonicalMappingStatus.UNMAPPED_MENTION,
                    V2CanonicalMappingStatus.AMBIGUOUS,
                    V2CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                and not evidence.review_required
            ):
                errors.append(f"review_missing:{evidence.evidence_id}")
            continue
        term = terms.get(evidence.canonical_id or "")
        if term is None:
            errors.append(f"canonical_not_in_registry:{evidence.evidence_id}")
        elif evidence.subject.entity_type.value not in term.allowed_subject_types:
            errors.append(f"canonical_subject_type_mismatch:{evidence.evidence_id}")
        candidate_ids = {candidate.canonical_id for candidate in evidence.candidates}
        if not candidate_ids:
            errors.append(f"confirmed_candidates_missing:{evidence.evidence_id}")
        elif (evidence.canonical_id or "") not in candidate_ids:
            errors.append(f"confirmed_not_in_recall_candidates:{evidence.evidence_id}")
        for candidate in evidence.candidates:
            if candidate.canonical_id not in terms:
                errors.append(f"candidate_not_in_registry:{candidate.canonical_id}")
    return _gate(
        gate_id="v2_canonical_registry",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code="canonical_registry_invalid"
        if errors
        else "canonical_registry_valid",
        stage="canonical_mapping",
        severity="blocking",
        action=V2QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V2QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "confirmed_count": len(stage2.observations) - unresolved,
            "unresolved_count": unresolved,
            "review_required_count": sum(
                evidence.review_required for evidence in stage2.observations
            ),
        },
        review_required=bool(errors)
        or any(evidence.review_required for evidence in stage2.observations),
    )


def _assertion_consistency_gate(
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> V2QualityGateResult:
    errors: list[str] = []
    refs: list[str] = []
    expected_assertions = _expected_assertions(stage1)
    for evidence in stage2.observations:
        expected_assertion = expected_assertions.get(
            (evidence.segment_id, evidence.item_id)
        )
        if expected_assertion is not None and evidence.assertion != expected_assertion:
            errors.append(f"stage2_assertion_mismatch:{evidence.evidence_id}")
            refs.append(evidence.evidence_id)
    return _gate(
        gate_id="v2_assertion_consistency",
        status=V2QualityGateStatus.FAILED if errors else V2QualityGateStatus.PASSED,
        reason_code=(
            "stage2_assertion_mismatch" if errors else "stage2_assertion_verified"
        ),
        stage="semantic_quality",
        severity="blocking",
        action=V2QualityGateAction.ROUTE_TO_REVIEW
        if errors
        else V2QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "assertion_item_count": len(expected_assertions),
        },
        review_required=bool(errors),
    )


def _expected_assertions(
    stage1: V2Stage1Output,
) -> dict[tuple[str, str], Any]:
    assertions: dict[tuple[str, str], Any] = {}
    for segment in stage1.segments:
        if isinstance(segment, V2SharedAssertionScopeSegment):
            for item in segment.items:
                assertions[(segment.segment_id, item.item_id)] = segment.scope_assertion
        elif isinstance(segment, V2AtomicClaimSegment):
            assertions[(segment.segment_id, segment.item_id)] = (
                segment.initial_assertion
            )
    return assertions


def _suspicious_empty_gate(
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> V2QualityGateResult:
    expected_count = len(_expected_keys(stage1))
    if stage1.profile.expected_fact_candidate_count == 0:
        return _gate(
            gate_id="v2_suspicious_empty",
            status=V2QualityGateStatus.NOT_APPLICABLE,
            reason_code="no_explicit_fact_expected",
            stage="semantic_quality",
            severity="observability_only",
            action=V2QualityGateAction.PASS,
        )
    failed = expected_count > 0 and len(stage2.observations) == 0
    return _gate(
        gate_id="v2_suspicious_empty",
        status=V2QualityGateStatus.FAILED if failed else V2QualityGateStatus.PASSED,
        reason_code="stage2_suspicious_empty"
        if failed
        else "semantic_coverage_observed",
        stage="semantic_quality",
        severity="blocking",
        action=V2QualityGateAction.ROUTE_TO_REVIEW
        if failed
        else V2QualityGateAction.PASS,
        metadata={
            "expected_evidence_count": expected_count,
            "handled_evidence_count": len(stage2.observations),
        },
        review_required=failed,
    )


def _validate_entity_binding(
    owner: str,
    role: str,
    binding: V2EntityBinding,
    trusted: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if binding.resolution_status == V2ResolutionStatus.RESOLVED:
        reference = trusted.get(binding.reference_id)
        if reference is None:
            errors.append(f"entity_not_in_turn_context:{owner}:{role}")
        elif reference.entity_type != binding.entity_type:
            errors.append(f"entity_type_mismatch:{owner}:{role}")
    elif binding.resolution_status == V2ResolutionStatus.AMBIGUOUS:
        if len(binding.subject_candidates) < 2:
            errors.append(f"ambiguous_candidates_missing:{owner}:{role}")
        if any(candidate not in trusted for candidate in binding.subject_candidates):
            errors.append(f"ambiguous_candidate_not_trusted:{owner}:{role}")
    elif binding.resolution_status == V2ResolutionStatus.MISSING:
        if role not in {"action_object", "goal", "cause"}:
            errors.append(f"required_participant_missing:{owner}:{role}")
    else:  # pragma: no cover - Pydantic enum prevents this branch.
        errors.append(f"resolution_status_invalid:{owner}:{role}")

    participant_role = next(
        (item for item in V2ParticipantRole if item.value == role), None
    )
    if participant_role is not None:
        allowed = _ROLE_ENTITY_TYPES[participant_role]
        if binding.entity_type.value not in allowed:
            errors.append(f"participant_role_type_mismatch:{owner}:{role}")
    return errors


def _segment_item_ids(segment: V2Segment) -> set[str]:
    if isinstance(segment, V2SharedAssertionScopeSegment):
        return {item.item_id for item in segment.items}
    if isinstance(segment, V2AtomicClaimSegment):
        return {segment.item_id}
    raise AssertionError("unknown_segment_type")


def _stage1_item_texts(stage1: V2Stage1Output) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for segment in stage1.segments:
        if isinstance(segment, V2SharedAssertionScopeSegment):
            for item in segment.items:
                result[(segment.segment_id, item.item_id)] = item.source_text
        elif isinstance(segment, V2AtomicClaimSegment):
            result[(segment.segment_id, segment.item_id)] = segment.source_text
    return result


def _stage1_item_bindings(
    stage1: V2Stage1Output,
) -> dict[tuple[str, str], tuple[V2EntityBinding, list[Any]]]:
    result: dict[tuple[str, str], tuple[V2EntityBinding, list[Any]]] = {}
    for segment in stage1.segments:
        if isinstance(segment, V2SharedAssertionScopeSegment):
            for item in segment.items:
                result[(segment.segment_id, item.item_id)] = (
                    item.subject,
                    item.participants,
                )
        elif isinstance(segment, V2AtomicClaimSegment):
            result[(segment.segment_id, segment.item_id)] = (
                segment.subject,
                segment.participants,
            )
    return result


def _participant_refs(participants: list[Any]) -> list[tuple[str, str]]:
    return sorted((item.role.value, item.entity.reference_id) for item in participants)


def _expected_keys(stage1: V2Stage1Output) -> set[tuple[str, str]]:
    return {
        (segment.segment_id, item_id)
        for segment in stage1.segments
        if segment.requires_evidence_analysis
        for item_id in _segment_item_ids(segment)
    }


def _segment_kinds(stage1: V2Stage1Output) -> dict[str, int]:
    kinds: dict[str, int] = defaultdict(int)
    for segment in stage1.segments:
        kinds[segment.kind] += 1
    return dict(kinds)


def _gate(
    *,
    gate_id: str,
    status: V2QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: V2QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> V2QualityGateResult:
    return V2QualityGateResult(
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
