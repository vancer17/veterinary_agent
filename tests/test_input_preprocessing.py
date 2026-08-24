"""Tests for the input-preprocessing shadow quick-validation path."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vet_agent.agents.semantic_extractor import ConsultationFactStatus
from vet_agent.input_preprocessing.analyzer import InputPreprocessingAnalyzer
from vet_agent.input_preprocessing.contracts import (
    AssertionState,
    CanonicalStatus,
    DiscourseRole,
    EvidenceAnalysisOutput,
    EvidenceObservation,
    InputContentProfile,
    PreprocessingIntent,
    QualityGateStatus,
    SegmentationOutput,
    SegmentModel,
    SubjectBinding,
    SubjectReference,
    SubjectResolutionMethod,
    TurnContext,
)
from vet_agent.input_preprocessing.evaluation import (
    InputPreprocessingEvaluation,
    load_suite,
)
from vet_agent.input_preprocessing.projection import project_consultation
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

ROOT = Path(__file__).parent.parent
SUITE_PATH = ROOT / "tests/fixtures/input_preprocessing/quick_validation.json"
VOCABULARY_PATH = (
    ROOT / "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
)


class FakeQwenClient:
    """Return deterministic structured outputs for the two model stages."""

    available = True

    def __init__(
        self, segmentation: SegmentationOutput, evidence: EvidenceAnalysisOutput
    ) -> None:
        self.segmentation = segmentation
        self.evidence = evidence
        self.calls = 0

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any:
        del messages, response_model, model, temperature
        self.calls += 1
        if self.calls == 1:
            return self.segmentation
        return self.evidence


class FakeEmbeddingClient:
    """Return a stable vector without invoking an external service."""

    available = True

    def embed(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]


def test_analyzer_and_projection_preserve_denied_semantics() -> None:
    """Verify the shadow pipeline keeps denied distinct from present."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    segmentation = SegmentationOutput(
        profile=InputContentProfile(expected_fact_candidate_count=1),
        intent=PreprocessingIntent(),
        segments=[
            SegmentModel(
                segment_id="seg-1",
                source_text="没有呕吐",
                analysis_text="当前宠物没有呕吐",
                discourse_role=DiscourseRole.FACT_STATEMENT,
                confidence=0.98,
            )
        ],
    )
    evidence = EvidenceAnalysisOutput(
        observations=[
            EvidenceObservation(
                evidence_id="ev-1",
                segment_id="seg-1",
                source_text="没有呕吐",
                canonical_id="vomiting",
                canonical_status=CanonicalStatus.CONFIRMED,
                assertion=AssertionState.DENIED,
                subject=SubjectBinding(
                    subject_reference="current_pet",
                    subject_type="current_pet",
                    resolution_method=SubjectResolutionMethod.TRUSTED_CURRENT_PET,
                    confidence=0.98,
                ),
                confidence=0.98,
            )
        ]
    )
    analyzer = InputPreprocessingAnalyzer(
        qwen=FakeQwenClient(segmentation, evidence),
        embeddings=FakeEmbeddingClient(),
        vocabulary=vocabulary,
        model="fake-model",
    )

    result = asyncio.run(
        analyzer.analyze(user_text="没有呕吐", turn_context=_turn_context())
    )
    projection = project_consultation(result, vocabulary=vocabulary)

    assert result.failed_blocking_gates() == []
    assert result.evidence.observations[0].assertion == AssertionState.DENIED
    assert projection.semantic_result.facts[0].key.value == "vomiting"
    assert projection.semantic_result.facts[0].status == ConsultationFactStatus.NEGATIVE
    assert projection.semantic_result.facts[0].metadata["assertion"] == "denied"


