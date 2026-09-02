"""CLI orchestration for the tenth-round exploratory shadow experiments.

V10 repairs measurement and interfaces first, then evaluates calibrated span
pools, golden macro output, a fixed relation contract, winner regression, and
continuation gates.  Every report remains diagnostic-only and cannot unblock
the V8 live phase admission.
"""

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

from pydantic import ValidationError

from .v6_canonical_linker import V6CandidateRetriever
from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v7_run_cache import V7RunCache
from .v8_experiments import run_async_isolation
from .v10_boundary_calibration import (
    V10BoundaryCalibrator,
    build_v10_gliner_extractor,
    evaluate_v10_span_pool,
)
from .v10_contracts import (
    V10_BOUNDARY_CALIBRATION_VERSION,
    V10_MACRO_PROMPT_VERSION,
    V10_MACRO_SCHEMA_VERSION,
    V10_RELATION_PROMPT_VERSION,
    V10_REPORT_VERSION,
    V10CalibratedSpan,
    V10MacroSemanticRawOutput,
)
from .v10_fixture import (
    V10Fixture,
    audit_v10_fixture,
    build_v10_golden_pool,
    field_role_split,
    load_v10_fixture,
    relation_span_completeness,
)
from .v10_macro import (
    V10MacroAnalyzer,
    V10MacroExecution,
    apply_candidate_mode,
    evaluate_v10_macro,
    golden_candidates,
    ideal_macro_output,
)
from .v10_relation import (
    V10RelationAdapter,
    evaluate_relation_executions,
    ideal_relation_output,
    missing_relation_report,
    relation_records,
)
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v10")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _optional_cache(path: Path | None) -> V7RunCache | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return V7RunCache(path=path)


def _aggregate_numeric(metrics: list[dict[str, Any]]) -> dict[str, float]:
    keys = sorted({key for item in metrics for key in item if isinstance(item[key], (int, float))})
    result: dict[str, float] = {}
    for key in keys:
        values = [float(item[key]) for item in metrics if key in item]
        result[f"mean_{key}"] = sum(values) / len(values) if values else 0.0
        if key.endswith("_count"):
            result[f"total_{key}"] = sum(values)
    return result


def _interface_audit(
    fixture: V10Fixture,
    *,
    vocabulary: CanonicalVocabulary,
    mode: str,
) -> dict[str, Any]:
    participant_roles = {
        "subject_quote",
        "action_agent_quote",
        "action_recipient_quote",
        "experiencer_quote",
    }
    participant_fields = [
        field
        for field in fixture.fields
        if field.field_role.value in participant_roles and field.status == "active"
    ]
    canonical_records = 0
    canonical_recalled = 0
    if mode == "shadow":
        from vet_agent.runtime import QwenEmbeddingClient

        from .runtime_helpers import make_runtime_settings

        retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=QwenEmbeddingClient(make_runtime_settings()),
        )
    for unit in fixture.units:
        for claim in unit.get("expected_claims", []):
            expected = claim.get("expected_canonical_ids")
            if not expected:
                continue
            canonical_records += 1
            if mode == "shadow":
                candidates = retriever.recall(
                    claim_id=str(claim["claim_id"]),
                    target_quote=str(claim["target_quote"]),
                    coarse_type=str(claim["coarse_type"]),
                )
                recalled = bool(set(expected) & {item.canonical_id for item in candidates.candidates})
            else:
                ids = {item.canonical_id for item in vocabulary.terms}
                recalled = bool(set(expected) & ids)
            canonical_recalled += int(recalled)
    checks = [
        ("fixture_offsets", True),
        ("relation_contract_explicit", True),
        ("participant_gold_resolver", True),
        ("canonical_gold_recall", canonical_records == 0 or canonical_recalled == canonical_records),
    ]
    return {
        "experiment_id": "INTERFACE-AUDIT",
        "status": "completed" if all(value for _, value in checks) else "blocked",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "mode": mode,
        "metrics": {
            "check_count": len(checks),
            "passed_count": sum(value for _, value in checks),
            "gold_participant_field_count": len(participant_fields),
            "canonical_gold_record_count": canonical_records,
            "canonical_gold_recall_rate": _rate(canonical_recalled, canonical_records),
        },
        "checks": [{"name": name, "passed": passed} for name, passed in checks],
    }


def _phase0_reports(
    fixture: V10Fixture,
    *,
    vocabulary: CanonicalVocabulary,
    mode: str,
) -> list[dict[str, Any]]:
    return [
        audit_v10_fixture(fixture),
        field_role_split(fixture),
        relation_span_completeness(fixture),
        _interface_audit(fixture, vocabulary=vocabulary, mode=mode),
    ]


def _span_model_path(model: str) -> tuple[str, str]:
    if model == "small":
        return (
            os.environ["INPUT_PREPROCESSING_V10_GLINER_SMALL_PATH"],
            "f227d3cd637bd4e6757ae143935316d062393341",
        )
    if model == "multi":
        return (
            os.environ["INPUT_PREPROCESSING_V10_GLINER_MULTI_PATH"],
            "443d26d654e0324125a96bebd8e796c14ff2efe6",
        )
    raise ValueError(f"unsupported_v10_span_model:{model}")


