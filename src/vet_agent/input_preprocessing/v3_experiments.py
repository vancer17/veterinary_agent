"""Third-round V3 architecture validation and report-only shadow runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .runtime_helpers import make_runtime_settings
from .v3_analyzer import InputPreprocessingV3Analyzer
from .v3_candidate_linker import V3CandidateRetriever
from .v3_contracts import (
    V3AssertionState,
    V3AssertionVerification,
    V3CandidateSet,
    V3CanonicalCandidate,
    V3CanonicalMappingStatus,
    V3EntityType,
    V3InputAnalysisResult,
    V3ObservationVerification,
    V3ParticipantVerification,
    V3QualityGateStatus,
    V3SemanticClass,
    V3Stage1Output,
    V3Stage2Output,
    V3SubjectReference,
    V3TurnContext,
    V3VerifiedEvidence,
)
from .v3_gates import evaluate_v3_quality_gates
from .v3_stage1_assembler import V3ItemContext, iter_v3_items
from .vocabulary import CanonicalVocabulary


class V3ExperimentKind:
    """Finite string constants for the third-round matrix."""

    S1_COUNT = "s1_count"
    S1_SCOPE = "s1_scope"
    S1_ROLE = "s1_role"
    S1_INTENT = "s1_intent"
    S2_ITEM = "s2_item"
    S2_PARTICIPANT = "s2_participant"
    CAN_LINK = "can_link"
    CAN_TYPE = "can_type"
    REP = "rep"
    NEG = "neg"
    ASYNC = "async"
    CS = "cs"


class V3ExpectedEvidence(BaseModel):
    """Expected semantic signature for one Stage 1 item."""

    model_config = ConfigDict(extra="forbid")

    surface_text: str = Field(min_length=1, max_length=320)
    assertion: str = Field(min_length=1, max_length=40)
    mapping_status: str = Field(min_length=1, max_length=40)
    canonical_id: str | None = Field(default=None, max_length=96)
    subject_reference: str = Field(min_length=1, max_length=64)
    subject_type: str = Field(min_length=1, max_length=40)
    participants: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    review_required: bool = False


class V3ExpectedCase(BaseModel):
    """One development or held-out V3 sample."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    user_text: str = Field(min_length=1, max_length=4000)
    other_subjects: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    golden_stage1: dict[str, Any]
    expected_intent: dict[str, bool]
    expected_evidence: list[V3ExpectedEvidence] = Field(
        default_factory=list, max_length=64
    )


class V3ExperimentSpec(BaseModel):
    """One controlled architecture experiment."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    architecture_question: str = Field(min_length=1, max_length=500)
    control_group: str = Field(min_length=1, max_length=240)
    experimental_group: str = Field(min_length=1, max_length=240)
    sample_ids: list[str] = Field(min_length=1, max_length=32)
    repeat_count: int = Field(default=3, ge=1, le=10)
    stage1_variant: Literal["model", "golden"] = "model"
    primary_metrics: list[str] = Field(default_factory=list, max_length=16)
    gate_expectation: str = Field(min_length=1, max_length=240)
    decision_rule: str = Field(min_length=1, max_length=500)
    expected_outcome: Literal[
        "architecture_match",
        "stable_output",
        "gate_blocked_as_expected",
        "async_isolation_report_only",
        "comparison_report_only",
    ]


class V3ExperimentDocument(BaseModel):
    """Versioned third-round fixture contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3"]
    dataset_type: Literal["development", "held_out"]
    reference_time: datetime
    pet_profile: dict[str, Any]
    cases: list[V3ExpectedCase] = Field(min_length=1)
    experiments: list[V3ExperimentSpec] = Field(min_length=1)
    negative_mutations: list[dict[str, str]] = Field(min_length=1)


class V3ClinicalBaselineAgent(Protocol):
    """Minimal existing clinical-safety semantic interface."""

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        model: str,
    ) -> Any: ...


@dataclass(frozen=True)
class AsyncShadowSnapshotV3:
    """A durable report-only API shadow snapshot."""

    snapshot_id: str
    sample_id: str
    user_text: str
    turn_context: V3TurnContext


@dataclass(frozen=True)
class AsyncShadowSubmitResultV3:
    """Explicit nonblocking enqueue result."""

    accepted: bool
    reason: Literal["accepted", "queue_full", "not_sampled"]
    latency_ms: int
    snapshot_id: str


