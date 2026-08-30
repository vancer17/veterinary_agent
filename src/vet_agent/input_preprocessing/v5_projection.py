"""Report-only domain projection for V5 thin claims."""

from __future__ import annotations

from typing import Any

from .v5_contracts import (
    GovernedThinUserClaim,
    V5CanonicalMappingStatus,
    V5ClaimStateStatus,
    V5EntityType,
    V5InputAnalysisResult,
    V5UserStatementType,
)
from .vocabulary import CanonicalVocabulary


def project_v5_consultation_report(
    *,
    result: V5InputAnalysisResult,
    vocabulary: CanonicalVocabulary,
) -> dict[str, Any]:
    """Return report-only consultation facts without writing state."""

    if result.failed_blocking_gates():
        return {
            "status": "blocked_by_gate",
            "facts": [],
            "claims": [],
            "consultation_state_written": False,
        }

    facts: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    terms = vocabulary.term_map()
    for claim in result.claims:
        projection = _claim_projection(
            claim=claim,
            term=terms.get(claim.canonical.canonical_id or "")
            if claim.canonical is not None
            else None,
        )
        projections.append(projection)
        if projection["disposition"] == "consumable":
            facts.append(projection["payload"])
    return {
        "status": "projected_report_only",
        "facts": facts,
        "claims": projections,
        "consultation_state_written": False,
    }


def project_v5_clinical_safety_report(
    *,
    result: V5InputAnalysisResult,
) -> dict[str, Any]:
    """Return structural clinical-safety comparison only."""

    if result.failed_blocking_gates():
        return {
            "status": "blocked_by_gate",
            "downstream_evaluation": "not_implemented",
            "evaluator_called": False,
            "opa_called": False,
            "required_context_called": False,
        }

    present: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for claim in result.claims:
        payload = _clinical_projection(claim)
        disposition = payload["disposition"]
        if disposition == "current_pet_present":
            present.append(payload)
        elif disposition == "current_pet_denied":
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


def _claim_projection(
    *,
    claim: GovernedThinUserClaim,
    term: Any | None,
) -> dict[str, Any]:
    base = {
        "claim_id": claim.raw.claim_id,
        "canonical_id": claim.canonical.canonical_id if claim.canonical else None,
        "user_statement_type": claim.raw.user_statement_type.value,
        "subject_reference": (
            claim.subject.subject.reference_id if claim.subject else None
        ),
        "evidence_quote": claim.raw.evidence_quote,
        "target_quote": claim.raw.target_quote,
    }
    disposition = _consultation_disposition(claim)
    if disposition != "consumable" or term is None:
        return base | {"disposition": disposition, "payload": None}

    projection = term.consultation_projection
    statement = claim.raw.user_statement_type
    assertion_value = _consultation_assertion(statement)
    if projection.get("kind") == "fact":
        value = projection.get("assertion_values", {}).get(assertion_value)
        if value is None:
            return base | {"disposition": "projection_value_missing", "payload": None}
        payload = {
            "kind": "fact",
            "key": projection["key"],
            "value": value,
            "assertion": assertion_value,
            "subject_reference": claim.subject.subject.reference_id
            if claim.subject
            else None,
            "target_quote": claim.raw.target_quote,
            "evidence_quote": claim.raw.evidence_quote,
        }
    else:
        payload = {
            "kind": "observation",
            "category": projection.get("category", "other"),
            "label": projection.get("label", claim.raw.target_quote),
            "value": _statement_display(statement),
            "subject_reference": claim.subject.subject.reference_id
            if claim.subject
            else None,
            "target_quote": claim.raw.target_quote,
            "evidence_quote": claim.raw.evidence_quote,
        }
    if claim.temporal is not None:
        payload["temporal_quote"] = claim.raw.temporal_quote
        payload["temporal_status"] = claim.temporal.normalization_status.value
    if claim.measurement is not None:
        payload["measurement_quote"] = claim.raw.measurement_quote
        payload["measurement_status"] = claim.measurement.normalization_status.value
    return base | {"disposition": disposition, "payload": payload}