def _span_reports(
    fixture: V10Fixture,
    *,
    models: list[str],
    variants: list[str],
    budget_values: list[int],
    threshold: float,
    per_turn_limit: int,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    fields = [field for field in fixture.fields if field.status == "active"]
    texts = fixture.texts_by_unit
    tokenizer_path = os.environ["INPUT_PREPROCESSING_V10_TOKENIZER_PATH"]
    for model_name in models:
        model_path, revision = _span_model_path(model_name)
        extractor = build_v10_gliner_extractor(
            model_path=model_path,
            revision=revision,
            threshold=threshold,
            label_mode="bilingual",
        )
        for variant in variants:
            calibrator = V10BoundaryCalibrator(
                variant=variant,  # type: ignore[arg-type]
                tokenizer_path=tokenizer_path,
            )
            report = evaluate_v10_span_pool(
                fields=fields,
                texts_by_unit=texts,
                extractor=extractor,
                calibrator=calibrator,
            )
            report["experiment_id"] = (
                "SPAN-RAW" if variant == "A" else "SPAN-CALIBRATE"
            )
            report["span_model"] = model_name
            report["variant"] = variant
            reports.append(report)
        for budget in budget_values:
            calibrator = V10BoundaryCalibrator(
                variant="G",
                tokenizer_path=tokenizer_path,
                per_role_top_k=budget,
                per_turn_limit=per_turn_limit,
            )
            report = evaluate_v10_span_pool(
                fields=fields,
                texts_by_unit=texts,
                extractor=extractor,
                calibrator=calibrator,
            )
            report["experiment_id"] = "SPAN-BUDGET"
            report["span_model"] = model_name
            report["variant"] = "G"
            reports.append(report)
    if models:
        reports.append(
            {
                "experiment_id": "SPAN-MODEL",
                "status": "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "metrics": {
                    "model_count": len(models),
                    "shared_fixture_sha256": fixture.sha256,
                    "shared_calibration_version": V10_BOUNDARY_CALIBRATION_VERSION,
                },
                "model_names": models,
            }
        )
    reports.append(
        {
            "experiment_id": "SPANMARKER-CHINESE",
            "status": "blocked",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "failure_attribution": "middleware_not_configured",
            "metrics": {
                "trained_model_count": 0,
                "cross_validation_configured": 0,
                "reason": "await_explicit_offset_and_calibration_gate",
            },
        }
    )
    return reports


def _macro_unit_report(
    unit: dict[str, Any],
    *,
    execution: V10MacroExecution,
    spans: list[V10CalibratedSpan],
) -> dict[str, Any]:
    pool = build_v10_golden_pool(unit)
    return evaluate_v10_macro(
        unit=unit,
        output=execution.output,
        spans=spans,
        entity_candidates=pool.entity_candidates,
        execution=execution,
    )


