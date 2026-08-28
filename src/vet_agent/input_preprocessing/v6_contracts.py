"""V6 thin-claim, intent, and batched-enrichment contracts.

The model-facing contracts remain intentionally small.  Canonical IDs,
normalized temporal/measurement semantics, and event participants are produced
by deterministic governance or narrowly scoped batch enrichment nodes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V6UserStatementType(StrEnum):
    """Speech-act semantics; this is not a medical assertion."""

    REPORTS = "reports"
    DENIES = "denies"
    REPORTS_NORMAL = "reports_normal"
    REPORTS_ABNORMAL = "reports_abnormal"
    UNCERTAIN = "uncertain"
    HISTORICAL = "historical"
    HYPOTHETICAL = "hypothetical"
    ASKS = "asks"
    CORRECTS = "corrects"


class V6CoarseType(StrEnum):
    """A small structural type; it is not a veterinary taxonomy."""

    SYMPTOM = "symptom"
    STATE = "state"
    ACTION = "action"
    EXPOSURE = "exposure"
    FOOD = "food"
    MEDICATION = "medication"
    TIME = "time"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class V6ClaimRelation(StrEnum):
    """Relation between a target and its stated baseline."""

    ABSOLUTE_STATUS = "absolute_status"
    NO_CHANGE = "no_change"
    CHANGE = "change"
    UNCLEAR = "unclear"


class V6EntityType(StrEnum):
    """Trusted entity types supplied by the service boundary."""

    CURRENT_PET = "current_pet"
    OTHER_PET = "other_pet"
    USER = "user"
    CAREGIVER = "caregiver"
    FOOD = "food"
    MEDICATION = "medication"
    ENVIRONMENT = "environment"
    MEDICAL_ACTOR = "medical_actor"
    SAMPLE = "sample"
    UNKNOWN = "unknown"


class V6ResolutionMethod(StrEnum):
    """Explicit provenance for resolving a reference."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SELECTED_FROM_TURN_CONTEXT = "selected_from_turn_context"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class V6ResolutionStatus(StrEnum):
    """Resolution state for a subject or participant."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class V6EnrichmentType(StrEnum):
    """Finite enrichment node types."""

    REFERENCE = "reference"
    PARTICIPANT = "participant"
    TEMPORAL = "temporal"
    MEASUREMENT = "measurement"
    ASSERTION = "assertion"
    CANONICAL = "canonical"


class V6TemporalRelation(StrEnum):
    """Relation between a temporal quote and its claim."""

    STARTED_AT = "started_at"
    DURATION = "duration"
    FREQUENCY = "frequency"
    ENDED_AT = "ended_at"
    UNSTRUCTURED = "unstructured"


class V6TemporalPrecision(StrEnum):
    """Precision retained by temporal enrichment."""

    EXACT = "exact"
    DAY = "day"
    APPROXIMATE_DURATION = "approximate_duration"
    FREQUENCY = "frequency"
    UNRESOLVED = "unresolved"


class V6NormalizedStatus(StrEnum):
    """Status of a normalized value."""

    NORMALIZED = "normalized"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class V6UnresolvedReason(StrEnum):
    """Why a parser or enrichment result remains unresolved."""

    AMBIGUOUS_EXPRESSION = "ambiguous_expression"
    MISSING_REFERENCE_TIME = "missing_reference_time"
    PARSER_UNSUPPORTED = "parser_unsupported"
    RELATION_BINDING_UNCLEAR = "relation_binding_unclear"
    POLICY_CONSERVATIVE = "policy_conservative"
    MODEL_UNRESOLVED = "model_unresolved"


class V6CanonicalMappingStatus(StrEnum):
    """Governance status of a constrained canonical mapping."""

    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    UNMAPPED_MENTION = "unmapped_mention"
    TYPE_MISMATCH = "type_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"
    NEW_CONCEPT_REQUEST = "new_concept_request"
    NOT_APPLICABLE = "not_applicable"
    UNRESOLVED = "unresolved"


class V6CanonicalDiagnostic(StrEnum):
    """Attribution for canonical under-confirmation."""

    NO_CANDIDATE_RECALLED = "no_candidate_recalled"
    CANDIDATE_BELOW_THRESHOLD = "candidate_below_threshold"
    FILTERED_BY_COARSE_TYPE = "candidate_filtered_by_coarse_type"
    FILTERED_BY_SUBJECT = "candidate_filtered_by_subject"
    PRESENT_NOT_SELECTED = "candidate_present_but_not_selected"
    TOP1_LOW_CONFIDENCE = "top1_low_confidence"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    AGGREGATE_NOT_DECOMPOSED = "aggregate_target_not_decomposed"
    REGISTRY_MISSING = "canonical_missing_in_registry"
    ALIAS_MISSING = "alias_missing"
    CONTEXT_REQUIRED = "context_required"
    NOT_APPLICABLE = "not_applicable"


class V6ClaimStateStatus(StrEnum):
    """State of one claim-governance dimension."""

    PENDING = "pending"
    PLANNED = "planned"
    BATCHED = "batched"
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    READY = "ready"


class V6QualityGateStatus(StrEnum):
    """Outcome of one V6 architecture gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class V6QualityGateAction(StrEnum):
    """Finite action emitted by a V6 gate."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


class V6SubjectReference(BaseModel):
    """A trusted entity supplied by the server boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V6EntityType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class V6PreviousQuestionTarget(BaseModel):
    """Server-owned target context for a short follow-up answer."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    target_surface: str = Field(min_length=1, max_length=120)
    assertion_context: str = Field(default="", max_length=120)


class V6TurnContext(BaseModel):
    """Trusted turn boundary for all V6 experiments."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6"] = "v6"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: V6SubjectReference
    other_subjects: list[V6SubjectReference] = Field(
        default_factory=list, max_length=16
    )
    previous_question_target: V6PreviousQuestionTarget | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def entity_references(self) -> dict[str, V6SubjectReference]:
        """Return trusted references indexed by server-owned ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        return {item.reference_id: item for item in references}


class V6TurnIntentRaw(BaseModel):
    """Parallel input-intent attributes with explicit evidence anchors."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    clarification_request: bool = False
    fact_statement_present: bool = False
    question_present: bool = False
    report_context_present: bool = False
    answer_now_evidence_quote: str = Field(max_length=480)
    wants_triage_evidence_quote: str = Field(max_length=480)
    correction_evidence_quote: str = Field(max_length=480)
    clarification_request_evidence_quote: str = Field(max_length=480)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)

    @model_validator(mode="after")
    def validate_evidence(self) -> V6TurnIntentRaw:
        for flag, quote in (
            (self.answer_now, self.answer_now_evidence_quote),
            (self.wants_triage, self.wants_triage_evidence_quote),
            (self.correction, self.correction_evidence_quote),
            (self.clarification_request, self.clarification_request_evidence_quote),
        ):
            if flag and not quote:
                raise ValueError("explicit_intent_requires_evidence_quote")
        return self


