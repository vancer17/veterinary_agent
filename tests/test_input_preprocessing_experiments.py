"""Tests for the remaining-risk shadow experiment matrix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from vet_agent.input_preprocessing.experiments import (
    AsyncShadowTurnSnapshot,
    InMemoryAsyncShadowQueue,
    ShadowExperimentRunner,
    audit_vocabulary_static,
    compare_clinical_safety_structures,
    load_experiment_document,
)
from vet_agent.input_preprocessing.projection import ClinicalSafetyShadowProjection
from vet_agent.input_preprocessing.vocabulary import CanonicalVocabulary

ROOT = Path(__file__).parent.parent
MATRIX_PATH = ROOT / "tests/fixtures/input_preprocessing/unproven_shadow_matrix.json"
VOCABULARY_PATH = (
    ROOT / "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
)


def test_unproven_experiment_matrix_passes_in_ideal_control_mode() -> None:
    """Verify every remaining-risk experiment has a valid ideal control path."""

    runner = ShadowExperimentRunner(
        document=load_experiment_document(MATRIX_PATH),
        vocabulary=CanonicalVocabulary.load(VOCABULARY_PATH),
        policy="local",
        vocabulary_audit=audit_vocabulary_static(
            CanonicalVocabulary.load(VOCABULARY_PATH)
        ),
    )
    reports = asyncio.run(runner.run(mode="ideal"))

    assert len(reports) == 9
    assert all(report["status"] == "passed" for report in reports)


def test_negative_mapping_experiment_reports_expected_gate_blocks() -> None:
    """Verify not_found and invented concepts are successful Fail-Fast cases."""

    document = load_experiment_document(MATRIX_PATH)
    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    runner = ShadowExperimentRunner(
        document=document,
        vocabulary=vocabulary,
        policy="local",
    )
    reports = asyncio.run(
        runner.run(
            mode="ideal",
            only_experiment_ids={"N_invalid_canonical_fail_fast"},
        )
    )
    turn_statuses = [
        turn["status"] for report in reports[0]["reports"] for turn in report["turns"]
    ]

    assert reports[0]["status"] == "passed"
    assert turn_statuses == ["gate_blocked_as_expected"] * 4


def test_answer_now_only_branch_changes_decision_without_inventing_facts() -> None:
    """Verify control-intent consumption is isolated from medical fact projection."""

    document = load_experiment_document(MATRIX_PATH)
    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    runner = ShadowExperimentRunner(
        document=document,
        vocabulary=vocabulary,
        policy="local",
    )
    reports = asyncio.run(
        runner.run(
            mode="ideal",
            only_experiment_ids={"B_consultation_decision_branches"},
        )
    )
    branches = next(
        turn["behavior_simulation"]["branches"]
        for report in reports[0]["reports"]
        if report["sample_id"] == "C_answer_now_branch_control"
        for turn in report["turns"]
    )

    assert reports[0]["status"] == "passed"
    assert branches["baseline_empty_success"]["action"] == "ask"
    assert branches["answer_now_only"]["action"] == "answer"


def test_clinical_safety_comparison_is_report_only() -> None:
    """Verify clinical comparison observes structure without medical policy input."""

    baseline = SimpleNamespace(
        strategy="litellm_response_format",
        observed_features=[
            SimpleNamespace(normalized_text="大便偏软", state="present")
        ],
        negated_terms=("呕吐",),
    )
    projection = ClinicalSafetyShadowProjection(
        status="projected",
        current_pet_symptoms=[{"canonical_id": "soft_stool"}],
        denied_evidence=[{"canonical_id": "vomiting"}],
        normal_evidence=[],
        excluded_evidence=[],
    )
    comparison = compare_clinical_safety_structures(baseline, projection)

    assert comparison["comparison_status"] == "report_only"
    assert comparison["baseline_present_count"] == 1
    assert comparison["new_present_count"] == 1
    assert comparison["denied_coverage_gap"] is False
    assert comparison["downstream_evaluation"] == "not_implemented"


def test_async_shadow_queue_is_bounded_and_nonblocking() -> None:
    """Verify API shadow scaffolding can reject overflow without failing a turn."""

    queue = InMemoryAsyncShadowQueue(max_size=1)
    first = AsyncShadowTurnSnapshot(
        request_id="r1",
        trace_id="t1",
        user_id="u1",
        pet_id="p1",
        session_id="s1",
        task_key="__default__",
        user_text="文本",
        turn_context={},
    )
    second = AsyncShadowTurnSnapshot(
        request_id="r2",
        trace_id="t2",
        user_id="u1",
        pet_id="p1",
        session_id="s1",
        task_key="__default__",
        user_text="文本2",
        turn_context={},
    )

    assert queue.submit(first) is True
    assert queue.submit(second) is False
    assert queue.pop() is first
    assert queue.pop() is None


def test_static_vocabulary_audit_reports_duplicate_aliases() -> None:
    """Verify alias collisions are observable governance inputs."""

    vocabulary = CanonicalVocabulary.load(VOCABULARY_PATH)
    duplicated = vocabulary.terms[0].model_copy(
        update={"aliases": [*vocabulary.terms[0].aliases, "重复别名"]}
    )
    vocabulary = vocabulary.model_copy(
        update={
            "terms": [
                duplicated,
                vocabulary.terms[1].model_copy(
                    update={"aliases": [*vocabulary.terms[1].aliases, "重复别名"]}
                ),
                *vocabulary.terms[2:],
            ]
        }
    )
    audit = audit_vocabulary_static(vocabulary)

    assert audit["term_count"] == len(vocabulary.terms)
    assert audit["alias_count"] > audit["unique_alias_count"]
    assert audit["review_required"] is True
