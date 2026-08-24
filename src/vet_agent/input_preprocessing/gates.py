"""Synchronous quality gates for input-preprocessing shadow output."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import (
    AssertionState,
    CanonicalStatus,
    EvidenceAnalysisOutput,
    EvidenceObservation,
    QualityGateAction,
    QualityGateResult,
    QualityGateStatus,
    SegmentationOutput,
    SubjectResolutionMethod,
    TurnContext,
)
from .vocabulary import CanonicalVocabulary

_CONTRADICTION_ASSERTIONS: tuple[tuple[AssertionState, AssertionState], ...] = (
    (AssertionState.PRESENT, AssertionState.DENIED),
    (AssertionState.PRESENT, AssertionState.NORMAL),
    (AssertionState.PRESENT, AssertionState.NOT_APPLICABLE),
    (AssertionState.ABNORMAL, AssertionState.NORMAL),
    (AssertionState.ABNORMAL, AssertionState.DENIED),
)


def evaluate_quality_gates(
    *,
    user_text: str,
    turn_context: TurnContext,
    segmentation: SegmentationOutput,
    evidence: EvidenceAnalysisOutput,
    vocabulary: CanonicalVocabulary,
) -> list[QualityGateResult]:
    """Evaluate all synchronous shadow gates without inspecting medical terms."""

    return [
        _turn_context_gate(turn_context),
        _segmentation_gate(user_text, segmentation),
        _evidence_contract_gate(
            user_text=user_text,
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            vocabulary=vocabulary,
        ),
        _suspicious_empty_gate(segmentation, evidence),
        _assertion_consistency_gate(evidence),
    ]


def _turn_context_gate(turn_context: TurnContext) -> QualityGateResult:
    references = turn_context.subject_references()
    errors: list[str] = []
    if turn_context.current_pet_subject.subject_type != "current_pet":
        errors.append("current_pet_subject_type_invalid")
    if turn_context.current_pet_subject.reference_id in {
        item.reference_id for item in turn_context.other_subjects
    }:
        errors.append("duplicate_subject_reference")
    if any(not subject.trusted for subject in references.values()):
        errors.append("untrusted_subject_reference")

    return _gate(
        gate_id="turn_context_contract",
        status=QualityGateStatus.FAILED if errors else QualityGateStatus.PASSED,
        reason_code="turn_context_invalid" if errors else "turn_context_valid",
        stage="turn_context",
        severity="blocking",
        action=QualityGateAction.FAIL_TURN if errors else QualityGateAction.PASS,
        metadata={"errors": errors, "subject_count": len(references)},
    )


def _segmentation_gate(
    user_text: str,
    segmentation: SegmentationOutput,
) -> QualityGateResult:
    errors: list[str] = []
    segment_ids: set[str] = set()
    refs: list[str] = []
    for segment in segmentation.segments:
        if segment.segment_id in segment_ids:
            errors.append(f"duplicate_segment_id:{segment.segment_id}")
        segment_ids.add(segment.segment_id)
        if segment.source_text not in user_text:
            errors.append(f"source_not_anchored:{segment.segment_id}")
            continue
        refs.append(segment.segment_id)

    expected = segmentation.profile.expected_fact_candidate_count
    if expected and not segmentation.segments:
        errors.append("expected_facts_but_segmentation_empty")

    return _gate(
        gate_id="segmentation_contract",
        status=QualityGateStatus.FAILED if errors else QualityGateStatus.PASSED,
        reason_code="segmentation_invalid" if errors else "segmentation_valid",
        stage="segmentation",
        severity="blocking",
        action=QualityGateAction.FAIL_TURN if errors else QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={
            "errors": errors,
            "segment_count": len(segmentation.segments),
            "expected_fact_candidate_count": expected,
        },
    )


def _evidence_contract_gate(
    *,
    user_text: str,
    turn_context: TurnContext,
    segmentation: SegmentationOutput,
    evidence: EvidenceAnalysisOutput,
    vocabulary: CanonicalVocabulary,
) -> QualityGateResult:
    errors: list[str] = []
    segment_map = {item.segment_id: item for item in segmentation.segments}
    subject_map = turn_context.subject_references()
    term_map = vocabulary.term_map()
    evidence_ids: set[str] = set()
    refs: list[str] = []

    for observation in evidence.observations:
        if observation.evidence_id in evidence_ids:
            errors.append(f"duplicate_evidence_id:{observation.evidence_id}")
        evidence_ids.add(observation.evidence_id)
        refs.append(observation.evidence_id)

        segment = segment_map.get(observation.segment_id)
        if segment is None:
            errors.append(f"unknown_segment:{observation.segment_id}")
        elif (
            observation.source_text not in user_text
            and observation.source_text not in segment.source_text
        ):
            errors.append(f"evidence_not_anchored:{observation.evidence_id}")

        term = term_map.get(observation.canonical_id)
        if term is None:
            errors.append(f"unknown_canonical:{observation.canonical_id}")
        elif observation.subject.subject_reference not in {
            "subject_ambiguous",
            "subject_missing",
        } and (
            observation.subject.subject_type not in term.allowed_subject_types
            and observation.canonical_status != CanonicalStatus.TYPE_MISMATCH
        ):
            errors.append(f"subject_type_not_allowed:{observation.canonical_id}")

        subject_reference = observation.subject.subject_reference
        if subject_reference in {"subject_ambiguous", "subject_missing"}:
            if observation.subject.subject_type != "unknown":
                errors.append(
                    f"unresolved_subject_type_invalid:{observation.evidence_id}"
                )
            if observation.subject.resolution_method not in {
                SubjectResolutionMethod.SUBJECT_AMBIGUOUS,
                SubjectResolutionMethod.SUBJECT_MISSING,
            }:
                errors.append(
                    f"unresolved_subject_method_invalid:{observation.evidence_id}"
                )
        else:
            trusted_subject = subject_map.get(subject_reference)
            if trusted_subject is None:
                errors.append(f"unknown_subject_reference:{observation.evidence_id}")
            elif trusted_subject.subject_type != observation.subject.subject_type:
                errors.append(f"subject_type_mismatch:{observation.evidence_id}")

        for candidate in observation.candidates:
            if candidate.canonical_id not in term_map:
                errors.append(f"unknown_candidate:{candidate.canonical_id}")
        for temporal in observation.temporal_observations:
            if temporal.segment_id != observation.segment_id:
                errors.append(f"temporal_segment_mismatch:{temporal.temporal_id}")
        for measurement in observation.measurement_observations:
            if measurement.segment_id != observation.segment_id:
                errors.append(
                    f"measurement_segment_mismatch:{measurement.measurement_id}"
                )

    return _gate(
        gate_id="evidence_contract",
        status=QualityGateStatus.FAILED if errors else QualityGateStatus.PASSED,
        reason_code="evidence_contract_invalid"
        if errors
        else "evidence_contract_valid",
        stage="evidence_analysis",
        severity="blocking",
        action=QualityGateAction.FAIL_TURN if errors else QualityGateAction.PASS,
        evidence_refs=refs,
        metadata={"errors": errors, "evidence_count": len(evidence.observations)},
    )


def _suspicious_empty_gate(
    segmentation: SegmentationOutput,
    evidence: EvidenceAnalysisOutput,
) -> QualityGateResult:
    expected = segmentation.profile.expected_fact_candidate_count
    analyzable_segments = [
        segment
        for segment in segmentation.segments
        if segment.requires_evidence_analysis
    ]
    if expected == 0:
        return _gate(
            gate_id="suspicious_empty",
            status=QualityGateStatus.NOT_APPLICABLE,
            reason_code="no_explicit_fact_expected",
            stage="semantic_quality",
            severity="observability_only",
            action=QualityGateAction.PASS,
            metadata={"expected_fact_candidate_count": 0},
        )

    if (
        not segmentation.segments
        or not analyzable_segments
        or not evidence.observations
    ):
        return _gate(
            gate_id="suspicious_empty",
            status=QualityGateStatus.FAILED,
            reason_code="segmentation_suspicious_empty",
            stage="semantic_quality",
            severity="blocking",
            action=QualityGateAction.ROUTE_TO_REVIEW,
            evidence_refs=[
                *(segment.segment_id for segment in analyzable_segments[:10]),
                *(item.evidence_id for item in evidence.observations[:10]),
            ],
            metadata={
                "expected_fact_candidate_count": expected,
                "segment_count": len(segmentation.segments),
                "analyzable_segment_count": len(analyzable_segments),
                "evidence_count": len(evidence.observations),
            },
            review_required=True,
        )

    if len(analyzable_segments) < expected:
        return _gate(
            gate_id="suspicious_empty",
            status=QualityGateStatus.WARNING,
            reason_code="segment_coverage_low",
            stage="semantic_quality",
            severity="major",
            action=QualityGateAction.ROUTE_TO_REVIEW,
            evidence_refs=[segment.segment_id for segment in analyzable_segments],
            metadata={
                "expected_fact_candidate_count": expected,
                "analyzable_segment_count": len(analyzable_segments),
            },
            review_required=True,
        )

    return _gate(
        gate_id="suspicious_empty",
        status=QualityGateStatus.PASSED,
        reason_code="semantic_coverage_observed",
        stage="semantic_quality",
        severity="observability_only",
        action=QualityGateAction.PASS,
        metadata={
            "expected_fact_candidate_count": expected,
            "analyzable_segment_count": len(analyzable_segments),
            "evidence_count": len(evidence.observations),
        },
    )


def _assertion_consistency_gate(
    evidence: EvidenceAnalysisOutput,
) -> QualityGateResult:
    grouped: dict[tuple[str, str], list[EvidenceObservation]] = defaultdict(list)
    for observation in evidence.observations:
        grouped[
            (observation.canonical_id, observation.subject.subject_reference)
        ].append(observation)

    conflicts: list[str] = []
    warnings: list[str] = []
    for group_key, observations in grouped.items():
        assertions = {item.assertion for item in observations}
        for left, right in _CONTRADICTION_ASSERTIONS:
            if left in assertions and right in assertions:
                conflicts.append(f"{group_key[0]}:{left.value}:{right.value}")
        if AssertionState.UNKNOWN in assertions and len(assertions) > 1:
            warnings.append(f"{group_key[0]}:unknown_mixed")

    if conflicts:
        return _gate(
            gate_id="assertion_consistency",
            status=QualityGateStatus.FAILED,
            reason_code="assertion_conflict",
            stage="semantic_quality",
            severity="blocking",
            action=QualityGateAction.ROUTE_TO_REVIEW,
            evidence_refs=[
                item.evidence_id
                for item in evidence.observations
                if item.assertion != AssertionState.UNKNOWN
            ],
            metadata={"conflicts": conflicts},
            review_required=True,
        )

    return _gate(
        gate_id="assertion_consistency",
        status=QualityGateStatus.WARNING if warnings else QualityGateStatus.PASSED,
        reason_code="assertion_warning" if warnings else "assertions_consistent",
        stage="semantic_quality",
        severity="minor" if warnings else "observability_only",
        action=QualityGateAction.PASS_WITH_METADATA
        if warnings
        else QualityGateAction.PASS,
        metadata={"warnings": warnings},
    )


def _gate(
    *,
    gate_id: str,
    status: QualityGateStatus,
    reason_code: str,
    stage: str,
    severity: str,
    action: QualityGateAction,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    review_required: bool = False,
) -> QualityGateResult:
    return QualityGateResult(
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
