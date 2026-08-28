"""V5 thin-claim contracts for quote-anchored, policy-enriched shadow runs.

The model-facing contracts intentionally contain only a thin user claim.  All
canonical IDs, normalized temporal/measurement semantics, and event participants
are produced by deterministic governance or narrowly scoped enrichment nodes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V5UserStatementType(StrEnum):
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


class V5CoarseType(StrEnum):
    """Small structural claim type; it is not a veterinary taxonomy."""

    SYMPTOM = "symptom"
    STATE = "state"
    ACTION = "action"
    EXPOSURE = "exposure"
    FOOD = "food"
    MEDICATION = "medication"
    TIME = "time"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class V5EntityType(StrEnum):
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


class V5ResolutionMethod(StrEnum):
    """Explicit provenance for resolving a reference."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class V5ResolutionStatus(StrEnum):
    """Resolution state for a subject or participant."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class V5EnrichmentType(StrEnum):
    """Finite enrichment node types."""

    REFERENCE = "reference"
    PARTICIPANT = "participant"
    TEMPORAL = "temporal"
    MEASUREMENT = "measurement"
    ASSERTION = "assertion"
    CANONICAL = "canonical"


class V5TemporalRelation(StrEnum):
    """Relation between a temporal quote and its claim."""

    STARTED_AT = "started_at"
    DURATION = "duration"
    FREQUENCY = "frequency"
    ENDED_AT = "ended_at"
    UNSTRUCTURED = "unstructured"


class V5TemporalPrecision(StrEnum):
    """Precision retained by temporal enrichment."""

    EXACT = "exact"
    DAY = "day"
    APPROXIMATE_DURATION = "approximate_duration"
    FREQUENCY = "frequency"
    UNRESOLVED = "unresolved"


class V5NormalizedStatus(StrEnum):
    """Status of a normalized value."""

    NORMALIZED = "normalized"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class V5CanonicalMappingStatus(StrEnum):
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


class V5QualityGateStatus(StrEnum):
    """Outcome of one V5 architecture gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class V5QualityGateAction(StrEnum):
    """Finite action emitted by a V5 gate."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


class V5SubjectReference(BaseModel):
    """A trusted entity supplied by the server boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V5EntityType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class V5PreviousQuestionTarget(BaseModel):
    """Server-owned target context for a short follow-up answer."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    target_surface: str = Field(min_length=1, max_length=120)
    assertion_context: str = Field(default="", max_length=120)


class V5TurnContext(BaseModel):
    """Trusted turn boundary for all V5 experiments."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v5"] = "v5"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: V5SubjectReference
    other_subjects: list[V5SubjectReference] = Field(
        default_factory=list, max_length=16
    )
    previous_question_target: V5PreviousQuestionTarget | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def entity_references(self) -> dict[str, V5SubjectReference]:
        """Return trusted references indexed by server-owned ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        return {item.reference_id: item for item in references}


class V5TurnIntentRaw(BaseModel):
    """Independent control-intent route; never a fact claim."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    clarification: bool = False
    fact_path_required: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class ThinUserClaimRaw(BaseModel):
    """One minimal quote-anchored user claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    source_id: Literal["current_turn"] = "current_turn"
    source_block_id: str = Field(default="block-current", max_length=96)
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    user_statement_type: V5UserStatementType
    coarse_type: V5CoarseType
    subject_role: Literal[
        "subject", "action_agent", "action_recipient", "experiencer", "unknown"
    ] = "subject"
    subject_status: Literal["pending", "ambiguous", "unresolved"] = "pending"
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    temporal_quote: str = Field(default="", max_length=160)
    measurement_quote: str = Field(default="", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False

    @model_validator(mode="after")
    def validate_control_intent(self) -> ThinUserClaimRaw:
        if (
            self.user_statement_type == V5UserStatementType.CORRECTS
            and not self.needs_review
        ):
            raise ValueError("corrective_claim_requires_review")
        return self


class ThinExtractionRawOutput(BaseModel):
    """Raw output of the thin claim extraction call."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v5-thin-raw"] = "v5-thin-raw"
    claims: list[ThinUserClaimRaw] = Field(default_factory=list, max_length=128)


class V5QuoteAnchor(BaseModel):
    """A deterministically resolved conservative quote anchor."""

    model_config = ConfigDict(extra="forbid")

    quote_type: Literal["evidence", "target", "temporal", "measurement"]
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


