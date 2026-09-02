"""V2 input-preprocessing contracts for the second shadow validation round.

These models intentionally live beside the production consultation and clinical
safety contracts.  They are experiment-first contracts: every stage communicates
through explicit structures and every unresolved value keeps a status instead of
being silently coerced into a fact.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V2AssertionState(StrEnum):
    """Assertion semantics kept separate from canonical concepts."""

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


class V2DiscourseRole(StrEnum):
    """Discourse role assigned by Stage 1."""

    FACT_STATEMENT = "fact_statement"
    USER_QUESTION = "user_question"
    CONTROL_INTENT = "control_intent"
    HISTORICAL_STATEMENT = "historical_statement"
    HYPOTHETICAL_STATEMENT = "hypothetical_statement"
    UNCERTAIN_STATEMENT = "uncertain_statement"
    OTHER = "other"


class V2EntityType(StrEnum):
    """Entity types allowed in a trusted TurnContext."""

    CURRENT_PET = "current_pet"
    OTHER_PET = "other_pet"
    USER = "user"
    CAREGIVER = "caregiver"
    FOOD = "food"
    ENVIRONMENT = "environment"
    MEDICAL_ACTOR = "medical_actor"
    SAMPLE = "sample"
    UNKNOWN = "unknown"


class V2ResolutionMethod(StrEnum):
    """Explicit provenance used to resolve an entity mention."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class V2ResolutionStatus(StrEnum):
    """Resolution state for a subject or participant."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class V2CanonicalMappingStatus(StrEnum):
    """Governance status of a canonical mapping decision."""

    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    UNMAPPED_MENTION = "unmapped_mention"
    TYPE_MISMATCH = "type_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"
    NEW_CONCEPT_REQUEST = "new_concept_request"


class V2QualityGateStatus(StrEnum):
    """Outcome of one V2 architecture gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class V2QualityGateAction(StrEnum):
    """Finite action associated with a gate result."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


class V2TemporalRelation(StrEnum):
    STARTED_AT = "started_at"
    DURATION = "duration"
    FREQUENCY = "frequency"
    ENDED_AT = "ended_at"
    UNSTRUCTURED = "unstructured"


class V2TemporalPrecision(StrEnum):
    EXACT = "exact"
    DAY = "day"
    APPROXIMATE_DURATION = "approximate_duration"
    FREQUENCY = "frequency"
    UNRESOLVED = "unresolved"


class V2ResolutionQuality(StrEnum):
    CONFIRMED = "confirmed"
    IMPRECISE = "imprecise"
    UNRESOLVED = "unresolved"


class V2SubjectReference(BaseModel):
    """A trusted entity supplied by the service boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V2EntityType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class V2TurnContext(BaseModel):
    """Trusted scope for Stage 1 and Stage 2 analysis."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"] = "v2"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: V2SubjectReference
    other_subjects: list[V2SubjectReference] = Field(
        default_factory=list, max_length=16
    )
    previous_question_target: V2SubjectReference | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def entity_references(self) -> dict[str, V2SubjectReference]:
        """Return trusted references indexed by ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        if self.previous_question_target is not None:
            references.append(self.previous_question_target)
        return {item.reference_id: item for item in references}


