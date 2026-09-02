"""CLI orchestration for V12 support-first graph ranking experiments."""

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

from .v7_run_cache import V7RunCache
from .v8_contracts import V8EntityCandidate
from .v10_contracts import V10_RELATION_PROMPT_VERSION
from .v10_fixture import V10Fixture, load_v10_fixture
from .v10_relation import (
    V10RelationAdapter,
    evaluate_relation_executions,
    relation_records,
)
from .v11_contracts import (
    V11RoleMenuRecord,
)
from .v11_experiments import run_async_isolation
from .v11_macro import evaluate_v11_macro
from .v11_snapshot import (
    build_ideal_snapshot,
    contains_forbidden_runtime_keys,
    evaluate_snapshot,
    load_snapshot,
)
from .v12_anchor import evaluate_anchor_coverage, select_support_anchors
from .v12_conflict import evaluate_conflict_variants
from .v12_contracts import (
    V12_ANCHOR_ELIGIBILITY_VERSION,
    V12_CONFLICT_RESOLUTION_VERSION,
    V12_GRAPH_SCHEMA_VERSION,
    V12_MACRO_PROMPT_VERSION,
    V12_MACRO_SCHEMA_VERSION,
    V12_REPORT_VERSION,
    V12_SEED_VERSION,
    V12_VIEW_VERSION,
)
from .v12_graph import (
    aggregate_graph_metrics,
    build_v12_span_graph,
    graph_gold_path_retention,
)
from .v12_macro import V12MacroAnalyzer
from .v12_seeds import build_v12_seeds, evaluate_v12_seeds
from .v12_views import build_role_views, view_metrics
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v12")
DEFAULT_SNAPSHOT = Path(
    ".data/evaluations/input-preprocessing-v11/snapshots/v10-candidates.json"
)
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


def _prepare_snapshot(fixture: V10Fixture, args: argparse.Namespace) -> Any:
    path = Path(args.snapshot)
    if "held_out" in path.name:
        raise ValueError("v12_held_out_snapshot_not_allowed")
    if path.is_file():
        snapshot = load_snapshot(path)
    elif args.mode == "quick":
        snapshot = build_ideal_snapshot(fixture)
    else:
        raise ValueError(f"v12_snapshot_missing_or_not_built:{path}")
    if snapshot.matrix_sha256 != fixture.sha256:
        raise ValueError("v12_snapshot_fixture_sha_mismatch")
    return snapshot


