"""Sixth-round V6 thin-claim batched-enrichment validation runner."""

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
from .v6_analyzer import V6_PROMPT_VERSION, InputPreprocessingV6Analyzer
from .v6_canonical_linker import V6CandidateRetriever
from .v6_claim_graph import ClaimGraphBuilder
from .v6_contracts import (
    GovernedThinUserClaim,
    ThinUserClaimRaw,
    V6AssertionVerification,
    V6CandidateSet,
    V6CanonicalCandidate,
    V6CanonicalDiagnostic,
    V6CanonicalMapping,
    V6CanonicalMappingStatus,
    V6ClaimStateStatus,
    V6EntityBinding,
    V6EntityType,
    V6InputAnalysisResult,
    V6MeasurementEnrichment,
    V6NormalizedStatus,
    V6ParticipantBinding,
    V6ParticipantEnrichment,
    V6PreviousQuestionTarget,
    V6QualityGateStatus,
    V6ResolutionMethod,
    V6ResolutionStatus,
    V6SubjectEnrichment,
    V6SubjectReference,
    V6TemporalEnrichment,
    V6TurnContext,
    V6TurnIntentRaw,
)
from .v6_gates import evaluate_v6_quality_gates
from .v6_policy import (
    V6_POLICY_VERSION,
    V6EnrichmentPolicy,
    projection_readiness,
)
from .v6_projection import (
    project_v6_clinical_safety_report,
    project_v6_consultation_report,
)
from .v6_quote_governance import (
    QUOTE_NORMALIZATION_VERSION,
    normalize_quote_text,
    resolve_intent_quotes,
    resolve_thin_claim_quotes,
)
from .vocabulary import CanonicalVocabulary


class V6ExperimentKind:
    INTENT = "intent"
    THIN_QUOTE = "thin_quote"
    THIN_SCHEMA = "thin_schema"
    STATEMENT_RELATION = "statement_relation"
    SUBJECT_STAGE = "subject_stage"
    PARTICIPANT_BATCH = "participant_batch"
    AGGREGATE = "aggregate_decomposition"
    CAN_DIAG = "canonical_diagnostic"
    CAN_SELECT = "canonical_select"
    TEMPORAL_BATCH = "temporal_batch"
    MEASUREMENT_BATCH = "measurement_batch"
    ENRICH_PLANNER = "enrichment_planner"
    POLICY_T0 = "policy_t0"
    POLICY_T2 = "policy_t2"
    GRAPH = "graph_state"
    DEDUP = "dedup_merge"
    MULTI_TURN = "multi_turn"
    DOMAIN = "domain_projection"
    CS = "cs_report_only"
    NEG = "neg"
    REP = "repeat_stability"
    ASYNC = "async"
    COST = "cost_quality"
    TOOL = "tool_adapter"
    HELD_OUT = "held_out"


V6_GATE_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V6_GATE_VERSION",
    "v6-gates-dev-20260826-1",
)
V6_GRAPH_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V6_GRAPH_VERSION",
    "v6-claim-graph-dev-20260826-1",
)


class V6ExpectedParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    reference_id: str
    object_mention: str = ""


class V6ExpectedClaim(BaseModel):
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
    measurement_value: str = Field(default="", max_length=160)
    measurement_unit: str = Field(default="", max_length=64)
    measurement_status: str = "normalized"
    participants: list[V6ExpectedParticipant] = Field(
        default_factory=list, max_length=4
    )
    canonical_id: str | None = None
    mapping_status: str = "confirmed"
    review_required: bool = False
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    relation_quote: str = Field(default="", max_length=160)
    relation: str = "unclear"
    subject_evidence_quote: str = Field(default="", max_length=480)


class V6ExpectedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    clarification_request: bool = False
    fact_statement_present: bool = False
    question_present: bool = False
    report_context_present: bool = False


class V6ExpectedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    user_text: str = Field(min_length=1, max_length=12000)
    other_subjects: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    previous_question_target: dict[str, Any] | None = None
    expected_intent: V6ExpectedIntent
    expected_claims: list[V6ExpectedClaim] = Field(default_factory=list, max_length=128)


class V6ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    architecture_question: str = Field(min_length=1, max_length=500)
    control_group: str = Field(min_length=1, max_length=240)
    experimental_group: str = Field(min_length=1, max_length=240)
    sample_ids: list[str] = Field(min_length=1, max_length=32)
    repeat_count: int = Field(default=3, ge=1, le=10)
    variant: Literal["v6_t0", "v6_t1", "v6_t2"] = "v6_t1"
    primary_metrics: list[str] = Field(default_factory=list, max_length=16)
    expected_outcome: str = Field(min_length=1, max_length=120)


