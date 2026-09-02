from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from vet_agent.input_preprocessing.v8_contracts import (
    V8CoarseType,
    V8SpanCandidate,
    V8SpanLabel,
    V8UserStatementType,
)
from vet_agent.input_preprocessing.v10_contracts import V10CalibratedSpan, V10FieldRole
from vet_agent.input_preprocessing.v10_fixture import load_v10_fixture
from vet_agent.input_preprocessing.v11_contracts import (
    V11MacroClaimRaw,
    V11MacroSemanticRawOutput,
)
from vet_agent.input_preprocessing.v11_macro import menu_violations
from vet_agent.input_preprocessing.v11_snapshot import (
    build_ideal_snapshot,
    contains_forbidden_runtime_keys,
)
from vet_agent.input_preprocessing.v12_anchor import (
    evaluate_anchor_coverage,
    select_support_anchors,
)
from vet_agent.input_preprocessing.v12_conflict import evaluate_conflict_variants
from vet_agent.input_preprocessing.v12_graph import (
    build_v12_span_graph,
    graph_gold_path_retention,
)
from vet_agent.input_preprocessing.v12_seeds import build_v12_seeds, evaluate_v12_seeds
from vet_agent.input_preprocessing.v12_views import build_role_views, view_metrics

MATRIX = Path("tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json")


def _span(
    *,
    start: int,
    end: int,
    label: V8SpanLabel,
    roles: set[V10FieldRole],
    offset: int = 0,
) -> V10CalibratedSpan:
    return V10CalibratedSpan(
        span=V8SpanCandidate(
            span_id=f"span-{offset}-{start}-{end}-{label.value}",
            source_id="unit",
            source_block_id="block-001",
            start=start,
            end=end,
            text="abcd"[start:end],
            label=label,
            score=0.5,
            extractor_version="test-extractor",
        ),
        eligible_roles=frozenset(roles),
    )


def test_graph_deduplicates_boundaries_and_reduces_transitive_containment() -> None:
    unit = _make_snapshot_unit()
    graph = build_v12_span_graph(unit)
    assert graph.metrics["node_count"] == 3
    assert graph.containment.has_edge(
        "unit:block-001:0:4",
        "unit:block-001:0:3",
    )
    assert graph.containment.has_edge(
        "unit:block-001:0:3",
        "unit:block-001:0:2",
    )
    assert not graph.containment.has_edge(
        "unit:block-001:0:4",
        "unit:block-001:0:2",
    )


def _make_snapshot_unit():
    from vet_agent.input_preprocessing.v11_contracts import V11SnapshotUnit

    candidates = [
        _span(
            start=0,
            end=4,
            label=V8SpanLabel.STATE_MENTION,
            roles={V10FieldRole.SUPPORT},
        ),
        _span(
            start=0,
            end=3,
            label=V8SpanLabel.TARGET_MENTION,
            roles={V10FieldRole.TARGET},
            offset=1,
        ),
        _span(
            start=0,
            end=2,
            label=V8SpanLabel.OBJECT_MENTION,
            roles={V10FieldRole.OBJECT},
            offset=2,
        ),
    ]
    return V11SnapshotUnit(unit_id="unit", source_text="abcd", candidates=candidates)


def test_ideal_support_first_view_and_seed_recover_gold_without_runtime_leak() -> None:
    fixture = load_v10_fixture(MATRIX)
    snapshot = build_ideal_snapshot(fixture)
    graphs = {unit.unit_id: build_v12_span_graph(unit) for unit in snapshot.units}
    anchors = {
        unit_id: select_support_anchors(graph, alternatives=4)
        for unit_id, graph in graphs.items()
    }
    views = {
        unit_id: build_role_views(
            graphs[unit_id],
            anchors[unit_id],
            role_top_k=24,
            target_top_k=24,
        )
        for unit_id in graphs
    }
    anchor_report = evaluate_anchor_coverage(fixture=fixture, anchors_by_unit=anchors)
    view_report = view_metrics(
        fixture=fixture,
        views=views,
        snapshot_unique_candidates=sum(
            graph.metrics["node_count"] for graph in graphs.values()
        ),
    )
    assert anchor_report["metrics"]["gold_support_anchor_recall@3"] >= 0.95
    assert view_report["gold_in_view"] >= 0.95
    assert view_report["unique_candidates_sent_to_macro"] < 100
    assert contains_forbidden_runtime_keys(snapshot.model_dump(mode="json")) is False
    assert all(
        graph_gold_path_retention(graph, fixture.fields) == 1.0
        for graph in graphs.values()
    )

    unit = next(item for item in fixture.units if item["unit_id"] == "macro-action-roles")
    seeds, gaps = build_v12_seeds(views["macro-action-roles"])
    evaluation = evaluate_v12_seeds(unit=unit, seeds=seeds, gaps=gaps)
    assert evaluation["metrics"]["seed_recall"] == 1.0
    assert evaluation["metrics"]["action_seed_recall"] == 1.0
    assert evaluation["metrics"]["claim_id_stability"] == 1.0
    first_ids = [seed.seed_id for seed in seeds]
    second_seeds, _second_gaps = build_v12_seeds(views["macro-action-roles"])
    assert first_ids == [seed.seed_id for seed in second_seeds]


