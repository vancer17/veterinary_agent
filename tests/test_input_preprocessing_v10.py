from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vet_agent.input_preprocessing.v6_deterministic_parsers import parse_temporal
from vet_agent.input_preprocessing.v8_contracts import V8SpanCandidate, V8SpanLabel
from vet_agent.input_preprocessing.v10_boundary_calibration import (
    V10BoundaryCalibrator,
    evaluate_v10_span_pool,
)
from vet_agent.input_preprocessing.v10_contracts import (
    V10MacroSemanticRawOutput,
)
from vet_agent.input_preprocessing.v10_fixture import (
    audit_v10_fixture,
    build_v10_golden_pool,
    field_role_split,
    load_v10_fixture,
    relation_span_completeness,
)
from vet_agent.input_preprocessing.v10_macro import (
    V10MacroExecution,
    evaluate_v10_macro,
    golden_candidates,
    ideal_macro_output,
)
from vet_agent.input_preprocessing.v10_relation import (
    evaluate_relation_executions,
    ideal_relation_output,
    missing_relation_report,
    relation_records,
)

MATRIX = Path("tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json")


def test_explicit_offset_fixture_passes_phase_zero_audit() -> None:
    fixture = load_v10_fixture(MATRIX)
    audit = audit_v10_fixture(fixture)
    roles = field_role_split(fixture)
    relation = relation_span_completeness(fixture)

    assert audit["metrics"]["offset_valid_rate"] == 1.0
    assert audit["metrics"]["text_match_rate"] == 1.0
    assert audit["metrics"]["owner_occurrence_valid_rate"] == 1.0
    assert roles["metrics"]["field_role_count"] == 82
    assert roles["metrics"]["multi_role_boundary_count"] > 0
    assert relation["metrics"]["relation_span_available_rate"] == 1.0


def test_v10_fixture_rejects_held_out_path() -> None:
    with pytest.raises(ValueError, match="held_out"):
        load_v10_fixture(Path("tenth_round_held_out.json"))


def test_golden_pool_uses_opaque_ids_and_owner_scoped_offsets() -> None:
    fixture = load_v10_fixture(MATRIX)
    unit = next(item for item in fixture.units if item["unit_id"] == "macro-action-roles")
    pool = build_v10_golden_pool(unit)
    assert len(pool.spans) > 0
    assert all(item.span.span_id.startswith("macro-action-roles:v10-gold-") for item in pool.spans)
    assert all(unit["user_text"][item.span.start : item.span.end] == item.span.text for item in pool.spans)
    recipient = pool.field_to_span[("claim-feeding", "action_recipient_quote")]
    assert recipient.span.text == "它"
    assert recipient.span.start == 27


class _RawCoarseExtractor:
    extractor_version = "test-coarse-locator"

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]:
        return [
            V8SpanCandidate(
                span_id=f"{source_id}:raw-0",
                source_id=source_id,
                source_block_id=source_block_id,
                start=10,
                end=21,
                text=text[10:21],
                label=V8SpanLabel.ACTION_EVENT,
                score=0.8,
                extractor_version=self.extractor_version,
            )
        ]


def test_boundary_calibration_generates_nested_exact_candidates_without_gold_input() -> None:
    fixture = load_v10_fixture(MATRIX)
    fields = [field for field in fixture.fields if field.unit_id == "macro-action-roles"]
    calibrator = V10BoundaryCalibrator(variant="G", tokenizer_path="")
    report = evaluate_v10_span_pool(
        fields=fields,
        texts_by_unit={"macro-action-roles": fixture.texts_by_unit["macro-action-roles"]},
        extractor=_RawCoarseExtractor(),
        calibrator=calibrator,
    )
    candidates = report["metrics"]["candidate_count"]
    assert candidates > 1
    assert report["metrics"]["field_coverage"] > 0
    assert report["candidate_policy"]["variant"] == "G"
    assert report["diagnostic_only"] is True
    assert report["can_unblock_v8_phase"] is False


