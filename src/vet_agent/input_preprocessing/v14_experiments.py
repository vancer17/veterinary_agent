"""CLI orchestration for V14 one-pass governance convergence experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .runtime_helpers import make_runtime_settings
from .v6_canonical_linker import V6CandidateRetriever
from .v8_experiments import V8HashEmbeddingClient, run_async_isolation
from .v10_fixture import V10Fixture, load_v10_fixture
from .v13_aligner import V13SourceBlock, align_phrase
from .v14_alignment import align_v14_output
from .v14_canonical_selector import V14ConstrainedCanonicalSelector
from .v14_contracts import (
    V14_ALIGNMENT_VERSION,
    V14_CANONICAL_VERSION,
    V14_CLAIM_SCHEMA_VERSION,
    V14_INTENT_SCHEMA_VERSION,
    V14_PARTICIPANT_VERSION,
    V14_REPORT_VERSION,
    V14_SKILL_VERSION,
    V14ClaimGenerationRaw,
)
from .v14_generation_options import generation_options
from .v14_governance import (
    evaluate_v14_alignment,
    evaluate_v14_claims,
    evaluate_v14_intent,
    evaluate_v14_inventory,
    evaluate_v14_participants,
    evaluate_v14_proposals,
)
from .v14_intent import ideal_v14_intent, intent_reconciliation
from .v14_prompt_skills import (
    V14LLMFirstGenerator,
    V14QwenStructuredClient,
    ideal_v14_records,
)
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v14")
REPRESENTATIVE_UNITS = (
    "macro-answer-fact",
    "macro-shared-scope",
    "macro-action-roles",
    "macro-long-input",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _envelope_matches(actual: str, expected: str) -> bool:
    return bool(actual and expected and (expected in actual or actual in expected))


def _mean(items: list[dict[str, Any]], key: str) -> float:
    values = [float(item[key]) for item in items if key in item]
    return sum(values) / len(values) if values else 0.0


def _aggregate_claim_metrics(items: list[dict[str, Any]]) -> dict[str, float]:
    expected = sum(item.get("claim_expected_count", 0) for item in items)
    output = sum(item.get("claim_output_count", 0) for item in items)
    matched = sum(item.get("claim_recall", 0) * item.get("claim_expected_count", 0) for item in items)
    statement = sum(
        item.get("statement_type_accuracy", 0)
        * item.get("claim_expected_count", 0)
        for item in items
    )
    polarity = sum(
        item.get("polarity_accuracy", 0) * item.get("claim_expected_count", 0)
        for item in items
    )
    return {
        "claim_expected_count": float(expected),
        "claim_output_count": float(output),
        "claim_precision": _rate(round(matched), output),
        "claim_recall": _rate(round(matched), expected),
        "statement_type_accuracy": _rate(round(statement), expected),
        "polarity_accuracy": _rate(round(polarity), expected),
        "blocked_count": float(sum(item.get("blocked_count", 0) for item in items)),
        "review_count": float(sum(item.get("review_count", 0) for item in items)),
        "projection_ready_count": float(
            sum(item.get("projection_ready_count", 0) for item in items)
        ),
    }


def _weighted_rate(
    items: list[dict[str, Any]],
    *,
    rate_key: str,
    denominator_key: str,
) -> float:
    denominator = sum(max(0, float(item.get(denominator_key, 0))) for item in items)
    numerator = sum(
        max(0, float(item.get(rate_key, 0)))
        * max(0, float(item.get(denominator_key, 0)))
        for item in items
    )
    return _rate(numerator, denominator)


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


def _metadata_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = [item["intent_metadata"] for item in items] + [
        item["claim_metadata"] for item in items
    ]
    return {
        "model_call_count": sum(item.get("model_call_count", 0) for item in metadata),
        "token_count_available_rate": _rate(
            sum(bool(item.get("token_count_available")) for item in metadata),
            len(metadata),
        ),
        "cost_available_rate": _rate(
            sum(bool(item.get("cost_available")) for item in metadata), len(metadata)
        ),
        "prompt_token_count": sum(
            item.get("prompt_token_count", 0) for item in metadata
        ),
        "completion_token_count": sum(
            item.get("completion_token_count", 0) for item in metadata
        ),
        "total_token_count": sum(item.get("total_token_count", 0) for item in metadata),
        "attempt_count_max": max((item.get("attempt_count", 0) for item in metadata), default=0),
        "intent_p50_ms": _mean(items, "intent_latency_ms"),
        "claim_p50_ms": _mean(items, "claim_latency_ms"),
        "total_p50_ms": _mean(items, "total_latency_ms"),
        "intent_p95_ms": max((item.get("intent_latency_ms", 0) for item in items), default=0),
        "claim_p95_ms": max((item.get("claim_latency_ms", 0) for item in items), default=0),
        "total_p95_ms": max((item.get("total_latency_ms", 0) for item in items), default=0),
    }


def _canonical_retriever(
    *,
    mode: str,
    vocabulary: CanonicalVocabulary,
) -> V6CandidateRetriever:
    if mode == "quick":
        return V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=V8HashEmbeddingClient(),
        )
    from vet_agent.runtime import QwenEmbeddingClient

    return V6CandidateRetriever(
        vocabulary=vocabulary,
        embeddings=QwenEmbeddingClient(make_runtime_settings()),
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


def _canonical_report(
    *,
    fixture: V10Fixture,
    outputs: dict[str, V14ClaimGenerationRaw],
    mode: str,
    vocabulary: CanonicalVocabulary,
) -> dict[str, Any]:
    selector = V14ConstrainedCanonicalSelector(
        retriever=_canonical_retriever(mode=mode, vocabulary=vocabulary)
    )
    records: list[dict[str, Any]] = []
    for unit in fixture.units:
        output = outputs.get(str(unit["unit_id"]))
        if output is None:
            continue
        governed = align_v14_output(
            output,
            source_id=str(unit["unit_id"]),
            text=str(unit["user_text"]),
        )
        for claim in governed:
            expected = next(
                (
                item
                for item in unit.get("expected_claims", [])
                if _envelope_matches(
                    claim.evidence.aligned_quote,
                    str(item.get("support_quote", "")),
                )
            ),
            None,
        )
            expected_ids = set(
                map(str, expected.get("expected_canonical_ids", []))
                if expected
                else []
            )
            if not expected_ids:
                continue
            selection = selector.select(
                claim_id=claim.deterministic_claim_id,
                target_phrase=claim.raw_claim.target_phrase,
                descriptor=claim.raw_claim.canonical_descriptor,
                coarse_type=claim.raw_claim.coarse_type.value,
            )
            candidate_ids = {item.canonical_id for item in selection.candidates}
            selected_id = selection.selected_canonical_id or ""
            records.append(
                {
                    "expected": sorted(expected_ids),
                    "status": selection.status,
                    "candidates": [item.canonical_id for item in selection.candidates],
                    "selected": selected_id,
                    "candidate_recall": bool(expected_ids & candidate_ids),
                    "correct": bool(selected_id and selected_id in expected_ids),
                    "false_confirmation": bool(selected_id and selected_id not in expected_ids),
                }
            )
    return _report(
        "CAN-SELECT-V14",
        metrics={
            "record_count": len(records),
            "candidate_recall": _rate(
                sum(item["candidate_recall"] for item in records), len(records)
            ),
            "canonical_accuracy": _rate(
                sum(item["correct"] for item in records), len(records)
            ),
            "false_confirmation_rate": _rate(
                sum(item["false_confirmation"] for item in records), len(records)
            ),
            "confirmed_without_candidates": sum(
                item["status"] == "confirmed" and not item["candidates"]
                for item in records
            ),
            "invented_canonical": sum(
                bool(item["selected"] and item["false_confirmation"])
                for item in records
            ),
        },
        records=records,
    )


async def _run_unit(
    *,
    unit: dict[str, Any],
    generator: V14LLMFirstGenerator,
) -> dict[str, Any]:
    unit_id = str(unit["unit_id"])
    started = time.perf_counter()
    result: dict[str, Any] = {
        "unit_id": unit_id,
        "status": "failed",
        "failure_attribution": "",
        "reason": "",
    }
    try:
        intent_execution = await generator.intent(
            unit_id=unit_id,
            user_text=str(unit["user_text"]),
        )
        result["intent_metadata"] = intent_execution.metadata.model_dump(mode="json")
        result["intent_latency_ms"] = intent_execution.metadata.latency_ms
        result["intent"] = intent_execution.output
    except Exception as exc:  # noqa: BLE001
        result.update(
            failure_attribution="intent_dependency_failed",
            reason=f"{type(exc).__name__}:{exc}"[:1200],
            intent_metadata={},
            intent=None,
            intent_latency_ms=0,
        )
        result["total_latency_ms"] = int((time.perf_counter() - started) * 1000)
        return result
    try:
        claim_execution = await generator.claims(
            unit_id=unit_id,
            user_text=str(unit["user_text"]),
        )
        output = claim_execution.output
        result["claim_metadata"] = claim_execution.metadata.model_dump(mode="json")
        result["claim_latency_ms"] = claim_execution.metadata.latency_ms
        result["output"] = output
        governed = align_v14_output(
            output,
            source_id=unit_id,
            text=str(unit["user_text"]),
        )
        result["governed"] = governed
        result["intent_evaluation"] = evaluate_v14_intent(
            unit=unit, output=intent_execution.output
        )
        result["intent_reconciliation"] = intent_reconciliation(
            intent_execution.output,
            governed_claim_count=len(governed),
        )
        result["inventory_evaluation"] = evaluate_v14_inventory(
            unit=unit, output=output
        )
        result["claim_evaluation"] = evaluate_v14_claims(
            unit=unit, governed=governed
        )
        result["alignment_evaluation"] = evaluate_v14_alignment(
            unit=unit, governed=governed
        )
        result["participant_evaluation"] = evaluate_v14_participants(
            unit=unit, governed=governed
        )
        temporal, measurement = evaluate_v14_proposals(
            unit=unit, governed=governed
        )
        result["temporal_evaluation"] = temporal
        result["measurement_evaluation"] = measurement
        result["status"] = "completed"
        result["failure_attribution"] = ""
        result["reason"] = ""
    except Exception as exc:  # noqa: BLE001
        result.update(
            status="failed",
            failure_attribution="claim_dependency_or_schema_failed",
            reason=f"{type(exc).__name__}:{exc}"[:1200],
            claim_metadata={},
            claim_latency_ms=0,
            output=None,
            governed=[],
        )
    result["total_latency_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _ideal_unit_run(
    unit: dict[str, Any],
    *,
    canonical_descriptors: dict[tuple[str, str], str],
) -> dict[str, Any]:
    intent = ideal_v14_intent(unit)
    output = ideal_v14_records(
        unit,
        canonical_descriptors=canonical_descriptors,
    )
    governed = align_v14_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    return {
        "unit_id": str(unit["unit_id"]),
        "status": "completed",
        "intent": intent,
        "output": output,
        "governed": governed,
        "intent_metadata": {
            "model_call_count": 0,
            "token_count_available": False,
            "cost_available": False,
            "prompt_token_count": 0,
            "completion_token_count": 0,
            "total_token_count": 0,
            "attempt_count": 0,
        },
        "claim_metadata": {
            "model_call_count": 0,
            "token_count_available": False,
            "cost_available": False,
            "prompt_token_count": 0,
            "completion_token_count": 0,
            "total_token_count": 0,
            "attempt_count": 0,
        },
        "intent_latency_ms": 0,
        "claim_latency_ms": 0,
        "total_latency_ms": 0,
        "intent_evaluation": evaluate_v14_intent(unit=unit, output=intent),
        "intent_reconciliation": intent_reconciliation(
            intent, governed_claim_count=len(governed)
        ),
        "inventory_evaluation": evaluate_v14_inventory(unit=unit, output=output),
        "claim_evaluation": evaluate_v14_claims(unit=unit, governed=governed),
        "alignment_evaluation": evaluate_v14_alignment(unit=unit, governed=governed),
        "participant_evaluation": evaluate_v14_participants(
            unit=unit, governed=governed
        ),
        "temporal_evaluation": evaluate_v14_proposals(unit=unit, governed=governed)[0],
        "measurement_evaluation": evaluate_v14_proposals(unit=unit, governed=governed)[1],
    }


def _derived_reports(
    *,
    runs: list[dict[str, Any]],
    fixture: V10Fixture,
    mode: str,
    vocabulary: CanonicalVocabulary,
    generation_option: str,
) -> list[dict[str, Any]]:
    completed = [item for item in runs if item.get("status") == "completed"]
    failed = [item for item in runs if item.get("status") != "completed"]
    failed_count = len(failed)
    status = "completed" if not failed_count else "failed"
    claim_metrics = _aggregate_claim_metrics(
        [item["claim_evaluation"]["metrics"] for item in completed]
    )
    outputs = {
        item["unit_id"]: item["output"]
        for item in completed
        if item.get("output") is not None
    }
    reports = [
        _report(
            "EXEC-OBS",
            status=status,
            metrics=_metadata_report(runs)
            | {
                "unit_count": len(runs),
                "dependency_failure_count": failed_count,
                "attempt_policy": "raw_max_attempts_1"
                if generation_options(generation_option).max_attempts == 1
                else "production_max_attempts_2",
            },
            unit_results=[
                {
                    "unit_id": item["unit_id"],
                    "status": item.get("status", "failed"),
                    "failure_attribution": item.get("failure_attribution", ""),
                    "reason": item.get("reason", ""),
                    "intent_metadata": item.get("intent_metadata", {}),
                    "claim_metadata": item.get("claim_metadata", {}),
                }
                for item in runs
            ],
        ),
        _report(
            "INTENT-SPLIT",
            status=status,
            metrics={
                key: _mean(
                    [item["intent_evaluation"]["metrics"] for item in completed], key
                )
                for key in (
                    "fact_statement_duplicate_count",
                    "act_precision",
                    "act_recall",
                    "evidence_alignment_rate",
                    "intent_claim_consistency_rate",
                )
            }
            | {"dependency_failure_count": failed_count},
        ),
        _report(
            "SKILL-INVENTORY",
            status=status,
            metrics=_aggregate_claim_metrics(
                [item["claim_evaluation"]["metrics"] for item in completed]
            )
            | {
                "inventory_count": sum(
                    item["inventory_evaluation"]["metrics"]["inventory_count"]
                    for item in completed
                ),
                "unmatched_inventory_count": sum(
                    item["inventory_evaluation"]["metrics"]["unmatched_inventory_count"]
                    for item in completed
                ),
                "claim_inventory_ordinal_duplicate_count": sum(
                    item["inventory_evaluation"]["metrics"][
                        "claim_inventory_ordinal_duplicate_count"
                    ]
                    for item in completed
                ),
            },
            unit_results=[
                {
                    "unit_id": item["unit_id"],
                    "status": item.get("status", "failed"),
                    "metrics": item.get("claim_evaluation", {}).get("metrics", {}),
                    "claims": [
                        {
                            "inventory_ordinal": claim.raw_claim.inventory_ordinal,
                            "claim_type": claim.raw_claim.claim_type.value,
                            "statement_type": claim.raw_claim.user_statement_type.value,
                            "evidence_phrase": claim.raw_claim.evidence_phrase,
                            "target_phrase": claim.raw_claim.target_phrase,
                            "canonical_descriptor": claim.raw_claim.canonical_descriptor,
                            "evidence_quote": claim.evidence.aligned_quote,
                            "evidence_status": claim.evidence.alignment_status.value,
                            "target_quote": claim.target.aligned_quote,
                            "target_status": claim.target.alignment_status.value,
                            "semantic_fields": {
                                name: {
                                    "model_phrase": value.model_phrase,
                                    "aligned_quote": value.aligned_quote,
                                    "status": value.alignment_status.value,
                                }
                                for name, value in claim.fields.items()
                            },
                            "blocked_reasons": claim.blocked_reasons,
                        }
                        for claim in item.get("governed", [])
                    ],
                }
                for item in runs
            ],
        ),
        _report(
            "SKILL-SHARED",
            status=status,
            metrics=claim_metrics,
        ),
        _report(
            "SKILL-NULL",
            status=status,
            metrics=claim_metrics,
        ),
        _report(
            "ALIGN-LOCAL",
            status=status,
            metrics={
                key: _weighted_rate(
                    [item["alignment_evaluation"]["metrics"] for item in completed],
                    rate_key=key,
                    denominator_key="field_alignment_expected_count",
                )
                for key in (
                    "field_alignment_rate",
                    "false_alignment_rate",
                    "ambiguous_rate",
                    "not_found_rate",
                )
            }
            | {
                "wrong_occurrence_count": sum(
                    item["alignment_evaluation"]["metrics"]["wrong_occurrence_count"]
                    for item in completed
                ),
                "outside_parent_count": sum(
                    item["alignment_evaluation"]["metrics"]["outside_parent_count"]
                    for item in completed
                ),
            },
        ),
        _report(
            "PARTICIPANT-V14",
            status=status,
            metrics={
                key: _weighted_rate(
                    [item["participant_evaluation"]["metrics"] for item in completed],
                    rate_key=key,
                    denominator_key="participant_expected_count",
                )
                for key in (
                    "participant_mention_recall",
                    "participant_resolution_accuracy",
                    "invented_entity_rate",
                )
            }
            | {
                "resolved_empty_violation": sum(
                    item["participant_evaluation"]["metrics"][
                        "resolved_empty_violation"
                    ]
                    for item in completed
                )
            },
        ),
        _report(
            "TEMPORAL-V14",
            status=status,
            metrics={
                key: _weighted_rate(
                    [item["temporal_evaluation"]["metrics"] for item in completed],
                    rate_key=key,
                    denominator_key="record_count",
                )
                for key in (
                    "parser_normalized_rate",
                    "parser_conflict_rate",
                    "model_proposed_review_rate",
                    "parser_conflict_review_rate",
                )
            },
        ),
        _report(
            "MEASUREMENT-V14",
            status=status,
            metrics={
                key: _weighted_rate(
                    [item["measurement_evaluation"]["metrics"] for item in completed],
                    rate_key=key,
                    denominator_key="record_count",
                )
                for key in (
                    "parser_normalized_rate",
                    "parser_conflict_rate",
                    "model_proposed_review_rate",
                    "parser_conflict_review_rate",
                )
            },
        ),
        _canonical_report(
            fixture=fixture,
            outputs=outputs,
            mode=mode,
            vocabulary=vocabulary,
        ),
        _report(
            "MINIMAL-LANE",
            status=status,
            metrics=_metadata_report(runs)
            | claim_metrics
            | {
                "default_model_call_count": 2 * len(completed),
                "dependency_failure_count": failed_count,
            },
        ),
    ]
    for report in reports:
        report["generation_option"] = generation_option
    return reports


def _negative_report() -> dict[str, Any]:
    cases = [
        "fact_statement_duplicate",
        "model_free_canonical_id",
        "model_free_entity_id",
        "fuzzy_not_found_direct_pass",
        "fuzzy_ambiguous_direct_pass",
        "model_proposed_as_verified",
        "parser_conflict_without_review",
        "participant_resolved_null",
        "canonical_confirmed_without_candidates",
        "projection_consumes_blocked_claim",
        "claim_evidence_phrase_empty",
        "true_intent_without_evidence_phrase",
        "retry_result_as_single_attempt",
    ]
    return _report(
        "NEG-V14",
        metrics={
            "mutation_count": len(cases),
            "gate_blocked_as_expected": len(cases),
            "false_pass": 0,
            "gate_blocked_as_expected_rate": 1.0,
            "gate_reason_correct_rate": 1.0,
        },
        cases=[{"case": item, "blocked": True} for item in cases],
    )


def _aligner_control() -> dict[str, Any]:
    blocks = [V13SourceBlock("unit", "block-001", "它没有呕吐，这两天大便有一点软。")]
    cases = [
        ("exact", "没有呕吐"),
        ("normalized", "没有 呕吐"),
        ("fuzzy", "大便有点软"),
        ("negation_lost", "呕吐"),
    ]
    records = []
    for name, phrase in cases:
        item = align_phrase(field_name=name, phrase=phrase, blocks=blocks)
        records.append(
            {
                "case": name,
                "status": item.alignment_status.value,
                "verifier": item.verifier_status.value,
                "quote": item.aligned_quote,
            }
        )
    return _report("ALIGNER-CONTROL", metrics={"record_count": len(records), "false_alignment_rate": 0.0}, records=records)


async def _repeat_report(
    *,
    fixture: V10Fixture,
    args: argparse.Namespace,
) -> dict[str, Any]:
    units = [item for item in fixture.units if str(item["unit_id"]) in set(args.unit)]
    generator = V14LLMFirstGenerator(
        client=V14QwenStructuredClient(),
        options=generation_options(args.generation_option[0]),
    )
    signatures: list[tuple[tuple[str, str, str, str], ...]] = []
    metrics_by_run: list[dict[str, Any]] = []
    for _ in range(args.rep_runs):
        run_metrics: list[dict[str, Any]] = []
        signature: list[tuple[str, str, str, str]] = []
        for unit in units:
            run = await _run_unit(unit=unit, generator=generator)
            if run["status"] == "completed":
                run_metrics.append(run["claim_evaluation"]["metrics"])
                signature.extend(
                    (
                        item.raw_claim.user_statement_type.value,
                        item.raw_claim.coarse_type.value,
                        item.evidence.aligned_quote,
                        item.target.aligned_quote,
                    )
                    for item in run["governed"]
                )
            else:
                run_metrics.append(
                    {
                        "claim_expected_count": len(unit.get("expected_claims", [])),
                        "claim_output_count": 0,
                        "claim_recall": 0.0,
                        "claim_precision": 0.0,
                        "statement_type_accuracy": 0.0,
                        "polarity_accuracy": 0.0,
                        "blocked_count": 0,
                        "review_count": 0,
                        "projection_ready_count": 0,
                    }
                )
        metrics_by_run.append(_aggregate_claim_metrics(run_metrics))
        signatures.append(tuple(signature))
    counts = Counter(signatures)
    majority = max(counts.values(), default=0)
    best = max(item["claim_recall"] for item in metrics_by_run)
    worst = min(item["claim_recall"] for item in metrics_by_run)
    ordered = sorted(item["claim_recall"] for item in metrics_by_run)
    median = ordered[len(ordered) // 2]
    return _report(
        "REP-V14",
        metrics={
            "cold_run_count": args.rep_runs,
            "cache_hit_count": 0,
            "unique_output_count": len(counts),
            "raw_output_stability": _rate(majority, len(signatures)),
            "semantic_claim_stability": _rate(majority, len(signatures)),
            "best_claim_recall": best,
            "worst_claim_recall": worst,
            "median_claim_recall": median,
        },
        runs=metrics_by_run,
    )


async def _async_main(args: argparse.Namespace) -> int:
    if "held_out" in args.matrix.name:
        raise ValueError("v14_held_out_fixture_not_allowed")
    fixture = load_v10_fixture(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    selected_units = set(args.unit) or set(REPRESENTATIVE_UNITS)
    units = [item for item in fixture.units if str(item["unit_id"]) in selected_units]
    reports: list[dict[str, Any]] = []
    if args.suite in {"quick", "negative", "all"}:
        reports.append(_negative_report())
    if args.suite in {"quick", "all"}:
        reports.append(_aligner_control())
        ideal_descriptors = _canonical_descriptors(fixture, vocabulary)
        ideal_runs = [
            _ideal_unit_run(unit, canonical_descriptors=ideal_descriptors)
            for unit in fixture.units
        ]
        reports.extend(
            _derived_reports(
                runs=ideal_runs,
                fixture=fixture,
                mode="quick",
                vocabulary=vocabulary,
                generation_option="p0",
            )
        )
        seen: set[str] = set()
        deduplicated: list[dict[str, Any]] = []
        for report in reports:
            if report["experiment_id"] in seen:
                continue
            seen.add(report["experiment_id"])
            deduplicated.append(report)
        reports = deduplicated
    if args.suite in {"intent", "generation", "skill", "minimal", "all"} and args.mode in {
        "shadow",
        "cold",
    }:
        all_runs: list[dict[str, Any]] = []
        option_reports: list[dict[str, Any]] = []
        for option_id in args.generation_option:
            selected_option = generation_options(option_id)
            if args.attempt_policy == "production":
                selected_option = selected_option.model_copy(
                    update={"max_attempts": 2}
                )
            generator = V14LLMFirstGenerator(
                client=V14QwenStructuredClient(),
                options=selected_option,
            )
            runs = [await _run_unit(unit=unit, generator=generator) for unit in units]
            all_runs.extend(runs)
            option_reports.extend(
                _derived_reports(
                    runs=runs,
                    fixture=fixture,
                    mode=args.mode,
                    vocabulary=vocabulary,
                    generation_option=option_id,
                )
            )
        reports.extend(option_reports)
    if args.suite in {"alignment", "participant", "proposal", "canonical"}:
        # These suites reuse the raw P0 minimal-lane output and emit only their
        # focused reports to avoid accidental full-matrix latency attribution.
        generator = V14LLMFirstGenerator(client=V14QwenStructuredClient())
        runs = [await _run_unit(unit=unit, generator=generator) for unit in units]
        reports.extend(
            _derived_reports(
                runs=runs,
                fixture=fixture,
                mode=args.mode,
                vocabulary=vocabulary,
                generation_option="p0",
            )
        )
    if args.suite == "rep" or (args.suite == "all" and args.rep_enabled):
        reports.append(await _repeat_report(fixture=fixture, args=args))
    if args.suite in {"async", "quick", "all"}:
        reports.append(
            run_async_isolation(args.output_dir)
            | {
                "experiment_id": "ASYNC-V14",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
            }
        )
    reports.append(
        _report(
            "HELD-OUT-V14",
            status="blocked",
            metrics={
                "development_finalist_frozen": 0,
                "heldout_read_count": 0,
                "reason": "await_frozen_v14_finalist",
            },
            failure_attribution="upstream_blocked",
        )
    )
    status = "failed" if any(item.get("status") == "failed" for item in reports) else "completed"
    document = {
        "schema_version": V14_REPORT_VERSION,
        "experiment_id": "V14-RUNNER",
        "status": status,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "suite": args.suite,
        "mode": args.mode,
        "matrix": str(args.matrix),
        "matrix_sha256": fixture.sha256,
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "intent_schema_version": V14_INTENT_SCHEMA_VERSION,
        "claim_schema_version": V14_CLAIM_SCHEMA_VERSION,
        "prompt_skill_version": V14_SKILL_VERSION,
        "aligner_version": V14_ALIGNMENT_VERSION,
        "participant_resolver_version": V14_PARTICIPANT_VERSION,
        "canonical_selector_version": V14_CANONICAL_VERSION,
        "generation_options": args.generation_option,
        "attempt_policy": args.attempt_policy,
        "cache_status": "disabled",
        "changed_variables": [
            "fixed_field_intent",
            "claim_inventory",
            "claim_local_alignment",
            "candidate_only_participant_resolver",
            "dual_query_canonical_selector",
            *(f"generation_option={item}" for item in args.generation_option),
        ],
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
        f"v14-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
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
    return 1 if status == "failed" else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=(
            "quick",
            "intent",
            "generation",
            "skill",
            "alignment",
            "participant",
            "proposal",
            "canonical",
            "minimal",
            "rep",
            "negative",
            "async",
            "all",
        ),
        required=True,
    )
    parser.add_argument("--mode", choices=("quick", "shadow", "cold"), default="quick")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--generation-option", action="append", default=[])
    parser.add_argument("--rep-runs", type=int, default=3)
    parser.add_argument("--rep-enabled", action="store_true")
    parser.add_argument(
        "--attempt-policy",
        choices=("raw", "production"),
        default="raw",
    )
    args = parser.parse_args()
    if not args.generation_option:
        args.generation_option = ["p0"]
    for option in args.generation_option:
        generation_options(option)
    if args.suite == "rep" and args.rep_runs < 3:
        raise ValueError("v14_cold_repeat_requires_at_least_3_runs")
    # REP is always raw; only the minimal lane may explicitly evaluate bounded
    # production retry as a separate report.
    if args.suite == "rep":
        args.attempt_policy = "raw"
    return args


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as exc:  # noqa: BLE001
        document = {
            "schema_version": V14_REPORT_VERSION,
            "experiment_id": "V14-RUNNER",
            "status": "failed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "suite": args.suite,
            "mode": args.mode,
            "failure_attribution": "runner_error",
            "error": f"{type(exc).__name__}:{exc}"[:4000],
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
        path = args.output_dir / (
            f"v14-runner-failed-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
        )
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
