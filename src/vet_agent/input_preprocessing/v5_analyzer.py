"""V5 thin-claim extraction with policy-driven local enrichment."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from .errors import (
    InputPreprocessingContractError,
    InputPreprocessingDependencyError,
)
from .v5_canonical_linker import V5CandidateRetriever
from .v5_claim_graph import ClaimGraphBuilder
from .v5_contracts import (
    GovernedThinUserClaim,
    ThinExtractionRawOutput,
    ThinUserClaimRaw,
    V5AssertionVerification,
    V5AssertionVerificationRaw,
    V5CanonicalMapping,
    V5CanonicalMappingStatus,
    V5ClaimStateStatus,
    V5ClaimTransition,
    V5EntityBinding,
    V5EntityType,
    V5InputAnalysisResult,
    V5MeasurementEnrichment,
    V5MeasurementEnrichmentRaw,
    V5NormalizedStatus,
    V5ParticipantBinding,
    V5ParticipantEnrichment,
    V5ParticipantEnrichmentRaw,
    V5ResolutionMethod,
    V5ResolutionStatus,
    V5SubjectEnrichment,
    V5SubjectEnrichmentRaw,
    V5TemporalEnrichment,
    V5TemporalEnrichmentRaw,
    V5TurnContext,
    V5TurnIntentRaw,
)
from .v5_gates import evaluate_v5_quality_gates
from .v5_policy import V5EnrichmentPolicy, projection_readiness
from .v5_quote_governance import (
    QUOTE_NORMALIZATION_VERSION,
    resolve_thin_claim_quotes,
)
from .vocabulary import CanonicalVocabulary

V5_PROMPT_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V5_PROMPT_VERSION",
    "v5-thin-dev-20260826-1",
)


class V5StructuredClient(Protocol):
    """Minimal structured-output interface used by V5."""

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


class InputPreprocessingV5Analyzer:
    """Run independent intent/thin extraction and governed enrichment."""

    def __init__(
        self,
        *,
        qwen: V5StructuredClient,
        vocabulary: CanonicalVocabulary,
        candidate_retriever: V5CandidateRetriever,
        model: str = "qwen-plus",
    ) -> None:
        if not qwen.available:
            raise ValueError("v5_structured_client_unavailable")
        self.qwen = qwen
        self.vocabulary = vocabulary
        self.candidate_retriever = candidate_retriever
        self.model = model
        self.graph = ClaimGraphBuilder()
        self.policy = V5EnrichmentPolicy()

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: V5TurnContext,
        variant: Literal["v5_t0", "v5_t1", "v5_t2"],
    ) -> V5InputAnalysisResult:
        """Extract thin claims, then enrich only the selected variant."""

        if variant == "ideal":
            raise ValueError("ideal_variant_requires_fixture_runner")
        started = time.perf_counter()
        model_calls = 0
        attempts: dict[str, int] = {}

        async def intent_call() -> V5TurnIntentRaw:
            return await self._intent(user_text=user_text, turn_context=turn_context)

        intent, attempt_count = await self._retry("intent_router", intent_call)
        model_calls += attempt_count
        attempts["intent_router"] = attempt_count

        async def extraction_call() -> ThinExtractionRawOutput:
            return await self._extract_claims(
                user_text=user_text,
                turn_context=turn_context,
            )

        raw_output, attempt_count = await self._retry(
            "thin_extraction",
            extraction_call,
        )
        model_calls += attempt_count
        attempts["thin_extraction"] = attempt_count
        extraction_latency = _elapsed_ms(started)

        governance_started = time.perf_counter()
        claims: list[GovernedThinUserClaim] = []
        transitions: list[V5ClaimTransition] = []
        for raw in raw_output.claims:
            claim, claim_transitions = self._create_claim(
                raw=raw,
                user_text=user_text,
            )
            claims.append(claim)
            transitions.extend(claim_transitions)

        initial_governance_latency = _elapsed_ms(governance_started)
        enrichment_started = time.perf_counter()
        for claim in claims:
            decision = self.policy.decide(
                claim,
                always_enrich=variant == "v5_t2",
            )
            if variant == "v5_t0":
                # T0 reports the thin baseline and never marks it consumable.
                claim.state.projection_state = V5ClaimStateStatus.UNRESOLVED
                continue

            if decision.reference:
                raw_subject, attempt_count = await self._retry(
                    f"reference:{claim.raw.claim_id}",
                    lambda item=claim: self._subject_enrichment(
                        claim=item,
                        turn_context=turn_context,
                    ),
                )
                model_calls += attempt_count
                attempts[f"reference:{claim.raw.claim_id}"] = attempt_count
                subject = self._govern_subject(raw_subject, claim, turn_context)
                claim.subject = subject
                claim.state.subject_state = subject.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="SUBJECT_ENRICHED",
                        dimension="subject_state",
                        to_state=subject.status,
                        reason_code=f"subject_{subject.subject.resolution_status.value}",
                        evidence_refs=[claim.raw.evidence_quote],
                    )
                )

            if decision.participant:
                raw_participant, attempt_count = await self._retry(
                    f"participant:{claim.raw.claim_id}",
                    lambda item=claim: self._participant_enrichment(item),
                )
                model_calls += attempt_count
                attempts[f"participant:{claim.raw.claim_id}"] = attempt_count
                participant = self._govern_participant(
                    raw_participant,
                    claim,
                    turn_context,
                )
                claim.participants = participant
                claim.state.participant_state = participant.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="PARTICIPANT_ENRICHED",
                        dimension="participant_state",
                        to_state=participant.status,
                        reason_code="participant_enrichment_complete",
                        evidence_refs=[claim.raw.evidence_quote],
                    )
                )

            if decision.temporal:
                raw_temporal, attempt_count = await self._retry(
                    f"temporal:{claim.raw.claim_id}",
                    lambda item=claim: self._temporal_enrichment(item, turn_context),
                )
                model_calls += attempt_count
                attempts[f"temporal:{claim.raw.claim_id}"] = attempt_count
                temporal = self._govern_temporal(raw_temporal, claim)
                claim.temporal = temporal
                claim.state.temporal_state = temporal.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="TEMPORAL_ENRICHED",
                        dimension="temporal_state",
                        to_state=temporal.status,
                        reason_code=f"temporal_{temporal.normalization_status.value}",
                        evidence_refs=[claim.raw.temporal_quote],
                    )
                )

            if decision.measurement:
                raw_measurement, attempt_count = await self._retry(
                    f"measurement:{claim.raw.claim_id}",
                    lambda item=claim: self._measurement_enrichment(item),
                )
                model_calls += attempt_count
                attempts[f"measurement:{claim.raw.claim_id}"] = attempt_count
                measurement = self._govern_measurement(raw_measurement, claim)
                claim.measurement = measurement
                claim.state.measurement_state = measurement.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="MEASUREMENT_ENRICHED",
                        dimension="measurement_state",
                        to_state=measurement.status,
                        reason_code=f"measurement_{measurement.normalization_status.value}",
                        evidence_refs=[claim.raw.measurement_quote],
                    )
                )

            if decision.assertion:
                raw_assertion, attempt_count = await self._retry(
                    f"assertion:{claim.raw.claim_id}",
                    lambda item=claim: self._assertion_verification(item),
                )
                model_calls += attempt_count
                attempts[f"assertion:{claim.raw.claim_id}"] = attempt_count
                assertion = self._govern_assertion(raw_assertion, claim)
                claim.assertion = assertion
                claim.state.assertion_state = assertion.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="ASSERTION_VERIFIED",
                        dimension="assertion_state",
                        to_state=assertion.status,
                        reason_code=assertion.reason_code,
                        evidence_refs=[claim.raw.evidence_quote],
                    )
                )

            if decision.canonical:
                canonical = self._canonical_mapping(claim, turn_context)
                claim.canonical = canonical
                claim.state.canonical_state = canonical.status
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="CANONICAL_LINKED",
                        dimension="canonical_state",
                        to_state=canonical.status,
                        reason_code=canonical.mapping_status.value,
                        evidence_refs=[claim.raw.target_quote],
                    )
                )

            ready, missing = projection_readiness(claim.state)
            claim.state.projection_state = (
                V5ClaimStateStatus.READY
                if ready
                else V5ClaimStateStatus.REVIEW_REQUIRED
            )
            transitions.append(
                self.graph.transition(
                    claim,
                    event="PROJECTION_READINESS_EVALUATED",
                    dimension="projection_state",
                    to_state=claim.state.projection_state,
                    reason_code=(
                        "projection_ready"
                        if ready
                        else "missing_enrichment:" + ",".join(missing)
                    ),
                    evidence_refs=[claim.raw.claim_id],
                )
            )

        result = V5InputAnalysisResult(
            variant=variant,
            turn_context=turn_context,
            intent=intent,
            raw_claims=raw_output.claims,
            claims=claims,
            transitions=transitions,
            model_name=self.model,
            prompt_version=V5_PROMPT_VERSION,
            vocabulary_version=self.vocabulary.version,
            candidate_recall_version=self.candidate_retriever.recall_version,
            quote_normalization_version=QUOTE_NORMALIZATION_VERSION,
            stage_latency_ms={
                "intent_and_thin_extraction": extraction_latency,
                "initial_governance": initial_governance_latency,
                "policy_enrichment": _elapsed_ms(enrichment_started),
            },
            stage_attempts=attempts,
            model_call_count=model_calls,
        )
        result.gates = evaluate_v5_quality_gates(result=result)
        return result

    def _analyze_sync_for_test(
        self,
        *,
        user_text: str,
        turn_context: V5TurnContext,
        variant: Literal["v5_t0", "v5_t1", "v5_t2"],
    ) -> V5InputAnalysisResult:
        """Run the analyzer synchronously for deterministic unit tests."""

        async def run() -> V5InputAnalysisResult:
            return await self.analyze(
                user_text=user_text,
                turn_context=turn_context,
                variant=variant,
            )

        return asyncio.run(run())

    def _create_claim(
        self,
        *,
        raw: ThinUserClaimRaw,
        user_text: str,
    ) -> tuple[GovernedThinUserClaim, list[V5ClaimTransition]]:
        evidence, target, temporal, measurement = resolve_thin_claim_quotes(
            user_text=user_text,
            raw=raw,
        )
        return self.graph.create_claim(
            raw=raw,
            evidence_quote=evidence,
            target_quote=target,
            temporal_quote=temporal,
            measurement_quote=measurement,
        )

    async def _intent(
        self,
        *,
        user_text: str,
        turn_context: V5TurnContext,
    ) -> V5TurnIntentRaw:
        payload = {
            "user_text": user_text,
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "control_intent_only": True,
                "answer_now_requires_explicit_request_to_answer_before_more_questions": True,
                "do_not_output_facts": True,
                "do_not_output_canonical": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5TurnIntentRaw,
            stage="intent_router",
        )

    async def _extract_claims(
        self,
        *,
        user_text: str,
        turn_context: V5TurnContext,
    ) -> ThinExtractionRawOutput:
        payload = {
            "user_text": user_text,
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "one_claim_per_independently_verifiable_user_statement": True,
                "expand_shared_assertions_to_multiple_claims": True,
                "evidence_quote_must_be_exact_substring_of_user_text": True,
                "target_quote_must_be_exact_substring_of_evidence_quote": True,
                "use_user_speech_act_not_medical_assertion": True,
                "do_not_output_canonical_id_or_surface": True,
                "do_not_output_normalized_time_or_measurement": True,
                "do_not_output_action_participants": True,
                "subject_candidates_must_come_from_turn_context": True,
                "control_intent_is_not_a_fact_claim": True,
                "temporal_and_measurement_quotes_are_raw_text_only": True,
            },
        }
        return await self._structured(
            payload,
            response_model=ThinExtractionRawOutput,
            stage="thin_extraction",
        )

    async def _subject_enrichment(
        self,
        *,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5SubjectEnrichmentRaw:
        payload = {
            "claim": claim.raw.model_dump(mode="json"),
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "subject_reference_must_come_from_turn_context": True,
                "multi_pet_ambiguity_requires_at_least_two_candidates": True,
                "do_not_default_to_current_pet": True,
                "do_not_rewrite_user_text": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5SubjectEnrichmentRaw,
            stage="reference_enrichment",
        )

    async def _participant_enrichment(
        self,
        claim: GovernedThinUserClaim,
    ) -> V5ParticipantEnrichmentRaw:
        payload = {
            "claim": claim.raw.model_dump(mode="json"),
            "output_requirements": {
                "only_action_intervention_food_medication_claims": True,
                "references_must_come_from_turn_context": True,
                "object_mention_may_be_unresolved": True,
                "do_not_invent_entities": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5ParticipantEnrichmentRaw,
            stage="participant_enrichment",
        )

    async def _temporal_enrichment(
        self,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5TemporalEnrichmentRaw:
        payload = {
            "claim": claim.raw.model_dump(mode="json"),
            "reference_time": turn_context.reference_time.isoformat(),
            "timezone": str(turn_context.reference_time.tzinfo),
            "output_requirements": {
                "only_use_temporal_quote": True,
                "do_not_make_approximate_values_exact": True,
                "unparseable_values_must_be_unresolved": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5TemporalEnrichmentRaw,
            stage="temporal_enrichment",
        )

    async def _measurement_enrichment(
        self,
        claim: GovernedThinUserClaim,
    ) -> V5MeasurementEnrichmentRaw:
        payload = {
            "claim": claim.raw.model_dump(mode="json"),
            "output_requirements": {
                "only_use_measurement_quote": True,
                "do_not_guess_colloquial_quantities": True,
                "unparseable_values_must_be_unresolved": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5MeasurementEnrichmentRaw,
            stage="measurement_enrichment",
        )

    async def _assertion_verification(
        self,
        claim: GovernedThinUserClaim,
    ) -> V5AssertionVerificationRaw:
        payload = {
            "claim": claim.raw.model_dump(mode="json"),
            "output_requirements": {
                "verify_speech_act_only": True,
                "do_not_diagnose_or_assess_risk": True,
                "mismatch_when_quote_does_not_support_statement_type": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V5AssertionVerificationRaw,
            stage="assertion_verification",
        )

    def _govern_subject(
        self,
        raw: V5SubjectEnrichmentRaw,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5SubjectEnrichment:
        references = turn_context.entity_references()
        reference = references.get(raw.subject_reference or "")
        status = V5ClaimStateStatus.READY
        resolution_status = raw.resolution_status
        resolution_method = raw.resolution_method
        if reference is not None:
            resolution_status = V5ResolutionStatus.RESOLVED
            if resolution_method == V5ResolutionMethod.SUBJECT_MISSING:
                resolution_method = (
                    V5ResolutionMethod.TRUSTED_CURRENT_PET
                    if reference.entity_type == V5EntityType.CURRENT_PET
                    else V5ResolutionMethod.EXPLICIT_COREFERENCE
                )
        elif resolution_status == V5ResolutionStatus.AMBIGUOUS:
            if len(raw.subject_candidates) < 2 or any(
                candidate not in references for candidate in raw.subject_candidates
            ):
                status = V5ClaimStateStatus.BLOCKED
        else:
            resolution_status = V5ResolutionStatus.MISSING
            resolution_method = V5ResolutionMethod.SUBJECT_MISSING
            status = V5ClaimStateStatus.BLOCKED
        return V5SubjectEnrichment(
            claim_id=claim.raw.claim_id,
            status=status,
            subject=V5EntityBinding(
                reference_id=raw.subject_reference,
                entity_type=reference.entity_type
                if reference
                else V5EntityType.UNKNOWN,
                resolution_method=resolution_method,
                resolution_status=resolution_status,
                subject_candidates=raw.subject_candidates,
                confidence=raw.confidence,
            ),
            evidence_quote=claim.raw.evidence_quote,
            review_required=status != V5ClaimStateStatus.READY,
            failure_reason=""
            if status == V5ClaimStateStatus.READY
            else "subject_unresolved",
        )

    def _govern_participant(
        self,
        raw: V5ParticipantEnrichmentRaw,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext,
    ) -> V5ParticipantEnrichment:
        references = turn_context.entity_references()
        definitions = (
            ("action_agent", raw.action_agent_reference),
            ("action_recipient", raw.action_recipient_reference),
            ("experiencer", raw.experiencer_reference),
        )
        participants: list[V5ParticipantBinding] = []
        invalid = False
        for role, reference_id in definitions:
            reference = references.get(reference_id or "")
            if reference_id is not None and reference is None:
                invalid = True
            participants.append(
                V5ParticipantBinding(
                    role=role,  # type: ignore[arg-type]
                    entity=V5EntityBinding(
                        reference_id=reference_id,
                        entity_type=reference.entity_type
                        if reference
                        else V5EntityType.UNKNOWN,
                        resolution_method=(
                            V5ResolutionMethod.EXPLICIT_COREFERENCE
                            if reference is not None
                            else V5ResolutionMethod.SUBJECT_MISSING
                        ),
                        resolution_status=(
                            V5ResolutionStatus.RESOLVED
                            if reference is not None
                            else V5ResolutionStatus.MISSING
                        ),
                        confidence=raw.confidence,
                    ),
                )
            )
        participants.append(
            V5ParticipantBinding(
                role="action_object",
                entity=V5EntityBinding(
                    resolution_status=(
                        V5ResolutionStatus.AMBIGUOUS
                        if raw.object_mention
                        else V5ResolutionStatus.MISSING
                    ),
                    entity_type=(
                        V5EntityType.FOOD
                        if claim.raw.coarse_type.value == "food"
                        else V5EntityType.MEDICATION
                        if claim.raw.coarse_type.value == "medication"
                        else V5EntityType.UNKNOWN
                    ),
                ),
                object_mention=raw.object_mention,
            )
        )
        status = V5ClaimStateStatus.BLOCKED if invalid else V5ClaimStateStatus.READY
        return V5ParticipantEnrichment(
            claim_id=claim.raw.claim_id,
            status=status,
            participants=participants,
            review_required=invalid,
            failure_reason="participant_reference_not_trusted" if invalid else "",
        )

    def _govern_temporal(
        self,
        raw: V5TemporalEnrichmentRaw,
        claim: GovernedThinUserClaim,
    ) -> V5TemporalEnrichment:
        status = (
            V5ClaimStateStatus.READY
            if raw.status == V5NormalizedStatus.NORMALIZED
            else V5ClaimStateStatus.UNRESOLVED
        )
        return V5TemporalEnrichment(
            claim_id=claim.raw.claim_id,
            status=status,
            relation=raw.relation,
            value=raw.value,
            precision=raw.precision,
            normalization_status=raw.status,
            temporal_quote=claim.temporal_quote,
            review_required=status != V5ClaimStateStatus.READY,
            failure_reason=""
            if status == V5ClaimStateStatus.READY
            else "temporal_unresolved",
        )

    def _govern_measurement(
        self,
        raw: V5MeasurementEnrichmentRaw,
        claim: GovernedThinUserClaim,
    ) -> V5MeasurementEnrichment:
        status = (
            V5ClaimStateStatus.READY
            if raw.status == V5NormalizedStatus.NORMALIZED
            else V5ClaimStateStatus.UNRESOLVED
        )
        return V5MeasurementEnrichment(
            claim_id=claim.raw.claim_id,
            status=status,
            value=raw.value,
            unit=raw.unit,
            relation=raw.relation,
            precision=raw.precision,
            normalization_status=raw.status,
            measurement_quote=claim.measurement_quote,
            review_required=status != V5ClaimStateStatus.READY,
            failure_reason=""
            if status == V5ClaimStateStatus.READY
            else "measurement_unresolved",
        )

    def _govern_assertion(
        self,
        raw: V5AssertionVerificationRaw,
        claim: GovernedThinUserClaim,
    ) -> V5AssertionVerification:
        status = {
            "verified": V5ClaimStateStatus.VERIFIED,
            "mismatch": V5ClaimStateStatus.BLOCKED,
            "uncertain": V5ClaimStateStatus.REVIEW_REQUIRED,
        }[raw.status]
        return V5AssertionVerification(
            claim_id=claim.raw.claim_id,
            status=status,
            verification_status=raw.status,
            reason_code=raw.reason_code,
            review_required=status != V5ClaimStateStatus.VERIFIED,
        )

    def _canonical_mapping(
        self,
        claim: GovernedThinUserClaim,
        turn_context: V5TurnContext | None = None,
    ) -> V5CanonicalMapping:
        subject_type = (
            claim.subject.subject.entity_type
            if claim.subject is not None
            else V5EntityType.UNKNOWN
        )
        retrieval_quote = claim.raw.target_quote
        retrieval_context = "direct_target_quote"
        if (
            turn_context is not None
            and turn_context.previous_question_target is not None
            and claim.raw.target_quote == claim.raw.evidence_quote
        ):
            retrieval_quote = turn_context.previous_question_target.target_surface
            retrieval_context = "previous_question_target"
        candidate_set = self.candidate_retriever.recall(
            claim_id=claim.raw.claim_id,
            target_quote=claim.raw.target_quote,
            retrieval_quote=retrieval_quote,
            retrieval_context=retrieval_context,
            subject_entity_type=subject_type,
            coarse_type=claim.raw.coarse_type.value,
        )
        selected = self.candidate_retriever.top_candidate(candidate_set)
        return V5CanonicalMapping(
            claim_id=claim.raw.claim_id,
            status=(
                V5ClaimStateStatus.READY
                if selected is not None
                else V5ClaimStateStatus.REVIEW_REQUIRED
            ),
            candidate_set=candidate_set,
            selected_candidate_id=selected.candidate_id if selected else None,
            canonical_id=selected.canonical_id if selected else None,
            mapping_status=(
                V5CanonicalMappingStatus.CONFIRMED
                if selected is not None
                else V5CanonicalMappingStatus.NOT_FOUND
            ),
            review_required=selected is None,
            failure_reason="" if selected is not None else "canonical_no_candidate",
        )

    async def _structured(
        self,
        payload: dict[str, Any],
        *,
        response_model: type,
        stage: str,
    ) -> Any:
        try:
            return await self.qwen.chat_structured(
                _messages(payload),
                response_model=response_model,
                model=self.model,
                temperature=0.0,
            )
        except ValidationError as exc:
            raise InputPreprocessingContractError(
                f"v5_{stage}_invalid_schema:{_validation_details(exc)}"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"v5_{stage}_failed:{type(exc).__name__}:{exc}"
            ) from exc

    async def _retry(
        self,
        label: str,
        callback: Any,
    ) -> tuple[Any, int]:
        try:
            return await callback(), 1
        except (
            InputPreprocessingContractError,
            InputPreprocessingDependencyError,
        ):
            return await callback(), 2

    def _turn_payload(self, turn_context: V5TurnContext) -> dict[str, Any]:
        return {
            "reference_time": turn_context.reference_time.isoformat(),
            "entities": [
                {
                    "reference_id": reference.reference_id,
                    "entity_type": reference.entity_type.value,
                    "display_name": reference.display_name,
                }
                for reference in turn_context.entity_references().values()
            ],
            "previous_question_target": (
                turn_context.previous_question_target.model_dump(mode="json")
                if turn_context.previous_question_target is not None
                else None
            ),
        }


def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的 V5 输入前置预处理实验器。你只输出结构化 JSON，"
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
