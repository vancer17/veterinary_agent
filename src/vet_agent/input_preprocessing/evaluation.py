"""Offline ideal-injection and shadow evaluation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vet_agent import Settings
from vet_agent.agents.semantic_extractor import ConsultationFactStatus
from vet_agent.consultation_state import (
    ConsultationStatePolicyContext,
    ConsultationStateService,
    LocalConsultationAnswerabilityPolicyClient,
    OpaConsultationAnswerabilityPolicyClient,
)
from vet_agent.repositories import ConsultationRuleSet, RuleRepository
from vet_agent.runtime import QwenClient, QwenEmbeddingClient
from vet_agent.services import PetContext

from .analyzer import InputPreprocessingAnalyzer
from .contracts import (
    AssertionState,
    CanonicalStatus,
    DiscourseRole,
    EvidenceAnalysisOutput,
    EvidenceObservation,
    InputAnalysisResult,
    InputContentProfile,
    PreprocessingIntent,
    QualityGateStatus,
    ResolutionStatus,
    SegmentationOutput,
    SegmentModel,
    SubjectBinding,
    SubjectReference,
    SubjectResolutionMethod,
    SubjectType,
    TemporalObservation,
    TemporalPrecision,
    TemporalRelation,
    TurnContext,
)
from .gates import evaluate_quality_gates
from .projection import project_clinical_safety, project_consultation
from .vocabulary import CanonicalVocabulary


class ExpectedTemporal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=160)
    relation: TemporalRelation
    precision: TemporalPrecision
    status: ResolutionStatus


class ExpectedObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_index: int = Field(ge=0, le=63)
    source_text: str = Field(min_length=1, max_length=320)
    canonical_id: str = Field(min_length=1, max_length=96)
    assertion: AssertionState
    subject_reference: str = Field(min_length=1, max_length=64)
    subject_type: SubjectType | None = None
    resolution_method: SubjectResolutionMethod | None = None
    temporal: list[ExpectedTemporal] = Field(default_factory=list, max_length=8)


class ExpectedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=320)
    discourse_role: DiscourseRole
    requires_evidence_analysis: bool = True


class ExpectedBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulate: bool = False
    action: Literal["answer", "ask"]
    answer_now_recognized: bool = False
    answer_now_respected: bool = True
    unknown_slot_count_should_decrease: bool = False
    allow_empty_evidence: bool = False
    subject_ambiguous_expected: bool = False


class ExpectedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = Field(default="", max_length=120)


class ExpectedTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_text: str = Field(min_length=1)
    intent: ExpectedIntent
    segments: list[ExpectedSegment] = Field(min_length=1, max_length=64)
    observations: list[ExpectedObservation] = Field(default_factory=list, max_length=64)
    expected_behavior: ExpectedBehavior


class ExpectedSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    subject_type: SubjectType
    display_name: str = Field(default="", max_length=80)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1, max_length=120)
    task_domain: str = Field(min_length=1, max_length=80)
    turns: list[ExpectedTurn] = Field(min_length=1, max_length=8)
    other_subjects: list[ExpectedSubject] = Field(default_factory=list, max_length=8)


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"]
    reference_time: datetime
    pet_profile: dict[str, Any]
    cases: list[EvaluationCase] = Field(min_length=1)


@dataclass
class _InlineRuleRepository(RuleRepository):
    """Deterministic evaluation catalog without legacy extraction rules."""

    _rules: ConsultationRuleSet

    def consultation_rules(self) -> ConsultationRuleSet:
        return self._rules

    def is_ready(self) -> bool:
        return True


@dataclass
class InputPreprocessingEvaluation:
    """Run ideal injection and optional real-model shadow validation."""

    suite: EvaluationSuite
    vocabulary: CanonicalVocabulary
    policy: Literal["local", "opa"]
    opa_base_url: str = ""
    analyzer: InputPreprocessingAnalyzer | None = None
    reports: list[dict[str, Any]] = field(default_factory=list)

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        repeat: int,
    ) -> list[dict[str, Any]]:
        for case in self.suite.cases:
            for attempt in range(1, max(1, repeat) + 1):
                self.reports.append(
                    await self._run_case(
                        case=case,
                        mode=mode,
                        attempt=attempt,
                    )
                )
        return self.reports

    async def _run_case(
        self,
        *,
        case: EvaluationCase,
        mode: Literal["ideal", "shadow"],
        attempt: int,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        previous_state: dict[str, Any] | None = None
        previous_unknown_count: int | None = None
        turn_reports: list[dict[str, Any]] = []
        case_passed = True

        for turn_index, expected_turn in enumerate(case.turns, start=1):
            turn_context = self._turn_context(case, turn_index=turn_index)
            if mode == "ideal":
                result = self._ideal_result(
                    expected_turn=expected_turn,
                    turn_context=turn_context,
                )
                model_error = None
            else:
                try:
                    assert self.analyzer is not None
                    result = await self.analyzer.analyze(
                        user_text=expected_turn.user_text,
                        turn_context=turn_context,
                    )
                    model_error = None
                except Exception as exc:  # noqa: BLE001 - evaluation reports explicit failure
                    result = None
                    model_error = {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }

            if result is None:
                turn_reports.append(
                    {
                        "turn_index": turn_index,
                        "status": "failed",
                        "model_error": model_error,
                        "expected_semantics": _expected_semantics(expected_turn),
                    }
                )
                case_passed = False
                break

            turn_report = await self._evaluate_result(
                result=result,
                expected_turn=expected_turn,
                case=case,
                mode=mode,
                previous_state=previous_state,
                previous_unknown_count=previous_unknown_count,
            )
            turn_reports.append(turn_report)
            if turn_report["status"] != "passed":
                case_passed = False
            behavior = turn_report.get("behavior_simulation") or {}
            if isinstance(behavior, dict) and behavior.get("state"):
                previous_state = behavior["state"]
                previous_unknown_count = int(behavior.get("unknown_slot_count") or 0)

        return {
            "sample_id": case.sample_id,
            "mode": mode,
            "attempt": attempt,
            "status": "passed" if case_passed else "failed",
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
            "policy_backend": self.policy,
            "vocabulary_version": self.vocabulary.version,
            "model": self.analyzer.model if self.analyzer else "ideal_injection",
            "turns": turn_reports,
        }

    async def _evaluate_result(
        self,
        *,
        result: InputAnalysisResult,
        expected_turn: ExpectedTurn,
        case: EvaluationCase,
        mode: Literal["ideal", "shadow"],
        previous_state: dict[str, Any] | None,
        previous_unknown_count: int | None,
    ) -> dict[str, Any]:
        failures: list[str] = []
        blocking_gates = result.failed_blocking_gates()
        if blocking_gates:
            failures.extend(
                f"blocking_gate:{gate.gate_id}:{gate.reason_code}"
                for gate in blocking_gates
            )

        try:
            consultation = project_consultation(result, vocabulary=self.vocabulary)
            clinical = project_clinical_safety(result, vocabulary=self.vocabulary)
        except Exception as exc:  # noqa: BLE001 - evaluation reports explicit failure
            consultation = None
            clinical = None
            failures.append(f"projection_failed:{type(exc).__name__}")

        expected_semantics = _expected_semantics(expected_turn)
        actual_semantics = _actual_semantics(result)
        semantic_comparison: dict[str, Any] | None = None
        if mode == "shadow":
            missing = sorted(expected_semantics - actual_semantics)
            unexpected = sorted(actual_semantics - expected_semantics)
            semantic_comparison = {
                "expected_count": len(expected_semantics),
                "actual_count": len(actual_semantics),
                "missing": missing,
                "unexpected": unexpected,
                "precision": _ratio(
                    len(expected_semantics & actual_semantics),
                    len(actual_semantics),
                ),
                "recall": _ratio(
                    len(expected_semantics & actual_semantics),
                    len(expected_semantics),
                ),
            }
            if missing or unexpected:
                failures.append("shadow_semantic_mismatch")

        intent_recognized = (
            result.segmentation.intent.answer_now
            == expected_turn.expected_behavior.answer_now_recognized
        )
        if not intent_recognized:
            failures.append("answer_now_intent_mismatch")

        if expected_turn.expected_behavior.subject_ambiguous_expected:
            ambiguous = any(
                observation.subject.subject_reference == "subject_ambiguous"
                for observation in result.evidence.observations
            )
            if not ambiguous:
                failures.append("subject_ambiguous_not_observed")

        behavior: dict[str, Any] | None = None
        if expected_turn.expected_behavior.simulate and consultation is not None:
            service = ConsultationStateService(
                _evaluation_rule_repository(),
                self._policy_client(),
                max_followup_rounds=2,
            )
            decision = await service.update(
                previous_state,
                expected_turn.user_text,
                PetContext(verified_profile=self.suite.pet_profile),
                policy_context=ConsultationStatePolicyContext(
                    request_id=f"eval-{uuid4().hex[:12]}",
                    trace_id=f"trace-{uuid4().hex[:12]}",
                    user_id="eval-user",
                    pet_id="eval-pet",
                    session_id="eval-session",
                ),
                task_domain=case.task_domain,
                semantic_result=consultation.semantic_result,
                max_questions=3,
            )
            unresolved = decision.state.evidence_profile.get("unresolved_slots") or []
            unknown_count = len(unresolved)
            action = "answer" if decision.ready else "ask"
            if action != expected_turn.expected_behavior.action:
                failures.append(f"behavior_action_mismatch:{action}")
            if (
                expected_turn.expected_behavior.unknown_slot_count_should_decrease
                and previous_unknown_count is not None
                and unknown_count >= previous_unknown_count
            ):
                failures.append("unknown_slot_count_did_not_decrease")
            if (
                expected_turn.expected_behavior.answer_now_recognized
                and not decision.ready
            ):
                failures.append("answer_now_not_respected")

            behavior = {
                "action": action,
                "mode": decision.answerability.get("mode"),
                "blocking_slots": decision.missing_slots,
                "unresolved_slots": unresolved,
                "unknown_slot_count": unknown_count,
                "previous_unknown_slot_count": previous_unknown_count,
                "answer_now": consultation.semantic_result.intent.answer_now,
                "answered_slot_repeat_count": len(
                    [
                        slot
                        for slot in decision.missing_slots
                        if decision.state.slots.get(slot)
                    ]
                ),
                "state": decision.state.to_dict(),
            }

        projected_count = (
            len(consultation.semantic_result.facts)
            + len(consultation.semantic_result.observations)
            if consultation is not None
            else 0
        )
        cross_agent_gap = bool(result.evidence.observations) and projected_count == 0
        if cross_agent_gap and not expected_turn.expected_behavior.allow_empty_evidence:
            failures.append("cross_agent_coverage_gap")

        normal_as_denied = any(
            observation.assertion == AssertionState.NORMAL
            and (
                observation.canonical_id
                in {item[0] for item in actual_semantics if item[1] == "denied"}
            )
            for observation in result.evidence.observations
        )
        denied_as_present = any(
            observation.assertion == AssertionState.DENIED
            and (
                observation.canonical_id
                in {item[0] for item in actual_semantics if item[1] == "present"}
            )
            for observation in result.evidence.observations
        )
        if consultation is not None:
            normal_as_denied = normal_as_denied or any(
                fact.metadata.get("assertion") == "normal"
                and fact.status == ConsultationFactStatus.NEGATIVE
                for fact in consultation.semantic_result.facts
            )
            denied_as_present = denied_as_present or any(
                fact.metadata.get("assertion") == "denied"
                and fact.status == ConsultationFactStatus.CONFIRMED
                for fact in consultation.semantic_result.facts
            )
        if normal_as_denied:
            failures.append("normal_projected_as_denied")
        if denied_as_present:
            failures.append("denied_projected_as_present")

        return {
            "status": "passed" if not failures else "failed",
            "failures": failures,
            "intent": result.segmentation.intent.model_dump(),
            "segment_count": len(result.segmentation.segments),
            "evidence_count": len(result.evidence.observations),
            "semantic_comparison": semantic_comparison,
            "gates": [gate.model_dump(mode="json") for gate in result.gates],
            "consultation_projection": (
                {
                    "status": consultation.status,
                    "fact_count": len(consultation.semantic_result.facts),
                    "observation_count": len(consultation.semantic_result.observations),
                    "rejected_count": len(consultation.rejected_observations),
                }
                if consultation is not None
                else None
            ),
            "clinical_safety_projection": (
                {
                    "status": clinical.status,
                    "current_pet_symptom_count": len(clinical.current_pet_symptoms),
                    "denied_count": len(clinical.denied_evidence),
                    "normal_count": len(clinical.normal_evidence),
                    "downstream_evaluation": clinical.downstream_evaluation,
                }
                if clinical is not None
                else None
            ),
            "cross_agent_coverage_gap": cross_agent_gap,
            "metrics": {
                "normal_as_denied": normal_as_denied,
                "denied_as_present": denied_as_present,
                "answer_now_recognized": result.segmentation.intent.answer_now,
                "quality_gate_failed": any(
                    gate.status == QualityGateStatus.FAILED for gate in result.gates
                ),
            },
            "behavior_simulation": behavior,
        }

    def _policy_client(self):
        if self.policy == "opa":
            if not self.opa_base_url:
                raise ValueError("OPA base URL is required for OPA policy mode")
            return OpaConsultationAnswerabilityPolicyClient(
                base_url=self.opa_base_url,
                version="v1",
                package_path="vet_agent.consultation_state",
                rule_name="decision",
            )
        return LocalConsultationAnswerabilityPolicyClient()

    def _turn_context(self, case: EvaluationCase, *, turn_index: int) -> TurnContext:
        other_subjects = [
            SubjectReference(
                reference_id=subject.reference_id,
                subject_type=subject.subject_type,
                display_name=subject.display_name,
            )
            for subject in case.other_subjects
        ]
        return TurnContext(
            request_id=f"eval-{case.sample_id}-{turn_index}",
            trace_id=f"trace-{case.sample_id}-{turn_index}",
            user_id="eval-user",
            pet_id="eval-pet",
            session_id="eval-session",
            task_key=case.sample_id,
            reference_time=self.suite.reference_time,
            current_pet_subject=SubjectReference(
                reference_id="current_pet",
                subject_type="current_pet",
                display_name="当前宠物",
            ),
            other_subjects=other_subjects,
            verified_pet_profile=self.suite.pet_profile,
        )

    def _ideal_result(
        self,
        *,
        expected_turn: ExpectedTurn,
        turn_context: TurnContext,
    ) -> InputAnalysisResult:
        segments = [
            SegmentModel(
                segment_id=f"seg-{index}",
                source_text=segment.source_text,
                analysis_text=segment.source_text,
                discourse_role=segment.discourse_role,
                requires_evidence_analysis=segment.requires_evidence_analysis,
                confidence=0.98,
            )
            for index, segment in enumerate(expected_turn.segments, start=1)
        ]
        expected_count = sum(
            1 for segment in segments if segment.requires_evidence_analysis
        )
        segmentation = SegmentationOutput(
            profile=InputContentProfile(
                expected_fact_candidate_count=expected_count,
                has_user_question=any(
                    segment.discourse_role == DiscourseRole.USER_QUESTION
                    for segment in segments
                ),
                has_control_intent=expected_turn.intent.answer_now,
                has_uncertainty=any(
                    segment.discourse_role == DiscourseRole.UNCERTAIN_STATEMENT
                    for segment in segments
                ),
                has_historical_statement=any(
                    segment.discourse_role == DiscourseRole.HISTORICAL_STATEMENT
                    for segment in segments
                ),
                has_hypothetical_statement=any(
                    segment.discourse_role == DiscourseRole.HYPOTHETICAL_STATEMENT
                    for segment in segments
                ),
            ),
            intent=PreprocessingIntent(**expected_turn.intent.model_dump()),
            segments=segments,
        )
        observations = [
            EvidenceObservation(
                evidence_id=f"ev-{index}",
                segment_id=f"seg-{item.segment_index + 1}",
                source_text=item.source_text,
                canonical_id=item.canonical_id,
                canonical_status=CanonicalStatus.CONFIRMED,
                assertion=item.assertion,
                subject=self._expected_subject(item),
                temporal_observations=[
                    TemporalObservation(
                        temporal_id=f"temporal-{index}-{temporal_index}",
                        segment_id=f"seg-{item.segment_index + 1}",
                        source_text=temporal.source_text,
                        relation=temporal.relation,
                        precision=temporal.precision,
                        status=temporal.status,
                        confidence=0.96,
                    )
                    for temporal_index, temporal in enumerate(item.temporal, start=1)
                ],
                confidence=0.97,
            )
            for index, item in enumerate(expected_turn.observations, start=1)
        ]
        evidence = EvidenceAnalysisOutput(observations=observations)
        gates = evaluate_quality_gates(
            user_text=expected_turn.user_text,
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            vocabulary=self.vocabulary,
        )
        return InputAnalysisResult(
            turn_context=turn_context,
            segmentation=segmentation,
            evidence=evidence,
            gates=gates,
            model_name="ideal_injection",
            vocabulary_version=self.vocabulary.version,
        )

    def _expected_subject(self, item: ExpectedObservation) -> SubjectBinding:
        if item.subject_reference in {"subject_ambiguous", "subject_missing"}:
            method = (
                SubjectResolutionMethod.SUBJECT_AMBIGUOUS
                if item.subject_reference == "subject_ambiguous"
                else SubjectResolutionMethod.SUBJECT_MISSING
            )
            return SubjectBinding(
                subject_reference=item.subject_reference,
                subject_type="unknown",
                resolution_method=method,
                confidence=0.9,
            )
        subject_types: dict[str, SubjectType] = {
            "current_pet": "current_pet",
            "other_pet": "other_pet",
            "user": "user",
            "caregiver": "caregiver",
        }
        methods: dict[str, SubjectResolutionMethod] = {
            "current_pet": SubjectResolutionMethod.TRUSTED_CURRENT_PET,
            "other_pet": SubjectResolutionMethod.EXPLICIT_COREFERENCE,
            "user": SubjectResolutionMethod.EXPLICIT_COREFERENCE,
            "caregiver": SubjectResolutionMethod.EXPLICIT_COREFERENCE,
        }
        subject_type = subject_types.get(item.subject_reference, "unknown")
        method = methods.get(
            item.subject_reference,
            SubjectResolutionMethod.SUBJECT_MISSING,
        )
        return SubjectBinding(
            subject_reference=item.subject_reference,
            subject_type=item.subject_type or subject_type,
            resolution_method=item.resolution_method or method,
            confidence=0.97,
        )


def _evaluation_rule_repository() -> RuleRepository:
    slot_names = (
        "species",
        "life_stage_or_age",
        "weight",
        "onset",
        "mental_status",
        "appetite",
        "vomiting",
        "stool",
        "breathing",
        "pain_or_mobility",
        "behavior_context",
        "current_food",
        "symptom_detail",
    )
    from vet_agent.repositories import ConsultationDomainRule, ConsultationSlotRule

    rules = ConsultationRuleSet(
        domains={
            "gastrointestinal": ConsultationDomainRule(
                domain="gastrointestinal",
                required_slots=[
                    "species",
                    "onset",
                    "mental_status",
                    "appetite",
                    "vomiting",
                    "stool",
                ],
                priority=10,
            ),
            "general": ConsultationDomainRule(
                domain="general",
                required_slots=[
                    "species",
                    "onset",
                    "mental_status",
                    "appetite",
                    "symptom_detail",
                ],
                priority=100,
            ),
        },
        slots={
            slot_name: ConsultationSlotRule(
                slot_name=slot_name,
                question=f"请补充 {slot_name}。",
                label=slot_name,
                priority=index * 10,
            )
            for index, slot_name in enumerate(slot_names, start=1)
        },
        safety_net_text="如出现明显危急情况，请直接联系线下兽医。",
    )
    return _InlineRuleRepository(rules)


def _expected_semantics(turn: ExpectedTurn) -> set[tuple[str, str, str]]:
    return {
        (item.canonical_id, item.assertion.value, item.subject_reference)
        for item in turn.observations
    }


def _actual_semantics(result: InputAnalysisResult) -> set[tuple[str, str, str]]:
    return {
        (
            observation.canonical_id,
            observation.assertion.value,
            observation.subject.subject_reference,
        )
        for observation in result.evidence.observations
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def load_suite(path: Path) -> EvaluationSuite:
    try:
        return EvaluationSuite.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid evaluation suite: {path}") from exc


def build_analyzer(
    *,
    model: str,
    vocabulary: CanonicalVocabulary,
) -> InputPreprocessingAnalyzer:
    api_key = os.getenv("INPUT_PREPROCESSING_LITELLM_API_KEY") or os.getenv(
        "LITELLM_API_KEY"
    )
    base_url = os.getenv("INPUT_PREPROCESSING_LITELLM_BASE_URL") or os.getenv(
        "LITELLM_BASE_URL"
    )
    if not api_key or not base_url:
        raise ValueError("LiteLLM API key and base URL are required for shadow mode")
    settings = Settings(
        litellm_api_key=api_key,
        litellm_base_url=base_url.rstrip("/"),
        request_timeout_seconds=float(
            os.getenv("INPUT_PREPROCESSING_TIMEOUT_SECONDS", "45")
        ),
        qwen_max_retries=int(os.getenv("INPUT_PREPROCESSING_MAX_RETRIES", "1")),
    )
    return InputPreprocessingAnalyzer(
        qwen=QwenClient(settings),
        embeddings=QwenEmbeddingClient(settings),
        vocabulary=vocabulary,
        model=model,
    )


def write_report(
    *,
    output_dir: Path,
    reports: Sequence[dict[str, Any]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-{uuid4().hex[:12]}.json"
    passed = sum(item["status"] == "passed" for item in reports)
    payload = {
        "schema_version": "v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "total": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
        },
        "reports": list(reports),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return path


def _json_default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported report value: {type(value).__name__}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("tests/fixtures/input_preprocessing/quick_validation.json"),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v1.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow", "both"), default="ideal")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--policy", choices=("local", "opa"), default="local")
    parser.add_argument(
        "--opa-base-url", default=os.getenv("INPUT_PREPROCESSING_OPA_BASE_URL", "")
    )
    parser.add_argument(
        "--model", default=os.getenv("INPUT_PREPROCESSING_MODEL", "qwen-plus")
    )
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing"),
    )
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    suite = load_suite(args.samples)
    if args.only:
        suite = suite.model_copy(
            update={
                "cases": [
                    case for case in suite.cases if case.sample_id in set(args.only)
                ]
            }
        )
        if not suite.cases:
            raise ValueError("no evaluation case matched --only")
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    modes: list[Literal["ideal", "shadow"]] = (
        ["ideal", "shadow"] if args.mode == "both" else [args.mode]
    )
    analyzer = (
        build_analyzer(model=args.model, vocabulary=vocabulary)
        if "shadow" in modes
        else None
    )
    evaluation = InputPreprocessingEvaluation(
        suite=suite,
        vocabulary=vocabulary,
        policy=args.policy,
        opa_base_url=args.opa_base_url,
        analyzer=analyzer,
    )
    reports: list[dict[str, Any]] = []
    for mode in modes:
        reports.extend(await evaluation.run(mode=mode, repeat=args.repeat))
    report_path = write_report(output_dir=args.output_dir, reports=reports)
    failed = [item for item in reports if item["status"] != "passed"]
    print(f"report={report_path}")
    print(f"passed={len(reports) - len(failed)} failed={len(failed)}")
    for item in failed:
        print(
            f"FAILED mode={item['mode']} sample={item['sample_id']} "
            f"attempt={item['attempt']}"
        )
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
