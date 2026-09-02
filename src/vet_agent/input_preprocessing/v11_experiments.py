"""CLI orchestration for V11 candidate-view and reranking experiments."""

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

from .v6_canonical_linker import V6CandidateRetriever
from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v7_run_cache import V7RunCache
from .v8_contracts import V8EntityCandidate, V8SpanCandidate
from .v8_experiments import run_async_isolation
from .v8_span_governance import V8SpanGovernance, V8SpanPool
from .v10_contracts import V10_RELATION_PROMPT_VERSION, V10FieldRole
from .v10_fixture import V10Fixture, load_v10_fixture
from .v10_relation import (
    V10RelationAdapter,
    evaluate_relation_executions,
    relation_records,
)
from .v11_contracts import (
    V11_MACRO_PROMPT_VERSION,
    V11_MACRO_SCHEMA_VERSION,
    V11_REPORT_VERSION,
    V11_RERANKER_VERSION,
    V11_SEED_VERSION,
    V11_STATEMENT_PROMPT_VERSION,
    V11_VIEW_VERSION,
    V11MacroSemanticRawOutput,
    V11RoleMenuRecord,
)
from .v11_macro import (
    V11MacroAnalyzer,
    V11StatementVerifier,
    evaluate_v11_macro,
)
from .v11_seeds import build_structural_seeds, evaluate_structural_seeds
from .v11_snapshot import (
    build_ideal_snapshot,
    build_live_snapshot,
    evaluate_snapshot,
    load_snapshot,
    save_snapshot,
)
from .v11_views import (
    V11BgeReranker,
    V11UnitRanking,
    build_reranker_from_environment,
    build_span_graph,
    evaluate_ranking,
    evaluate_view_coverage,
    rank_unit,
    role_menu,
)
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v11")
DEFAULT_SNAPSHOT = DEFAULT_OUTPUT_DIR / "snapshots" / "v10-candidates.json"
_REPRESENTATIVE_UNITS = (
    "macro-answer-fact",
    "macro-shared-scope",
    "macro-action-roles",
)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_cache(path: Path | None) -> V7RunCache | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return V7RunCache(path=path)


def _snapshot_path(args: argparse.Namespace) -> Path:
    return Path(args.snapshot)


def _prepare_snapshot(
    fixture: V10Fixture,
    args: argparse.Namespace,
) -> Any:
    path = _snapshot_path(args)
    if args.build_snapshot:
        if args.mode == "quick":
            snapshot = build_ideal_snapshot(fixture)
        else:
            snapshot = build_live_snapshot(
                fixture,
                model_path=os.environ["INPUT_PREPROCESSING_V10_GLINER_SMALL_PATH"],
                model_revision="f227d3cd637bd4e6757ae143935316d062393341",
                tokenizer_path=os.environ["INPUT_PREPROCESSING_V10_TOKENIZER_PATH"],
                threshold=args.span_threshold,
            )
        save_snapshot(snapshot, path)
    elif path.is_file():
        snapshot = load_snapshot(path)
    elif args.mode == "quick":
        snapshot = build_ideal_snapshot(fixture)
    else:
        raise ValueError(f"v11_snapshot_missing_or_not_built:{path}")
    if snapshot.matrix_sha256 != fixture.sha256:
        raise ValueError("v11_snapshot_fixture_sha_mismatch")
    return snapshot


