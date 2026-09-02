from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from vet_agent.input_preprocessing.v10_fixture import load_v10_fixture
from vet_agent.input_preprocessing.v13_aligner import V13SourceBlock, align_phrase
from vet_agent.input_preprocessing.v13_contracts import (
    V13ClaimRecordRawOutput,
    V13PhrasePolicy,
    V13TurnIntentRawOutput,
)
from vet_agent.input_preprocessing.v13_generator import (
    V13LLMFirstGenerator,
    ideal_records,
    ideal_units,
)
from vet_agent.input_preprocessing.v13_governance import (
    evaluate_claims,
    evaluate_participants,
    govern_v13_output,
)

MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)


def test_v13_aligner_accepts_exact_normalized_and_unique_fuzzy() -> None:
    blocks = [
        V13SourceBlock(
            "unit",
            "block-001",
            "它前天开始换新猫粮，这两天大便有一点软，没有呕吐。",
        )
    ]
    exact = align_phrase(
        field_name="target",
        phrase="呕吐",
        blocks=blocks,
        scope=(20, 25),
    )
    assert exact.alignment_status.value == "exact"
    assert exact.aligned_quote == "呕吐"

    normalized = align_phrase(
        field_name="target",
        phrase="呕 吐",
        blocks=blocks,
        scope=(20, 25),
    )
    assert normalized.alignment_status.value == "exact_normalized"
    assert normalized.aligned_quote == "呕吐"

    fuzzy = align_phrase(
        field_name="target",
        phrase="大便有点软",
        blocks=blocks,
        scope=(10, 19),
    )
    assert fuzzy.alignment_status.value == "fuzzy_verified"
    assert fuzzy.aligned_quote == "大便有一点软"

    noncontiguous = align_phrase(
        field_name="evidence",
        phrase="精神...正常",
        blocks=[
            V13SourceBlock(
                "unit",
                "block-001",
                "精神、食欲和饮水都正常",
            )
        ],
    )
    assert noncontiguous.alignment_status.value == "fuzzy_verified"
    assert noncontiguous.aligned_quote == "精神、食欲和饮水都正常"


def test_v13_aligner_blocks_semantic_loss_and_cross_source_block() -> None:
    negation = align_phrase(
        field_name="evidence",
        phrase="没有呕吐",
        blocks=[V13SourceBlock("unit", "block-001", "呕吐")],
    )
    assert negation.alignment_status.value == "fuzzy_not_found"
    assert negation.verifier_status.value == "negation_lost"

    cross = align_phrase(
        field_name="evidence",
        phrase="没有呕吐",
        blocks=[V13SourceBlock("unit", "block-001", "没有呕吐")],
        source_block_id="block-002",
    )
    assert cross.alignment_status.value == "cross_source_block"


def test_v13_contracts_reject_empty_acts_without_reason_and_forbidden_ids() -> None:
    with pytest.raises(ValidationError):
        V13TurnIntentRawOutput(schema_version="v13-intent-1")

    valid = ideal_records(load_v10_fixture(MATRIX).units[0])
    raw = valid.claims[0].model_dump(mode="json")
    raw["span_id"] = "forbidden"
    with pytest.raises(ValidationError):
        V13ClaimRecordRawOutput.model_validate(
            {
                "schema_version": "v13-claim-records-1",
                "claims": [raw],
            }
        )


def test_v13_phrase_policy_switches_between_literal_and_approximate() -> None:


    class CaptureClient:
        adapter_name = "capture"

        @property
        def internal_retry_limit(self) -> int:
            return 0

        async def run_structured(
            self,
            *,
            messages: list[dict[str, Any]],
            response_model: type[BaseModel],
            model: str,
        ) -> V13TurnIntentRawOutput:
            self.messages = messages
            return V13TurnIntentRawOutput(
                schema_version="v13-intent-1",
                no_act_reason="capture",
            )

    literal_client = CaptureClient()
    asyncio.run(V13LLMFirstGenerator(
        client=literal_client,
        phrase_policy=V13PhrasePolicy.LITERAL,
    ).intent(unit_id="unit", user_text="它没有呕吐"))
    assert "必须逐字来自原文" in literal_client.messages[0]["content"]
    assert "不要求逐字复制原文" not in literal_client.messages[0]["content"]

    approximate_client = CaptureClient()
    execution = asyncio.run(V13LLMFirstGenerator(
        client=approximate_client,
        phrase_policy=V13PhrasePolicy.APPROXIMATE,
    ).intent(unit_id="unit", user_text="它没有呕吐"))
    prompt = approximate_client.messages[0]["content"]
    assert "不要求逐字复制原文" in prompt
    assert "必须逐字来自原文" not in prompt
    assert "phrase 不是 quote" in prompt
    assert execution.prompt_version.endswith(":approximate")