class V5EntityBinding(BaseModel):
    """A subject or participant binding resolved from TurnContext."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str | None = Field(default=None, max_length=64)
    entity_type: V5EntityType = V5EntityType.UNKNOWN
    resolution_method: V5ResolutionMethod = V5ResolutionMethod.SUBJECT_MISSING
    resolution_status: V5ResolutionStatus = V5ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5ParticipantBinding(BaseModel):
    """A participant attached to one action claim."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["action_agent", "action_recipient", "experiencer", "action_object"]
    entity: V5EntityBinding
    object_mention: str = Field(default="", max_length=160)


class V5ClaimStateStatus(StrEnum):
    """State of one claim-governance dimension."""

    PENDING = "pending"
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    READY = "ready"


class V5ClaimStateVector(BaseModel):
    """Independent state dimensions for one claim."""

    model_config = ConfigDict(extra="forbid")

    quote_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    statement_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    subject_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    participant_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    temporal_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    measurement_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    assertion_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    canonical_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING
    projection_state: V5ClaimStateStatus = V5ClaimStateStatus.PENDING


class V5ClaimTransition(BaseModel):
    """One auditable claim-state transition."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    event: str = Field(min_length=1, max_length=80)
    dimension: str = Field(min_length=1, max_length=40)
    from_state: str = Field(min_length=1, max_length=40)
    to_state: str = Field(min_length=1, max_length=40)
    reason_code: str = Field(min_length=1, max_length=120)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class V5SubjectEnrichment(BaseModel):
    """Reference enrichment for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    subject: V5EntityBinding
    evidence_quote: str = Field(default="", max_length=480)
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=160)


class V5ParticipantEnrichmentRaw(BaseModel):
    """Model-facing participant enrichment output."""

    model_config = ConfigDict(extra="forbid")

    action_agent_reference: str | None = Field(default=None, max_length=64)
    action_recipient_reference: str | None = Field(default=None, max_length=64)
    experiencer_reference: str | None = Field(default=None, max_length=64)
    object_mention: str = Field(default="", max_length=160)
    resolution_status: V5ResolutionStatus = V5ResolutionStatus.MISSING
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5ParticipantEnrichment(BaseModel):
    """Governed participant enrichment for one action claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    participants: list[V5ParticipantBinding] = Field(default_factory=list, max_length=4)
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=160)


class V5SubjectEnrichmentRaw(BaseModel):
    """Model-facing subject enrichment output."""

    model_config = ConfigDict(extra="forbid")

    subject_reference: str | None = Field(default=None, max_length=64)
    resolution_method: V5ResolutionMethod = V5ResolutionMethod.SUBJECT_MISSING
    resolution_status: V5ResolutionStatus = V5ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5TemporalEnrichmentRaw(BaseModel):
    """Model-facing temporal enrichment output."""

    model_config = ConfigDict(extra="forbid")

    relation: V5TemporalRelation = V5TemporalRelation.UNSTRUCTURED
    value: str = Field(default="", max_length=160)
    precision: V5TemporalPrecision = V5TemporalPrecision.UNRESOLVED
    status: V5NormalizedStatus = V5NormalizedStatus.UNRESOLVED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5TemporalEnrichment(BaseModel):
    """Governed temporal enrichment for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    relation: V5TemporalRelation = V5TemporalRelation.UNSTRUCTURED
    value: str = Field(default="", max_length=160)
    precision: V5TemporalPrecision = V5TemporalPrecision.UNRESOLVED
    normalization_status: V5NormalizedStatus = V5NormalizedStatus.NOT_APPLICABLE
    temporal_quote: V5QuoteAnchor | None = None
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=160)


class V5MeasurementEnrichmentRaw(BaseModel):
    """Model-facing measurement enrichment output."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(default="", max_length=160)
    unit: str = Field(default="", max_length=64)
    relation: str = Field(default="associated_with", max_length=80)
    precision: str = Field(default="unresolved", max_length=40)
    status: V5NormalizedStatus = V5NormalizedStatus.UNRESOLVED
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5MeasurementEnrichment(BaseModel):
    """Governed measurement enrichment for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    value: str = Field(default="", max_length=160)
    unit: str = Field(default="", max_length=64)
    relation: str = Field(default="associated_with", max_length=80)
    precision: str = Field(default="unresolved", max_length=40)
    normalization_status: V5NormalizedStatus = V5NormalizedStatus.NOT_APPLICABLE
    measurement_quote: V5QuoteAnchor | None = None
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=160)


