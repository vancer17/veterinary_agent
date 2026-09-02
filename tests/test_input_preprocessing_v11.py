from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vet_agent.input_preprocessing.v8_contracts import V8CoarseType, V8UserStatementType
from vet_agent.input_preprocessing.v10_fixture import load_v10_fixture
from vet_agent.input_preprocessing.v11_contracts import (
    V11MacroClaimRaw,
    V11MacroSemanticRawOutput,
    V11RoleMenuRecord,
)
from vet_agent.input_preprocessing.v11_macro import menu_violations
from vet_agent.input_preprocessing.v11_seeds import build_structural_seeds
from vet_agent.input_preprocessing.v11_snapshot import (
    build_ideal_snapshot,
    contains_forbidden_runtime_keys,
    evaluate_snapshot,
    load_snapshot,
)
from vet_agent.input_preprocessing.v11_views import evaluate_view_coverage, rank_unit

MATRIX = Path("tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json")


def _ideal_fixture_and_snapshot():
    fixture = load_v10_fixture(MATRIX)
    return fixture, build_ideal_snapshot(fixture)


def test_ideal_snapshot_is_offset_valid_and_does_not_leak_gold_fields() -> None:
    fixture, snapshot = _ideal_fixture_and_snapshot()
    report = evaluate_snapshot(fixture=fixture, snapshot=snapshot)
    metrics = report["metrics"]
    assert metrics["offset_valid_rate"] == 1.0
    assert metrics["text_match_rate"] == 1.0
    assert metrics["exact_field_recall"] == 1.0
    assert report["diagnostic_only"] is True
    assert report["can_unblock_v8_phase"] is False
    assert contains_forbidden_runtime_keys(snapshot.model_dump(mode="json")) is False


def test_snapshot_rejects_held_out_path() -> None:
    with pytest.raises(ValueError, match="held_out"):
        load_snapshot(Path("v11-held-out-candidates.json"))


def test_claim_local_role_view_preserves_gold_and_reports_empty_menus() -> None:
    fixture, snapshot = _ideal_fixture_and_snapshot()
    rankings = {
        unit.unit_id: rank_unit(
            unit_id=unit.unit_id,
            text=unit.source_text,
            candidates=unit.candidates,
            mode="base",
        )
        for unit in snapshot.units
    }
    reports = evaluate_view_coverage(
        fields=fixture.fields,
        rankings=rankings,
        top_k=5,
        modes=("global", "role", "role-fallback", "claim-local"),
    )
    by_mode = {item["view_mode"]: item for item in reports}
    assert by_mode["global"]["metrics"]["gold_in_view_rate"] == 0.9390243902439024
    assert by_mode["role-fallback"]["metrics"]["gold_in_view_rate"] == 1.0
    assert by_mode["claim-local"]["metrics"]["gold_in_view_rate"] == 1.0
    assert by_mode["role"]["metrics"]["empty_role_menu_count"] > 0
    assert by_mode["role"]["metrics"]["gold_in_view_rate"] == 1.0


def test_structural_seeds_are_deterministic_and_recover_gold_skeleton() -> None:
    fixture, snapshot = _ideal_fixture_and_snapshot()
    unit = next(item for item in fixture.units if item["unit_id"] == "macro-shared-scope")
    snapshot_unit = next(item for item in snapshot.units if item.unit_id == "macro-shared-scope")
    ranking = rank_unit(
        unit_id=snapshot_unit.unit_id,
        text=snapshot_unit.source_text,
        candidates=snapshot_unit.candidates,
        mode="base",
    )
    first = build_structural_seeds(ranking)
    second = build_structural_seeds(ranking)
    assert [item.seed_id for item in first] == [item.seed_id for item in second]
    from vet_agent.input_preprocessing.v11_seeds import evaluate_structural_seeds

    report = evaluate_structural_seeds(unit=unit, seeds=first)
    assert report["metrics"]["seed_recall"] >= 0.7
    assert report["metrics"]["claim_id_stability"] == 1.0


def test_candidate_menu_gate_blocks_cross_role_binding() -> None:
    _fixture, snapshot = _ideal_fixture_and_snapshot()
    snapshot_unit = next(item for item in snapshot.units if item.unit_id == "macro-action-roles")
    ranking = rank_unit(
        unit_id=snapshot_unit.unit_id,
        text=snapshot_unit.source_text,
        candidates=snapshot_unit.candidates,
        mode="base",
    )
    seeds = build_structural_seeds(ranking)
    assert seeds
    seed = seeds[0]
    target_ids = {item.span_id for item in seed.menus["target_quote"]}
    outside_target = next(
        item.span.span_id
        for item in snapshot_unit.candidates
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
        coverage_gap_reason="cross-role-control",
        claims=[claim],
    )
    violations = menu_violations(output=output, seeds=seeds)
    assert violations
    assert violations[0]["reason_code"] == "candidate_menu_violation"

    payload = seed.menus["target_quote"][0].model_dump(mode="json")
    payload["source"] = "fallback"
    payload["reason"] = ""
    with pytest.raises(ValidationError):
        V11RoleMenuRecord.model_validate(payload)


def test_v11_quick_runner_generates_diagnostic_report(tmp_path: Path) -> None:
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
                "--output-dir",
                str(output),
            ]
        )
    )
    assert code == 0
    report_path = next(output.glob("v11-*.json"))
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["diagnostic_only"] is True
    assert document["can_unblock_v8_phase"] is False
    experiment_ids = {item["experiment_id"] for item in document["reports"]}
    assert {
        "SNAP-INTEGRITY",
        "VIEW-COVERAGE",
        "DOWNSTREAM-GOLD",
        "EARLY-FAILURE",
        "NEG-V11",
        "ASYNC-V11",
        "HELD-OUT-V11",
    } <= experiment_ids
    assert document["safety_boundary"]["held_out_read"] is False
    assert document["safety_boundary"]["dspy_used"] is False


async def _run_main(args: list[str]) -> int:
    from vet_agent.input_preprocessing.v11_experiments import _async_main

    return await _async_main(_parse_args_with_args(args))


def _parse_args_with_args(args: list[str]):
    import sys

    old = sys.argv
    sys.argv = ["v11-experiments", *args]
    try:
        from vet_agent.input_preprocessing.v11_experiments import _parse_args

        return _parse_args()
    finally:
        sys.argv = old