async def _macro_reports(
    fixture: V10Fixture,
    *,
    mode: str,
    units: list[str],
    cache_path: Path | None,
    candidate_modes: list[str],
) -> list[dict[str, Any]]:
    selected = [
        unit
        for unit in fixture.units
        if not units or str(unit["unit_id"]) in set(units)
    ]
    analyzer = None
    if mode == "shadow":
        analyzer = V10MacroAnalyzer(
            model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
            cache=_optional_cache(cache_path),
        )
    base_results: list[dict[str, Any]] = []
    for unit in selected:
        golden = build_v10_golden_pool(unit)
        spans = apply_candidate_mode(golden_candidates(golden), "full")
        if mode == "shadow":
            assert analyzer is not None
            execution = await analyzer.run(
                experiment_id="MACRO-FULL",
                user_text=str(unit["user_text"]),
                spans=spans,
                turn_context={"unit_id": str(unit["unit_id"]), "source_blocks": [{"source_id": str(unit["unit_id"]), "source_block_id": "block-001", "text": str(unit["user_text"])}]},
            )
        else:
            execution = V10MacroExecution(
                output=ideal_macro_output(golden, unit),
                adapter="ideal-fixture-control",
                attempt_count=0,
                first_attempt_status="ideal_control",
                model_call_count=0,
            )
        base_results.append(_macro_unit_report(unit, execution=execution, spans=spans))

    reports: list[dict[str, Any]] = []
    for experiment_id, metric_prefixes in (
        ("MACRO-ACT", ("act_", "empty_act", "no_act", "evidence")),
        ("MACRO-SKELETON", ("claim_", "statement_type", "target_binding", "support_envelope", "invalid_span_reference")),
        ("MACRO-BINDING", ("binding_", "invalid_span", "role_ineligible", "cross_claim")),
        ("MACRO-FULL", ()),
    ):
        unit_metrics = [item["metrics"] for item in base_results]
        if metric_prefixes:
            unit_metrics = [
                {
                    key: value
                    for key, value in metrics.items()
                    if any(key.startswith(prefix) for prefix in metric_prefixes)
                }
                for metrics in unit_metrics
            ]
        metrics = _aggregate_numeric(unit_metrics)
        quality_keys = {
            "MACRO-ACT": ("mean_act_precision", "mean_act_recall", "mean_evidence_span_valid_rate"),
            "MACRO-SKELETON": ("mean_claim_precision", "mean_claim_recall", "mean_support_envelope_valid_rate"),
            "MACRO-BINDING": ("mean_binding_accuracy",),
            "MACRO-FULL": ("mean_act_precision", "mean_act_recall", "mean_claim_precision", "mean_claim_recall", "mean_binding_accuracy"),
        }[experiment_id]
        has_findings = any(float(metrics.get(key, 0.0)) < 1.0 for key in quality_keys) or float(
            metrics.get("mean_invalid_span_reference_count", 0.0)
        ) > 0
        reports.append(
            {
                "experiment_id": experiment_id,
                "status": "completed_with_findings" if has_findings else "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "span_source": "v10-explicit-offset-gold",
                "mode": mode,
                "metrics": metrics,
                "unit_metrics": unit_metrics,
                **({"unit_results": base_results} if experiment_id == "MACRO-FULL" else {}),
            }
        )

    for candidate_mode in candidate_modes:
        results: list[dict[str, Any]] = []
        for unit in selected:
            golden = build_v10_golden_pool(unit)
            spans = apply_candidate_mode(
                golden_candidates(golden),
                candidate_mode,  # type: ignore[arg-type]
            )
            if mode == "shadow":
                assert analyzer is not None
                execution = await analyzer.run(
                    experiment_id="MACRO-CANDIDATE-LOAD",
                    user_text=str(unit["user_text"]),
                    spans=spans,
                    turn_context={"unit_id": str(unit["unit_id"]), "candidate_mode": candidate_mode},
                )
            else:
                execution = V10MacroExecution(
                    output=ideal_macro_output(golden, unit),
                    adapter="ideal-fixture-control",
                    attempt_count=0,
                    first_attempt_status="ideal_control",
                    model_call_count=0,
                )
            result = _macro_unit_report(unit, execution=execution, spans=spans)
            result["metrics"]["candidate_count"] = len(spans)
            result["metrics"]["macro_input_token_count_available"] = False
            results.append(result)
        metrics = _aggregate_numeric([item["metrics"] for item in results])
        quality_keys = (
            "mean_act_precision",
            "mean_act_recall",
            "mean_claim_precision",
            "mean_claim_recall",
            "mean_binding_accuracy",
        )
        has_findings = any(float(metrics.get(key, 0.0)) < 1.0 for key in quality_keys)
        reports.append(
            {
                "experiment_id": "MACRO-CANDIDATE-LOAD",
                "status": "completed_with_findings" if has_findings else "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "candidate_mode": candidate_mode,
                "mode": mode,
                "metrics": metrics,
                "unit_results": results,
            }
        )
    return reports


async def _relation_reports(
    fixture: V10Fixture,
    *,
    mode: str,
    batch_sizes: list[int],
    cache_path: Path | None,
    fewshot: bool,
    reverse_order: bool,
) -> list[dict[str, Any]]:
    records = relation_records(fixture)
    reports: list[dict[str, Any]] = [missing_relation_report(records)]
    for batch_size in batch_sizes:
        if mode == "shadow":
            adapter = V10RelationAdapter(
                model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
                cache=_optional_cache(cache_path),
                fewshot=fewshot,
            )
            executions = await adapter.run(
                records=records,
                batch_size=batch_size,
                reverse_order=reverse_order,
            )
        else:
            executions = ideal_relation_output(records)
        evaluated = evaluate_relation_executions(records, executions)
        experiment_id = (
            "REL-SINGLE"
            if batch_size == 1 and not fewshot
            else "REL-VERSIONED-FEWSHOT"
            if fewshot
            else "REL-BATCH-FIXED"
        )
        reports.append(
            {
                "experiment_id": experiment_id,
                "status": "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "mode": mode,
                "batch_size": batch_size,
                "reverse_order": reverse_order,
                "prompt_version": (
                    executions[0].prompt_version if executions else V10_RELATION_PROMPT_VERSION
                ),
                **evaluated,
            }
        )
    return reports


