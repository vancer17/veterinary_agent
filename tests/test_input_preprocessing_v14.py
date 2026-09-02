from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from vet_agent.input_preprocessing.v6_canonical_linker import V6CandidateRetriever
from vet_agent.input_preprocessing.v8_contracts import V8EntityCandidate
from vet_agent.input_preprocessing.v8_experiments import V8HashEmbeddingClient
from vet_agent.input_preprocessing.v10_fixture import load_v10_fixture
from vet_agent.input_preprocessing.v13_aligner import V13SourceBlock
from vet_agent.input_preprocessing.v14_alignment import align_v14_claim
from vet_agent.input_preprocessing.v14_canonical_selector import (
    V14ConstrainedCanonicalSelector,
)
from vet_agent.input_preprocessing.v14_contracts import (
    V14ClaimGenerationRaw,
    V14GenerationOptions,
    V14SignalDetection,
    V14TurnIntentRaw,
)
from vet_agent.input_preprocessing.v14_generation_options import generation_options
from vet_agent.input_preprocessing.v14_governance import (
    evaluate_v14_alignment,
    evaluate_v14_claims,
    evaluate_v14_intent,
    evaluate_v14_participants,
)
from vet_agent.input_preprocessing.v14_intent import (
    ideal_v14_intent,
    intent_reconciliation,
)
from vet_agent.input_preprocessing.v14_participant_resolver import (
    V14TurnContextParticipantResolver,
)
from vet_agent.input_preprocessing.v14_prompt_skills import (
    V14LLMFirstGenerator,
    ideal_v14_records,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

MATRIX = Path(
    "tests/fixtures/input_preprocessing/tenth_round_boundary_calibration_development.json"
)
VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)


def test_fixed_field_intent_cannot_duplicate_fact_statement_and_requires_reason() -> None:
    detected = {
        key: V14SignalDetection(detected=False)
        for key in (
            "answer_now",
            "wants_triage",
            "correction",
            "clarification_request",
            "fact_statement_present",
            "question_present",
            "report_context_present",
        )
    }
    with pytest.raises(ValidationError):
        V14TurnIntentRaw(
            schema_version="v14-fixed-field-intent-1",
            **detected,
        )
    detected["fact_statement_present"] = V14SignalDetection(
        detected=True,
        evidence_phrase="它没有呕吐",
    )
    intent = V14TurnIntentRaw(
        schema_version="v14-fixed-field-intent-1",
        **detected,
        no_signal_reason=None,
    )
    assert intent.fact_statement_present.detected is True
    assert intent_reconciliation(intent, governed_claim_count=1)["status"] == "consistent"
    assert intent_reconciliation(intent, governed_claim_count=0)["review_required"] is True


def test_claim_inventory_must_match_claim_records() -> None:
    fixture = load_v10_fixture(MATRIX)
    ideal = ideal_v14_records(fixture.units[0])
    assert len(ideal.claim_inventory) == len(ideal.claims)
    payload = ideal.model_dump(mode="json")
    payload["claims"].pop()
    with pytest.raises(ValidationError, match="claim_inventory_mismatch"):
        V14ClaimGenerationRaw.model_validate(payload)


def test_generation_options_are_bounded_and_versioned() -> None:
    assert generation_options("p0").temperature == 0.0
    assert generation_options("p1").seed == 14
    assert generation_options("p2").top_p == 1.0
    assert generation_options("p3").temperature == 0.2
    with pytest.raises(ValueError):
        generation_options("p9")