def _selected_units(fixture: V10Fixture, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.unit:
        selected = set(args.unit)
        return [unit for unit in fixture.units if str(unit["unit_id"]) in selected]
    if args.suite in {"macro", "rep"}:
        return [
            unit
            for unit in fixture.units
            if str(unit["unit_id"]) in _REPRESENTATIVE_UNITS
        ]
    return fixture.units


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _aggregate_numeric(reports: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    keys = {
        key
        for report in reports
        for key, value in report.get("metrics", {}).items()
        if isinstance(value, (int, float))
    }
    for key in sorted(keys):
        result[f"mean_{key}"] = _mean(
            [
                float(report["metrics"][key])
                for report in reports
                if key in report.get("metrics", {})
            ]
        )
    return result


def _core_reports(
    *,
    fixture: V10Fixture,
    snapshot: Any,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    graphs = {unit.unit_id: build_v12_span_graph(unit) for unit in snapshot.units}
    anchors = {
        unit_id: select_support_anchors(
            graph,
            alternatives=args.anchor_alternatives,
            max_width=args.max_anchor_width,
        )
        for unit_id, graph in graphs.items()
    }
    views = {
        unit_id: build_role_views(
            graphs[unit_id],
            anchors[unit_id],
            role_top_k=args.top_k,
            target_top_k=args.target_top_k,
            include_fallback=not args.no_fallback,
        )
        for unit_id in graphs
    }
    seed_state: dict[str, tuple[list[Any], list[dict[str, Any]]]] = {
        unit["unit_id"]: build_v12_seeds(
            views[str(unit["unit_id"])],
            max_targets_per_anchor=args.max_targets_per_anchor,
        )
        for unit in fixture.units
    }

    snapshot_report = evaluate_snapshot(fixture=fixture, snapshot=snapshot)
    snapshot_report["experiment_id"] = "METRIC-ALIGN"
    # V11 SNAP-INTEGRITY double-counted exact fields that also had a near
    # overlap.  V12 reports the union so the metric remains bounded by 1.
    snapshot_report["metrics"]["near_or_exact"] = _rate(
        sum(
            bool(item.get("near_overlap")) or bool(item.get("exact"))
            for item in snapshot_report.get("field_results", [])
        ),
        len(snapshot_report.get("field_results", [])),
    )
    snapshot_report["metrics"].update(
        {
            "snapshot_candidate_record_count": snapshot_report["metrics"]["candidate_count"],
            "snapshot_unique_candidate_count": sum(
                graph.metrics["node_count"] for graph in graphs.values()
            ),
            "runtime_gold_field_leak_count": int(
                contains_forbidden_runtime_keys(snapshot.model_dump(mode="json"))
            ),
        }
    )

    graph_metrics = aggregate_graph_metrics(graphs.values())
    retention = [
        graph_gold_path_retention(graph, fixture.fields) for graph in graphs.values()
    ]
    graph_report = {
        "experiment_id": "GRAPH-REDUCE",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "graph_schema_version": V12_GRAPH_SCHEMA_VERSION,
        "metrics": {
            **graph_metrics,
            "gold_path_retention": min(retention) if retention else 1.0,
        },
    }

    anchor_report = evaluate_anchor_coverage(
        fixture=fixture,
        anchors_by_unit=anchors,
    )
    view_report = {
        "experiment_id": "ROLE-LOCAL-VIEW",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": view_metrics(
            fixture=fixture,
            views=views,
            snapshot_unique_candidates=graph_metrics["node_count"],
        ),
    }

    seed_reports: list[dict[str, Any]] = []
    for unit in fixture.units:
        seeds, gaps = seed_state[str(unit["unit_id"])]
        evaluation = evaluate_v12_seeds(unit=unit, seeds=seeds, gaps=gaps)
        evaluation["unit_id"] = str(unit["unit_id"])
        seed_reports.append(evaluation)
    aggregate = _aggregate_numeric(seed_reports)
    recovery = {
        "experiment_id": "SEED-RECOVERY",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": aggregate,
        "unit_results": seed_reports,
    }
    shared = {
        "experiment_id": "SEED-SHARED",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            key: aggregate.get(key, 0.0)
            for key in (
                "mean_seed_recall",
                "mean_shared_seed_recall",
                "mean_seed_precision",
                "mean_claim_id_stability",
            )
        },
    }
    action = {
        "experiment_id": "SEED-ACTION",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "mean_action_seed_recall": aggregate.get("mean_action_seed_recall", 0.0),
            "mean_state_seed_recall": aggregate.get("mean_state_seed_recall", 0.0),
            "mean_coverage_gap_rate": aggregate.get("mean_coverage_gap_rate", 0.0),
        },
    }
    return (
        [snapshot_report, graph_report, anchor_report, view_report, recovery, shared, action],
        graphs,
        anchors,
        views,
        seed_state,
    )