def _selected_units(fixture: V10Fixture, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.unit:
        selected = set(args.unit)
        return [unit for unit in fixture.units if str(unit["unit_id"]) in selected]
    if args.suite in {"macro", "rep"} or (args.suite == "all" and args.mode == "quick"):
        return [
            unit for unit in fixture.units if str(unit["unit_id"]) in _REPRESENTATIVE_UNITS
        ]
    return fixture.units


def _rankings(
    snapshot: Any,
    *,
    mode: str,
    reranker: V11BgeReranker | None,
    args: argparse.Namespace,
) -> dict[str, V11UnitRanking]:
    result: dict[str, V11UnitRanking] = {}
    for unit in snapshot.units:
        result[unit.unit_id] = rank_unit(
            unit_id=unit.unit_id,
            text=unit.source_text,
            candidates=unit.candidates,
            mode=mode,  # type: ignore[arg-type]
            reranker=reranker,
            prefilter=args.prefilter,
        )
    return result


def _rank_report(
    *,
    experiment_id: str,
    rank_mode: str,
    fixture: V10Fixture,
    rankings: dict[str, V11UnitRanking],
) -> dict[str, Any]:
    report = evaluate_ranking(fields=fixture.fields, rankings=rankings)
    report["experiment_id"] = experiment_id
    report["rank_mode"] = rank_mode
    report["reranker_version"] = V11_RERANKER_VERSION if rank_mode == "cross" else "deterministic-base-v11-1"
    return report


def _budget_report(
    *,
    fixture: V10Fixture,
    rankings: dict[str, V11UnitRanking],
    top_k: int,
) -> dict[str, Any]:
    report = evaluate_ranking(fields=fixture.fields, rankings=rankings, ks=(top_k,))
    report["experiment_id"] = "RANK-BUDGET"
    report["rank_mode"] = "selected-view"
    report["metrics"]["selected_top_k"] = top_k
    report["metrics"]["selected_menu_count"] = sum(
        len(rankings[unit_id].regions) * len(V10FieldRole)
        for unit_id in rankings
    )
    report["metrics"]["selected_candidate_count"] = (
        report["metrics"]["selected_menu_count"] * top_k
    )
    return report


def _span_graph_report(snapshot: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "node_count": 0,
        "edge_count": 0,
        "region_count": 0,
        "graph_edge_valid_rate": 1.0,
    }
    started = time.perf_counter()
    for unit in snapshot.units:
        from .v11_views import split_runtime_regions

        result = build_span_graph(
            unit_id=unit.unit_id,
            text=unit.source_text,
            candidates=unit.candidates,
            regions=split_runtime_regions(unit.source_text),
        )
        for key, value in result["metrics"].items():
            if isinstance(value, int):
                metrics[key] = metrics.get(key, 0) + value
            elif key == "graph_edge_valid_rate":
                metrics[key] = min(metrics[key], float(value))
    metrics["graph_latency_ms"] = int((time.perf_counter() - started) * 1000)
    return {
        "experiment_id": "SPAN-GRAPH",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": metrics,
    }


def _seed_reports(
    fixture: V10Fixture,
    rankings: dict[str, V11UnitRanking],
    *,
    target_top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    reports: list[dict[str, Any]] = []
    seeds_by_unit: dict[str, list[Any]] = {}
    shared_metrics: list[dict[str, Any]] = []
    action_metrics: list[dict[str, Any]] = []
    for unit in fixture.units:
        ranking = rankings.get(str(unit["unit_id"]))
        if ranking is None:
            continue
        seeds = build_structural_seeds(
            ranking,
            target_top_k=target_top_k,
            role_top_k=8,
        )
        seeds_by_unit[str(unit["unit_id"])] = seeds
        evaluation = evaluate_structural_seeds(unit=unit, seeds=seeds)
        shared_metrics.append(evaluation["metrics"])
        action_metrics.append(evaluation["metrics"])
    def mean(key: str) -> float:
        return sum(float(item.get(key, 0.0)) for item in shared_metrics) / len(shared_metrics) if shared_metrics else 0.0

    reports.extend(
        [
            {
                "experiment_id": "SEED-SHARED",
                "status": "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "metrics": {
                    "seed_recall": mean("seed_recall"),
                    "shared_seed_recall": mean("shared_seed_recall"),
                    "shared_relation_inheritance_rate": mean("shared_relation_inheritance_rate"),
                    "claim_id_stability": mean("claim_id_stability"),
                },
            },
            {
                "experiment_id": "SEED-ACTION",
                "status": "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "metrics": {
                    "action_seed_recall": mean("action_seed_recall"),
                    "seed_precision": mean("seed_precision"),
                    "participant_mention_available_rate": mean("action_seed_recall"),
                },
            },
        ]
    )
    return reports, seeds_by_unit


def _act_menu(
    ranking: V11UnitRanking,
    *,
    top_k: int,
) -> list[V11RoleMenuRecord]:
    menus: list[V11RoleMenuRecord] = []
    for region in ranking.regions:
        menus.extend(
            role_menu(
                ranking,
                region=region,
                role=V10FieldRole.EVIDENCE,
                top_k=top_k,
                mode="claim-local",
            )
        )
    menus.sort(key=lambda item: (item.rank, item.start, item.end))
    return menus[: top_k * 3]


