"""Tests for the ninth-round V8 attribution diagnostics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from vet_agent.input_preprocessing.v8_contracts import (
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8SpanCandidate,
)
from vet_agent.input_preprocessing.v8_experiments import _label_for_role, load_v8_matrix
from vet_agent.input_preprocessing.v8_span_extractors import V8GlinerSpanExtractor
from vet_agent.input_preprocessing.v9_attribution import (
    audit_v9_gold_integrity,
    build_v9_ideal_span_pool,
    evaluate_v9_span_pool,
    gold_integrity_report,
    run_v9_adapter_cold,
    run_v9_canonical_gold,
    run_v9_macro_attribution,
    run_v9_participant_gold,
    run_v9_relation_gold,
    run_v9_repeat_attribution,
)
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

MATRIX = Path("tests/fixtures/input_preprocessing/eighth_round_span_macro_matrix.json")
VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)


def test_gold_integrity_finds_owner_occurrence_and_label_conflict_risks() -> None:
    matrix = load_v8_matrix(MATRIX)
    report = gold_integrity_report(matrix)
    assert report["metrics"]["required_field_count"] == 80
    assert report["metrics"]["wrong_occurrence_count"] == 2
    assert report["metrics"]["support_containment_violation_count"] == 0
    assert report["metrics"]["conflicting_label_boundary_count"] == 5
    assert report["metrics"]["label_evaluable_rate"] < 1.0

    integrity = audit_v9_gold_integrity(matrix)
    assert integrity.role_counts["subject_quote"] == 4
    assert any(
        item["code"] == "global_first_occurrence_is_not_owner_scoped"
        for item in report["findings"]
    )


def test_opaque_owner_scoped_gold_pool_does_not_leak_role_names() -> None:
    matrix = load_v8_matrix(MATRIX)
    unit = next(
        item for item in matrix["macro_units"] if item["unit_id"] == "macro-action-roles"
    )
    pool = build_v9_ideal_span_pool(unit, id_mode="opaque")
    assert len(pool.spans) == len({span.span_id for span in pool.spans})
    assert all("recipient" not in span.span_id for span in pool.spans)
    assert all("target" not in span.span_id for span in pool.spans)
    assert len(pool.role_span_ids) == 19


def test_gliner_label_modes_use_generic_multilingual_prompts() -> None:
    extractor = object.__new__(V8GlinerSpanExtractor)
    object.__setattr__(extractor, "label_profile", "staged")
    object.__setattr__(extractor, "label_mode", "bilingual")
    groups = extractor._label_groups
    assert [name for name, _ in groups] == ["core", "participant", "discourse"]
    assert "目标现象或事物 target mention" in groups[0][1]
    assert "动作承受者 recipient mention" in groups[1][1]
    assert extractor._canonical_label("状态表述 state mention").value == "state_mention"


class _ExactGoldExtractor:
    extractor_version = "test-exact-gold"

    def __init__(self, matrix: dict[str, Any]) -> None:
        self.by_text = {
            str(unit["user_text"]): unit for unit in matrix["macro_units"]
        }

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]:
        assert text in self.by_text
        integrity = audit_v9_gold_integrity(
            {"macro_units": [self.by_text[text]]}
        )
        return [
            V8SpanCandidate(
                span_id=f"{field.unit_id}:test:{index}",
                source_id=field.unit_id,
                source_block_id=source_block_id,
                start=field.start,
                end=field.end,
                text=field.quote,
                label=field.label,
                score=0.9,
                extractor_version=self.extractor_version,
            )
            for index, field in enumerate(integrity.fields, start=1)
        ]


def test_span_attribution_separates_boundary_and_label_errors() -> None:
    matrix = load_v8_matrix(MATRIX)
    report = evaluate_v9_span_pool(
        matrix,
        extractor=_ExactGoldExtractor(matrix),
    )
    assert report["metrics"]["boundary_recall"] == 1.0
    assert report["metrics"]["label_accuracy_on_exact"] == 1.0
    assert report["metrics"]["span_intake_error_count"] == 0
    assert report["metrics"]["label_conflict_field_count"] == 11
    assert all(
        item["attribution"] == "correct" for item in report["field_results"]
    )


class _ExpectedV9Client:
    adapter_name = "response_format"
    internal_retry_limit = 0

    def __init__(self, matrix: dict[str, Any], *, use_role_ids: bool = False) -> None:
        self.by_text = {str(unit["user_text"]): unit for unit in matrix["macro_units"]}
        self.use_role_ids = use_role_ids
        self.calls = 0

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str,
    ) -> Any:
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        unit = self.by_text[payload["user_text"]]
        pool = build_v9_ideal_span_pool(
            unit,
            id_mode="role-hinted" if self.use_role_ids else "opaque",
        )
        return _expected_output(unit, pool)


def _expected_output(
    unit: dict[str, Any],
    pool: Any,
) -> V8MacroSemanticRawOutput:
    def span_id(
        *,
        owner: str,
        role: str,
        quote: str,
        coarse_type: str = "",
        act_type: str = "",
    ) -> str:
        label = _label_for_role(role, coarse_type=coarse_type, act_type=act_type)
        matches = [
            span
            for span in pool.spans
            if span.text == quote and span.label is label
        ]
        assert matches, f"missing test span:{role}:{quote}"
        return matches[0].span_id

    return V8MacroSemanticRawOutput(
        acts=[
            V8MacroDiscourseActRaw(
                unit_id=str(unit["unit_id"]),
                act_type=act["act_type"],
                evidence_span_ids=[
                    span_id(
                        owner=f"act-{index}",
                        role="evidence_quote",
                        quote=str(act["evidence_quote"]),
                        act_type=str(act["act_type"]),
                    )
                ],
                confidence=1.0,
            )
            for index, act in enumerate(unit.get("expected_acts", []))
        ],
        claims=[
            V8MacroClaimRaw(
                unit_id=str(unit["unit_id"]),
                claim_id=str(claim["claim_id"]),
                statement_type=str(claim["statement_type"]),
                coarse_type=str(claim["coarse_type"]),
                support_span_ids=[
                    span_id(
                        owner=str(claim["claim_id"]),
                        role="support_quote",
                        quote=str(claim["support_quote"]),
                        coarse_type=str(claim["coarse_type"]),
                    )
                ],
                target_span_ids=[
                    span_id(
                        owner=str(claim["claim_id"]),
                        role="target_quote",
                        quote=str(claim["target_quote"]),
                        coarse_type=str(claim["coarse_type"]),
                    )
                ],
                relation_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="relation_quote",
                            quote=str(claim["relation_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("relation_quote")
                    else []
                ),
                subject_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="subject_quote",
                            quote=str(claim["subject_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("subject_quote")
                    else []
                ),
                action_agent_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="action_agent_quote",
                            quote=str(claim["action_agent_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("action_agent_quote")
                    else []
                ),
                action_recipient_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="action_recipient_quote",
                            quote=str(claim["action_recipient_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("action_recipient_quote")
                    else []
                ),
                experiencer_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="experiencer_quote",
                            quote=str(claim["experiencer_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("experiencer_quote")
                    else []
                ),
                object_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="object_quote",
                            quote=str(claim["object_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("object_quote")
                    else []
                ),
                temporal_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="temporal_quote",
                            quote=str(claim["temporal_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("temporal_quote")
                    else []
                ),
                measurement_span_ids=(
                    [
                        span_id(
                            owner=str(claim["claim_id"]),
                            role="measurement_quote",
                            quote=str(claim["measurement_quote"]),
                            coarse_type=str(claim["coarse_type"]),
                        )
                    ]
                    if claim.get("measurement_quote")
                    else []
                ),
                confidence=1.0,
            )
            for claim in unit.get("expected_claims", [])
        ],
    )


def test_macro_attribution_uses_one_call_per_unit_and_passes_gold_control() -> None:
    matrix = load_v8_matrix(MATRIX)
    client = _ExpectedV9Client(matrix)
    report = asyncio.run(
        run_v9_macro_attribution(
            matrix=matrix,
            adapter="base",
            id_mode="opaque",
            client_factory=lambda _: client,
            unit_ids=["macro-action-roles"],
        )
    )
    assert client.calls == 1
    assert report["span_pool"]["id_mode"] == "opaque"
    assert report["metrics"]["total_matched_claim_count"] == 3
    assert report["metrics"]["mean_participant_mention_recall"] == 1.0
    assert report["metrics"]["total_invalid_span_binding_count"] == 0
    assert report["unit_results"][0]["raw_output"]["claims"][0][
        "support_span_ids"
    ]


def test_gold_injection_isolates_relation_canonical_and_participant() -> None:
    matrix = load_v8_matrix(MATRIX)
    relation = asyncio.run(
        run_v9_relation_gold(matrix=matrix, mode="quick", cache_path=None)
    )
    canonical = run_v9_canonical_gold(
        matrix=matrix,
        vocabulary=CanonicalVocabulary.load(VOCABULARY),
        mode="quick",
    )
    participant = run_v9_participant_gold(matrix)
    assert relation["metrics"]["gold_relation_field_count"] == 6
    assert relation["metrics"]["gold_relation_span_missing_count"] == 2
    assert relation["metrics"]["relation_input_availability"] == pytest.approx(
        4 / 6
    )
    assert relation["metrics"]["relation_accuracy"] == 1.0
    assert canonical["metrics"]["candidate_recall"] == 1.0
    assert participant["metrics"]["participant_resolution_accuracy"] == 1.0
    assert participant["metrics"]["resolved_empty_count"] == 0


class _AlternatingClient:
    adapter_name = "response_format"
    internal_retry_limit = 0

    def __init__(self, matrix: dict[str, Any]) -> None:
        self.matrix = matrix
        self.calls = 0

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str,
    ) -> Any:
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        unit = next(
            item
            for item in self.matrix["macro_units"]
            if str(item["user_text"]) == payload["user_text"]
        )
        pool = build_v9_ideal_span_pool(unit, id_mode="opaque")
        output = _expected_output(unit, pool)
        if self.calls % 2 == 0:
            output = output.model_copy(update={"acts": []})
        return output


def test_repeat_attribution_separates_act_and_claim_stability() -> None:
    matrix = load_v8_matrix(MATRIX)
    client = _AlternatingClient(matrix)
    report = asyncio.run(
        run_v9_repeat_attribution(
            matrix=matrix,
            unit_id="macro-answer-fact",
            adapter="base",
            run_count=3,
            client_factory=lambda _: client,
        )
    )
    assert report["metrics"]["cold_run_count"] == 3
    assert report["metrics"]["cache_hit_count"] == 0
    assert report["metrics"]["claim_signature_stability"] == 1.0
    assert report["metrics"]["act_signature_stability"] < 1.0
    assert report["metrics"]["raw_output_stability"] < 1.0


def test_adapter_cold_uses_same_gold_input_for_each_adapter() -> None:
    matrix = load_v8_matrix(MATRIX)
    client = _AlternatingClient(matrix)
    report = asyncio.run(
        run_v9_adapter_cold(
            matrix=matrix,
            adapters=["base", "instructor"],
            unit_id="macro-answer-fact",
            run_count=2,
            client_factory=lambda _: client,
        )
    )
    assert report["metrics"]["adapter_count"] == 2
    assert report["metrics"]["cold_run_count"] == 4
    assert report["metrics"]["cache_hit_count"] == 0
    assert [item["adapter"] for item in report["adapter_results"]] == [
        "base",
        "instructor",
    ]
