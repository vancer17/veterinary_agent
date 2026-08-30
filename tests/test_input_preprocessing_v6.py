"""Deterministic V6 thin-claim, batch planner, and runner tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from vet_agent.input_preprocessing.v6_analyzer import InputPreprocessingV6Analyzer
from vet_agent.input_preprocessing.v6_canonical_linker import (
    V6CandidateRetriever,
    V6EmbeddingClient,
)
from vet_agent.input_preprocessing.v6_contracts import (
    ThinExtractionRawOutput,
    ThinUserClaimRaw,
    V6ClaimRelation,
    V6CoarseType,
    V6EntityType,
    V6ResolutionMethod,
    V6ResolutionStatus,
    V6SubjectBatchRawOutput,
    V6SubjectEnrichmentRaw,
    V6TurnContext,
    V6TurnIntentRaw,
    V6UserStatementType,
)
from vet_agent.input_preprocessing.v6_deterministic_parsers import (
    parse_measurement,
    parse_temporal,
)
from vet_agent.input_preprocessing.v6_experiments import (
    AsyncShadowBatchSnapshotV6,
    FileAsyncShadowQueueV6,
    V6ArchitectureValidationRunner,
    load_v6_experiment_document,
)
from vet_agent.input_preprocessing.v6_quote_governance import (
    normalize_quote_text,
    resolve_thin_claim_quotes,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

VOCABULARY = CanonicalVocabulary.load(
    Path("assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json")
)


def test_sixth_round_development_ideal_control_passes() -> None:
    document = load_v6_experiment_document(
        Path("tests/fixtures/input_preprocessing/sixth_round_thin_shadow_matrix.json")
    )
    runner = V6ArchitectureValidationRunner(
        document=document,
        vocabulary=VOCABULARY,
    )
    reports = runner._run_all_ideal_for_test()
    assert [item["status"] for item in reports] == ["passed"] * len(reports)
    assert len(reports) == 24


def test_sixth_round_held_out_ideal_control_passes() -> None:
    held_out = Path(
        "tests/fixtures/input_preprocessing/sixth_round_thin_held_out_matrix.json"
    )
    if not held_out.is_file():
        pytest.skip("restricted held-out fixture is not published")
    document = load_v6_experiment_document(
        held_out
    )
    runner = V6ArchitectureValidationRunner(
        document=document,
        vocabulary=VOCABULARY,
    )
    reports = runner._run_all_ideal_for_test()
    assert [item["status"] for item in reports] == ["passed"]


def test_conservative_quote_normalization_and_anchor_governance() -> None:
    assert normalize_quote_text("没有呕吐，　干呕。") == "没有呕吐,干呕."
    raw = ThinUserClaimRaw(
        claim_id="claim-1",
        evidence_quote="它没有呕吐",
        target_quote="呕吐",
        user_statement_type=V6UserStatementType.DENIES,
        coarse_type=V6CoarseType.SYMPTOM,
        subject_evidence_quote="它没有呕吐",
        confidence=0.99,
    )
    quotes = resolve_thin_claim_quotes(
        user_text="它没有呕吐；精神正常。",
        raw=raw,
    )
    assert quotes.evidence.status == "resolved"
    assert quotes.target.status == "resolved"
    assert quotes.subject_evidence is not None
    assert quotes.subject_evidence.status == "resolved"


def test_deterministic_temporal_and_measurement_parsers() -> None:
    assert parse_temporal(
        temporal_quote="前天",
        relation_quote="前天开始",
    ) == (
        "started_at",
        "day-2",
        "day",
        "normalized",
        None,
    )
    assert parse_temporal(temporal_quote="这两天") == (
        "duration",
        "recent-2-days-approximate",
        "approximate_duration",
        "normalized",
        None,
    )
    assert parse_measurement(measurement_quote="一天一次") == (
        "1/day",
        "day",
        "frequency",
        "frequency",
        "normalized",
        None,
    )
    assert parse_measurement(measurement_quote="一小把")[4] == "unresolved"


def test_v6_analyzer_uses_batched_candidate_only_subject_enrichment() -> None:
    analyzer = InputPreprocessingV6Analyzer(
        qwen=OnePathV6QwenClient(),
        vocabulary=VOCABULARY,
        candidate_retriever=V6CandidateRetriever(
            vocabulary=VOCABULARY,
            embeddings=AliasEmbeddingClient(VOCABULARY),
        ),
    )
    result = analyzer._analyze_sync_for_test(
        user_text="它没有呕吐",
        turn_context=_turn_context("v6-one-path"),
        variant="v6_t1",
    )
    assert result.model_call_count == 3
    assert result.batch_count == 2
    assert result.claims[0].subject is not None
    assert result.claims[0].subject.subject.reference_id == "current_pet"
    assert result.claims[0].canonical is not None
    assert result.claims[0].canonical.canonical_id == "vomiting"
    assert result.claims[0].state.projection_state.value == "ready"


def test_v6_analyzer_does_not_confirm_without_candidate() -> None:
    analyzer = InputPreprocessingV6Analyzer(
        qwen=OnePathV6QwenClient(),
        vocabulary=VOCABULARY,
        candidate_retriever=V6CandidateRetriever(
            vocabulary=VOCABULARY,
            embeddings=ZeroEmbeddingClient(),
        ),
    )
    result = analyzer._analyze_sync_for_test(
        user_text="它最近出现ZZZ表现",
        turn_context=_turn_context("v6-unmapped"),
        variant="v6_t1",
    )
    assert result.claims[0].canonical is not None
    assert result.claims[0].canonical.canonical_id is None
    assert result.claims[0].canonical.review_required is True
    assert result.claims[0].canonical.diagnostic.value != "not_applicable"
    assert result.claims[0].state.projection_state.value == "review_required"


def test_file_async_queue_is_bounded_durable_and_dead_letters_failure() -> None:
    with TemporaryDirectory() as temporary:
        queue = FileAsyncShadowQueueV6(directory=Path(temporary), max_size=2)
        snapshots = [
            AsyncShadowBatchSnapshotV6(
                snapshot_id=f"snapshot-{index}",
                sample_id=f"sample-{index}",
                batch_id=f"batch-{index}",
                claim_ids=(f"claim-{index}",),
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


def _turn_context(request_id: str) -> V6TurnContext:
    return V6TurnContext(
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        user_id="v6-test-user",
        pet_id="v6-test-pet",
        session_id="v6-test-session",
        reference_time="2026-08-26T10:00:00+08:00",  # type: ignore[arg-type]
        current_pet_subject={  # type: ignore[arg-type]
            "reference_id": "current_pet",
            "entity_type": V6EntityType.CURRENT_PET,
        },
    )


class OnePathV6QwenClient:
    """Return deterministic intent, thin claim, and subject batch output."""

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
        if response_model is V6TurnIntentRaw:
            return V6TurnIntentRaw(
                fact_statement_present=True,
                answer_now_evidence_quote="",
                wants_triage_evidence_quote="",
                correction_evidence_quote="",
                clarification_request_evidence_quote="",
                confidence=0.99,
            )
        if response_model is ThinExtractionRawOutput:
            payload = json.loads(messages[1]["content"])
            user_text = payload["user_text"]
            unmapped = "ZZZ表现" in user_text
            evidence = "它最近出现ZZZ表现" if unmapped else "它没有呕吐"
            target = "ZZZ表现" if unmapped else "呕吐"
            return ThinExtractionRawOutput(
                claims=[
                    ThinUserClaimRaw(
                        claim_id="claim-1",
                        evidence_quote=evidence,
                        target_quote=target,
                        user_statement_type=(
                            V6UserStatementType.REPORTS
                            if unmapped
                            else V6UserStatementType.DENIES
                        ),
                        coarse_type=V6CoarseType.SYMPTOM,
                        relation=V6ClaimRelation.ABSOLUTE_STATUS,
                        subject_evidence_quote=evidence,
                        confidence=0.99,
                    )
                ]
            )
        if response_model is V6SubjectBatchRawOutput:
            return V6SubjectBatchRawOutput(
                results=[
                    V6SubjectEnrichmentRaw(
                        claim_id="claim-1",
                        selected_subject_candidate="current_pet",
                        resolution_method=V6ResolutionMethod.TRUSTED_CURRENT_PET,
                        resolution_status=V6ResolutionStatus.RESOLVED,
                        confidence=0.99,
                    )
                ]
            )
        raise AssertionError(f"unexpected V6 response model: {response_model.__name__}")


class AliasEmbeddingClient(V6EmbeddingClient):
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


class ZeroEmbeddingClient(V6EmbeddingClient):
    """Embedding client that recalls no candidate."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]