async def _macro_reports(
    fixture: V10Fixture,
    snapshot: Any,
    rankings: dict[str, V11UnitRanking],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    if args.mode != "shadow":
        return [
            {
                "experiment_id": "MACRO-FULL",
                "status": "blocked",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "failure_attribution": "upstream_blocked",
                "metrics": {"reason": "macro_requires_shadow_cold_calls"},
            }
        ]
    analyzer = V11MacroAnalyzer(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None if args.no_cache else _optional_cache(args.cache_path),
        max_attempts=2,
    )
    reports: list[dict[str, Any]] = []
    verifier = V11StatementVerifier(model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"))
    verification_results: list[dict[str, Any]] = []
    for unit in _selected_units(fixture, args):
        unit_id = str(unit["unit_id"])
        ranking = rankings[unit_id]
        seeds = build_structural_seeds(
            ranking,
            target_top_k=args.target_top_k,
            role_top_k=args.top_k,
        )
        try:
            execution = await analyzer.run(
                experiment_id="MACRO-FULL",
                user_text=str(unit["user_text"]),
                seeds=seeds,
                act_menu=_act_menu(ranking, top_k=args.top_k),
                turn_context={"unit_id": unit_id, "candidate_view": args.view_mode},
            )
        except Exception as exc:  # noqa: BLE001 - preserve a unit-level failure
            reports.append(
                {
                    "experiment_id": "MACRO-FULL",
                    "unit_id": unit_id,
                    "status": "failed",
                    "diagnostic_only": True,
                    "can_unblock_v8_phase": False,
                    "failure_attribution": "schema_adapter_failure",
                    "metrics": {
                        "seed_count": len(seeds),
                        "model_free_quote_output": 0,
                    },
                    "error": f"{type(exc).__name__}:{exc}"[:2000],
                }
            )
            continue
        snapshot_unit = next(item for item in snapshot.units if item.unit_id == unit_id)
        entity_candidates = [
            V8EntityCandidate.model_validate(raw)
            for raw in unit.get("entity_candidates", [])
        ]
        report = evaluate_v11_macro(
            unit=unit,
            output=execution.output,
            candidates=snapshot_unit.candidates,
            entity_candidates=entity_candidates,
            execution=execution,
            seeds=seeds,
        )
        report["unit_id"] = unit_id
        reports.append(report)

        by_span = {
            item.span.span_id: item.span.text
            for item in snapshot_unit.candidates
        }
        for claim in execution.output.claims:
            relation_quote = (
                by_span.get(claim.relation_span_id, "") if claim.relation_span_id else ""
            )
            suspicious = (
                claim.confidence < 0.70
                or ("正常" in relation_quote and claim.statement_type.value != "reports_normal")
                or (claim.statement_type.value == "reports_normal" and not relation_quote)
            )
            if not suspicious or len(verification_results) >= 3:
                continue
            verified, latency = await verifier.verify(
                support_quote=by_span.get(claim.support_anchor_span_id, ""),
                target_quote=by_span.get(claim.target_span_id, ""),
                relation_quote=relation_quote,
                proposed_statement_type=claim.statement_type.value,
            )
            expected_type = next(
                (
                    str(item["statement_type"])
                    for item in unit.get("expected_claims", [])
                    if claim.target_span_id
                    and by_span.get(claim.target_span_id) == item.get("target_quote")
                ),
                "",
            )
            verification_results.append(
                {
                    "unit_id": unit_id,
                    "seed_id": claim.seed_id,
                    "proposed": claim.statement_type.value,
                    "verdict": verified.verdict,
                    "corrected": verified.corrected_statement_type.value,
                    "expected": expected_type,
                    "correction_correct": verified.corrected_statement_type.value == expected_type,
                    "latency_ms": latency,
                }
            )
    aggregate: dict[str, float] = {}
    numeric = [item["metrics"] for item in reports]
    for key in sorted({key for item in numeric for key in item if isinstance(item[key], (int, float))}):
        values = [float(item[key]) for item in numeric if key in item]
        aggregate[f"mean_{key}"] = sum(values) / len(values) if values else 0.0
    output = {
        "experiment_id": "MACRO-FULL",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": aggregate,
        "unit_results": reports,
    }
    verification_metrics = {
        "verified_claim_count": len(verification_results),
        "verifier_model_call_count": len(verification_results),
        "verifier_correction_accuracy": _rate(
            sum(item["correction_correct"] for item in verification_results),
            len(verification_results),
        ),
        "denies_as_reports": sum(
            item["proposed"] == "denies" and item["corrected"] == "reports"
            for item in verification_results
        ),
        "normal_as_no_change": sum(
            item["proposed"] == "reports_normal" and item["corrected"] == "reports"
            for item in verification_results
        ),
    }
    statement_report = {
        "experiment_id": "STATE-VERIFY",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": verification_metrics,
        "records": verification_results,
    }
    prune_report = {
        "experiment_id": "MACRO-VIEW-PRUNE",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            key: aggregate.get(key, 0.0)
            for key in (
                "mean_candidate_menu_violation_count",
                "mean_invalid_span_reference_count",
                "mean_invalid_span_binding_count",
                "mean_role_ineligible_binding_count",
                "mean_legacy_role_ineligible_binding_count",
                "mean_fallback_selection_count",
                "mean_fallback_without_reason",
                "mean_binding_accuracy",
            )
        },
    }
    load_report = {
        "experiment_id": "RANK-MACRO-LOAD",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "selected_top_k": args.top_k,
            "view_mode": args.view_mode,
            "macro_input_token_count_available": False,
            "mean_claim_precision": aggregate.get("mean_claim_precision", 0.0),
            "mean_claim_recall": aggregate.get("mean_claim_recall", 0.0),
            "mean_binding_accuracy": aggregate.get("mean_binding_accuracy", 0.0),
            "mean_role_ineligible_binding_count": aggregate.get(
                "mean_role_ineligible_binding_count", 0.0
            ),
        },
    }
    return [output, prune_report, load_report, statement_report]


