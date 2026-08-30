"""Tests for the fourth-round V4 flat quote-anchored architecture."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from vet_agent.input_preprocessing.v4_analyzer import InputPreprocessingV4Analyzer
from vet_agent.input_preprocessing.v4_candidate_linker import V4CandidateRetriever
from vet_agent.input_preprocessing.v4_contracts import (
    FlatExtractionRawOutput,
    FlatObservationRaw,
    V4EntityType,
    V4TurnContext,
)
from vet_agent.input_preprocessing.v4_experiments import (
    AsyncShadowSnapshotV4,
    FileAsyncShadowQueueV4,
    V4ArchitectureValidationRunner,
    load_v4_experiment_document,
)
from vet_agent.input_preprocessing.v4_quote_governance import (
    normalize_quote_text,
    resolve_observation_quotes,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

ROOT = Path(__file__).parent.parent
DEVELOPMENT_MATRIX = (
    ROOT / "tests/fixtures/input_preprocessing/fourth_round_flat_shadow_matrix.json"
)
HELD_OUT_MATRIX = (
    ROOT / "tests/fixtures/input_preprocessing/fourth_round_flat_held_out_matrix.json"
)
VOCABULARY_PATH = (
    ROOT / "assets/evaluations/input_preprocessing_canonical_vocabulary.v4.json"
)


def test_fourth_round_development_ideal_control_passes() -> None:
    """Validate the deterministic V4 development control path."""

    runner = V4ArchitectureValidationRunner(
        document=load_v4_experiment_document(DEVELOPMENT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        async_queue_directory=ROOT / ".tmp/input-preprocessing-v4-test-queue",
    )
    reports = asyncio.run(runner.run(mode="ideal", repeat_override=1))

    assert len(reports) == 17
    assert all(report["status"] == "passed" for report in reports)


def test_fourth_round_held_out_confirmatory_control_passes() -> None:
    """Validate held-out V4 samples with three-repeat stability."""

    if not HELD_OUT_MATRIX.is_file():
        pytest.skip("restricted held-out fixture is not published")
    runner = V4ArchitectureValidationRunner(
        document=load_v4_experiment_document(HELD_OUT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        async_queue_directory=ROOT / ".tmp/input-preprocessing-v4-test-queue-held",
    )
    reports = asyncio.run(runner.run(mode="ideal", phase="confirmatory"))

    assert len(reports) == 15
    assert all(report["status"] == "passed" for report in reports)
    assert all(
        report["metrics"].get("unique_output_count", 1) == 1 for report in reports
    )


def test_v4_negative_mutations_are_all_blocked() -> None:
    """Verify quote, subject, candidate, review, and empty-output mutations."""

    runner = V4ArchitectureValidationRunner(
        document=load_v4_experiment_document(DEVELOPMENT_MATRIX),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
    )
    reports = asyncio.run(
        runner.run(
            mode="ideal",
            only_experiment_ids={"NEG_D4"},
            repeat_override=1,
        )
    )
    turns = reports[0]["turns"]

    assert reports[0]["status"] == "passed"
    assert len(turns) == 10
    assert all(turn["status"] == "gate_blocked_as_expected" for turn in turns)
    assert all(turn["blocking_gates"] for turn in turns)


def test_flat_raw_schema_does_not_ask_model_for_canonical_id_or_counts() -> None:
    """The model-facing flat schema stays flat and non-authoritative."""

    raw_fields = set(FlatObservationRaw.model_fields)
    top_level_fields = set(FlatExtractionRawOutput.model_fields)

    assert "canonical_id" not in raw_fields
    assert "selected_candidate_id" not in raw_fields
    assert "profile_expected_fact_count" not in top_level_fields
    assert "expected_evidence_count" not in top_level_fields


def test_conservative_quote_normalization_and_strict_semantic_rejection() -> None:
    """Only format-level normalization is allowed for quote anchoring."""

    source = "没有呕吐、干呕；精神正常。"
    exact = _raw_observation(
        evidence_quote="没有呕吐、干呕",
        target_quote="呕吐",
        canonical_surface="呕吐",
    )
    normalized = _raw_observation(
        evidence_quote="没有呕吐,干呕;;",
        target_quote="呕吐",
        canonical_surface="呕吐",
    )
    semantic_rewrite = _raw_observation(
        evidence_quote="没有出现呕吐症状",
        target_quote="呕吐",
        canonical_surface="呕吐",
    )

    exact_anchors = resolve_observation_quotes(
        user_text=source,
        raw=exact,
    )
    normalized_anchors = resolve_observation_quotes(
        user_text=source,
        raw=normalized,
    )
    semantic_anchors = resolve_observation_quotes(
        user_text=source,
        raw=semantic_rewrite,
    )

    assert exact_anchors[0].status == "resolved"
    assert exact_anchors[1].status == "resolved"
    assert normalized_anchors[0].status == "resolved"
    assert normalized_anchors[1].status == "resolved"
    assert semantic_anchors[0].status == "not_found"
    assert normalize_quote_text(" 没有 呕吐，，。 ") == normalize_quote_text(
        "没有呕吐,."
    )


def test_v4_analyzer_uses_one_call_and_cannot_confirm_without_candidate() -> None:
    """A no-candidate surface is forced to not_found plus review."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    qwen = OneCallFlatQwenClient(mode="unmapped")
    analyzer = InputPreprocessingV4Analyzer(
        qwen=qwen,
        vocabulary=vocabulary,
        candidate_retriever=V4CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=ZeroEmbeddingClient(),
        ),
        model="fake-v4",
    )

    result = asyncio.run(
        analyzer.analyze(
            user_text="它最近出现ZZZ表现，请帮忙看看。",
            turn_context=_turn_context("v4-unmapped"),
        )
    )

    assert qwen.call_count == 1
    assert len(result.observations) == 1
    assert result.observations[0].mapping_status.value == "not_found"
    assert result.observations[0].canonical_id is None
    assert result.observations[0].review_required is True
    assert not result.failed_blocking_gates()