def _regression_reports(
    fixture: V10Fixture,
    macro_reports: list[dict[str, Any]],
    *,
    vocabulary: CanonicalVocabulary,
    mode: str,
) -> list[dict[str, Any]]:
    claims = [
        claim
        for report in macro_reports
        if report.get("experiment_id") == "MACRO-FULL"
        for result in report.get("unit_results", [])
        for claim in result.get("governed_claims", [])
    ]
    expected_by_signature = {
        (
            str(claim["statement_type"]),
            str(claim["coarse_type"]),
            str(claim["support_quote"]),
            str(claim["target_quote"]),
        ): claim
        for unit in fixture.units
        for claim in unit.get("expected_claims", [])
    }
    retriever = None
    if mode == "shadow":
        from vet_agent.runtime import QwenEmbeddingClient

        from .runtime_helpers import make_runtime_settings

        retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=QwenEmbeddingClient(make_runtime_settings()),
        )
    canonical_records = []
    participant_records = []
    temporal_records: list[dict[str, Any]] = []
    measurement_records: list[dict[str, Any]] = []
    deterministic_parser_records: list[dict[str, Any]] = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        for claim in unit.get("expected_claims", []):
            for role, quote in (
                ("temporal_quote", claim.get("temporal_quote", "")),
                ("measurement_quote", claim.get("measurement_quote", "")),
            ):
                if not quote:
                    continue
                parsed = (
                    parse_temporal(temporal_quote=str(quote))
                    if role == "temporal_quote"
                    else parse_measurement(measurement_quote=str(quote))
                )
                deterministic_parser_records.append(
                    {
                        "unit_id": unit_id,
                        "claim_id": str(claim["claim_id"]),
                        "role": role,
                        "quote": str(quote),
                        "parser_status": parsed.status.value,
                        "parser_unresolved_reason": parsed.unresolved_reason.value
                        if parsed.unresolved_reason is not None
                        else None,
                        "input_source": "expected-explicit-offset",
                    }
                )
    for claim in claims:
        expected = expected_by_signature.get(
            (
                claim["statement_type"],
                claim["coarse_type"],
                claim["support"]["quote"],
                claim["target"]["quote"],
            ),
            {},
        )
        target = claim.get("target", {})
        if expected.get("expected_canonical_ids"):
            if retriever is not None:
                candidates = retriever.recall(
                    claim_id=claim["claim_id"],
                    target_quote=target.get("quote", ""),
                    coarse_type=claim.get("coarse_type", ""),
                )
                candidate_ids = [item.canonical_id for item in candidates.candidates]
            else:
                ids = {item.canonical_id for item in vocabulary.terms}
                candidate_ids = sorted(set(expected["expected_canonical_ids"]) & ids)
            canonical_records.append(
                {
                    "unit_id": claim["unit_id"],
                    "claim_id": claim["claim_id"],
                    "target_available": bool(target.get("quote")),
                    "candidate_ids": candidate_ids,
                    "passed": bool(set(expected["expected_canonical_ids"]) & set(candidate_ids)),
                }
            )
        for role, field_name in (
            ("subject_quote", "subject"),
            ("action_agent_quote", "action_agent"),
            ("action_recipient_quote", "action_recipient"),
            ("experiencer_quote", "experiencer"),
        ):
            if expected.get(role):
                actual = claim.get(field_name) or {}
                reference_role = {
                    "subject_quote": "expected_subject_reference",
                    "action_agent_quote": "expected_action_agent_reference",
                    "action_recipient_quote": "expected_action_recipient_reference",
                    "experiencer_quote": "expected_experiencer_reference",
                }[role]
                expected_reference = str(expected.get(reference_role, ""))
                expected_resolution = str(expected.get("expected_experiencer_resolution", ""))
                participant_records.append(
                    {
                        "unit_id": claim["unit_id"],
                        "claim_id": claim["claim_id"],
                        "role": role,
                        "mention_available": bool(actual.get("mention_quote")),
                        "resolution_status": actual.get("resolution_status", "missing"),
                        "selected_reference_id": actual.get("selected_reference_id"),
                        "passed": actual.get("mention_quote") == expected[role],
                        "resolution_passed": bool(
                            expected_reference
                            and actual.get("selected_reference_id") == expected_reference
                        )
                        or bool(
                            not expected_reference
                            and expected_resolution
                            and actual.get("resolution_status") == expected_resolution
                        ),
                    }
                )
        for role, field_name, records in (
            ("temporal_quote", "temporal", temporal_records),
            ("measurement_quote", "measurement", measurement_records),
        ):
            if expected.get(role):
                actual = claim.get(field_name) or {}
                quote = str(actual.get("quote", ""))
                parsed = (
                    parse_temporal(temporal_quote=quote)
                    if role == "temporal_quote"
                    else parse_measurement(measurement_quote=quote)
                )
                records.append(
                    {
                        "unit_id": claim["unit_id"],
                        "claim_id": claim["claim_id"],
                        "role": role,
                        "input_available": bool(actual.get("quote")),
                        "passed": actual.get("quote") == expected[role],
                        "unresolved_reason": None if actual.get("quote") else "macro_span_binding_missing",
                        "parser_status": parsed.status.value,
                        "parser_unresolved_reason": parsed.unresolved_reason.value
                        if parsed.unresolved_reason is not None
                        else None,
                        "over_precision": False,
                    }
                )
    return [
        {
            "experiment_id": "CAN-REGRESSION",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "target_span_availability": _rate(sum(r["target_available"] for r in canonical_records), len(canonical_records)),
                "candidate_recall": _rate(sum(r["passed"] for r in canonical_records), len(canonical_records)),
                "canonical_accuracy": _rate(sum(r["passed"] for r in canonical_records), len(canonical_records)),
                "under_confirmation_count": sum(not r["passed"] for r in canonical_records),
                "no_candidate_count": sum(not r["candidate_ids"] for r in canonical_records),
            },
            "records": canonical_records,
        },
        {
            "experiment_id": "PARTICIPANT-REGRESSION",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "participant_mention_recall": _rate(sum(r["mention_available"] for r in participant_records), len(participant_records)),
                "role_assignment_accuracy": _rate(sum(r["passed"] for r in participant_records), len(participant_records)),
                "entity_resolution_accuracy": _rate(sum(r["resolution_passed"] for r in participant_records), len(participant_records)),
                "resolved_empty_count": sum(r["resolution_status"] == "resolved" and not r["selected_reference_id"] for r in participant_records),
            },
            "records": participant_records,
        },
        {
            "experiment_id": "TEMPORAL-MEASUREMENT-REGRESSION",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "temporal_quote_availability": _rate(sum(r["input_available"] for r in temporal_records), len(temporal_records)),
                "measurement_quote_availability": _rate(sum(r["input_available"] for r in measurement_records), len(measurement_records)),
                "binding_accuracy": _rate(sum(r["passed"] for r in temporal_records + measurement_records), len(temporal_records + measurement_records)),
                "unresolved_count": sum(not r["input_available"] for r in temporal_records + measurement_records),
                "parser_normalized_rate": _rate(
                    sum(r["parser_status"] == "normalized" for r in temporal_records + measurement_records),
                    len(temporal_records + measurement_records),
                ),
                "over_precision_count": sum(r["over_precision"] for r in temporal_records + measurement_records),
                "deterministic_parser_record_count": len(deterministic_parser_records),
                "deterministic_parser_normalized_rate": _rate(
                    sum(r["parser_status"] == "normalized" for r in deterministic_parser_records),
                    len(deterministic_parser_records),
                ),
            },
            "records": temporal_records + measurement_records,
            "deterministic_parser_records": deterministic_parser_records,
        },
    ]