def test_v13_ideal_claim_alignment_and_participant_resolution() -> None:
    fixture = load_v10_fixture(MATRIX)
    unit = next(
        item for item in fixture.units if item["unit_id"] == "macro-answer-fact"
    )
    output = ideal_records(unit)
    claim_report = evaluate_claims(unit=unit, output=output)
    assert claim_report["metrics"]["claim_precision"] == 1.0
    assert claim_report["metrics"]["claim_recall"] == 1.0
    assert claim_report["metrics"]["statement_type_accuracy"] == 1.0
    assert claim_report["metrics"]["projection_consuming_blocked_count"] == 0

    participant_report = evaluate_participants(unit=unit, output=output)
    assert participant_report["metrics"]["participant_mention_recall"] == 1.0
    assert participant_report["metrics"]["participant_resolution_accuracy"] == 1.0
    assert participant_report["metrics"]["resolved_empty_violation"] == 0

    governed = govern_v13_output(
        output,
        source_id=str(unit["unit_id"]),
        text=str(unit["user_text"]),
    )
    assert [item.deterministic_claim_id for item in governed] == [
        item.deterministic_claim_id
        for item in govern_v13_output(
            output,
            source_id=str(unit["unit_id"]),
            text=str(unit["user_text"]),
        )
    ]


def test_v13_ideal_segmentation_recovers_shared_scope() -> None:
    from vet_agent.input_preprocessing.v13_governance import evaluate_segmentation

    fixture = load_v10_fixture(MATRIX)
    unit = next(
        item for item in fixture.units if item["unit_id"] == "macro-shared-scope"
    )
    report = evaluate_segmentation(unit=unit, output=ideal_units(unit))
    assert report["metrics"]["claim_unit_precision"] == 1.0
    assert report["metrics"]["claim_unit_recall"] == 1.0
    assert report["metrics"]["claim_unit_expected_count"] == 10


def test_v13_quick_runner_generates_diagnostic_report(tmp_path: Path) -> None:
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
    report_path = next(output.glob("v13-*.json"))
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["diagnostic_only"] is True
    assert document["can_unblock_v8_phase"] is False
    assert document["phrase_policy"] == "approximate"
    experiment_ids = {item["experiment_id"] for item in document["reports"]}
    assert {
        "ALIGNER-CONTROL",
        "TURN-INTENT",
        "NEG-V13",
        "LLMF-SEG-ONLY",
        "LLMF-ONEPASS",
        "LLMF-TWOSTAGE",
        "CLAIM-ALIGN",
        "STATEMENT-SEMANTICS",
        "TEMPORAL-PROPOSAL",
        "MEASUREMENT-PROPOSAL",
        "PARTICIPANT-RESOLVE",
        "CAN-DESCRIPTOR",
        "CLAIM-GRAPH",
        "PARADIGM-COMPARE",
        "ASYNC-V13",
        "HELD-OUT-V13",
    } <= experiment_ids
    negative = next(
        item
        for item in document["reports"]
        if item["experiment_id"] == "NEG-V13"
    )
    assert negative["metrics"]["gate_blocked_as_expected_rate"] == 1.0
    assert negative["metrics"]["false_pass"] == 0
    assert document["safety_boundary"] == {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
        "held_out_read": False,
        "dspy_used": False,
        "gliner_called_on_main_path": False,
    }


def test_v13_shadow_report_records_approximate_phrase_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argparse import Namespace

    from vet_agent.input_preprocessing import v13_experiments as experiments
    from vet_agent.input_preprocessing.v13_generator import ideal_intent

    fixture = load_v10_fixture(MATRIX)
    unit = next(
        item for item in fixture.units if item["unit_id"] == "macro-answer-fact"
    )

    class LocalStructuredClient:
        adapter_name = "local-control"

        @property
        def internal_retry_limit(self) -> int:
            return 0

        async def run_structured(
            self,
            *,
            messages: list[dict[str, Any]],
            response_model: type[BaseModel],
            model: str,
        ) -> Any:
            if response_model is experiments.V13TurnIntentRawOutput:
                return ideal_intent(unit)
            if response_model is experiments.V13ClaimUnitRawOutput:
                return ideal_units(unit)
            if response_model is experiments.V13ClaimRecordRawOutput:
                return ideal_records(unit)
            raise AssertionError(f"unexpected response model: {response_model}")

    monkeypatch.setattr(
        experiments,
        "build_v13_client",
        lambda: LocalStructuredClient(),
    )
    args = Namespace(
        unit=["macro-answer-fact"],
        no_cache=True,
        cache_path=Path(".tmp/v13-local-shadow/cache.json"),
    )
    reports = asyncio.run(
        experiments._shadow_reports(
            fixture,
            experiments.CanonicalVocabulary.load(
                Path(
                    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
                )
            ),
            args,
            V13PhrasePolicy.APPROXIMATE,
        )
    )
    assert reports
    assert all(report["phrase_policy"] == "approximate" for report in reports)
    experiment_ids = {report["experiment_id"] for report in reports}
    assert {
        "TURN-INTENT",
        "LLMF-SEG-ONLY",
        "LLMF-ONEPASS",
        "LLMF-TWOSTAGE",
        "CLAIM-ALIGN",
        "FUZZY-POLICY",
        "PARTICIPANT-RESOLVE",
        "PARADIGM-COMPARE",
    } <= experiment_ids


async def _run_main(args: list[str]) -> int:
    from vet_agent.input_preprocessing.v13_experiments import _async_main

    return await _async_main(_parse_args_with_args(args))


def _parse_args_with_args(args: list[str]):
    from vet_agent.input_preprocessing.v13_experiments import _parse_args

    old = sys.argv
    sys.argv = ["v13-experiments", *args]
    try:
        return _parse_args()
    finally:
        sys.argv = old
