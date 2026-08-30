"""Tests for the third-round V3 architecture validation matrix."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from vet_agent.input_preprocessing.v3_analyzer import InputPreprocessingV3Analyzer
from vet_agent.input_preprocessing.v3_candidate_linker import V3CandidateRetriever
from vet_agent.input_preprocessing.v3_contracts import (
    V3CanonicalMappingStatus,
    V3ItemVerificationRaw,
    V3ScopeSegmentationRawOutput,
    V3Stage1Output,
    V3TurnContext,
)
from vet_agent.input_preprocessing.v3_experiments import (
    AsyncShadowSnapshotV3,
    FileAsyncShadowQueueV3,
    V3ArchitectureValidationRunner,
    load_v3_experiment_document,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

ROOT = Path(__file__).parent.parent
DEVELOPMENT_MATRIX = (
    ROOT / "tests/fixtures/input_preprocessing/third_round_shadow_matrix.json"
)
HELD_OUT_MATRIX = (
    ROOT / "tests/fixtures/input_preprocessing/third_round_held_out_matrix.json"
)
VOCABULARY_PATH = (
    ROOT / "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
)


def test_third_round_development_ideal_control_passes() -> None:
    """Validate the deterministic development control path."""

    runner = V3ArchitectureValidationRunner(
        document=load_v3_experiment_document(DEVELOPMENT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        async_queue_directory=ROOT / ".tmp/input-preprocessing-v3-test-queue",
    )
    reports = asyncio.run(
        runner.run(mode="ideal", repeat_override=1),
    )

    assert len(reports) == 12
    assert all(report["status"] == "passed" for report in reports)


def test_third_round_held_out_confirmatory_control_passes() -> None:
    """Validate held-out samples with the required three-repeat stability rule."""

    if not HELD_OUT_MATRIX.is_file():
        pytest.skip("restricted held-out fixture is not published")
    runner = V3ArchitectureValidationRunner(
        document=load_v3_experiment_document(HELD_OUT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        async_queue_directory=ROOT / ".tmp/input-preprocessing-v3-test-queue-held",
    )
    reports = asyncio.run(
        runner.run(mode="ideal", phase="confirmatory"),
    )

    assert len(reports) == 12
    assert all(report["status"] == "passed" for report in reports)
    assert all(
        report["metrics"].get("unique_output_count", 1) == 1
        for report in reports
    )


def test_v3_negative_mutations_are_all_blocked() -> None:
    """Verify candidate, coverage, participant, assertion, and review mutations."""

    runner = V3ArchitectureValidationRunner(
        document=load_v3_experiment_document(DEVELOPMENT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
    )
    reports = asyncio.run(
        runner.run(
            mode="ideal",
            only_experiment_ids={"NEG_D3"},
            repeat_override=1,
        )
    )
    turns = reports[0]["turns"]

    assert reports[0]["status"] == "passed"
    assert len(turns) == 8
    assert all(turn["status"] == "gate_blocked_as_expected" for turn in turns)
    assert all(turn["blocking_gates"] for turn in turns)


def test_raw_stage1_schema_does_not_ask_model_for_derived_counts() -> None:
    """Derived count fields are absent from the raw segmentation contract."""

    raw_fields = set(V3ScopeSegmentationRawOutput.model_fields)
    segment_type = V3ScopeSegmentationRawOutput.model_fields["segments"].annotation
    assert "expected_evidence_count" not in raw_fields
    assert "profile" not in raw_fields
    assert segment_type is not None


def test_stage1_override_uses_item_keyed_verifier_without_segmentation_calls() -> None:
    """Golden Stage 1 remains a control and Stage 2 runs once per expected item."""

    document = load_v3_experiment_document(DEVELOPMENT_MATRIX)
    case = document.cases[0]
    stage1 = V3Stage1Output.model_validate(case.golden_stage1)
    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    embeddings = AliasEmbeddingClient(vocabulary)
    retriever = V3CandidateRetriever(
        vocabulary=vocabulary,
        embeddings=embeddings,
        minimum_score=0.99,
    )
    qwen = ItemVerifierQwenClient()
    analyzer = InputPreprocessingV3Analyzer(
        qwen=qwen,
        vocabulary=vocabulary,
        candidate_retriever=retriever,
        model="fake-v3",
    )

    result = asyncio.run(
        analyzer.analyze(
            user_text=case.user_text,
            turn_context=_turn_context("stage1-override"),
            stage1_override=stage1,
        )
    )

    assert qwen.stage2_calls == 10
    assert len(result.stage2.observations) == 10
    assert not result.failed_blocking_gates()


def test_no_candidate_cannot_be_confirmed_even_when_model_attempts_it() -> None:
    """A missing canonical candidate is forced to review, not false confirmation."""

    document = load_v3_experiment_document(DEVELOPMENT_MATRIX)
    case = next(item for item in document.cases if item.sample_id == "V3_unmapped_dev")
    stage1 = V3Stage1Output.model_validate(case.golden_stage1)
    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    retriever = V3CandidateRetriever(
        vocabulary=vocabulary,
        embeddings=ZeroEmbeddingClient(),
        minimum_score=0.99,
    )
    qwen = ItemVerifierQwenClient()
    analyzer = InputPreprocessingV3Analyzer(
        qwen=qwen,
        vocabulary=vocabulary,
        candidate_retriever=retriever,
        model="fake-v3",
    )

    result = asyncio.run(
        analyzer.analyze(
            user_text=case.user_text,
            turn_context=_turn_context("unmapped"),
            stage1_override=stage1,
        )
    )
    evidence = result.stage2.observations[0]

    assert qwen.stage2_calls == 1
    assert qwen.last_attempted_confirmation is True
    assert evidence.mapping_status == V3CanonicalMappingStatus.NOT_FOUND
    assert evidence.selected_candidate_id is None
    assert evidence.canonical_id is None
    assert evidence.review_required is True
    assert not result.failed_blocking_gates()


def test_file_async_queue_is_bounded_durable_and_dead_letters_failure() -> None:
    """Queue overflow, completion, and dead-letter state persist explicitly."""

    with TemporaryDirectory() as temporary_directory:
        queue = FileAsyncShadowQueueV3(
            directory=Path(temporary_directory),
            max_size=2,
        )
        snapshots = [_snapshot(index) for index in range(3)]
        submissions = [queue.submit(snapshot) for snapshot in snapshots]
        first = queue.claim()
        assert first is not None
        queue.complete(first.snapshot_id, trace={"status": "passed"})
        second = queue.claim()
        assert second is not None
        result = queue.fail(second.snapshot_id, reason="timeout", max_attempts=1)

        assert [item.accepted for item in submissions] == [True, True, False]
        assert submissions[-1].reason == "queue_full"
        assert result == "dead_letter"
        assert len(queue.dead_letters()) == 1


def _turn_context(request_id: str) -> V3TurnContext:
    return V3TurnContext(
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        user_id="v3-test-user",
        pet_id="v3-test-pet",
        session_id="v3-test-session",
        reference_time="2026-08-25T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject={  # type: ignore[arg-type]
            "reference_id": "current_pet",
            "entity_type": "current_pet",
        },
    )


def _snapshot(index: int) -> AsyncShadowSnapshotV3:
    return AsyncShadowSnapshotV3(
        snapshot_id=f"snapshot-{index}",
        sample_id=f"sample-{index}",
        user_text=f"文本-{index}",
        turn_context=_turn_context(f"queue-{index}"),
    )


class ItemVerifierQwenClient:
    """Structured client that deliberately follows or attacks item mapping."""

    def __init__(self) -> None:
        self.stage2_calls = 0
        self.last_attempted_confirmation = False

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
        assert response_model is V3ItemVerificationRaw
        self.stage2_calls += 1
        payload = json.loads(messages[1]["content"])
        candidates = payload["candidates"]
        attempted_confirmation = not candidates
        self.last_attempted_confirmation = attempted_confirmation
        return V3ItemVerificationRaw(
            item_key=payload["item"]["item_key"],
            assertion_verification="verified",
            mapping_status="confirmed",
            selected_candidate_id=(
                candidates[0]["candidate_id"] if candidates else "c-1"
            ),
            participant_verification=(
                "verified" if payload["item"]["participants"] else "not_applicable"
            ),
            confidence=0.99,
        )


class AliasEmbeddingClient:
    """Deterministic exact-alias embeddings for local contract tests."""

    def __init__(self, vocabulary: CanonicalVocabulary) -> None:
        self.aliases = [
            alias
            for term in vocabulary.terms
            for alias in term.aliases
        ]
        self.index = {alias: position for position, alias in enumerate(self.aliases)}

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * len(self.aliases)
        if text in self.index:
            vector[self.index[text]] = 1.0
        return vector


class ZeroEmbeddingClient:
    """Embedding client that recalls no candidate for unknown mentions."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]