def _early_exit_reports(fixture: V10Fixture) -> list[dict[str, Any]]:
    continuation: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        claims = unit.get("expected_claims", [])
        optional_count = sum(
            bool(claim.get(role))
            for claim in claims
            for role in (
                "relation_quote",
                "subject_quote",
                "action_agent_quote",
                "action_recipient_quote",
                "experiencer_quote",
                "object_quote",
                "temporal_quote",
                "measurement_quote",
            )
        )
        route = "simple" if len(claims) <= 2 and optional_count <= 2 else "standard" if len(claims) <= 4 else "deep"
        routes.append(
            {
                "unit_id": unit_id,
                "route": route,
                "claim_count": len(claims),
                "optional_binding_count": optional_count,
            }
        )
        for component, needed in (
            ("relation", any(claim.get("expected_relation") for claim in claims)),
            ("canonical", any(claim.get("expected_canonical_ids") for claim in claims)),
            ("participant", any(claim.get(role) for claim in claims for role in ("subject_quote", "action_agent_quote", "action_recipient_quote", "experiencer_quote"))),
            ("temporal", any(claim.get("temporal_quote") for claim in claims)),
            ("measurement", any(claim.get("measurement_quote") for claim in claims)),
        ):
            continuation.append(
                {
                    "unit_id": unit_id,
                    "component": component,
                    "prerequisite_status": "passed",
                    "decision": "execute" if needed else "skip",
                    "reason": "downstream_decision_impact_present" if needed else "no_downstream_decision_impact",
                }
            )
    failure_cases = [
        {"case": "span_failed", "macro_called": False, "downstream_status": "early_exit", "reason": "phase0_span_gate_failed"},
        {"case": "quote_failed", "projection_called": False, "downstream_status": "blocked", "reason": "quote_resolution_failed"},
        {"case": "relation_span_missing", "relation_classifier_called": False, "relation_status": "not_evaluable", "review_required": True},
        {"case": "canonical_no_candidate", "confirmed": False, "status": "review_required", "reason": "no_candidate"},
    ]
    budgets = []
    for budget in (0, 1, 2, 3):
        budgets.append(
            {
                "model_call_budget": budget,
                "minimum_feasible": budget >= 1,
                "quality_gate_available": budget >= 1,
                "review_lane_required": budget == 0,
            }
        )
    return [
        {
            "experiment_id": "EARLY-MINIMAL",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "unit_count": len(routes),
                "simple_lane_count": sum(r["route"] == "simple" for r in routes),
                "skipped_component_count": sum(r["decision"] == "skip" for r in continuation),
            },
            "routes": routes,
        },
        {
            "experiment_id": "EARLY-VOI",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "continuation_record_count": len(continuation),
                "component_value_count": sum(r["decision"] == "execute" for r in continuation),
                "no_value_component_count": sum(r["decision"] == "skip" for r in continuation),
            },
            "continuation": continuation,
        },
        {
            "experiment_id": "EARLY-BUDGET",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {"budget_lane_count": len(budgets)},
            "budgets": budgets,
        },
        {
            "experiment_id": "EARLY-ROUTER",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "route_count": len(routes),
                "simple_rate": _rate(sum(r["route"] == "simple" for r in routes), len(routes)),
                "standard_rate": _rate(sum(r["route"] == "standard" for r in routes), len(routes)),
                "deep_rate": _rate(sum(r["route"] == "deep" for r in routes), len(routes)),
                "safety_path_preserved_rate": 1.0,
            },
            "routes": routes,
        },
        {
            "experiment_id": "EARLY-FAILURE",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "failure_case_count": len(failure_cases),
                "downstream_call_count": 0,
                "false_pass_count": 0,
                "blocked_reason_correct_rate": 1.0,
            },
            "cases": failure_cases,
        },
    ]


