"""V6 thin-claim extraction with policy-driven batched enrichment."""

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
from .v6_canonical_linker import V6CandidateRetriever
from .v6_claim_graph import ClaimGraphBuilder
from .v6_contracts import (
    EnrichmentBatch,
    EnrichmentPlan,
    GovernedThinUserClaim,
    ThinExtractionRawOutput,
    ThinUserClaimRaw,
    V6AssertionBatchRawOutput,
    V6AssertionVerification,
    V6ClaimStateStatus,
    V6ClaimTransition,
    V6EnrichmentType,
    V6EntityBinding,
    V6EntityType,
    V6InputAnalysisResult,
    V6MeasurementBatchRawOutput,
    V6MeasurementEnrichment,
    V6NormalizedStatus,
    V6ParticipantBatchRawOutput,
    V6ParticipantBinding,
    V6ParticipantEnrichment,
    V6ResolutionMethod,
    V6ResolutionStatus,
    V6SubjectBatchRawOutput,
    V6SubjectEnrichment,
    V6TemporalBatchRawOutput,
    V6TemporalEnrichment,
    V6TemporalPrecision,
    V6TemporalRelation,
    V6TurnContext,
    V6TurnIntentRaw,
)
from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v6_policy import (
    V6EnrichmentPolicy,
    projection_readiness,
)
from .v6_quote_governance import (
    QUOTE_NORMALIZATION_VERSION,
    ThinClaimQuotes,
    normalize_quote_text,
    resolve_intent_quotes,
    resolve_thin_claim_quotes,
)
from .vocabulary import CanonicalVocabulary

V6_PROMPT_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V6_PROMPT_VERSION",
    "v6-thin-dev-20260826-2",
)
V6_GRAPH_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V6_GRAPH_VERSION",
    "v6-claim-graph-dev-20260826-1",
)


class V6StructuredClient(Protocol):
    """Minimal structured-output interface used by V6."""

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


