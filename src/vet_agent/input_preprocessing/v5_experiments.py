"""Fifth-round V5 thin-claim architecture validation runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .runtime_helpers import make_runtime_settings
from .v5_analyzer import V5_PROMPT_VERSION, InputPreprocessingV5Analyzer
from .v5_canonical_linker import V5CandidateRetriever
from .v5_claim_graph import ClaimGraphBuilder
from .v5_contracts import (
    GovernedThinUserClaim,
    ThinUserClaimRaw,
    V5AssertionVerification,
    V5CandidateSet,
    V5CanonicalCandidate,
    V5CanonicalMapping,
    V5ClaimStateStatus,
    V5EntityBinding,
    V5EntityType,
    V5InputAnalysisResult,
    V5MeasurementEnrichment,
    V5NormalizedStatus,
    V5ParticipantBinding,
    V5ParticipantEnrichment,
    V5PreviousQuestionTarget,
    V5QualityGateStatus,
    V5ResolutionMethod,
    V5ResolutionStatus,
    V5SubjectEnrichment,
    V5SubjectReference,
    V5TemporalEnrichment,
    V5TurnContext,
    V5TurnIntentRaw,
)
from .v5_gates import evaluate_v5_quality_gates
from .v5_policy import projection_readiness
from .v5_projection import (
    project_v5_clinical_safety_report,
    project_v5_consultation_report,
)
from .v5_quote_governance import (
    QUOTE_NORMALIZATION_VERSION,
    resolve_thin_claim_quotes,
)
from .vocabulary import CanonicalVocabulary


class V5ExperimentKind:
    INTENT = "intent"
    THIN_SCHEMA = "thin_schema"
    QUOTE = "quote"
    COVERAGE = "claim_coverage"
    STATEMENT = "statement_type"
    SUBJECT = "subject_ref"
    PARTICIPANT = "participant_enrich"
    TARGET_CAN = "target_can"
    CAN_SELECT = "can_select"
    POLICY = "policy_enrich"
    TEMPORAL = "temporal_enrich"
    MEASUREMENT = "measurement_enrich"
    GRAPH = "graph_state"
    DEDUP = "dedup_merge"
    MULTI_TURN = "multi_turn"
    DOMAIN = "domain_projection"
    CS = "cs_report_only"
    NEG = "neg"
    REP = "repeat_stability"
    ASYNC = "async"
    COST = "cost_quality"


_THIN_SIGNATURE_KEYS = {
    "evidence_quote",
    "target_quote",
    "user_statement_type",
    "coarse_type",
    "temporal_quote",
    "measurement_quote",
}

V5_POLICY_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V5_POLICY_VERSION",
    "v5-policy-dev-20260826-1",
)
V5_GRAPH_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V5_GRAPH_VERSION",
    "v5-claim-graph-dev-20260826-1",
)
V5_GATE_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V5_GATE_VERSION",
    "v5-gates-dev-20260826-1",
)


class V5ExpectedParticipant(BaseModel):
    role: str
    reference_id: str
    object_mention: str = ""


class V5ExpectedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    user_statement_type: str
    coarse_type: str
    subject_reference: str | None = None
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    temporal_quote: str = Field(default="", max_length=160)
    temporal_relation: str = "unstructured"
    temporal_value: str = ""
    temporal_precision: str = "unresolved"
    temporal_status: str = "normalized"
    measurement_quote: str = Field(default="", max_length=160)
    measurement_value: str = ""
    measurement_unit: str = ""
    measurement_status: str = "normalized"
    participants: list[V5ExpectedParticipant] = Field(
        default_factory=list, max_length=4
    )
    canonical_id: str | None = None
    mapping_status: str = "confirmed"
    review_required: bool = False
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)


class V5ExpectedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    user_text: str = Field(min_length=1, max_length=12000)
    other_subjects: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    previous_question_target: dict[str, Any] | None = None
    expected_intent: dict[str, bool]
    expected_claims: list[V5ExpectedClaim] = Field(default_factory=list, max_length=128)


class V5ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    architecture_question: str = Field(min_length=1, max_length=500)
    control_group: str = Field(min_length=1, max_length=240)
    experimental_group: str = Field(min_length=1, max_length=240)
    sample_ids: list[str] = Field(min_length=1, max_length=32)
    repeat_count: int = Field(default=3, ge=1, le=10)
    variant: Literal["v5_t0", "v5_t1", "v5_t2"] = "v5_t1"
    primary_metrics: list[str] = Field(default_factory=list, max_length=16)
    expected_outcome: Literal[
        "architecture_match",
        "stable_output",
        "gate_blocked_as_expected",
        "async_isolation_report_only",
        "comparison_report_only",
    ]


class V5ExperimentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v5"]
    dataset_type: Literal["development", "held_out"]
    reference_time: datetime
    pet_profile: dict[str, Any] = Field(default_factory=dict)
    cases: list[V5ExpectedCase] = Field(min_length=1, max_length=64)
    experiments: list[V5ExperimentSpec] = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class AsyncShadowSnapshotV5:
    snapshot_id: str
    sample_id: str
    claim_id: str
    user_text: str
    turn_context: V5TurnContext


@dataclass(frozen=True)
class AsyncShadowSubmitResultV5:
    snapshot_id: str
    accepted: bool
    reason: str


class FileAsyncShadowQueueV5:
    """Small file-backed queue for report-only V5 shadow isolation tests."""

    def __init__(self, *, directory: Path, max_size: int = 2) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_size = max(1, max_size)

    def submit(
        self,
        snapshot: AsyncShadowSnapshotV5,
    ) -> AsyncShadowSubmitResultV5:
        pending = list(self.directory.glob("snapshot-*.json"))
        if len(pending) >= self.max_size:
            return AsyncShadowSubmitResultV5(
                snapshot_id=snapshot.snapshot_id,
                accepted=False,
                reason="queue_full",
            )
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "sample_id": snapshot.sample_id,
            "claim_id": snapshot.claim_id,
            "user_text": snapshot.user_text,
            "turn_context": snapshot.turn_context.model_dump(mode="json"),
            "status": "pending",
            "attempts": 0,
            "trace": None,
        }
        path = self.directory / f"snapshot-{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return AsyncShadowSubmitResultV5(
            snapshot_id=snapshot.snapshot_id,
            accepted=True,
            reason="accepted",
        )

    def claim(self) -> AsyncShadowSnapshotV5 | None:
        pending = sorted(
            (path for path in self.directory.glob("snapshot-*.json")),
            key=lambda path: path.stat().st_mtime_ns,
        )
        for path in pending:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "pending":
                continue
            payload["status"] = "running"
            payload["attempts"] += 1
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return AsyncShadowSnapshotV5(
                snapshot_id=payload["snapshot_id"],
                sample_id=payload["sample_id"],
                claim_id=payload["claim_id"],
                user_text=payload["user_text"],
                turn_context=V5TurnContext.model_validate(payload["turn_context"]),
            )
        return None

    def complete(self, snapshot_id: str, *, trace: dict[str, Any]) -> None:
        path = self.directory / f"snapshot-{snapshot_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "complete"
        payload["trace"] = trace
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def fail(
        self,
        snapshot_id: str,
        *,
        reason: str,
        max_attempts: int = 1,
    ) -> Literal["retry", "dead_letter"]:
        path = self.directory / f"snapshot-{snapshot_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["failure_reason"] = reason
        if payload["attempts"] >= max_attempts:
            payload["status"] = "dead_letter"
            result: Literal["retry", "dead_letter"] = "dead_letter"
        else:
            payload["status"] = "pending"
            result = "retry"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    def dead_letters(self) -> list[dict[str, Any]]:
        result = []
        for path in self.directory.glob("snapshot-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") == "dead_letter":
                result.append(payload)
        return result


class V5ArchitectureValidationRunner:
    """Run ideal controls, shadow cases, mutations, and async isolation."""

    def __init__(
        self,
        *,
        document: V5ExperimentDocument,
        vocabulary: CanonicalVocabulary,
        analyzer_factory: Any | None = None,
    ) -> None:
        self.document = document
        self.vocabulary = vocabulary
        self.analyzer_factory = analyzer_factory
        self.graph = ClaimGraphBuilder()

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        only_experiment_ids: set[str] | None = None,
        repeat_override: int | None = None,
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for experiment in self.document.experiments:
            if (
                only_experiment_ids
                and experiment.experiment_id not in only_experiment_ids
            ):
                continue
            repeat_count = repeat_override or experiment.repeat_count
            started = time.perf_counter()
            try:
                report = await self._run_experiment(
                    experiment=experiment,
                    mode=mode,
                    repeat_count=repeat_count,
                )
                report["latency_ms"] = _elapsed_ms(started)
                reports.append(report)
            except Exception as exc:  # noqa: BLE001
                reports.append(
                    {
                        "experiment_id": experiment.experiment_id,
                        "kind": experiment.kind,
                        "mode": mode,
                        "variant": experiment.variant,
                        "status": "failed",
                        "failures": [f"runner_error:{type(exc).__name__}:{exc}"],
                        "latency_ms": _elapsed_ms(started),
                    }
                )
        return reports

    async def _run_experiment(
        self,
        *,
        experiment: V5ExperimentSpec,
        mode: Literal["ideal", "shadow"],
        repeat_count: int,
    ) -> dict[str, Any]:
        cases = [
            case
            for case in self.document.cases
            if case.sample_id in set(experiment.sample_ids)
        ]
        if experiment.kind == V5ExperimentKind.ASYNC:
            return self._run_async_experiment(experiment)

        failures: list[str] = []
        signatures_by_sample: dict[str, list[list[dict[str, Any]]]] = {}
        result_summaries: list[dict[str, Any]] = []
        for case in cases:
            signatures_by_sample[case.sample_id] = []
            for repeat in range(repeat_count):
                if experiment.kind == V5ExperimentKind.NEG:
                    result = self._mutated_negative_result(case)
                elif mode == "ideal":
                    result = self._ideal_result(
                        case=case,
                        variant=experiment.variant,
                    )
                else:
                    analyzer = self._build_analyzer()
                    result = await analyzer.analyze(
                        user_text=case.user_text,
                        turn_context=self._turn_context(case),
                        variant=experiment.variant,
                    )
                actual = _result_signature(result)
                signatures_by_sample[case.sample_id].append(actual)
                expected = [_expected_signature(item) for item in case.expected_claims]
                if experiment.kind != V5ExperimentKind.NEG:
                    failures.extend(
                        _evaluate_signatures(
                            expected=expected,
                            actual=actual,
                            prefix=f"{case.sample_id}:r{repeat + 1}",
                            keys=_THIN_SIGNATURE_KEYS
                            if experiment.variant == "v5_t0"
                            else None,
                        )
                    )
                intent_failures = _evaluate_intent(case.expected_intent, result.intent)
                failures.extend(
                    f"{case.sample_id}:r{repeat + 1}:{item}" for item in intent_failures
                )
                if experiment.kind == V5ExperimentKind.NEG:
                    if not any(
                        gate.status
                        in {
                            V5QualityGateStatus.FAILED,
                            V5QualityGateStatus.NEEDS_REVIEW,
                        }
                        for gate in result.gates
                    ):
                        failures.append("negative_mutation_was_not_blocked")
                else:
                    failures.extend(
                        f"{case.sample_id}:blocking_gate:{gate.reason_code}"
                        for gate in result.failed_blocking_gates()
                    )
                result_summaries.append(
                    _result_metrics(
                        result,
                        self.vocabulary,
                        sample_id=case.sample_id,
                    )
                )

        stability_failures = _stability_failures(signatures_by_sample)
        failures.extend(stability_failures)
        passed = not failures or (
            experiment.kind == V5ExperimentKind.NEG and not failures
        )
        return {
            "experiment_id": experiment.experiment_id,
            "kind": experiment.kind,
            "mode": mode,
            "variant": experiment.variant,
            "architecture_question": experiment.architecture_question,
            "control_group": experiment.control_group,
            "experimental_group": experiment.experimental_group,
            "sample_ids": experiment.sample_ids,
            "repeat_count": repeat_count,
            "status": "passed" if passed else "failed",
            "failures": sorted(set(failures))[:100],
            "results": result_summaries,
            "signature_counts": {
                sample: len(
                    {
                        json.dumps(item, ensure_ascii=False, sort_keys=True)
                        for item in items
                    }
                )
                for sample, items in signatures_by_sample.items()
            },
        }

    def _run_async_experiment(
        self,
        experiment: V5ExperimentSpec,
    ) -> dict[str, Any]:
        root_base = Path(
            os.getenv(
                "INPUT_PREPROCESSING_V5_ASYNC_TEST_DIR",
                "/tmp/input-preprocessing-v5-async",
            )
        )
        root = root_base / uuid4().hex[:12]
        queue = FileAsyncShadowQueueV5(directory=root, max_size=2)
        case = next(
            item
            for item in self.document.cases
            if item.sample_id in set(experiment.sample_ids)
        )
        ideal_result = self._ideal_result(case=case, variant="v5_t1")
        snapshots = [
            AsyncShadowSnapshotV5(
                snapshot_id=f"snapshot-{index}",
                sample_id=case.sample_id,
                claim_id=claim.raw.claim_id,
                user_text=case.user_text,
                turn_context=self._turn_context(case),
            )
            for index, claim in enumerate(ideal_result.claims[:3])
        ]
        submissions = [queue.submit(snapshot) for snapshot in snapshots]
        first = queue.claim()
        if first is not None:
            queue.complete(
                first.snapshot_id,
                trace={"status": "complete", "business_state_written": False},
            )
        second = queue.claim()
        dead_letter = "not_attempted"
        if second is not None:
            dead_letter = queue.fail(
                second.snapshot_id,
                reason="simulated_worker_timeout",
                max_attempts=1,
            )
        failures: list[str] = []
        if [item.accepted for item in submissions] != [True, True, False]:
            failures.append("bounded_queue_behavior_invalid")
        if submissions[-1].reason != "queue_full":
            failures.append("queue_full_not_explicit")
        if dead_letter != "dead_letter" or len(queue.dead_letters()) != 1:
            failures.append("dead_letter_isolation_invalid")
        return {
            "experiment_id": experiment.experiment_id,
            "kind": experiment.kind,
            "mode": "ideal",
            "variant": experiment.variant,
            "status": "passed" if not failures else "failed",
            "failures": failures,
            "submissions": [item.__dict__ for item in submissions],
            "dead_letter_count": len(queue.dead_letters()),
            "claim_task_count": len(ideal_result.claims),
            "submitted_claim_task_count": len(snapshots),
            "business_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "trace_incomplete_count": 0,
        }

    def _build_analyzer(self) -> InputPreprocessingV5Analyzer:
        if self.analyzer_factory is None:
            raise ValueError("v5_shadow_analyzer_factory_required")
        return self.analyzer_factory()

    def _run_all_ideal_for_test(self) -> list[dict[str, Any]]:
        """Run the ideal path synchronously for deterministic unit tests."""

        async def run() -> list[dict[str, Any]]:
            return await self.run(mode="ideal", repeat_override=1)

        return asyncio.run(run())

    def _ideal_result(
        self,
        *,
        case: V5ExpectedCase,
        variant: Literal["v5_t0", "v5_t1", "v5_t2"],
    ) -> V5InputAnalysisResult:
        turn_context = self._turn_context(case)
        claims: list[GovernedThinUserClaim] = []
        transitions = []
        for expected in case.expected_claims:
            raw = ThinUserClaimRaw(
                claim_id=expected.claim_id,
                evidence_quote=expected.evidence_quote,
                target_quote=expected.target_quote,
                user_statement_type=expected.user_statement_type,  # type: ignore[arg-type]
                coarse_type=expected.coarse_type,  # type: ignore[arg-type]
                subject_status=(
                    "ambiguous" if len(expected.subject_candidates) > 1 else "pending"
                ),
                subject_candidates=expected.subject_candidates,
                temporal_quote=expected.temporal_quote,
                measurement_quote=expected.measurement_quote,
                confidence=expected.confidence,
                needs_review=expected.review_required,
            )
            evidence, target, temporal, measurement = resolve_thin_claim_quotes(
                user_text=case.user_text,
                raw=raw,
            )
            claim, claim_transitions = self.graph.create_claim(
                raw=raw,
                evidence_quote=evidence,
                target_quote=target,
                temporal_quote=temporal,
                measurement_quote=measurement,
            )
            claims.append(claim)
            transitions.extend(claim_transitions)

        if variant != "v5_t0":
            for claim, expected in zip(
                claims,
                case.expected_claims,
                strict=True,
            ):
                claim.subject = self._ideal_subject(expected, claim, turn_context)
                claim.state.subject_state = claim.subject.status
                claim.participants = self._ideal_participants(
                    expected,
                    claim,
                    turn_context,
                )
                claim.state.participant_state = (
                    claim.participants.status
                    if claim.participants
                    else V5ClaimStateStatus.NOT_REQUIRED
                )
                claim.temporal = self._ideal_temporal(expected, claim)
                claim.state.temporal_state = (
                    claim.temporal.status
                    if claim.temporal
                    else V5ClaimStateStatus.NOT_REQUIRED
                )
                claim.measurement = self._ideal_measurement(expected, claim)
                claim.state.measurement_state = (
                    claim.measurement.status
                    if claim.measurement
                    else V5ClaimStateStatus.NOT_REQUIRED
                )
                claim.assertion = V5AssertionVerification(
                    claim_id=claim.raw.claim_id,
                    status=V5ClaimStateStatus.VERIFIED,
                    verification_status="verified",
                    reason_code="quote_supports_statement_type",
                )
                claim.state.assertion_state = V5ClaimStateStatus.VERIFIED
                claim.canonical = self._ideal_canonical(expected, claim)
                claim.state.canonical_state = claim.canonical.status
                ready, _missing = projection_readiness(claim.state)
                claim.state.projection_state = (
                    V5ClaimStateStatus.READY
                    if ready and not expected.review_required
                    else V5ClaimStateStatus.REVIEW_REQUIRED
                )
        else:
            for claim in claims:
                claim.state.subject_state = V5ClaimStateStatus.PENDING
                claim.state.participant_state = (
                    V5ClaimStateStatus.NOT_REQUIRED
                    if claim.raw.coarse_type.value
                    not in {"action", "food", "medication"}
                    else V5ClaimStateStatus.PENDING
                )
                claim.state.canonical_state = V5ClaimStateStatus.PENDING
                claim.state.assertion_state = V5ClaimStateStatus.NOT_REQUIRED
                claim.state.projection_state = V5ClaimStateStatus.UNRESOLVED

        result = V5InputAnalysisResult(
            variant="ideal" if variant == "v5_t1" else variant,
            turn_context=turn_context,
            intent=V5TurnIntentRaw.model_validate(
                {**case.expected_intent, "confidence": 0.99}
            ),
            raw_claims=[claim.raw for claim in claims],
            claims=claims,
            transitions=transitions,
            model_name="ideal-control",
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version="ideal-control-target-quote",
            quote_normalization_version=QUOTE_NORMALIZATION_VERSION,
        )
        result.gates = evaluate_v5_quality_gates(result=result)
        return result

    def _mutated_negative_result(
        self,
        case: V5ExpectedCase,
    ) -> V5InputAnalysisResult:
        result = self._ideal_result(case=case, variant="v5_t1")
        if not result.claims:
            return result
        first = result.claims[0]
        first.evidence_quote = first.evidence_quote.model_copy(
            update={"status": "not_found"}
        )
        first.state.quote_state = V5ClaimStateStatus.BLOCKED
        if first.subject is not None:
            first.subject = first.subject.model_copy(
                deep=True,
            )
            first.subject.subject.reference_id = "invented-entity"
            first.subject.subject.resolution_status = V5ResolutionStatus.RESOLVED
            first.subject.status = V5ClaimStateStatus.BLOCKED
            first.state.subject_state = V5ClaimStateStatus.BLOCKED
        if first.canonical is not None:
            first.canonical = first.canonical.model_copy(
                update={"selected_candidate_id": "c-invalid"}
            )
            first.state.canonical_state = V5ClaimStateStatus.BLOCKED
        first.state.projection_state = V5ClaimStateStatus.BLOCKED
        result.gates = evaluate_v5_quality_gates(result=result)
        return result

    def _ideal_subject(
        self,
        expected: V5ExpectedClaim,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5SubjectEnrichment:
        reference = turn_context.entity_references().get(
            expected.subject_reference or ""
        )
        if reference is None:
            return V5SubjectEnrichment(
                claim_id=claim.raw.claim_id,
                status=V5ClaimStateStatus.BLOCKED,
                subject=V5EntityBinding(),
                review_required=True,
                failure_reason="subject_reference_not_found",
            )
        return V5SubjectEnrichment(
            claim_id=claim.raw.claim_id,
            status=V5ClaimStateStatus.READY,
            subject=V5EntityBinding(
                reference_id=reference.reference_id,
                entity_type=reference.entity_type,
                resolution_method=(
                    V5ResolutionMethod.PREVIOUS_QUESTION_TARGET
                    if turn_context.previous_question_target is not None
                    and expected.target_quote == expected.evidence_quote
                    else V5ResolutionMethod.TRUSTED_CURRENT_PET
                    if reference.entity_type == V5EntityType.CURRENT_PET
                    else V5ResolutionMethod.EXPLICIT_COREFERENCE
                ),
                resolution_status=V5ResolutionStatus.RESOLVED,
                subject_candidates=expected.subject_candidates,
                confidence=expected.confidence,
            ),
            evidence_quote=claim.raw.evidence_quote,
        )

    def _ideal_participants(
        self,
        expected: V5ExpectedClaim,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5ParticipantEnrichment | None:
        if not expected.participants:
            return None
        return V5ParticipantEnrichment(
            claim_id=claim.raw.claim_id,
            status=V5ClaimStateStatus.READY,
            participants=[
                V5ParticipantBinding(
                    role=item.role,  # type: ignore[arg-type]
                    entity=V5EntityBinding(
                        reference_id=item.reference_id,
                        entity_type=turn_context.entity_references()[
                            item.reference_id
                        ].entity_type,
                        resolution_status=V5ResolutionStatus.RESOLVED,
                    ),
                    object_mention=item.object_mention,
                )
                for item in expected.participants
            ],
        )

    def _ideal_temporal(
        self,
        expected: V5ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V5TemporalEnrichment | None:
        if not expected.temporal_quote:
            return None
        return V5TemporalEnrichment(
            claim_id=claim.raw.claim_id,
            status=V5ClaimStateStatus.READY,
            relation=expected.temporal_relation,  # type: ignore[arg-type]
            value=expected.temporal_value,
            precision=expected.temporal_precision,  # type: ignore[arg-type]
            normalization_status=V5NormalizedStatus.NORMALIZED,
            temporal_quote=claim.temporal_quote,
        )

    def _ideal_measurement(
        self,
        expected: V5ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V5MeasurementEnrichment | None:
        if not expected.measurement_quote:
            return None
        return V5MeasurementEnrichment(
            claim_id=claim.raw.claim_id,
            status=V5ClaimStateStatus.READY,
            value=expected.measurement_value,
            unit=expected.measurement_unit,
            relation="frequency"
            if expected.measurement_value.startswith("1/")
            else "associated_with",
            precision="frequency"
            if expected.measurement_value.startswith("1/")
            else "exact",
            normalization_status=V5NormalizedStatus.NORMALIZED,
            measurement_quote=claim.measurement_quote,
        )

    def _ideal_canonical(
        self,
        expected: V5ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V5CanonicalMapping:
        terms = self.vocabulary.term_map()
        term = terms.get(expected.canonical_id or "")
        candidates: list[V5CanonicalCandidate] = []
        if term is not None:
            candidates.append(
                V5CanonicalCandidate(
                    candidate_id="c-1",
                    canonical_id=term.canonical_id,
                    canonical_type=term.canonical_type,
                    surface_form=term.aliases[0],
                    score=1.0,
                    recall_source="ideal_control",
                )
            )
        return V5CanonicalMapping(
            claim_id=claim.raw.claim_id,
            status=(
                V5ClaimStateStatus.READY
                if expected.mapping_status == "confirmed"
                else V5ClaimStateStatus.REVIEW_REQUIRED
            ),
            candidate_set=V5CandidateSet(
                claim_id=claim.raw.claim_id,
                target_quote=claim.raw.target_quote,
                retrieval_query=claim.raw.target_quote,
                retrieval_context="direct_target_quote",
                candidates=candidates,
                recall_status="recalled" if candidates else "no_candidate",
                recall_version="ideal-control-target-quote",
            ),
            selected_candidate_id="c-1" if candidates else None,
            canonical_id=expected.canonical_id if candidates else None,
            mapping_status=expected.mapping_status,  # type: ignore[arg-type]
            review_required=expected.review_required,
        )

    def _turn_context(self, case: V5ExpectedCase) -> V5TurnContext:
        return V5TurnContext(
            request_id=f"request-{case.sample_id}",
            trace_id=f"trace-{case.sample_id}",
            user_id="v5-test-user",
            pet_id="v5-test-pet",
            session_id="v5-test-session",
            reference_time=self.document.reference_time,
            current_pet_subject=V5SubjectReference(
                reference_id="current_pet",
                entity_type=V5EntityType.CURRENT_PET,
            ),
            other_subjects=[
                V5SubjectReference.model_validate(item) for item in case.other_subjects
            ],
            previous_question_target=(
                V5PreviousQuestionTarget.model_validate(case.previous_question_target)
                if case.previous_question_target is not None
                else None
            ),
        )


def load_v5_experiment_document(path: Path) -> V5ExperimentDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return V5ExperimentDocument.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid V5 experiment document: {path}") from exc


def write_v5_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v5-{uuid4().hex[:12]}.json"
    passed = sum(item.get("status") == "passed" for item in reports)
    payload = {
        "schema_version": "v5",
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "runner_configuration": metadata or {},
        "experiments": reports,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _evaluate_signatures(
    *,
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    prefix: str,
    keys: set[str] | None = None,
) -> list[str]:
    expected = [
        {key: item.get(key) for key in sorted(keys)} if keys else item
        for item in expected
    ]
    actual = [
        {key: item.get(key) for key in sorted(keys)} if keys else item
        for item in actual
    ]
    expected_keys = {
        json.dumps(item, sort_keys=True, ensure_ascii=False) for item in expected
    }
    actual_keys = {
        json.dumps(item, sort_keys=True, ensure_ascii=False) for item in actual
    }
    failures = []
    if len(actual) != len(expected):
        failures.append(f"{prefix}:claim_count_mismatch:{len(actual)}!={len(expected)}")
    if expected_keys - actual_keys:
        failures.append(f"{prefix}:missing_claims")
    if actual_keys - expected_keys:
        failures.append(f"{prefix}:unexpected_claims")
    return failures


def _evaluate_intent(
    expected: dict[str, bool],
    actual: V5TurnIntentRaw,
) -> list[str]:
    failures = []
    for key, value in expected.items():
        if getattr(actual, key) != value:
            failures.append(f"intent_{key}_mismatch:{getattr(actual, key)}!={value}")
    return failures


def _expected_signature(item: V5ExpectedClaim) -> dict[str, Any]:
    return {
        "evidence_quote": item.evidence_quote,
        "target_quote": item.target_quote,
        "user_statement_type": item.user_statement_type,
        "coarse_type": item.coarse_type,
        "subject_reference": item.subject_reference,
        "participants": [participant.model_dump() for participant in item.participants],
        "temporal_quote": item.temporal_quote,
        "temporal_relation": item.temporal_relation
        if item.temporal_quote
        else "not_applicable",
        "temporal_value": item.temporal_value,
        "temporal_precision": item.temporal_precision
        if item.temporal_quote
        else "not_applicable",
        "measurement_quote": item.measurement_quote,
        "measurement_value": item.measurement_value,
        "measurement_unit": item.measurement_unit,
        "canonical_id": item.canonical_id,
        "mapping_status": item.mapping_status,
        "projection_state": "ready"
        if item.mapping_status == "confirmed" and not item.review_required
        else "review_required",
    }


def _actual_signature(item: GovernedThinUserClaim) -> dict[str, Any]:
    return {
        "evidence_quote": item.raw.evidence_quote,
        "target_quote": item.raw.target_quote,
        "user_statement_type": item.raw.user_statement_type.value,
        "coarse_type": item.raw.coarse_type.value,
        "subject_reference": item.subject.subject.reference_id
        if item.subject is not None
        else None,
        "participants": [
            {
                "role": participant.role,
                "reference_id": participant.entity.reference_id,
                "object_mention": participant.object_mention,
            }
            for participant in (
                item.participants.participants if item.participants else []
            )
        ],
        "temporal_quote": item.raw.temporal_quote,
        "temporal_relation": item.temporal.relation.value
        if item.temporal is not None
        else "not_applicable",
        "temporal_value": item.temporal.value if item.temporal is not None else "",
        "temporal_precision": item.temporal.precision.value
        if item.temporal is not None
        else "not_applicable",
        "measurement_quote": item.raw.measurement_quote,
        "measurement_value": item.measurement.value
        if item.measurement is not None
        else "",
        "measurement_unit": item.measurement.unit
        if item.measurement is not None
        else "",
        "canonical_id": item.canonical.canonical_id if item.canonical else None,
        "mapping_status": item.canonical.mapping_status.value
        if item.canonical
        else "pending",
        "projection_state": item.state.projection_state.value,
    }


def _result_signature(result: V5InputAnalysisResult) -> list[dict[str, Any]]:
    return [_actual_signature(item) for item in result.claims]


def _result_metrics(
    result: V5InputAnalysisResult,
    vocabulary: CanonicalVocabulary,
    *,
    sample_id: str,
) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "variant": result.variant,
        "prompt_version": result.prompt_version,
        "claim_count": len(result.claims),
        "projection_ready_count": sum(
            item.state.projection_state == V5ClaimStateStatus.READY
            for item in result.claims
        ),
        "review_count": sum(
            item.state.projection_state == V5ClaimStateStatus.REVIEW_REQUIRED
            for item in result.claims
        ),
        "blocked_count": sum(
            any(
                getattr(item.state, dimension) == V5ClaimStateStatus.BLOCKED
                for dimension in (
                    "quote_state",
                    "subject_state",
                    "participant_state",
                    "temporal_state",
                    "measurement_state",
                    "assertion_state",
                    "canonical_state",
                )
            )
            for item in result.claims
        ),
        "model_call_count": result.model_call_count,
        "stage_latency_ms": result.stage_latency_ms,
        "gates": [gate.reason_code for gate in result.gates],
        "consultation_projection": project_v5_consultation_report(
            result=result,
            vocabulary=vocabulary,
        ),
        "clinical_safety_projection": project_v5_clinical_safety_report(
            result=result,
        ),
    }


def _stability_failures(
    signatures_by_sample: dict[str, list[list[dict[str, Any]]]],
) -> list[str]:
    failures = []
    for sample, runs in signatures_by_sample.items():
        keys = {
            json.dumps(
                sorted(item, key=lambda value: json.dumps(value, sort_keys=True)),
                ensure_ascii=False,
                sort_keys=True,
            )
            for item in runs
        }
        if len(keys) > 1:
            failures.append(f"{sample}:signature_unstable:{len(keys)}")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(
            "tests/fixtures/input_preprocessing/fifth_round_thin_shadow_matrix.json"
        ),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v4.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow"), default="ideal")
    parser.add_argument(
        "--phase", choices=("exploratory", "confirmatory"), default="exploratory"
    )
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument("--repeat-override", type=int, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v5-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    if args.repeat_override is not None and not 1 <= args.repeat_override <= 10:
        raise ValueError("--repeat-override must be between 1 and 10")
    document = load_v5_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    analyzer_factory = None
    if args.mode == "shadow":
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()

        def factory() -> InputPreprocessingV5Analyzer:
            qwen = QwenClient(settings)
            retriever = V5CandidateRetriever(
                vocabulary=vocabulary,
                embeddings=QwenEmbeddingClient(settings),
            )
            return InputPreprocessingV5Analyzer(
                qwen=qwen,
                vocabulary=vocabulary,
                candidate_retriever=retriever,
                model=os.getenv("INPUT_PREPROCESSING_V5_MODEL", "qwen-plus"),
            )

        analyzer_factory = factory

    runner = V5ArchitectureValidationRunner(
        document=document,
        vocabulary=vocabulary,
        analyzer_factory=analyzer_factory,
    )
    only = set(args.experiment) if args.experiment else None
    reports = await runner.run(
        mode=args.mode,
        only_experiment_ids=only,
        repeat_override=args.repeat_override,
    )
    path = write_v5_experiment_report(
        output_dir=args.output_dir,
        reports=reports,
        metadata={
            "phase": args.phase,
            "model": os.getenv("INPUT_PREPROCESSING_V5_MODEL", "qwen-plus"),
            "prompt_version": V5_PROMPT_VERSION,
            "schema_version": "v5-thin-raw",
            "policy_version": V5_POLICY_VERSION,
            "graph_version": V5_GRAPH_VERSION,
            "gate_version": V5_GATE_VERSION,
            "vocabulary_version": vocabulary.version,
            "fixture_path": str(args.matrix),
            "fixture_sha256": hashlib.sha256(args.matrix.read_bytes()).hexdigest(),
            "analyzer_isolation": "per-case-fresh-qwen-client",
        },
    )
    failed = [item for item in reports if item.get("status") != "passed"]
    print(f"report={path}")
    print(
        f"experiments={len(reports)} passed={len(reports) - len(failed)} failed={len(failed)}"
    )
    for item in failed:
        print(
            f"FAILED experiment={item['experiment_id']} failures={','.join(item.get('failures', [])[:5])}"
        )
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


if __name__ == "__main__":
    main()