def _consultation_disposition(claim: GovernedThinUserClaim) -> str:
    if claim.state.projection_state != V5ClaimStateStatus.READY:
        return "projection_not_ready"
    if (
        claim.canonical is None
        or claim.canonical.mapping_status != V5CanonicalMappingStatus.CONFIRMED
    ):
        return "unmapped_or_review"
    if claim.subject is None:
        return "subject_not_resolved"
    entity_type = claim.subject.subject.entity_type
    if entity_type == V5EntityType.OTHER_PET:
        return "other_pet_excluded"
    if claim.raw.user_statement_type in {
        V5UserStatementType.HISTORICAL,
        V5UserStatementType.HYPOTHETICAL,
        V5UserStatementType.ASKS,
    }:
        return "non_current_statement_excluded"
    if entity_type == V5EntityType.CURRENT_PET:
        return "consumable"
    if entity_type in {V5EntityType.USER, V5EntityType.CAREGIVER} and (
        claim.raw.coarse_type.value in {"action", "food"}
    ):
        return "consumable"
    return "subject_not_consumable"


def _clinical_projection(claim: GovernedThinUserClaim) -> dict[str, Any]:
    base = {
        "claim_id": claim.raw.claim_id,
        "canonical_id": claim.canonical.canonical_id if claim.canonical else None,
        "user_statement_type": claim.raw.user_statement_type.value,
        "subject_reference": (
            claim.subject.subject.reference_id if claim.subject else None
        ),
        "subject_type": (
            claim.subject.subject.entity_type.value if claim.subject else "unknown"
        ),
        "target_quote": claim.raw.target_quote,
        "evidence_quote": claim.raw.evidence_quote,
    }
    disposition = _clinical_disposition(claim)
    return base | {"disposition": disposition}


def _clinical_disposition(claim: GovernedThinUserClaim) -> str:
    if claim.state.projection_state != V5ClaimStateStatus.READY:
        return "projection_not_ready"
    if (
        claim.canonical is None
        or claim.canonical.mapping_status != V5CanonicalMappingStatus.CONFIRMED
    ):
        return "unmapped_or_review"
    if claim.subject is None:
        return "subject_not_resolved"
    if claim.subject.subject.entity_type == V5EntityType.OTHER_PET:
        return "other_pet_excluded"
    if claim.subject.subject.entity_type != V5EntityType.CURRENT_PET:
        return "subject_not_current_pet"
    if claim.raw.user_statement_type in {
        V5UserStatementType.HISTORICAL,
        V5UserStatementType.HYPOTHETICAL,
        V5UserStatementType.ASKS,
    }:
        return "non_current_statement_excluded"
    if claim.raw.user_statement_type in {
        V5UserStatementType.REPORTS,
        V5UserStatementType.REPORTS_ABNORMAL,
    }:
        return "current_pet_present"
    if claim.raw.user_statement_type == V5UserStatementType.DENIES:
        return "current_pet_denied"
    return "current_pet_state"


def _consultation_assertion(statement: V5UserStatementType) -> str:
    return {
        V5UserStatementType.REPORTS: "present",
        V5UserStatementType.REPORTS_ABNORMAL: "abnormal",
        V5UserStatementType.REPORTS_NORMAL: "normal",
        V5UserStatementType.DENIES: "denied",
        V5UserStatementType.UNCERTAIN: "uncertain",
        V5UserStatementType.HISTORICAL: "historical",
        V5UserStatementType.HYPOTHETICAL: "hypothetical",
    }[statement]


def _statement_display(statement: V5UserStatementType) -> str:
    return {
        V5UserStatementType.REPORTS: "存在",
        V5UserStatementType.REPORTS_ABNORMAL: "异常",
        V5UserStatementType.REPORTS_NORMAL: "正常",
        V5UserStatementType.DENIES: "无",
        V5UserStatementType.UNCERTAIN: "不确定",
        V5UserStatementType.HISTORICAL: "既往存在",
        V5UserStatementType.HYPOTHETICAL: "假设",
        V5UserStatementType.ASKS: "询问",
        V5UserStatementType.CORRECTS: "纠正",
    }[statement]
