"""Single-call flat extraction with deterministic V4 governance."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from pydantic import ValidationError

from .errors import (
    InputPreprocessingContractError,
    InputPreprocessingDependencyError,
)
from .v4_candidate_linker import V4CandidateRetriever
from .v4_contracts import (
    FlatExtractionRawOutput,
    FlatObservationRaw,
    GovernedFlatObservation,
    V4CanonicalMappingStatus,
    V4EntityBinding,
    V4EntityType,
    V4InputAnalysisResult,
    V4ParticipantBinding,
    V4ResolutionMethod,
    V4ResolutionStatus,
    V4TurnContext,
)
from .v4_gates import evaluate_v4_quality_gates
from .v4_quote_governance import (
    QUOTE_NORMALIZATION_VERSION,
    resolve_observation_quotes,
)
from .vocabulary import CanonicalVocabulary


class V4StructuredClient(Protocol):
    """Minimal structured-output interface used by the flat analyzer."""

    @property
    def available(self) -> bool: ...

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any: ...


class InputPreprocessingV4Analyzer:
    """Run one flat extraction call and govern every quoted observation."""

    def __init__(
        self,
        *,
        qwen: V4StructuredClient,
        vocabulary: CanonicalVocabulary,
        candidate_retriever: V4CandidateRetriever,
        model: str = "qwen-plus",
    ) -> None:
        self.qwen = qwen
        self.vocabulary = vocabulary
        self.candidate_retriever = candidate_retriever
        self.model = model

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: V4TurnContext,
    ) -> V4InputAnalysisResult:
        """Extract flat observations and deterministically govern them."""

        started = time.perf_counter()
        raw, attempts = await self._call_with_one_retry(
            label="flat_extraction",
            callback=lambda: self._extract(
                user_text=user_text,
                turn_context=turn_context,
            ),
        )
        extraction_latency = _elapsed_ms(started)
        governance_started = time.perf_counter()
        observations = [
            self._govern_observation(
                raw=observation,
                user_text=user_text,
                turn_context=turn_context,
            )
            for observation in raw.observations
        ]
        result = V4InputAnalysisResult(
            turn_context=turn_context,
            intent=raw.intent,
            profile=raw.profile,
            raw_observations=raw.observations,
            observations=observations,
            model_name=self.model,
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version=self.candidate_retriever.recall_version,
            quote_normalization_version=QUOTE_NORMALIZATION_VERSION,
            stage_latency_ms={
                "flat_extraction": extraction_latency,
                "deterministic_governance": _elapsed_ms(governance_started),
            },
            stage_attempts={"flat_extraction": attempts},
        )
        result.gates = evaluate_v4_quality_gates(result=result)
        return result

    async def _extract(
        self,
        *,
        user_text: str,
        turn_context: V4TurnContext,
    ) -> FlatExtractionRawOutput:
        payload = {
            "user_text": user_text,
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "one_observation_per_independently_verifiable_fact": True,
                "expand_shared_assertions_to_multiple_observations": True,
                "preserve_user_language": True,
                "evidence_quote_must_be_exact_substring_of_user_text": True,
                "never_translate_or_paraphrase_quotes": True,
                "target_quote_must_be_exact_substring_of_evidence_quote": True,
                "canonical_surface_is_original_language_target_concept": True,
                "do_not_rewrite_quotes": True,
                "do_not_output_canonical_id": True,
                "subject_and_participant_references_must_come_from_turn_context": True,
                "selected_reference_id_implies_resolution_status_resolved": True,
                "unknown_or_missing_subject_leaves_reference_id_null": True,
                "symptom_or_status_observations_use_semantic_class_state": True,
                "intervention_or_care_action_uses_semantic_class_action": True,
                "question_control_intent_and_triage_are_not_fact_observations": True,
                "answer_now_requires_explicit_request_to_answer_before_more_questions": True,
                "temporal_and_measurement_unresolved_when_not_parseable": True,
                "control_intent_must_not_be_fact_observation": True,
            },
        }
        try:
            return await self.qwen.chat_structured(
                _messages(payload),
                response_model=FlatExtractionRawOutput,
                model=self.model,
                temperature=0.0,
            )
        except ValidationError as exc:
            raise InputPreprocessingContractError(
                f"v4_flat_invalid_schema:{_validation_details(exc)}"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"v4_flat_failed:{type(exc).__name__}:{exc}"
            ) from exc

    def _govern_observation(
        self,
        *,
        raw: FlatObservationRaw,
        user_text: str,
        turn_context: V4TurnContext,
    ) -> GovernedFlatObservation:
        evidence, target, temporal, measurement = resolve_observation_quotes(
            user_text=user_text,
            raw=raw,
        )
        subject = self._binding(
            reference_id=raw.subject_reference,
            declared_type=raw.subject_type,
            resolution_method=raw.subject_resolution_method,
            resolution_status=raw.subject_resolution_status,
            subject_candidates=raw.subject_candidates,
            confidence=raw.confidence,
            turn_context=turn_context,
        )
        participants = self._participants(raw, turn_context)
        candidate_set = self.candidate_retriever.recall(
            observation_id=raw.observation_id,
            canonical_surface=raw.canonical_surface,
            subject_entity_type=subject.entity_type,
        )
        selected = self.candidate_retriever.top_candidate(candidate_set)
        mapping_status = (
            V4CanonicalMappingStatus.CONFIRMED
            if selected is not None
            else V4CanonicalMappingStatus.NOT_FOUND
        )
        semantic_class = (
            selected.semantic_class if selected is not None else raw.semantic_class
        )
        return GovernedFlatObservation(
            observation_id=raw.observation_id,
            evidence_quote=evidence,
            target_quote=target,
            event_or_state_text=raw.event_or_state_text,
            semantic_class=semantic_class,
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
            selected_candidate_id=selected.candidate_id if selected else None,
            canonical_id=selected.canonical_id if selected else None,
            mapping_status=mapping_status,
            review_required=selected is None,
            confidence=raw.confidence,
            rationale=raw.rationale,
        )

    def _participants(
        self,
        raw: FlatObservationRaw,
        turn_context: V4TurnContext,
    ) -> list[V4ParticipantBinding]:
        definitions = (
            (
                "action_agent",
                raw.action_agent_reference,
                raw.subject_resolution_method,
                raw.subject_resolution_status,
            ),
            (
                "action_recipient",
                raw.action_recipient_reference,
                raw.subject_resolution_method,
                raw.subject_resolution_status,
            ),
            (
                "experiencer",
                raw.experiencer_reference,
                raw.subject_resolution_method,
                raw.subject_resolution_status,
            ),
        )
        participants = [
            V4ParticipantBinding(
                role=role,  # type: ignore[arg-type]
                entity=self._binding(
                    reference_id=reference_id,
                    declared_type=_declared_type(reference_id, turn_context),
                    resolution_method=method,
                    resolution_status=status,
                    subject_candidates=raw.subject_candidates,
                    confidence=raw.confidence,
                    turn_context=turn_context,
                ),
            )
            for role, reference_id, method, status in definitions
            if reference_id is not None
        ]
        if raw.semantic_class.value == "action":
            participants.append(
                V4ParticipantBinding(
                    role="action_object",
                    entity=V4EntityBinding(
                        resolution_status=V4ResolutionStatus.MISSING,
                    ),
                )
            )
        return participants

    def _binding(
        self,
        *,
        reference_id: str | None,
        declared_type: Any,
        resolution_method: Any,
        resolution_status: Any,
        subject_candidates: list[str],
        confidence: float,
        turn_context: V4TurnContext,
    ) -> V4EntityBinding:
        reference = turn_context.entity_references().get(reference_id or "")
        effective_status = resolution_status
        effective_method = resolution_method
        if reference is not None:
            effective_status = V4ResolutionStatus.RESOLVED
            if effective_method == V4ResolutionMethod.SUBJECT_MISSING:
                effective_method = (
                    V4ResolutionMethod.TRUSTED_CURRENT_PET
                    if reference.entity_type == V4EntityType.CURRENT_PET
                    else V4ResolutionMethod.EXPLICIT_COREFERENCE
                )
        elif effective_status != V4ResolutionStatus.AMBIGUOUS:
            effective_status = V4ResolutionStatus.MISSING
            effective_method = V4ResolutionMethod.SUBJECT_MISSING
        return V4EntityBinding(
            reference_id=reference_id,
            entity_type=reference.entity_type if reference else declared_type,
            resolution_method=effective_method,
            resolution_status=effective_status,
            subject_candidates=subject_candidates,
            confidence=confidence,
        )

    async def _call_with_one_retry(
        self,
        *,
        label: str,
        callback: Any,
    ) -> tuple[Any, int]:
        try:
            return await callback(), 1
        except (InputPreprocessingContractError, InputPreprocessingDependencyError):
            # Same-contract retry only; no alternate parser or local fallback.
            return await callback(), 2

    def _turn_payload(self, turn_context: V4TurnContext) -> dict[str, Any]:
        references = turn_context.entity_references()
        return {
            "reference_time": turn_context.reference_time.isoformat(),
            "entities": [
                {
                    "reference_id": reference.reference_id,
                    "entity_type": reference.entity_type.value,
                    "display_name": reference.display_name,
                }
                for reference in references.values()
            ],
            "previous_question_target": (
                turn_context.previous_question_target.model_dump(mode="json")
                if turn_context.previous_question_target is not None
                else None
            ),
        }


def _declared_type(reference_id: str | None, turn_context: V4TurnContext) -> Any:
    reference = turn_context.entity_references().get(reference_id or "")
    return reference.entity_type if reference is not None else None


def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的 V4 输入前置预处理实验器。你只输出结构化 JSON，"
                "不诊断、不判断风险、不生成建议、不扫描超出用户原文的内容。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _validation_details(exc: ValidationError) -> str:
    details = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())),
            "type": error.get("type", ""),
            "msg": error.get("msg", ""),
        }
        for error in exc.errors()[:20]
    ]
    return json.dumps(details, ensure_ascii=False, separators=(",", ":"))[:2000]


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