class ThinUserClaimRaw(BaseModel):
    """One minimal quote-anchored user claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    source_id: Literal["current_turn"] = "current_turn"
    source_block_id: str = Field(default="block-current", max_length=96)
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    user_statement_type: V6UserStatementType
    coarse_type: V6CoarseType
    subject_role: Literal[
        "subject", "action_agent", "action_recipient", "experiencer", "unknown"
    ] = "subject"
    subject_status: Literal["pending", "ambiguous", "unresolved"] = "pending"
    temporal_quote: str = Field(default="", max_length=160)
    measurement_quote: str = Field(default="", max_length=160)
    relation_quote: str = Field(default="", max_length=160)
    relation: V6ClaimRelation = V6ClaimRelation.UNCLEAR
    subject_evidence_quote: str = Field(default="", max_length=480)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False


class ThinExtractionRawOutput(BaseModel):
    """Raw output of the V6 thin-claim extraction call."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-thin-raw"] = "v6-thin-raw"
    claims: list[ThinUserClaimRaw] = Field(default_factory=list, max_length=128)


class V6QuoteAnchor(BaseModel):
    """A deterministically resolved conservative quote anchor."""

    model_config = ConfigDict(extra="forbid")

    quote_type: Literal[
        "evidence",
        "target",
        "temporal",
        "measurement",
        "relation",
        "subject_evidence",
        "intent",
    ]
    source_id: Literal["current_turn"] = "current_turn"
    raw_quote: str = Field(min_length=1, max_length=480)
    normalized_quote: str = Field(min_length=1, max_length=480)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    occurrence: int = Field(default=1, ge=0)
    status: Literal[
        "resolved", "ambiguous_occurrence", "not_found", "invalid_containment"
    ]
    normalization_version: str = Field(min_length=1, max_length=80)


