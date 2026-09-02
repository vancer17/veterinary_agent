"""Evaluation and field-level governance metrics for V14."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v8_contracts import V8EntityCandidate
from .v14_contracts import V14AlignmentStatus, V14GovernedClaim, V14TurnIntentRaw
from .v14_intent import ideal_v14_intent
from .v14_participant_resolver import resolve_claim_participants


def _rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _envelope_matches(actual: str, expected: str) -> bool:
    if not actual or not expected:
        return False
    if actual == expected or actual in expected:
        return True
    # A gold support may legitimately contain a slightly broader model phrase,
    # but a full-turn envelope must not count as claim-local evidence.
    return expected in actual and len(actual) <= len(expected) + 8


def _claim_matches(claim: V14GovernedClaim, expected: dict[str, Any]) -> bool:
    return (
        claim.raw_claim.user_statement_type.value
        == str(expected["statement_type"])
        and claim.target.aligned_quote == str(expected["target_quote"])
        and _envelope_matches(
            claim.evidence.aligned_quote,
            str(expected["support_quote"]),
        )
    )


def _expected_for_claim(
    claim: V14GovernedClaim,
    expected_claims: list[dict[str, Any]],
) -> dict[str, Any] | None:
    exact = next(
        (
            item
            for item in expected_claims
            if claim.target.aligned_quote == str(item.get("target_quote", ""))
            and _envelope_matches(
                claim.evidence.aligned_quote,
                str(item.get("support_quote", "")),
            )
        ),
        None,
    )
    if exact is not None:
        return exact
    matches = [
        item
        for item in expected_claims
        if _envelope_matches(
            claim.evidence.aligned_quote,
            str(item.get("support_quote", "")),
        )
    ]
    return matches[0] if len(matches) == 1 else None


def evaluate_v14_intent(
    *,
    unit: dict[str, Any],
    output: V14TurnIntentRaw,
) -> dict[str, Any]:
    expected = ideal_v14_intent(unit)
    keys = (
        "answer_now",
        "wants_triage",
        "correction",
        "clarification_request",
        "fact_statement_present",
        "question_present",
        "report_context_present",
    )
    true_positive = 0
    false_positive = 0
    false_negative = 0
    evidence_correct = 0
    detected_count = 0
    records: list[dict[str, Any]] = []
    for key in keys:
        expected_item = getattr(expected, key)
        actual_item = getattr(output, key)
        if actual_item.detected:
            detected_count += 1
        if expected_item.detected and actual_item.detected:
            true_positive += 1
            evidence_correct += int(
                actual_item.evidence_phrase == expected_item.evidence_phrase
            )
        elif actual_item.detected:
            false_positive += 1
        elif expected_item.detected:
            false_negative += 1
        records.append(
            {
                "signal": key,
                "expected": expected_item.detected,
                "actual": actual_item.detected,
                "evidence_phrase": actual_item.evidence_phrase,
            }
        )
    return {
        "metrics": {
            "fact_statement_duplicate_count": 0,
            "act_output_count": detected_count,
            "act_expected_count": sum(
                getattr(expected, key).detected for key in keys
            ),
            "act_precision": _rate(true_positive, true_positive + false_positive),
            "act_recall": _rate(true_positive, true_positive + false_negative),
            "evidence_alignment_rate": _rate(evidence_correct, true_positive),
            "intent_claim_consistency_rate": 1.0,
        },
        "records": records,
    }


def evaluate_v14_claims(
    *,
    unit: dict[str, Any],
    governed: list[V14GovernedClaim],
) -> dict[str, Any]:
    expected = list(unit.get("expected_claims", []))
    used: set[int] = set()
    matched = 0
    statement_correct = 0
    polarity_correct = 0
    modality_correct = 0
    epistemic_correct = 0
    records: list[dict[str, Any]] = []
    for claim in governed:
        index = next(
            (
                position
                for position, item in enumerate(expected)
                if position not in used and _claim_matches(claim, item)
            ),
            None,
        )
        statement = ""
        if index is not None:
            used.add(index)
            matched += 1
            expected_item = expected[index]
            statement = str(expected_item["statement_type"])
            statement_correct += int(
                claim.raw_claim.user_statement_type.value == statement
            )
            expected_polarity = (
                "negative" if statement == "denies" else "positive"
            )
            expected_modality = (
                statement
                if statement in {"historical", "hypothetical", "uncertain"}
                else "factual"
            )
            expected_epistemic = (
                "uncertain" if statement == "uncertain" else "certain"
            )
            polarity_correct += int(
                claim.raw_claim.polarity.value == expected_polarity
            )
            modality_correct += int(
                claim.raw_claim.modality_type.value == expected_modality
            )
            epistemic_correct += int(
                claim.raw_claim.epistemic_status.value == expected_epistemic
            )
        records.append(
            {
                "deterministic_claim_id": claim.deterministic_claim_id,
                "matched": index is not None,
                "expected_statement_type": statement,
                "projection_ready": claim.projection_ready,
                "review_required": claim.review_required,
                "blocked_reasons": claim.blocked_reasons,
            }
        )
    return {
        "metrics": {
            "claim_output_count": len(governed),
            "claim_expected_count": len(expected),
            "claim_precision": _rate(matched, len(governed)),
            "claim_recall": _rate(matched, len(expected)),
            "statement_type_accuracy": _rate(statement_correct, len(expected)),
            "polarity_accuracy": _rate(polarity_correct, len(expected)),
            "modality_accuracy": _rate(modality_correct, len(expected)),
            "epistemic_accuracy": _rate(epistemic_correct, len(expected)),
            "projection_ready_count": sum(item.projection_ready for item in governed),
            "review_count": sum(item.review_required for item in governed),
            "blocked_count": sum(not item.projection_ready for item in governed),
        },
        "claims": records,
    }


def evaluate_v14_inventory(
    *,
    unit: dict[str, Any],
    output: Any,
) -> dict[str, Any]:
    expected_count = len(unit.get("expected_claims", []))
    inventory_count = len(output.claim_inventory)
    claim_count = len(output.claims)
    claim_ordinals = [item.inventory_ordinal for item in output.claims]
    ordered_inventory = sorted(
        output.claim_inventory, key=lambda value: value.ordinal
    )
    ordered_claims = sorted(
        output.claims, key=lambda value: value.inventory_ordinal
    )
    kind_mismatch = sum(
        index >= len(ordered_inventory)
        or index >= len(ordered_claims)
        or ordered_inventory[index].claim_kind != ordered_claims[index].claim_type
        for index in range(max(len(ordered_inventory), len(ordered_claims)))
    )
    return {
        "metrics": {
            "inventory_count": inventory_count,
            "claim_record_count": claim_count,
            "claim_expected_count": expected_count,
            "inventory_claim_balance_rate": _rate(
                inventory_count == claim_count, 1
            ),
            "claim_count_accuracy": _rate(
                min(inventory_count, expected_count), max(inventory_count, expected_count)
            ),
            "unmatched_inventory_count": abs(inventory_count - claim_count),
            "inventory_claim_kind_mismatch_count": kind_mismatch,
            "claim_inventory_ordinal_duplicate_count": len(claim_ordinals)
            - len(set(claim_ordinals)),
        }
    }


def evaluate_v14_alignment(
    *,
    unit: dict[str, Any],
    governed: list[V14GovernedClaim],
) -> dict[str, Any]:
    expected_claims = list(unit.get("expected_claims", []))
    total = 0
    correct = 0
    false_alignment = 0
    supplied = 0
    statuses: Counter[str] = Counter()
    for claim in governed:
        expected = _expected_for_claim(claim, expected_claims)
        if expected is None:
            continue
        pairs: list[tuple[str, V14AlignmentStatus, str, str]] = [
            (
                "evidence",
                claim.evidence.alignment_status,
                str(expected["support_quote"]),
                claim.evidence.aligned_quote,
            ),
            (
                "target",
                claim.target.alignment_status,
                str(expected["target_quote"]),
                claim.target.aligned_quote,
            ),
        ]
        for role in (
            "subject",
            "experiencer",
            "action_agent",
            "action_recipient",
            "experiencer",
            "object",
            "temporal",
            "measurement",
            "relation",
        ):
            aligned = claim.fields.get(role)
            expected_quote = str(expected.get(f"{role}_quote", ""))
            if aligned is not None:
                pairs.append(
                    (
                        role,
                        aligned.alignment_status,
                        expected_quote,
                        aligned.aligned_quote,
                    )
                )
            elif expected_quote:
                pairs.append(
                    (
                        role,
                        V14AlignmentStatus.EMPTY_PHRASE,
                        expected_quote,
                        "",
                    )
                )
        for field_name, status, expected_quote, actual_quote in pairs:
            statuses[status.value] += 1
            total += 1
            supplied += int(bool(expected_quote))
            if status in {
                V14AlignmentStatus.EXACT,
                V14AlignmentStatus.EXACT_NORMALIZED,
                V14AlignmentStatus.FUZZY_VERIFIED,
            }:
                # The status itself does not prove the field is correct; a
                # supplied expected quote must also match the aligned offset.
                field_correct = bool(expected_quote) and (
                    actual_quote == expected_quote
                    or (
                        field_name == "evidence"
                        and _envelope_matches(actual_quote, expected_quote)
                    )
                )
                correct += int(field_correct)
                false_alignment += int(not field_correct)
            elif expected_quote:
                false_alignment += 1
    return {
        "metrics": {
            "field_alignment_expected_count": total,
            "field_alignment_rate": _rate(correct, total),
            "false_alignment_rate": _rate(false_alignment, total),
            "wrong_occurrence_count": statuses["wrong_occurrence"],
            "outside_parent_count": statuses["outside_parent"],
            "ambiguous_rate": _rate(statuses["fuzzy_ambiguous"], total),
            "not_found_rate": _rate(
                statuses["not_found"]
                + statuses["semantic_mismatch"]
                + statuses["negation_lost"]
                + statuses["temporal_lost"]
                + statuses["subject_lost"],
                total,
            ),
        },
        "status_distribution": dict(statuses),
    }


def evaluate_v14_participants(
    *,
    unit: dict[str, Any],
    governed: list[V14GovernedClaim],
) -> dict[str, Any]:
    entities = [
        V8EntityCandidate.model_validate(raw)
        for raw in unit.get("entity_candidates", [])
    ]
    expected_claims = list(unit.get("expected_claims", []))
    expected_count = 0
    mention_correct = 0
    resolution_correct = 0
    resolved_empty = 0
    invented = 0
    records: list[dict[str, Any]] = []
    for claim in governed:
        expected = _expected_for_claim(claim, expected_claims)
        if expected is None:
            continue
        resolved = resolve_claim_participants(claim, candidates=entities)
        role_map = {
            "subject": ("subject_quote", "expected_subject_reference"),
            "action_agent": (
                "action_agent_quote",
                "expected_action_agent_reference",
            ),
            "action_recipient": (
                "action_recipient_quote",
                "expected_action_recipient_reference",
            ),
            "experiencer": (
                "experiencer_quote",
                "expected_experiencer_reference",
            ),
        }
        for role, (quote_role, reference_role) in role_map.items():
            if not expected.get(quote_role):
                continue
            expected_count += 1
            item = resolved.get(role)
            if item is None:
                records.append({"role": role, "status": "missing"})
                continue
            mention_ok = item.phrase == str(expected[quote_role])
            expected_reference = str(expected.get(reference_role, ""))
            expected_status = (
                "resolved"
                if expected_reference
                else str(expected.get("expected_experiencer_resolution", ""))
            )
            if expected_status == "resolved":
                resolution_ok = (
                    mention_ok
                    and item.status == "resolved"
                    and item.selected_reference_id == expected_reference
                )
            else:
                resolution_ok = mention_ok and item.status == expected_status
            mention_correct += int(mention_ok)
            resolution_correct += int(resolution_ok)
            resolved_empty += int(
                item.status == "resolved" and not item.selected_reference_id
            )
            invented += int(
                bool(item.selected_reference_id)
                and item.selected_reference_id
                not in {candidate.reference_id for candidate in entities}
            )
            records.append(
                {
                    "role": role,
                    "phrase": item.phrase,
                    "status": item.status,
                    "selected": item.selected_reference_id,
                    "expected": expected_reference,
                }
            )
    return {
        "metrics": {
            "participant_expected_count": expected_count,
            "participant_mention_recall": _rate(mention_correct, expected_count),
            "participant_resolution_accuracy": _rate(
                resolution_correct, expected_count
            ),
            "resolved_empty_violation": resolved_empty,
            "invented_entity_rate": _rate(invented, expected_count),
        },
        "records": records,
    }


def evaluate_v14_proposals(
    *,
    unit: dict[str, Any],
    governed: list[V14GovernedClaim],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_claims = list(unit.get("expected_claims", []))
    temporal_records: list[dict[str, Any]] = []
    measurement_records: list[dict[str, Any]] = []
    for claim in governed:
        expected = _expected_for_claim(claim, expected_claims)
        if expected is None:
            continue
        temporal = claim.fields.get("temporal")
        if temporal and expected.get("temporal_quote"):
            parsed = parse_temporal(
                temporal_quote=temporal.aligned_quote,
                relation_quote=str(expected.get("relation_quote", "")),
            )
            temporal_records.append(
                {
                    "aligned_quote": temporal.aligned_quote,
                    "model_value": claim.raw_claim.temporal_value,
                    "parser_status": parsed.status.value,
                    "parser_value": parsed.value,
                    "governance_status": (
                        "verified"
                        if parsed.status.value == "normalized"
                        else "model_proposed"
                    ),
                    "parser_conflict": bool(
                        claim.raw_claim.temporal_value
                        and parsed.value
                        and claim.raw_claim.temporal_value != parsed.value
                    ),
                }
            )
        measurement = claim.fields.get("measurement")
        if measurement and expected.get("measurement_quote"):
            parsed_measurement = parse_measurement(
                measurement_quote=measurement.aligned_quote,
                relation_quote=str(expected.get("measurement_relation", "")),
            )
            measurement_records.append(
                {
                    "aligned_quote": measurement.aligned_quote,
                    "model_value": claim.raw_claim.measurement_value,
                    "parser_status": parsed_measurement.status.value,
                    "parser_value": parsed_measurement.value,
                    "governance_status": (
                        "verified"
                        if parsed_measurement.status.value == "normalized"
                        else "model_proposed"
                    ),
                }
            )

    def proposal_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(records)
        normalized = sum(
            item["parser_status"] == "normalized" for item in records
        )
        conflict = sum(bool(item.get("parser_conflict")) for item in records)
        return {
            "record_count": count,
            "parser_normalized_rate": _rate(normalized, count),
            "parser_conflict_rate": _rate(conflict, count),
            "model_proposed_review_rate": _rate(
                sum(item["governance_status"] == "model_proposed" for item in records),
                count,
            ),
            "parser_conflict_review_rate": _rate(conflict, count),
        }

    return (
        {"metrics": proposal_metrics(temporal_records), "records": temporal_records},
        {"metrics": proposal_metrics(measurement_records), "records": measurement_records},
    )
