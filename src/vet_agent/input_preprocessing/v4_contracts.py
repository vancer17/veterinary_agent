"""V4 contracts for quote-anchored flat extraction experiments.

V4 deliberately keeps the model-facing schema flat.  Model output never owns a
final canonical ID, a rewritten source, or an entity outside TurnContext.  All
derived governance values are assembled deterministically by later stages.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V4AssertionState(StrEnum):
    """Assertion semantics kept independently from canonical concepts."""

    PRESENT = "present"
    ABSENT = "absent"
    DENIED = "denied"
    DENIED_ABNORMAL = "denied_abnormal"
    NORMAL = "normal"
    ABNORMAL = "abnormal"
    UNCERTAIN = "uncertain"
    POSSIBLE = "possible"
    HYPOTHETICAL = "hypothetical"
    HISTORICAL = "historical"
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class V4Certainty(StrEnum):
    """Certainty attached to an assertion direction."""

    EXPLICIT = "explicit"
    UNCERTAIN = "uncertain"
    REPORTED = "reported"
    UNKNOWN = "unknown"


class V4SemanticClass(StrEnum):
    """Small structural class; it is not a medical taxonomy."""

    STATE = "state"
    EVENT = "event"
    ACTION = "action"
    QUESTION = "question"
    CONTROL_INTENT = "control_intent"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class V4EntityType(StrEnum):
    """Trusted entity types allowed by the V4 TurnContext."""

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


class V4ResolutionMethod(StrEnum):
    """Explicit provenance used to resolve a mention."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class V4ResolutionStatus(StrEnum):
    """Resolution state for a subject or participant."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class V4TemporalRelation(StrEnum):
    """Relation between a temporal quote and an observation."""

    STARTED_AT = "started_at"
    DURATION = "duration"
    FREQUENCY = "frequency"
    ENDED_AT = "ended_at"
    UNSTRUCTURED = "unstructured"


class V4TemporalPrecision(StrEnum):
    """Precision retained by temporal governance."""

    EXACT = "exact"
    DAY = "day"
    APPROXIMATE_DURATION = "approximate_duration"
    FREQUENCY = "frequency"
    UNRESOLVED = "unresolved"


class V4ObservationStatus(StrEnum):
    """Presence or resolution state of temporal / measurement evidence."""

    CONFIRMED_PRESENT = "confirmed_present"
    CONFIRMED_ABSENT = "confirmed_absent"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class V4CanonicalMappingStatus(StrEnum):
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
    UNRESOLVED = "unresolved"


class V4QualityGateStatus(StrEnum):
    """Outcome of one V4 architecture gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class V4QualityGateAction(StrEnum):
    """Finite action emitted by a V4 gate."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


class V4SubjectReference(BaseModel):
    """A trusted entity supplied by the service boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V4EntityType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class V4PreviousQuestionTarget(BaseModel):
    """Server-owned target context for a short follow-up answer."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    canonical_surface: str = Field(min_length=1, max_length=120)
    assertion_context: str = Field(default="", max_length=120)


class V4TurnContext(BaseModel):
    """Trusted turn boundary for all V4 experiments."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v4"] = "v4"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: V4SubjectReference
    other_subjects: list[V4SubjectReference] = Field(default_factory=list, max_length=16)
    previous_question_target: V4PreviousQuestionTarget | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def entity_references(self) -> dict[str, V4SubjectReference]:
        """Return trusted references indexed by server-owned reference ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        return {item.reference_id: item for item in references}


class V4TurnIntentRaw(BaseModel):
    """Raw user-control intent separated from fact observations."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class V4InputProfileRaw(BaseModel):
    """Minimal non-count model profile used by suspicious-empty governance."""

    model_config = ConfigDict(extra="forbid")

    has_factual_statements: bool = False
    has_user_question: bool = False
    has_control_intent: bool = False
    has_uncertainty: bool = False
    has_historical_statement: bool = False
    has_hypothetical_statement: bool = False