class V6EntityBinding(BaseModel):
    """A subject or participant binding resolved from TurnContext."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str | None = Field(default=None, max_length=64)
    entity_type: V6EntityType = V6EntityType.UNKNOWN
    resolution_method: V6ResolutionMethod = V6ResolutionMethod.SUBJECT_MISSING
    resolution_status: V6ResolutionStatus = V6ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6ParticipantBinding(BaseModel):
    """A participant attached to one action claim."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["action_agent", "action_recipient", "experiencer", "action_object"]
    entity: V6EntityBinding
    object_mention: str = Field(default="", max_length=160)


class V6ClaimStateVector(BaseModel):
    """Independent state dimensions for one claim."""

    model_config = ConfigDict(extra="forbid")

    quote_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    statement_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    subject_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    participant_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    temporal_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    measurement_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    assertion_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    canonical_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING
    projection_state: V6ClaimStateStatus = V6ClaimStateStatus.PENDING


class V6ClaimTransition(BaseModel):
    """One auditable claim-state transition."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    event: str = Field(min_length=1, max_length=80)
    dimension: str = Field(min_length=1, max_length=40)
    from_state: str = Field(min_length=1, max_length=40)
    to_state: str = Field(min_length=1, max_length=40)
    reason_code: str = Field(min_length=1, max_length=160)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class EnrichmentRequest(BaseModel):
    """One planned claim-local enrichment request."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=120)
    claim_id: str = Field(min_length=1, max_length=96)
    enrichment_type: V6EnrichmentType
    reason_code: str = Field(min_length=1, max_length=120)
    required_for_projection: bool = True
    priority: int = Field(default=0, ge=0, le=100)


class EnrichmentBatch(BaseModel):
    """A bounded structural batch of enrichment requests."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=120)
    enrichment_type: V6EnrichmentType
    claim_ids: list[str] = Field(min_length=1, max_length=32)
    execution_strategy: Literal["deterministic", "model_batch", "high_risk_singleton"]
    request_ids: list[str] = Field(min_length=1, max_length=32)
    batch_size: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def validate_size(self) -> EnrichmentBatch:
        if len(self.claim_ids) != self.batch_size:
            raise ValueError("batch_size_must_equal_claim_count")
        if len(self.request_ids) != self.batch_size:
            raise ValueError("batch_size_must_equal_request_count")
        if len(set(self.claim_ids)) != self.batch_size:
            raise ValueError("batch_claim_ids_must_be_unique")
        return self


class EnrichmentPlan(BaseModel):
    """Deterministic plan produced before any enrichment adapter runs."""

    model_config = ConfigDict(extra="forbid")

    requests: list[EnrichmentRequest] = Field(default_factory=list, max_length=256)
    batches: list[EnrichmentBatch] = Field(default_factory=list, max_length=128)
    policy_version: str = Field(min_length=1, max_length=120)


class V6SubjectEnrichmentRaw(BaseModel):
    """Model-facing candidate-only subject result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    selected_subject_candidate: str | None = Field(default=None, max_length=64)
    resolution_method: V6ResolutionMethod = V6ResolutionMethod.SUBJECT_MISSING
    resolution_status: V6ResolutionStatus = V6ResolutionStatus.MISSING
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6SubjectBatchRawOutput(BaseModel):
    """Model-facing batched subject enrichment output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-subject-batch-raw"] = "v6-subject-batch-raw"
    results: list[V6SubjectEnrichmentRaw] = Field(min_length=1, max_length=32)


class V6SubjectEnrichment(BaseModel):
    """Governed reference enrichment for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    batch_id: str = Field(default="", max_length=120)
    status: V6ClaimStateStatus
    subject: V6EntityBinding
    evidence_quote: str = Field(default="", max_length=480)
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=200)


class V6ParticipantEnrichmentRaw(BaseModel):
    """Model-facing candidate-only participant result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    action_agent_selected_candidate: str | None = Field(default=None, max_length=64)
    action_recipient_selected_candidate: str | None = Field(default=None, max_length=64)
    experiencer_selected_candidate: str | None = Field(default=None, max_length=64)
    object_mention: str = Field(default="", max_length=160)
    resolution_status: V6ResolutionStatus = V6ResolutionStatus.MISSING
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6ParticipantBatchRawOutput(BaseModel):
    """Model-facing batched participant enrichment output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-participant-batch-raw"] = "v6-participant-batch-raw"
    results: list[V6ParticipantEnrichmentRaw] = Field(min_length=1, max_length=32)