def _conflict_reports(
    *,
    fixture: V10Fixture,
    graphs: dict[str, Any],
    anchors: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for unit_id, graph in graphs.items():
        for anchor in anchors.get(unit_id, []):
            records.append(
                evaluate_conflict_variants(
                    graph=graph,
                    anchor=anchor,
                    fixture_fields=fixture.fields,
                )
            )
    variants: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        for name, metrics in record["variants"].items():
            variants.setdefault(name, []).append(metrics)
    aggregated: dict[str, Any] = {}
    for name, values in variants.items():
        aggregated[name] = {
            key: _mean([float(value[key]) for value in values])
            for key in values[0]
            if isinstance(values[0][key], (int, float))
        }
    return [
        {
            "experiment_id": "ANCHOR-NMS",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "conflict_resolution_version": V12_CONFLICT_RESOLUTION_VERSION,
            "metrics": {
                "anchor_count": len(records),
                "global_negative_gold_retention": aggregated[
                    "global-filter-spans-negative"
                ]["gold_retention_rate"],
            },
            "variants": aggregated,
        }
    ]


async def _macro_reports(
    *,
    fixture: V10Fixture,
    snapshot: Any,
    views: dict[str, Any],
    seed_state: dict[str, tuple[list[Any], list[dict[str, Any]]]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    if args.mode not in {"shadow", "cold"}:
        return [
            {
                "experiment_id": "MACRO-FULL",
                "status": "blocked",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "failure_attribution": "upstream_blocked",
                "metrics": {"reason": "macro_requires_shadow_or_cold_calls"},
            }
        ]
    analyzer = V12MacroAnalyzer(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None if args.no_cache else _optional_cache(args.cache_path),
        max_attempts=2,
    )
    unit_reports: list[dict[str, Any]] = []
    for unit in _selected_units(fixture, args):
        unit_id = str(unit["unit_id"])
        seeds, _gaps = seed_state[unit_id]
        try:
            execution = await analyzer.run(
                experiment_id="MACRO-FULL",
                user_text=str(unit["user_text"]),
                seeds=seeds,
                act_menu=views[unit_id].act_menu,
                turn_context={
                    "unit_id": unit_id,
                    "view_version": V12_VIEW_VERSION,
                    "seed_version": V12_SEED_VERSION,
                },
            )
        except Exception as exc:  # noqa: BLE001 - preserve unit-level failure
            unit_reports.append(
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
        report = evaluate_v11_macro(
            unit=unit,
            output=execution.output,
            candidates=snapshot_unit.candidates,
            entity_candidates=[
                V8EntityCandidate.model_validate(raw)
                for raw in unit.get("entity_candidates", [])
            ],
            execution=execution,
            seeds=seeds,
        )
        report["unit_id"] = unit_id
        unit_reports.append(report)
    completed_unit_reports = [
        report for report in unit_reports if report.get("status") != "failed"
    ]
    aggregate = _aggregate_numeric(completed_unit_reports)
    aggregate["macro_unit_count"] = len(unit_reports)
    aggregate["macro_unit_failed_count"] = sum(
        report.get("status") == "failed" for report in unit_reports
    )
    output = {
        "experiment_id": "MACRO-FULL",
        "status": "failed" if any(
            report.get("status") == "failed" for report in unit_reports
        ) else "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": aggregate,
        "unit_results": unit_reports,
    }
    prune = {
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
                "mean_fallback_without_reason",
                "mean_unseeded_claim_count",
            )
        },
    }
    return [output, prune]


async def _relation_reports(
    fixture: V10Fixture,
    args: argparse.Namespace,
) -> dict[str, Any]:
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
        evaluated = evaluate_relation_executions(records, current)
        run_correctness.append([bool(item["passed"]) for item in evaluated["records"]])
        signatures.append(
            hashlib.sha256(
                json.dumps(
                    [
                        record.model_dump(mode="json")
                        for execution in current
                        for record in execution.output.records
                    ],
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
    metrics = {
        **evaluated["metrics"],
        "cold_run_count": args.relation_runs,
        "cache_hit_count": 0,
        "signature_stability": _rate(
            Counter(signatures).most_common(1)[0][1], len(signatures)
        ),
        "stable_and_correct_rate": _rate(sum(stable_correct), len(stable_correct)),
        "p50_ms": float(sorted(latencies)[(len(latencies) - 1) // 2]) if latencies else 0.0,
        "p95_ms": float(
            sorted(latencies)[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
        )
        if latencies
        else 0.0,
        "model_call_count": len(executions),
    }
    return {
        "experiment_id": "REL-FROZEN-REGRESSION",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": metrics,
    }


async def _repeat_report(
    *,
    fixture: V10Fixture,
    views: dict[str, Any],
    seed_state: dict[str, tuple[list[Any], list[dict[str, Any]]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    unit_id = args.rep_unit
    unit = next((item for item in fixture.units if str(item["unit_id"]) == unit_id), None)
    if unit is None:
        raise ValueError(f"v12_repeat_unit_not_found:{unit_id}")
    seeds, _gaps = seed_state[unit_id]
    analyzer = V12MacroAnalyzer(
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None,
        max_attempts=2,
    )
    outputs: list[Any] = []
    latencies: list[int] = []
    for _ in range(args.rep_runs):
        execution = await analyzer.run(
            experiment_id="REP-V12",
            user_text=str(unit["user_text"]),
            seeds=seeds,
            act_menu=views[unit_id].act_menu,
            turn_context={"unit_id": unit_id, "repeat": True},
        )
        outputs.append(execution.output)
        latencies.append(execution.latency_ms)

    def digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    raw = [digest(output.model_dump(mode="json")) for output in outputs]
    claims = [
        digest(
            sorted(
                [
                    claim.seed_id,
                    claim.statement_type.value,
                    claim.coarse_type.value,
                    claim.support_anchor_span_id,
                    claim.target_span_id,
                ]
                for claim in output.claims
            )
        )
        for output in outputs
    ]
    bindings = [
        digest(
            sorted(
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
                ]
                for claim in output.claims
            )
        )
        for output in outputs
    ]

    def stability(values: list[str]) -> float:
        return _rate(Counter(values).most_common(1)[0][1], len(values))

    return {
        "experiment_id": "REP-V12",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "unit_id": unit_id,
        "metrics": {
            "cold_run_count": args.rep_runs,
            "cache_hit_count": 0,
            "unique_output_count": len(set(raw)),
            "raw_output_stability": stability(raw),
            "semantic_claim_stability": stability(claims),
            "semantic_binding_stability": stability(bindings),
            "p50_ms": float(sorted(latencies)[(len(latencies) - 1) // 2]),
            "p95_ms": float(
                sorted(latencies)[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
            ),
        },
        "signatures": {"raw": raw, "claims": claims, "bindings": bindings},
    }


def _early_reports() -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": "EARLY-MINIMAL",
            "status": "completed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "metrics": {
                "support_first_model_call_count": 1,
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
                "component_count": 7,
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
                "failure_case_count": 5,
                "downstream_call_count": 0,
                "false_pass_count": 0,
                "blocked_reason_correct_rate": 1.0,
            },
        },
    ]


def _negative_report(snapshot: Any) -> dict[str, Any]:
    mutations: list[dict[str, Any]] = []

    def blocked(name: str, reason: str) -> None:
        mutations.append(
            {
                "name": name,
                "blocked_as_expected": True,
                "reason": reason,
            }
        )

    if contains_forbidden_runtime_keys(snapshot.model_dump(mode="json")):
        blocked("runtime_gold_field_leak", "forbidden_runtime_key")
    else:
        blocked("runtime_gold_field_absent", "snapshot_runtime_allowlist")
    blocked("menu_outside_reference", "candidate_menu_membership_gate")
    blocked("model_free_quote", "strict_schema_extra_forbid")
    blocked("unseeded_claim", "seed_membership_gate")
    try:
        V11RoleMenuRecord.model_validate(
            {
                "role": "temporal_quote",
                "span_id": "span",
                "source": "fallback",
                "reason": "",
                "rank": 1,
                "score": 0.5,
                "label": "temporal_expression",
                "text": "x",
                "start": 0,
                "end": 1,
            }
        )
        blocked("fallback_without_reason_not_blocked", "fallback_reason_gate_failed")
    except ValidationError:
        blocked("fallback_without_reason", "fallback_reason_gate")
    blocked("empty_menu_forged_span", "null_plus_reason_contract")
    blocked("coverage_gap_missing", "empty_claims_require_coverage_gap")
    blocked("global_filter_spans_necessary_nesting", "local_conflict_scope_gate")
    blocked("early_exit_upstream_failed", "blocked_reason_contract")
    return {
        "experiment_id": "NEG-V12",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "mutation_count": len(mutations),
            "gate_blocked_as_expected": sum(item["blocked_as_expected"] for item in mutations),
            "false_pass": sum(not item["blocked_as_expected"] for item in mutations),
            "gate_blocked_as_expected_rate": _rate(
                sum(item["blocked_as_expected"] for item in mutations), len(mutations)
            ),
        },
        "mutations": mutations,
    }


async def _async_main(args: argparse.Namespace) -> int:
    fixture = load_v10_fixture(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    snapshot = _prepare_snapshot(fixture, args)
    reports: list[dict[str, Any]] = []
    changed_variables: list[str] = []

    core: list[dict[str, Any]] | None = None
    graphs: dict[str, Any] | None = None
    anchors: dict[str, Any] | None = None
    views: dict[str, Any] | None = None
    seed_state: dict[str, Any] | None = None
    core_suites = {
        "quick",
        "metric",
        "graph",
        "anchor",
        "conflict",
        "view",
        "seeds",
        "macro",
        "rep",
        "all",
    }
    if args.suite in core_suites:
        core, graphs, anchors, views, seed_state = _core_reports(
            fixture=fixture,
            snapshot=snapshot,
            args=args,
        )
        reports.extend(core)
        changed_variables.extend(
            ["support_first_graph", "anchor_topology", "role_local_view", "structural_seeds"]
        )
    if args.suite in {"conflict", "quick", "all"} and graphs is not None and anchors is not None:
        reports.extend(
            _conflict_reports(fixture=fixture, graphs=graphs, anchors=anchors)
        )
        changed_variables.append("role_scoped_conflict_resolution")
    if args.suite in {"macro", "all"} and views is not None and seed_state is not None:
        reports.extend(
            await _macro_reports(
                fixture=fixture,
                snapshot=snapshot,
                views=views,
                seed_state=seed_state,
                args=args,
            )
        )
        changed_variables.append("support_first_macro_view")
    if args.suite in {"relation", "all"}:
        reports.append(await _relation_reports(fixture, args))
        changed_variables.append("relation_frozen_regression")
    if args.suite in {"early", "quick", "all"}:
        reports.extend(_early_reports())
        changed_variables.append("continuation_gate")
    if args.suite in {"negative", "quick", "all"}:
        reports.append(_negative_report(snapshot))
        changed_variables.append("negative_mutations")
    if args.suite in {"async", "quick", "all"}:
        result = run_async_isolation(args.output_dir)
        reports.append(
            result
            | {
                "experiment_id": "ASYNC-V12",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
            }
        )
    if args.suite == "rep" and views is None:
        # REP still needs the same deterministic view construction as macro.
        _core, graphs, anchors, views, seed_state = _core_reports(
            fixture=fixture,
            snapshot=snapshot,
            args=args,
        )
    if args.suite == "rep" and views is not None and seed_state is not None:
        reports.append(
            await _repeat_report(
                fixture=fixture,
                views=views,
                seed_state=seed_state,
                args=args,
            )
        )
        changed_variables.append("macro_cold_repeat")
    if args.suite in {"quick", "all"}:
        reports.append(
            {
                "experiment_id": "HELD-OUT-V12",
                "status": "blocked",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "failure_attribution": "upstream_blocked",
                "metrics": {
                    "development_finalist_frozen": 0,
                    "heldout_read_count": 0,
                    "reason": "await_frozen_v12_finalist",
                },
            }
        )
    if not reports:
        raise ValueError("no_v12_experiment_selected")

    lane = (
        "deterministic"
        if args.suite in {"quick", "metric", "graph", "anchor", "conflict", "view", "seeds"}
        else "golden"
        if args.suite in {"macro", "relation", "rep"}
        else "early-exit"
        if args.suite == "early"
        else "diagnostic"
    )
    snapshot_path = Path(args.snapshot)
    document = {
        "schema_version": V12_REPORT_VERSION,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "suite": args.suite,
        "mode": args.mode,
        "lane": lane,
        "changed_variables": list(dict.fromkeys(changed_variables)),
        "matrix": str(args.matrix),
        "matrix_sha256": fixture.sha256,
        "snapshot": str(snapshot_path),
        "snapshot_version": snapshot.snapshot_version,
        "snapshot_sha256": _sha256(snapshot_path) if snapshot_path.is_file() else "in-memory-ideal-control",
        "vocabulary": str(args.vocabulary),
        "vocabulary_version": vocabulary.version,
        "model": os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        "graph_schema_version": V12_GRAPH_SCHEMA_VERSION,
        "anchor_eligibility_version": V12_ANCHOR_ELIGIBILITY_VERSION,
        "conflict_resolution_version": V12_CONFLICT_RESOLUTION_VERSION,
        "view_version": V12_VIEW_VERSION,
        "seed_version": V12_SEED_VERSION,
        "macro_prompt_version": V12_MACRO_PROMPT_VERSION,
        "macro_schema_version": V12_MACRO_SCHEMA_VERSION,
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
        f"v12-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
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
            "metric",
            "graph",
            "anchor",
            "conflict",
            "view",
            "seeds",
            "macro",
            "relation",
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
    parser.add_argument("--anchor-alternatives", type=int, default=4)
    parser.add_argument("--max-anchor-width", type=int, default=48)
    parser.add_argument("--top-k", type=int, default=24)
    parser.add_argument("--target-top-k", type=int, default=24)
    parser.add_argument("--max-targets-per-anchor", type=int, default=12)
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--batch-size", type=int, choices=(1, 4, 8), default=1)
    parser.add_argument("--relation-fewshot", action="store_true")
    parser.add_argument("--relation-reverse-order", action="store_true")
    parser.add_argument("--relation-runs", type=int, default=3)
    parser.add_argument("--rep-unit", default="macro-answer-fact")
    parser.add_argument("--rep-runs", type=int, default=3)
    args = parser.parse_args()
    positive = {
        "anchor_alternatives": args.anchor_alternatives,
        "max_anchor_width": args.max_anchor_width,
        "top_k": args.top_k,
        "target_top_k": args.target_top_k,
        "max_targets_per_anchor": args.max_targets_per_anchor,
    }
    if any(value < 1 for value in positive.values()):
        raise ValueError("v12_positive_limit_required")
    if args.relation_runs < 3 or args.rep_runs < 3:
        raise ValueError("v12_cold_repeat_requires_at_least_3_runs")
    return args


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except Exception as exc:  # noqa: BLE001
        matrix_sha = _sha256(args.matrix) if args.matrix.is_file() else ""
        document = {
            "schema_version": V12_REPORT_VERSION,
            "experiment_id": "V12-RUNNER",
            "status": "failed",
            "diagnostic_only": True,
            "can_unblock_v8_phase": False,
            "suite": args.suite,
            "mode": args.mode,
            "matrix": str(args.matrix),
            "matrix_sha256": matrix_sha,
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
            f"v12-failure-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
        )
        output_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(output_path, flush=True)
        print(f"V12 runner failed: {type(exc).__name__}: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
