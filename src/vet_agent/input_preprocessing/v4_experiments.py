"""Fourth-round V4 flat quote-anchored architecture validation runner."""

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
from .v4_analyzer import InputPreprocessingV4Analyzer
from .v4_candidate_linker import V4CandidateRetriever
from .v4_contracts import (
    FlatExtractionRawOutput,
    FlatObservationRaw,
    GovernedFlatObservation,
    V4CanonicalCandidate,
    V4CanonicalMappingStatus,
    V4EntityType,
    V4InputAnalysisResult,
    V4InputProfileRaw,
    V4ObservationStatus,
    V4ParticipantBinding,
    V4PreviousQuestionTarget,
    V4QualityGateStatus,
    V4ResolutionStatus,
    V4SubjectReference,
    V4TurnContext,
    V4TurnIntentRaw,
)
from .v4_gates import evaluate_v4_quality_gates
from .v4_projection import project_clinical_safety_report, project_consultation_facts
from .v4_quote_governance import normalize_quote_text
from .vocabulary import CanonicalVocabulary


class V4ExperimentKind:
    """Finite string constants for the fourth-round matrix."""

    FLAT_SCHEMA = "flat_schema"
    QUOTE_GATE = "quote_gate"
    SHARED_COVERAGE = "shared_coverage"
    SUBJECT_ROLE = "subject_role"
    ASSERT_TEMPORAL = "assert_temporal"
    CAN_RECALL = "can_recall"
    CAN_SELECT = "can_select"
    CAN_TYPE = "can_type"
    DIRTY_INPUT = "dirty_input"
    MULTI_TURN = "multi_turn"
    ANSWER_NOW = "answer_now"
    EMPTY_BASE = "empty_base"
    NEG = "neg"
    REP = "repeat_stability"
    ASYNC = "async"
    DOMAIN_PROJECTION = "domain_projection"
    CS = "cs_report_only"


class V4ExpectedObservation(BaseModel):
    """Expected golden semantic signature for one flat observation."""

    model_config = ConfigDict(extra="forbid")

    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    semantic_class: str = Field(min_length=1, max_length=40)
    assertion: str = Field(min_length=1, max_length=40)
    certainty: str = Field(default="explicit", max_length=40)
    subject_reference: str = Field(min_length=1, max_length=64)
    participants: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    object_mention: str = Field(default="", max_length=160)
    temporal_quote: str = Field(default="", max_length=160)
    temporal_relation: str = Field(default="unstructured", max_length=40)
    temporal_value: str = Field(default="", max_length=160)
    temporal_precision: str = Field(default="unresolved", max_length=40)
    temporal_status: str = Field(default="not_applicable", max_length=40)
    measurement_quote: str = Field(default="", max_length=160)
    measurement_value: str = Field(default="", max_length=160)
    measurement_unit: str = Field(default="", max_length=64)
    measurement_status: str = Field(default="not_applicable", max_length=40)
    canonical_surface: str = Field(min_length=1, max_length=160)
    mapping_status: str = Field(min_length=1, max_length=40)
    canonical_id: str | None = Field(default=None, max_length=96)
    review_required: bool = False


class V4ExpectedCase(BaseModel):
    """One development or held-out V4 sample."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    user_text: str = Field(min_length=1, max_length=12000)
    other_subjects: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    previous_question_target: dict[str, Any] | None = None
    expected_intent: dict[str, bool]
    profile: dict[str, bool]
    expected_observations: list[V4ExpectedObservation] = Field(
        default_factory=list, max_length=128
    )


class V4ExperimentSpec(BaseModel):
    """One controlled V4 architecture experiment."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    architecture_question: str = Field(min_length=1, max_length=500)
    control_group: str = Field(min_length=1, max_length=240)
    experimental_group: str = Field(min_length=1, max_length=240)
    sample_ids: list[str] = Field(min_length=1, max_length=32)
    repeat_count: int = Field(default=3, ge=1, le=10)
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


