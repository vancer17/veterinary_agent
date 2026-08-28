"""Deterministic V5 thin-claim contract and runner tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from vet_agent.input_preprocessing.v5_analyzer import InputPreprocessingV5Analyzer
from vet_agent.input_preprocessing.v5_canonical_linker import (
    V5CandidateRetriever,
    V5EmbeddingClient,
)
from vet_agent.input_preprocessing.v5_contracts import (
    ThinExtractionRawOutput,
    ThinUserClaimRaw,
    V5CoarseType,
    V5EntityType,
    V5QualityGateStatus,
    V5ResolutionMethod,
    V5ResolutionStatus,
    V5TurnContext,
    V5TurnIntentRaw,
    V5UserStatementType,
)
from vet_agent.input_preprocessing.v5_experiments import (
    AsyncShadowSnapshotV5,
    FileAsyncShadowQueueV5,
    V5ArchitectureValidationRunner,
    load_v5_experiment_document,
)
from vet_agent.input_preprocessing.v5_quote_governance import (
    normalize_quote_text,
    resolve_thin_claim_quotes,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

VOCABULARY = CanonicalVocabulary.load(
    Path("assets/evaluations/input_preprocessing_canonical_vocabulary.v4.json")
)


def test_fifth_round_development_ideal_control_passes() -> None:
    document = load_v5_experiment_document(
        Path("tests/fixtures/input_preprocessing/fifth_round_thin_shadow_matrix.json")
    )
    runner = V5ArchitectureValidationRunner(
        document=document,
        vocabulary=VOCABULARY,
    )
    reports = runner._run_all_ideal_for_test()
    assert [item["status"] for item in reports] == ["passed"] * len(reports)


def test_fifth_round_held_out_ideal_confirmatory_passes() -> None:
    document = load_v5_experiment_document(
        Path("tests/fixtures/input_preprocessing/fifth_round_thin_held_out_matrix.json")
    )
    runner = V5ArchitectureValidationRunner(
        document=document,
        vocabulary=VOCABULARY,
    )
    reports = runner._run_all_ideal_for_test()
    assert [item["status"] for item in reports] == ["passed"] * len(reports)


def test_v5_negative_mutations_are_blocked_or_reviewed() -> None:
    document = load_v5_experiment_document(
        Path("tests/fixtures/input_preprocessing/fifth_round_thin_shadow_matrix.json")
    )
    case = next(
        item for item in document.cases if item.sample_id == "D5_shared_scope_dev"
    )
    runner = V5ArchitectureValidationRunner(
        document=document,
        vocabulary=VOCABULARY,
    )
    result = runner._mutated_negative_result(case)
    assert any(
        gate.status in {V5QualityGateStatus.FAILED, V5QualityGateStatus.NEEDS_REVIEW}
        for gate in result.gates
    )
    assert result.claims[0].state.quote_state.value == "blocked"


def test_thin_schema_omits_canonical_and_enrichment_fields() -> None:
    field_names = set(ThinUserClaimRaw.model_fields)
    forbidden = {
        "canonical_id",
        "selected_candidate_id",
        "canonical_surface",
        "action_agent",
        "action_recipient",
        "action_object",
        "normalized_temporal_value",
        "normalized_measurement_value",
    }
    assert field_names.isdisjoint(forbidden)
    assert {
        "evidence_quote",
        "target_quote",
        "user_statement_type",
        "temporal_quote",
        "measurement_quote",
    } <= field_names


def test_v5_quote_normalization_is_conservative_and_strict() -> None:
    assert normalize_quote_text("没有呕吐，") == normalize_quote_text("没有呕吐,")
    raw = ThinUserClaimRaw(
        claim_id="claim-1",
        evidence_quote="没有呕吐",
        target_quote="呕吐",
        user_statement_type=V5UserStatementType.DENIES,
        coarse_type=V5CoarseType.SYMPTOM,
    )
    evidence, target, temporal, measurement = resolve_thin_claim_quotes(
        user_text="它说：没有呕吐。",
        raw=raw,
    )
    assert evidence.status == "resolved"
    assert target.status == "resolved"
    assert temporal is None
    assert measurement is None


def test_v5_analyzer_uses_target_quote_and_cannot_confirm_without_candidate() -> None:
    qwen = OnePathV5QwenClient()
    embeddings = AliasEmbeddingClient(VOCABULARY)
    analyzer = InputPreprocessingV5Analyzer(
        qwen=qwen,
        vocabulary=VOCABULARY,
        candidate_retriever=V5CandidateRetriever(
            vocabulary=VOCABULARY,
            embeddings=embeddings,
        ),
    )
    result = analyzer._analyze_sync_for_test(
        user_text="它没有呕吐",
        turn_context=_turn_context("v5-analyze"),
        variant="v5_t1",
    )
    assert qwen.call_count == 3
    assert result.model_call_count == 3
    assert result.claims[0].canonical is not None
    assert result.claims[0].canonical.canonical_id == "vomiting"
    assert result.claims[0].state.projection_state.value == "ready"

    zero_analyzer = InputPreprocessingV5Analyzer(
        qwen=OnePathV5QwenClient(),
        vocabulary=VOCABULARY,
        candidate_retriever=V5CandidateRetriever(
            vocabulary=VOCABULARY,
            embeddings=ZeroEmbeddingClient(),
        ),
    )
    unmapped = zero_analyzer._analyze_sync_for_test(
        user_text="它最近出现ZZZ表现",
        turn_context=_turn_context("v5-unmapped"),
        variant="v5_t1",
    )
    assert unmapped.claims[0].canonical is not None
    assert unmapped.claims[0].canonical.canonical_id is None
    assert unmapped.claims[0].canonical.review_required is True
    assert unmapped.claims[0].state.projection_state.value == "review_required"


def test_file_async_queue_is_bounded_durable_and_dead_letters_failure() -> None:
    with TemporaryDirectory() as temporary:
        queue = FileAsyncShadowQueueV5(directory=Path(temporary), max_size=2)
        snapshots = [
            AsyncShadowSnapshotV5(
                snapshot_id=f"snapshot-{index}",
                sample_id=f"sample-{index}",
                claim_id=f"claim-{index}",
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
        assert (
            queue.fail(
                second.snapshot_id,
                reason="simulated_stage_timeout",
                max_attempts=1,
            )
            == "dead_letter"
        )
        assert [item.accepted for item in submissions] == [True, True, False]
        assert submissions[-1].reason == "queue_full"
        assert len(queue.dead_letters()) == 1


def _turn_context(request_id: str) -> V5TurnContext:
    return V5TurnContext(
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        user_id="v5-test-user",
        pet_id="v5-test-pet",
        session_id="v5-test-session",
        reference_time="2026-08-26T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject={  # type: ignore[arg-type]
            "reference_id": "current_pet",
            "entity_type": V5EntityType.CURRENT_PET,
        },
    )


class OnePathV5QwenClient:
    """Return deterministic intent, thin claim, and subject enrichment."""

    def __init__(self) -> None:
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
        assert model == "qwen-plus"
        self.call_count += 1
        if response_model is V5TurnIntentRaw:
            return V5TurnIntentRaw(fact_path_required=True)
        if response_model is ThinExtractionRawOutput:
            payload = json.loads(messages[1]["content"])
            user_text = payload["user_text"]
            evidence = "它没有呕吐" if "呕吐" in user_text else user_text[-20:]
            target = "呕吐" if "呕吐" in user_text else user_text[-10:]
            return ThinExtractionRawOutput(
                claims=[
                    ThinUserClaimRaw(
                        claim_id="claim-1",
                        evidence_quote=evidence,
                        target_quote=target,
                        user_statement_type=(
                            V5UserStatementType.DENIES
                            if "没有" in user_text
                            else V5UserStatementType.REPORTS
                        ),
                        coarse_type=V5CoarseType.SYMPTOM,
                        confidence=0.99,
                    )
                ]
            )
        # The remaining V5 model call on this one-claim path is subject enrichment.
        from vet_agent.input_preprocessing.v5_contracts import (
            V5SubjectEnrichmentRaw,
        )

        return V5SubjectEnrichmentRaw(
            subject_reference="current_pet",
            resolution_method=V5ResolutionMethod.TRUSTED_CURRENT_PET,
            resolution_status=V5ResolutionStatus.RESOLVED,
            confidence=0.99,
        )


class AliasEmbeddingClient(V5EmbeddingClient):
    """Deterministic exact-alias embeddings for local tests."""

    def __init__(self, vocabulary: CanonicalVocabulary) -> None:
        self.aliases = [alias for term in vocabulary.terms for alias in term.aliases]
        self.index = {alias: position for position, alias in enumerate(self.aliases)}

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * len(self.aliases)
        if text in self.index:
            vector[self.index[text]] = 1.0
        return vector


class ZeroEmbeddingClient(V5EmbeddingClient):
    """Embedding client that recalls no candidate."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]