def test_v4_analyzer_resolves_canonical_from_selected_candidate() -> None:
    """The canonical ID is resolved only through an auditable candidate."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    qwen = OneCallFlatQwenClient(mode="vomiting")
    analyzer = InputPreprocessingV4Analyzer(
        qwen=qwen,
        vocabulary=vocabulary,
        candidate_retriever=V4CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=AliasEmbeddingClient(vocabulary),
            minimum_score=0.99,
        ),
        model="fake-v4",
    )

    result = asyncio.run(
        analyzer.analyze(
            user_text="没有呕吐、干呕、反流、流涎或舔唇。",
            turn_context=_turn_context("v4-candidate"),
        )
    )

    assert qwen.call_count == 1
    observation = result.observations[0]
    assert observation.selected_candidate_id == "c-1"
    assert observation.canonical_id == "vomiting"
    assert observation.candidate_set.candidates
    assert observation.candidate_set.candidates[0].canonical_id == "vomiting"
    assert not result.failed_blocking_gates()


def test_file_async_queue_is_bounded_durable_and_dead_letters_failure() -> None:
    """The V4 experiment queue isolates overflow and worker failure."""

    with TemporaryDirectory() as temporary:
        queue = FileAsyncShadowQueueV4(directory=Path(temporary), max_size=2)
        snapshots = [
            AsyncShadowSnapshotV4(
                snapshot_id=f"snapshot-{index}",
                sample_id=f"sample-{index}",
                user_text=f"文本-{index}",
                turn_context=_turn_context(f"queue-{index}"),
            )
            for index in range(3)
        ]
        submissions = [queue.submit(snapshot) for snapshot in snapshots]
        first = queue.claim()
        assert first is not None
        queue.complete(first.snapshot_id, trace={"status": "passed"})
        second = queue.claim()
        assert second is not None
        result = queue.fail(
            second.snapshot_id,
            reason="simulated_stage_timeout",
            max_attempts=1,
        )

        assert [item.accepted for item in submissions] == [True, True, False]
        assert submissions[-1].reason == "queue_full"
        assert result == "dead_letter"
        assert len(queue.dead_letters()) == 1


def _turn_context(request_id: str) -> V4TurnContext:
    return V4TurnContext(
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        user_id="v4-test-user",
        pet_id="v4-test-pet",
        session_id="v4-test-session",
        reference_time="2026-08-25T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject={  # type: ignore[arg-type]
            "reference_id": "current_pet",
            "entity_type": V4EntityType.CURRENT_PET,
        },
    )


def _raw_observation(
    *,
    evidence_quote: str,
    target_quote: str,
    canonical_surface: str,
) -> FlatObservationRaw:
    return FlatObservationRaw(
        observation_id="obs-1",
        evidence_quote=evidence_quote,
        target_quote=target_quote,
        event_or_state_text=canonical_surface,
        semantic_class="state",
        assertion="denied",
        subject_reference="current_pet",
        subject_type=V4EntityType.CURRENT_PET,
        subject_resolution_method="trusted_current_pet",
        subject_resolution_status="resolved",
        canonical_surface=canonical_surface,
        confidence=0.99,
    )


class OneCallFlatQwenClient:
    """Return one prepared flat extraction response per test."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.call_count = 0

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
        assert response_model is FlatExtractionRawOutput
        self.call_count += 1
        payload = json.loads(messages[1]["content"])
        user_text = payload["user_text"]
        if self.mode == "unmapped":
            raw = _raw_observation(
                evidence_quote="它最近出现ZZZ表现",
                target_quote="ZZZ表现",
                canonical_surface="ZZZ表现",
            )
            raw = raw.model_copy(update={"assertion": "present"})
        else:
            raw = _raw_observation(
                evidence_quote="没有呕吐、干呕、反流、流涎或舔唇",
                target_quote="呕吐",
                canonical_surface="呕吐",
            )
            raw = raw.model_copy(update={"subject_resolution_method": "subject_missing"})
        assert raw.evidence_quote in user_text
        return FlatExtractionRawOutput(
            intent={"answer_now": False, "wants_triage": False, "correction": False},
            profile={"has_factual_statements": True},
            observations=[raw],
        )


class AliasEmbeddingClient:
    """Deterministic exact-alias embeddings for local contract tests."""

    def __init__(self, vocabulary: CanonicalVocabulary) -> None:
        self.aliases = [
            alias for term in vocabulary.terms for alias in term.aliases
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
    """Embedding client that recalls no candidate."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]
