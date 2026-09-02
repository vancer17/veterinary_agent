"""Fixed-field turn intent helpers for V14."""

from __future__ import annotations

from typing import Any

from .v13_contracts import V13IntentActType
from .v14_contracts import V14SignalDetection, V14TurnIntentRaw

_DIRECT_SIGNALS = {
    V13IntentActType.ANSWER_NOW: "answer_now",
    V13IntentActType.WANTS_TRIAGE: "wants_triage",
    V13IntentActType.CORRECTION: "correction",
    V13IntentActType.CLARIFICATION_REQUEST: "clarification_request",
    V13IntentActType.FACT_STATEMENT: "fact_statement_present",
    V13IntentActType.QUESTION: "question_present",
    V13IntentActType.REPORT_CONTEXT: "report_context_present",
}


def ideal_v14_intent(unit: dict[str, Any]) -> V14TurnIntentRaw:
    """Convert explicit-offset fixture acts to the fixed-field control."""

    keys = (
        "answer_now",
        "wants_triage",
        "correction",
        "clarification_request",
        "fact_statement_present",
        "question_present",
        "report_context_present",
    )
    detections = {key: V14SignalDetection(detected=False) for key in keys}
    for act in unit.get("expected_acts", []):
        key = _DIRECT_SIGNALS[V13IntentActType(str(act["act_type"]))]
        detections[key] = V14SignalDetection(
            detected=True,
            evidence_phrase=str(act["evidence_quote"]),
            confidence=1.0,
        )
    return V14TurnIntentRaw(
        schema_version="v14-fixed-field-intent-1",
        **detections,
        no_signal_reason=(
            None
            if any(item.detected for item in detections.values())
            else "no_fixture_signal"
        ),
    )


def intent_reconciliation(
    intent: V14TurnIntentRaw,
    *,
    governed_claim_count: int,
) -> dict[str, Any]:
    """Reconcile turn-level fact presence with governed claim count."""

    fact_present = intent.fact_statement_present.detected
    if fact_present and governed_claim_count > 0:
        status = "consistent"
        review = False
    elif fact_present or governed_claim_count > 0:
        status = "intent_claim_mismatch"
        review = True
    else:
        status = "consistent_no_fact"
        review = False
    return {
        "status": status,
        "review_required": review,
        "fact_statement_present": fact_present,
        "governed_claim_count": governed_claim_count,
    }