def test_ideal_macro_output_repairs_act_skeleton_and_binding_control() -> None:
    fixture = load_v10_fixture(MATRIX)
    unit = next(item for item in fixture.units if item["unit_id"] == "macro-action-roles")
    pool = build_v10_golden_pool(unit)
    output = ideal_macro_output(pool, unit)
    execution = V10MacroExecution(
        output=output,
        adapter="ideal-control",
        attempt_count=0,
        first_attempt_status="ideal_control",
        model_call_count=0,
    )
    report = evaluate_v10_macro(
        unit=unit,
        output=output,
        spans=golden_candidates(pool),
        entity_candidates=pool.entity_candidates,
        execution=execution,
    )
    metrics = report["metrics"]
    assert metrics["act_precision"] == 1.0
    assert metrics["act_recall"] == 1.0
    assert metrics["claim_precision"] == 1.0
    assert metrics["claim_recall"] == 1.0
    assert metrics["binding_accuracy"] == 1.0
    assert metrics["invalid_span_reference_count"] == 0
    assert metrics["invalid_span_binding_count"] == 0
    assert metrics["role_ineligible_binding_count"] == 0
    assert metrics["model_free_quote_output"] == 0

    ineligible = output.model_copy(deep=True)
    ineligible.claims[0].target_span_id = ineligible.claims[0].temporal_span_id or ""
    ineligible_report = evaluate_v10_macro(
        unit=unit,
        output=ineligible,
        spans=golden_candidates(pool),
        entity_candidates=pool.entity_candidates,
        execution=execution,
    )
    assert ineligible_report["metrics"]["role_ineligible_binding_count"] >= 1

    payload = output.model_dump(mode="json")
    payload["claims"][0]["support_quote"] = "free quote"
    with pytest.raises(ValidationError):
        V10MacroSemanticRawOutput.model_validate(payload)


def test_relation_contract_is_complete_and_missing_spans_do_not_call_classifier() -> None:
    fixture = load_v10_fixture(MATRIX)
    records = relation_records(fixture)
    executions = ideal_relation_output(records)
    report = evaluate_relation_executions(records, executions)
    assert report["metrics"]["relation_accuracy"] == 1.0
    assert report["metrics"]["format_error_count"] == 0

    missing = missing_relation_report([record for record in records[:1]] + [])
    # The development fixture is now complete; force one unavailable record to
    # verify explicit not-evaluable semantics.
    forced = [records[0].__class__(**{**records[0].__dict__, "input_available": False})]
    missing = missing_relation_report(forced)
    assert missing["metrics"]["relation_classifier_call_count"] == 0
    assert missing["metrics"]["relation_input_not_evaluable_count"] == 1
    assert missing["metrics"]["misclassified_as_unclear_count"] == 0
    assert missing["records"][0]["review_required"] is True


def test_v10_runner_quick_control_generates_diagnostic_report(tmp_path: Path) -> None:

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
    report_path = next(output.glob("v10-*.json"))
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["diagnostic_only"] is True
    assert document["can_unblock_v8_phase"] is False
    experiment_ids = {item["experiment_id"] for item in document["reports"]}
    assert {
        "FIXTURE-OFFSET",
        "FIELD-ROLE-SPLIT",
        "RELATION-SPAN-COMPLETE",
        "INTERFACE-AUDIT",
        "EARLY-FAILURE",
        "NEG-V10",
        "ASYNC-V10",
    } <= experiment_ids
    assert document["safety_boundary"] == {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
    }


def test_temporal_parser_supports_common_chinese_onset_expression() -> None:
    result = parse_temporal(temporal_quote="前天开始")
    assert result.status.value == "normalized"
    assert result.value == "day-2"
    assert result.relation is not None


async def _run_main(args: list[str]) -> int:
    from vet_agent.input_preprocessing.v10_experiments import _async_main

    return await _async_main(_parse_args_with_args(args))


def _parse_args_with_args(args: list[str]):
    import sys

    old = sys.argv
    sys.argv = ["v10-experiments", *args]
    try:
        from vet_agent.input_preprocessing.v10_experiments import _parse_args

        return _parse_args()
    finally:
        sys.argv = old