def test_candidate_menu_blocks_cross_role_and_empty_claims_require_gap() -> None:
    fixture = load_v10_fixture(MATRIX)
    snapshot = build_ideal_snapshot(fixture)
    unit_id = "macro-action-roles"
    graph = next(build_v12_span_graph(unit) for unit in snapshot.units if unit.unit_id == unit_id)
    anchors = select_support_anchors(graph, alternatives=4)
    view = build_role_views(graph, anchors, role_top_k=24, target_top_k=24)
    seeds, _gaps = build_v12_seeds(view)
    assert seeds
    seed = seeds[0]
    target_ids = {item.span_id for item in seed.menus["target_quote"]}
    outside_target = next(
        item.span.span_id
        for unit in snapshot.units
        if unit.unit_id == unit_id
        for item in unit.candidates
        if item.span.span_id not in target_ids
    )
    claim = V11MacroClaimRaw(
        unit_id=seed.unit_id,
        seed_id=seed.seed_id,
        seed_decision="accepted",
        statement_type=V8UserStatementType.REPORTS,
        coarse_type=V8CoarseType.ACTION,
        support_anchor_span_id=seed.support_span_id,
        target_span_id=outside_target,
    )
    output = V11MacroSemanticRawOutput(
        schema_version="v11-macro-seeded-1",
        no_act_reason="control",
        coverage_gap_suspected=True,
        coverage_gap_reason="menu-control",
        claims=[claim],
    )
    assert menu_violations(output=output, seeds=seeds)
    with pytest.raises(ValidationError):
        V11MacroSemanticRawOutput(
            schema_version="v11-macro-seeded-1",
            no_act_reason="control",
        )


def test_local_conflict_scope_preserves_more_gold_than_global_negative() -> None:
    fixture = load_v10_fixture(MATRIX)
    snapshot = build_ideal_snapshot(fixture)
    unit_id = "macro-action-roles"
    graph = next(build_v12_span_graph(unit) for unit in snapshot.units if unit.unit_id == unit_id)
    anchor = select_support_anchors(graph, alternatives=4)[0]
    report = evaluate_conflict_variants(
        graph=graph,
        anchor=anchor,
        fixture_fields=fixture.fields,
    )
    variants = report["variants"]
    assert variants["no-pruning"]["gold_retention_rate"] == 1.0
    assert variants["global-filter-spans-negative"]["gold_retention_rate"] < 1.0
    assert (
        variants["same-anchor-role"]["gold_retention_rate"]
        >= variants["global-filter-spans-negative"]["gold_retention_rate"]
    )


def test_v12_quick_runner_generates_diagnostic_report(tmp_path: Path) -> None:
    output = tmp_path / "quick"
    code = asyncio.run(
        _run_main(
            [
                "--suite",
                "quick",
                "--mode",
                "quick",
                "--matrix",
                str(MATRIX),
                "--no-cache",
                "--snapshot",
                str(tmp_path / "missing-ideal.json"),
                "--output-dir",
                str(output),
            ]
        )
    )
    assert code == 0
    report_path = next(output.glob("v12-*.json"))
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["diagnostic_only"] is True
    assert document["can_unblock_v8_phase"] is False
    experiment_ids = {item["experiment_id"] for item in document["reports"]}
    assert {
        "METRIC-ALIGN",
        "GRAPH-REDUCE",
        "ANCHOR-TOPO",
        "ROLE-LOCAL-VIEW",
        "SEED-RECOVERY",
        "ANCHOR-NMS",
        "NEG-V12",
        "ASYNC-V12",
        "HELD-OUT-V12",
    } <= experiment_ids
    assert document["safety_boundary"] == {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
        "held_out_read": False,
        "dspy_used": False,
    }


async def _run_main(args: list[str]) -> int:
    from vet_agent.input_preprocessing.v12_experiments import _async_main

    return await _async_main(_parse_args_with_args(args))


def _parse_args_with_args(args: list[str]):
    from vet_agent.input_preprocessing.v12_experiments import _parse_args

    old = sys.argv
    sys.argv = ["v12-experiments", *args]
    try:
        return _parse_args()
    finally:
        sys.argv = old