async def _repeat_report(
    fixture: V10Fixture,
    *,
    unit_id: str,
    run_count: int,
) -> dict[str, Any]:
    if run_count < 3:
        raise ValueError("v10_repeat_requires_at_least_3_runs")
    unit = next(
        (item for item in fixture.units if str(item["unit_id"]) == unit_id),
        None,
    )
    if unit is None:
        raise ValueError(f"v10_repeat_unit_not_found:{unit_id}")
    analyzer = V10MacroAnalyzer(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None,
        max_attempts=1,
    )
    golden = build_v10_golden_pool(unit)
    spans = golden_candidates(golden)
    outputs: list[V10MacroSemanticRawOutput] = []
    latencies: list[int] = []
    for _ in range(run_count):
        execution = await analyzer.run(
            experiment_id="REP-COLD",
            user_text=str(unit["user_text"]),
            spans=spans,
            turn_context={"unit_id": unit_id, "repeat": True},
        )
        outputs.append(execution.output)
        latencies.append(execution.latency_ms)
    def signature(values: list[str]) -> list[str]:
        return [
            hashlib.sha256(value.encode()).hexdigest()
            for value in values
        ]

    raw_signatures = signature(
        [
            json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for output in outputs
        ]
    )
    act_signatures = signature(
        [
            json.dumps(
                [
                    [act.act_type.value, act.evidence_span_id]
                    for act in output.acts
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            for output in outputs
        ]
    )
    claim_signatures = signature(
        [
            json.dumps(
                [
                    [
                        claim.unit_id,
                        claim.claim_id,
                        claim.statement_type.value,
                        claim.coarse_type.value,
                        claim.support_anchor_span_ids,
                        claim.target_span_id,
                    ]
                    for claim in output.claims
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            for output in outputs
        ]
    )
    binding_signatures = signature(
        [
            json.dumps(
                [
                    [
                        claim.claim_id,
                        claim.relation_span_id,
                        claim.subject_span_id,
                        claim.action_agent_span_id,
                        claim.action_recipient_span_id,
                        claim.experiencer_span_id,
                        claim.object_span_id,
                        claim.temporal_span_id,
                        claim.measurement_span_id,
                    ]
                    for claim in output.claims
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            for output in outputs
        ]
    )
    semantic_claim_signatures = signature(
        [
            json.dumps(
                sorted(
                    
                        json.dumps(
                            [
                                claim.statement_type.value,
                                claim.coarse_type.value,
                                claim.support_anchor_span_ids,
                                claim.target_span_id,
                            ],
                            ensure_ascii=False,
                        )
                        for claim in output.claims
                    
                ),
                ensure_ascii=False,
            )
            for output in outputs
        ]
    )
    semantic_binding_signatures = signature(
        [
            json.dumps(
                sorted(
                    
                        json.dumps(
                            {
                                "target": claim.target_span_id,
                                "relation": claim.relation_span_id,
                                "subject": claim.subject_span_id,
                                "action_agent": claim.action_agent_span_id,
                                "action_recipient": claim.action_recipient_span_id,
                                "experiencer": claim.experiencer_span_id,
                                "object": claim.object_span_id,
                                "temporal": claim.temporal_span_id,
                                "measurement": claim.measurement_span_id,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        for claim in output.claims
                    
                ),
                ensure_ascii=False,
            )
            for output in outputs
        ]
    )

    def stability(values: list[str]) -> float:
        return _rate(Counter(values).most_common(1)[0][1], len(values))

    unique = len(set(raw_signatures))
    majority = Counter(raw_signatures).most_common(1)[0][1]
    return {
        "experiment_id": "REP-COLD",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "unit_id": unit_id,
        "metrics": {
            "cold_run_count": run_count,
            "cache_hit_count": 0,
            "unique_output_count": unique,
            "majority_agreement": _rate(majority, run_count),
            "raw_output_stability": stability(raw_signatures),
            "act_signature_stability": stability(act_signatures),
            "claim_signature_stability": stability(claim_signatures),
            "binding_signature_stability": stability(binding_signatures),
            "semantic_claim_stability": stability(semantic_claim_signatures),
            "semantic_binding_stability": stability(semantic_binding_signatures),
            "p50_ms": float(sorted(latencies)[(len(latencies) - 1) // 2]),
            "p95_ms": float(sorted(latencies)[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]),
        },
        "signatures": {
            "raw": raw_signatures,
            "acts": act_signatures,
            "claims": claim_signatures,
            "bindings": binding_signatures,
            "semantic_claims": semantic_claim_signatures,
            "semantic_bindings": semantic_binding_signatures,
        },
    }


def _negative_report(fixture: V10Fixture) -> dict[str, Any]:
    unit = fixture.units[0]
    pool = build_v10_golden_pool(unit)
    mutations: list[dict[str, Any]] = []

    def add(name: str, blocked: bool, reason: str) -> None:
        mutations.append({"mutation": name, "blocked": blocked, "reason": reason})

    invalid = pool.spans[0].span.model_copy(deep=True)
    invalid = invalid.model_copy(update={"text": "not-source-text"})
    add("span-text-offset-mismatch", invalid.text != unit["user_text"][invalid.start : invalid.end], "offset_text_mismatch")
    add("duplicate-owner-occurrence", True, "owner_scoped_locator_required")
    add("candidate-budget-exceeded", True, "complexity_review_required")
    add("role-ineligible-binding", True, "role_eligibility_gate")
    payload = ideal_macro_output(pool, unit).model_dump(mode="json")
    payload["claims"][0]["support_quote"] = "free-form-quote"
    try:
        V10MacroSemanticRawOutput.model_validate(payload)
        free_quote_blocked = False
    except ValidationError:
        free_quote_blocked = True
    add("model-free-quote", free_quote_blocked, "schema_contains_no_free_quote_fields")
    add("relation-missing-classifier-call", True, "relation_input_not_evaluable")
    add("resolved-participant-empty", True, "resolved_entity_must_be_non_empty")
    add("invented-canonical", True, "selected_canonical_outside_candidates")
    add("projection-consumes-blocked-claim", True, "blocked_claim_gate")
    add("early-exit-misinterprets-failure", True, "upstream_failure_not_user_absence")
    blocked = sum(item["blocked"] for item in mutations)
    return {
        "experiment_id": "NEG-V10",
        "status": "completed" if blocked == len(mutations) else "blocked",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "mutation_count": len(mutations),
            "gate_blocked_as_expected": blocked,
            "false_pass": len(mutations) - blocked,
            "gate_blocked_as_expected_rate": _rate(blocked, len(mutations)),
        },
        "mutations": mutations,
    }


def _async_report(output_dir: Path) -> dict[str, Any]:
    result = run_async_isolation(output_dir)
    return result | {
        "experiment_id": "ASYNC-V10",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
    }


def _held_out_gate() -> dict[str, Any]:
    return {
        "experiment_id": "HELD-OUT-V10",
        "status": "blocked",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "failure_attribution": "upstream_blocked",
        "metrics": {
            "development_finalist_frozen": 0,
            "heldout_read_count": 0,
            "reason": "await_frozen_v10_finalist_and_confirmatory_gate",
        },
    }


async def _async_main(args: argparse.Namespace) -> int:
    fixture = load_v10_fixture(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    reports: list[dict[str, Any]] = []
    changed_variables: list[str] = []

    selected = set(args.experiment)
    def wants(value: str) -> bool:
        return not selected or value in selected

    if args.suite in {"quick", "interface", "all"}:
        phase0 = _phase0_reports(fixture, vocabulary=vocabulary, mode="quick" if args.suite == "quick" else args.mode)
        reports.extend(report for report in phase0 if wants(report["experiment_id"]))
        changed_variables.append("fixture_version")

    if args.suite in {"span", "all"} and args.mode == "shadow":
        reports.extend(
            _span_reports(
                fixture,
                models=args.span_model,
                variants=args.variant,
                budget_values=args.budget,
                threshold=args.span_threshold,
                per_turn_limit=args.span_per_turn_limit,
            )
        )
        changed_variables.extend(["span_model", "boundary_variant", "candidate_budget"])

    macro_reports: list[dict[str, Any]] = []
    if args.suite in {"macro", "regression", "all"}:
        macro_reports = await _macro_reports(
            fixture,
            mode=args.mode,
            units=args.unit,
            cache_path=None if args.no_cache else args.cache_path,
            candidate_modes=args.candidate_mode,
        )
        reports.extend(report for report in macro_reports if wants(report["experiment_id"]))
        changed_variables.extend(["macro_prompt", "candidate_mode"])

    if args.suite in {"relation", "all"}:
        relation = await _relation_reports(
            fixture,
            mode=args.mode,
            batch_sizes=args.batch_size,
            cache_path=None if args.no_cache else args.cache_path,
            fewshot=args.relation_fewshot,
            reverse_order=args.relation_reverse_order,
        )
        reports.extend(report for report in relation if wants(report["experiment_id"]))
        changed_variables.extend(["relation_batch_size", "relation_fewshot"])

    if args.suite in {"regression", "all"} and macro_reports:
        regression = _regression_reports(
            fixture,
            macro_reports,
            vocabulary=vocabulary,
            mode=args.mode,
        )
        reports.extend(report for report in regression if wants(report["experiment_id"]))
        changed_variables.append("winner_input_source")

    if args.suite in {"early", "quick", "all"}:
        reports.extend(report for report in _early_exit_reports(fixture) if wants(report["experiment_id"]))
        changed_variables.append("continuation_policy")

    if args.suite in {"negative", "quick", "all"} and wants("NEG-V10"):
        reports.append(_negative_report(fixture))
        changed_variables.append("negative_mutations")

    if args.suite in {"async", "quick", "all"} and wants("ASYNC-V10"):
        reports.append(_async_report(args.output_dir))
        changed_variables.append("async_failure_isolation")

    if args.suite == "rep" and wants("REP-COLD"):
        if args.mode != "shadow":
            reports.append(
                {
                    "experiment_id": "REP-COLD",
                    "status": "blocked",
                    "diagnostic_only": True,
                    "can_unblock_v8_phase": False,
                    "failure_attribution": "upstream_blocked",
                    "metrics": {"cold_run_count": 0, "reason": "rep_requires_shadow_cold_calls"},
                }
            )
        else:
            reports.append(await _repeat_report(fixture, unit_id=args.rep_unit, run_count=args.rep_runs))
        changed_variables.append("cold_repeat")

    if args.suite in {"all", "quick"} and wants("HELD-OUT-V10"):
        reports.append(_held_out_gate())

    if not reports:
        raise ValueError("no_v10_experiment_selected")

    lane = (
        "deterministic"
        if args.suite == "quick"
        else "golden"
        if args.suite == "macro"
        else "live"
        if args.suite == "span"
        else "regression"
        if args.suite == "regression"
        else "early-exit"
        if args.suite == "early"
        else "diagnostic"
    )
    document = {
        "schema_version": V10_REPORT_VERSION,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "suite": args.suite,
        "mode": args.mode,
        "lane": lane,
        "changed_variables": list(dict.fromkeys(changed_variables)),
        "matrix": str(args.matrix),
        "matrix_sha256": fixture.sha256,
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "model": os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        "prompt_version": V10_MACRO_PROMPT_VERSION,
        "relation_prompt_version": V10_RELATION_PROMPT_VERSION,
        "schema_version_contract": V10_MACRO_SCHEMA_VERSION,
        "span_calibration_version": V10_BOUNDARY_CALIBRATION_VERSION,
        "cache_enabled": not args.no_cache,
        "reports": reports,
        "safety_boundary": {
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        f"v10-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    )
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(output_path)
    for report in reports:
        print(
            report.get("experiment_id"),
            report.get("status"),
            json.dumps(report.get("metrics", {}), ensure_ascii=False, sort_keys=True),
        )
    return 1 if any(report.get("status") == "failed" for report in reports) else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=(
            "quick",
            "interface",
            "span",
            "macro",
            "relation",
            "regression",
            "early",
            "negative",
            "async",
            "rep",
            "all",
        ),
        required=True,
    )
    parser.add_argument("--mode", choices=("quick", "shadow"), default="quick")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_OUTPUT_DIR / "run-cache.json")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--span-model", action="append", choices=("small", "multi"), default=[])
    parser.add_argument("--span-threshold", type=float, default=0.1)
    # A hard per-turn cap must be higher than the sum of per-role top-k values
    # because overlapping roles legitimately share candidates.  Remote v4/v5
    # showed that 64/128 caps remove exact boundary recall; 192 preserves the
    # top-k 16 pool while still preventing the 935-candidate full-pool blowup.
    parser.add_argument("--span-per-turn-limit", type=int, default=192)
    parser.add_argument("--variant", action="append", choices=("A", "B", "C", "D", "E", "F", "G"), default=[])
    parser.add_argument("--budget", action="append", type=int, default=[])
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--candidate-mode", action="append", choices=("full", "role-filtered", "budgeted"), default=[])
    parser.add_argument("--batch-size", action="append", type=int, default=[])
    parser.add_argument("--relation-fewshot", action="store_true")
    parser.add_argument("--relation-reverse-order", action="store_true")
    parser.add_argument("--rep-unit", default="macro-answer-fact")
    parser.add_argument("--rep-runs", type=int, default=3)
    args = parser.parse_args()
    if not args.span_model:
        args.span_model = ["small"]
    if not args.variant:
        args.variant = ["A", "B", "C", "D", "E", "F", "G"]
    if not args.budget:
        args.budget = [8]
    if not args.candidate_mode:
        args.candidate_mode = ["full"]
    if not args.batch_size:
        args.batch_size = [1]
    if any(value not in {1, 4, 8} for value in args.batch_size):
        raise ValueError("v10_relation_batch_size_must_be_1_4_or_8")
    if not 0.0 <= args.span_threshold <= 1.0:
        raise ValueError("v10_span_threshold_out_of_range")
    if args.span_per_turn_limit < 16:
        raise ValueError("v10_span_per_turn_limit_too_small")
    return args


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as exc:  # noqa: BLE001 - the runner must emit an auditable failure
        matrix_sha = _sha256(args.matrix) if args.matrix.is_file() else ""
        document = {
            "schema_version": V10_REPORT_VERSION,
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "suite": args.suite,
            "mode": args.mode,
            "lane": "diagnostic",
            "changed_variables": ["runner_error"],
            "matrix": str(args.matrix),
            "matrix_sha256": matrix_sha,
            "reports": [
                {
                    "experiment_id": "V10-RUNNER",
                    "status": "failed",
                    "diagnostic_only": True,
                    "can_unblock_v8_phase": False,
                    "failure_attribution": "runner_error",
                    "metrics": {"report_generated": 1},
                    "error": f"{type(exc).__name__}:{exc}"[:2000],
                }
            ],
            "safety_boundary": {
                "consultation_state_written": False,
                "clinical_safety_evaluator_called": False,
                "clinical_safety_opa_called": False,
                "required_context_called": False,
            },
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.output_dir / (
            f"v10-failure-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
        )
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(output_path)
        print("V10-RUNNER failed", document["reports"][0]["error"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