class V5AssertionVerificationRaw(BaseModel):
    """Model-facing assertion verification output."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "mismatch", "uncertain"] = "uncertain"
    reason_code: str = Field(default="assertion_not_verified", max_length=120)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V5AssertionVerification(BaseModel):
    """Governed verification that a quote supports its speech act."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    verification_status: Literal["verified", "mismatch", "uncertain"]
    reason_code: str = Field(min_length=1, max_length=120)
    review_required: bool = False


class V5CanonicalCandidate(BaseModel):
    """One auditable canonical candidate; it is not yet a fact."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)
    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_type: str = Field(min_length=1, max_length=64)
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["registry_alias", "embedding", "ideal_control"]


class V5CandidateSet(BaseModel):
    """Candidates recalled from one target quote."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    target_quote: str = Field(min_length=1, max_length=240)
    retrieval_query: str = Field(min_length=1, max_length=240)
    retrieval_context: Literal["direct_target_quote", "previous_question_target"] = (
        "direct_target_quote"
    )
    candidates: list[V5CanonicalCandidate] = Field(default_factory=list, max_length=16)
    recall_status: Literal["recalled", "no_candidate", "not_applicable"]
    recall_version: str = Field(default="", max_length=120)


class V5CanonicalMapping(BaseModel):
    """Code-resolved canonical mapping for one claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    status: V5ClaimStateStatus
    candidate_set: V5CandidateSet
    selected_candidate_id: str | None = Field(default=None, max_length=96)
    canonical_id: str | None = Field(default=None, max_length=96)
    mapping_status: V5CanonicalMappingStatus
    review_required: bool = False
    failure_reason: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_mapping(self) -> V5CanonicalMapping:
        if self.mapping_status == V5CanonicalMappingStatus.CONFIRMED:
            if not self.selected_candidate_id or not self.canonical_id:
                raise ValueError("confirmed_mapping_requires_selection")
            if not self.candidate_set.candidates:
                raise ValueError("confirmed_mapping_requires_candidates")
        elif self.canonical_id is not None:
            raise ValueError("only_confirmed_mapping_may_set_canonical_id")
        if (
            self.mapping_status
            in {
                V5CanonicalMappingStatus.NOT_FOUND,
                V5CanonicalMappingStatus.UNMAPPED_MENTION,
                V5CanonicalMappingStatus.AMBIGUOUS,
                V5CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            and not self.review_required
        ):
            raise ValueError("unresolved_mapping_requires_review")
        return self


class GovernedThinUserClaim(BaseModel):
    """One report-only governed thin claim."""

    model_config = ConfigDict(extra="forbid")

    raw: ThinUserClaimRaw
    evidence_quote: V5QuoteAnchor
    target_quote: V5QuoteAnchor
    temporal_quote: V5QuoteAnchor | None = None
    measurement_quote: V5QuoteAnchor | None = None
    state: V5ClaimStateVector
    subject: V5SubjectEnrichment | None = None
    participants: V5ParticipantEnrichment | None = None
    temporal: V5TemporalEnrichment | None = None
    measurement: V5MeasurementEnrichment | None = None
    assertion: V5AssertionVerification | None = None
    canonical: V5CanonicalMapping | None = None


class V5QualityGateResult(BaseModel):
    """Serializable result of one V5 gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: V5QualityGateStatus
    severity: Literal["blocking", "critical", "major", "minor", "observability_only"]
    reason_code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: V5QualityGateAction
    review_required: bool = False


class V5InputAnalysisResult(BaseModel):
    """Report-only V5 result retained for trace, evaluation, and review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v5"] = "v5"
    strategy: Literal["v5_thin_claim_policy_enrichment_shadow"] = (
        "v5_thin_claim_policy_enrichment_shadow"
    )
    variant: Literal["v5_t0", "v5_t1", "v5_t2", "ideal"]
    turn_context: V5TurnContext
    intent: V5TurnIntentRaw
    raw_claims: list[ThinUserClaimRaw] = Field(default_factory=list, max_length=128)
    claims: list[GovernedThinUserClaim] = Field(default_factory=list, max_length=128)
    transitions: list[V5ClaimTransition] = Field(default_factory=list)
    gates: list[V5QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    prompt_version: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)
    candidate_recall_version: str = Field(default="", max_length=120)
    quote_normalization_version: str = Field(default="", max_length=120)
    model_call_count: int = Field(default=0, ge=0)

    def failed_blocking_gates(self) -> list[V5QualityGateResult]:
        """Return gates that block report-only domain projection."""

        return [
            gate
            for gate in self.gates
            if gate.status == V5QualityGateStatus.FAILED and gate.severity == "blocking"
        ]