class V4ExperimentDocument(BaseModel):
    """Versioned fourth-round fixture contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v4"]
    dataset_type: Literal["development", "held_out"]
    reference_time: datetime
    pet_profile: dict[str, Any]
    cases: list[V4ExpectedCase] = Field(min_length=1)
    experiments: list[V4ExperimentSpec] = Field(min_length=1)
    negative_mutations: list[dict[str, str]] = Field(min_length=1)


class V4ClinicalBaselineAgent(Protocol):
    """Minimal existing clinical-safety semantic interface."""

    async def extract(
        self,
        *,
        user_text: str,
        pet_context_summary: str,
        model: str,
    ) -> Any: ...


@dataclass(frozen=True)
class AsyncShadowSnapshotV4:
    """A durable report-only V4 API shadow snapshot."""

    snapshot_id: str
    sample_id: str
    user_text: str
    turn_context: V4TurnContext


@dataclass(frozen=True)
class AsyncShadowSubmitResultV4:
    """Explicit nonblocking enqueue result."""

    accepted: bool
    reason: Literal["accepted", "queue_full", "not_sampled"]
    latency_ms: int
    snapshot_id: str


class FileAsyncShadowQueueV4:
    """Experiment-only durable bounded queue with explicit dead letters."""

    def __init__(self, *, directory: Path, max_size: int = 2) -> None:
        self.directory = directory
        self.max_size = max_size
        self.directory.mkdir(parents=True, exist_ok=True)

    def submit(self, snapshot: AsyncShadowSnapshotV4) -> AsyncShadowSubmitResultV4:
        started = time.perf_counter()
        if len(self._records(status={"pending", "running"})) >= self.max_size:
            return AsyncShadowSubmitResultV4(
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
        (self.directory / f"{snapshot.snapshot_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AsyncShadowSubmitResultV4(
            accepted=True,
            reason="accepted",
            latency_ms=_elapsed_ms(started),
            snapshot_id=snapshot.snapshot_id,
        )

    def claim(self) -> AsyncShadowSnapshotV4 | None:
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
        return AsyncShadowSnapshotV4(
            snapshot_id=raw["snapshot_id"],
            sample_id=raw["sample_id"],
            user_text=raw["user_text"],
            turn_context=V4TurnContext.model_validate(raw["turn_context"]),
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
            "dead_letter" if record["attempt_count"] >= max_attempts else "failed"
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
        (self.directory / f"{record['snapshot_id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class V4ArchitectureValidationRunner:
    """Run controlled V4 experiments without consuming report-only results."""

    document: V4ExperimentDocument
    vocabulary: CanonicalVocabulary
    analyzer: InputPreprocessingV4Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV4Analyzer] | None = None
    clinical_baseline: V4ClinicalBaselineAgent | None = None
    model: str = "qwen-plus"
    async_queue_directory: Path = Path(
        ".data/evaluations/input-preprocessing-v4-async-shadow"
    )
    _turn_cache: dict[tuple[Any, ...], dict[str, Any]] = field(
        default_factory=dict, init=False
    )

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        phase: Literal["exploratory", "confirmatory"] = "exploratory",
        only_experiment_ids: set[str] | None = None,
        repeat_override: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run selected V4 experiments and return versioned report objects."""

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
        spec: V4ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
        phase: Literal["exploratory", "confirmatory"],
        repeat_override: int | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if spec.kind == V4ExperimentKind.NEG:
            turns = self._run_negative_mutations()
        elif spec.kind == V4ExperimentKind.ASYNC:
            turns = self._run_async_shadow(spec, mode=mode)
        else:
            cases = {case.sample_id: case for case in self.document.cases}
            turns = []
            repeat = repeat_override or spec.repeat_count
            for sample_id in spec.sample_ids:
                case = cases.get(sample_id)
                if case is None:
                    raise ValueError(f"unknown sample: {sample_id}")
                for attempt in range(1, repeat + 1):
                    cache_key = (mode, phase, sample_id, attempt)
                    if cache_key not in self._turn_cache:
                        self._turn_cache[cache_key] = await self._run_case(
                            case=case,
                            mode=mode,
                            attempt=attempt,
                            analyzer=self._next_analyzer(),
                        )
                    turns.append(self._turn_cache[cache_key])

        evaluation = _evaluate_experiment(spec, turns)
        clinical_comparison = None
        if spec.kind == V4ExperimentKind.CS:
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
            "architecture_version": "input-preprocessing-v4",
            "architecture_question": spec.architecture_question,
            "control_group": spec.control_group,
            "experimental_group": spec.experimental_group,
            "kind": spec.kind,
            "mode": mode,
            "phase": phase,
            "dataset_type": self.document.dataset_type,
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
        case: V4ExpectedCase,
        mode: Literal["ideal", "shadow"],
        attempt: int,
        analyzer: InputPreprocessingV4Analyzer | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        turn_context = self._turn_context(case)
        if mode == "ideal":
            result = self._ideal_result(case, turn_context)
            model_error = None
        else:
            analyzer = analyzer or self.analyzer
            if analyzer is None:
                raise ValueError("v4 shadow analyzer is required")
            try:
                result = await analyzer.analyze(
                    user_text=case.user_text,
                    turn_context=turn_context,
                )
                model_error = None
            except Exception as exc:  # noqa: BLE001 - report-only isolation
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
                "metrics": {
                    "schema_valid_count": 0,
                    "model_call_count": 0,
                },
                "latency_ms": _elapsed_ms(started),
            }

        match = _observations_match(case, result)
        intent_match = all(
            getattr(result.intent, key) is value
            for key, value in case.expected_intent.items()
        )
        blocking_gates = result.failed_blocking_gates()
        failures: list[str] = []
        if not intent_match:
            failures.append("intent_mismatch")
        if not match["matched"]:
            failures.append("flat_semantic_mismatch")
        failures.extend(
            f"blocking_gate:{gate.gate_id}:{gate.reason_code}"
            for gate in blocking_gates
        )
        status = "gate_blocked" if blocking_gates else (
            "failed" if failures else "passed"
        )
        consultation = project_consultation_facts(result)
        clinical = project_clinical_safety_report(result)
        return {
            "sample_id": case.sample_id,
            "attempt": attempt,
            "status": status,
            "failures": failures,
            "intent_match": intent_match,
            "semantic_match": match,
            "semantic_signature": _result_signature(result),
            "metrics": _result_metrics(result, case),
            "consultation_projection": consultation,
            "clinical_safety_projection": clinical,
            "result": result.model_dump(mode="json"),
            "blocking_gates": [
                gate.model_dump(mode="json") for gate in blocking_gates
            ],
            "latency_ms": _elapsed_ms(started),
        }

    def _ideal_result(
        self,
        case: V4ExpectedCase,
        turn_context: V4TurnContext,
    ) -> V4InputAnalysisResult:
        raw_observations = [
            self._raw_observation(
                expected=expected,
                index=index,
                turn_context=turn_context,
            )
            for index, expected in enumerate(case.expected_observations, start=1)
        ]
        raw = FlatExtractionRawOutput(
            intent=V4TurnIntentRaw.model_validate(case.expected_intent),
            profile=V4InputProfileRaw.model_validate(case.profile),
            observations=raw_observations,
        )
        observations = [
            self._ideal_governed_observation(
                raw=raw_observation,
                expected=expected,
                turn_context=turn_context,
            )
            for raw_observation, expected in zip(
                raw.observations, case.expected_observations, strict=True
            )
        ]
        result = V4InputAnalysisResult(
            turn_context=turn_context,
            intent=raw.intent,
            profile=raw.profile,
            raw_observations=raw.observations,
            observations=observations,
            model_name="golden_v4",
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version="ideal-control",
            quote_normalization_version="ideal-control",
            stage_latency_ms={"flat_extraction": 0, "deterministic_governance": 0},
            stage_attempts={"flat_extraction": 1},
        )
        result.gates = evaluate_v4_quality_gates(result=result)
        return result

    def _raw_observation(
        self,
        *,
        expected: V4ExpectedObservation,
        index: int,
        turn_context: V4TurnContext,
    ) -> FlatObservationRaw:
        reference = turn_context.entity_references()[expected.subject_reference]
        method = (
            "previous_question_target"
            if (
                turn_context.previous_question_target is not None
                and expected.target_quote in {"没有", "没"}
            )
            else (
                "trusted_current_pet"
                if reference.entity_type == V4EntityType.CURRENT_PET
                else "explicit_coreference"
            )
        )
        return FlatObservationRaw(
            observation_id=f"obs-{index}",
            evidence_quote=expected.evidence_quote,
            target_quote=expected.target_quote,
            event_or_state_text=expected.canonical_surface,
            semantic_class=expected.semantic_class,  # type: ignore[arg-type]
            assertion=expected.assertion,  # type: ignore[arg-type]
            certainty=expected.certainty,  # type: ignore[arg-type]
            subject_reference=expected.subject_reference,
            subject_type=reference.entity_type,
            subject_resolution_method=method,  # type: ignore[arg-type]
            subject_resolution_status=V4ResolutionStatus.RESOLVED,
            action_agent_reference=next(
                (
                    item["reference_id"]
                    for item in expected.participants
                    if item["role"] == "action_agent"
                ),
                None,
            ),
            action_recipient_reference=next(
                (
                    item["reference_id"]
                    for item in expected.participants
                    if item["role"] == "action_recipient"
                ),
                None,
            ),
            experiencer_reference=next(
                (
                    item["reference_id"]
                    for item in expected.participants
                    if item["role"] == "experiencer"
                ),
                None,
            ),
            object_mention=expected.object_mention,
            temporal_quote=expected.temporal_quote,
            temporal_relation=expected.temporal_relation,  # type: ignore[arg-type]
            temporal_value=expected.temporal_value,
            temporal_precision=expected.temporal_precision,  # type: ignore[arg-type]
            temporal_status=expected.temporal_status,  # type: ignore[arg-type]
            measurement_quote=expected.measurement_quote,
            measurement_value=expected.measurement_value,
            measurement_unit=expected.measurement_unit,
            measurement_status=expected.measurement_status,  # type: ignore[arg-type]
            canonical_surface=expected.canonical_surface,
            confidence=0.99,
        )

    def _ideal_governed_observation(
        self,
        *,
        raw: FlatObservationRaw,
        expected: V4ExpectedObservation,
        turn_context: V4TurnContext,
    ) -> GovernedFlatObservation:
        from .v4_quote_governance import resolve_observation_quotes

        evidence, target, temporal, measurement = resolve_observation_quotes(
            user_text=self._case_user_text_for_expected(expected),
            raw=raw,
        )
        subject = self._entity_binding(
            reference_id=raw.subject_reference,
            declared_type=raw.subject_type,
            method=raw.subject_resolution_method,
            status=raw.subject_resolution_status,
            candidates=raw.subject_candidates,
            confidence=raw.confidence,
            turn_context=turn_context,
        )
        participants = self._participants(raw, turn_context)
        candidate = None
        if expected.canonical_id in self.vocabulary.term_map():
            term = self.vocabulary.term_map()[expected.canonical_id]
            candidate = V4CanonicalCandidate(
                candidate_id="c-1",
                canonical_id=term.canonical_id,
                canonical_type=term.canonical_type,
                semantic_class=raw.semantic_class,
                surface_form=(
                    expected.canonical_surface
                    if expected.canonical_surface in term.aliases
                    else term.aliases[0]
                ),
                score=1.0,
                recall_source="ideal_control",
            )
        from .v4_contracts import V4CandidateSet

        candidate_set = V4CandidateSet(
            observation_id=raw.observation_id,
            canonical_surface=raw.canonical_surface,
            candidates=[candidate] if candidate is not None else [],
            recall_status="recalled" if candidate is not None else "no_candidate",
            recall_version="ideal-control",
        )
        return GovernedFlatObservation(
            observation_id=raw.observation_id,
            evidence_quote=evidence,
            target_quote=target,
            event_or_state_text=raw.event_or_state_text,
            semantic_class=raw.semantic_class,
            assertion=raw.assertion,
            certainty=raw.certainty,
            subject=subject,
            participants=participants,
            object_mention=raw.object_mention,
            temporal_quote=temporal,
            temporal_relation=raw.temporal_relation,
            temporal_value=raw.temporal_value,
            temporal_precision=raw.temporal_precision,
            temporal_status=raw.temporal_status,
            measurement_quote=measurement,
            measurement_value=raw.measurement_value,
            measurement_unit=raw.measurement_unit,
            measurement_status=raw.measurement_status,
            canonical_surface=raw.canonical_surface,
            candidate_set=candidate_set,
            selected_candidate_id="c-1" if candidate is not None else None,
            canonical_id=expected.canonical_id if candidate is not None else None,
            mapping_status=expected.mapping_status,  # type: ignore[arg-type]
            review_required=expected.review_required,
            confidence=raw.confidence,
        )

    def _case_user_text_for_expected(self, expected: V4ExpectedObservation) -> str:
        for case in self.document.cases:
            if any(item is expected for item in case.expected_observations):
                return case.user_text
        raise ValueError("expected observation is not attached to a case")

    def _entity_binding(
        self,
        *,
        reference_id: str | None,
        declared_type: Any,
        method: Any,
        status: Any,
        candidates: list[str],
        confidence: float,
        turn_context: V4TurnContext,
    ):
        from .v4_contracts import V4EntityBinding

        reference = turn_context.entity_references().get(reference_id or "")
        return V4EntityBinding(
            reference_id=reference_id,
            entity_type=reference.entity_type if reference else declared_type,
            resolution_method=method,
            resolution_status=status,
            subject_candidates=candidates,
            confidence=confidence,
        )

    def _participants(
        self,
        raw: FlatObservationRaw,
        turn_context: V4TurnContext,
    ) -> list[V4ParticipantBinding]:
        definitions = (
            ("action_agent", raw.action_agent_reference),
            ("action_recipient", raw.action_recipient_reference),
            ("experiencer", raw.experiencer_reference),
        )
        participants = [
            V4ParticipantBinding(
                role=role,  # type: ignore[arg-type]
                entity=self._entity_binding(
                    reference_id=reference_id,
                    declared_type=None,
                    method=raw.subject_resolution_method,
                    status=raw.subject_resolution_status,
                    candidates=raw.subject_candidates,
                    confidence=raw.confidence,
                    turn_context=turn_context,
                ),
            )
            for role, reference_id in definitions
            if reference_id is not None
        ]
        if raw.semantic_class.value == "action":
            from .v4_contracts import V4EntityBinding

            participants.append(
                V4ParticipantBinding(
                    role="action_object",
                    entity=V4EntityBinding(),
                )
            )
        return participants

    def _run_negative_mutations(self) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        cases = {case.sample_id: case for case in self.document.cases}
        for mutation in self.document.negative_mutations:
            case = cases.get(mutation["base_sample_id"])
            if case is None:
                raise ValueError(f"unknown mutation sample: {mutation['base_sample_id']}")
            result = self._ideal_result(case, self._turn_context(case))
            mutated = _apply_negative_mutation(result, mutation["mutation_id"])
            mutated.gates = evaluate_v4_quality_gates(result=mutated)
            blocking = [
                gate for gate in mutated.gates if gate.status == V4QualityGateStatus.FAILED
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

    def _run_async_shadow(
        self,
        spec: V4ExperimentSpec,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> list[dict[str, Any]]:
        cases = {case.sample_id: case for case in self.document.cases}
        sample_ids = spec.sample_ids[:3]
        queue = FileAsyncShadowQueueV4(
            directory=self.async_queue_directory / uuid4().hex[:8],
            max_size=2,
        )
        submissions: list[dict[str, Any]] = []
        for index, sample_id in enumerate(sample_ids, start=1):
            case = cases[sample_id]
            snapshot = AsyncShadowSnapshotV4(
                snapshot_id=f"{sample_id}-{uuid4().hex[:8]}",
                sample_id=sample_id,
                user_text=case.user_text,
                turn_context=self._turn_context(case),
            )
            submissions.append(queue.submit(snapshot).__dict__ | {"queue_slot": index})

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
            case = next(item for item in self.document.cases if item.sample_id == sample_id)
            result = self._ideal_result(case, self._turn_context(case))
            return {
                "status": "passed",
                "semantic_signature": _result_signature(result),
                "metrics": _result_metrics(result, case),
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
                    pet_context_summary=(
                        "犬，成年"
                        if self.document.pet_profile.get("species") == "dog"
                        else "猫，成年"
                    ),
                    model=self.model,
                )
                payload = baseline.model_dump(mode="json")
                baseline_present = sum(
                    item.get("state") == "present"
                    for item in payload.get("observed_features", [])
                )
                baseline_denied = sum(
                    item.get("state") == "denied"
                    for item in payload.get("observed_features", [])
                )
            except Exception as exc:  # noqa: BLE001 - report-only dependency
                baseline_error = {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
        metrics = turns[0].get("metrics", {}) if turns else {}
        return {
            "baseline_error": baseline_error,
            "baseline_present_count": baseline_present,
            "baseline_denied_count": baseline_denied,
            "new_projection_present_count": metrics.get(
                "current_pet_confirmed_present_count", 0
            ),
            "new_projection_denied_count": metrics.get(
                "current_pet_confirmed_denied_count", 0
            ),
            "downstream_evaluation": "not_implemented",
            "evaluator_called": False,
            "opa_called": False,
        }

    def _case_user_text(self, sample_id: Any) -> str:
        return next(
            case.user_text
            for case in self.document.cases
            if case.sample_id == sample_id
        )

    def _turn_context(self, case: V4ExpectedCase) -> V4TurnContext:
        return V4TurnContext(
            request_id=f"v4-{case.sample_id}",
            trace_id=f"trace-v4-{case.sample_id}",
            user_id="v4-test-user",
            pet_id="v4-test-pet",
            session_id="v4-test-session",
            reference_time=self.document.reference_time,
            current_pet_subject=V4SubjectReference(
                reference_id="current_pet",
                entity_type=V4EntityType.CURRENT_PET,
                display_name="当前宠物",
            ),
            other_subjects=[
                V4SubjectReference.model_validate(item)
                for item in case.other_subjects
            ],
            previous_question_target=(
                V4PreviousQuestionTarget.model_validate(case.previous_question_target)
                if case.previous_question_target is not None
                else None
            ),
            verified_pet_profile=self.document.pet_profile,
        )

    def _next_analyzer(self) -> InputPreprocessingV4Analyzer | None:
        return (
            self.analyzer_factory()
            if self.analyzer_factory is not None
            else self.analyzer
        )


def load_v4_experiment_document(path: Path) -> V4ExperimentDocument:
    """Load and validate a versioned fourth-round fixture."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = V4ExperimentDocument.model_validate(raw)
        sample_ids = {case.sample_id for case in document.cases}
        unknown = {
            sample_id
            for spec in document.experiments
            for sample_id in spec.sample_ids
        } - sample_ids
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
        raise ValueError(f"invalid V4 experiment document: {path}") from exc


def write_v4_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    fixed_versions: dict[str, str] | None = None,
) -> Path:
    """Write a V4 report with fixed architecture and phase metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v4-{uuid4().hex[:12]}.json"
    passed = sum(item["status"] == "passed" for item in reports)
    versions = fixed_versions or {}
    for report in reports:
        report["fixed_versions"] = versions
    payload = {
        "schema_version": "v4",
        "generated_at": datetime.now().astimezone().isoformat(),
        "architecture_version": "input-preprocessing-v4",
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


def _observations_match(
    case: V4ExpectedCase,
    result: V4InputAnalysisResult,
) -> dict[str, Any]:
    expected = [_expected_signature(item) for item in case.expected_observations]
    actual = [_actual_signature(item) for item in result.observations]
    expected_sorted = sorted(expected, key=json.dumps)
    actual_sorted = sorted(actual, key=json.dumps)
    expected_keys = {item["signature_key"] for item in expected}
    actual_keys = {item["signature_key"] for item in actual}
    return {
        "matched": expected_sorted == actual_sorted,
        "expected": expected_sorted,
        "actual": actual_sorted,
        "recall": (
            len(expected_keys & actual_keys) / len(expected_keys)
            if expected_keys
            else 1.0
        ),
        "precision": (
            len(expected_keys & actual_keys) / len(actual_keys)
            if actual_keys
            else (1.0 if not expected_keys else 0.0)
        ),
    }


def _expected_signature(item: V4ExpectedObservation) -> dict[str, Any]:
    return {
        "signature_key": _signature_key(
            evidence=item.evidence_quote,
            target=item.target_quote,
            subject=item.subject_reference,
        ),
        "evidence_quote": normalize_quote_text(item.evidence_quote),
        "target_quote": normalize_quote_text(item.target_quote),
        "semantic_class": item.semantic_class,
        "assertion": item.assertion,
        "certainty": item.certainty,
        "subject_reference": item.subject_reference,
        "participants": sorted(
            (participant["role"], participant["reference_id"])
            for participant in item.participants
        ),
        "canonical_surface": item.canonical_surface,
        "mapping_status": item.mapping_status,
        "canonical_id": item.canonical_id,
        "temporal_quote": normalize_quote_text(item.temporal_quote),
        "temporal_status": item.temporal_status,
        "measurement_quote": normalize_quote_text(item.measurement_quote),
        "measurement_status": item.measurement_status,
    }


def _actual_signature(item: GovernedFlatObservation) -> dict[str, Any]:
    return {
        "signature_key": _signature_key(
            evidence=item.evidence_quote.raw_quote,
            target=item.target_quote.raw_quote,
            subject=item.subject.reference_id or "",
        ),
        "evidence_quote": item.evidence_quote.normalized_quote,
        "target_quote": item.target_quote.normalized_quote,
        "semantic_class": item.semantic_class.value,
        "assertion": item.assertion.value,
        "certainty": item.certainty.value,
        "subject_reference": item.subject.reference_id,
        "participants": sorted(
            (
                participant.role,
                participant.entity.reference_id or "",
            )
            for participant in item.participants
            if participant.role != "action_object"
        ),
        "canonical_surface": item.canonical_surface,
        "mapping_status": item.mapping_status.value,
        "canonical_id": item.canonical_id,
        "temporal_quote": (
            item.temporal_quote.normalized_quote if item.temporal_quote else ""
        ),
        "temporal_status": item.temporal_status.value,
        "measurement_quote": (
            item.measurement_quote.normalized_quote if item.measurement_quote else ""
        ),
        "measurement_status": item.measurement_status.value,
    }


def _signature_key(
    *,
    evidence: str,
    target: str,
    subject: str,
) -> str:
    return "::".join(
        (
            normalize_quote_text(evidence),
            normalize_quote_text(target),
            subject,
        )
    )


def _result_signature(result: V4InputAnalysisResult) -> list[dict[str, Any]]:
    return [_actual_signature(item) for item in result.observations]


def _result_metrics(
    result: V4InputAnalysisResult,
    case: V4ExpectedCase,
) -> dict[str, Any]:
    match = _observations_match(case, result)
    expected = {
        _signature_key(
            evidence=item.evidence_quote,
            target=item.target_quote,
            subject=item.subject_reference,
        ): item
        for item in case.expected_observations
    }
    actual = {
        _signature_key(
            evidence=item.evidence_quote.raw_quote,
            target=item.target_quote.raw_quote,
            subject=item.subject.reference_id or "",
        ): item
        for item in result.observations
    }
    subject_wrong = 0
    assertion_wrong = 0
    normal_as_denied = 0
    denied_as_present = 0
    for key, expected_item in expected.items():
        actual_item = actual.get(key)
        if actual_item is None:
            continue
        if actual_item.subject.reference_id != expected_item.subject_reference:
            subject_wrong += 1
        if actual_item.assertion.value != expected_item.assertion:
            assertion_wrong += 1
        if (
            expected_item.assertion == "normal"
            and actual_item.assertion.value == "denied"
        ):
            normal_as_denied += 1
        if (
            expected_item.assertion == "denied"
            and actual_item.assertion.value == "present"
        ):
            denied_as_present += 1
    return {
        "schema_valid_count": 1,
        "model_call_count": 1,
        "attempt_count": sum(result.stage_attempts.values()),
        "observation_count": len(result.observations),
        "expected_observation_count": len(case.expected_observations),
        "observation_recall": match["recall"],
        "observation_precision": match["precision"],
        "quote_valid_rate": (
            sum(
                item.evidence_quote.status == "resolved"
                and item.target_quote.status == "resolved"
                for item in result.observations
            )
            / len(result.observations)
            if result.observations
            else 1.0
        ),
        "subject_wrong_binding_count": subject_wrong,
        "assertion_mismatch_count": assertion_wrong,
        "normal_as_denied_count": normal_as_denied,
        "denied_as_present_count": denied_as_present,
        "confirmed_without_candidates_count": sum(
            item.mapping_status == V4CanonicalMappingStatus.CONFIRMED
            and not item.candidate_set.candidates
            for item in result.observations
        ),
        "invented_canonical_count": sum(
            item.canonical_id is not None
            and not any(
                candidate.canonical_id == item.canonical_id
                for candidate in item.candidate_set.candidates
            )
            for item in result.observations
        ),
        "unmapped_review_rate": (
            sum(
                item.mapping_status
                in {
                    V4CanonicalMappingStatus.NOT_FOUND,
                    V4CanonicalMappingStatus.UNMAPPED_MENTION,
                    V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                and item.review_required
                for item in result.observations
            )
            / sum(
                item.mapping_status
                in {
                    V4CanonicalMappingStatus.NOT_FOUND,
                    V4CanonicalMappingStatus.UNMAPPED_MENTION,
                    V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                for item in result.observations
            )
            if any(
                item.mapping_status
                in {
                    V4CanonicalMappingStatus.NOT_FOUND,
                    V4CanonicalMappingStatus.UNMAPPED_MENTION,
                    V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
                }
                for item in result.observations
            )
            else 1.0
        ),
        "current_pet_confirmed_present_count": sum(
            item.mapping_status == V4CanonicalMappingStatus.CONFIRMED
            and item.assertion.value in {"present", "abnormal"}
            and item.subject.entity_type == V4EntityType.CURRENT_PET
            for item in result.observations
        ),
        "current_pet_confirmed_denied_count": sum(
            item.mapping_status == V4CanonicalMappingStatus.CONFIRMED
            and item.assertion.value == "denied"
            and item.subject.entity_type == V4EntityType.CURRENT_PET
            for item in result.observations
        ),
        "review_required_count": sum(item.review_required for item in result.observations),
        "blocking_gate_count": len(result.failed_blocking_gates()),
    }


def _apply_negative_mutation(
    result: V4InputAnalysisResult,
    mutation_id: str,
) -> V4InputAnalysisResult:
    mutated = result.model_copy(deep=True)
    observations = mutated.observations
    if not observations:
        raise ValueError(f"negative mutation requires observations: {mutation_id}")
    first = observations[0]
    if mutation_id == "evidence_quote_not_anchored":
        first.evidence_quote.raw_quote += "不存在"
        first.evidence_quote.status = "not_found"
    elif mutation_id == "target_quote_not_contained":
        first.target_quote.raw_quote = "不存在目标"
        first.target_quote.status = "not_found"
    elif mutation_id == "temporal_quote_not_anchored":
        if first.temporal_quote is None:
            first.temporal_quote = first.evidence_quote.model_copy(
                update={"raw_quote": "不存在时间", "status": "not_found"}
            )
        else:
            first.temporal_quote.raw_quote += "不存在"
            first.temporal_quote.status = "not_found"
        first.temporal_status = V4ObservationStatus.CONFIRMED_PRESENT
    elif mutation_id == "subject_not_in_turn_context":
        first.subject.reference_id = "ghost-entity"
    elif mutation_id == "participant_role_type_mismatch":
        if not first.participants:
            first.participants.append(
                V4ParticipantBinding(
                    role="action_agent",
                    entity=first.subject.model_copy(),
                )
            )
        else:
            first.participants[0].entity.reference_id = "current_pet"
            first.participants[0].entity.entity_type = V4EntityType.CURRENT_PET
    elif mutation_id == "confirmed_without_candidates":
        first.candidate_set.candidates = []
        first.mapping_status = V4CanonicalMappingStatus.CONFIRMED
        first.selected_candidate_id = "c-invalid"
        first.canonical_id = "invented_canonical"
        first.review_required = False
    elif mutation_id == "selected_candidate_not_in_candidate_set":
        first.selected_candidate_id = "c-invalid"
    elif mutation_id == "invented_canonical":
        first.canonical_id = "invented_canonical"
    elif mutation_id == "unmapped_review_missing":
        if first.mapping_status == V4CanonicalMappingStatus.CONFIRMED:
            first.mapping_status = V4CanonicalMappingStatus.UNMAPPED_MENTION
            first.canonical_id = None
            first.selected_candidate_id = None
        first.review_required = False
    elif mutation_id == "suspicious_empty":
        mutated.profile.has_factual_statements = True
        mutated.observations = []
    else:
        raise ValueError(f"unknown negative mutation: {mutation_id}")
    return mutated


def _evaluate_experiment(
    spec: V4ExperimentSpec,
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    failures: list[str] = []
    if not reports:
        failures.append("experiment_reports_missing")
    elif spec.kind == V4ExperimentKind.NEG:
        if not all(item["status"] == "gate_blocked_as_expected" for item in reports):
            failures.append("negative_not_blocked")
    elif spec.kind == V4ExperimentKind.REP:
        if not all(item["status"] == "passed" for item in reports):
            failures.append("repeat_turn_failed")
        signatures: dict[str, set[str]] = {}
        for item in reports:
            signature = json.dumps(
                item.get("semantic_signature"),
                ensure_ascii=False,
                sort_keys=True,
            )
            signatures.setdefault(item["sample_id"], set()).add(signature)
        if any(len(values) > 1 for values in signatures.values()):
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
                (len(values) for values in signatures_by_sample.values()),
                default=0,
            ),
            "negative_gate_blocked": (
                all(item["status"] == "gate_blocked_as_expected" for item in reports)
                if spec.kind == V4ExperimentKind.NEG
                else None
            ),
        },
        "metrics": metrics,
    }


def _experiment_metrics(
    spec: V4ExperimentSpec,
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
    if spec.kind == V4ExperimentKind.NEG:
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
    if spec.kind == V4ExperimentKind.ASYNC:
        metrics.update(
            {
                "queue_drop_rate": (
                    sum(item.get("status") == "passed" for item in reports)
                    / len(reports)
                    if reports
                    else 0
                ),
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

    average_keys = {
        "schema_valid_count",
        "observation_recall",
        "observation_precision",
        "quote_valid_rate",
        "unmapped_review_rate",
        "current_pet_confirmed_present_count",
        "current_pet_confirmed_denied_count",
    }
    sum_keys = {
        "model_call_count",
        "attempt_count",
        "expected_observation_count",
        "observation_count",
        "subject_wrong_binding_count",
        "assertion_mismatch_count",
        "normal_as_denied_count",
        "denied_as_present_count",
        "confirmed_without_candidates_count",
        "invented_canonical_count",
        "review_required_count",
        "blocking_gate_count",
    }
    for key in average_keys:
        values = [item.get("metrics", {}).get(key) for item in reports]
        metrics[key] = (
            sum(value for value in values if value is not None)
            / sum(value is not None for value in values)
            if any(value is not None for value in values)
            else 0
        )
    for key in sum_keys:
        metrics[key] = sum(
            item.get("metrics", {}).get(key, 0) for item in reports
        )
    metrics["unique_output_count"] = max(
        (len(values) for values in _signatures_by_sample(reports).values()),
        default=0,
    )
    return metrics


def _signatures_by_sample(reports: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for report in reports:
        signature = json.dumps(
            report.get("semantic_signature"),
            ensure_ascii=False,
            sort_keys=True,
        )
        result.setdefault(report.get("sample_id", ""), set()).add(signature)
    return result


def _snapshot_payload(snapshot: AsyncShadowSnapshotV4) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "sample_id": snapshot.sample_id,
        "user_text": snapshot.user_text,
        "turn_context": snapshot.turn_context.model_dump(mode="json"),
    }


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(
            "tests/fixtures/input_preprocessing/fourth_round_flat_shadow_matrix.json"
        ),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v4.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow", "both"), default="ideal")
    parser.add_argument(
        "--phase",
        choices=("exploratory", "confirmatory"),
        default="exploratory",
    )
    parser.add_argument("--model", default=os.getenv("INPUT_PREPROCESSING_V4_MODEL", "qwen-plus"))
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--repeat-override", type=int, default=None)
    parser.add_argument("--with-clinical-baseline", action="store_true")
    parser.add_argument(
        "--async-queue-directory",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v4-async-shadow"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v4-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    document = load_v4_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    modes: list[Literal["ideal", "shadow"]] = (
        ["ideal", "shadow"] if args.mode == "both" else [args.mode]
    )
    analyzer: InputPreprocessingV4Analyzer | None = None
    analyzer_factory: Callable[[], InputPreprocessingV4Analyzer] | None = None
    clinical_baseline: V4ClinicalBaselineAgent | None = None
    candidate_retriever: V4CandidateRetriever | None = None
    if "shadow" in modes:
        from vet_agent.clinical_safety import ClinicalSafetySemanticExtractorAgent
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()
        embeddings = QwenEmbeddingClient(settings)
        candidate_retriever = V4CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=embeddings,
            candidate_limit=int(
                os.getenv("INPUT_PREPROCESSING_V4_CANDIDATE_LIMIT", "8")
            ),
            minimum_score=float(
                os.getenv("INPUT_PREPROCESSING_V4_CANDIDATE_MIN_SCORE", "0.72")
            ),
        )

        def build_analyzer() -> InputPreprocessingV4Analyzer:
            return InputPreprocessingV4Analyzer(
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
    runner = V4ArchitectureValidationRunner(
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
    path = write_v4_experiment_report(
        output_dir=args.output_dir,
        reports=reports,
        fixed_versions={
            "model": args.model,
            "prompt_version": os.getenv(
                "INPUT_PREPROCESSING_V4_PROMPT_VERSION",
                "v4-flat-dev-20260825-2",
            ),
            "schema_version": "v4-flat-raw",
            "vocabulary_version": vocabulary.version,
            "candidate_recall_version": (
                candidate_retriever.recall_version
                if candidate_retriever is not None
                else "ideal-control"
            ),
            "quote_normalization_version": "v4-conservative-20260825-1",
            "fixture_version": _file_digest(args.matrix),
            "gate_version": os.getenv(
                "INPUT_PREPROCESSING_V4_GATE_VERSION",
                "v4-gates-20260825-1",
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
