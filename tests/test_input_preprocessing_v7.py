"""Deterministic V7 attribution microbench tests."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from vet_agent.input_preprocessing.v7_contracts import (
    V7ExperimentId,
    V7IntentBatchRawOutput,
    V7IntentBinaryRaw,
    V7ParticipantRawOutput,
    V7ParticipantSelectionRaw,
    V7ThinUserClaimRaw,
)
from vet_agent.input_preprocessing.v7_experiments import (
    V7AttributionRunner,
    load_v7_experiment_document,
)
from vet_agent.input_preprocessing.v7_microbench import V7MicroAnalyzer
from vet_agent.input_preprocessing.v7_run_cache import V7RunCache
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

VOCABULARY = CanonicalVocabulary.load(
    Path("assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json")
)


def test_v7_development_core_microbench_ideal_control_passes() -> None:
    document = load_v7_experiment_document(
        Path("tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json")
    )
    runner = V7AttributionRunner(document=document, vocabulary=VOCABULARY)
    reports = runner.run_for_test(mode="ideal")
    assert [item["status"] for item in reports] == ["passed"] * 8
    assert all(
        item["safety_boundary"]["clinical_safety_opa_called"] is False
        for item in reports
    )


def test_v7_held_out_core_microbench_ideal_control_passes() -> None:
    document = load_v7_experiment_document(
        Path(
            "tests/fixtures/input_preprocessing/seventh_round_attribution_held_out.json"
        )
    )
    runner = V7AttributionRunner(document=document, vocabulary=VOCABULARY)
    reports = runner.run_for_test(mode="ideal")
    assert [item["status"] for item in reports] == ["passed"] * 8


def test_v7_thin_contract_does_not_reintroduce_relation_or_canonical() -> None:
    fields = set(V7ThinUserClaimRaw.model_fields)
    assert "relation" not in fields
    assert "canonical_id" not in fields
    assert "canonical_surface" not in fields
    assert "relation_quote" in fields


def test_v7_run_cache_prevents_duplicate_model_call() -> None:
    with TemporaryDirectory() as temporary:
        cache = V7RunCache(Path(temporary) / "run-cache.json")
        client = FixedIntentV7Client()
        analyzer = V7MicroAnalyzer(qwen=client, cache=cache)
        payload = [{"unit_id": "unit-1", "user_text": "请先回答。"}]
        first = analyzer.run_intent_for_test(
            experiment_id=V7ExperimentId.INTENT_ANSWER_NOW,
            units=payload,
            turn_context_digest="digest",
        )
        second = analyzer.run_intent_for_test(
            experiment_id=V7ExperimentId.INTENT_ANSWER_NOW,
            units=payload,
            turn_context_digest="digest",
        )
        assert client.call_count == 1
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert cache.hit_count == 1
        assert cache.miss_count == 1


def test_v7_invalid_intent_quote_is_attributed_and_failed() -> None:
    document = load_v7_experiment_document(
        Path("tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json")
    )
    runner = V7AttributionRunner(
        document=document,
        vocabulary=VOCABULARY,
        analyzer=V7MicroAnalyzer(qwen=InvalidIntentQuoteV7Client()),
    )
    reports = runner.run_for_test(
        mode="shadow",
        only_experiment_ids={V7ExperimentId.INTENT_ANSWER_NOW.value},
    )
    report = reports[0]
    assert report["status"] == "failed"
    assert "quote_selector_error" in report["attribution_distribution"]


def test_v7_invented_participant_is_blocked_and_attributed() -> None:
    document = load_v7_experiment_document(
        Path("tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json")
    )
    runner = V7AttributionRunner(
        document=document,
        vocabulary=VOCABULARY,
        analyzer=V7MicroAnalyzer(qwen=InventedParticipantV7Client()),
    )
    reports = runner.run_for_test(
        mode="shadow",
        only_experiment_ids={V7ExperimentId.PART_GOLDEN.value},
    )
    report = reports[0]
    assert report["status"] == "failed"
    assert "participant_candidate_error" in report["attribution_distribution"]
    assert report["metrics"]["invented_entity_count"] > 0


class FixedIntentV7Client:
    @property
    def available(self) -> bool:
        return True

    def __init__(self) -> None:
        self.call_count = 0

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any:
        assert response_model is V7IntentBatchRawOutput
        self.call_count += 1
        return V7IntentBatchRawOutput(
            results=[
                V7IntentBinaryRaw(
                    unit_id="unit-1",
                    detected=True,
                    evidence_quote="请先回答",
                    confidence=1.0,
                )
            ]
        )


class InvalidIntentQuoteV7Client:
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
        assert response_model is V7IntentBatchRawOutput
        unit_ids = [
            "ans-simple-1",
            "ans-long-1",
            "ans-summary-1",
            "ans-fact-only-1",
            "ans-question-only-1",
        ]
        return V7IntentBatchRawOutput(
            results=[
                V7IntentBinaryRaw(
                    unit_id=unit_id,
                    detected=True,
                    evidence_quote="这句原文里不存在",
                    confidence=1.0,
                )
                for unit_id in unit_ids
            ]
        )


class InventedParticipantV7Client:
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
        assert response_model is V7ParticipantRawOutput
        unit_ids = [
            "part-user-food-1",
            "part-medical-actor-1",
            "part-caregiver-1",
            "part-other-recipient-1",
        ]
        return V7ParticipantRawOutput(
            results=[
                V7ParticipantSelectionRaw(
                    unit_id=unit_id,
                    action_agent_selected_candidate="罐头",
                    action_recipient_selected_candidate="current_pet",
                    object_mention="罐头",
                    resolution_status="resolved",
                    confidence=1.0,
                )
                for unit_id in unit_ids
            ]
        )