class FileAsyncShadowQueueV3:
    """Experiment-only durable bounded queue with explicit dead letters."""

    def __init__(self, *, directory: Path, max_size: int = 2) -> None:
        self.directory = directory
        self.max_size = max_size
        self.directory.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        snapshot: AsyncShadowSnapshotV3,
    ) -> AsyncShadowSubmitResultV3:
        """Persist a snapshot without blocking on model execution."""

        started = time.perf_counter()
        if len(self._records(status={"pending", "running"})) >= self.max_size:
            return AsyncShadowSubmitResultV3(
                accepted=False,
                reason="queue_full",
                latency_ms=_elapsed_ms(started),
                snapshot_id=snapshot.snapshot_id,
            )
        record = {
            "snapshot_id": snapshot.snapshot_id,
            "sample_id": snapshot.sample_id,
            "status": "pending",
            "attempt_count": 0,
            "snapshot": _snapshot_payload(snapshot),
            "created_at": datetime.now().astimezone().isoformat(),
            "updated_at": datetime.now().astimezone().isoformat(),
        }
        path = self.directory / f"{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AsyncShadowSubmitResultV3(
            accepted=True,
            reason="accepted",
            latency_ms=_elapsed_ms(started),
            snapshot_id=snapshot.snapshot_id,
        )

    def claim(self) -> AsyncShadowSnapshotV3 | None:
        """Claim the oldest pending snapshot and persist running state."""

        records = self._records(status={"pending"})
        if not records:
            return None
        record = records[0]
        record["status"] = "running"
        record["attempt_count"] += 1
        record["lease_owner"] = uuid4().hex
        record["updated_at"] = datetime.now().astimezone().isoformat()
        self._write(record)
        raw = record["snapshot"]
        return AsyncShadowSnapshotV3(
            snapshot_id=raw["snapshot_id"],
            sample_id=raw["sample_id"],
            user_text=raw["user_text"],
            turn_context=V3TurnContext.model_validate(raw["turn_context"]),
        )

    def complete(self, snapshot_id: str, *, trace: dict[str, Any]) -> None:
        record = self._record(snapshot_id)
        record.update(
            {
                "status": "succeeded",
                "trace": trace,
                "updated_at": datetime.now().astimezone().isoformat(),
            }
        )
        self._write(record)

    def fail(
        self,
        snapshot_id: str,
        *,
        reason: str,
        max_attempts: int = 1,
    ) -> Literal["failed", "dead_letter"]:
        record = self._record(snapshot_id)
        status: Literal["failed", "dead_letter"] = (
            "dead_letter"
            if record["attempt_count"] >= max_attempts
            else "failed"
        )
        record.update(
            {
                "status": status,
                "failure_reason": reason,
                "updated_at": datetime.now().astimezone().isoformat(),
            }
        )
        self._write(record)
        return status

    def dead_letters(self) -> list[dict[str, Any]]:
        return self._records(status={"dead_letter"})

    def _records(self, *, status: set[str]) -> list[dict[str, Any]]:
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.directory.glob("*.json"))
        ]
        return [record for record in records if record["status"] in status]

    def _record(self, snapshot_id: str) -> dict[str, Any]:
        path = self.directory / f"{snapshot_id}.json"
        if not path.exists():
            raise ValueError(f"unknown async snapshot: {snapshot_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, record: dict[str, Any]) -> None:
        path = self.directory / f"{record['snapshot_id']}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class V3ArchitectureValidationRunner:
    """Run controlled V3 experiments without consuming report-only results."""

    document: V3ExperimentDocument
    vocabulary: CanonicalVocabulary
    analyzer: InputPreprocessingV3Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV3Analyzer] | None = None
    clinical_baseline: V3ClinicalBaselineAgent | None = None
    model: str = "qwen-plus"
    async_queue_directory: Path = Path(
        ".data/evaluations/input-preprocessing-v3-async-shadow"
    )
    _turn_cache: dict[tuple[Any, ...], dict[str, Any]] = field(
        default_factory=dict,
        init=False,
    )

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        phase: Literal["exploratory", "confirmatory"] = "exploratory",
        only_experiment_ids: set[str] | None = None,
        repeat_override: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run selected experiments and return versioned report objects."""

        if phase == "confirmatory":
            if self.document.dataset_type != "held_out":
                raise ValueError("confirmatory experiments require held_out fixture")
            effective_repeat = repeat_override or 3
            if effective_repeat < 3:
                raise ValueError("confirmatory experiments require at least 3 repeats")

        reports: list[dict[str, Any]] = []
        for spec in self.document.experiments:
            if only_experiment_ids and spec.experiment_id not in only_experiment_ids:
                continue
            reports.append(
                await self._run_experiment(
                    spec,
                    mode=mode,
                    phase=phase,
                    repeat_override=repeat_override,
                )
            )
        return reports

    async def _run_experiment(
        self,
        spec: V3ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
        phase: Literal["exploratory", "confirmatory"],
        repeat_override: int | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if spec.kind == V3ExperimentKind.NEG:
            turns = self._run_negative_mutations()
        elif spec.kind == V3ExperimentKind.ASYNC:
            turns = await self._run_async_shadow(spec, mode=mode)
        else:
            cases = {
                case.sample_id: case for case in self.document.cases
            }
            turns = []
            repeat = repeat_override or spec.repeat_count
            for sample_id in spec.sample_ids:
                case = cases.get(sample_id)
                if case is None:
                    raise ValueError(f"unknown sample: {sample_id}")
                for attempt in range(1, repeat + 1):
                    cache_key = (
                        mode,
                        phase,
                        sample_id,
                        attempt,
                        spec.stage1_variant,
                    )
                    if cache_key not in self._turn_cache:
                        self._turn_cache[cache_key] = await self._run_case(
                            case=case,
                            mode=mode,
                            attempt=attempt,
                            stage1_override=spec.stage1_variant == "golden",
                            analyzer=self._next_analyzer(),
                        )
                    turns.append(self._turn_cache[cache_key])

        evaluation = _evaluate_experiment(spec, turns)
        clinical_comparison = None
        if spec.kind == V3ExperimentKind.CS:
            clinical_comparison = await self._clinical_comparison(turns)
            if (
                clinical_comparison["downstream_evaluation"] != "not_implemented"
                or clinical_comparison["evaluator_called"]
                or clinical_comparison["opa_called"]
            ):
                evaluation["failures"].append("clinical_downstream_boundary_violation")
                evaluation["status"] = "failed"
            elif any(turn["status"] != "passed" for turn in turns):
                evaluation["failures"].append("upstream_gate_blocked_comparison")
                evaluation["status"] = "failed"

        return {
            "experiment_id": spec.experiment_id,
            "architecture_version": "input-preprocessing-v3",
            "architecture_question": spec.architecture_question,
            "control_group": spec.control_group,
            "experimental_group": spec.experimental_group,
            "kind": spec.kind,
            "mode": mode,
            "phase": phase,
            "dataset_type": self.document.dataset_type,
            "stage1_variant": spec.stage1_variant,
            "expected_outcome": spec.expected_outcome,
            "repeat_count": spec.repeat_count,
            "effective_repeat_count": repeat_override or spec.repeat_count,
            "status": evaluation["status"],
            "failures": evaluation["failures"],
            "exit_criteria": evaluation["exit_criteria"],
            "metrics": evaluation["metrics"],
            "latency_ms": _elapsed_ms(started),
            "turns": turns,
            "clinical_safety_comparison": clinical_comparison,
        }

    async def _run_case(
        self,
        *,
        case: V3ExpectedCase,
        mode: Literal["ideal", "shadow"],
        attempt: int,
        stage1_override: bool,
        analyzer: InputPreprocessingV3Analyzer | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        turn_context = self._turn_context(case)
        golden_stage1 = V3Stage1Output.model_validate(case.golden_stage1)
        if mode == "ideal":
            result = self._ideal_result(case, golden_stage1, turn_context)
            model_error = None
        else:
            analyzer = analyzer or self.analyzer
            if analyzer is None:
                raise ValueError("v3 shadow analyzer is required")
            try:
                result = await analyzer.analyze(
                    user_text=case.user_text,
                    turn_context=turn_context,
                    stage1_override=golden_stage1 if stage1_override else None,
                )
                model_error = None
            except Exception as exc:  # noqa: BLE001 - report-only failure is isolated
                result = None
                model_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1200],
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
        failures: list[str] = []
        if not intent_match:
            failures.append("intent_mismatch")
        if not stage1_match["matched"]:
            failures.append("stage1_semantic_mismatch")
        if not stage2_match["matched"]:
            failures.append("stage2_semantic_mismatch")
        failures.extend(
            f"blocking_gate:{gate.gate_id}:{gate.reason_code}"
            for gate in blocking_gates
        )
        status = "gate_blocked" if blocking_gates else ("failed" if failures else "passed")
        return {
            "sample_id": case.sample_id,
            "attempt": attempt,
            "status": status,
            "failures": failures,
            "intent_match": intent_match,
            "stage1_match": stage1_match,
            "stage2_match": stage2_match,
            "semantic_signature": _result_signature(result),
            "metrics": _result_metrics(result),
            "result": result.model_dump(mode="json"),
            "blocking_gates": [
                gate.model_dump(mode="json") for gate in blocking_gates
            ],
            "latency_ms": _elapsed_ms(started),
        }

    def _ideal_result(
        self,
        case: V3ExpectedCase,
        stage1: V3Stage1Output,
        turn_context: V3TurnContext,
    ) -> V3InputAnalysisResult:
        items = {item.source_text: item for item in iter_v3_items(stage1)}
        candidate_sets: list[V3CandidateSet] = []
        observations: list[V3VerifiedEvidence] = []
        terms = self.vocabulary.term_map()
        for index, expected in enumerate(case.expected_evidence, start=1):
            item = items.get(expected.surface_text)
            if item is None:
                raise ValueError(
                    f"golden Stage 1 lacks expected surface: {expected.surface_text}"
                )
            candidates = (
                [
                    V3CanonicalCandidate(
                        candidate_id="c-1",
                        canonical_id=expected.canonical_id or "",
                        canonical_type=terms[expected.canonical_id].canonical_type,
                        semantic_class=_semantic_class(
                            terms[expected.canonical_id].canonical_type
                        ),
                        surface_form=terms[expected.canonical_id].aliases[0],
                        score=1.0,
                        recall_source="ideal_control",
                    )
                ]
                if expected.canonical_id is not None
                and expected.canonical_id in terms
                else []
            )
            candidate_set = V3CandidateSet(
                segment_id=item.segment_id,
                item_id=item.item_id,
                source_text=item.source_text,
                candidates=candidates,
                recall_status="recalled" if candidates else "no_candidate",
                recall_version="ideal-control",
            )
            candidate_sets.append(candidate_set)
            observations.append(
                V3VerifiedEvidence(
                    evidence_id=f"golden-{index}",
                    segment_id=item.segment_id,
                    item_id=item.item_id,
                    source_text=item.source_text,
                    initial_assertion=V3AssertionState(expected.assertion),
                    assertion_verification=V3AssertionVerification.VERIFIED,
                    mapping_status=V3CanonicalMappingStatus(expected.mapping_status),
                    selected_candidate_id="c-1" if candidates else None,
                    canonical_id=expected.canonical_id if candidates else None,
                    candidates=candidates,
                    subject=item.subject,
                    participants=list(item.participants),
                    participant_verification=(
                        V3ParticipantVerification.VERIFIED
                        if item.participants
                        else V3ParticipantVerification.NOT_APPLICABLE
                    ),
                    temporal_verification=V3ObservationVerification.NOT_APPLICABLE,
                    measurement_verification=V3ObservationVerification.NOT_APPLICABLE,
                    review_required=expected.review_required,
                    confidence=0.99,
                )
            )
        stage2 = V3Stage2Output(observations=observations)
        return V3InputAnalysisResult(
            turn_context=turn_context,
            stage1=stage1,
            candidate_sets=candidate_sets,
            stage2=stage2,
            gates=evaluate_v3_quality_gates(
                user_text=case.user_text,
                turn_context=turn_context,
                stage1=stage1,
                candidate_sets=candidate_sets,
                stage2=stage2,
                vocabulary=self.vocabulary,
            ),
            model_name="golden_v3",
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version="ideal-control",
        )

    def _run_negative_mutations(self) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        cases = {case.sample_id: case for case in self.document.cases}
        for mutation in self.document.negative_mutations:
            case = cases.get(mutation["base_sample_id"])
            if case is None:
                raise ValueError(f"unknown mutation sample: {mutation['base_sample_id']}")
            stage1 = V3Stage1Output.model_validate(case.golden_stage1)
            result = self._ideal_result(case, stage1, self._turn_context(case))
            mutated = _apply_negative_mutation(result, mutation["mutation_id"])
            gates = evaluate_v3_quality_gates(
                user_text=case.user_text,
                turn_context=mutated.turn_context,
                stage1=mutated.stage1,
                candidate_sets=mutated.candidate_sets,
                stage2=mutated.stage2,
                vocabulary=self.vocabulary,
            )
            blocking = [
                gate for gate in gates if gate.status == V3QualityGateStatus.FAILED
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
        spec: V3ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> list[dict[str, Any]]:
        cases = {case.sample_id: case for case in self.document.cases}
        queue = FileAsyncShadowQueueV3(
            directory=self.async_queue_directory / uuid4().hex,
            max_size=2,
        )
        submissions: list[dict[str, Any]] = []
        snapshots: list[AsyncShadowSnapshotV3] = []
        sample_ids = [*spec.sample_ids, spec.sample_ids[-1]]
        for index, sample_id in enumerate(sample_ids, start=1):
            case = cases[sample_id]
            snapshot = AsyncShadowSnapshotV3(
                snapshot_id=f"snapshot-{uuid4().hex[:12]}",
                sample_id=sample_id,
                user_text=case.user_text,
                turn_context=self._turn_context(case),
            )
            snapshots.append(snapshot)
            submissions.append(
                queue.submit(snapshot).__dict__ | {"queue_slot": index}
            )

        first = queue.claim()
        if first is None:
            return [
                {
                    "sample_id": "ASYNC",
                    "status": "failed",
                    "failures": ["async_snapshot_missing"],
                    "submissions": submissions,
                }
            ]
        first_trace = self._cached_trace_for_sample(first.sample_id, mode=mode)
        queue.complete(first.snapshot_id, trace=first_trace)

        second = queue.claim()
        second_status = "not_claimed"
        if second is not None:
            second_status = queue.fail(
                second.snapshot_id,
                reason="simulated_stage_timeout",
                max_attempts=1,
            )
        dead_letters = queue.dead_letters()
        expected_drop = len(sample_ids) > queue.max_size
        passed = (
            submissions[0]["accepted"]
            and submissions[1]["accepted"]
            and expected_drop
            and submissions[2]["reason"] == "queue_full"
            and second_status == "dead_letter"
            and len(dead_letters) == 1
        )
        return [
            {
                "sample_id": "ASYNC",
                "status": "passed" if passed else "failed",
                "failures": [] if passed else ["async_isolation_contract_failed"],
                "submissions": submissions,
                "queue_max_size": queue.max_size,
                "enqueue_latency_ms": {
                    "max": max(item["latency_ms"] for item in submissions),
                },
                "worker_status": "completed_and_dead_letter",
                "dead_letter_count": len(dead_letters),
                "trace_complete": bool(first_trace),
                "business_state_written": False,
                "clinical_safety_evaluator_called": False,
                "latency_ms": 0,
            }
        ]

    def _cached_trace_for_sample(
        self,
        sample_id: str,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        for key, turn in self._turn_cache.items():
            if key[0] == mode and turn.get("sample_id") == sample_id:
                return {
                    "status": turn.get("status"),
                    "semantic_signature": turn.get("semantic_signature"),
                    "metrics": turn.get("metrics"),
                }
        if mode == "ideal":
            case = next(
                case for case in self.document.cases if case.sample_id == sample_id
            )
            stage1 = V3Stage1Output.model_validate(case.golden_stage1)
            result = self._ideal_result(case, stage1, self._turn_context(case))
            return {
                "status": "passed",
                "semantic_signature": _result_signature(result),
                "metrics": _result_metrics(result),
            }
        return {}

    async def _clinical_comparison(
        self,
        turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline_present = 0
        baseline_denied = 0
        baseline_error = None
        if self.clinical_baseline is not None and turns:
            turn = turns[0]
            user_text = self._case_user_text(turn.get("sample_id"))
            try:
                baseline = await self.clinical_baseline.extract(
                    user_text=user_text,
                    pet_context_summary="猫，成年" if self.document.pet_profile.get("species") == "cat" else "犬，成年",
                    model=self.model,
                )
                baseline_payload = baseline.model_dump(mode="json")
                baseline_present = sum(
                    item.get("state") == "present"
                    for item in baseline_payload.get("observed_features", [])
                )
                baseline_denied = sum(
                    item.get("state") == "denied"
                    for item in baseline_payload.get("observed_features", [])
                )
            except Exception as exc:  # noqa: BLE001 - report-only dependency failure
                baseline_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
        actual_metrics = turns[0].get("metrics", {}) if turns else {}
        return {
            "baseline_error": baseline_error,
            "baseline_present_count": baseline_present,
            "baseline_denied_count": baseline_denied,
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

    def _case_user_text(self, sample_id: Any) -> str:
        for case in self.document.cases:
            if case.sample_id == sample_id:
                return case.user_text
        raise ValueError(f"unknown sample: {sample_id}")

    def _turn_context(self, case: V3ExpectedCase) -> V3TurnContext:
        return V3TurnContext(
            request_id=f"v3-{case.sample_id}-{uuid4().hex[:8]}",
            trace_id=f"v3-trace-{case.sample_id}",
            user_id="v3-eval-user",
            pet_id="v3-eval-pet",
            session_id="v3-eval-session",
            task_key=case.sample_id,
            reference_time=self.document.reference_time,
            current_pet_subject=V3SubjectReference(
                reference_id="current_pet",
                entity_type=V3EntityType.CURRENT_PET,
                display_name="当前宠物",
            ),
            other_subjects=[
                V3SubjectReference.model_validate(item)
                for item in case.other_subjects
            ],
            verified_pet_profile=self.document.pet_profile,
        )

    def _next_analyzer(self) -> InputPreprocessingV3Analyzer | None:
        return (
            self.analyzer_factory()
            if self.analyzer_factory is not None
            else self.analyzer
        )



def load_v3_experiment_document(path: Path) -> V3ExperimentDocument:
    """Load and validate a versioned third-round fixture."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = V3ExperimentDocument.model_validate(raw)
        for case in document.cases:
            V3Stage1Output.model_validate(case.golden_stage1)
        sample_ids = {case.sample_id for case in document.cases}
        for spec in document.experiments:
            unknown = set(spec.sample_ids) - sample_ids
            if unknown:
                raise ValueError(f"unknown experiment samples: {sorted(unknown)}")
        mutation_samples = {
            mutation["base_sample_id"] for mutation in document.negative_mutations
        }
        unknown_mutations = mutation_samples - sample_ids
        if unknown_mutations:
            raise ValueError(f"unknown mutation samples: {sorted(unknown_mutations)}")
        return document
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid V3 experiment document: {path}") from exc