def test_v14_generator_uses_inventory_and_approximate_phrase_skills() -> None:
    class CaptureClient:
        adapter_name = "capture"

        async def run_structured_with_details(
            self,
            *,
            messages: list[dict[str, Any]],
            response_model: type[BaseModel],
            model: str,
            options: V14GenerationOptions,
        ) -> Any:
            self.messages = messages
            self.options = options

            class Result:
                output = ideal_v14_records(load_v10_fixture(MATRIX).units[0])
                usage: ClassVar[dict[str, int]] = {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
                finish_reason = "stop"
                response_id = "resp-test"
                provider_model = "qwen-plus-test"

            return Result()

    client = CaptureClient()
    execution = asyncio.run(
        V14LLMFirstGenerator(
            client=client,
            options=generation_options("p0"),
        ).claims(
            unit_id="macro-answer-fact",
            user_text="它前天开始换新猫粮，这两天大便有一点软，没有呕吐。",
        )
    )
    prompt = client.messages[0]["content"]
    assert "claim_inventory" in prompt
    assert "approximate semantic proposal" in prompt
    assert "不得丢失否定" in prompt
    assert execution.metadata.token_count_available is True
    assert execution.metadata.total_token_count == 15


def test_claim_local_alignment_uses_parent_occurrence_and_blocks_wrong_target() -> None:
    fixture = load_v10_fixture(MATRIX)
    unit = next(
        item for item in fixture.units if item["unit_id"] == "macro-action-roles"
    )
    ideal = ideal_v14_records(unit)
    raw = ideal.claims[1]
    blocks = [V13SourceBlock(str(unit["unit_id"]), "block-001", str(unit["user_text"]))]
    governed = align_v14_claim(raw, source_id=str(unit["unit_id"]), blocks=blocks)
    assert governed.evidence.aligned_quote == "我前天开始给它换新猫粮"
    assert governed.fields["action_recipient"].aligned_quote == "它"
    assert governed.fields["action_recipient"].start >= governed.evidence.start


def test_resolver_is_candidate_only_role_compatible_and_ambiguous() -> None:
    resolver = V14TurnContextParticipantResolver()
    candidates = [
        V8EntityCandidate(
            reference_id="pet-current",
            entity_type="current_pet",
            mention_aliases=["它"],
        ),
        V8EntityCandidate(
            reference_id="pet-other",
            entity_type="other_pet",
            mention_aliases=["它"],
        ),
    ]
    ambiguous = resolver.resolve(
        role="subject",
        phrase="它",
        candidates=candidates,
    )
    assert ambiguous.status == "ambiguous"
    assert ambiguous.selected_reference_id is None
    mismatch = resolver.resolve(
        role="action_agent",
        phrase="它",
        candidates=candidates,
    )
    assert mismatch.status == "unresolved"


def test_ideal_v14_quick_metrics_are_contract_control() -> None:
    fixture = load_v10_fixture(MATRIX)
    vocabulary = CanonicalVocabulary.load(VOCABULARY)
    terms = vocabulary.term_map()
    descriptors = {
        (str(unit["unit_id"]), str(claim["claim_id"])): terms[str(claim["expected_canonical_ids"][0])].aliases[0]
        for unit in fixture.units
        for claim in unit.get("expected_claims", [])
        if claim.get("expected_canonical_ids")
    }
    for unit in fixture.units:
        intent = ideal_v14_intent(unit)
        output = ideal_v14_records(unit, canonical_descriptors=descriptors)
        from vet_agent.input_preprocessing.v14_alignment import align_v14_output

        governed = align_v14_output(
            output,
            source_id=str(unit["unit_id"]),
            text=str(unit["user_text"]),
        )
        assert evaluate_v14_intent(unit=unit, output=intent)["metrics"][
            "fact_statement_duplicate_count"
        ] == 0
        assert evaluate_v14_claims(unit=unit, governed=governed)["metrics"][
            "claim_recall"
        ] == 1.0
        assert evaluate_v14_alignment(unit=unit, governed=governed)["metrics"][
            "false_alignment_rate"
        ] == 0.0
        participant_metrics = evaluate_v14_participants(
            unit=unit,
            governed=governed,
        )["metrics"]
        if participant_metrics["participant_expected_count"]:
            assert participant_metrics["participant_resolution_accuracy"] == 1.0


def test_constrained_canonical_selector_uses_exact_alias_candidate_only() -> None:
    vocabulary = CanonicalVocabulary.load(VOCABULARY)
    selector = V14ConstrainedCanonicalSelector(
        retriever=V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=V8HashEmbeddingClient(),
        )
    )
    selection = selector.select(
        claim_id="claim",
        target_phrase="呕吐",
        descriptor="呕吐",
        coarse_type="symptom",
    )
    assert selection.status == "confirmed"
    assert selection.selected_canonical_id == "vomiting"
