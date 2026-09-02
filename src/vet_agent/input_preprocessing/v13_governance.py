"""Field-level governance and deterministic evaluation for V13 claims."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v13_aligner import V13SourceBlock, align_phrase
from .v13_contracts import (
    V13AlignedEvidence,
    V13AlignmentStatus,
    V13ClaimRecordRaw,
    V13ClaimRecordRawOutput,
    V13ClaimUnitRawOutput,
    V13GovernedClaim,
    V13TurnIntentRawOutput,
)

_OPTIONAL_FIELDS = (
    "subject_phrase",
    "action_agent_phrase",
    "action_recipient_phrase",
    "object_phrase",
    "temporal_phrase",
    "measurement_phrase",
    "relation_phrase",
)
_PARTICIPANT_FIELDS = {
    "subject_phrase",
    "action_agent_phrase",
    "action_recipient_phrase",
}


def _accepted(item: V13AlignedEvidence) -> bool:
    return item.alignment_status in {
        V13AlignmentStatus.EXACT,
        V13AlignmentStatus.EXACT_NORMALIZED,
        V13AlignmentStatus.FUZZY_VERIFIED,
    } and not item.review_required


def _contained(inner: V13AlignedEvidence, outer: V13AlignedEvidence) -> bool:
    return (
        inner.source_block_id == outer.source_block_id
        and inner.start >= outer.start
        and inner.end <= outer.end
    )


def govern_v13_claim(
    raw: V13ClaimRecordRaw,
    *,
    source_id: str,
    blocks: list[V13SourceBlock],
) -> V13GovernedClaim:
    evidence = align_phrase(
        field_name="evidence",
        phrase=raw.evidence_phrase,
        blocks=blocks,
        source_block_id="block-001",
    )
    scope = (
        (evidence.start, evidence.end)
        if _accepted(evidence)
        else None
    )
    target = align_phrase(
        field_name="target",
        phrase=raw.target_phrase,
        blocks=blocks,
        scope=scope,
        source_block_id="block-001",
    )
    fields: dict[str, V13AlignedEvidence] = {}
    for phrase_field in _OPTIONAL_FIELDS:
        phrase = str(getattr(raw, phrase_field))
        if not phrase:
            continue
        aligned = align_phrase(
            field_name=phrase_field.removesuffix("_phrase"),
            phrase=phrase,
            blocks=blocks,
            scope=scope,
            source_block_id="block-001",
        )
        if phrase_field in _PARTICIPANT_FIELDS and not _accepted(aligned):
            # Participants may be elided from the local evidence phrase and
            # refer to an owner occurrence elsewhere in the source.  A repeated
            # exact surface form can still be resolved by TurnContext; it does
            # not become evidence.
            aligned = align_phrase(
                field_name=phrase_field.removesuffix("_phrase"),
                phrase=phrase,
                blocks=blocks,
                scope=None,
                source_block_id="block-001",
            )
        fields[phrase_field] = aligned

    blocked: list[str] = []
    if not _accepted(evidence):
        blocked.append(f"evidence_{evidence.alignment_status.value}")
    if not _accepted(target):
        blocked.append(f"target_{target.alignment_status.value}")
    elif _accepted(evidence) and not _contained(target, evidence):
        blocked.append("target_outside_evidence")
    for name, item in fields.items():
        participant_source_found = (
            name in _PARTICIPANT_FIELDS
            and item.alignment_status
            in {V13AlignmentStatus.EXACT, V13AlignmentStatus.EXACT_NORMALIZED, V13AlignmentStatus.FUZZY_AMBIGUOUS}
            and item.model_phrase
        )
        if not _accepted(item) and not participant_source_found:
            blocked.append(f"{name}_{item.alignment_status.value}")
        elif (
            _accepted(item)
            and name not in _PARTICIPANT_FIELDS
            and _accepted(evidence)
            and not _contained(item, evidence)
        ):
            blocked.append(f"{name}_outside_evidence")

    deterministic_id = hashlib.sha256(
        "\u241f".join(
            (
                source_id,
                str(evidence.start),
                str(evidence.end),
                str(target.start),
                str(target.end),
                raw.claim_type.value,
                raw.user_statement_type.value,
            )
        ).encode("utf-8"),
    ).hexdigest()
    return V13GovernedClaim(
        source_id=source_id,
        deterministic_claim_id=f"{source_id}:v13-{deterministic_id[:24]}",
        raw_claim=raw,
        evidence=evidence,
        target=target,
        fields=fields,
        projection_ready=not blocked,
        review_required=bool(blocked or raw.needs_review),
        blocked_reasons=sorted(set(blocked)),
    )


def govern_v13_output(
    output: V13ClaimRecordRawOutput,
    *,
    source_id: str,
    text: str,
) -> list[V13GovernedClaim]:
    blocks = [V13SourceBlock(source_id, "block-001", text)]
    return [
        govern_v13_claim(raw, source_id=source_id, blocks=blocks)
        for raw in output.claims
    ]


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _evidence_matches(actual: str, expected: str) -> bool:
    """Match a model support envelope to gold support without changing offsets."""

    return bool(actual and expected and (expected in actual or actual in expected))


def _claim_matches(
    item: V13GovernedClaim,
    expected: dict[str, Any],
) -> bool:
    return (
        item.raw_claim.user_statement_type.value == str(expected["statement_type"])
        and item.target.aligned_quote == str(expected["target_quote"])
        and _evidence_matches(
            item.evidence.aligned_quote,
            str(expected["support_quote"]),
        )
    )


def evaluate_claims(
    *,
    unit: dict[str, Any],
    output: V13ClaimRecordRawOutput,
) -> dict[str, Any]:
    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    expected = list(unit.get("expected_claims", []))
    matched = 0
    statement_correct = 0
    polarity_correct = 0
    modality_correct = 0
    epistemic_correct = 0
    denied_as_present = 0
    records: list[dict[str, Any]] = []
    used_expected: set[int] = set()
    for item in governed:
        expected_index = next(
            (
                index
                for index, child in enumerate(expected)
                if index not in used_expected and _claim_matches(item, child)
            ),
            None,
        )
        if expected_index is not None:
            used_expected.add(expected_index)
            matched += 1
            expected_item = expected[expected_index]
            statement_correct += int(
                item.raw_claim.user_statement_type.value
                == str(expected_item["statement_type"])
            )
            expected_statement = str(expected_item["statement_type"])
            expected_polarity = "negative" if expected_statement == "denies" else "positive"
            expected_modality = (
                expected_statement
                if expected_statement in {"historical", "hypothetical", "uncertain"}
                else "factual"
            )
            expected_epistemic = "uncertain" if expected_statement == "uncertain" else "certain"
            polarity_correct += int(
                item.raw_claim.polarity.value == expected_polarity
            )
            modality_correct += int(
                item.raw_claim.modality_type.value == expected_modality
            )
            epistemic_correct += int(
                item.raw_claim.epistemic_status.value == expected_epistemic
            )
            denied_as_present += int(
                expected_statement == "denies"
                and item.raw_claim.user_statement_type.value != "denies"
            )
        records.append(
            {
                "model_unit_id": item.raw_claim.unit_id,
                "deterministic_claim_id": item.deterministic_claim_id,
                "projection_ready": item.projection_ready,
                "review_required": item.review_required,
                "blocked_reasons": item.blocked_reasons,
                "statement_semantics": {
                    "user_statement_type": item.raw_claim.user_statement_type.value,
                    "polarity": item.raw_claim.polarity.value,
                    "modality_type": item.raw_claim.modality_type.value,
                    "epistemic_status": item.raw_claim.epistemic_status.value,
                },
                "evidence": item.evidence.model_dump(mode="json"),
                "target": item.target.model_dump(mode="json"),
                "fields": {
                    name: value.model_dump(mode="json")
                    for name, value in item.fields.items()
                },
            }
        )
    metrics = {
        "claim_output_count": len(governed),
        "claim_expected_count": len(expected),
        "claim_precision": _rate(matched, len(governed)),
        "claim_recall": _rate(matched, len(expected)),
        "statement_type_accuracy": _rate(statement_correct, len(expected)),
        "polarity_accuracy": _rate(polarity_correct, len(expected)),
        "modality_accuracy": _rate(modality_correct, len(expected)),
        "epistemic_accuracy": _rate(epistemic_correct, len(expected)),
        "denied_as_present": denied_as_present,
        "projection_ready_count": sum(item.projection_ready for item in governed),
        "review_count": sum(item.review_required for item in governed),
        "blocked_count": sum(not item.projection_ready for item in governed),
        "projection_consuming_blocked_count": 0,
    }
    return {"metrics": metrics, "claims": records}


def evaluate_segmentation(
    *,
    unit: dict[str, Any],
    output: V13ClaimUnitRawOutput,
) -> dict[str, Any]:
    # Claim unit segmentation is evaluated by evidence scope; ``core_phrase``
    # is a model-facing anchor, not necessarily the eventual thin-claim target
    # (for example, a denial unit may legitimately retain the negation).
    expected_items = list(unit.get("expected_claims", []))
    actual_units = list(output.units)
    blocks = [V13SourceBlock(str(unit["unit_id"]), "block-001", str(unit["user_text"]))]
    aligned_units: list[dict[str, Any]] = []
    matched = 0
    used: set[int] = set()
    for item in actual_units:
        evidence_alignment = align_phrase(
            field_name="unit_evidence",
            phrase=item.evidence_phrase,
            blocks=blocks,
        )
        core_scope = (
            (evidence_alignment.start, evidence_alignment.end)
            if _accepted(evidence_alignment)
            else None
        )
        core_alignment = align_phrase(
            field_name="unit_core",
            phrase=item.core_phrase,
            blocks=blocks,
            scope=core_scope,
        )
        aligned_units.append(
            {
                "raw": item.model_dump(mode="json"),
                "evidence_alignment": evidence_alignment.model_dump(mode="json"),
                "core_alignment": core_alignment.model_dump(mode="json"),
            }
        )
        index = next(
            (
                position
                for position, claim in enumerate(expected_items)
                if position not in used
                and _evidence_matches(
                    evidence_alignment.aligned_quote,
                    str(claim["support_quote"]),
                )
                and (
                    str(claim["target_quote"]) in core_alignment.aligned_quote
                    or core_alignment.aligned_quote in str(claim["target_quote"])
                )
            ),
            None,
        )
        if index is not None:
            used.add(index)
            matched += 1
    return {
        "metrics": {
            "claim_unit_output_count": len(actual_units),
            "claim_unit_expected_count": len(expected_items),
            "claim_unit_precision": _rate(matched, len(actual_units)),
            "claim_unit_recall": _rate(matched, len(expected_items)),
            "over_merge_rate": _rate(
                max(0, len(expected_items) - len(actual_units)),
                len(expected_items),
            ),
            "over_split_rate": _rate(
                max(0, len(actual_units) - len(expected_items)),
                max(1, len(actual_units)),
            ),
            "coverage_gap_explicit_rate": float(
                bool(output.coverage_gap_suspected and output.coverage_gap_reason)
            ),
        },
        "units": aligned_units,
    }


def evaluate_intent(
    *,
    unit: dict[str, Any],
    output: V13TurnIntentRawOutput,
) -> dict[str, Any]:
    blocks = [V13SourceBlock(str(unit["unit_id"]), "block-001", str(unit["user_text"]))]
    expected = Counter(
        (str(act["act_type"]), str(act["evidence_quote"]))
        for act in unit.get("expected_acts", [])
    )
    available = expected.copy()
    matched = 0
    records: list[dict[str, Any]] = []
    aligned_count = 0
    for act in output.acts:
        aligned = align_phrase(
            field_name="intent_evidence",
            phrase=act.evidence_phrase,
            blocks=blocks,
        )
        aligned_ok = _accepted(aligned)
        aligned_count += int(aligned_ok)
        actual = (act.act_type, aligned.aligned_quote)
        hit = available[actual] > 0
        if hit:
            available[actual] -= 1
            matched += 1
        records.append(
            {
                "act_type": act.act_type,
                "model_phrase": act.evidence_phrase,
                "alignment": aligned.model_dump(mode="json"),
                "alignment_accepted": aligned_ok,
                "matched": hit,
            }
        )
    return {
        "metrics": {
            "act_output_count": len(output.acts),
            "act_expected_count": sum(expected.values()),
            "act_precision": _rate(matched, len(output.acts)),
            "act_recall": _rate(matched, sum(expected.values())),
            "evidence_alignment_rate": _rate(
                aligned_count,
                len(records),
            ),
            "empty_act_rate": float(not output.acts),
        },
        "acts": records,
    }


def evaluate_field_alignment(
    *,
    unit: dict[str, Any],
    output: V13ClaimRecordRawOutput,
) -> dict[str, Any]:
    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    statuses: Counter[str] = Counter()
    accepted = 0
    total = 0
    false_alignment = 0
    expected_claims = list(unit.get("expected_claims", []))
    role_to_expected = {
        "subject": "subject_quote",
        "action_agent": "action_agent_quote",
        "action_recipient": "action_recipient_quote",
        "object": "object_quote",
        "temporal": "temporal_quote",
        "measurement": "measurement_quote",
        "relation": "relation_quote",
    }
    for claim in governed:
        candidates = [
            expected
            for expected in expected_claims
            if _evidence_matches(
                claim.evidence.aligned_quote,
                str(expected["support_quote"]),
            )
        ]
        expected_claim = next(
            (
                item
                for item in candidates
                if claim.target.aligned_quote == str(item["target_quote"])
            ),
            None,
        )
        for item in (claim.evidence, claim.target, *claim.fields.values()):
            total += 1
            statuses[item.alignment_status.value] += 1
            accepted += int(_accepted(item))
            if not _accepted(item):
                continue
            if item.field_name == "evidence":
                field_correct = bool(candidates)
            elif item.field_name == "target":
                field_correct = any(
                    item.aligned_quote == str(child["target_quote"])
                    for child in candidates
                )
            else:
                expected_key = role_to_expected.get(item.field_name, "")
                field_correct = bool(
                    expected_claim
                    and expected_key
                    and item.aligned_quote == str(expected_claim.get(expected_key, ""))
                )
            false_alignment += int(not field_correct)
    return {
        "metrics": {
            "field_alignment_rate": _rate(accepted, total),
            "exact_rate": _rate(statuses["exact"], total),
            "exact_normalized_rate": _rate(statuses["exact_normalized"], total),
            "fuzzy_verified_rate": _rate(statuses["fuzzy_verified"], total),
            "fuzzy_ambiguous_rate": _rate(statuses["fuzzy_ambiguous"], total),
            "not_found_rate": _rate(statuses["fuzzy_not_found"], total),
            "false_alignment_rate": _rate(false_alignment, total),
            "review_rate": _rate(
                sum(not item.projection_ready for item in governed),
                len(governed),
            ),
            "field_count": total,
        },
        "status_distribution": dict(statuses),
    }


def evaluate_participants(
    *,
    unit: dict[str, Any],
    output: V13ClaimRecordRawOutput,
) -> dict[str, Any]:
    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    expected_claims = list(unit.get("expected_claims", []))
    role_map = {
        "subject_phrase": ("subject_quote", "expected_subject_reference"),
        "action_agent_phrase": (
            "action_agent_quote",
            "expected_action_agent_reference",
        ),
        "action_recipient_phrase": (
            "action_recipient_quote",
            "expected_action_recipient_reference",
        ),
    }
    entity_matches: dict[str, list[dict[str, Any]]] = {}
    for entity in unit.get("entity_candidates", []):
        surface_forms = {
            str(entity.get("display_name", "")),
            *(str(alias) for alias in entity.get("mention_aliases", [])),
        }
        for surface in surface_forms:
            if surface:
                entity_matches.setdefault(surface, []).append(entity)
    expected_count = 0
    mention_correct = 0
    resolution_correct = 0
    object_expected_count = 0
    object_mention_correct = 0
    resolved_empty = 0
    records: list[dict[str, Any]] = []
    for claim in governed:
        expected = next(
            (
                child
                for child in expected_claims
                if _evidence_matches(
                    claim.evidence.aligned_quote,
                    str(child["support_quote"]),
                )
            ),
            None,
        )
        if expected is None:
            continue
        for phrase_role, (quote_role, reference_role) in role_map.items():
            if not expected.get(quote_role):
                continue
            expected_count += 1
            aligned = claim.fields.get(phrase_role)
            mention_ok = aligned is not None and (
                aligned.aligned_quote == str(
                expected[quote_role]
                )
                or aligned.model_phrase == str(expected[quote_role])
            )
            mention_correct += int(mention_ok)
            mention = (
                aligned.aligned_quote
                if aligned is not None and aligned.aligned_quote
                else str(getattr(claim.raw_claim, phrase_role))
            )
            matched_entities = entity_matches.get(mention, [])
            selected_reference = (
                str(matched_entities[0].get("reference_id", ""))
                if len(matched_entities) == 1
                else ""
            )
            resolution_status = (
                "resolved"
                if selected_reference
                else "ambiguous"
                if len(matched_entities) > 1
                else "missing"
            )
            expected_reference = str(expected.get(reference_role, ""))
            resolution_ok = mention_ok and selected_reference == expected_reference
            resolution_correct += int(resolution_ok)
            resolved_empty += int(resolution_status == "resolved" and not selected_reference)
            records.append(
                {
                    "role": phrase_role,
                    "model_phrase": getattr(claim.raw_claim, phrase_role),
                    "aligned_quote": aligned.aligned_quote if aligned else "",
                    "mention_correct": mention_ok,
                    "resolution_status": resolution_status,
                    "selected_reference": selected_reference,
                    "expected_reference": expected_reference,
                }
            )
        if expected.get("object_quote"):
            aligned = claim.fields.get("object_phrase")
            object_expected_count += 1
            object_ok = aligned is not None and (
                aligned.aligned_quote == str(expected["object_quote"])
                or aligned.model_phrase == str(expected["object_quote"])
            )
            object_mention_correct += int(object_ok)
            records.append(
                {
                    "role": "object_phrase",
                    "model_phrase": claim.raw_claim.object_phrase,
                    "aligned_quote": aligned.aligned_quote if aligned else "",
                    "mention_correct": object_ok,
                    "resolution_status": "not_entity_resolved",
                }
            )
    return {
        "metrics": {
            "participant_expected_count": expected_count,
            "participant_mention_recall": _rate(mention_correct, expected_count),
            "participant_resolution_accuracy": _rate(
                resolution_correct,
                expected_count,
            ),
            "invented_entity_rate": 0.0,
            "resolved_empty_violation": resolved_empty,
            "object_expected_count": object_expected_count,
            "object_mention_accuracy": _rate(
                object_mention_correct,
                object_expected_count,
            ),
        },
        "records": records,
    }


def evaluate_proposals(
    *,
    unit: dict[str, Any],
    output: V13ClaimRecordRawOutput,
) -> tuple[dict[str, Any], dict[str, Any]]:
    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    expected_claims = list(unit.get("expected_claims", []))
    temporal_records: list[dict[str, Any]] = []
    measurement_records: list[dict[str, Any]] = []
    for claim in governed:
        expected = next(
            (
                child
                for child in expected_claims
                if _evidence_matches(
                    claim.evidence.aligned_quote,
                    str(child["support_quote"]),
                )
            ),
            None,
        )
        if expected is None:
            continue
        temporal = claim.fields.get("temporal_phrase")
        if temporal and expected.get("temporal_quote"):
            parsed = parse_temporal(temporal_quote=temporal.aligned_quote)
            temporal_records.append(
                {
                    "aligned_quote": temporal.aligned_quote,
                    "model_value": claim.raw_claim.temporal_value,
                    "parser_status": parsed.status.value,
                    "parser_value": parsed.value,
                    "parser_conflict": bool(claim.raw_claim.temporal_value)
                    and bool(parsed.value)
                    and claim.raw_claim.temporal_value != parsed.value,
                }
            )
        measurement = claim.fields.get("measurement_phrase")
        if measurement and expected.get("measurement_quote"):
            parsed_measurement = parse_measurement(
                measurement_quote=measurement.aligned_quote
            )
            measurement_records.append(
                {
                    "aligned_quote": measurement.aligned_quote,
                    "model_value": claim.raw_claim.measurement_value,
                    "parser_status": parsed_measurement.status.value,
                    "parser_value": parsed_measurement.value,
                    "parser_unit": parsed_measurement.unit,
                    "parser_conflict": bool(claim.raw_claim.measurement_value)
                    and bool(parsed.value)
                    and claim.raw_claim.measurement_value != parsed.value,
                }
            )
    temporal_metrics = {
        "record_count": len(temporal_records),
        "parser_normalized_rate": _rate(
            sum(item["parser_status"] == "normalized" for item in temporal_records),
            len(temporal_records),
        ),
        "parser_conflict_rate": _rate(
            sum(item["parser_conflict"] for item in temporal_records),
            len(temporal_records),
        ),
        "unresolved_rate": _rate(
            sum(item["parser_status"] == "unresolved" for item in temporal_records),
            len(temporal_records),
        ),
    }
    measurement_metrics = {
        "record_count": len(measurement_records),
        "parser_normalized_rate": _rate(
            sum(item["parser_status"] == "normalized" for item in measurement_records),
            len(measurement_records),
        ),
        "parser_conflict_rate": _rate(
            sum(item["parser_conflict"] for item in measurement_records),
            len(measurement_records),
        ),
        "unresolved_rate": _rate(
            sum(item["parser_status"] == "unresolved" for item in measurement_records),
            len(measurement_records),
        ),
    }
    return temporal_metrics, measurement_metrics
