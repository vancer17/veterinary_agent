"""Explicit domain projections from the unified evidence graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from vet_agent.agents.semantic_extractor import (
    ConsultationFactCategory,
    ConsultationFactKey,
    ConsultationFactStatus,
    SemanticExtractionResult,
    SemanticFact,
    SemanticIntent,
    SemanticObservation,
)

from .contracts import (
    AssertionState,
    CanonicalStatus,
    CanonicalTerm,
    EvidenceObservation,
    InputAnalysisResult,
)
from .errors import InputPreprocessingQualityGateError
from .vocabulary import CanonicalVocabulary


@dataclass(frozen=True)
class ConsultationShadowProjection:
    """Consultation-domain projection retained for shadow behavior simulation."""

    status: Literal["projected", "blocked", "not_applicable"]
    semantic_result: SemanticExtractionResult
    rejected_observations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ClinicalSafetyShadowProjection:
    """Clinical-safety projection without retrieval or policy interpretation."""

    status: Literal["projected", "blocked", "not_applicable"]
    current_pet_symptoms: list[dict[str, Any]] = field(default_factory=list)
    denied_evidence: list[dict[str, Any]] = field(default_factory=list)
    normal_evidence: list[dict[str, Any]] = field(default_factory=list)
    excluded_evidence: list[dict[str, Any]] = field(default_factory=list)
    downstream_evaluation: Literal["not_implemented"] = "not_implemented"


def project_consultation(
    result: InputAnalysisResult,
    *,
    vocabulary: CanonicalVocabulary,
) -> ConsultationShadowProjection:
    """Project evidence into the existing consultation semantic contract."""

    if result.failed_blocking_gates():
        raise InputPreprocessingQualityGateError("consultation_projection_blocked")

    terms = vocabulary.term_map()
    facts: list[SemanticFact] = []
    observations: list[SemanticObservation] = []
    rejected: list[dict[str, Any]] = []
    current_pet = result.turn_context.current_pet_subject.reference_id

    for observation in result.evidence.observations:
        term = terms.get(observation.canonical_id)
        projection = _consultation_projection(observation, term)
        if (
            projection is None
            or term is None
            or not _consultation_subject_allowed(
                observation,
                current_pet_reference=current_pet,
                canonical_type=term.canonical_type,
            )
        ):
            rejected.append(observation.model_dump())
            continue

        if projection["kind"] == "fact":
            facts.append(
                SemanticFact(
                    key=ConsultationFactKey(projection["key"]),
                    value=projection["value"],
                    status=_consultation_status(observation.assertion),
                    confidence=observation.confidence,
                    source_text=observation.source_text,
                    category=ConsultationFactCategory(projection["category"]),
                    metadata={
                        "assertion": observation.assertion.value,
                        "canonical_id": observation.canonical_id,
                        "evidence_id": observation.evidence_id,
                        "subject_reference": observation.subject.subject_reference,
                        "temporal_count": len(observation.temporal_observations),
                        "measurement_count": len(observation.measurement_observations),
                    },
                )
            )
            if term.canonical_type == "symptom" and observation.temporal_observations:
                temporal = observation.temporal_observations[0]
                facts.append(
                    SemanticFact(
                        key=ConsultationFactKey.ONSET,
                        value=temporal.source_text[:160],
                        status=ConsultationFactStatus.CONFIRMED,
                        confidence=temporal.confidence,
                        source_text=temporal.source_text,
                        category=ConsultationFactCategory.TIME_COURSE,
                        metadata={
                            "derived_from": "structured_temporal_observation",
                            "canonical_id": observation.canonical_id,
                            "evidence_id": observation.evidence_id,
                            "temporal_id": temporal.temporal_id,
                            "temporal_status": temporal.status.value,
                            "precision": temporal.precision.value,
                        },
                    )
                )
        else:
            observations.append(
                SemanticObservation(
                    category=projection["category"],
                    label=projection["label"],
                    value=projection["value"],
                    status=_consultation_status(observation.assertion),
                    confidence=observation.confidence,
                    source_text=observation.source_text,
                    temporal_text=_temporal_text(observation),
                    metadata={
                        "assertion": observation.assertion.value,
                        "canonical_id": observation.canonical_id,
                        "evidence_id": observation.evidence_id,
                        "subject_reference": observation.subject.subject_reference,
                    },
                )
            )

    confidence = (
        sum(item.confidence for item in result.evidence.observations)
        / len(result.evidence.observations)
        if result.evidence.observations
        else 0.0
    )
    semantic_result = SemanticExtractionResult(
        facts=facts,
        observations=observations,
        intent=SemanticIntent(
            answer_now=result.segmentation.intent.answer_now,
            wants_triage=result.segmentation.intent.wants_triage,
            correction=result.segmentation.intent.correction,
            raw_intent=result.segmentation.intent.raw_intent,
        ),
        strategy="litellm_response_format",
        confidence=confidence,
        source_text="\n".join(
            segment.source_text for segment in result.segmentation.segments
        )[:240],
    )
    return ConsultationShadowProjection(
        status="projected",
        semantic_result=semantic_result,
        rejected_observations=rejected,
    )


def project_clinical_safety(
    result: InputAnalysisResult,
    *,
    vocabulary: CanonicalVocabulary,
) -> ClinicalSafetyShadowProjection:
    """Project current-pet evidence without making a safety decision."""

    if result.failed_blocking_gates():
        return ClinicalSafetyShadowProjection(status="blocked")

    symptoms: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    normal: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    current_pet = result.turn_context.current_pet_subject.reference_id
    terms = vocabulary.term_map()

    for observation in result.evidence.observations:
        payload = observation.model_dump()
        term = terms.get(observation.canonical_id)
        if (
            observation.canonical_status != CanonicalStatus.CONFIRMED
            or term is None
            or observation.subject.subject_reference != current_pet
        ):
            excluded.append(payload)
            continue
        if observation.assertion in {AssertionState.DENIED, AssertionState.NORMAL}:
            item = {
                "canonical_id": observation.canonical_id,
                "assertion": observation.assertion.value,
                "evidence_id": observation.evidence_id,
            }
            if observation.assertion == AssertionState.DENIED:
                denied.append(item)
            else:
                normal.append(item)
            continue
        if observation.assertion not in {
            AssertionState.PRESENT,
            AssertionState.ABNORMAL,
        }:
            excluded.append(payload)
            continue
        if term.clinical_safety_projection == "symptom":
            symptoms.append(
                {
                    "canonical_id": observation.canonical_id,
                    "assertion": observation.assertion.value,
                    "evidence_id": observation.evidence_id,
                    "source_text": observation.source_text,
                }
            )
        else:
            excluded.append(payload)

    return ClinicalSafetyShadowProjection(
        status="projected",
        current_pet_symptoms=symptoms,
        denied_evidence=denied,
        normal_evidence=normal,
        excluded_evidence=excluded,
    )


def _consultation_projection(
    observation: EvidenceObservation,
    term: CanonicalTerm | None,
) -> dict[str, Any] | None:
    if term is None or observation.canonical_status != CanonicalStatus.CONFIRMED:
        return None
    if observation.assertion not in {
        AssertionState.PRESENT,
        AssertionState.DENIED,
        AssertionState.NORMAL,
        AssertionState.ABNORMAL,
        AssertionState.HISTORICAL,
        AssertionState.RESOLVED,
    }:
        return None

    projection = term.consultation_projection
    if projection.get("kind") == "fact":
        values = projection.get("assertion_values") or {}
        value = values.get(observation.assertion.value)
        if not value:
            return None
        return {
            "kind": "fact",
            "key": projection["key"],
            "value": value,
            "category": _consultation_category(term.canonical_type),
        }
    if projection.get("kind") == "observation":
        return {
            "kind": "observation",
            "category": projection["category"],
            "label": projection["label"],
            "value": _observation_value(observation),
        }
    return None


def _consultation_subject_allowed(
    observation: EvidenceObservation,
    *,
    current_pet_reference: str,
    canonical_type: str,
) -> bool:
    if observation.subject.subject_reference == current_pet_reference:
        return True
    return canonical_type == "intervention" and observation.subject.subject_type in {
        "user",
        "caregiver",
    }


def _consultation_category(canonical_type: str) -> str:
    return {
        "symptom": "symptom_profile",
        "status": "systemic_status",
        "intake_output": "intake_output",
        "behavior": "behavior_context",
        "intervention": "domain_specific",
        "measurement": "intake_output",
    }.get(canonical_type, "other")


def _consultation_status(assertion: AssertionState) -> ConsultationFactStatus:
    if assertion == AssertionState.DENIED:
        return ConsultationFactStatus.NEGATIVE
    if assertion == AssertionState.UNCERTAIN:
        return ConsultationFactStatus.UNCERTAIN
    if assertion == AssertionState.UNKNOWN:
        return ConsultationFactStatus.UNKNOWN
    return ConsultationFactStatus.CONFIRMED


def _observation_value(observation: EvidenceObservation) -> str:
    mapping = {
        AssertionState.DENIED: "无",
        AssertionState.NORMAL: "正常",
        AssertionState.ABNORMAL: "异常",
        AssertionState.HISTORICAL: "既往存在",
        AssertionState.RESOLVED: "已恢复",
        AssertionState.PRESENT: "存在",
    }
    return mapping.get(observation.assertion, observation.source_text[:120])


def _temporal_text(observation: EvidenceObservation) -> str:
    return "; ".join(item.source_text for item in observation.temporal_observations)[
        :80
    ]