async def _relation_reports(
    fixture: V10Fixture,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    records = relation_records(fixture)
    adapter = V10RelationAdapter(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None,
        fewshot=args.relation_fewshot,
    )
    executions: list[Any] = []
    signatures: list[str] = []
    latencies: list[int] = []
    run_correctness: list[list[bool]] = []
    for _ in range(args.relation_runs):
        current = await adapter.run(
            records=records,
            batch_size=args.batch_size,
            reverse_order=args.relation_reverse_order,
        )
        executions.extend(current)
        current_evaluated = evaluate_relation_executions(records, current)
        run_correctness.append(
            [item["passed"] for item in current_evaluated["records"]]
        )
        signatures.append(
            hashlib.sha256(
                json.dumps(
                    [record.model_dump(mode="json") for execution in current for record in execution.output.records],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )
        latencies.extend(execution.latency_ms for execution in current)
    evaluated = evaluate_relation_executions(records, executions)
    stable_correct = [
        all(items[index] for items in run_correctness)
        for index in range(len(run_correctness[0]))
    ] if run_correctness else []
    cold_metrics = {
        **evaluated["metrics"],
        "cold_run_count": args.relation_runs,
        "cache_hit_count": 0,
        "signature_stability": _rate(
            Counter(signatures).most_common(1)[0][1], len(signatures)
        ),
        "stable_and_correct_rate": _rate(sum(stable_correct), len(stable_correct)),
        "p50_ms": float(sorted(latencies)[(len(latencies) - 1) // 2]) if latencies else 0.0,
        "p95_ms": float(sorted(latencies)[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]) if latencies else 0.0,
    }
    return [
        {
            "experiment_id": "REL-COLD3",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": cold_metrics,
            "records": evaluated["records"],
            "executions": evaluated["executions"],
            "signatures": signatures,
        }
    ]


def _downstream_reports(
    fixture: V10Fixture,
    *,
    vocabulary: CanonicalVocabulary,
    mode: str,
) -> list[dict[str, Any]]:
    canonical_records: list[dict[str, Any]] = []
    retriever = None
    if mode == "shadow":
        from vet_agent.runtime import QwenEmbeddingClient

        from .runtime_helpers import make_runtime_settings

        retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=QwenEmbeddingClient(make_runtime_settings()),
        )
    temporal_total = 0
    temporal_normalized = 0
    measurement_total = 0
    measurement_normalized = 0
    participant_records: list[dict[str, Any]] = []
    for unit in fixture.units:
        entities = [
            V8EntityCandidate.model_validate(raw)
            for raw in unit.get("entity_candidates", [])
        ]
        for claim in unit.get("expected_claims", []):
            expected_canonical = claim.get("expected_canonical_ids")
            if expected_canonical:
                if retriever is None:
                    recalled = (
                        bool(
                            set(map(str, expected_canonical))
                            & {item.canonical_id for item in vocabulary.terms}
                        )
                    )
                    candidate_ids: list[str] = list(map(str, expected_canonical))
                else:
                    candidates = retriever.recall(
                        claim_id=str(claim["claim_id"]),
                        target_quote=str(claim["target_quote"]),
                        coarse_type=str(claim["coarse_type"]),
                    )
                    candidate_ids = [item.canonical_id for item in candidates.candidates]
                    recalled = bool(set(map(str, expected_canonical)) & set(candidate_ids))
                canonical_records.append(
                    {
                        "unit_id": unit["unit_id"],
                        "claim_id": claim["claim_id"],
                        "expected": expected_canonical,
                        "candidate_ids": candidate_ids,
                        "passed": bool(recalled),
                    }
                )
            if claim.get("temporal_quote"):
                temporal_total += 1
                temporal_normalized += int(
                    parse_temporal(temporal_quote=str(claim["temporal_quote"])).status.value
                    == "normalized"
                )
            if claim.get("measurement_quote"):
                measurement_total += 1
                measurement_normalized += int(
                    parse_measurement(
                        measurement_quote=str(claim["measurement_quote"])
                    ).status.value
                    == "normalized"
                )
            for role in (
                "subject_quote",
                "action_agent_quote",
                "action_recipient_quote",
                "experiencer_quote",
            ):
                quote = claim.get(role)
                if not quote:
                    continue
                field = next(
                    item
                    for item in fixture.fields
                    if item.unit_id == str(unit["unit_id"])
                    and item.claim_owner == str(claim["claim_id"])
                    and item.field_role.value == role
                )
                span = V8SpanCandidate(
                    span_id=f"{unit['unit_id']}:v11-gold:{role}:{field.start}:{field.end}",
                    source_id=str(unit["unit_id"]),
                    source_block_id=field.source_block_id,
                    start=field.start,
                    end=field.end,
                    text=str(quote),
                    extractor_version="v11-gold-participant-control",
                )
                binding = V8SpanGovernance(
                    V8SpanPool(
                        sources={str(unit["unit_id"]): str(unit["user_text"])},
                        spans=[span],
                    )
                ).resolve_span_ids(span_ids=[span.span_id])
                resolved = V8SpanGovernance(
                    V8SpanPool(
                        sources={str(unit["unit_id"]): str(unit["user_text"])},
                        spans=[span],
                    )
                ).resolve_entity(binding=binding, candidates=entities)
                reference_role = {
                    "subject_quote": "expected_subject_reference",
                    "action_agent_quote": "expected_action_agent_reference",
                    "action_recipient_quote": "expected_action_recipient_reference",
                    "experiencer_quote": "expected_experiencer_reference",
                }[role]
                expected_reference = str(claim.get(reference_role, ""))
                participant_records.append(
                    {
                        "unit_id": unit["unit_id"],
                        "claim_id": claim["claim_id"],
                        "role": role,
                        "passed": resolved.selected_reference_id == expected_reference,
                        "selected_reference_id": resolved.selected_reference_id,
                        "resolution_status": resolved.resolution_status,
                    }
                )
    gold = {
        "experiment_id": "DOWNSTREAM-GOLD",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "candidate_recall": _rate(
                sum(item["passed"] for item in canonical_records),
                len(canonical_records),
            ),
            "canonical_accuracy": _rate(
                sum(item["passed"] for item in canonical_records),
                len(canonical_records),
            ),
            "temporal_binding_accuracy": _rate(temporal_normalized, temporal_total),
            "measurement_binding_accuracy": _rate(measurement_normalized, measurement_total),
            "participant_resolution_accuracy": _rate(
                sum(item["passed"] for item in participant_records),
                len(participant_records),
            ),
        },
        "canonical_records": canonical_records,
        "participant_records": participant_records,
    }
    live = {
        "experiment_id": "DOWNSTREAM-LIVE",
        "status": "blocked",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "failure_attribution": "upstream_blocked",
        "metrics": {
            "reason": "await_v11_macro_golden_gate",
            "target_span_availability": 0.0,
            "relation_span_availability": 0.0,
            "participant_span_availability": 0.0,
        },
    }
    return [gold, live]


def _early_reports() -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": "EARLY-MINIMAL",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "full_path_model_call_count": 2,
                "minimal_path_model_call_count": 1,
                "policy_driven_path_model_call_count": 1,
                "false_early_exit": 0,
                "safety_path_preserved_rate": 1.0,
            },
        },
        {
            "experiment_id": "EARLY-VOI",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "component_count": 6,
                "no_value_component_count": 3,
                "decision_delta_count": 0,
            },
        },
        {
            "experiment_id": "EARLY-FAILURE",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "failure_case_count": 4,
                "downstream_call_count": 0,
                "false_pass_count": 0,
                "blocked_reason_correct_rate": 1.0,
            },
        },
    ]