def test_suspicious_empty_is_a_blocking_quality_gate() -> None:
    """Verify a non-empty factual input cannot silently become an empty result."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    segmentation = SegmentationOutput(
        profile=InputContentProfile(expected_fact_candidate_count=1),
        intent=PreprocessingIntent(),
        segments=[
            SegmentModel(
                segment_id="seg-1",
                source_text="没有呕吐",
                analysis_text="当前宠物没有呕吐",
                discourse_role=DiscourseRole.FACT_STATEMENT,
                confidence=0.98,
            )
        ],
    )
    analyzer = InputPreprocessingAnalyzer(
        qwen=FakeQwenClient(segmentation, EvidenceAnalysisOutput()),
        embeddings=FakeEmbeddingClient(),
        vocabulary=vocabulary,
        model="fake-model",
    )

    result = asyncio.run(
        analyzer.analyze(user_text="没有呕吐", turn_context=_turn_context())
    )
    gate = next(item for item in result.gates if item.gate_id == "suspicious_empty")

    assert gate.status == QualityGateStatus.FAILED
    assert gate.reason_code == "segmentation_suspicious_empty"
    assert result.failed_blocking_gates()


def test_ideal_injection_supports_answer_now_and_multi_turn_recovery() -> None:
    """Verify downstream consultation behavior with ideal structured facts."""

    evaluation = InputPreprocessingEvaluation(
        suite=load_suite(SUITE_PATH),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        policy="local",
    )
    reports = asyncio.run(evaluation.run(mode="ideal", repeat=1))
    by_sample = {item["sample_id"]: item for item in reports}

    assert all(item["status"] == "passed" for item in reports)
    answer_now = by_sample["C_answer_now"]["turns"][0]["behavior_simulation"]
    recovery = by_sample["D_second_round_recovery"]["turns"][1]["behavior_simulation"]
    assert answer_now["action"] == "answer"
    assert answer_now["mode"] == "user_requested_answer_now"
    assert recovery["action"] == "answer"
    assert recovery["unknown_slot_count"] < recovery["previous_unknown_slot_count"]
    assert recovery["answered_slot_repeat_count"] == 0


def test_subject_ambiguity_is_observable_and_not_projected() -> None:
    """Verify multi-pet ambiguity is not silently defaulted to the current pet."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    segmentation = SegmentationOutput(
        profile=InputContentProfile(expected_fact_candidate_count=1),
        intent=PreprocessingIntent(),
        segments=[
            SegmentModel(
                segment_id="seg-1",
                source_text="好像呕吐",
                analysis_text="某一只宠物好像呕吐",
                discourse_role=DiscourseRole.UNCERTAIN_STATEMENT,
                confidence=0.8,
            )
        ],
    )
    evidence = EvidenceAnalysisOutput(
        observations=[
            EvidenceObservation(
                evidence_id="ev-1",
                segment_id="seg-1",
                source_text="好像呕吐",
                canonical_id="vomiting",
                canonical_status=CanonicalStatus.AMBIGUOUS,
                assertion=AssertionState.POSSIBLE,
                subject=SubjectBinding(
                    subject_reference="subject_ambiguous",
                    subject_type="unknown",
                    resolution_method=SubjectResolutionMethod.SUBJECT_AMBIGUOUS,
                    confidence=0.8,
                ),
                confidence=0.8,
            )
        ]
    )
    analyzer = InputPreprocessingAnalyzer(
        qwen=FakeQwenClient(segmentation, evidence),
        embeddings=FakeEmbeddingClient(),
        vocabulary=vocabulary,
        model="fake-model",
    )
    result = asyncio.run(
        analyzer.analyze(user_text="好像呕吐", turn_context=_turn_context())
    )
    projection = project_consultation(result, vocabulary=vocabulary)

    assert (
        result.evidence.observations[0].subject.subject_reference == "subject_ambiguous"
    )
    assert projection.semantic_result.facts == []
    assert projection.semantic_result.observations == []
    assert projection.rejected_observations


def _turn_context() -> TurnContext:
    return TurnContext(
        request_id="request-1",
        trace_id="trace-1",
        user_id="user-1",
        pet_id="pet-1",
        session_id="session-1",
        reference_time="2026-08-24T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject=SubjectReference(
            reference_id="current_pet",
            subject_type="current_pet",
            display_name="当前宠物",
        ),
    )