class V6ExperimentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6"]
    dataset_type: Literal["development", "held_out"]
    reference_time: datetime
    pet_profile: dict[str, Any] = Field(default_factory=dict)
    cases: list[V6ExpectedCase] = Field(min_length=1, max_length=64)
    experiments: list[V6ExperimentSpec] = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class AsyncShadowBatchSnapshotV6:
    snapshot_id: str
    sample_id: str
    batch_id: str
    claim_ids: tuple[str, ...]
    user_text: str
    turn_context: V6TurnContext


@dataclass(frozen=True)
class AsyncShadowSubmitResultV6:
    snapshot_id: str
    accepted: bool
    reason: str


class FileAsyncShadowQueueV6:
    """File-backed bounded queue for report-only batched V6 isolation tests."""

    def __init__(self, *, directory: Path, max_size: int = 2) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_size = max(1, max_size)

    def submit(
        self,
        snapshot: AsyncShadowBatchSnapshotV6,
    ) -> AsyncShadowSubmitResultV6:
        if len(list(self.directory.glob("snapshot-*.json"))) >= self.max_size:
            return AsyncShadowSubmitResultV6(
                snapshot_id=snapshot.snapshot_id,
                accepted=False,
                reason="queue_full",
            )
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "sample_id": snapshot.sample_id,
            "batch_id": snapshot.batch_id,
            "claim_ids": list(snapshot.claim_ids),
            "user_text": snapshot.user_text,
            "turn_context": snapshot.turn_context.model_dump(mode="json"),
            "status": "pending",
            "attempts": 0,
            "trace": None,
        }
        path = self.directory / f"snapshot-{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return AsyncShadowSubmitResultV6(
            snapshot_id=snapshot.snapshot_id,
            accepted=True,
            reason="accepted",
        )

    def claim(self) -> AsyncShadowBatchSnapshotV6 | None:
        for path in sorted(
            self.directory.glob("snapshot-*.json"),
            key=lambda item: item.stat().st_mtime_ns,
        ):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "pending":
                continue
            payload["status"] = "running"
            payload["attempts"] += 1
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            return AsyncShadowBatchSnapshotV6(
                snapshot_id=payload["snapshot_id"],
                sample_id=payload["sample_id"],
                batch_id=payload["batch_id"],
                claim_ids=tuple(payload["claim_ids"]),
                user_text=payload["user_text"],
                turn_context=V6TurnContext.model_validate(payload["turn_context"]),
            )
        return None

    def complete(self, snapshot_id: str, *, trace: dict[str, Any]) -> None:
        path = self.directory / f"snapshot-{snapshot_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "complete"
        payload["trace"] = trace
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

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
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return result

    def dead_letters(self) -> list[dict[str, Any]]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.directory.glob("snapshot-*.json")
            if json.loads(path.read_text(encoding="utf-8")).get("status")
            == "dead_letter"
        ]