def write_v3_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    fixed_versions: dict[str, str] | None = None,
) -> Path:
    """Write a V3 report with fixed architecture and phase metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v3-{uuid4().hex[:12]}.json"
    passed = sum(item["status"] == "passed" for item in reports)
    versions = fixed_versions or {}
    for report in reports:
        report["fixed_versions"] = versions
    payload = {
        "schema_version": "v3",
        "generated_at": datetime.now().astimezone().isoformat(),
        "architecture_version": "input-preprocessing-v3",
        "summary": {
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "fixed_versions": versions,
        "experiments": reports,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _stage1_matches(
    case: V3ExpectedCase,
    stage1: V3Stage1Output,
) -> dict[str, Any]:
    expected = [
        _expected_stage1_signature(item) for item in case.expected_evidence
    ]
    actual = [_actual_stage1_signature(item) for item in iter_v3_items(stage1)]
    expected_sorted = sorted(expected, key=json.dumps)
    actual_sorted = sorted(actual, key=json.dumps)
    return {
        "matched": expected_sorted == actual_sorted,
        "expected": expected_sorted,
        "actual": actual_sorted,
    }


def _stage2_matches(
    case: V3ExpectedCase,
    stage2: V3Stage2Output,
) -> dict[str, Any]:
    expected = [
        _expected_stage2_signature(item) for item in case.expected_evidence
    ]
    actual = [_actual_stage2_signature(item) for item in stage2.observations]
    expected_sorted = sorted(expected, key=json.dumps)
    actual_sorted = sorted(actual, key=json.dumps)
    return {
        "matched": expected_sorted == actual_sorted,
        "expected": expected_sorted,
        "actual": actual_sorted,
    }


def _expected_stage1_signature(item: V3ExpectedEvidence) -> dict[str, Any]:
    return {
        "source_text": item.surface_text,
        "assertion": item.assertion,
        "subject_reference": item.subject_reference,
        "subject_type": item.subject_type,
        "participants": _normalized_participants(item.participants),
    }


def _actual_stage1_signature(item: V3ItemContext) -> dict[str, Any]:
    return {
        "source_text": item.source_text,
        "assertion": item.initial_assertion,
        "subject_reference": item.subject.reference_id,
        "subject_type": item.subject.entity_type.value,
        "participants": _normalized_participants(
            [participant.model_dump(mode="json") for participant in item.participants]
        ),
    }


def _expected_stage2_signature(item: V3ExpectedEvidence) -> dict[str, Any]:
    return _expected_stage1_signature(item) | {
        "mapping_status": item.mapping_status,
        "canonical_id": item.canonical_id,
        "review_required": item.review_required,
        "assertion_verification": "verified",
    }


def _actual_stage2_signature(item: V3VerifiedEvidence) -> dict[str, Any]:
    return {
        "source_text": item.source_text,
        "assertion": item.initial_assertion.value,
        "subject_reference": item.subject.reference_id,
        "subject_type": item.subject.entity_type.value,
        "participants": _normalized_participants(
            [participant.model_dump(mode="json") for participant in item.participants]
        ),
        "mapping_status": item.mapping_status.value,
        "canonical_id": item.canonical_id,
        "review_required": item.review_required,
        "assertion_verification": item.assertion_verification.value,
    }


def _normalized_participants(
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for participant in participants:
        entity = participant.get("entity", participant)
        normalized.append(
            {
                "role": participant.get("role", participant.get("participant_role")),
                "reference_id": entity.get("reference_id")
                or participant.get("reference"),
                "entity_type": entity.get("entity_type")
                or participant.get("entity_type"),
                "resolution_status": entity.get(
                    "resolution_status", participant.get("resolution_status")
                ),
                "subject_candidates": entity.get(
                    "subject_candidates", participant.get("subject_candidates", [])
                ),
            }
        )
    return sorted(normalized, key=json.dumps)


def _apply_negative_mutation(
    result: V3InputAnalysisResult,
    mutation_id: str,
) -> V3InputAnalysisResult:
    observations = [item.model_copy(deep=True) for item in result.stage2.observations]
    candidate_sets = [
        item.model_copy(deep=True) for item in result.candidate_sets
    ]
    if not observations:
        raise ValueError(f"mutation requires evidence: {mutation_id}")
    first = observations[0]
    if mutation_id == "selected_candidate_not_in_candidate_set":
        first.selected_candidate_id = "c-invalid"
    elif mutation_id == "confirmed_without_candidates":
        first.candidates = []
        first.selected_candidate_id = None
        for candidate_set in candidate_sets:
            if (candidate_set.segment_id, candidate_set.item_id) == (
                first.segment_id,
                first.item_id,
            ):
                candidate_set.candidates = []
                candidate_set.recall_status = "no_candidate"
    elif mutation_id == "invented_canonical":
        first.canonical_id = "invented_canonical_zzz"
    elif mutation_id == "expected_item_missing" and len(observations) > 1:
        observations.pop()
    elif mutation_id == "stage2_items_merged" and len(observations) > 1:
        target = observations[0]
        observations[1].segment_id = target.segment_id
        observations[1].item_id = target.item_id
        observations[1].evidence_id = target.evidence_id
    elif mutation_id == "participants_replaced":
        first.participants = []
        first.participant_verification = V3ParticipantVerification.MISMATCH
    elif mutation_id == "stage2_assertion_not_verified":
        first.assertion_verification = V3AssertionVerification.MISMATCH
    elif mutation_id == "unmapped_review_missing":
        first.review_required = False
    else:
        raise ValueError(f"unknown negative mutation: {mutation_id}")

    return result.model_copy(
        deep=True,
        update={
            "stage2": V3Stage2Output(observations=observations),
            "candidate_sets": candidate_sets,
        },
    )


def _evaluate_experiment(
    spec: V3ExperimentSpec,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    failures: list[str] = []
    if not reports:
        failures.append("experiment_reports_missing")
    elif spec.kind == V3ExperimentKind.NEG:
        if not all(item["status"] == "gate_blocked_as_expected" for item in reports):
            failures.append("negative_not_blocked")
    elif spec.kind == V3ExperimentKind.REP:
        if not all(item["status"] == "passed" for item in reports):
            failures.append("repeat_turn_failed")
        signatures_by_sample: dict[str, set[str]] = {}
        for item in reports:
            signature = json.dumps(
                item.get("semantic_signature"),
                ensure_ascii=False,
                sort_keys=True,
            )
            signatures_by_sample.setdefault(item["sample_id"], set()).add(signature)
        if any(len(values) > 1 for values in signatures_by_sample.values()):
            failures.append("semantic_signature_drift")
    elif not all(item["status"] == "passed" for item in reports):
        failures.append("architecture_turn_failed")

    signatures_by_sample = _signatures_by_sample(reports)
    metrics = _experiment_metrics(spec, reports)
    return {
        "status": "failed" if failures else "passed",
        "failures": failures,
        "exit_criteria": {
            "all_turns_passed": bool(reports)
            and all(item["status"] == "passed" for item in reports),
            "blocking_gate_count": sum(
                len(item.get("blocking_gates", [])) for item in reports
            ),
            "unique_output_count": max(
                (len(signatures) for signatures in signatures_by_sample.values()),
                default=0,
            ),
            "negative_gate_blocked": (
                all(item["status"] == "gate_blocked_as_expected" for item in reports)
                if spec.kind == V3ExperimentKind.NEG
                else None
            ),
        },
        "metrics": metrics,
    }


def _experiment_metrics(
    spec: V3ExperimentSpec,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "turn_count": len(reports),
        "passed_turn_count": sum(item.get("status") == "passed" for item in reports),
        "gate_blocked_count": sum(
            item.get("status") in {"gate_blocked", "gate_blocked_as_expected"}
            for item in reports
        ),
        "median_turn_latency_ms": (
            int(median(item["latency_ms"] for item in reports)) if reports else 0
        ),
    }
    if spec.kind == V3ExperimentKind.NEG:
        metrics["gate_blocked_as_expected_rate"] = (
            sum(item.get("status") == "gate_blocked_as_expected" for item in reports)
            / len(reports)
            if reports
            else 0
        )
        metrics["false_pass_rate"] = (
            sum(item.get("status") != "gate_blocked_as_expected" for item in reports)
            / len(reports)
            if reports
            else 0
        )
        return metrics
    if spec.kind == V3ExperimentKind.ASYNC:
        metrics.update(
            {
                "queue_drop_rate": sum(
                    item.get("status") == "passed" for item in reports
                )
                / len(reports)
                if reports
                else 0,
                "dead_letter_count": sum(
                    item.get("dead_letter_count", 0) for item in reports
                ),
                "worker_failure_count": sum(
                    item.get("worker_status") == "completed_and_dead_letter"
                    for item in reports
                ),
                "trace_incomplete_count": sum(
                    not item.get("trace_complete", False) for item in reports
                ),
            }
        )
        return metrics

    metric_keys = (
        "schema_valid_count",
        "expected_evidence_count",
        "handled_evidence_count",
        "item_coverage",
        "scope_item_recall",
        "scope_item_precision",
        "profile_count_consistency_rate",
        "intent_fact_separation_rate",
        "subject_wrong_binding_count",
        "participant_retention",
        "confirmed_without_candidates_count",
        "invented_canonical_count",
        "unmapped_review_rate",
        "normal_as_denied_count",
        "denied_as_present_count",
    )
    for key in metric_keys:
        values = [item.get("metrics", {}).get(key) for item in reports]
        if (
            key.endswith("_rate")
            or key
            in {
                "item_coverage",
                "participant_retention",
                "scope_item_recall",
                "scope_item_precision",
            }
        ):
            metrics[key] = (
                sum(value for value in values if value is not None)
                / sum(value is not None for value in values)
                if any(value is not None for value in values)
                else 0
            )
        else:
            metrics[key] = sum(value for value in values if value is not None)
    metrics["unique_output_count"] = max(
        (
            len(signatures)
            for signatures in _signatures_by_sample(reports).values()
        ),
        default=0,
    )
    return metrics


def _signatures_by_sample(reports: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for item in reports:
        if item.get("semantic_signature") is None:
            continue
        signature = json.dumps(
            item["semantic_signature"],
            ensure_ascii=False,
            sort_keys=True,
        )
        result.setdefault(item["sample_id"], set()).add(signature)
    return result


def _result_signature(result: V3InputAnalysisResult) -> list[dict[str, Any]]:
    return [_actual_stage2_signature(item) for item in result.stage2.observations]


def _result_metrics(result: V3InputAnalysisResult) -> dict[str, Any]:
    stage1_items = list(iter_v3_items(result.stage1))
    observations = result.stage2.observations
    expected_keys = {
        (item.segment_id, item.item_id) for item in stage1_items
    }
    actual_keys = [
        (item.segment_id, item.item_id) for item in observations
    ]
    stage1_participants = {
        f"{item.segment_id}:{item.item_id}": _participant_tuple(item)
        for item in stage1_items
    }
    stage2_participants = {
        f"{item.segment_id}:{item.item_id}": _participant_tuple(item)
        for item in observations
    }
    retained = sum(
        stage1_participants.get(key) == value
        for key, value in stage2_participants.items()
    )
    valid_canonical_ids = {
        candidate.canonical_id
        for candidate_set in result.candidate_sets
        for candidate in candidate_set.candidates
    }
    return {
        "schema_valid_count": 1,
        "segment_count": len(result.stage1.segments),
        "expected_evidence_count": result.stage1.profile.expected_fact_candidate_count,
        "handled_evidence_count": len(observations),
        "item_coverage": len(set(actual_keys) & expected_keys)
        / len(expected_keys)
        if expected_keys
        else 1.0,
        "scope_item_recall": len(set(actual_keys) & expected_keys)
        / len(expected_keys)
        if expected_keys
        else 1.0,
        "scope_item_precision": len(set(actual_keys) & expected_keys)
        / len(set(actual_keys))
        if actual_keys
        else 1.0,
        "profile_count_consistency_rate": float(
            result.stage1.profile.expected_fact_candidate_count
            == sum(
                segment.expected_evidence_count
                for segment in result.stage1.segments
                if segment.requires_evidence_analysis
            )
        ),
        "intent_fact_separation_rate": float(
            not any(
                segment.discourse_role.value == "control_intent"
                for segment in result.stage1.segments
            )
        ),
        "subject_wrong_binding_count": sum(
            gate.gate_id == "v3_entity_subject_role"
            and gate.status == V3QualityGateStatus.FAILED
            for gate in result.gates
        ),
        "participant_retention": retained
        / len(stage1_participants)
        if stage1_participants
        else 1.0,
        "confirmed_count": sum(
            item.mapping_status == V3CanonicalMappingStatus.CONFIRMED
            for item in observations
        ),
        "confirmed_without_candidates_count": sum(
            item.mapping_status == V3CanonicalMappingStatus.CONFIRMED
            and not item.candidates
            for item in observations
        ),
        "invented_canonical_count": sum(
            item.mapping_status == V3CanonicalMappingStatus.CONFIRMED
            and item.canonical_id not in valid_canonical_ids
            for item in observations
        ),
        "unmapped_count": sum(
            item.mapping_status
            in {
                V3CanonicalMappingStatus.NOT_FOUND,
                V3CanonicalMappingStatus.UNMAPPED_MENTION,
                V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            for item in observations
        ),
        "unmapped_review_rate": (
            sum(
                item.mapping_status
                in {
                    V3CanonicalMappingStatus.NOT_FOUND,
                    V3CanonicalMappingStatus.UNMAPPED_MENTION,
                    V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                and item.review_required
                for item in observations
            )
            / sum(
                item.mapping_status
                in {
                    V3CanonicalMappingStatus.NOT_FOUND,
                    V3CanonicalMappingStatus.UNMAPPED_MENTION,
                    V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                for item in observations
            )
            if any(
                item.mapping_status
                in {
                    V3CanonicalMappingStatus.NOT_FOUND,
                    V3CanonicalMappingStatus.UNMAPPED_MENTION,
                    V3CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                for item in observations
            )
            else 1.0
        ),
        "normal_as_denied_count": sum(
            item.initial_assertion == V3AssertionState.DENIED
            and item.canonical_id in {"mental_status", "appetite", "water_intake"}
            for item in observations
        ),
        "denied_as_present_count": sum(
            item.initial_assertion == V3AssertionState.PRESENT
            and item.canonical_id in {"vomiting", "bloody_stool"}
            for item in observations
        ),
        "current_pet_confirmed_present_count": sum(
            item.mapping_status == V3CanonicalMappingStatus.CONFIRMED
            and item.initial_assertion in {V3AssertionState.PRESENT, V3AssertionState.ABNORMAL}
            and item.subject.entity_type == V3EntityType.CURRENT_PET
            for item in observations
        ),
        "current_pet_confirmed_denied_count": sum(
            item.mapping_status == V3CanonicalMappingStatus.CONFIRMED
            and item.initial_assertion == V3AssertionState.DENIED
            and item.subject.entity_type == V3EntityType.CURRENT_PET
            for item in observations
        ),
        "review_required_count": sum(item.review_required for item in observations),
        "blocking_gate_count": len(result.failed_blocking_gates()),
    }


def _participant_tuple(
    item: Any,
) -> tuple[tuple[str, Any, Any, Any], ...]:
    values = [
        (
            participant.role.value,
            participant.entity.reference_id,
            participant.entity.entity_type.value,
            participant.entity.resolution_status.value,
        )
        for participant in item.participants
    ]
    subject = (
        "subject",
        item.subject.reference_id,
        item.subject.entity_type.value,
        item.subject.resolution_status.value,
    )
    return (subject, *values)


def _snapshot_payload(snapshot: AsyncShadowSnapshotV3) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "sample_id": snapshot.sample_id,
        "user_text": snapshot.user_text,
        "turn_context": snapshot.turn_context.model_dump(mode="json"),
    }


def _file_digest(path: Path) -> str:
    """Return a stable fixture digest for report reproducibility."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_class(canonical_type: str) -> V3SemanticClass:
    return {
        "symptom": V3SemanticClass.STATE,
        "status": V3SemanticClass.STATE,
        "intake_output": V3SemanticClass.STATE,
        "behavior": V3SemanticClass.STATE,
        "intervention": V3SemanticClass.ACTION,
        "exposure": V3SemanticClass.EVENT,
        "measurement": V3SemanticClass.MEASUREMENT,
        "question_intent": V3SemanticClass.QUESTION,
    }.get(canonical_type, V3SemanticClass.STATE)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("tests/fixtures/input_preprocessing/third_round_shadow_matrix.json"),
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
        "--phase",
        choices=("exploratory", "confirmatory"),
        default="exploratory",
    )
    parser.add_argument(
        "--model", default=os.getenv("INPUT_PREPROCESSING_V3_MODEL", "qwen-plus")
    )
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--repeat-override", type=int, default=None)
    parser.add_argument("--with-clinical-baseline", action="store_true")
    parser.add_argument(
        "--async-queue-directory",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v3-async-shadow"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v3-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    document = load_v3_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    modes: list[Literal["ideal", "shadow"]] = (
        ["ideal", "shadow"] if args.mode == "both" else [args.mode]
    )
    analyzer: InputPreprocessingV3Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV3Analyzer] | None = None
    clinical_baseline: V3ClinicalBaselineAgent | None = None
    candidate_retriever: V3CandidateRetriever | None = None
    if "shadow" in modes:
        from vet_agent.clinical_safety import ClinicalSafetySemanticExtractorAgent
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()
        embeddings = QwenEmbeddingClient(settings)
        candidate_retriever = V3CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=embeddings,
            candidate_limit=int(
                os.getenv("INPUT_PREPROCESSING_V3_CANDIDATE_LIMIT", "8")
            ),
            minimum_score=float(
                os.getenv("INPUT_PREPROCESSING_V3_CANDIDATE_MIN_SCORE", "0.72")
            ),
        )

        def build_analyzer() -> InputPreprocessingV3Analyzer:
            return InputPreprocessingV3Analyzer(
                qwen=QwenClient(settings),
                vocabulary=vocabulary,
                candidate_retriever=candidate_retriever,
                model=args.model,
            )

        analyzer_factory = build_analyzer
        if args.with_clinical_baseline:
            clinical_baseline = ClinicalSafetySemanticExtractorAgent(
                QwenClient(settings),
                settings,
            )
    runner = V3ArchitectureValidationRunner(
        document=document,
        vocabulary=vocabulary,
        analyzer=analyzer,
        analyzer_factory=analyzer_factory,
        clinical_baseline=clinical_baseline,
        model=args.model,
        async_queue_directory=args.async_queue_directory,
    )
    reports: list[dict[str, Any]] = []
    only = set(args.experiment) if args.experiment else None
    for mode in modes:
        reports.extend(
            await runner.run(
                mode=mode,
                phase=args.phase,
                only_experiment_ids=only,
                repeat_override=args.repeat_override,
            )
        )
    path = write_v3_experiment_report(
        output_dir=args.output_dir,
        reports=reports,
        fixed_versions={
            "model": args.model,
            "prompt_version": os.getenv(
                "INPUT_PREPROCESSING_V3_PROMPT_VERSION",
                "v3-dev-20260825-5",
            ),
            "schema_version": "v3",
            "vocabulary_version": vocabulary.version,
            "candidate_recall_version": (
                candidate_retriever.recall_version
                if candidate_retriever is not None
                else "ideal-control"
            ),
            "fixture_version": _file_digest(args.matrix),
            "gate_version": os.getenv(
                "INPUT_PREPROCESSING_V3_GATE_VERSION",
                "v3-gates-20260825-2",
            ),
            "analyzer_isolation_policy": (
                "per-case-fresh-qwen-client-shared-candidate-retriever"
            ),
        },
    )
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