class V6ParticipantEnrichment(BaseModel):
    """Governed candidate-only participant enrichment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    batch_id: str = Field(default="", max_length=120)
    status: V6ClaimStateStatus
    participants: list[V6ParticipantBinding] = Field(default_factory=list, max_length=4)
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=200)


class V6TemporalEnrichment(BaseModel):
    """Governed temporal enrichment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    batch_id: str = Field(default="", max_length=120)
    status: V6ClaimStateStatus
    relation: V6TemporalRelation
    value: str = Field(default="", max_length=160)
    precision: V6TemporalPrecision
    normalization_status: V6NormalizedStatus
    unresolved_reason: V6UnresolvedReason | None = None
    temporal_quote: V6QuoteAnchor | None = None
    resolution_method: Literal["deterministic_parser", "batched_model", "ideal"]
    review_required: bool = False


class V6TemporalBatchRawItem(BaseModel):
    """Model-facing fallback temporal result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    relation: V6TemporalRelation = V6TemporalRelation.UNSTRUCTURED
    value: str = Field(default="", max_length=160)
    precision: V6TemporalPrecision = V6TemporalPrecision.UNRESOLVED
    unresolved_reason: V6UnresolvedReason = V6UnresolvedReason.MODEL_UNRESOLVED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6TemporalBatchRawOutput(BaseModel):
    """Model-facing bounded temporal fallback batch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-temporal-batch-raw"] = "v6-temporal-batch-raw"
    results: list[V6TemporalBatchRawItem] = Field(min_length=1, max_length=32)


class V6MeasurementEnrichment(BaseModel):
    """Governed measurement enrichment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    batch_id: str = Field(default="", max_length=120)
    status: V6ClaimStateStatus
    value: str = Field(default="", max_length=160)
    unit: str = Field(default="", max_length=64)
    relation: str = Field(default="associated_with", max_length=80)
    precision: str = Field(default="unresolved", max_length=40)
    normalization_status: V6NormalizedStatus
    unresolved_reason: V6UnresolvedReason | None = None
    measurement_quote: V6QuoteAnchor | None = None
    resolution_method: Literal["deterministic_parser", "batched_model", "ideal"]
    review_required: bool = False


class V6MeasurementBatchRawItem(BaseModel):
    """Model-facing fallback measurement result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    value: str = Field(default="", max_length=160)
    unit: str = Field(default="", max_length=64)
    relation: str = Field(default="associated_with", max_length=80)
    precision: str = Field(default="unresolved", max_length=40)
    unresolved_reason: V6UnresolvedReason = V6UnresolvedReason.MODEL_UNRESOLVED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6MeasurementBatchRawOutput(BaseModel):
    """Model-facing bounded measurement fallback batch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-measurement-batch-raw"] = "v6-measurement-batch-raw"
    results: list[V6MeasurementBatchRawItem] = Field(min_length=1, max_length=32)


class V6AssertionVerificationRaw(BaseModel):
    """Model-facing assertion verification result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: Literal["verified", "mismatch", "uncertain"] = "uncertain"
    reason_code: str = Field(default="assertion_not_verified", max_length=120)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V6AssertionBatchRawOutput(BaseModel):
    """Model-facing assertion verifier batch."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6-assertion-batch-raw"] = "v6-assertion-batch-raw"
    results: list[V6AssertionVerificationRaw] = Field(min_length=1, max_length=32)


class V6AssertionVerification(BaseModel):
    """Governed verification that a quote supports its speech act."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    batch_id: str = Field(default="", max_length=120)
    status: V6ClaimStateStatus
    verification_status: Literal["verified", "mismatch", "uncertain"]
    reason_code: str = Field(min_length=1, max_length=120)
    review_required: bool = False


class V6CanonicalCandidate(BaseModel):
    """One auditable canonical candidate; it is not yet a fact."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)
    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_type: str = Field(min_length=1, max_length=64)
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["registry_alias", "embedding", "ideal_control"]


class V6CandidateSet(BaseModel):
    """Candidates recalled independently of subject projection."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    target_quote: str = Field(min_length=1, max_length=240)
    retrieval_query: str = Field(min_length=1, max_length=240)
    retrieval_context: Literal[
        "direct_target_quote", "previous_question_target", "hybrid"
    ]
    candidates: list[V6CanonicalCandidate] = Field(default_factory=list, max_length=16)
    recalled_candidates: list[V6CanonicalCandidate] = Field(
        default_factory=list, max_length=32
    )
    filtered_candidates: list[V6CanonicalCandidate] = Field(
        default_factory=list, max_length=32
    )
    filter_reasons: list[str] = Field(default_factory=list, max_length=32)
    recall_status: Literal["recalled", "no_candidate", "not_applicable"]
    recall_version: str = Field(default="", max_length=160)