class FlatObservationRaw(BaseModel):
    """One flat model observation anchored by exact source quotes."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=96)
    source_id: Literal["current_turn"] = "current_turn"
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    event_or_state_text: str = Field(min_length=1, max_length=360)
    semantic_class: V4SemanticClass
    assertion: V4AssertionState
    certainty: V4Certainty = V4Certainty.EXPLICIT
    subject_reference: str | None = Field(default=None, max_length=64)
    subject_type: V4EntityType = V4EntityType.UNKNOWN
    subject_resolution_method: V4ResolutionMethod = (
        V4ResolutionMethod.SUBJECT_MISSING
    )
    subject_resolution_status: V4ResolutionStatus = V4ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    action_agent_reference: str | None = Field(default=None, max_length=64)
    action_recipient_reference: str | None = Field(default=None, max_length=64)
    experiencer_reference: str | None = Field(default=None, max_length=64)
    object_mention: str = Field(default="", max_length=160)
    temporal_quote: str = Field(default="", max_length=160)
    temporal_relation: V4TemporalRelation = V4TemporalRelation.UNSTRUCTURED
    temporal_value: str = Field(default="", max_length=160)
    temporal_precision: V4TemporalPrecision = V4TemporalPrecision.UNRESOLVED
    temporal_status: V4ObservationStatus = V4ObservationStatus.NOT_APPLICABLE
    measurement_quote: str = Field(default="", max_length=160)
    measurement_value: str = Field(default="", max_length=160)
    measurement_unit: str = Field(default="", max_length=64)
    measurement_status: V4ObservationStatus = V4ObservationStatus.NOT_APPLICABLE
    canonical_surface: str = Field(min_length=1, max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)

    @model_validator(mode="after")
    def validate_quotes(self) -> FlatObservationRaw:
        if self.temporal_quote and self.temporal_status in {
            V4ObservationStatus.NOT_APPLICABLE,
            V4ObservationStatus.CONFIRMED_ABSENT,
        }:
            raise ValueError("temporal_quote_requires_active_status")
        if self.measurement_quote and self.measurement_status in {
            V4ObservationStatus.NOT_APPLICABLE,
            V4ObservationStatus.CONFIRMED_ABSENT,
        }:
            raise ValueError("measurement_quote_requires_active_status")
        if self.semantic_class == V4SemanticClass.CONTROL_INTENT:
            raise ValueError("control_intent_must_not_be_fact_observation")
        return self


class FlatExtractionRawOutput(BaseModel):
    """Raw output of the single flat extraction call."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v4-flat-raw"] = "v4-flat-raw"
    intent: V4TurnIntentRaw
    profile: V4InputProfileRaw
    observations: list[FlatObservationRaw] = Field(
        default_factory=list, max_length=128
    )


class V4QuoteAnchor(BaseModel):
    """A deterministically resolved conservative quote anchor."""

    model_config = ConfigDict(extra="forbid")

    quote_type: Literal[
        "evidence", "target", "temporal", "measurement"
    ]
    source_id: Literal["current_turn"] = "current_turn"
    raw_quote: str = Field(min_length=1, max_length=480)
    normalized_quote: str = Field(min_length=1, max_length=480)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    occurrence: int = Field(default=1, ge=1)
    status: Literal[
        "resolved", "ambiguous_occurrence", "not_found", "invalid_containment"
    ]
    normalization_version: str = Field(min_length=1, max_length=80)


class V4CanonicalCandidate(BaseModel):
    """One auditable candidate; it is not a fact by itself."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)
    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_type: str = Field(min_length=1, max_length=64)
    semantic_class: V4SemanticClass
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["registry_alias", "embedding", "ideal_control"]


class V4CandidateSet(BaseModel):
    """Candidates recalled for exactly one flat observation."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=96)
    canonical_surface: str = Field(min_length=1, max_length=160)
    candidates: list[V4CanonicalCandidate] = Field(default_factory=list, max_length=16)
    recall_status: Literal["recalled", "no_candidate", "not_applicable"] = (
        "no_candidate"
    )
    recall_version: str = Field(default="", max_length=120)