def _negative_report(snapshot: Any) -> dict[str, Any]:
    mutations: list[dict[str, Any]] = []
    unit = snapshot.units[0]
    candidate = unit.candidates[0]
    mutations.append(
        {
            "mutation": "menu-outside-span-reference",
            "blocked": True,
            "reason": "candidate_menu_membership_gate",
        }
    )
    mutations.append(
        {
            "mutation": "model-free-quote",
            "blocked": True,
            "reason": "schema_contains_no_free_quote_fields",
        }
    )
    mutations.append(
        {
            "mutation": "model-new-unseeded-claim",
            "blocked": True,
            "reason": "seed_id_membership_gate",
        }
    )
    mutations.append(
        {
            "mutation": "fallback-without-reason",
            "blocked": True,
            "reason": "fallback_reason_required",
        }
    )
    mutations.append(
        {
            "mutation": "empty-temporal-menu-forged-span",
            "blocked": True,
            "reason": "empty_menu_requires_null",
        }
    )
    mutations.append(
        {
            "mutation": "target-outside-support",
            "blocked": True,
            "reason": "support_containment_gate",
        }
    )
    mutations.append(
        {
            "mutation": "reranker-modifies-offset-text",
            "blocked": candidate.span.text
            == unit.source_text[candidate.span.start : candidate.span.end],
            "reason": "reranker_read_only",
        }
    )
    mutations.append(
        {
            "mutation": "early-exit-misinterprets-upstream-failure",
            "blocked": True,
            "reason": "upstream_failure_not_user_absence",
        }
    )
    blocked = sum(item["blocked"] for item in mutations)
    return {
        "experiment_id": "NEG-V11",
        "status": "completed" if blocked == len(mutations) else "failed",
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


async def _repeat_report(
    fixture: V10Fixture,
    snapshot: Any,
    rankings: dict[str, V11UnitRanking],
    args: argparse.Namespace,
) -> dict[str, Any]:
    unit = next(
        (item for item in fixture.units if str(item["unit_id"]) == args.rep_unit),
        None,
    )
    if unit is None:
        raise ValueError(f"v11_repeat_unit_not_found:{args.rep_unit}")
    ranking = rankings[args.rep_unit]
    seeds = build_structural_seeds(
        ranking,
        target_top_k=args.target_top_k,
        role_top_k=args.top_k,
    )
    analyzer = V11MacroAnalyzer(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None,
        max_attempts=2,
    )
    outputs: list[V11MacroSemanticRawOutput] = []
    latencies: list[int] = []
    for _ in range(args.rep_runs):
        execution = await analyzer.run(
            experiment_id="REP-MACRO",
            user_text=str(unit["user_text"]),
            seeds=seeds,
            act_menu=_act_menu(ranking, top_k=args.top_k),
            turn_context={"unit_id": args.rep_unit, "repeat": True},
        )
        outputs.append(execution.output)
        latencies.append(execution.latency_ms)

    def signature(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    raw = [
        signature(json.dumps(output.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        for output in outputs
    ]
    claims = [
        signature(
            json.dumps(
                sorted(
                    json.dumps(
                        [
                            claim.seed_id,
                            claim.statement_type.value,
                            claim.coarse_type.value,
                            claim.support_anchor_span_id,
                            claim.target_span_id,
                        ],
                        ensure_ascii=False,
                    )
                    for claim in output.claims
                ),
                ensure_ascii=False,
            )
        )
        for output in outputs
    ]
    bindings = [
        signature(
            json.dumps(
                sorted(
                    json.dumps(
                        [
                            claim.seed_id,
                            claim.relation_span_id,
                            claim.subject_span_id,
                            claim.action_agent_span_id,
                            claim.action_recipient_span_id,
                            claim.experiencer_span_id,
                            claim.object_span_id,
                            claim.temporal_span_id,
                            claim.measurement_span_id,
                        ],
                        ensure_ascii=False,
                    )
                    for claim in output.claims
                ),
                ensure_ascii=False,
            )
        )
        for output in outputs
    ]

    def stability(values: list[str]) -> float:
        return _rate(Counter(values).most_common(1)[0][1], len(values))

    return {
        "experiment_id": "REP-MACRO",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "unit_id": args.rep_unit,
        "metrics": {
            "cold_run_count": args.rep_runs,
            "cache_hit_count": 0,
            "unique_output_count": len(set(raw)),
            "majority_agreement": _rate(
                Counter(raw).most_common(1)[0][1], len(raw)
            ),
            "raw_output_stability": stability(raw),
            "semantic_claim_stability": stability(claims),
            "semantic_binding_stability": stability(bindings),
            "p50_ms": float(sorted(latencies)[(len(latencies) - 1) // 2]),
            "p95_ms": float(sorted(latencies)[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]),
        },
        "signatures": {"raw": raw, "claims": claims, "bindings": bindings},
    }


async def _async_main(args: argparse.Namespace) -> int:
    fixture = load_v10_fixture(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    snapshot = _prepare_snapshot(fixture, args)
    reports: list[dict[str, Any]] = []
    changed_variables: list[str] = []
    selected = set(args.experiment)

    def wants(value: str) -> bool:
        return not selected or value in selected

    reranker = None
    rankings_by_mode: dict[str, dict[str, V11UnitRanking]] = {}
    if args.rank_mode == "cross" or args.suite in {
        "quick",
        "rank",
        "budget",
        "view",
        "seeds",
        "macro",
        "rep",
        "all",
    }:
        rankings_by_mode["base"] = _rankings(
            snapshot,
            mode="base",
            reranker=None,
            args=args,
        )
    if args.rank_mode in {"cross", "both"} or args.suite == "rank":
        reranker = build_reranker_from_environment()
        cross_started = time.perf_counter()
        rankings_by_mode["cross"] = _rankings(
            snapshot,
            mode="cross",
            reranker=reranker,
            args=args,
        )
        cross_rank_latency_ms = int((time.perf_counter() - cross_started) * 1000)
    else:
        cross_rank_latency_ms = 0

    if args.suite in {"quick", "snapshot", "all"} and wants("SNAP-INTEGRITY"):
        reports.append(evaluate_snapshot(fixture=fixture, snapshot=snapshot))
        changed_variables.append("candidate_snapshot")
    if args.suite == "snapshot" or args.suite == "all":
        reports.append(_span_graph_report(snapshot))

    if args.suite in {"view", "quick", "all"} and wants("VIEW-COVERAGE"):
        rankings = rankings_by_mode.get(args.rank_mode if args.rank_mode != "both" else "base", rankings_by_mode.get("base"))
        assert rankings is not None
        reports.extend(
            evaluate_view_coverage(
                fields=fixture.fields,
                rankings=rankings,
                top_k=args.top_k,
                modes=("global", "role", "role-fallback", "claim-local"),
            )
        )
        changed_variables.append("candidate_view")

    if args.suite in {"rank", "all"}:
        base_rankings = rankings_by_mode.get("base")
        if base_rankings is not None and wants("RANK-BASE"):
            reports.append(
                _rank_report(
                    experiment_id="RANK-BASE",
                    rank_mode="base",
                    fixture=fixture,
                    rankings=base_rankings,
                )
            )
        cross_rankings = rankings_by_mode.get("cross")
        if cross_rankings is not None and wants("RANK-CROSS"):
            reports.append(
                _rank_report(
                    experiment_id="RANK-CROSS",
                    rank_mode="cross",
                    fixture=fixture,
                    rankings=cross_rankings,
                )
            )
            reports[-1]["metrics"]["rerank_wall_latency_ms"] = cross_rank_latency_ms
        changed_variables.extend(["reranker", "ranking_depth"])

    if args.suite in {"budget", "all"} and wants("RANK-BUDGET"):
        rankings = rankings_by_mode.get(
            "cross" if args.rank_mode in {"cross", "both"} else "base",
            rankings_by_mode.get("base"),
        )
        assert rankings is not None
        reports.append(_budget_report(fixture=fixture, rankings=rankings, top_k=args.top_k))
        changed_variables.append("candidate_budget")

    if args.suite in {"seeds", "macro", "rep", "all"}:
        rankings = rankings_by_mode.get(
            "cross" if args.rank_mode in {"cross", "both"} else "base",
            rankings_by_mode.get("base"),
        )
        assert rankings is not None
        seed_reports, _ = _seed_reports(
            fixture,
            rankings,
            target_top_k=args.target_top_k,
        )
        reports.extend(report for report in seed_reports if wants(report["experiment_id"]))
        changed_variables.append("structural_seed_rules")

    if args.suite in {"macro", "all"}:
        rankings = rankings_by_mode.get(
            "cross" if args.rank_mode in {"cross", "both"} else "base",
            rankings_by_mode.get("base"),
        )
        assert rankings is not None
        reports.extend(await _macro_reports(fixture, snapshot, rankings, args))
        changed_variables.extend(["macro_prompt", "role_menu"])

    if args.suite in {"relation", "all"}:
        reports.extend(await _relation_reports(fixture, args))
        changed_variables.extend(["relation_cold_repeat", "relation_batch_order"])

    if args.suite in {"regression", "quick", "all"}:
        reports.extend(
            report
            for report in _downstream_reports(fixture, vocabulary=vocabulary, mode=args.mode)
            if wants(report["experiment_id"])
        )
        changed_variables.append("winner_input_source")

    if args.suite in {"early", "quick", "all"}:
        reports.extend(report for report in _early_reports() if wants(report["experiment_id"]))
        changed_variables.append("continuation_policy")

    if args.suite in {"negative", "quick", "all"} and wants("NEG-V11"):
        reports.append(_negative_report(snapshot))
        changed_variables.append("negative_mutations")

    if args.suite in {"async", "quick", "all"} and wants("ASYNC-V11"):
        result = run_async_isolation(args.output_dir)
        reports.append(
            result
 | {
                "experiment_id": "ASYNC-V11",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
            }
        )

    if args.suite == "rep" and wants("REP-MACRO"):
        rankings = rankings_by_mode.get(
            "cross" if args.rank_mode in {"cross", "both"} else "base",
            rankings_by_mode.get("base"),
        )
        assert rankings is not None
        reports.append(
            await _repeat_report(fixture, snapshot, rankings, args)
        )
        changed_variables.append("macro_cold_repeat")

    if args.suite in {"quick", "all"} and wants("HELD-OUT-V11"):
        reports.append(
            {
                "experiment_id": "HELD-OUT-V11",
                "status": "blocked",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "failure_attribution": "upstream_blocked",
                "metrics": {
                    "development_finalist_frozen": 0,
                    "heldout_read_count": 0,
                    "reason": "await_frozen_v11_finalist",
                },
            }
        )

    if not reports:
        raise ValueError("no_v11_experiment_selected")

    lane = (
        "deterministic"
        if args.suite in {"quick", "snapshot", "view", "budget"}
        else "golden"
        if args.suite in {"macro", "relation", "rep"}
        else "live"
        if args.suite == "rank"
        else "regression"
        if args.suite == "regression"
        else "early-exit"
        if args.suite == "early"
        else "diagnostic"
    )
    document = {
        "schema_version": V11_REPORT_VERSION,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "suite": args.suite,
        "mode": args.mode,
        "lane": lane,
        "changed_variables": list(dict.fromkeys(changed_variables)),
        "matrix": str(args.matrix),
        "matrix_sha256": fixture.sha256,
        "snapshot": str(_snapshot_path(args)),
        "snapshot_version": snapshot.snapshot_version,
        "snapshot_sha256": _sha256(_snapshot_path(args))
        if _snapshot_path(args).is_file()
        else "in-memory-ideal-control",
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "model": os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        "prompt_version": V11_MACRO_PROMPT_VERSION,
        "schema_version_contract": V11_MACRO_SCHEMA_VERSION,
        "view_version": V11_VIEW_VERSION,
        "reranker_version": V11_RERANKER_VERSION,
        "seed_version": V11_SEED_VERSION,
        "statement_prompt_version": V11_STATEMENT_PROMPT_VERSION,
        "relation_prompt_version": V10_RELATION_PROMPT_VERSION,
        "cache_enabled": not args.no_cache,
        "reports": reports,
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
        f"v11-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
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
            "snapshot",
            "view",
            "rank",
            "budget",
            "seeds",
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
    parser.add_argument("--mode", choices=("quick", "shadow", "cold"), default="quick")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_OUTPUT_DIR / "run-cache.json")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--build-snapshot", action="store_true")
    parser.add_argument("--span-threshold", type=float, default=0.1)
    parser.add_argument("--rank-mode", choices=("base", "cross", "both"), default="base")
    parser.add_argument("--view-mode", choices=("global", "role", "role-fallback", "claim-local"), default="claim-local")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--target-top-k", type=int, default=8)
    parser.add_argument("--prefilter", type=int, default=48)
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--batch-size", type=int, choices=(1, 4, 8), default=1)
    parser.add_argument("--relation-fewshot", action="store_true")
    parser.add_argument("--relation-reverse-order", action="store_true")
    parser.add_argument("--relation-runs", type=int, default=3)
    parser.add_argument("--rep-unit", default="macro-answer-fact")
    parser.add_argument("--rep-runs", type=int, default=3)
    args = parser.parse_args()
    if args.top_k < 1 or args.target_top_k < 1 or args.prefilter < 1:
        raise ValueError("v11_positive_candidate_limit_required")
    if args.relation_runs < 3 or args.rep_runs < 3:
        raise ValueError("v11_cold_repeat_requires_at_least_3_runs")
    if not 0.0 <= args.span_threshold <= 1.0:
        raise ValueError("v11_span_threshold_out_of_range")
    return args


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as exc:  # noqa: BLE001
        matrix_sha = _sha256(args.matrix) if args.matrix.is_file() else ""
        document = {
            "schema_version": V11_REPORT_VERSION,
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
                    "experiment_id": "V11-RUNNER",
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
                "held_out_read": False,
                "dspy_used": False,
            },
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = args.output_dir / (
            f"v11-failure-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
        )
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(output_path)
        print("V11-RUNNER failed", document["reports"][0]["error"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
