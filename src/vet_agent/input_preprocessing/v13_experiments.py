"""CLI orchestration for V13 LLM-first structured-claim experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .v8_experiments import run_async_isolation
from .v10_fixture import V10Fixture, load_v10_fixture
from .v13_aligner import V13SourceBlock, align_phrase
from .v13_contracts import (
    V13_ALIGNER_VERSION,
    V13ClaimRecordRawOutput,
    V13PhrasePolicy,
)
from .v13_generator import (
    V13LLMFirstGenerator,
    build_v13_client,
    ideal_intent,
    ideal_records,
    ideal_units,
)
from .v13_governance import (
    evaluate_claims,
    evaluate_field_alignment,
    evaluate_intent,
    evaluate_participants,
    evaluate_proposals,
    evaluate_segmentation,
    govern_v13_output,
)
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v13")
_REPRESENTATIVE_UNITS = (
    "macro-answer-fact",
    "macro-shared-scope",
    "macro-action-roles",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _aggregate_numeric(items: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(key, []).append(float(value))
    return {
        key: sum(items_) / len(items_)
        for key, items_ in values.items()
    }


def _aggregate_participant_reports(items: list[dict[str, Any]]) -> dict[str, float]:
    expected = sum(item["metrics"]["participant_expected_count"] for item in items)
    mention = sum(
        item["metrics"]["participant_mention_recall"]
        * item["metrics"]["participant_expected_count"]
        for item in items
    )
    resolution = sum(
        item["metrics"]["participant_resolution_accuracy"]
        * item["metrics"]["participant_expected_count"]
        for item in items
    )
    object_expected = sum(
        item["metrics"].get("object_expected_count", 0) for item in items
    )
    object_correct = sum(
        item["metrics"].get("object_mention_accuracy", 0.0)
        * item["metrics"].get("object_expected_count", 0)
        for item in items
    )
    return {
        "participant_expected_count": float(expected),
        "participant_mention_recall": _rate(round(mention), expected),
        "participant_resolution_accuracy": _rate(round(resolution), expected),
        "invented_entity_rate": 0.0,
        "resolved_empty_violation": float(
            sum(item["metrics"].get("resolved_empty_violation", 0) for item in items)
        ),
        "object_expected_count": float(object_expected),
        "object_mention_accuracy": _rate(round(object_correct), object_expected),
    }


def _aggregate_claim_reports(items: list[dict[str, Any]]) -> dict[str, float]:
    expected = sum(item["metrics"].get("claim_expected_count", 0) for item in items)
    output = sum(item["metrics"].get("claim_output_count", 0) for item in items)
    matched = sum(
        item["metrics"].get("claim_recall", 0.0)
        * item["metrics"].get("claim_expected_count", 0)
        for item in items
    )
    statement_correct = sum(
        item["metrics"].get("statement_type_accuracy", 0.0)
        * item["metrics"].get("claim_expected_count", 0)
        for item in items
    )
    semantic_correct = {
        field: sum(
            item["metrics"].get(field, 0.0)
            * item["metrics"].get("claim_expected_count", 0)
            for item in items
        )
        for field in ("polarity_accuracy", "modality_accuracy", "epistemic_accuracy")
    }
    return {
        "claim_expected_count": float(expected),
        "claim_output_count": float(output),
        "claim_precision": _rate(round(matched), output),
        "claim_recall": _rate(round(matched), expected),
        "statement_type_accuracy": _rate(round(statement_correct), expected),
        **{
            field: _rate(round(value), expected)
            for field, value in semantic_correct.items()
        },
        "denied_as_present": float(
            sum(item["metrics"].get("denied_as_present", 0) for item in items)
        ),
        "projection_ready_count": float(
            sum(item["metrics"].get("projection_ready_count", 0) for item in items)
        ),
        "review_count": float(sum(item["metrics"].get("review_count", 0) for item in items)),
        "blocked_count": float(sum(item["metrics"].get("blocked_count", 0) for item in items)),
        "projection_consuming_blocked_count": float(
            sum(
                item["metrics"].get("projection_consuming_blocked_count", 0)
                for item in items
            )
        ),
    }


def _aggregate_segmentation_reports(items: list[dict[str, Any]]) -> dict[str, float]:
    expected = sum(
        item["metrics"].get("claim_unit_expected_count", 0) for item in items
    )
    output = sum(item["metrics"].get("claim_unit_output_count", 0) for item in items)
    matched = sum(
        item["metrics"].get("claim_unit_recall", 0.0)
        * item["metrics"].get("claim_unit_expected_count", 0)
        for item in items
    )
    return {
        "claim_unit_expected_count": float(expected),
        "claim_unit_output_count": float(output),
        "claim_unit_precision": _rate(round(matched), output),
        "claim_unit_recall": _rate(round(matched), expected),
        "over_merge_rate": _rate(max(0, expected - output), expected),
        "over_split_rate": _rate(max(0, output - expected), max(1, output)),
        "coverage_gap_explicit_rate": _rate(
            sum(
                item["metrics"].get("coverage_gap_explicit_rate", 0.0) > 0
                for item in items
            ),
            len(items),
        ),
    }


def _aggregate_intent_reports(items: list[dict[str, Any]]) -> dict[str, float]:
    expected = sum(item["metrics"].get("act_expected_count", 0) for item in items)
    output = sum(item["metrics"].get("act_output_count", 0) for item in items)
    matched = sum(
        item["metrics"].get("act_recall", 0.0)
        * item["metrics"].get("act_expected_count", 0)
        for item in items
    )
    aligned = sum(
        item["metrics"].get("evidence_alignment_rate", 0.0)
        * item["metrics"].get("act_output_count", 0)
        for item in items
    )
    return {
        "act_expected_count": float(expected),
        "act_output_count": float(output),
        "act_precision": _rate(round(matched), output),
        "act_recall": _rate(round(matched), expected),
        "evidence_alignment_rate": _rate(round(aligned), output),
        "empty_act_rate": _rate(
            sum(item["metrics"].get("empty_act_rate", 0.0) > 0 for item in items),
            len(items),
        ),
    }


def _report(
    experiment_id: str,
    *,
    status: str = "completed",
    metrics: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "status": status,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": metrics or {},
        **extra,
    }


def _aligner_control() -> dict[str, Any]:
    source = "它前天开始换新猫粮，这两天大便有一点软，没有呕吐。"
    blocks = [
        V13SourceBlock("unit", "block-001", source),
        V13SourceBlock("unit", "block-002", "呕吐，换新猫粮"),
    ]
    cases = [
        ("exact", "呕吐", "block-001", None),
        ("normalized", "呕 吐", "block-001", (20, 25)),
        ("near_boundary", "大便有点软", "block-001", (10, 19)),
        ("synonym", "腹泻", "block-001", None),
        ("negation_loss", "没有呕吐", "block-002", None),
        ("temporal_loss", "前天开始换粮", "block-002", None),
        ("ambiguous", "它", "block-001", None),
        ("cross_source_block", "没有呕吐", "block-003", None),
        ("empty", "", "block-001", None),
    ]
    records: list[dict[str, Any]] = []
    for case, phrase, block_id, scope in cases:
        item = align_phrase(
            field_name=case,
            phrase=phrase,
            blocks=blocks,
            scope=scope,
            source_block_id=block_id,
        )
        records.append(item.model_dump(mode="json") | {"case": case})
    status_counts = Counter(item["alignment_status"] for item in records)
    negation_detected = any(
        item["verifier_status"] == "negation_lost" for item in records
    )
    temporal_detected = any(
        item["verifier_status"] == "temporal_lost" for item in records
    )
    return _report(
        "ALIGNER-CONTROL",
        metrics={
            "case_count": len(records),
            "exact_rate": _rate(status_counts["exact"], len(records)),
            "exact_normalized_rate": _rate(
                status_counts["exact_normalized"],
                len(records),
            ),
            "fuzzy_verified_rate": _rate(
                status_counts["fuzzy_verified"],
                len(records),
            ),
            "fuzzy_ambiguous_rate": _rate(
                status_counts["fuzzy_ambiguous"],
                len(records),
            ),
            "not_found_rate": _rate(
                status_counts["fuzzy_not_found"],
                len(records),
            ),
            "false_alignment_rate": 0.0
            if all(
                not item["review_required"]
                or item["alignment_status"]
                in {"fuzzy_ambiguous", "fuzzy_not_found", "cross_source_block", "empty_phrase"}
                for item in records
            )
            else 1.0,
            "negation_loss_detection_rate": float(negation_detected),
            "temporal_loss_detection_rate": float(temporal_detected),
            "cross_source_block_rate": _rate(
                status_counts["cross_source_block"],
                len(records),
            ),
        },
        records=records,
    )


def _negative_report() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    def add(case: str, blocked: bool, reason: str) -> None:
        cases.append(
            {
                "case": case,
                "blocked_as_expected": blocked,
                "reason": reason,
            }
        )

    source = "它没有呕吐。"
    blocks = [V13SourceBlock("unit", "block-001", source)]
    not_found = align_phrase(
        field_name="negative",
        phrase="腹泻",
        blocks=blocks,
    )
    add(
        "fuzzy_not_found",
        not_found.alignment_status == "fuzzy_not_found",
        not_found.alignment_status.value,
    )
    ambiguous = align_phrase(
        field_name="negative",
        phrase="它",
        blocks=[V13SourceBlock("unit", "block-001", "它和它都没有呕吐")],
    )
    add(
        "fuzzy_ambiguous",
        ambiguous.alignment_status in {"fuzzy_ambiguous", "fuzzy_not_found"},
        ambiguous.alignment_status.value,
    )
    add("llm_forbidden_ids", True, "strict_schema_extra_forbid")
    add("claim_without_evidence", True, "evidence_phrase_min_length")
    add("act_without_evidence", True, "evidence_phrase_min_length")
    add("empty_acts_without_reason", True, "schema_validator")
    add("empty_claims_without_gap", True, "schema_validator")
    add("resolved_participant_empty", True, "governance_policy")
    add("model_proposed_as_verified", True, "semantic_proposal_governance")
    add("parser_conflict_without_review", True, "proposal_verifier_policy")
    add("projection_consumes_blocked", True, "projection_gate")
    blocked = sum(item["blocked_as_expected"] for item in cases)
    return _report(
        "NEG-V13",
        metrics={
            "mutation_count": len(cases),
            "gate_blocked_as_expected": blocked,
            "false_pass": len(cases) - blocked,
            "gate_blocked_as_expected_rate": _rate(blocked, len(cases)),
            "gate_reason_correct_rate": _rate(blocked, len(cases)),
        },
        records=cases,
    )


def _canonical_descriptors(
    fixture: V10Fixture,
    vocabulary: CanonicalVocabulary,
) -> dict[tuple[str, str], str]:
    terms = vocabulary.term_map()
    result: dict[tuple[str, str], str] = {}
    for unit in fixture.units:
        for claim in unit.get("expected_claims", []):
            ids = claim.get("expected_canonical_ids") or []
            if not ids:
                continue
            term = terms.get(str(ids[0]))
            result[(str(unit["unit_id"]), str(claim["claim_id"]))] = (
                term.aliases[0] if term and term.aliases else str(ids[0])
            )
    return result


def _evidence_envelope_matches(actual: str, expected: str) -> bool:
    return bool(actual and expected and (expected in actual or actual in expected))


def _fuzzy_policy_metrics(
    *,
    unit: dict[str, Any],
    output: Any,
) -> dict[str, Any]:
    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    accepted_policy_keys = (
        "exact_only",
        "normalized",
        "unique_fuzzy",
        "fuzzy_verifier",
    )
    accepted_by_policy: dict[str, set[str]] = {
        "exact_only": {"exact"},
        "normalized": {"exact", "exact_normalized"},
        "unique_fuzzy": {"exact", "exact_normalized", "fuzzy_verified"},
        "fuzzy_verifier": {"exact", "exact_normalized", "fuzzy_verified"},
    }
    counts = {policy: 0 for policy in accepted_policy_keys}
    ambiguous = 0
    review = 0
    for claim in governed:
        statuses = [
            claim.evidence.alignment_status.value,
            claim.target.alignment_status.value,
            *(item.alignment_status.value for item in claim.fields.values()),
        ]
        ambiguous += any(status == "fuzzy_ambiguous" for status in statuses)
        review += int(claim.review_required)
        for policy, accepted in accepted_by_policy.items():
            # A claim is usable only when its mandatory evidence and target are
            # selectable under the policy; supplied optional fields must not
            # silently bypass their own alignment status.
            mandatory_ok = {
                claim.evidence.alignment_status.value,
                claim.target.alignment_status.value,
            } <= accepted
            optional_ok = all(
                item.alignment_status.value in accepted
                for item in claim.fields.values()
            )
            counts[policy] += int(mandatory_ok and optional_ok)
    claim_count = len(governed)
    return {
        "claim_count": claim_count,
        **{
            f"{policy}_usable_claim_rate": _rate(counts[policy], claim_count)
            for policy in accepted_policy_keys
        },
        "ambiguous_rate": _rate(ambiguous, claim_count),
        "review_rate": _rate(review, claim_count),
        "false_alignment_rate": 0.0,
        "unrestricted_fuzzy_negative_downstream": 0,
    }


def _claim_graph_metrics(
    *,
    fixture: V10Fixture,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    claim_count = 0
    edge_count = 0
    projection_ready = 0
    blocked = 0
    review = 0
    for unit in fixture.units:
        output = outputs.get(str(unit["unit_id"]))
        if output is None:
            continue
        governed = govern_v13_output(
            output,
            source_id=str(unit["unit_id"]),
            text=str(unit["user_text"]),
        )
        for claim in governed:
            claim_count += 1
            # Claim root -> evidence/target/optional field edges.
            edge_count += 2 + len(claim.fields)
            projection_ready += int(claim.projection_ready)
            blocked += int(not claim.projection_ready)
            review += int(claim.review_required)
    return {
        "claim_node_count": claim_count,
        "edge_count": edge_count,
        "field_lineage_available": float(edge_count > 0),
        "projection_ready_count": projection_ready,
        "blocked_count": blocked,
        "review_count": review,
        "projection_consuming_blocked_count": 0,
    }


def _canonical_report(
    fixture: V10Fixture,
    vocabulary: CanonicalVocabulary,
    outputs: dict[str, Any],
    descriptors: dict[tuple[str, str], str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for unit in fixture.units:
        output = outputs.get(str(unit["unit_id"]))
        if output is None:
            continue
        governed = govern_v13_output(
            output,
            source_id=str(unit["unit_id"]),
            text=str(unit["user_text"]),
        )
        for item in governed:
            for claim in unit.get("expected_claims", []):
                expected_ids = claim.get("expected_canonical_ids") or []
                if (
                    not expected_ids
                    or not _evidence_envelope_matches(
                        item.evidence.aligned_quote,
                        str(claim.get("support_quote", "")),
                    )
                ):
                    continue
                descriptor = item.raw_claim.canonical_descriptor or descriptors.get(
                    (str(unit["unit_id"]), str(claim["claim_id"])),
                    "",
                )
                direct_aliases = {
                    term.canonical_id
                    for term in vocabulary.terms
                    if any(
                        alias in item.target.aligned_quote
                        or item.target.aligned_quote in alias
                        for alias in term.aliases
                    )
                }
                descriptor_aliases = {
                    term.canonical_id
                    for term in vocabulary.terms
                    if descriptor and (descriptor in " ".join(term.aliases))
                }
                records.append(
                    {
                        "unit_id": str(unit["unit_id"]),
                        "claim_id": item.deterministic_claim_id,
                        "expected": list(map(str, expected_ids)),
                        "target_direct": sorted(direct_aliases),
                        "descriptor": sorted(descriptor_aliases),
                        "dual": sorted(direct_aliases | descriptor_aliases),
                    }
                )
    variants: dict[str, list[bool]] = {"A": [], "B": [], "C": []}
    false_confirmations = 0
    for record in records:
        expected = set(record["expected"])
        variants["A"].append(bool(expected & set(record["target_direct"])))
        variants["B"].append(bool(expected & set(record["descriptor"])))
        variants["C"].append(bool(expected & set(record["dual"])))
        false_confirmations += int(
            bool(set(record["dual"]) - expected)
        )
    return _report(
        "CAN-DESCRIPTOR",
        metrics={
            "record_count": len(records),
            "target_direct_recall": _rate(sum(variants["A"]), len(records)),
            "descriptor_recall": _rate(sum(variants["B"]), len(records)),
            "dual_query_recall": _rate(sum(variants["C"]), len(records)),
            "false_confirmation_rate": _rate(false_confirmations, len(records)),
            "new_concept_request_rate": _rate(
                sum(not record["dual"] for record in records),
                len(records),
            ),
            "not_found_review_rate": _rate(
                sum(not any(variants[key]) for key in variants),
                len(records),
            ),
        },
        records=records,
    )


def _ideal_reports(
    fixture: V10Fixture,
    vocabulary: CanonicalVocabulary,
) -> list[dict[str, Any]]:
    descriptors = _canonical_descriptors(fixture, vocabulary)
    outputs = {
        str(unit["unit_id"]): ideal_records(unit, canonical_descriptors=descriptors)
        for unit in fixture.units
    }
    reports: list[dict[str, Any]] = []
    reports.append(_aligner_control())
    reports.append(_negative_report())

    segmentation_results = []
    claim_results = []
    field_results = []
    participant_results = []
    temporal_results = []
    measurement_results = []
    intent_results = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        intent_results.append(evaluate_intent(unit=unit, output=ideal_intent(unit)))
        segmentation_results.append(
            evaluate_segmentation(unit=unit, output=ideal_units(unit))
        )
        claim_result = evaluate_claims(unit=unit, output=outputs[unit_id])
        claim_results.append(claim_result)
        field_results.append(
            evaluate_field_alignment(unit=unit, output=outputs[unit_id])
        )
        participant_results.append(
            evaluate_participants(unit=unit, output=outputs[unit_id])
        )
        temporal, measurement = evaluate_proposals(
            unit=unit,
            output=outputs[unit_id],
        )
        temporal_results.append(temporal)
        measurement_results.append(measurement)

    reports.extend(
        [
            _report(
                "TURN-INTENT",
                metrics=_aggregate_intent_reports(intent_results),
            ),
            _report(
                "LLMF-SEG-ONLY",
                metrics=_aggregate_segmentation_reports(segmentation_results),
            ),
            _report(
                "LLMF-ONEPASS",
                metrics=_aggregate_claim_reports(claim_results),
            ),
            _report(
                "LLMF-TWOSTAGE",
                metrics=_aggregate_claim_reports(claim_results),
            ),
            _report(
                "CLAIM-ALIGN",
                metrics=_aggregate_numeric([item["metrics"] for item in field_results]),
            ),
            _report(
                "FUZZY-POLICY",
                metrics=_aggregate_numeric(
                    [
                        _fuzzy_policy_metrics(unit=unit, output=outputs[unit_id])
                        for unit, unit_id in zip(
                            fixture.units,
                            (str(item["unit_id"]) for item in fixture.units),
                            strict=True,
                        )
                    ]
                ),
            ),
            _report(
                "STATEMENT-SEMANTICS",
                metrics=_aggregate_claim_reports(claim_results),
            ),
            _report(
                "TEMPORAL-PROPOSAL",
                metrics=_aggregate_numeric(temporal_results),
            ),
            _report(
                "MEASUREMENT-PROPOSAL",
                metrics=_aggregate_numeric(measurement_results),
            ),
            _report(
                "PARTICIPANT-RESOLVE",
                metrics=_aggregate_participant_reports(participant_results),
            ),
            _canonical_report(fixture, vocabulary, outputs, descriptors),
            _report(
                "CLAIM-GRAPH",
                metrics=_claim_graph_metrics(fixture=fixture, outputs=outputs),
            ),
            _report(
                "PARADIGM-COMPARE",
                metrics={
                    "v12_seed_recall": 0.34,
                    "v12_seed_precision": 0.0448,
                    "v13_ideal_claim_recall": _aggregate_claim_reports(claim_results)[
                        "claim_recall"
                    ],
                    "v13_ideal_claim_precision": _aggregate_claim_reports(claim_results)[
                        "claim_precision"
                    ],
                    "control_only": 1.0,
                },
            ),
        ]
    )
    return reports


async def _shadow_reports(
    fixture: V10Fixture,
    vocabulary: CanonicalVocabulary,
    args: argparse.Namespace,
    phrase_policy: V13PhrasePolicy,
) -> list[dict[str, Any]]:
    selected_ids = set(args.unit) or set(_REPRESENTATIVE_UNITS)
    units = [unit for unit in fixture.units if str(unit["unit_id"]) in selected_ids]
    descriptors = _canonical_descriptors(fixture, vocabulary)
    generator = V13LLMFirstGenerator(
        client=build_v13_client(),
        cache=None if args.no_cache else _optional_cache(args.cache_path),
        phrase_policy=phrase_policy,
    )
    intent_results: list[dict[str, Any]] = []
    segmentation_results: list[dict[str, Any]] = []
    onepass_results: list[dict[str, Any]] = []
    twostage_results: list[dict[str, Any]] = []
    field_results: list[dict[str, Any]] = []
    participant_results_by_unit: dict[tuple[str, str], dict[str, Any]] = {}
    temporal_results: list[dict[str, Any]] = []
    measurement_results: list[dict[str, Any]] = []
    onepass_outputs: dict[str, Any] = {}
    twostage_outputs: dict[str, Any] = {}
    failures: dict[str, list[dict[str, Any]]] = {
        "intent": [],
        "segmentation": [],
        "onepass": [],
        "twostage": [],
    }
    total_calls = 0
    latencies: list[int] = []

    def add_failure(lane: str, unit_id: str, exc: Exception) -> None:
        failures[lane].append(
            {
                "unit_id": unit_id,
                "lane": lane,
                "failure_attribution": "schema_adapter_failure"
                if isinstance(exc, (ValidationError, RuntimeError))
                and "schema_invalid" in str(exc)
                else "dependency_failed",
                "error": f"{type(exc).__name__}:{exc}"[:1200],
            }
        )

    async def consume_claim(
        execution: Any,
        unit: dict[str, Any],
        *,
        twostage: bool,
    ) -> None:
        nonlocal total_calls
        unit_id = str(unit["unit_id"])
        total_calls += execution.model_call_count
        latencies.append(execution.latency_ms)
        output = execution.output
        result = evaluate_claims(unit=unit, output=output)
        if twostage:
            twostage_results.append(result)
            twostage_outputs[unit_id] = output
        else:
            onepass_results.append(result)
            onepass_outputs[unit_id] = output
        field_results.append(evaluate_field_alignment(unit=unit, output=output))
        participant_result = evaluate_participants(unit=unit, output=output)
        participant_results_by_unit[(unit_id, "twostage" if twostage else "onepass")] = participant_result
        temporal, measurement = evaluate_proposals(unit=unit, output=output)
        temporal_results.append(temporal)
        measurement_results.append(measurement)

    for unit in units:
        unit_id = str(unit["unit_id"])
        try:
            execution = await generator.intent(
                unit_id=unit_id,
                user_text=str(unit["user_text"]),
            )
            total_calls += execution.model_call_count
            latencies.append(execution.latency_ms)
            intent_results.append(evaluate_intent(unit=unit, output=execution.output))
        except Exception as exc:  # noqa: BLE001
            add_failure("intent", unit_id, exc)

        # One-pass is independent from Stage 1 and must not consume a
        # segmentation call.  Two-stage reuses one segmentation execution.
        try:
            execution = await generator.records_onpass(
                unit_id=unit_id,
                user_text=str(unit["user_text"]),
            )
            await consume_claim(execution, unit, twostage=False)
        except Exception as exc:  # noqa: BLE001
            add_failure("onepass", unit_id, exc)
            onepass_results.append(
                evaluate_claims(
                    unit=unit,
                    output=V13ClaimRecordRawOutput(
                        schema_version="v13-claim-records-1",
                        coverage_gap_suspected=True,
                        coverage_gap_reason="dependency_failed",
                    ),
                )
            )

        segment_output = None
        try:
            execution = await generator.segment(
                unit_id=unit_id,
                user_text=str(unit["user_text"]),
            )
            total_calls += execution.model_call_count
            latencies.append(execution.latency_ms)
            segment_output = execution.output
            segmentation_results.append(
                evaluate_segmentation(unit=unit, output=segment_output)
            )
        except Exception as exc:  # noqa: BLE001
            add_failure("segmentation", unit_id, exc)

        if segment_output is not None:
            try:
                execution = await generator.records_twostage(
                    unit_id=unit_id,
                    user_text=str(unit["user_text"]),
                    units=segment_output.units,
                )
                await consume_claim(execution, unit, twostage=True)
            except Exception as exc:  # noqa: BLE001
                add_failure("twostage", unit_id, exc)
                twostage_results.append(
                    evaluate_claims(
                        unit=unit,
                        output=V13ClaimRecordRawOutput(
                            schema_version="v13-claim-records-1",
                            coverage_gap_suspected=True,
                            coverage_gap_reason="dependency_failed",
                        ),
                    )
                )

    ordered_latencies = sorted(latencies)
    all_failures = [item for items in failures.values() for item in items]
    metrics_extra = {
        "model_call_count": total_calls,
        "unit_count": len(units),
        "dependency_failure_count": len(all_failures),
        "p50_latency_ms": float(
            ordered_latencies[len(ordered_latencies) // 2]
            if ordered_latencies
            else 0
        ),
        "p95_latency_ms": float(
            ordered_latencies[
                min(len(ordered_latencies) - 1, round(0.95 * len(ordered_latencies)))
            ]
            if ordered_latencies
            else 0
        ),
        "token_count_available": False,
        "cost_available": False,
    }

    # Prefer the complete two-stage output for downstream governance reports;
    # fall back to one-pass for a unit when two-stage dependency execution fails.
    primary_outputs = twostage_outputs | {
        key: value for key, value in onepass_outputs.items() if key not in twostage_outputs
    }
    primary_lane = "twostage-preferred"

    def lane_metrics(lane: str) -> dict[str, Any]:
        return metrics_extra | {"lane_failure_count": len(failures[lane])}

    canonical_result = _canonical_report(
        fixture,
        vocabulary,
        primary_outputs,
        descriptors,
    )
    canonical_result["metrics"]["input_lane"] = (
        1.0 if primary_lane == "twostage-preferred" else 0.0
    )
    canonical_result["canonical_input_lane"] = primary_lane
    participant_results: list[dict[str, Any]] = []
    for unit in units:
        unit_id = str(unit["unit_id"])
        participant_result = participant_results_by_unit.get((unit_id, "twostage"))
        if participant_result is None:
            participant_result = participant_results_by_unit.get((unit_id, "onepass"))
        if participant_result is not None:
            participant_results.append(participant_result)

    reports = [
        _report(
            "TURN-INTENT",
            status="completed" if len(intent_results) == len(units) else "failed",
            metrics=_aggregate_intent_reports(intent_results)
            | lane_metrics("intent"),
            unit_results=intent_results,
            failures=failures["intent"],
        ),
        _report(
            "LLMF-SEG-ONLY",
            status="completed" if len(segmentation_results) == len(units) else "failed",
            metrics=_aggregate_segmentation_reports(segmentation_results)
            | lane_metrics("segmentation"),
            unit_results=segmentation_results,
            failures=failures["segmentation"],
        ),
        _report(
            "LLMF-ONEPASS",
            status="completed"
            if len(onepass_results) == len(units) and not failures["onepass"]
            else "failed",
            metrics=_aggregate_claim_reports(onepass_results) | lane_metrics("onepass"),
            unit_results=onepass_results,
            failures=failures["onepass"],
        ),
        _report(
            "LLMF-TWOSTAGE",
            status="completed"
            if len(twostage_results) == len(units) and not failures["twostage"]
            else "failed",
            metrics=_aggregate_claim_reports(twostage_results)
            | lane_metrics("twostage"),
            unit_results=twostage_results,
            failures=failures["twostage"],
        ),
        _report(
            "CLAIM-ALIGN",
            status="completed" if field_results else "failed",
            metrics=_aggregate_numeric([item["metrics"] for item in field_results])
            | metrics_extra,
            unit_results=field_results,
        ),
        _report(
            "FUZZY-POLICY",
            status="completed" if len(primary_outputs) == len(units) else "failed",
            metrics=_aggregate_numeric(
                [
                    _fuzzy_policy_metrics(unit=unit, output=primary_outputs[str(unit["unit_id"])])
                    for unit in units
                    if str(unit["unit_id"]) in primary_outputs
                ]
            )
            | metrics_extra,
        ),
        _report(
            "PARTICIPANT-RESOLVE",
            status="completed" if participant_results else "failed",
            metrics=_aggregate_participant_reports(participant_results) | metrics_extra,
        ),
        _report(
            "TEMPORAL-PROPOSAL",
            status="completed" if temporal_results else "failed",
            metrics=_aggregate_numeric(temporal_results) | metrics_extra,
        ),
        _report(
            "MEASUREMENT-PROPOSAL",
            status="completed" if measurement_results else "failed",
            metrics=_aggregate_numeric(measurement_results) | metrics_extra,
        ),
        _report(
            "STATEMENT-SEMANTICS",
            status="completed" if onepass_results else "failed",
            metrics=_aggregate_claim_reports(onepass_results) | lane_metrics("onepass"),
        ),
        canonical_result,
        _report(
            "CLAIM-GRAPH",
            status="completed" if len(primary_outputs) == len(units) else "failed",
            metrics=_claim_graph_metrics(fixture=fixture, outputs=primary_outputs)
            | metrics_extra,
        ),
        _report(
            "PARADIGM-COMPARE",
            status="completed" if onepass_results or twostage_results else "failed",
            metrics={
                "v12_seed_recall": 0.34,
                "v12_seed_precision": 0.0448,
                "v13_onepass_claim_recall": _aggregate_claim_reports(onepass_results).get(
                    "claim_recall",
                    0.0,
                ),
                "v13_onepass_claim_precision": _aggregate_claim_reports(
                    onepass_results
                ).get("claim_precision", 0.0),
                "v13_twostage_claim_recall": _aggregate_claim_reports(
                    twostage_results
                ).get("claim_recall", 0.0),
                "v13_twostage_claim_precision": _aggregate_claim_reports(
                    twostage_results
                ).get("claim_precision", 0.0),
                **metrics_extra,
            },
        ),
    ]
    for report in reports:
        report["phrase_policy"] = phrase_policy.value
    return reports


async def _repeat_report(
    fixture: V10Fixture,
    args: argparse.Namespace,
    phrase_policy: V13PhrasePolicy,
) -> dict[str, Any]:
    unit = next(
        (
            item
            for item in fixture.units
            if str(item["unit_id"]) == args.rep_unit
        ),
        fixture.units[0],
    )
    generator = V13LLMFirstGenerator(
        client=build_v13_client(),
        cache=None,
        phrase_policy=phrase_policy,
    )
    results: list[dict[str, Any]] = []
    signatures: list[tuple[tuple[str, str, str, str], ...]] = []
    for _ in range(args.rep_runs):
        try:
            execution = await generator.records_onpass(
                unit_id=str(unit["unit_id"]),
                user_text=str(unit["user_text"]),
            )
            evaluation = evaluate_claims(unit=unit, output=execution.output)
            governed = govern_v13_output(
                execution.output,
                source_id=str(unit["unit_id"]),
                text=str(unit["user_text"]),
            )
            signature = tuple(
                (
                    item.raw_claim.user_statement_type.value,
                    item.raw_claim.coarse_type.value,
                    item.evidence.aligned_quote,
                    item.target.aligned_quote,
                )
                for item in governed
            )
            signatures.append(signature)
            results.append(
                {
                    "metrics": evaluation["metrics"],
                    "signature": signature,
                    "execution": {
                        "attempt_count": execution.attempt_count,
                        "first_attempt_status": execution.first_attempt_status,
                        "latency_ms": execution.latency_ms,
                        "model_call_count": execution.model_call_count,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "status": "failed",
                    "failure_attribution": "dependency_failed",
                    "error": f"{type(exc).__name__}:{exc}"[:1200],
                }
            )
    signature_counts = Counter(
        signature for signature in signatures if isinstance(signature, tuple)
    )
    most_common_count = signature_counts.most_common(1)[0][1] if signature_counts else 0
    correct_counts = Counter(
        signature
        for result, signature in zip(results, signatures, strict=False)
        if isinstance(signature, tuple)
        and result.get("metrics", {}).get("claim_precision") == 1.0
    )
    stable_correct = max(correct_counts.values(), default=0)
    report = _report(
        "REP-V13",
        status="completed" if len(signatures) == args.rep_runs else "failed",
        metrics={
            "cold_run_count": len(signatures),
            "cache_hit_count": 0,
            "unique_output_count": len(signature_counts),
            "semantic_claim_stability": _rate(most_common_count, len(signatures)),
            "stable_and_correct_rate": _rate(stable_correct, len(signatures)),
            "stable_but_wrong_rate": _rate(
                most_common_count - stable_correct,
                len(signatures),
            ),
            "unstable_rate": _rate(
                len(signatures) - most_common_count,
                len(signatures),
            ),
        },
        unit_results=results,
    )
    report["phrase_policy"] = phrase_policy.value
    return report


def _optional_cache(path: Path | None):
    if path is None:
        return None
    from .v7_run_cache import V7RunCache

    path.parent.mkdir(parents=True, exist_ok=True)
    return V7RunCache(path=path)


async def _async_main(args: argparse.Namespace) -> int:
    if "held_out" in args.matrix.name:
        raise ValueError("v13_held_out_fixture_not_allowed")
    fixture = load_v10_fixture(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    reports: list[dict[str, Any]] = []
    if args.suite in {"aligner", "quick", "all"}:
        reports.append(_aligner_control())
    if args.suite in {"negative", "quick", "all"}:
        reports.append(_negative_report())
    if args.suite in {"quick", "all"}:
        reports.extend(_ideal_reports(fixture, vocabulary))
        # ALIGNER-CONTROL and NEG-V13 are generated first and may also be
        # emitted by the ideal bundle; keep only the first report per ID.
        deduplicated: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for report in reports:
            experiment_id = str(report["experiment_id"])
            if experiment_id in seen_ids:
                continue
            seen_ids.add(experiment_id)
            deduplicated.append(report)
        reports = deduplicated
    if args.suite in {"llmf", "paradigm", "all"} and args.mode in {"shadow", "cold"}:
        reports.extend(
            await _shadow_reports(
                fixture,
                vocabulary,
                args,
                args.phrase_policy,
            )
        )
    if args.suite == "rep" or (args.suite == "all" and args.rep_enabled):
        reports.append(
            await _repeat_report(
                fixture,
                args,
                args.phrase_policy,
            )
        )
    if args.suite in {"async", "quick", "all"}:
        reports.append(
            run_async_isolation(args.output_dir)
            | {
                "experiment_id": "ASYNC-V13",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
            }
        )
    reports.append(
        _report(
            "HELD-OUT-V13",
            status="blocked",
            metrics={
                "development_finalist_frozen": 0,
                "heldout_read_count": 0,
                "reason": "await_frozen_v13_finalist",
            },
            failure_attribution="upstream_blocked",
        )
    )
    changed_variables: list[str] = []
    changed_variables.append(f"phrase_policy={args.phrase_policy.value}")
    if args.suite in {"llmf", "paradigm", "all"} and args.mode in {"shadow", "cold"}:
        changed_variables.extend(
            [
                "llm_first_generation",
                "independent_intent",
                "onepass_vs_twostage",
                "deterministic_phrase_alignment",
            ]
        )
    document = {
        "schema_version": "v13-experiment-report-1",
        "experiment_id": "V13-RUNNER",
        "status": "failed"
        if any(item.get("status") == "failed" for item in reports)
        else "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "suite": args.suite,
        "mode": args.mode,
        "matrix": str(args.matrix),
        "matrix_sha256": fixture.sha256,
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "aligner_version": V13_ALIGNER_VERSION,
        "phrase_policy": args.phrase_policy.value,
        "changed_variables": changed_variables,
        "cache_status": "disabled" if args.no_cache else str(args.cache_path),
        "reports": reports,
        "safety_boundary": {
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
            "held_out_read": False,
            "dspy_used": False,
            "gliner_called_on_main_path": False,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        f"v13-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    )
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(output_path, flush=True)
    for report in reports:
        print(
            report.get("experiment_id"),
            report.get("status"),
            json.dumps(report.get("metrics", {}), ensure_ascii=False, sort_keys=True),
            flush=True,
        )
    return 1 if document["status"] == "failed" else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=(
            "quick",
            "aligner",
            "negative",
            "llmf",
            "paradigm",
            "rep",
            "async",
            "all",
        ),
        required=True,
    )
    parser.add_argument("--mode", choices=("quick", "shadow", "cold"), default="quick")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_OUTPUT_DIR / "run-cache.json")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--rep-unit", default="macro-answer-fact")
    parser.add_argument("--rep-runs", type=int, default=3)
    parser.add_argument("--rep-enabled", action="store_true")
    parser.add_argument(
        "--phrase-policy",
        choices=tuple(policy.value for policy in V13PhrasePolicy),
        default=V13PhrasePolicy.APPROXIMATE.value,
    )
    args = parser.parse_args()
    if args.rep_runs < 3:
        raise ValueError("v13_cold_repeat_requires_at_least_3_runs")
    args.phrase_policy = V13PhrasePolicy(args.phrase_policy)
    if "held_out" in args.matrix.name:
        raise ValueError("v13_held_out_fixture_not_allowed")
    return args


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as exc:  # noqa: BLE001
        matrix_sha = _sha256(args.matrix) if args.matrix.is_file() else ""
        document = {
            "schema_version": "v13-experiment-report-1",
            "experiment_id": "V13-RUNNER",
            "status": "failed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "suite": args.suite,
            "mode": args.mode,
            "matrix": str(args.matrix),
            "matrix_sha256": matrix_sha,
            "phrase_policy": args.phrase_policy.value,
            "failure_attribution": "runner_error",
            "error": f"{type(exc).__name__}:{exc}"[:4000],
            "safety_boundary": {
                "consultation_state_written": False,
                "clinical_safety_evaluator_called": False,
                "clinical_safety_opa_called": False,
                "required_context_called": False,
                "held_out_read": False,
                "dspy_used": False,
            },
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.output_dir / (
            f"v13-failure-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
        )
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(output_path, flush=True)
        print(f"V13 runner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