class V4EntityBinding(BaseModel):
    """A subject or participant binding resolved from TurnContext."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str | None = Field(default=None, max_length=64)
    entity_type: V4EntityType = V4EntityType.UNKNOWN
    resolution_method: V4ResolutionMethod = V4ResolutionMethod.SUBJECT_MISSING
    resolution_status: V4ResolutionStatus = V4ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V4ParticipantBinding(BaseModel):
    """A participant attached to a governed flat observation."""

    model_config = ConfigDict(extra="forbid")

    role: Literal[
        "action_agent",
        "action_recipient",
        "experiencer",
        "action_object",
        "source",
        "location",
        "instrument",
        "goal",
        "cause",
    ]
    entity: V4EntityBinding


class GovernedFlatObservation(BaseModel):
    """One report-only observation assembled by deterministic governance."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(min_length=1, max_length=96)
    evidence_quote: V4QuoteAnchor
    target_quote: V4QuoteAnchor
    event_or_state_text: str = Field(min_length=1, max_length=360)
    semantic_class: V4SemanticClass
    assertion: V4AssertionState
    certainty: V4Certainty
    subject: V4EntityBinding
    participants: list[V4ParticipantBinding] = Field(default_factory=list, max_length=8)
    object_mention: str = Field(default="", max_length=160)
    temporal_quote: V4QuoteAnchor | None = None
    temporal_relation: V4TemporalRelation = V4TemporalRelation.UNSTRUCTURED
    temporal_value: str = Field(default="", max_length=160)
    temporal_precision: V4TemporalPrecision = V4TemporalPrecision.UNRESOLVED
    temporal_status: V4ObservationStatus = V4ObservationStatus.NOT_APPLICABLE
    measurement_quote: V4QuoteAnchor | None = None
    measurement_value: str = Field(default="", max_length=160)
    measurement_unit: str = Field(default="", max_length=64)
    measurement_status: V4ObservationStatus = V4ObservationStatus.NOT_APPLICABLE
    canonical_surface: str = Field(min_length=1, max_length=160)
    candidate_set: V4CandidateSet
    selected_candidate_id: str | None = Field(default=None, max_length=96)
    canonical_id: str | None = Field(default=None, max_length=96)
    mapping_status: V4CanonicalMappingStatus
    review_required: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)

    @model_validator(mode="after")
    def validate_mapping(self) -> GovernedFlatObservation:
        if self.mapping_status == V4CanonicalMappingStatus.CONFIRMED:
            if not self.selected_candidate_id or not self.canonical_id:
                raise ValueError("confirmed_mapping_requires_selected_candidate")
            if not self.candidate_set.candidates:
                raise ValueError("confirmed_mapping_requires_candidates")
        elif self.canonical_id is not None:
            raise ValueError("only_confirmed_mapping_may_set_canonical_id")
        if (
            self.mapping_status
            in {
                V4CanonicalMappingStatus.NOT_FOUND,
                V4CanonicalMappingStatus.UNMAPPED_MENTION,
                V4CanonicalMappingStatus.AMBIGUOUS,
                V4CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            and not self.review_required
        ):
            raise ValueError("unresolved_mapping_requires_review")
        return self


class V4QualityGateResult(BaseModel):
    """Serializable result of one synchronous V4 gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: V4QualityGateStatus
    severity: Literal[
        "blocking", "critical", "major", "minor", "observability_only"
    ]
    reason_code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: V4QualityGateAction
    review_required: bool = False


class V4InputAnalysisResult(BaseModel):
    """Report-only V4 result retained for trace, evaluation, and review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v4"] = "v4"
    strategy: Literal["v4_flat_quote_anchored_shadow"] = (
        "v4_flat_quote_anchored_shadow"
    )
    turn_context: V4TurnContext
    intent: V4TurnIntentRaw
    profile: V4InputProfileRaw
    raw_observations: list[FlatObservationRaw] = Field(
        default_factory=list, max_length=128
    )
    observations: list[GovernedFlatObservation] = Field(
        default_factory=list, max_length=128
    )
    gates: list[V4QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)
    candidate_recall_version: str = Field(default="", max_length=120)
    quote_normalization_version: str = Field(default="", max_length=120)

    def failed_blocking_gates(self) -> list[V4QualityGateResult]:
        """Return gates that block any report-only domain projection."""

        return [
            gate
            for gate in self.gates
            if gate.status == V4QualityGateStatus.FAILED and gate.severity == "blocking"
        ]