class V6CanonicalMapping(BaseModel):
    """Code-resolved canonical mapping with under-confirmation attribution."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V6ClaimStateStatus
    candidate_set: V6CandidateSet
    selected_candidate_id: str | None = Field(default=None, max_length=96)
    canonical_id: str | None = Field(default=None, max_length=96)
    mapping_status: V6CanonicalMappingStatus
    diagnostic: V6CanonicalDiagnostic
    selection_margin: float = Field(default=0.0, ge=0.0, le=1.0)
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_mapping(self) -> V6CanonicalMapping:
        if self.mapping_status == V6CanonicalMappingStatus.CONFIRMED:
            if not self.selected_candidate_id or not self.canonical_id:
                raise ValueError("confirmed_mapping_requires_selection")
            if not self.candidate_set.candidates:
                raise ValueError("confirmed_mapping_requires_candidates")
        elif self.canonical_id is not None:
            raise ValueError("only_confirmed_mapping_may_set_canonical_id")
        if (
            self.mapping_status
            in {
                V6CanonicalMappingStatus.NOT_FOUND,
                V6CanonicalMappingStatus.UNMAPPED_MENTION,
                V6CanonicalMappingStatus.AMBIGUOUS,
                V6CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            and not self.review_required
        ):
            raise ValueError("unresolved_mapping_requires_review")
        if (
            self.mapping_status
            in {
                V6CanonicalMappingStatus.NOT_FOUND,
                V6CanonicalMappingStatus.UNMAPPED_MENTION,
            }
            and self.diagnostic == V6CanonicalDiagnostic.NOT_APPLICABLE
        ):
            raise ValueError("not_found_requires_diagnostic_reason")
        return self


class GovernedThinUserClaim(BaseModel):
    """One report-only governed thin claim."""

    model_config = ConfigDict(extra="forbid")

    raw: ThinUserClaimRaw
    evidence_quote: V6QuoteAnchor
    target_quote: V6QuoteAnchor
    temporal_quote: V6QuoteAnchor | None = None
    measurement_quote: V6QuoteAnchor | None = None
    relation_quote: V6QuoteAnchor | None = None
    subject_evidence_quote: V6QuoteAnchor | None = None
    state: V6ClaimStateVector
    subject: V6SubjectEnrichment | None = None
    participants: V6ParticipantEnrichment | None = None
    temporal: V6TemporalEnrichment | None = None
    measurement: V6MeasurementEnrichment | None = None
    assertion: V6AssertionVerification | None = None
    canonical: V6CanonicalMapping | None = None


class V6QualityGateResult(BaseModel):
    """Serializable result of one V6 gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: V6QualityGateStatus
    severity: Literal["blocking", "critical", "major", "minor", "observability_only"]
    reason_code: str = Field(min_length=1, max_length=160)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: V6QualityGateAction
    review_required: bool = False


class V6InputAnalysisResult(BaseModel):
    """Report-only V6 result retained for trace, evaluation, and review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v6"] = "v6"
    strategy: Literal["v6_thin_claim_batched_enrichment_shadow"] = (
        "v6_thin_claim_batched_enrichment_shadow"
    )
    variant: Literal["v6_t0", "v6_t1", "v6_t2", "ideal"]
    turn_context: V6TurnContext
    intent: V6TurnIntentRaw
    raw_claims: list[ThinUserClaimRaw] = Field(default_factory=list, max_length=128)
    claims: list[GovernedThinUserClaim] = Field(default_factory=list, max_length=128)
    intent_quote_anchors: list[V6QuoteAnchor] = Field(
        default_factory=list, max_length=8
    )
    transitions: list[V6ClaimTransition] = Field(default_factory=list)
    enrichment_plan: EnrichmentPlan | None = None
    gates: list[V6QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=120)
    policy_version: str = Field(default="", max_length=120)
    graph_version: str = Field(default="", max_length=120)
    gate_version: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)
    candidate_recall_version: str = Field(default="", max_length=160)
    quote_normalization_version: str = Field(default="", max_length=120)
    model_call_count: int = Field(default=0, ge=0)
    batch_count: int = Field(default=0, ge=0)

    def failed_blocking_gates(self) -> list[V6QualityGateResult]:
        """Return gates that block report-only domain projection."""

        return [
            gate
            for gate in self.gates
            if gate.status == V6QualityGateStatus.FAILED and gate.severity == "blocking"
        ]
