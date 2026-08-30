"""Second-round V2 architecture validation and report-only shadow runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .runtime_helpers import make_runtime_settings
from .v2_analyzer import InputPreprocessingV2Analyzer
from .v2_contracts import (
    V2AtomicClaimSegment,
    V2CanonicalCandidate,
    V2CanonicalMappingStatus,
    V2EntityBinding,
    V2EntityType,
    V2InputAnalysisResult,
    V2ParticipantBinding,
    V2QualityGateStatus,
    V2ResolutionMethod,
    V2ResolutionStatus,
    V2SharedAssertionScopeSegment,
    V2Stage1Output,
    V2Stage2Output,
    V2SubjectReference,
    V2TurnContext,
    V2VerifiedEvidence,
)
from .v2_gates import evaluate_v2_quality_gates
from .vocabulary import CanonicalVocabulary


class V2ExperimentKind:
    """Finite string constants for the second-round matrix."""

    D2 = "d2_shared_assertion_scope"
    E2 = "e2_event_role"
    N2 = "n2_negative_contract"
    V2 = "v2_canonical_governance"
    R2 = "r2_repeat_stability"
    B2 = "b2_answer_now_branch"
    CS2 = "cs2_clinical_compare"
    AS2 = "as2_async_shadow"


class V2ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    hypothesis: str = Field(min_length=1, max_length=500)
    sample_ids: list[str] = Field(min_length=1, max_length=32)
    repeat_count: int = Field(default=1, ge=1, le=10)
    segmentation_variant: Literal["model", "expected"] = "model"
    expected_outcome: Literal[
        "semantic_exact_match",
        "gate_blocked_as_expected",
        "governance_report_only",
        "stable_output",
        "branch_behavior_match",
        "comparison_report_only",
        "async_isolation_report_only",
    ]


class V2ExpectedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_text: str = Field(min_length=1, max_length=320)
    assertion: str = Field(min_length=1, max_length=40)
    mapping_status: str = Field(min_length=1, max_length=40)
    canonical_id: str | None = Field(default=None, max_length=96)
    subject_reference: str = Field(min_length=1, max_length=64)
    subject_type: str = Field(min_length=1, max_length=40)
    participants: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    review_required: bool = False


class V2ExpectedBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_empty: Literal["ask", "answer"]
    answer_now_only: Literal["ask", "answer"]
    full_projection: Literal["ask", "answer"]


class V2ExpectedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    user_text: str = Field(min_length=1, max_length=4000)
    other_subjects: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    golden_stage1: dict[str, Any]
    expected_intent: dict[str, bool]
    expected_evidence: list[V2ExpectedEvidence] = Field(
        default_factory=list, max_length=64
    )
    expected_behavior: V2ExpectedBehavior | None = None


class V2ExperimentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"]
    reference_time: datetime
    pet_profile: dict[str, Any]
    cases: list[V2ExpectedCase] = Field(min_length=1)
    experiments: list[V2ExperimentSpec] = Field(min_length=1)
    negative_mutations: list[dict[str, str]] = Field(default_factory=list)


class V2ClinicalBaselineAgent(Protocol):
    """Minimal existing clinical-safety semantic interface."""

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        model: str,
    ) -> Any: ...


@dataclass(frozen=True)
class AsyncShadowSnapshotV2:
    """Bounded snapshot for the async API-shadow experiment."""

    snapshot_id: str
    sample_id: str
    user_text: str
    turn_context: V2TurnContext


@dataclass(frozen=True)
class AsyncShadowSubmitResultV2:
    """Explicit result for a nonblocking enqueue operation."""

    accepted: bool
    reason: Literal["accepted", "queue_full", "not_sampled"]
    latency_ms: int


@dataclass
class AsyncShadowQueueV2:
    """In-memory bounded queue used only by the experiment runner."""

    max_size: int = 100
    _items: list[AsyncShadowSnapshotV2] = field(default_factory=list)

    def submit(self, snapshot: AsyncShadowSnapshotV2) -> AsyncShadowSubmitResultV2:
        started = time.perf_counter()
        if len(self._items) >= self.max_size:
            return AsyncShadowSubmitResultV2(
                accepted=False,
                reason="queue_full",
                latency_ms=_elapsed_ms(started),
            )
        self._items.append(snapshot)
        return AsyncShadowSubmitResultV2(
            accepted=True,
            reason="accepted",
            latency_ms=_elapsed_ms(started),
        )

    def pop(self) -> AsyncShadowSnapshotV2 | None:
        return self._items.pop(0) if self._items else None


@dataclass
class V2ArchitectureValidationRunner:
    """Run second-round experiments without consuming results in production."""

    document: V2ExperimentDocument
    vocabulary: CanonicalVocabulary
    analyzer: InputPreprocessingV2Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV2Analyzer] | None = None
    clinical_baseline: V2ClinicalBaselineAgent | None = None
    model: str = "qwen-plus"

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        only_experiment_ids: set[str] | None = None,
        repeat_override: int | None = None,
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for spec in self.document.experiments:
            if only_experiment_ids and spec.experiment_id not in only_experiment_ids:
                continue
            reports.append(
                await self._run_experiment(
                    spec,
                    mode=mode,
                    repeat_override=repeat_override,
                )
            )
        return reports

    async def _run_experiment(
        self,
        spec: V2ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
        repeat_override: int | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if spec.kind == V2ExperimentKind.N2:
            turn_reports = self._run_negative_mutations()
        elif spec.kind == V2ExperimentKind.AS2:
            turn_reports = await self._run_async_shadow(spec, mode=mode)
        else:
            cases = {
                case.sample_id: case
                for case in self.document.cases
                if case.sample_id in set(spec.sample_ids)
            }
            turn_reports = []
            repeat = repeat_override or spec.repeat_count
            for sample_id in spec.sample_ids:
                case = cases.get(sample_id)
                if case is None:
                    raise ValueError(f"unknown sample: {sample_id}")
                for attempt in range(1, repeat + 1):
                    turn_reports.append(
                        await self._run_case(
                            case=case,
                            mode=mode,
                            attempt=attempt,
                            analyzer=self._next_analyzer(),
                            stage1_override=spec.segmentation_variant == "expected",
                        )
                    )

        evaluation = _evaluate_experiment(spec, turn_reports)
        clinical_comparison = None
        if spec.kind == V2ExperimentKind.CS2:
            clinical_comparison = await self._clinical_comparison(spec, turn_reports)
            if clinical_comparison["downstream_evaluation"] != "not_implemented":
                evaluation["failures"].append("clinical_downstream_boundary_violation")
                evaluation["status"] = "failed"
        return {
            "experiment_id": spec.experiment_id,
            "architecture_version": "input-preprocessing-v2",
            "kind": spec.kind,
            "hypothesis": spec.hypothesis,
            "mode": mode,
            "expected_outcome": spec.expected_outcome,
            "segmentation_variant": spec.segmentation_variant,
            "repeat_count": spec.repeat_count,
            "effective_repeat_count": repeat_override or spec.repeat_count,
            "status": evaluation["status"],
            "failures": evaluation["failures"],
            "exit_criteria": evaluation["exit_criteria"],
            "metrics": evaluation["metrics"],
            "latency_ms": _elapsed_ms(started),
            "turns": turn_reports,
            "clinical_safety_comparison": clinical_comparison,
        }

    async def _run_case(
        self,
        *,
        case: V2ExpectedCase,
        mode: Literal["ideal", "shadow"],
        attempt: int,
        stage1_override: bool,
        analyzer: InputPreprocessingV2Analyzer | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        turn_context = self._turn_context(case)
        stage1 = V2Stage1Output.model_validate(case.golden_stage1)
        if mode == "ideal":
            stage2 = self._golden_stage2(case, stage1)
            result = V2InputAnalysisResult(
                turn_context=turn_context,
                stage1=stage1,
                stage2=stage2,
                gates=evaluate_v2_quality_gates(
                    user_text=case.user_text,
                    turn_context=turn_context,
                    stage1=stage1,
                    stage2=stage2,
                    vocabulary=self.vocabulary,
                ),
                model_name="golden_v2",
                vocabulary_version=self.vocabulary.version,
            )
            model_error = None
        else:
            analyzer = analyzer or self.analyzer
            if analyzer is None:
                raise ValueError("v2 shadow analyzer is required")
            try:
                result = await analyzer.analyze(
                    user_text=case.user_text,
                    turn_context=turn_context,
                    stage1_override=stage1 if stage1_override else None,
                )
                model_error = None
            except Exception as exc:  # noqa: BLE001 - explicit report-only failure
                result = None
                model_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }

        if result is None:
            return {
                "sample_id": case.sample_id,
                "attempt": attempt,
                "status": "failed",
                "model_error": model_error,
                "latency_ms": _elapsed_ms(started),
            }

        stage1_match = _stage1_matches(case, result.stage1)
        stage2_match = _stage2_matches(case, result.stage2)
        intent_match = all(
            getattr(result.stage1.intent, key) is value
            for key, value in case.expected_intent.items()
        )
        blocking_gates = result.failed_blocking_gates()
        status = "passed"
        failures: list[str] = []
        if not intent_match:
            failures.append("intent_mismatch")
        if not stage1_match["matched"]:
            failures.append("stage1_semantic_mismatch")
        if not stage2_match["matched"]:
            failures.append("stage2_semantic_mismatch")
        if blocking_gates:
            failures.extend(
                f"blocking_gate:{gate.gate_id}:{gate.reason_code}"
                for gate in blocking_gates
            )
            status = "gate_blocked"
        elif failures:
            status = "failed"
        report: dict[str, Any] = {
            "sample_id": case.sample_id,
            "attempt": attempt,
            "status": status,
            "failures": failures,
            "intent_match": intent_match,
            "stage1_match": stage1_match,
            "stage2_match": stage2_match,
            "semantic_signature": _result_signature(result),
            "metrics": _result_metrics(result),
            "gates": [gate.model_dump(mode="json") for gate in result.gates],
            "stage_latency_ms": result.stage_latency_ms,
            "stage_attempts": result.stage_attempts,
            "stage1_trace": result.stage1.model_dump(mode="json"),
            "stage2_trace": result.stage2.model_dump(mode="json"),
            "model": result.model_name,
            "vocabulary_version": result.vocabulary_version,
            "latency_ms": _elapsed_ms(started),
        }
        if case.expected_behavior is not None:
            report["behavior_simulation"] = _behavior_branches(
                result,
                case.expected_behavior,
            )
        return report

    def _run_negative_mutations(self) -> list[dict[str, Any]]:
        cases = {case.sample_id: case for case in self.document.cases}
        reports: list[dict[str, Any]] = []
        for mutation in self.document.negative_mutations:
            case = cases[mutation["base_sample_id"]]
            turn_context = self._turn_context(case)
            stage1 = V2Stage1Output.model_validate(case.golden_stage1)
            stage2 = self._golden_stage2(case, stage1)
            stage1, stage2 = _apply_negative_mutation(
                mutation["mutation_id"],
                stage1=stage1,
                stage2=stage2,
            )
            gates = evaluate_v2_quality_gates(
                user_text=case.user_text,
                turn_context=turn_context,
                stage1=stage1,
                stage2=stage2,
                vocabulary=self.vocabulary,
            )
            blocking = [
                gate for gate in gates if gate.status == V2QualityGateStatus.FAILED
            ]
            reports.append(
                {
                    "sample_id": case.sample_id,
                    "mutation_id": mutation["mutation_id"],
                    "status": "gate_blocked_as_expected" if blocking else "failed",
                    "blocking_gates": [
                        gate.model_dump(mode="json") for gate in blocking
                    ],
                    "failures": [] if blocking else ["negative_not_blocked"],
                    "latency_ms": 0,
                }
            )
        return reports

    async def _run_async_shadow(
        self,
        spec: V2ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> list[dict[str, Any]]:
        cases = {case.sample_id: case for case in self.document.cases}
        analyzer = self._next_analyzer()
        queue = AsyncShadowQueueV2(max_size=1)
        submissions: list[dict[str, Any]] = []
        for sample_id in spec.sample_ids:
            case = cases[sample_id]
            snapshot = AsyncShadowSnapshotV2(
                snapshot_id=hashlib.sha256(
                    f"{sample_id}:{self.document.reference_time.isoformat()}".encode()
                ).hexdigest()[:24],
                sample_id=sample_id,
                user_text=case.user_text,
                turn_context=self._turn_context(case),
            )
            submissions.append(queue.submit(snapshot).__dict__)

        worker_started = time.perf_counter()
        worker_status = "no_snapshot"
        worker_error: dict[str, Any] | None = None
        worker_trace: dict[str, Any] | None = None
        popped_snapshot = queue.pop()
        if popped_snapshot is not None:
            case = cases[popped_snapshot.sample_id]
            try:
                if mode == "ideal":
                    await asyncio.sleep(0)
                    worker_status = "completed"
                else:
                    if analyzer is None:
                        raise ValueError("v2 shadow analyzer is required")
                    worker_result = await asyncio.wait_for(
                        analyzer.analyze(
                            user_text=popped_snapshot.user_text,
                            turn_context=popped_snapshot.turn_context,
                        ),
                        timeout=float(
                            os.getenv("INPUT_PREPROCESSING_V2_TIMEOUT", "90")
                        ),
                    )
                    worker_status = "completed"
                    worker_trace = {
                        "stage1": worker_result.stage1.model_dump(mode="json"),
                        "stage2": worker_result.stage2.model_dump(mode="json"),
                        "gates": [
                            gate.model_dump(mode="json") for gate in worker_result.gates
                        ],
                        "stage_latency_ms": worker_result.stage_latency_ms,
                        "stage_attempts": worker_result.stage_attempts,
                    }
            except Exception as exc:  # noqa: BLE001 - worker failure is isolated
                worker_status = "failed"
                worker_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
        expected_drop = len(spec.sample_ids) > queue.max_size
        passed = (
            bool(submissions)
            and submissions[0]["accepted"]
            and expected_drop
            and any(item["reason"] == "queue_full" for item in submissions[1:])
            and (
                worker_status == "completed"
                or (worker_status == "failed" and worker_error is not None)
                or worker_status == "no_snapshot"
            )
        )
        return [
            {
                "sample_id": "AS2_async_shadow_isolation",
                "status": "passed" if passed else "failed",
                "submissions": submissions,
                "queue_max_size": queue.max_size,
                "enqueue_latency_ms": {
                    "max": max(item["latency_ms"] for item in submissions),
                },
                "worker_status": worker_status,
                "worker_error": worker_error,
                "worker_trace": worker_trace,
                "worker_latency_ms": _elapsed_ms(worker_started),
                "latency_ms": _elapsed_ms(worker_started),
                "business_state_written": False,
                "clinical_safety_evaluator_called": False,
            }
        ]

    def _next_analyzer(self) -> InputPreprocessingV2Analyzer | None:
        """Return a fresh analyzer for experiment-level failure isolation."""

        return (
            self.analyzer_factory()
            if self.analyzer_factory is not None
            else self.analyzer
        )

    async def _clinical_comparison(
        self,
        spec: V2ExperimentSpec,
        turn_reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        case = next(
            item for item in self.document.cases if item.sample_id in spec.sample_ids
        )
        baseline: Any | None = None
        baseline_error: dict[str, Any] | None = None
        if self.clinical_baseline is not None:
            try:
                baseline = await self.clinical_baseline.extract(
                    user_text=case.user_text,
                    pet_context_summary=json.dumps(
                        self.document.pet_profile,
                        ensure_ascii=False,
                    ),
                    model=self.model,
                )
            except Exception as exc:  # noqa: BLE001 - report-only failure
                baseline_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
        baseline_present = 0
        baseline_denied = 0
        if baseline is not None:
            baseline_present = sum(
                feature.state == "present" for feature in baseline.observed_features
            )
            baseline_denied = len(baseline.negated_terms)
        actual_metrics: dict[str, Any] = next(
            (
                item.get("metrics", {})
                for item in turn_reports
                if item.get("metrics") is not None
            ),
            {},
        )
        return {
            "comparison_status": "report_only"
            if baseline is not None
            else "baseline_unavailable",
            "baseline_error": baseline_error,
            "baseline_present_count": baseline_present,
            "baseline_denied_count": baseline_denied,
            "actual_turn_status": turn_reports[0].get("status")
            if turn_reports
            else "missing",
            "new_projection_present_count": actual_metrics.get(
                "current_pet_confirmed_present_count", 0
            ),
            "new_projection_denied_count": actual_metrics.get(
                "current_pet_confirmed_denied_count", 0
            ),
            "downstream_evaluation": "not_implemented",
            "evaluator_called": False,
            "opa_called": False,
        }

    def _turn_context(self, case: V2ExpectedCase) -> V2TurnContext:
        return V2TurnContext(
            request_id=f"v2-{case.sample_id}-{uuid4().hex[:8]}",
            trace_id=f"v2-trace-{case.sample_id}",
            user_id="v2-eval-user",
            pet_id="v2-eval-pet",
            session_id="v2-eval-session",
            task_key=case.sample_id,
            reference_time=self.document.reference_time,
            current_pet_subject=V2SubjectReference(
                reference_id="current_pet",
                entity_type=V2EntityType.CURRENT_PET,
                display_name="当前宠物",
            ),
            other_subjects=[
                V2SubjectReference.model_validate(item) for item in case.other_subjects
            ],
            verified_pet_profile=self.document.pet_profile,
        )

    def _golden_stage2(
        self,
        case: V2ExpectedCase,
        stage1: V2Stage1Output,
    ) -> V2Stage2Output:
        items = _stage1_item_bindings(stage1)
        terms = self.vocabulary.term_map()
        observations: list[V2VerifiedEvidence] = []
        for index, expected in enumerate(case.expected_evidence, start=1):
            item = items.get(expected.surface_text)
            if item is None:
                raise ValueError(
                    f"golden Stage 1 lacks expected surface: {expected.surface_text}"
                )
            segment_id, item_id, subject, participants = item
            candidate = (
                [
                    V2CanonicalCandidate(
                        canonical_id=expected.canonical_id or "",
                        surface_form=terms[expected.canonical_id].aliases[0],
                        score=1.0,
                    )
                ]
                if expected.canonical_id is not None and expected.canonical_id in terms
                else []
            )
            observations.append(
                V2VerifiedEvidence(
                    evidence_id=f"golden-{index}",
                    segment_id=segment_id,
                    item_id=item_id,
                    source_text=expected.surface_text,
                    assertion=expected.assertion,  # type: ignore[arg-type]
                    mapping_status=expected.mapping_status,  # type: ignore[arg-type]
                    canonical_id=expected.canonical_id,
                    subject=subject,
                    participants=participants,
                    candidates=candidate,
                    review_required=expected.review_required,
                    confidence=0.98,
                )
            )
        return V2Stage2Output(observations=observations)


def load_v2_experiment_document(path: Path) -> V2ExperimentDocument:
    """Load and validate the second-round experiment matrix."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = V2ExperimentDocument.model_validate(raw)
        for case in document.cases:
            V2Stage1Output.model_validate(case.golden_stage1)
        sample_ids = {case.sample_id for case in document.cases}
        for spec in document.experiments:
            unknown = set(spec.sample_ids) - sample_ids
            if unknown:
                raise ValueError(f"unknown experiment samples: {sorted(unknown)}")
        return document
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid V2 experiment document: {path}") from exc


