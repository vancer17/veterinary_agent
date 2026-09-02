"""Contract and governance tests for the staged V8 quick-validation runner."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from vet_agent.input_preprocessing.v7_run_cache import V7RunCache
from vet_agent.input_preprocessing.v8_contracts import (
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8SpanCandidate,
    V8SpanLabel,
)
from vet_agent.input_preprocessing.v8_experiments import (
    build_ideal_span_pool,
    load_v8_matrix,
    prepare_macro_units,
    run_async_isolation,
    run_canonical_live,
    run_macro_suite,
    run_negative_mutations,
    run_relation_live,
    run_winner_integration,
)
from vet_agent.input_preprocessing.v8_macro_analyzer import V8MacroAnalyzer
from vet_agent.input_preprocessing.v8_span_extractors import V8GlinerSpanExtractor
from vet_agent.input_preprocessing.v8_span_governance import (
    V8SpanGovernance,
    V8SpanPool,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

MATRIX = Path("tests/fixtures/input_preprocessing/eighth_round_span_macro_matrix.json")


def _pool() -> tuple[V8SpanGovernance, V8SpanPool, str, str, str, str]:
    source_id = "v8-test"
    text = "猫前天呕吐两天，一天两次。"
    support = f"{source_id}:support"
    target = f"{source_id}:target"
    temporal = f"{source_id}:temporal"
    outside = f"{source_id}:outside"
    spans = [
        V8SpanCandidate(
            span_id=support,
            source_id=source_id,
            source_block_id="block-001",
            start=0,
            end=8,
            text=text[0:8],
            label=V8SpanLabel.STATE_MENTION,
            score=1.0,
            extractor_version="test",
        ),
        V8SpanCandidate(
            span_id=target,
            source_id=source_id,
            source_block_id="block-001",
            start=3,
            end=5,
            text=text[3:5],
            label=V8SpanLabel.TARGET_MENTION,
            score=1.0,
            extractor_version="test",
        ),
        V8SpanCandidate(
            span_id=temporal,
            source_id=source_id,
            source_block_id="block-001",
            start=1,
            end=3,
            text=text[1:3],
            label=V8SpanLabel.TEMPORAL_EXPRESSION,
            score=1.0,
            extractor_version="test",
        ),
        V8SpanCandidate(
            span_id=outside,
            source_id=source_id,
            source_block_id="block-001",
            start=9,
            end=12,
            text=text[9:12],
            label=V8SpanLabel.MEASUREMENT_EXPRESSION,
            score=1.0,
            extractor_version="test",
        ),
    ]
    return (
        V8SpanGovernance(V8SpanPool(sources={source_id: text}, spans=spans)),
        V8SpanPool(sources={source_id: text}, spans=spans),
        support,
        target,
        temporal,
        outside,
    )


def _raw_output(
    *,
    support: str,
    target: str,
    temporal: str = "",
) -> V8MacroSemanticRawOutput:
    return V8MacroSemanticRawOutput(
        acts=[
            V8MacroDiscourseActRaw(
                unit_id="u1",
                act_type="fact_statement",
                evidence_span_ids=[support],
                confidence=1.0,
            )
        ],
        claims=[
            V8MacroClaimRaw(
                unit_id="u1",
                claim_id="c1",
                statement_type="reports",
                coarse_type="symptom",
                support_span_ids=[support],
                target_span_ids=[target],
                temporal_span_ids=[temporal] if temporal else [],
                confidence=1.0,
            )
        ],
    )


def test_v8_governance_resolves_quotes_and_accepts_valid_claim() -> None:
    governance, _, support, target, temporal, _ = _pool()
    result = governance.govern(
        _raw_output(support=support, target=target, temporal=temporal)
    )
    claim = result.governed_claims[0]
    assert claim.support.quote == "猫前天呕吐两天，"
    assert claim.target.quote == "呕吐"
    assert claim.temporal is not None and claim.temporal.quote == "前天"
    assert claim.projection_ready is True
    assert result.invalid_span_references == []


def test_v8_governance_blocks_claim_support_reference_without_false_attribution() -> (
    None
):
    governance, _, _, target, _, _ = _pool()
    result = governance.govern(_raw_output(support="missing-support", target=target))
    assert result.invalid_span_references == ["missing-support"]
    assert result.governed_claims == []
    gate = next(
        gate for gate in result.gates if gate.gate_id == "v8_claim_span_reference"
    )
    assert gate.status == "failed"
    assert gate.severity == "blocking"
    assert gate.reason_code == "invalid_span_reference"
    assert gate.evidence_refs == ["missing-support"]


def test_v8_governance_blocks_invalid_optional_reference() -> None:
    governance, _, support, target, _, _ = _pool()
    output = _raw_output(support=support, target=target, temporal="missing-temporal")
    result = governance.govern(output)
    assert "missing-temporal" in result.invalid_span_references
    assert any(
        gate.gate_id == "v8_claim_span_reference"
        and gate.status == "failed"
        and gate.severity == "blocking"
        for gate in result.gates
    )


def test_v8_governance_distinguishes_missing_reference_from_invalid_binding() -> None:
    governance, _, support, target, _, outside = _pool()
    output = _raw_output(support=support, target=target)
    output.acts[0].evidence_span_ids = [support, outside]
    result = governance.govern(output)
    assert result.invalid_span_references == []
    assert set(result.invalid_span_bindings) == {support, outside}
    gate = next(gate for gate in result.gates if gate.gate_id == "v8_span_binding")
    assert gate.status == "failed"
    assert gate.severity == "blocking"
    assert gate.reason_code == "invalid_span_binding"


def test_v8_governance_blocks_target_outside_support() -> None:
    governance, _, support, _, _, outside = _pool()
    result = governance.govern(_raw_output(support=support, target=outside))
    assert result.governed_claims[0].projection_ready is False
    assert any(
        gate.gate_id == "v8_target_containment"
        and gate.status == "failed"
        and gate.severity == "blocking"
        for gate in result.gates
    )


def test_v8_governance_rejects_free_quote_field() -> None:
    _, _, support, target, _, _ = _pool()
    payload = _raw_output(support=support, target=target).model_dump(mode="json")
    payload["claims"][0]["support_quote"] = "自由引述"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        V8MacroSemanticRawOutput.model_validate(payload)


def test_v8_negative_mutations_all_block_as_expected() -> None:
    report = run_negative_mutations(load_v8_matrix(MATRIX))
    assert report["metrics"]["false_pass"] == 0
    assert report["metrics"]["model_free_quote_output"] == 0
    mutations = {item["mutation"]: item for item in report["mutations"]}
    assert mutations["invalid-claim-support"]["invalid_span_references"] == [
        "missing-support-span"
    ]
    assert mutations["target-outside-support"]["projection_ready_count"] == 0


def test_v8_ideal_pool_is_offset_backed() -> None:
    matrix = load_v8_matrix(MATRIX)
    pool = build_ideal_span_pool(matrix["macro_units"][0])
    assert pool.spans
    assert all(pool.text[span.start : span.end] == span.text for span in pool.spans)
    assert pool.role_span_ids["support_quote:0"]
    assert pool.role_span_ids["target_quote:0"]


class FixedV8StructuredClient:
    internal_retry_limit = 0

    def __init__(self, adapter_name: str) -> None:
        self.adapter_name = adapter_name
        self.call_count = 0

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str,
    ) -> Any:
        self.call_count += 1
        return V8MacroSemanticRawOutput()


def test_v8_gliner_staged_profile_uses_bounded_label_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGlinerModel:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def predict_entities(
            self,
            text: str,
            labels: list[str],
            threshold: float,
        ) -> list[dict[str, Any]]:
            self.calls.append(tuple(labels))
            if labels == [
                "target_mention",
                "state_mention",
                "action_event",
                "temporal_expression",
                "measurement_expression",
                "relation_expression",
            ]:
                return [{"start": 0, "end": 1, "label": "target_mention", "score": 0.9}]
            if labels == [
                "agent_mention",
                "recipient_mention",
                "subject_mention",
                "object_mention",
            ]:
                return [
                    {"start": 0, "end": 2, "label": "subject_mention", "score": 0.8}
                ]
            return [
                {"start": 2, "end": 4, "label": "question_expression", "score": 0.7}
            ]

    fake_model = FakeGlinerModel()
    fake_module = ModuleType("gliner")
    fake_module.GLiNER = SimpleNamespace(from_pretrained=lambda _: fake_model)
    monkeypatch.setitem(sys.modules, "gliner", fake_module)

    extractor = V8GlinerSpanExtractor(
        model_name="fake-gliner",
        label_profile="staged",
        threshold=0.5,
        model_revision="fake-revision",
    )
    spans = extractor.extract(
        source_id="fake-source",
        source_block_id="block-001",
        text="猫呕吐两天",
    )

    assert extractor.extractor_version == (
        "v8-gliner-adapter-20260828-2:staged:threshold-0.500:english:fake-revision"
    )
    assert len(fake_model.calls) == 3
    assert [span.label for span in spans] == [
        V8SpanLabel.TARGET_MENTION,
        V8SpanLabel.SUBJECT_MENTION,
        V8SpanLabel.QUESTION_EXPRESSION,
    ]
    assert [span.text for span in spans] == ["猫", "猫呕", "吐两"]
    assert [span.span_id for span in spans] == [
        "fake-source:gliner-core-000001",
        "fake-source:gliner-participant-000001",
        "fake-source:gliner-discourse-000001",
    ]


def test_v8_run_cache_isolates_adapters() -> None:
    with TemporaryDirectory() as temporary:
        cache = V7RunCache(Path(temporary) / "run-cache.json")
        base = FixedV8StructuredClient("response_format")
        instructor = FixedV8StructuredClient("instructor")
        arguments = {
            "experiment_id": "STRUCT-SMOKE",
            "user_text": "猫呕吐两天",
            "spans": [],
            "turn_context": {"unit_id": "unit-1"},
        }
        base_execution = asyncio.run(
            V8MacroAnalyzer(client=base, cache=cache).run(**arguments)
        )
        instructor_execution = asyncio.run(
            V8MacroAnalyzer(client=instructor, cache=cache).run(**arguments)
        )
        base_cached_execution = asyncio.run(
            V8MacroAnalyzer(client=base, cache=cache).run(**arguments)
        )
        assert base.call_count == 1
        assert instructor.call_count == 1
        assert base_execution.cache_hit is False
        assert instructor_execution.cache_hit is False
        assert base_cached_execution.cache_hit is True
        assert base_execution.model_call_count == 1
        assert instructor_execution.model_call_count == 1
    assert base_cached_execution.model_call_count == 0


class _ExpectedOutputV8Client:
    adapter_name = "response_format"
    internal_retry_limit = 0

    def __init__(self, matrix: dict[str, Any]) -> None:
        self.by_text = {str(item["user_text"]): item for item in matrix["macro_units"]}

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str,
    ) -> Any:
        payload = json.loads(messages[1]["content"])
        unit = self.by_text[payload["user_text"]]
        pool = build_ideal_span_pool(unit)
        return V8MacroSemanticRawOutput(
            acts=[
                V8MacroDiscourseActRaw(
                    unit_id=str(unit["unit_id"]),
                    act_type=act["act_type"],
                    evidence_span_ids=[pool.role_span_ids[f"evidence_quote:{index}"]],
                    confidence=1.0,
                )
                for index, act in enumerate(unit.get("expected_acts", []))
            ],
            claims=[
                V8MacroClaimRaw(
                    unit_id=str(unit["unit_id"]),
                    claim_id=str(claim["claim_id"]),
                    statement_type=claim["statement_type"],
                    coarse_type=claim["coarse_type"],
                    support_span_ids=[pool.role_span_ids[f"support_quote:{index}"]],
                    target_span_ids=[pool.role_span_ids[f"target_quote:{index}"]],
                    relation_span_ids=(
                        [pool.role_span_ids[f"relation_quote:{index}"]]
                        if claim.get("relation_quote")
                        else []
                    ),
                    subject_span_ids=(
                        [pool.role_span_ids[f"subject_quote:{index}"]]
                        if claim.get("subject_quote")
                        else []
                    ),
                    action_agent_span_ids=(
                        [pool.role_span_ids[f"action_agent_quote:{index}"]]
                        if claim.get("action_agent_quote")
                        else []
                    ),
                    action_recipient_span_ids=(
                        [pool.role_span_ids[f"action_recipient_quote:{index}"]]
                        if claim.get("action_recipient_quote")
                        else []
                    ),
                    object_span_ids=(
                        [pool.role_span_ids[f"object_quote:{index}"]]
                        if claim.get("object_quote")
                        else []
                    ),
                    temporal_span_ids=(
                        [pool.role_span_ids[f"temporal_quote:{index}"]]
                        if claim.get("temporal_quote")
                        else []
                    ),
                    measurement_span_ids=(
                        [pool.role_span_ids[f"measurement_quote:{index}"]]
                        if claim.get("measurement_quote")
                        else []
                    ),
                    confidence=1.0,
                )
                for index, claim in enumerate(unit.get("expected_claims", []))
            ],
        )


def test_v8_full_ideal_pipeline_and_winner_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    import vet_agent.input_preprocessing.v8_experiments as runner

    matrix = load_v8_matrix(MATRIX)
    monkeypatch.setattr(
        runner,
        "build_v8_structured_client",
        lambda adapter: _ExpectedOutputV8Client(matrix),
    )
    args = SimpleNamespace(unit=[], max_units=0)
    prepared = prepare_macro_units(matrix, span_source="ideal", args=args)
    suite = asyncio.run(
        run_macro_suite(
            experiment_id="V8-TEST",
            matrix=matrix,
            prepared_units=prepared,
            adapter="base",
            cache_path=None,
        )
    )
    relation = asyncio.run(run_relation_live(suite=suite, mode="quick", cache_path=None))
    vocabulary = CanonicalVocabulary.load(
        Path("assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json")
    )
    canonical = run_canonical_live(
        suite=suite,
        mode="quick",
        vocabulary=vocabulary,
    )
    winner = run_winner_integration(
        suite=suite,
        relation_report=relation,
        canonical_report=canonical,
        vocabulary=vocabulary,
    )

    assert relation["metrics"]["relation_input_availability"] > 0
    assert canonical["metrics"]["candidate_recall"] > 0
    assert winner["metrics"]["claim_count"] > 0
    assert winner["metrics"]["projection_consuming_blocked_count"] == 0


def test_v8_async_isolation_reports_queue_full_and_dead_letter() -> None:
    with TemporaryDirectory() as temporary:
        report = run_async_isolation(Path(temporary))
        assert report["status"] == "completed"
        assert report["metrics"]["queue_full_count"] == 1
        assert report["metrics"]["dead_letter_count"] == 1
        assert report["metrics"]["trace_completeness"] == 1.0