class V2EntityBinding(BaseModel):
    """Initial or verified binding to a trusted entity."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V2EntityType
    resolution_method: V2ResolutionMethod
    resolution_status: V2ResolutionStatus
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class V2ParticipantRole(StrEnum):
    """Semantic roles required by the V2 architecture."""

    ACTION_AGENT = "action_agent"
    ACTION_RECIPIENT = "action_recipient"
    EXPERIENCER = "experiencer"
    ACTION_OBJECT = "action_object"
    SOURCE = "source"
    LOCATION = "location"
    INSTRUMENT = "instrument"
    GOAL = "goal"
    CAUSE = "cause"


class V2ParticipantBinding(BaseModel):
    """One event participant and its entity binding."""

    model_config = ConfigDict(extra="forbid")

    role: V2ParticipantRole
    entity: V2EntityBinding
    surface_text: str = Field(default="", max_length=160)


class V2TemporalObservation(BaseModel):
    """Temporal semantics attached to a verified item."""

    model_config = ConfigDict(extra="forbid")

    temporal_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=160)
    relation: V2TemporalRelation
    precision: V2TemporalPrecision
    status: V2ResolutionQuality
    confidence: float = Field(ge=0.0, le=1.0)


class V2MeasurementObservation(BaseModel):
    """Measurement semantics without forcing vague quantities to numbers."""

    model_config = ConfigDict(extra="forbid")

    measurement_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=160)
    value_text: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="", max_length=40)
    status: V2ResolutionQuality
    confidence: float = Field(ge=0.0, le=1.0)


class V2ScopeItem(BaseModel):
    """One item inherited from a shared assertion scope."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=240)
    analysis_text: str = Field(min_length=1, max_length=280)
    subject: V2EntityBinding
    participants: list[V2ParticipantBinding] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class V2SegmentBase(BaseModel):
    """Fields shared by atomic and shared-scope segments."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=480)
    analysis_text: str = Field(min_length=1, max_length=560)
    discourse_role: V2DiscourseRole
    requires_evidence_analysis: bool = True
    subject: V2EntityBinding
    participants: list[V2ParticipantBinding] = Field(default_factory=list, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)


class V2AtomicClaimSegment(V2SegmentBase):
    """A segment expected to produce one primary evidence result."""

    kind: Literal["atomic_claim"] = "atomic_claim"
    expected_evidence_count: Literal[1] = 1
    item_id: str = Field(default="item-1", min_length=1, max_length=96)
    initial_assertion: V2AssertionState = V2AssertionState.UNKNOWN


class V2SharedAssertionScopeSegment(V2SegmentBase):
    """One assertion applied to multiple independently verifiable items."""

    kind: Literal["shared_assertion_scope"] = "shared_assertion_scope"
    scope_assertion: V2AssertionState
    items: list[V2ScopeItem] = Field(min_length=1, max_length=32)
    expected_evidence_count: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def validate_expected_count(self) -> V2SharedAssertionScopeSegment:
        if self.expected_evidence_count != len(self.items):
            raise ValueError("expected_evidence_count_must_equal_scope_item_count")
        return self


V2Segment = Annotated[
    V2AtomicClaimSegment | V2SharedAssertionScopeSegment,
    Field(discriminator="kind"),
]


class V2PreprocessingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = Field(default="", max_length=160)


class V2InputContentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fact_candidate_count: int = Field(ge=0, le=64)
    has_user_question: bool = False
    has_control_intent: bool = False
    has_uncertainty: bool = False
    has_historical_statement: bool = False
    has_hypothetical_statement: bool = False


class V2Stage1Output(BaseModel):
    """Structured output of Stage 1 input organization."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"] = "v2"
    profile: V2InputContentProfile
    intent: V2PreprocessingIntent
    segments: list[V2Segment] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_profile_count(self) -> V2Stage1Output:
        expected = sum(
            segment.expected_evidence_count
            for segment in self.segments
            if segment.requires_evidence_analysis
        )
        if self.profile.expected_fact_candidate_count != expected:
            raise ValueError("profile_expected_fact_count_mismatch")
        return self


class V2CanonicalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=96)
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["registry_alias", "embedding"] = "registry_alias"


class V2VerifiedEvidence(BaseModel):
    """One Stage 2 result for an atomic item or shared-scope item."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=96)
    segment_id: str = Field(min_length=1, max_length=96)
    item_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=320)
    assertion: V2AssertionState
    mapping_status: V2CanonicalMappingStatus
    canonical_id: str | None = Field(default=None, max_length=96)
    subject: V2EntityBinding
    participants: list[V2ParticipantBinding] = Field(default_factory=list, max_length=8)
    candidates: list[V2CanonicalCandidate] = Field(default_factory=list, max_length=12)
    temporal_observations: list[V2TemporalObservation] = Field(
        default_factory=list, max_length=8
    )
    measurement_observations: list[V2MeasurementObservation] = Field(
        default_factory=list, max_length=8
    )
    review_required: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)

    @model_validator(mode="after")
    def validate_mapping_id(self) -> V2VerifiedEvidence:
        if self.mapping_status == V2CanonicalMappingStatus.CONFIRMED:
            if not self.canonical_id:
                raise ValueError("confirmed_mapping_requires_canonical_id")
        elif self.canonical_id is not None:
            raise ValueError("only_confirmed_mapping_may_set_canonical_id")
        if (
            self.mapping_status
            in {
                V2CanonicalMappingStatus.NOT_FOUND,
                V2CanonicalMappingStatus.UNMAPPED_MENTION,
                V2CanonicalMappingStatus.AMBIGUOUS,
                V2CanonicalMappingStatus.NEW_CONCEPT_REQUEST,
            }
            and not self.review_required
        ):
            raise ValueError("unresolved_mapping_requires_review")
        return self


class V2Stage2Output(BaseModel):
    """Structured verifier output constrained by Stage 1 items."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"] = "v2"
    observations: list[V2VerifiedEvidence] = Field(default_factory=list, max_length=256)


class V2QualityGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: V2QualityGateStatus
    severity: Literal["blocking", "critical", "major", "minor", "observability_only"]
    reason_code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: V2QualityGateAction
    review_required: bool = False


class V2InputAnalysisResult(BaseModel):
    """Report-only V2 result retained for trace and evaluation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2"] = "v2"
    strategy: Literal["v2_two_stage_shadow"] = "v2_two_stage_shadow"
    turn_context: V2TurnContext
    stage1: V2Stage1Output
    stage2: V2Stage2Output
    gates: list[V2QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)

    def failed_blocking_gates(self) -> list[V2QualityGateResult]:
        """Return gates that would block any downstream consumption."""

        return [
            gate
            for gate in self.gates
            if gate.status == V2QualityGateStatus.FAILED and gate.severity == "blocking"
        ]