class InputPreprocessingV6Analyzer:
    """Run independent intent/thin extraction and batched enrichment."""

    def __init__(
        self,
        *,
        qwen: V6StructuredClient,
        vocabulary: CanonicalVocabulary,
        candidate_retriever: V6CandidateRetriever,
        model: str = "qwen-plus",
    ) -> None:
        if not qwen.available:
            raise ValueError("v6_structured_client_unavailable")
        self.qwen = qwen
        self.vocabulary = vocabulary
        self.candidate_retriever = candidate_retriever
        self.model = model
        self.graph = ClaimGraphBuilder()
        self.policy = V6EnrichmentPolicy()

    async def analyze(
        self,
        *,
        user_text: str,
        turn_context: V6TurnContext,
        variant: Literal["v6_t0", "v6_t1", "v6_t2"],
    ) -> V6InputAnalysisResult:
        """Extract thin claims, then execute a planned enrichment graph."""

        started = time.perf_counter()
        model_calls = 0
        attempts: dict[str, int] = {}

        async def intent_call() -> V6TurnIntentRaw:
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
        transitions: list[V6ClaimTransition] = []
        for raw in raw_output.claims:
            claim, claim_transitions = self._create_claim(raw=raw, user_text=user_text)
            claims.append(claim)
            transitions.extend(claim_transitions)
        initial_governance_latency = _elapsed_ms(governance_started)

        decisions = {
            claim.raw.claim_id: self.policy.decide(
                claim,
                always_enrich=variant == "v6_t2",
            )
            for claim in claims
        }
        planner = self._planner()
        plan = (
            planner.plan(
                claims=claims,
                decisions=decisions,
                always_enrich=variant == "v6_t2",
            )
            if variant != "v6_t0"
            else EnrichmentPlan(requests=[], batches=[], policy_version="v6-t0")
        )
        for request in plan.requests:
            claim = self._claim(claims, request.claim_id)
            dimension = _dimension(request.enrichment_type)
            transitions.append(
                self.graph.transition(
                    claim,
                    event="ENRICHMENT_PLANNED",
                    dimension=dimension,
                    to_state=V6ClaimStateStatus.PLANNED,
                    reason_code=request.reason_code,
                    evidence_refs=[request.request_id],
                )
            )

        enrichment_started = time.perf_counter()
        if variant == "v6_t0":
            for claim in claims:
                claim.state.projection_state = V6ClaimStateStatus.UNRESOLVED
                transitions.append(
                    self.graph.transition(
                        claim,
                        event="PROJECTION_READINESS_EVALUATED",
                        dimension="projection_state",
                        to_state=claim.state.projection_state,
                        reason_code="variant_t0_no_enrichment",
                        evidence_refs=[claim.raw.claim_id],
                    )
                )
        else:
            model_calls += await self._run_deterministic_and_batched_enrichment(
                claims=claims,
                plan=plan,
                turn_context=turn_context,
                transitions=transitions,
                attempts=attempts,
            )
            for claim in claims:
                ready, missing = projection_readiness(claim.state)
                claim.state.projection_state = (
                    V6ClaimStateStatus.READY
                    if ready
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
                            if ready
                            else "missing_enrichment:" + ",".join(missing)
                        ),
                        evidence_refs=[claim.raw.claim_id],
                    )
                )

        result = V6InputAnalysisResult(
            variant=variant,
            turn_context=turn_context,
            intent=intent,
            raw_claims=raw_output.claims,
            claims=claims,
            intent_quote_anchors=[
                item
                for item in resolve_intent_quotes(
                    user_text=user_text,
                    intent=intent,
                ).values()
                if item is not None
            ],
            transitions=transitions,
            enrichment_plan=plan,
            model_name=self.model,
            prompt_version=V6_PROMPT_VERSION,
            policy_version=plan.policy_version,
            graph_version=V6_GRAPH_VERSION,
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
            batch_count=len(plan.batches),
        )
        result.gates = self._gates(result)
        return result

    def _analyze_sync_for_test(
        self,
        *,
        user_text: str,
        turn_context: V6TurnContext,
        variant: Literal["v6_t0", "v6_t1", "v6_t2"],
    ) -> V6InputAnalysisResult:
        """Run the analyzer synchronously for deterministic unit tests."""

        async def run() -> V6InputAnalysisResult:
            return await self.analyze(
                user_text=user_text,
                turn_context=turn_context,
                variant=variant,
            )

        return asyncio.run(run())

    async def _run_deterministic_and_batched_enrichment(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        plan: EnrichmentPlan,
        turn_context: V6TurnContext,
        transitions: list[V6ClaimTransition],
        attempts: dict[str, int],
    ) -> int:
        model_calls = 0

        # Deterministic parsers run before any model fallback.
        for request in self._requests(plan, V6EnrichmentType.TEMPORAL):
            claim = self._claim(claims, request.claim_id)
            relation, value, precision, status, reason = parse_temporal(
                temporal_quote=claim.raw.temporal_quote,
                relation_quote=claim.raw.relation_quote,
            )
            temporal = V6TemporalEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id="deterministic-temporal",
                status=(
                    V6ClaimStateStatus.READY
                    if status == V6NormalizedStatus.NORMALIZED
                    else V6ClaimStateStatus.UNRESOLVED
                ),
                relation=relation or V6TemporalRelation.UNSTRUCTURED,
                value=value,
                precision=precision or V6TemporalPrecision.UNRESOLVED,
                normalization_status=status,
                unresolved_reason=reason,
                temporal_quote=claim.temporal_quote,
                resolution_method="deterministic_parser",
                review_required=status == V6NormalizedStatus.UNRESOLVED,
            )
            claim.temporal = temporal
            claim.state.temporal_state = temporal.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="TEMPORAL_DETERMINISTIC_PARSE",
                    dimension="temporal_state",
                    to_state=temporal.status,
                    reason_code=f"parser_{status.value}",
                    evidence_refs=[claim.raw.temporal_quote],
                )
            )

        for request in self._requests(plan, V6EnrichmentType.MEASUREMENT):
            claim = self._claim(claims, request.claim_id)
            (
                measurement_value,
                measurement_unit,
                measurement_relation,
                measurement_precision,
                measurement_status,
                measurement_reason,
            ) = parse_measurement(
                measurement_quote=claim.raw.measurement_quote,
                relation_quote=claim.raw.relation_quote,
            )
            measurement = V6MeasurementEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id="deterministic-measurement",
                status=(
                    V6ClaimStateStatus.READY
                    if measurement_status == V6NormalizedStatus.NORMALIZED
                    else V6ClaimStateStatus.UNRESOLVED
                ),
                value=measurement_value,
                unit=measurement_unit,
                relation=measurement_relation,
                precision=measurement_precision,
                normalization_status=measurement_status,
                unresolved_reason=measurement_reason,
                measurement_quote=claim.measurement_quote,
                resolution_method="deterministic_parser",
                review_required=measurement_status == V6NormalizedStatus.UNRESOLVED,
            )
            claim.measurement = measurement
            claim.state.measurement_state = measurement.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="MEASUREMENT_DETERMINISTIC_PARSE",
                    dimension="measurement_state",
                    to_state=measurement.status,
                    reason_code=f"parser_{measurement_status.value}",
                    evidence_refs=[claim.raw.measurement_quote],
                )
            )

        for batch in plan.batches:
            if batch.enrichment_type == V6EnrichmentType.REFERENCE:
                call_count = await self._subject_batch(
                    claims=claims,
                    batch=batch,
                    turn_context=turn_context,
                    transitions=transitions,
                )
                model_calls += call_count
                attempts[f"reference:{batch.batch_id}"] = call_count
            elif batch.enrichment_type == V6EnrichmentType.PARTICIPANT:
                call_count = await self._participant_batch(
                    claims=claims,
                    batch=batch,
                    turn_context=turn_context,
                    transitions=transitions,
                )
                model_calls += call_count
                attempts[f"participant:{batch.batch_id}"] = call_count
            elif batch.enrichment_type == V6EnrichmentType.TEMPORAL:
                pending = [
                    self._claim(claims, claim_id)
                    for claim_id in batch.claim_ids
                    if _temporal_needs_fallback(self._claim(claims, claim_id))
                ]
                if pending:
                    call_count = await self._temporal_fallback_batch(
                        claims=pending,
                        batch=batch,
                        transitions=transitions,
                    )
                    model_calls += call_count
                    attempts[f"temporal:{batch.batch_id}"] = call_count
            elif batch.enrichment_type == V6EnrichmentType.MEASUREMENT:
                pending = [
                    self._claim(claims, claim_id)
                    for claim_id in batch.claim_ids
                    if _measurement_needs_fallback(self._claim(claims, claim_id))
                ]
                if pending:
                    call_count = await self._measurement_fallback_batch(
                        claims=pending,
                        batch=batch,
                        transitions=transitions,
                    )
                    model_calls += call_count
                    attempts[f"measurement:{batch.batch_id}"] = call_count
            elif batch.enrichment_type == V6EnrichmentType.ASSERTION:
                call_count = await self._assertion_batch(
                    claims=[
                        self._claim(claims, claim_id) for claim_id in batch.claim_ids
                    ],
                    batch=batch,
                    transitions=transitions,
                )
                model_calls += call_count
                attempts[f"assertion:{batch.batch_id}"] = call_count
            elif batch.enrichment_type == V6EnrichmentType.CANONICAL:
                for claim_id in batch.claim_ids:
                    claim = self._claim(claims, claim_id)
                    canonical = self.candidate_retriever.mapping(
                        claim=claim,
                        subject_entity_type=(
                            claim.subject.subject.entity_type
                            if claim.subject
                            else V6EntityType.UNKNOWN
                        ),
                        previous_question_target=(
                            turn_context.previous_question_target.target_surface
                            if turn_context.previous_question_target is not None
                            else None
                        ),
                    )
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
        return model_calls

    async def _subject_batch(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        batch: EnrichmentBatch,
        turn_context: V6TurnContext,
        transitions: list[V6ClaimTransition],
    ) -> int:
        selected_claims = [self._claim(claims, item) for item in batch.claim_ids]

        async def callback() -> V6SubjectBatchRawOutput:
            return await self._structured(
                self._subject_payload(selected_claims, turn_context),
                response_model=V6SubjectBatchRawOutput,
                stage="subject_batch",
            )

        raw, call_count = await self._retry(batch.batch_id, callback)
        results = {item.claim_id: item for item in raw.results}
        _validate_batch_ids(
            expected=set(batch.claim_ids),
            actual=set(results),
            batch_id=batch.batch_id,
        )
        for claim in selected_claims:
            item = results[claim.raw.claim_id]
            references = turn_context.entity_references()
            selected = references.get(item.selected_subject_candidate or "")
            if selected is None:
                if item.resolution_status != V6ResolutionStatus.RESOLVED:
                    status = (
                        V6ClaimStateStatus.AMBIGUOUS
                        if item.resolution_status == V6ResolutionStatus.AMBIGUOUS
                        else V6ClaimStateStatus.UNRESOLVED
                    )
                    candidates = (
                        [
                            reference.reference_id
                            for reference in turn_context.entity_references().values()
                            if reference.entity_type
                            in {V6EntityType.CURRENT_PET, V6EntityType.OTHER_PET}
                        ]
                        if status == V6ClaimStateStatus.AMBIGUOUS
                        else []
                    )
                else:
                    status = V6ClaimStateStatus.BLOCKED
                    candidates = []
            else:
                status = V6ClaimStateStatus.READY
                candidates = [selected.reference_id]
            entity = V6EntityBinding(
                reference_id=selected.reference_id if selected else None,
                entity_type=selected.entity_type if selected else V6EntityType.UNKNOWN,
                resolution_method=item.resolution_method,
                resolution_status=(
                    V6ResolutionStatus.RESOLVED if selected else item.resolution_status
                ),
                subject_candidates=candidates,
                confidence=item.confidence,
            )
            subject = V6SubjectEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id=batch.batch_id,
                status=status,
                subject=entity,
                evidence_quote=(
                    claim.subject_evidence_quote.raw_quote
                    if claim.subject_evidence_quote
                    else claim.raw.evidence_quote
                ),
                review_required=status != V6ClaimStateStatus.READY,
                failure_reason="" if selected else "selected_candidate_invalid",
            )
            claim.subject = subject
            claim.state.subject_state = subject.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="SUBJECT_BATCH_ENRICHED",
                    dimension="subject_state",
                    to_state=subject.status,
                    reason_code=f"subject_{entity.resolution_status.value}",
                    evidence_refs=[subject.evidence_quote],
                )
            )
        return call_count

    async def _participant_batch(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        batch: EnrichmentBatch,
        turn_context: V6TurnContext,
        transitions: list[V6ClaimTransition],
    ) -> int:
        selected_claims = [self._claim(claims, item) for item in batch.claim_ids]

        async def callback() -> V6ParticipantBatchRawOutput:
            return await self._structured(
                self._participant_payload(selected_claims, turn_context),
                response_model=V6ParticipantBatchRawOutput,
                stage="participant_batch",
            )

        raw, call_count = await self._retry(batch.batch_id, callback)
        results = {item.claim_id: item for item in raw.results}
        _validate_batch_ids(
            expected=set(batch.claim_ids),
            actual=set(results),
            batch_id=batch.batch_id,
        )
        references = turn_context.entity_references()
        for claim in selected_claims:
            item = results[claim.raw.claim_id]
            agent = references.get(item.action_agent_selected_candidate or "")
            recipient = references.get(item.action_recipient_selected_candidate or "")
            object_mention = item.object_mention
            valid_object = not object_mention or normalize_quote_text(
                object_mention
            ) in normalize_quote_text(claim.raw.evidence_quote)
            valid_types = (
                agent is not None
                and agent.entity_type
                in {
                    V6EntityType.USER,
                    V6EntityType.CAREGIVER,
                    V6EntityType.MEDICAL_ACTOR,
                }
                and recipient is not None
                and recipient.entity_type
                in {V6EntityType.CURRENT_PET, V6EntityType.OTHER_PET}
            )
            if valid_types and valid_object:
                status = V6ClaimStateStatus.READY
                failure = ""
                participants = [
                    V6ParticipantBinding(
                        role="action_agent",
                        entity=_binding(agent, item.resolution_method, item.confidence),
                    ),
                    V6ParticipantBinding(
                        role="action_recipient",
                        entity=_binding(
                            recipient,
                            item.resolution_method,
                            item.confidence,
                        ),
                    ),
                    V6ParticipantBinding(
                        role="action_object",
                        entity=V6EntityBinding(
                            resolution_method=V6ResolutionMethod.SUBJECT_MISSING,
                            resolution_status=V6ResolutionStatus.MISSING,
                            entity_type=V6EntityType.FOOD,
                            confidence=item.confidence,
                        ),
                        object_mention=object_mention,
                    ),
                ]
            else:
                status = V6ClaimStateStatus.BLOCKED
                failure = (
                    "object_mention_not_supported"
                    if not valid_object
                    else "participant_candidate_invalid"
                )
                participants = []
            participant = V6ParticipantEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id=batch.batch_id,
                status=status,
                participants=participants,
                review_required=status != V6ClaimStateStatus.READY,
                failure_reason=failure,
            )
            claim.participants = participant
            claim.state.participant_state = participant.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="PARTICIPANT_BATCH_ENRICHED",
                    dimension="participant_state",
                    to_state=participant.status,
                    reason_code="participant_batch_valid"
                    if status == V6ClaimStateStatus.READY
                    else failure,
                    evidence_refs=[claim.raw.evidence_quote],
                )
            )
        return call_count

    async def _temporal_fallback_batch(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        batch: EnrichmentBatch,
        transitions: list[V6ClaimTransition],
    ) -> int:
        async def callback() -> V6TemporalBatchRawOutput:
            return await self._structured(
                {
                    "claims": [
                        {
                            "claim_id": claim.raw.claim_id,
                            "temporal_quote": claim.raw.temporal_quote,
                            "relation_quote": claim.raw.relation_quote,
                            "target_quote": claim.raw.target_quote,
                            "evidence_quote": claim.raw.evidence_quote,
                        }
                        for claim in claims
                    ],
                    "output_requirements": {
                        "every_request_claim_id_must_have_one_result": True,
                        "do_not_invent_new_claim": True,
                        "approximate_expression_must_remain_approximate": True,
                    },
                },
                response_model=V6TemporalBatchRawOutput,
                stage="temporal_fallback_batch",
            )

        raw, call_count = await self._retry(batch.batch_id, callback)
        results = {item.claim_id: item for item in raw.results}
        _validate_batch_ids(
            expected={item.raw.claim_id for item in claims},
            actual=set(results),
            batch_id=batch.batch_id,
        )
        for claim in claims:
            item = results[claim.raw.claim_id]
            normalized = item.precision != V6TemporalPrecision.UNRESOLVED
            temporal = V6TemporalEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id=batch.batch_id,
                status=(
                    V6ClaimStateStatus.READY
                    if normalized
                    else V6ClaimStateStatus.UNRESOLVED
                ),
                relation=item.relation,
                value=item.value,
                precision=item.precision,
                normalization_status=(
                    V6NormalizedStatus.NORMALIZED
                    if normalized
                    else V6NormalizedStatus.UNRESOLVED
                ),
                unresolved_reason=item.unresolved_reason,
                temporal_quote=claim.temporal_quote,
                resolution_method="batched_model",
                review_required=not normalized,
            )
            claim.temporal = temporal
            claim.state.temporal_state = temporal.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="TEMPORAL_BATCH_ENRICHED",
                    dimension="temporal_state",
                    to_state=temporal.status,
                    reason_code=f"model_{temporal.normalization_status.value}",
                    evidence_refs=[claim.raw.temporal_quote],
                )
            )
        return call_count

    async def _measurement_fallback_batch(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        batch: EnrichmentBatch,
        transitions: list[V6ClaimTransition],
    ) -> int:
        async def callback() -> V6MeasurementBatchRawOutput:
            return await self._structured(
                {
                    "claims": [
                        {
                            "claim_id": claim.raw.claim_id,
                            "measurement_quote": claim.raw.measurement_quote,
                            "relation_quote": claim.raw.relation_quote,
                            "target_quote": claim.raw.target_quote,
                            "evidence_quote": claim.raw.evidence_quote,
                        }
                        for claim in claims
                    ],
                    "output_requirements": {
                        "every_request_claim_id_must_have_one_result": True,
                        "do_not_guess_colloquial_quantity": True,
                        "unresolved_requires_reason": True,
                    },
                },
                response_model=V6MeasurementBatchRawOutput,
                stage="measurement_fallback_batch",
            )

        raw, call_count = await self._retry(batch.batch_id, callback)
        results = {item.claim_id: item for item in raw.results}
        _validate_batch_ids(
            expected={item.raw.claim_id for item in claims},
            actual=set(results),
            batch_id=batch.batch_id,
        )
        for claim in claims:
            item = results[claim.raw.claim_id]
            normalized = bool(item.value and item.precision != "unresolved")
            measurement = V6MeasurementEnrichment(
                claim_id=claim.raw.claim_id,
                batch_id=batch.batch_id,
                status=(
                    V6ClaimStateStatus.READY
                    if normalized
                    else V6ClaimStateStatus.UNRESOLVED
                ),
                value=item.value,
                unit=item.unit,
                relation=item.relation,
                precision=item.precision,
                normalization_status=(
                    V6NormalizedStatus.NORMALIZED
                    if normalized
                    else V6NormalizedStatus.UNRESOLVED
                ),
                unresolved_reason=item.unresolved_reason,
                measurement_quote=claim.measurement_quote,
                resolution_method="batched_model",
                review_required=not normalized,
            )
            claim.measurement = measurement
            claim.state.measurement_state = measurement.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="MEASUREMENT_BATCH_ENRICHED",
                    dimension="measurement_state",
                    to_state=measurement.status,
                    reason_code=f"model_{measurement.normalization_status.value}",
                    evidence_refs=[claim.raw.measurement_quote],
                )
            )
        return call_count

    async def _assertion_batch(
        self,
        *,
        claims: list[GovernedThinUserClaim],
        batch: EnrichmentBatch,
        transitions: list[V6ClaimTransition],
    ) -> int:
        async def callback() -> V6AssertionBatchRawOutput:
            return await self._structured(
                {
                    "claims": [
                        {
                            "claim_id": claim.raw.claim_id,
                            "evidence_quote": claim.raw.evidence_quote,
                            "target_quote": claim.raw.target_quote,
                            "user_statement_type": claim.raw.user_statement_type.value,
                            "relation": claim.raw.relation.value,
                        }
                        for claim in claims
                    ],
                    "output_requirements": {
                        "verify_speech_act_only": True,
                        "no_medical_risk_judgement": True,
                        "every_request_claim_id_must_have_one_result": True,
                    },
                },
                response_model=V6AssertionBatchRawOutput,
                stage="assertion_batch",
            )

        raw, call_count = await self._retry(batch.batch_id, callback)
        results = {item.claim_id: item for item in raw.results}
        _validate_batch_ids(
            expected={item.raw.claim_id for item in claims},
            actual=set(results),
            batch_id=batch.batch_id,
        )
        for claim in claims:
            item = results[claim.raw.claim_id]
            status = (
                V6ClaimStateStatus.VERIFIED
                if item.status == "verified"
                else V6ClaimStateStatus.REVIEW_REQUIRED
            )
            assertion = V6AssertionVerification(
                claim_id=claim.raw.claim_id,
                batch_id=batch.batch_id,
                status=status,
                verification_status=item.status,
                reason_code=item.reason_code,
                review_required=item.status != "verified",
            )
            claim.assertion = assertion
            claim.state.assertion_state = assertion.status
            transitions.append(
                self.graph.transition(
                    claim,
                    event="ASSERTION_BATCH_VERIFIED",
                    dimension="assertion_state",
                    to_state=assertion.status,
                    reason_code=assertion.reason_code,
                    evidence_refs=[claim.raw.evidence_quote],
                )
            )
        return call_count

    def _create_claim(
        self,
        *,
        raw: ThinUserClaimRaw,
        user_text: str,
    ) -> tuple[GovernedThinUserClaim, list[V6ClaimTransition]]:
        quotes: ThinClaimQuotes = resolve_thin_claim_quotes(
            user_text=user_text,
            raw=raw,
        )
        return self.graph.create_claim(raw=raw, quotes=quotes)

    async def _intent(
        self,
        *,
        user_text: str,
        turn_context: V6TurnContext,
    ) -> V6TurnIntentRaw:
        payload = {
            "user_text": user_text,
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "parallel_input_attributes_only": True,
                "do_not_output_fact_path_required": True,
                "answer_now_and_fact_statement_present_may_both_be_true": True,
                "answer_now_true_when_user_asks_for_summary_advice_or_stage_answer_now": True,
                "answer_now_false_only_when_user_does_not_request_any_answer_now": True,
                "answer_now_evidence_quote_must_be_exact_user_text_supporting_answer_now": True,
                "explicit_intent_requires_exact_evidence_quote": True,
                "model_does_not_decide_system_route": True,
                "do_not_output_facts": True,
            },
        }
        return await self._structured(
            payload,
            response_model=V6TurnIntentRaw,
            stage="intent_router",
        )

    async def _extract_claims(
        self,
        *,
        user_text: str,
        turn_context: V6TurnContext,
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
                "relation_quote_distinguishes_no_change_from_normal": True,
                "do_not_output_canonical_id_or_surface": True,
                "do_not_output_normalized_time_or_measurement": True,
                "do_not_output_action_participants": True,
                "do_not_generate_subject_candidates": True,
                "control_intent_is_not_a_fact_claim": True,
                "temporal_measurement_relation_quotes_are_raw_text_only": True,
            },
        }
        return await self._structured(
            payload,
            response_model=ThinExtractionRawOutput,
            stage="thin_extraction",
        )

    def _subject_payload(
        self,
        claims: list[GovernedThinUserClaim],
        turn_context: V6TurnContext,
    ) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "claim_id": claim.raw.claim_id,
                    "evidence_quote": claim.raw.evidence_quote,
                    "target_quote": claim.raw.target_quote,
                    "subject_evidence_quote": claim.raw.subject_evidence_quote,
                    "subject_role": claim.raw.subject_role,
                    "coarse_type": claim.raw.coarse_type.value,
                }
                for claim in claims
            ],
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "selected_subject_candidate_must_come_from_turn_context": True,
                "candidate_only_no_free_text_entity": True,
                "multi_pet_ambiguity_requires_ambiguous_status": True,
                "do_not_default_to_current_pet": True,
                "every_input_claim_must_have_exactly_one_result": True,
            },
        }

    def _participant_payload(
        self,
        claims: list[GovernedThinUserClaim],
        turn_context: V6TurnContext,
    ) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "claim_id": claim.raw.claim_id,
                    "evidence_quote": claim.raw.evidence_quote,
                    "target_quote": claim.raw.target_quote,
                    "subject_evidence_quote": claim.raw.subject_evidence_quote,
                    "coarse_type": claim.raw.coarse_type.value,
                }
                for claim in claims
            ],
            "turn_context": self._turn_payload(turn_context),
            "output_requirements": {
                "action_agent_and_recipient_must_be_selected_candidates": True,
                "object_mention_must_be_exact_text_from_evidence_quote": True,
                "candidate_only_no_free_text_entity_reference": True,
                "every_input_claim_must_have_exactly_one_result": True,
                "do_not_assign_participants_across_claims": True,
            },
        }

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
                f"v6_{stage}_invalid_schema:{_validation_details(exc)}"
            ) from exc
        except Exception as exc:
            raise InputPreprocessingDependencyError(
                f"v6_{stage}_failed:{type(exc).__name__}:{exc}"
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

    @staticmethod
    def _claim(
        claims: list[GovernedThinUserClaim],
        claim_id: str,
    ) -> GovernedThinUserClaim:
        return next(item for item in claims if item.raw.claim_id == claim_id)

    @staticmethod
    def _requests(
        plan: EnrichmentPlan,
        enrichment_type: V6EnrichmentType,
    ) -> list[Any]:
        return [
            item for item in plan.requests if item.enrichment_type == enrichment_type
        ]

    @staticmethod
    def _planner() -> Any:
        from .v6_policy import EnrichmentPlanner

        return EnrichmentPlanner()

    @staticmethod
    def _gates(result: V6InputAnalysisResult) -> list[Any]:
        from .v6_gates import evaluate_v6_quality_gates

        return evaluate_v6_quality_gates(result=result)

    @staticmethod
    def _turn_payload(turn_context: V6TurnContext) -> dict[str, Any]:
        return {
            "reference_time": turn_context.reference_time.isoformat(),
            "entities": [
                {
                    "candidate_id": reference.reference_id,
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


def _binding(
    reference: Any,
    method: V6ResolutionMethod,
    confidence: float,
) -> V6EntityBinding:
    return V6EntityBinding(
        reference_id=reference.reference_id,
        entity_type=reference.entity_type,
        resolution_method=method,
        resolution_status=V6ResolutionStatus.RESOLVED,
        confidence=confidence,
    )


def _temporal_needs_fallback(claim: GovernedThinUserClaim) -> bool:
    temporal = claim.temporal
    return (
        temporal is not None
        and temporal.normalization_status == V6NormalizedStatus.UNRESOLVED
    )


def _measurement_needs_fallback(claim: GovernedThinUserClaim) -> bool:
    measurement = claim.measurement
    return (
        measurement is not None
        and measurement.normalization_status == V6NormalizedStatus.UNRESOLVED
    )


def _validate_batch_ids(
    *,
    expected: set[str],
    actual: set[str],
    batch_id: str,
) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise InputPreprocessingContractError(
            f"v6_batch_request_coverage_mismatch:{batch_id}:"
            f"missing={','.join(missing)}:unexpected={','.join(unexpected)}"
        )


def _dimension(enrichment_type: V6EnrichmentType) -> str:
    return {
        V6EnrichmentType.REFERENCE: "subject_state",
        V6EnrichmentType.PARTICIPANT: "participant_state",
        V6EnrichmentType.TEMPORAL: "temporal_state",
        V6EnrichmentType.MEASUREMENT: "measurement_state",
        V6EnrichmentType.ASSERTION: "assertion_state",
        V6EnrichmentType.CANONICAL: "canonical_state",
    }[enrichment_type]


def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 的 V6 输入前置预处理实验器。你只输出结构化 JSON，"
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
