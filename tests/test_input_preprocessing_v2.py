"""Tests for the second-round V2 architecture validation matrix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vet_agent.input_preprocessing.v2_analyzer import InputPreprocessingV2Analyzer
from vet_agent.input_preprocessing.v2_contracts import (
    V2AtomicClaimSegment,
    V2QualityGateStatus,
    V2Stage1Output,
    V2Stage2Output,
    V2TurnContext,
)
from vet_agent.input_preprocessing.v2_experiments import (
    AsyncShadowQueueV2,
    AsyncShadowSnapshotV2,
    V2ArchitectureValidationRunner,
    load_v2_experiment_document,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

ROOT = Path(__file__).parent.parent
MATRIX_PATH = (
    ROOT / "tests/fixtures/input_preprocessing/second_round_shadow_matrix.json"
)
VOCABULARY_PATH = (
    ROOT / "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
)


def test_second_round_ideal_control_matrix_passes() -> None:
    """Validate every V2 fixture and all deterministic control paths."""

    runner = V2ArchitectureValidationRunner(
        document=load_v2_experiment_document(MATRIX_PATH),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
    )
    reports = asyncio.run(runner.run(mode="ideal"))

    assert len(reports) == 10
    assert all(report["status"] == "passed" for report in reports)


def test_negative_v2_contracts_are_all_blocked() -> None:
    """Verify invented IDs, bad roles, missing candidates, and missing items fail."""

    runner = V2ArchitectureValidationRunner(
        document=load_v2_experiment_document(MATRIX_PATH),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
    )
    reports = asyncio.run(
        runner.run(
            mode="ideal",
            only_experiment_ids={"N2_invalid_contract_fail_fast"},
        )
    )
    turns = reports[0]["turns"]

    assert reports[0]["status"] == "passed"
    assert len(turns) == 6
    assert all(turn["status"] == "gate_blocked_as_expected" for turn in turns)
    assert all(turn["blocking_gates"] for turn in turns)


def test_shared_scope_contract_is_discriminated_and_counted() -> None:
    """Verify a shared scope remains distinct from an atomic claim."""

    document = load_v2_experiment_document(MATRIX_PATH)
    case = document.cases[0]
    stage1 = V2Stage1Output.model_validate(case.golden_stage1)
    shared = stage1.segments[0]

    assert shared.kind == "shared_assertion_scope"
    assert not isinstance(shared, V2AtomicClaimSegment)
    assert shared.expected_evidence_count == len(shared.items) == 5


def test_stage1_override_does_not_invoke_model_segmentation() -> None:
    """Verify golden Stage 1 remains a control input, not a hidden fallback."""

    document = load_v2_experiment_document(MATRIX_PATH)
    case = document.cases[0]
    stage1 = V2Stage1Output.model_validate(case.golden_stage1)
    qwen = CountingQwenClient(stage1=stage1)
    analyzer = InputPreprocessingV2Analyzer(
        qwen=qwen,
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        model="fake-v2",
    )

    result = asyncio.run(
        analyzer.analyze(
            user_text=case.user_text,
            turn_context=_turn_context(),
            stage1_override=stage1,
        )
    )
    suspicious = next(
        gate for gate in result.gates if gate.gate_id == "v2_suspicious_empty"
    )

    assert qwen.stage1_calls == 0
    assert qwen.stage2_calls == 4
    assert suspicious.status == V2QualityGateStatus.FAILED


def test_async_shadow_queue_reports_explicit_overflow() -> None:
    """Verify enqueue is bounded and nonblocking without silently dropping data."""

    queue = AsyncShadowQueueV2(max_size=1)
    first = _snapshot("snapshot-1")
    second = _snapshot("snapshot-2")

    first_result = queue.submit(first)
    second_result = queue.submit(second)

    assert first_result.accepted is True
    assert first_result.reason == "accepted"
    assert second_result.accepted is False
    assert second_result.reason == "queue_full"
    assert queue.pop() is first
    assert queue.pop() is None


def _turn_context() -> V2TurnContext:
    return V2TurnContext(
        request_id="request-v2",
        trace_id="trace-v2",
        user_id="user-v2",
        pet_id="pet-v2",
        session_id="session-v2",
        reference_time="2026-08-24T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject={  # type: ignore[arg-type]
            "reference_id": "current_pet",
            "entity_type": "current_pet",
        },
    )


def _snapshot(snapshot_id: str) -> AsyncShadowSnapshotV2:
    return AsyncShadowSnapshotV2(
        snapshot_id=snapshot_id,
        sample_id="sample",
        user_text="文本",
        turn_context=_turn_context(),
    )


class CountingQwenClient:
    """Structured client used to assert Stage 1 / Stage 2 boundaries."""

    def __init__(self, stage1: V2Stage1Output) -> None:
        self.stage1 = stage1
        self.stage1_calls = 0
        self.stage2_calls = 0

    @property
    def available(self) -> bool:
        return True

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any:
        if response_model is V2Stage1Output:
            self.stage1_calls += 1
            return self.stage1
        if response_model is V2Stage2Output:
            self.stage2_calls += 1
            return V2Stage2Output()
        raise AssertionError("unexpected response model")