def write_v2_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
) -> Path:
    """Write a V2 report with explicit architecture metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v2-{uuid4().hex[:12]}.json"
    passed = sum(item["status"] == "passed" for item in reports)
    payload = {
        "schema_version": "v2",
        "generated_at": datetime.now().astimezone().isoformat(),
        "architecture_version": "input-preprocessing-v2",
        "summary": {
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "experiments": reports,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _stage1_matches(
    case: V2ExpectedCase,
    actual: V2Stage1Output,
) -> dict[str, Any]:
    expected = V2Stage1Output.model_validate(case.golden_stage1)
    expected_by_surface = {
        _evaluation_surface(item["surface_text"]): item
        for item in _flatten_stage1(expected)
    }
    actual_by_surface = {
        _evaluation_surface(item["surface_text"]): item
        for item in _flatten_stage1(actual)
    }
    differences: list[str] = []
    for surface, expected_item in expected_by_surface.items():
        actual_item = actual_by_surface.get(surface)
        if actual_item is None:
            differences.append(f"missing_stage1_item:{surface}")
            continue
        for key in ("kind", "scope_assertion", "subject_reference"):
            if expected_item.get(key) != actual_item.get(key):
                differences.append(f"{surface}:{key}_mismatch")
        if expected_item.get("participants") != actual_item.get("participants"):
            differences.append(f"{surface}:participants_mismatch")
    for surface in actual_by_surface.keys() - expected_by_surface.keys():
        differences.append(f"extra_stage1_item:{surface}")
    return {
        "matched": not differences,
        "differences": differences,
        "expected_count": len(expected_by_surface),
        "actual_count": len(actual_by_surface),
    }


def _stage2_matches(
    case: V2ExpectedCase,
    actual: V2Stage2Output,
) -> dict[str, Any]:
    expected_by_surface = {
        _evaluation_surface(item.surface_text): _expected_stage2_signature(item)
        for item in case.expected_evidence
    }
    actual_by_surface = {
        _evaluation_surface(item.source_text): _actual_stage2_signature(item)
        for item in actual.observations
    }
    differences: list[str] = []
    for surface, expected in expected_by_surface.items():
        actual_item = actual_by_surface.get(surface)
        if actual_item is None:
            differences.append(f"missing_stage2_item:{surface}")
        elif expected != actual_item:
            differences.append(f"stage2_signature_mismatch:{surface}")
    for surface in actual_by_surface.keys() - expected_by_surface.keys():
        differences.append(f"extra_stage2_item:{surface}")
    return {
        "matched": not differences,
        "differences": differences,
        "expected_count": len(expected_by_surface),
        "actual_count": len(actual_by_surface),
    }


def _flatten_stage1(stage1: V2Stage1Output) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for segment in stage1.segments:
        base = {
            "surface_text": segment.source_text,
            "kind": segment.kind,
            "subject_reference": segment.subject.reference_id,
            "participants": _participant_signature(segment.participants),
        }
        if isinstance(segment, V2SharedAssertionScopeSegment):
            base["scope_assertion"] = segment.scope_assertion.value
            result.append(base)
            for item in segment.items:
                result.append(
                    {
                        "surface_text": item.source_text,
                        "kind": "scope_item",
                        "scope_assertion": segment.scope_assertion.value,
                        "subject_reference": item.subject.reference_id,
                        "participants": _participant_signature(item.participants),
                    }
                )
        elif isinstance(segment, V2AtomicClaimSegment):
            result.append(base)
    return result


def _stage1_item_bindings(
    stage1: V2Stage1Output,
) -> dict[str, tuple[str, str, V2EntityBinding, list[V2ParticipantBinding]]]:
    result: dict[str, tuple[str, str, V2EntityBinding, list[V2ParticipantBinding]]] = {}
    for segment in stage1.segments:
        if isinstance(segment, V2SharedAssertionScopeSegment):
            for item in segment.items:
                result[item.source_text] = (
                    segment.segment_id,
                    item.item_id,
                    item.subject,
                    item.participants,
                )
        elif isinstance(segment, V2AtomicClaimSegment):
            result[segment.source_text] = (
                segment.segment_id,
                segment.item_id,
                segment.subject,
                segment.participants,
            )
    return result


def _participant_signature(
    participants: list[V2ParticipantBinding],
) -> list[dict[str, str]]:
    return sorted(
        (
            {"role": item.role.value, "reference": item.entity.reference_id}
            for item in participants
        ),
        key=lambda item: (item["role"], item["reference"]),
    )


def _evaluation_surface(text: str) -> str:
    """Normalize only whitespace and punctuation for fixture comparisons."""

    return re.sub(r"[\s，。！？；：、,.!?;:…·\-—]+", "", text)


def _expected_stage2_signature(item: V2ExpectedEvidence) -> dict[str, Any]:
    return {
        "assertion": item.assertion,
        "mapping_status": item.mapping_status,
        "canonical_id": item.canonical_id,
        "subject_reference": item.subject_reference,
        "subject_type": item.subject_type,
        "participants": sorted(
            item.participants,
            key=lambda value: (value["role"], value["reference"]),
        ),
        "review_required": item.review_required,
    }


def _actual_stage2_signature(item: V2VerifiedEvidence) -> dict[str, Any]:
    return {
        "assertion": item.assertion.value,
        "mapping_status": item.mapping_status.value,
        "canonical_id": item.canonical_id,
        "subject_reference": item.subject.reference_id,
        "subject_type": item.subject.entity_type.value,
        "participants": _participant_signature(item.participants),
        "review_required": item.review_required,
    }


def _apply_negative_mutation(
    mutation_id: str,
    *,
    stage1: V2Stage1Output,
    stage2: V2Stage2Output,
) -> tuple[V2Stage1Output, V2Stage2Output]:
    if mutation_id == "not_found_as_canonical_id":
        changed = stage2.observations[0].model_copy(
            update={
                "mapping_status": V2CanonicalMappingStatus.CONFIRMED,
                "canonical_id": "not_found",
            }
        )
        return stage1, V2Stage2Output(observations=[changed, *stage2.observations[1:]])
    if mutation_id == "invented_canonical":
        changed = stage2.observations[0].model_copy(
            update={
                "mapping_status": V2CanonicalMappingStatus.CONFIRMED,
                "canonical_id": "__v2_invented_canonical__",
            }
        )
        return stage1, V2Stage2Output(observations=[changed, *stage2.observations[1:]])
    if mutation_id == "participant_role_mismatch":
        target = next(
            item
            for item in stage2.observations
            if item.participants and item.participants[0].role.value == "action_agent"
        )
        changed_participant = target.participants[0].model_copy(
            update={
                "entity": V2EntityBinding(
                    reference_id="current_pet",
                    entity_type=V2EntityType.CURRENT_PET,
                    resolution_method=V2ResolutionMethod.TRUSTED_CURRENT_PET,
                    resolution_status=V2ResolutionStatus.RESOLVED,
                    confidence=0.9,
                )
            }
        )
        changed = target.model_copy(
            update={"participants": [changed_participant, *target.participants[1:]]}
        )
        observations = [
            changed if item.evidence_id == target.evidence_id else item
            for item in stage2.observations
        ]
        return stage1, V2Stage2Output(observations=observations)
    if mutation_id == "ambiguous_subject_missing_candidates":
        segments: list[Any] = []
        for segment in stage1.segments:
            if isinstance(segment, V2AtomicClaimSegment):
                segments.append(
                    segment.model_copy(
                        update={
                            "subject": V2EntityBinding(
                                reference_id="subject_ambiguous",
                                entity_type=V2EntityType.UNKNOWN,
                                resolution_method=V2ResolutionMethod.SUBJECT_AMBIGUOUS,
                                resolution_status=V2ResolutionStatus.AMBIGUOUS,
                                subject_candidates=[],
                                confidence=0.5,
                            )
                        }
                    )
                )
            else:
                segments.append(segment)
        return (
            V2Stage1Output.model_validate(
                {
                    **stage1.model_dump(mode="json"),
                    "segments": [item.model_dump(mode="json") for item in segments],
                }
            ),
            stage2,
        )
    if mutation_id == "expected_item_missing":
        return stage1, V2Stage2Output(observations=stage2.observations[:-1])
    if mutation_id == "stage2_assertion_replaced":
        changed = stage2.observations[0].model_copy(
            update={"assertion": "normal"}  # type: ignore[arg-type]
        )
        return stage1, V2Stage2Output(observations=[changed, *stage2.observations[1:]])
    raise ValueError(f"unknown negative mutation: {mutation_id}")


def _behavior_branches(
    result: V2InputAnalysisResult,
    expected: V2ExpectedBehavior,
) -> dict[str, Any]:
    answer_now = result.stage1.intent.answer_now
    return {
        "answer_now": answer_now,
        "baseline_empty_success": {
            "action": expected.baseline_empty,
            "uses_new_facts": False,
        },
        "answer_now_only": {
            "action": "answer" if answer_now else "ask",
            "uses_new_facts": False,
        },
        "full_projection": {
            "action": expected.full_projection,
            "uses_new_facts": True,
        },
    }


def _evaluate_experiment(
    spec: V2ExperimentSpec,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    failures: list[str] = []
    if spec.expected_outcome == "semantic_exact_match":
        if any(item["status"] != "passed" for item in reports):
            failures.append("semantic_exact_match_failed")
    elif spec.expected_outcome == "gate_blocked_as_expected":
        if any(item["status"] != "gate_blocked_as_expected" for item in reports):
            failures.append("expected_gate_block_not_observed")
    elif spec.expected_outcome == "governance_report_only":
        if any(item["status"] != "passed" for item in reports):
            failures.append("governance_contract_failed")
        if not any(
            item.get("metrics", {}).get("review_required_count", 0) for item in reports
        ):
            failures.append("unmapped_review_missing")
    elif spec.expected_outcome == "stable_output":
        by_sample: dict[str, set[str]] = {}
        for item in reports:
            if item["status"] != "passed":
                failures.append("stable_output_semantic_failure")
            by_sample.setdefault(item["sample_id"], set()).add(
                json.dumps(
                    item.get("semantic_signature"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        unstable = {
            sample_id: len(signatures)
            for sample_id, signatures in by_sample.items()
            if len(signatures) != 1
        }
        if unstable:
            failures.append("repeat_output_unstable")
    elif spec.expected_outcome == "branch_behavior_match":
        for item in reports:
            behavior = item.get("behavior_simulation")
            if not behavior or not behavior["answer_now"]:
                failures.append("answer_now_not_recognized")
            elif behavior["answer_now_only"]["action"] != "answer":
                failures.append("answer_now_only_did_not_answer")
            elif (
                behavior["baseline_empty_success"]["action"]
                == behavior["answer_now_only"]["action"]
            ):
                failures.append("answer_now_branch_has_no_delta")
    elif spec.expected_outcome == "comparison_report_only":
        if not reports or not any(item["status"] == "passed" for item in reports):
            failures.append("comparison_report_missing_or_blocked")
    elif spec.expected_outcome == "async_isolation_report_only" and any(
        item["status"] != "passed" for item in reports
    ):
        failures.append("async_shadow_isolation_failed")

    signatures = [
        json.dumps(
            item.get("semantic_signature"),
            ensure_ascii=False,
            sort_keys=True,
        )
        for item in reports
        if item.get("semantic_signature") is not None
    ]
    signature_counts = Counter(signatures)
    return {
        "status": "passed" if not failures else "failed",
        "failures": failures,
        "exit_criteria": {
            "all_turns_passed": all(item["status"] == "passed" for item in reports),
            "blocking_gate_count": sum(
                len(item.get("blocking_gates", [])) for item in reports
            ),
            "unique_output_count": len(signature_counts),
        },
        "metrics": {
            "turn_count": len(reports),
            "passed_turn_count": sum(item["status"] == "passed" for item in reports),
            "gate_blocked_count": sum(
                item["status"] in {"gate_blocked", "gate_blocked_as_expected"}
                for item in reports
            ),
            "review_required_count": sum(
                item.get("metrics", {}).get("review_required_count", 0)
                for item in reports
            ),
            "normal_as_denied_count": sum(
                item.get("metrics", {}).get("normal_as_denied", False)
                for item in reports
            ),
            "denied_as_present_count": sum(
                item.get("metrics", {}).get("denied_as_present", False)
                for item in reports
            ),
            "median_turn_latency_ms": (
                int(median(item["latency_ms"] for item in reports)) if reports else 0
            ),
        },
    }


def _result_signature(result: V2InputAnalysisResult) -> list[dict[str, Any]]:
    return [
        _actual_stage2_signature(item) | {"source_text": item.source_text}
        for item in result.stage2.observations
    ]


def _result_metrics(result: V2InputAnalysisResult) -> dict[str, Any]:
    observations = result.stage2.observations
    return {
        "segment_count": len(result.stage1.segments),
        "expected_evidence_count": result.stage1.profile.expected_fact_candidate_count,
        "handled_evidence_count": len(observations),
        "confirmed_count": sum(
            item.mapping_status == V2CanonicalMappingStatus.CONFIRMED
            for item in observations
        ),
        "unmapped_count": sum(
            item.mapping_status
            in {
                V2CanonicalMappingStatus.NOT_FOUND,
                V2CanonicalMappingStatus.UNMAPPED_MENTION,
                V2CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            for item in observations
        ),
        "review_required_count": sum(item.review_required for item in observations),
        "participant_role_count": sum(len(item.participants) for item in observations),
        "current_pet_confirmed_present_count": sum(
            item.mapping_status == V2CanonicalMappingStatus.CONFIRMED
            and item.assertion.value in {"present", "abnormal"}
            and item.subject.reference_id
            == result.turn_context.current_pet_subject.reference_id
            for item in observations
        ),
        "current_pet_confirmed_denied_count": sum(
            item.mapping_status == V2CanonicalMappingStatus.CONFIRMED
            and item.assertion.value == "denied"
            and item.subject.reference_id
            == result.turn_context.current_pet_subject.reference_id
            for item in observations
        ),
        "normal_as_denied": any(
            item.assertion.value == "denied"
            and item.canonical_id in {"mental_status", "appetite", "water_intake"}
            for item in observations
        ),
        "denied_as_present": any(
            item.assertion.value == "present"
            and item.canonical_id in {"vomiting", "bloody_stool"}
            for item in observations
        ),
        "blocking_gate_count": len(result.failed_blocking_gates()),
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(
            "tests/fixtures/input_preprocessing/second_round_shadow_matrix.json"
        ),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow", "both"), default="ideal")
    parser.add_argument(
        "--model", default=os.getenv("INPUT_PREPROCESSING_V2_MODEL", "qwen-plus")
    )
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--repeat-override", type=int, default=None)
    parser.add_argument("--with-clinical-baseline", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v2-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    document = load_v2_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    modes: list[Literal["ideal", "shadow"]] = (
        ["ideal", "shadow"] if args.mode == "both" else [args.mode]
    )
    analyzer: InputPreprocessingV2Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV2Analyzer] | None = None
    clinical_baseline: V2ClinicalBaselineAgent | None = None
    if "shadow" in modes:
        from vet_agent.clinical_safety import ClinicalSafetySemanticExtractorAgent
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()

        def build_analyzer() -> InputPreprocessingV2Analyzer:
            return InputPreprocessingV2Analyzer(
                qwen=QwenClient(settings),
                embeddings=QwenEmbeddingClient(settings),
                vocabulary=vocabulary,
                model=args.model,
            )

        analyzer_factory = build_analyzer
        if args.with_clinical_baseline:
            clinical_baseline = ClinicalSafetySemanticExtractorAgent(
                QwenClient(settings),
                settings,
            )
    runner = V2ArchitectureValidationRunner(
        document=document,
        vocabulary=vocabulary,
        analyzer=analyzer,
        analyzer_factory=analyzer_factory,
        clinical_baseline=clinical_baseline,
        model=args.model,
    )
    reports: list[dict[str, Any]] = []
    only = set(args.experiment) if args.experiment else None
    for mode in modes:
        reports.extend(
            await runner.run(
                mode=mode,
                only_experiment_ids=only,
                repeat_override=args.repeat_override,
            )
        )
    path = write_v2_experiment_report(output_dir=args.output_dir, reports=reports)
    failed = [item for item in reports if item["status"] != "passed"]
    print(f"report={path}")
    print(
        f"experiments={len(reports)} passed={len(reports) - len(failed)} "
        f"failed={len(failed)}"
    )
    for item in failed:
        print(
            f"FAILED experiment={item['experiment_id']} mode={item['mode']} "
            f"failures={','.join(item['failures'])}"
        )
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