class V6ArchitectureValidationRunner:
    """Run ideal controls, shadow cases, mutations, projections, and isolation."""

    def __init__(
        self,
        *,
        document: V6ExperimentDocument,
        vocabulary: CanonicalVocabulary,
        analyzer_factory: Any | None = None,
    ) -> None:
        self.document = document
        self.vocabulary = vocabulary
        self.analyzer_factory = analyzer_factory
        self.graph = ClaimGraphBuilder()
        self.policy = V6EnrichmentPolicy()

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
        experiment: V6ExperimentSpec,
        mode: Literal["ideal", "shadow"],
        repeat_count: int,
    ) -> dict[str, Any]:
        cases = [
            case
            for case in self.document.cases
            if case.sample_id in set(experiment.sample_ids)
        ]
        if experiment.kind == V6ExperimentKind.ASYNC:
            return self._run_async_experiment(experiment)

        failures: list[str] = []
        signatures_by_sample: dict[str, list[list[dict[str, Any]]]] = {}
        result_summaries: list[dict[str, Any]] = []
        for case in cases:
            signatures_by_sample[case.sample_id] = []
            for repeat in range(repeat_count):
                if experiment.kind == V6ExperimentKind.NEG:
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
                expected = [_semantic_signature(item) for item in case.expected_claims]
                if experiment.kind != V6ExperimentKind.NEG:
                    failures.extend(
                        _evaluate_signatures(
                            expected=expected,
                            actual=actual,
                            prefix=f"{case.sample_id}:r{repeat + 1}",
                            thin_only=experiment.variant == "v6_t0",
                        )
                    )
                failures.extend(
                    f"{case.sample_id}:r{repeat + 1}:{item}"
                    for item in _evaluate_intent(case.expected_intent, result.intent)
                )
                if experiment.kind == V6ExperimentKind.NEG:
                    if not any(
                        gate.status
                        in {
                            V6QualityGateStatus.FAILED,
                            V6QualityGateStatus.NEEDS_REVIEW,
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
                        result=result,
                        sample_id=case.sample_id,
                    )
                )
                if experiment.kind in {
                    V6ExperimentKind.DOMAIN,
                    V6ExperimentKind.CS,
                }:
                    result_summaries[-1]["projection_report"] = (
                        project_v6_consultation_report(
                            result=result,
                            vocabulary=self.vocabulary,
                        )
                        if experiment.kind == V6ExperimentKind.DOMAIN
                        else project_v6_clinical_safety_report(result=result)
                    )

        failures.extend(_stability_failures(signatures_by_sample))
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
            "status": "passed" if not failures else "failed",
            "failures": sorted(set(failures))[:120],
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
        experiment: V6ExperimentSpec,
    ) -> dict[str, Any]:
        root_base = Path(
            os.getenv(
                "INPUT_PREPROCESSING_V6_ASYNC_TEST_DIR",
                "/tmp/input-preprocessing-v6-async",
            )
        )
        root = root_base / uuid4().hex[:12]
        queue = FileAsyncShadowQueueV6(directory=root, max_size=2)
        case = next(
            item
            for item in self.document.cases
            if item.sample_id in set(experiment.sample_ids)
        )
        ideal = self._ideal_result(case=case, variant="v6_t1")
        batches = ideal.enrichment_plan.batches[:3] if ideal.enrichment_plan else []
        snapshots = [
            AsyncShadowBatchSnapshotV6(
                snapshot_id=f"snapshot-{index}",
                sample_id=case.sample_id,
                batch_id=batch.batch_id,
                claim_ids=tuple(batch.claim_ids),
                user_text=case.user_text,
                turn_context=self._turn_context(case),
            )
            for index, batch in enumerate(batches)
        ]
        if not snapshots:
            snapshots = [
                AsyncShadowBatchSnapshotV6(
                    snapshot_id="snapshot-0",
                    sample_id=case.sample_id,
                    batch_id="batch-empty-diagnostic",
                    claim_ids=(),
                    user_text=case.user_text,
                    turn_context=self._turn_context(case),
                )
            ]
        submissions = [queue.submit(snapshot) for snapshot in snapshots]
        first = queue.claim()
        if first is not None:
            queue.complete(
                first.snapshot_id,
                trace={
                    "status": "complete",
                    "business_state_written": False,
                    "clinical_safety_evaluator_called": False,
                },
            )
        second = queue.claim()
        dead_letter = "not_attempted"
        if second is not None:
            dead_letter = queue.fail(
                second.snapshot_id,
                reason="simulated_batch_timeout",
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
            "batch_count": len(batches),
            "claim_task_count": len(ideal.claims),
            "business_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
            "trace_incomplete_count": 0,
        }

    def _build_analyzer(self) -> InputPreprocessingV6Analyzer:
        if self.analyzer_factory is None:
            raise ValueError("v6_shadow_analyzer_factory_required")
        return self.analyzer_factory()

    def _run_all_ideal_for_test(self) -> list[dict[str, Any]]:
        """Run ideal reports synchronously for deterministic tests."""

        async def run() -> list[dict[str, Any]]:
            return await self.run(mode="ideal", repeat_override=1)

        return asyncio.run(run())

    def _ideal_result(
        self,
        *,
        case: V6ExpectedCase,
        variant: Literal["v6_t0", "v6_t1", "v6_t2"],
    ) -> V6InputAnalysisResult:
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
                temporal_quote=expected.temporal_quote,
                measurement_quote=expected.measurement_quote,
                relation_quote=expected.relation_quote,
                relation=expected.relation,  # type: ignore[arg-type]
                subject_evidence_quote=expected.subject_evidence_quote,
                confidence=expected.confidence,
                needs_review=expected.review_required,
            )
            quotes = resolve_thin_claim_quotes(user_text=case.user_text, raw=raw)
            claim, claim_transitions = self.graph.create_claim(raw=raw, quotes=quotes)
            claims.append(claim)
            transitions.extend(claim_transitions)

        decisions = {
            claim.raw.claim_id: self.policy.decide(
                claim,
                always_enrich=variant == "v6_t2",
            )
            for claim in claims
        }
        from .v6_policy import EnrichmentPlanner

        plan = EnrichmentPlanner().plan(
            claims=claims,
            decisions=decisions,
            always_enrich=variant == "v6_t2",
        )
        if variant == "v6_t0":
            plan = plan.model_copy(update={"requests": [], "batches": []})

        for expected, claim in zip(case.expected_claims, claims, strict=True):
            if variant != "v6_t0":
                claim.subject = self._ideal_subject(expected, claim, turn_context)
                claim.state.subject_state = claim.subject.status
                claim.participants = self._ideal_participants(expected, claim)
                claim.state.participant_state = (
                    claim.participants.status
                    if claim.participants
                    else V6ClaimStateStatus.NOT_REQUIRED
                )
                claim.temporal = self._ideal_temporal(expected, claim)
                claim.state.temporal_state = (
                    claim.temporal.status
                    if claim.temporal
                    else V6ClaimStateStatus.NOT_REQUIRED
                )
                claim.measurement = self._ideal_measurement(expected, claim)
                claim.state.measurement_state = (
                    claim.measurement.status
                    if claim.measurement
                    else V6ClaimStateStatus.NOT_REQUIRED
                )
                claim.assertion = V6AssertionVerification(
                    claim_id=claim.raw.claim_id,
                    batch_id="ideal",
                    status=V6ClaimStateStatus.VERIFIED,
                    verification_status="verified",
                    reason_code="ideal_speech_act_verified",
                )
                claim.state.assertion_state = claim.assertion.status
                claim.canonical = self._ideal_canonical(expected, claim)
                claim.state.canonical_state = claim.canonical.status
            ready, missing = projection_readiness(claim.state)
            claim.state.projection_state = (
                V6ClaimStateStatus.READY
                if ready and variant != "v6_t0"
                else V6ClaimStateStatus.UNRESOLVED
                if variant == "v6_t0"
                else V6ClaimStateStatus.REVIEW_REQUIRED
            )
            transitions.append(
                self.graph.transition(
                    claim,
                    event="PROJECTION_READINESS_EVALUATED",
                    dimension="projection_state",
                    to_state=claim.state.projection_state,
                    reason_code=(
                        "projection_ready"
                        if claim.state.projection_state == V6ClaimStateStatus.READY
                        else "variant_t0_no_enrichment"
                        if variant == "v6_t0"
                        else "missing_enrichment:" + ",".join(missing)
                    ),
                    evidence_refs=[claim.raw.claim_id],
                )
            )

        intent = self._ideal_intent(case)
        result = V6InputAnalysisResult(
            variant="ideal",
            turn_context=turn_context,
            intent=intent,
            raw_claims=[claim.raw for claim in claims],
            claims=claims,
            intent_quote_anchors=[
                item
                for item in resolve_intent_quotes(
                    user_text=case.user_text,
                    intent=intent,
                ).values()
                if item is not None
            ],
            transitions=transitions,
            enrichment_plan=plan,
            model_name="ideal-control",
            prompt_version=V6_PROMPT_VERSION,
            policy_version=V6_POLICY_VERSION,
            graph_version=V6_GRAPH_VERSION,
            gate_version=V6_GATE_VERSION,
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version=f"{self.vocabulary.version}:ideal",
            quote_normalization_version=QUOTE_NORMALIZATION_VERSION,
            model_call_count=0,
            batch_count=len(plan.batches),
        )
        result.gates = evaluate_v6_quality_gates(result=result)
        return result

    def _mutated_negative_result(
        self,
        case: V6ExpectedCase,
    ) -> V6InputAnalysisResult:
        result = self._ideal_result(case=case, variant="v6_t1")
        if result.claims:
            result.claims[0].evidence_quote = result.claims[
                0
            ].evidence_quote.model_copy(
                update={
                    "raw_quote": "__not_in_user_text__",
                    "normalized_quote": "__not_in_user_text__",
                    "status": "not_found",
                    "occurrence": 0,
                }
            )
            result.claims[0].state.quote_state = V6ClaimStateStatus.BLOCKED
        result.gates = evaluate_v6_quality_gates(result=result)
        return result

    def _ideal_intent(self, case: V6ExpectedCase) -> V6TurnIntentRaw:
        intent = case.expected_intent
        answer_quote = ""
        triage_quote = ""
        correction_quote = ""
        clarification_quote = ""
        if intent.answer_now:
            answer_quote = case.user_text[-60:]
        if intent.wants_triage:
            triage_quote = case.user_text[-60:]
        if intent.correction:
            correction_quote = case.user_text[-60:]
        if intent.clarification_request:
            clarification_quote = case.user_text[-60:]
        return V6TurnIntentRaw(
            **intent.model_dump(),
            answer_now_evidence_quote=answer_quote,
            wants_triage_evidence_quote=triage_quote,
            correction_evidence_quote=correction_quote,
            clarification_request_evidence_quote=clarification_quote,
            confidence=1.0,
            rationale="ideal control",
        )

    @staticmethod
    def _ideal_subject(
        expected: V6ExpectedClaim,
        claim: GovernedThinUserClaim,
        turn_context: V6TurnContext,
    ) -> V6SubjectEnrichment:
        references = turn_context.entity_references()
        if len(expected.subject_candidates) > 1:
            status = V6ClaimStateStatus.AMBIGUOUS
            reference_id = None
            entity_type = V6EntityType.UNKNOWN
            resolution_status = V6ResolutionStatus.AMBIGUOUS
            method = V6ResolutionMethod.SUBJECT_AMBIGUOUS
            candidates = expected.subject_candidates
        else:
            reference_id = expected.subject_reference
            reference = references.get(reference_id or "")
            if reference is None:
                status = V6ClaimStateStatus.UNRESOLVED
                entity_type = V6EntityType.UNKNOWN
                resolution_status = V6ResolutionStatus.MISSING
                method = V6ResolutionMethod.SUBJECT_MISSING
                candidates = []
            else:
                status = V6ClaimStateStatus.READY
                entity_type = reference.entity_type
                resolution_status = V6ResolutionStatus.RESOLVED
                method = (
                    V6ResolutionMethod.TRUSTED_CURRENT_PET
                    if reference.entity_type == V6EntityType.CURRENT_PET
                    else V6ResolutionMethod.SELECTED_FROM_TURN_CONTEXT
                )
                candidates = [reference.reference_id]
        return V6SubjectEnrichment(
            claim_id=claim.raw.claim_id,
            batch_id="ideal",
            status=status,
            subject=V6EntityBinding(
                reference_id=reference_id,
                entity_type=entity_type,
                resolution_method=method,
                resolution_status=resolution_status,
                subject_candidates=candidates,
                confidence=1.0,
            ),
            evidence_quote=expected.subject_evidence_quote or expected.evidence_quote,
            review_required=status != V6ClaimStateStatus.READY,
            failure_reason="" if status == V6ClaimStateStatus.READY else "ambiguous",
        )

    @staticmethod
    def _ideal_participants(
        expected: V6ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V6ParticipantEnrichment | None:
        if not expected.participants:
            return None
        bindings = []
        entity_types = {
            "current_pet": V6EntityType.CURRENT_PET,
            "other_pet": V6EntityType.OTHER_PET,
            "user": V6EntityType.USER,
            "caregiver": V6EntityType.CAREGIVER,
            "doctor": V6EntityType.MEDICAL_ACTOR,
            "nurse": V6EntityType.MEDICAL_ACTOR,
        }
        for item in expected.participants:
            if item.role not in {"action_agent", "action_recipient"}:
                continue
            bindings.append(
                V6ParticipantBinding(
                    role=item.role,  # type: ignore[arg-type]
                    entity=V6EntityBinding(
                        reference_id=item.reference_id,
                        entity_type=entity_types.get(
                            item.reference_id, V6EntityType.UNKNOWN
                        ),
                        resolution_method=V6ResolutionMethod.SELECTED_FROM_TURN_CONTEXT,
                        resolution_status=V6ResolutionStatus.RESOLVED,
                        confidence=1.0,
                    ),
                    object_mention=item.object_mention,
                )
            )
        return V6ParticipantEnrichment(
            claim_id=claim.raw.claim_id,
            batch_id="ideal",
            status=V6ClaimStateStatus.READY,
            participants=bindings,
        )

    @staticmethod
    def _ideal_temporal(
        expected: V6ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V6TemporalEnrichment | None:
        if not expected.temporal_quote:
            return None
        return V6TemporalEnrichment(
            claim_id=claim.raw.claim_id,
            batch_id="ideal",
            status=V6ClaimStateStatus.READY,
            relation=expected.temporal_relation,  # type: ignore[arg-type]
            value=expected.temporal_value,
            precision=expected.temporal_precision,  # type: ignore[arg-type]
            normalization_status=V6NormalizedStatus.NORMALIZED,
            temporal_quote=claim.temporal_quote,
            resolution_method="ideal",
        )

    @staticmethod
    def _ideal_measurement(
        expected: V6ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V6MeasurementEnrichment | None:
        if not expected.measurement_quote:
            return None
        return V6MeasurementEnrichment(
            claim_id=claim.raw.claim_id,
            batch_id="ideal",
            status=V6ClaimStateStatus.READY,
            value=expected.measurement_value,
            unit=expected.measurement_unit,
            relation="frequency",
            precision="frequency",
            normalization_status=V6NormalizedStatus.NORMALIZED,
            measurement_quote=claim.measurement_quote,
            resolution_method="ideal",
        )

    def _ideal_canonical(
        self,
        expected: V6ExpectedClaim,
        claim: GovernedThinUserClaim,
    ) -> V6CanonicalMapping:
        terms = self.vocabulary.term_map()
        term = terms.get(expected.canonical_id or "")
        candidates: list[V6CanonicalCandidate] = []
        if term is not None:
            candidates.append(
                V6CanonicalCandidate(
                    candidate_id="c-1",
                    canonical_id=term.canonical_id,
                    canonical_type=term.canonical_type,
                    surface_form=term.aliases[0],
                    score=1.0,
                    recall_source="ideal_control",
                )
            )
        confirmed = expected.mapping_status == "confirmed" and bool(candidates)
        return V6CanonicalMapping(
            claim_id=claim.raw.claim_id,
            status=(
                V6ClaimStateStatus.READY
                if confirmed
                else V6ClaimStateStatus.REVIEW_REQUIRED
            ),
            candidate_set=V6CandidateSet(
                claim_id=claim.raw.claim_id,
                target_quote=claim.raw.target_quote,
                retrieval_query=claim.raw.target_quote,
                retrieval_context="direct_target_quote",
                candidates=candidates,
                recalled_candidates=candidates,
                recall_status="recalled" if candidates else "no_candidate",
                recall_version=f"{self.vocabulary.version}:ideal",
            ),
            selected_candidate_id="c-1" if confirmed else None,
            canonical_id=expected.canonical_id if confirmed else None,
            mapping_status=(
                V6CanonicalMappingStatus.CONFIRMED
                if confirmed
                else V6CanonicalMappingStatus.NOT_FOUND
            ),
            diagnostic=(
                V6CanonicalDiagnostic.NOT_APPLICABLE
                if confirmed
                else V6CanonicalDiagnostic.ALIAS_MISSING
            ),
            selection_margin=1.0 if confirmed else 0.0,
            review_required=not confirmed,
            failure_reason="" if confirmed else "alias_missing",
        )

    def _turn_context(self, case: V6ExpectedCase) -> V6TurnContext:
        return V6TurnContext(
            request_id=f"request-{case.sample_id}",
            trace_id=f"trace-{case.sample_id}",
            user_id="v6-test-user",
            pet_id="v6-test-pet",
            session_id="v6-test-session",
            reference_time=self.document.reference_time,
            current_pet_subject=V6SubjectReference(
                reference_id="current_pet",
                entity_type=V6EntityType.CURRENT_PET,
            ),
            other_subjects=[
                V6SubjectReference.model_validate(item) for item in case.other_subjects
            ],
            previous_question_target=(
                V6PreviousQuestionTarget.model_validate(case.previous_question_target)
                if case.previous_question_target is not None
                else None
            ),
        )


def load_v6_experiment_document(path: Path) -> V6ExperimentDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return V6ExperimentDocument.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid V6 experiment document: {path}") from exc


def write_v6_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v6-{uuid4().hex[:12]}.json"
    passed = sum(item.get("status") == "passed" for item in reports)
    payload = {
        "schema_version": "v6-report-1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "metadata": metadata,
        "summary": {
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "experiments": reports,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def _semantic_signature(item: V6ExpectedClaim) -> dict[str, Any]:
    return {
        "target_quote": normalize_quote_text(item.target_quote),
        "user_statement_type": item.user_statement_type,
        "coarse_type": item.coarse_type,
        "relation": item.relation,
        "subject_reference": item.subject_reference,
        "subject_candidates": tuple(
            item.subject_candidates if item.subject_reference is None else ()
        ),
        "temporal_quote": normalize_quote_text(item.temporal_quote),
        "temporal_relation": item.temporal_relation,
        "temporal_value": item.temporal_value,
        "measurement_quote": normalize_quote_text(item.measurement_quote),
        "measurement_value": item.measurement_value,
        "measurement_unit": item.measurement_unit,
        "canonical_id": item.canonical_id,
        "mapping_status": item.mapping_status,
        "participant_references": tuple(
            (item.role, item.reference_id)
            for item in item.participants
            if item.role in {"action_agent", "action_recipient"}
        ),
    }


def _actual_signature(item: GovernedThinUserClaim) -> dict[str, Any]:
    return {
        "target_quote": item.target_quote.normalized_quote,
        "user_statement_type": item.raw.user_statement_type.value,
        "coarse_type": item.raw.coarse_type.value,
        "relation": item.raw.relation.value,
        "subject_reference": (
            item.subject.subject.reference_id if item.subject else None
        ),
        "subject_candidates": tuple(
            item.subject.subject.subject_candidates
            if item.subject and item.subject.subject.reference_id is None
            else ()
        ),
        "temporal_quote": normalize_quote_text(item.raw.temporal_quote),
        "temporal_relation": (
            item.temporal.relation.value if item.temporal else "unstructured"
        ),
        "temporal_value": item.temporal.value if item.temporal else "",
        "measurement_quote": normalize_quote_text(item.raw.measurement_quote),
        "measurement_value": item.measurement.value if item.measurement else "",
        "measurement_unit": item.measurement.unit if item.measurement else "",
        "canonical_id": item.canonical.canonical_id if item.canonical else None,
        "mapping_status": (
            item.canonical.mapping_status.value if item.canonical else "unresolved"
        ),
        "participant_references": tuple(
            (
                participant.role,
                participant.entity.reference_id or "",
            )
            for participant in (
                item.participants.participants if item.participants else ()
            )
        ),
    }


def _result_signature(result: V6InputAnalysisResult) -> list[dict[str, Any]]:
    return [_actual_signature(item) for item in result.claims]


def _evaluate_signatures(
    *,
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    prefix: str,
    thin_only: bool,
) -> list[str]:
    if thin_only:
        expected = [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "target_quote",
                    "user_statement_type",
                    "coarse_type",
                    "relation",
                    "temporal_quote",
                    "measurement_quote",
                }
            }
            for item in expected
        ]
        actual = [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "target_quote",
                    "user_statement_type",
                    "coarse_type",
                    "relation",
                    "temporal_quote",
                    "measurement_quote",
                }
            }
            for item in actual
        ]
    failures: list[str] = []
    remaining = list(actual)
    for item in expected:
        matched = next(
            (
                candidate
                for candidate in remaining
                if candidate["target_quote"] == item["target_quote"]
                and candidate["user_statement_type"] == item["user_statement_type"]
                and candidate["relation"] == item["relation"]
            ),
            None,
        )
        if matched is None:
            failures.append(
                f"{prefix}:missing_claim_signature:{item['target_quote']}:{item['relation']}"
            )
            continue
        remaining.remove(matched)
        if matched != item:
            differences = [
                f"{key}:{item.get(key)}!={matched.get(key)}"
                for key in item
                if item.get(key) != matched.get(key)
            ]
            failures.append(
                f"{prefix}:claim_signature_mismatch:{item['target_quote']}:{';'.join(differences)}"
            )
    for item in remaining:
        failures.append(
            f"{prefix}:unexpected_claim_signature:{item['target_quote']}:{item['relation']}"
        )
    return failures


def _evaluate_intent(
    expected: V6ExpectedIntent,
    actual: V6TurnIntentRaw,
) -> list[str]:
    failures: list[str] = []
    for field in (
        "answer_now",
        "wants_triage",
        "correction",
        "clarification_request",
        "fact_statement_present",
        "question_present",
        "report_context_present",
    ):
        if getattr(expected, field) != getattr(actual, field):
            failures.append(
                f"intent_mismatch:{field}:{getattr(expected, field)}!={getattr(actual, field)}"
            )
    return failures


def _result_metrics(
    *,
    result: V6InputAnalysisResult,
    sample_id: str,
) -> dict[str, Any]:
    diagnostics = [
        claim.canonical.diagnostic.value
        for claim in result.claims
        if claim.canonical is not None
    ]
    return {
        "sample_id": sample_id,
        "variant": result.variant,
        "schema_valid": not result.failed_blocking_gates(),
        "claim_count": len(result.claims),
        "quote_valid_count": sum(
            claim.evidence_quote.status == "resolved"
            and claim.target_quote.status == "resolved"
            for claim in result.claims
        ),
        "projection_ready_count": sum(
            claim.state.projection_state == V6ClaimStateStatus.READY
            for claim in result.claims
        ),
        "blocked_count": sum(
            any(
                getattr(claim.state, dimension) == V6ClaimStateStatus.BLOCKED
                for dimension in (
                    "quote_state",
                    "statement_state",
                    "subject_state",
                    "participant_state",
                    "temporal_state",
                    "measurement_state",
                    "assertion_state",
                    "canonical_state",
                )
            )
            for claim in result.claims
        ),
        "review_count": sum(
            any(
                getattr(claim.state, dimension)
                in {V6ClaimStateStatus.REVIEW_REQUIRED, V6ClaimStateStatus.AMBIGUOUS}
                for dimension in (
                    "quote_state",
                    "statement_state",
                    "subject_state",
                    "participant_state",
                    "temporal_state",
                    "measurement_state",
                    "assertion_state",
                    "canonical_state",
                )
            )
            for claim in result.claims
        ),
        "canonical_diagnostic_distribution": _count_values(diagnostics),
        "quote_status_distribution": _count_values(
            [
                f"evidence:{claim.evidence_quote.status}:target:{claim.target_quote.status}"
                for claim in result.claims
            ]
        ),
        "claim_trace": [
            {
                "claim_id": claim.raw.claim_id,
                "evidence_quote": claim.raw.evidence_quote,
                "target_quote": claim.raw.target_quote,
                "user_statement_type": claim.raw.user_statement_type.value,
                "relation": claim.raw.relation.value,
                "relation_quote": claim.raw.relation_quote,
                "subject_reference": (
                    claim.subject.subject.reference_id if claim.subject else None
                ),
                "canonical_id": claim.canonical.canonical_id
                if claim.canonical
                else None,
                "mapping_status": (
                    claim.canonical.mapping_status.value if claim.canonical else None
                ),
            }
            for claim in result.claims
        ],
        "model_call_count": result.model_call_count,
        "batch_count": result.batch_count,
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
    }


def _count_values(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _stability_failures(
    signatures_by_sample: dict[str, list[list[dict[str, Any]]]],
) -> list[str]:
    failures: list[str] = []
    for sample_id, runs in signatures_by_sample.items():
        unique = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=list)
            for item in runs
        }
        if len(unique) > 1:
            failures.append(f"{sample_id}:signature_stability:{len(unique)}")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(
            "tests/fixtures/input_preprocessing/sixth_round_thin_shadow_matrix.json"
        ),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
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
        default=Path(".data/evaluations/input-preprocessing-v6-experiments"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    if args.repeat_override is not None and not 1 <= args.repeat_override <= 10:
        raise ValueError("--repeat-override must be between 1 and 10")
    document = load_v6_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    analyzer_factory = None
    if args.mode == "shadow":
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()

        def factory() -> InputPreprocessingV6Analyzer:
            return InputPreprocessingV6Analyzer(
                qwen=QwenClient(settings),
                vocabulary=vocabulary,
                candidate_retriever=V6CandidateRetriever(
                    vocabulary=vocabulary,
                    embeddings=QwenEmbeddingClient(settings),
                ),
                model=os.getenv("INPUT_PREPROCESSING_V6_MODEL", "qwen-plus"),
            )

        analyzer_factory = factory

    runner = V6ArchitectureValidationRunner(
        document=document,
        vocabulary=vocabulary,
        analyzer_factory=analyzer_factory,
    )
    reports = await runner.run(
        mode=args.mode,
        only_experiment_ids=set(args.experiment) if args.experiment else None,
        repeat_override=args.repeat_override,
    )
    path = write_v6_experiment_report(
        output_dir=args.output_dir,
        reports=reports,
        metadata={
            "phase": args.phase,
            "model": os.getenv("INPUT_PREPROCESSING_V6_MODEL", "qwen-plus"),
            "prompt_version": V6_PROMPT_VERSION,
            "schema_version": "v6-thin-raw",
            "policy_version": V6_POLICY_VERSION,
            "graph_version": V6_GRAPH_VERSION,
            "gate_version": V6_GATE_VERSION,
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
