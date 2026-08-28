"""Report-only domain projections for governed V4 flat observations."""

from __future__ import annotations

from typing import Any

from .v4_contracts import (
    GovernedFlatObservation,
    V4CanonicalMappingStatus,
    V4EntityType,
    V4InputAnalysisResult,
)


def project_consultation_facts(result: V4InputAnalysisResult) -> dict[str, Any]:
    """Return report-only consultation facts without writing business state."""

    if result.failed_blocking_gates():
        return {
            "status": "blocked_by_gate",
            "facts": [],
            "observations": [],
            "consultation_state_written": False,
        }

    facts: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for observation in result.observations:
        projection = _consultation_observation(observation)
        observations.append(projection)
        if projection["disposition"] == "consumable":
            facts.append(projection["payload"])
    return {
        "status": "projected",
        "facts": facts,
        "observations": observations,
        "consultation_state_written": False,
    }


def project_clinical_safety_report(result: V4InputAnalysisResult) -> dict[str, Any]:
    """Return a structural clinical-safety comparison payload only."""

    if result.failed_blocking_gates():
        return {
            "status": "blocked_by_gate",
            "downstream_evaluation": "not_implemented",
            "evaluator_called": False,
            "opa_called": False,
        }

    present: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for observation in result.observations:
        payload = _clinical_observation(observation)
        if payload["disposition"] == "current_pet_present":
            present.append(payload)
        elif payload["disposition"] == "current_pet_denied":
            denied.append(payload)
        else:
            excluded.append(payload)
    return {
        "status": "projected_report_only",
        "current_pet_present": present,
        "current_pet_denied": denied,
        "excluded_evidence": excluded,
        "downstream_evaluation": "not_implemented",
        "evaluator_called": False,
        "opa_called": False,
        "required_context_called": False,
    }


def _consultation_observation(
    observation: GovernedFlatObservation,
) -> dict[str, Any]:
    base = {
        "observation_id": observation.observation_id,
        "canonical_id": observation.canonical_id,
        "assertion": observation.assertion.value,
        "subject_reference": observation.subject.reference_id,
        "evidence_quote": observation.evidence_quote.raw_quote,
        "target_quote": observation.target_quote.raw_quote,
    }
    disposition = _disposition(observation)
    if disposition != "consumable":
        return base | {"disposition": disposition, "payload": None}
    return base | {
        "disposition": disposition,
        "payload": {
            "canonical_id": observation.canonical_id,
            "assertion": observation.assertion.value,
            "certainty": observation.certainty.value,
            "subject_reference": observation.subject.reference_id,
            "semantic_class": observation.semantic_class.value,
            "target_quote": observation.target_quote.raw_quote,
            "evidence_quote": observation.evidence_quote.raw_quote,
            "temporal_quote": (
                observation.temporal_quote.raw_quote
                if observation.temporal_quote is not None
                else None
            ),
            "measurement_quote": (
                observation.measurement_quote.raw_quote
                if observation.measurement_quote is not None
                else None
            ),
        },
    }


def _clinical_observation(
    observation: GovernedFlatObservation,
) -> dict[str, Any]:
    base = {
        "observation_id": observation.observation_id,
        "canonical_id": observation.canonical_id,
        "assertion": observation.assertion.value,
        "subject_reference": observation.subject.reference_id,
        "subject_type": observation.subject.entity_type.value,
        "target_quote": observation.target_quote.raw_quote,
    }
    disposition = _disposition(observation)
    if disposition == "current_pet_present":
        return base | {"disposition": disposition}
    if disposition == "current_pet_denied":
        return base | {"disposition": disposition}
    return base | {"disposition": disposition}


def _disposition(observation: GovernedFlatObservation) -> str:
    if observation.mapping_status != V4CanonicalMappingStatus.CONFIRMED:
        return "unmapped_or_review"
    if observation.evidence_quote.status != "resolved":
        return "invalid_quote"
    if observation.target_quote.status != "resolved":
        return "invalid_quote"
    if observation.subject.entity_type == V4EntityType.OTHER_PET:
        return "other_pet_excluded"
    if observation.subject.entity_type != V4EntityType.CURRENT_PET:
        return "subject_not_current_pet"
    if observation.assertion.value in {"historical", "hypothetical"}:
        return "non_current_assertion_excluded"
    if observation.assertion.value == "present":
        return "current_pet_present"
    if observation.assertion.value == "denied":
        return "current_pet_denied"
    if observation.assertion.value in {"normal", "abnormal", "resolved"}:
        return "current_pet_state"
    return "non_consumable_assertion"
