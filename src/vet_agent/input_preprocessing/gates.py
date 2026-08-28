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
    UnmappedMention,
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
        _segmentation_gate(user_text, turn_context, segmentation),
        _evidence_contract_gate(
            user_text=user_text,
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            vocabulary=vocabulary,
        ),
        _segment_evidence_coverage_gate(segmentation, evidence),
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
    turn_context: TurnContext,
    segmentation: SegmentationOutput,
) -> QualityGateResult:
    errors: list[str] = []
    segment_ids: set[str] = set()
    subject_ids = set(turn_context.subject_references())
    refs: list[str] = []
    for segment in segmentation.segments:
        if segment.segment_id in segment_ids:
            errors.append(f"duplicate_segment_id:{segment.segment_id}")
        segment_ids.add(segment.segment_id)
        if segment.source_text not in user_text:
            errors.append(f"source_not_anchored:{segment.segment_id}")
            continue
        refs.append(segment.segment_id)
        if segment.subject_reference is not None:
            allowed_subject_refs = {
                *subject_ids,
                "subject_ambiguous",
                "subject_missing",
            }
            if segment.subject_reference not in allowed_subject_refs:
                errors.append(f"unknown_segment_subject:{segment.segment_id}")
            if (
                segment.subject_reference == "subject_ambiguous"
                and not segment.subject_candidates
            ):
                errors.append(
                    f"segment_subject_candidates_missing:{segment.segment_id}"
                )
            if segment.subject_reference == "subject_ambiguous" and any(
                candidate not in subject_ids for candidate in segment.subject_candidates
            ):
                errors.append(f"unknown_segment_subject_candidate:{segment.segment_id}")

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
    mention_ids: set[str] = set()
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
        if (
            segment is not None
            and segment.subject_reference is not None
            and observation.subject.subject_reference != segment.subject_reference
        ):
            errors.append(f"segment_subject_binding_mismatch:{observation.evidence_id}")

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
            if subject_reference == "subject_ambiguous":
                if observation.subject.resolution_status != "ambiguous":
                    errors.append(
                        f"subject_ambiguous_status_invalid:{observation.evidence_id}"
                    )
                if not observation.subject.subject_candidates:
                    errors.append(
                        f"subject_ambiguous_candidates_missing:{observation.evidence_id}"
                    )
            elif observation.subject.resolution_status != "missing":
                errors.append(
                    f"subject_missing_status_invalid:{observation.evidence_id}"
                )
        else:
            trusted_subject = subject_map.get(subject_reference)
            if trusted_subject is None:
                errors.append(f"unknown_subject_reference:{observation.evidence_id}")
            elif trusted_subject.subject_type != observation.subject.subject_type:
                errors.append(f"subject_type_mismatch:{observation.evidence_id}")
        for candidate_reference in observation.subject.subject_candidates:
            if candidate_reference not in subject_map:
                errors.append(f"unknown_subject_candidate:{observation.evidence_id}")

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

    for mention in evidence.unmapped_mentions:
        if mention.mention_id in mention_ids:
            errors.append(f"duplicate_unmapped_mention_id:{mention.mention_id}")
        mention_ids.add(mention.mention_id)
        refs.append(mention.mention_id)
        segment = segment_map.get(mention.segment_id)
        if segment is None:
            errors.append(f"unknown_unmapped_segment:{mention.mention_id}")
        elif mention.source_text not in user_text and (
            mention.source_text not in segment.source_text
        ):
            errors.append(f"unmapped_mention_not_anchored:{mention.mention_id}")
        for candidate_id in mention.candidate_canonical_ids:
            if candidate_id not in term_map:
                errors.append(f"unknown_unmapped_candidate:{candidate_id}")

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
        metadata={
            "errors": errors,
            "evidence_count": len(evidence.observations),
            "unmapped_mention_count": len(evidence.unmapped_mentions),
        },
    )


def _segment_evidence_coverage_gate(
    segmentation: SegmentationOutput,
    evidence: EvidenceAnalysisOutput,
) -> QualityGateResult:
    actual_by_segment: dict[str, int] = defaultdict(int)
    for observation in evidence.observations:
        actual_by_segment[observation.segment_id] += 1

    errors: list[str] = []
    coverage: list[dict[str, Any]] = []
    for segment in segmentation.segments:
        if not segment.requires_evidence_analysis:
            continue
        actual = actual_by_segment.get(segment.segment_id, 0)
        expected = segment.expected_evidence_count
        if expected is not None and actual != expected:
            errors.append(f"segment_evidence_coverage_mismatch:{segment.segment_id}")
        coverage.append(
            {
                "segment_id": segment.segment_id,
                "expected": expected if expected is not None else -1,
                "actual": actual,
            }
        )

    return _gate(
        gate_id="segment_evidence_coverage",
        status=QualityGateStatus.FAILED if errors else QualityGateStatus.PASSED,
        reason_code="segment_evidence_coverage_mismatch"
        if errors
        else "segment_evidence_coverage_valid",
        stage="semantic_quality",
        severity="blocking",
        action=QualityGateAction.ROUTE_TO_REVIEW if errors else QualityGateAction.PASS,
        evidence_refs=[
            item["segment_id"]
            for item in coverage
            if item["expected"] != item["actual"]
        ],
        metadata={"errors": errors, "coverage": coverage},
        review_required=bool(errors),
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
        if evidence.unmapped_mentions:
            return _gate(
                gate_id="suspicious_empty",
                status=QualityGateStatus.FAILED,
                reason_code="canonical_not_found",
                stage="semantic_quality",
                severity="blocking",
                action=QualityGateAction.ROUTE_TO_REVIEW,
                evidence_refs=[
                    mention.mention_id for mention in evidence.unmapped_mentions
                ],
                metadata={
                    "expected_fact_candidate_count": expected,
                    "unmapped_mention_count": len(evidence.unmapped_mentions),
                    "mentions": [
                        UnmappedMention.model_validate(mention).model_dump()
                        for mention in evidence.unmapped_mentions
                    ],
                },
                review_required=True,
            )
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
