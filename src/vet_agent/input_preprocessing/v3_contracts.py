"""V3 contracts for the third-round input-preprocessing architecture experiments.

V3 separates raw model output from deterministically assembled facts.  Model
calls never own derived counts, final canonical IDs, or inherited participants;
every unresolved value remains explicit and auditable.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V3AssertionState(StrEnum):
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


class V3DiscourseRole(StrEnum):
    """Discourse role assigned by the scoped Stage 1 organizer."""

    FACT_STATEMENT = "fact_statement"
    USER_QUESTION = "user_question"
    HISTORICAL_STATEMENT = "historical_statement"
    HYPOTHETICAL_STATEMENT = "hypothetical_statement"
    UNCERTAIN_STATEMENT = "uncertain_statement"
    OTHER = "other"


class V3EntityType(StrEnum):
    """Trusted entity types allowed by the V3 TurnContext."""

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


class V3ResolutionMethod(StrEnum):
    """Explicit provenance used to resolve an entity mention."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class V3ResolutionStatus(StrEnum):
    """Resolution state for a subject or participant."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class V3ParticipantRole(StrEnum):
    """Generic semantic-role vocabulary; it is not a medical taxonomy."""

    ACTION_AGENT = "action_agent"
    ACTION_RECIPIENT = "action_recipient"
    EXPERIENCER = "experiencer"
    ACTION_OBJECT = "action_object"
    SOURCE = "source"
    LOCATION = "location"
    INSTRUMENT = "instrument"
    GOAL = "goal"
    CAUSE = "cause"


class V3SemanticClass(StrEnum):
    """Small structural class used for candidate compatibility."""

    STATE = "state"
    EVENT = "event"
    ACTION = "action"
    QUESTION = "question"
    CONTROL_INTENT = "control_intent"
    MEASUREMENT = "measurement"


class V3CanonicalMappingStatus(StrEnum):
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


class V3AssertionVerification(StrEnum):
    """Whether Stage 2 could verify the Stage 1 initial assertion."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNRESOLVED = "unresolved"


class V3ParticipantVerification(StrEnum):
    """Whether Stage 2 could verify inherited participants."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class V3ObservationVerification(StrEnum):
    """Presence or resolution state of temporal / measurement evidence."""

    CONFIRMED_PRESENT = "confirmed_present"
    CONFIRMED_ABSENT = "confirmed_absent"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class V3QualityGateStatus(StrEnum):
    """Outcome of one V3 architecture gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class V3QualityGateAction(StrEnum):
    """Finite action emitted by a V3 gate."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


class V3SubjectReference(BaseModel):
    """A trusted entity supplied by the service boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    entity_type: V3EntityType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class V3TurnContext(BaseModel):
    """Trusted turn boundary for all V3 stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3"] = "v3"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: V3SubjectReference
    other_subjects: list[V3SubjectReference] = Field(default_factory=list, max_length=16)
    previous_question_target: V3SubjectReference | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def entity_references(self) -> dict[str, V3SubjectReference]:
        """Return trusted references indexed by server-owned reference ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        if self.previous_question_target is not None:
            references.append(self.previous_question_target)
        return {item.reference_id: item for item in references}


class V3EntityBinding(BaseModel):
    """One explicit subject or participant binding."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str | None = Field(default=None, max_length=64)
    entity_type: V3EntityType = V3EntityType.UNKNOWN
    resolution_method: V3ResolutionMethod = V3ResolutionMethod.SUBJECT_MISSING
    resolution_status: V3ResolutionStatus = V3ResolutionStatus.MISSING
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V3ParticipantBinding(BaseModel):
    """A participant attached to an item before Stage 2 verification."""

    model_config = ConfigDict(extra="forbid")

    role: V3ParticipantRole
    entity: V3EntityBinding


class V3TurnIntentRaw(BaseModel):
    """Raw output of the independent Turn Intent Analyzer."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class V3RawScopeItem(BaseModel):
    """Raw item declared inside a shared assertion scope."""

    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=240)
    analysis_text: str = Field(min_length=1, max_length=280)


class V3RawSegment(BaseModel):
    """Raw segment output; IDs and counts are assigned by code."""

    model_config = ConfigDict(extra="forbid")

    source_text: str = Field(min_length=1, max_length=480)
    analysis_text: str = Field(min_length=1, max_length=560)
    discourse_role: V3DiscourseRole
    requires_evidence_analysis: bool = True
    kind: Literal["atomic_claim", "shared_assertion_scope"]
    initial_assertion: V3AssertionState | None = None
    scope_assertion: V3AssertionState | None = None
    items: list[V3RawScopeItem] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_segment_kind(self) -> V3RawSegment:
        if self.kind == "atomic_claim":
            if self.initial_assertion is None:
                raise ValueError("atomic_claim_requires_initial_assertion")
            if self.items:
                raise ValueError("atomic_claim_must_not_declare_scope_items")
        elif self.scope_assertion is None or not self.items:
            raise ValueError("shared_scope_requires_assertion_and_items")
        return self


class V3ScopeSegmentationRawOutput(BaseModel):
    """Raw output of the scoped segmentation analyzer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3-raw"] = "v3-raw"
    segments: list[V3RawSegment] = Field(default_factory=list, max_length=64)


class V3RawParticipantBinding(BaseModel):
    """Raw participant reference before trusted-entity resolution."""

    model_config = ConfigDict(extra="forbid")

    role: V3ParticipantRole
    resolution_status: V3ResolutionStatus
    resolution_method: V3ResolutionMethod
    reference_id: str | None = Field(default=None, max_length=64)
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V3RawItemBinding(BaseModel):
    """Raw subject and participants for exactly one Stage 1 item."""

    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=160)
    subject: V3RawParticipantBinding
    participants: list[V3RawParticipantBinding] = Field(
        default_factory=list, max_length=8
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V3ParticipantBindingRawOutput(BaseModel):
    """Raw output of the participant-binding analyzer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3-raw"] = "v3-raw"
    bindings: list[V3RawItemBinding] = Field(default_factory=list, max_length=64)


class V3PreprocessingIntent(BaseModel):
    """Assembled control intent separated from fact segments."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class V3InputContentProfile(BaseModel):
    """Derived profile; model calls never repeat these counts."""

    model_config = ConfigDict(extra="forbid")

    expected_fact_candidate_count: int = Field(ge=0, le=256)
    has_fact_statement: bool = False
    has_user_question: bool = False
    has_control_intent: bool = False
    has_uncertainty: bool = False
    has_historical_statement: bool = False
    has_hypothetical_statement: bool = False


class V3ScopeItem(BaseModel):
    """One assembled item inside a shared assertion scope."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=240)
    analysis_text: str = Field(min_length=1, max_length=280)
    subject: V3EntityBinding
    participants: list[V3ParticipantBinding] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V3SegmentBase(BaseModel):
    """Fields shared by assembled V3 segments."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=480)
    analysis_text: str = Field(min_length=1, max_length=560)
    discourse_role: V3DiscourseRole
    requires_evidence_analysis: bool = True
    subject: V3EntityBinding
    participants: list[V3ParticipantBinding] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V3AtomicClaimSegment(V3SegmentBase):
    """An independently verifiable atomic claim."""

    kind: Literal["atomic_claim"] = "atomic_claim"
    expected_evidence_count: Literal[1] = 1
    item_id: str = Field(min_length=1, max_length=96)
    initial_assertion: V3AssertionState


class V3SharedAssertionScopeSegment(V3SegmentBase):
    """One assertion applied to multiple independently verifiable items."""

    kind: Literal["shared_assertion_scope"] = "shared_assertion_scope"
    scope_assertion: V3AssertionState
    items: list[V3ScopeItem] = Field(min_length=1, max_length=32)
    expected_evidence_count: int = Field(ge=1, le=32)


V3Segment = Annotated[
    V3AtomicClaimSegment | V3SharedAssertionScopeSegment,
    Field(discriminator="kind"),
]


class V3Stage1Output(BaseModel):
    """Deterministically assembled Stage 1 output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3"] = "v3"
    intent: V3PreprocessingIntent
    profile: V3InputContentProfile
    segments: list[V3Segment] = Field(default_factory=list, max_length=64)


class V3CanonicalCandidate(BaseModel):
    """One auditable candidate; it is not a fact by itself."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=96)
    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_type: str = Field(min_length=1, max_length=64)
    semantic_class: V3SemanticClass
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["registry_alias", "embedding", "ideal_control"]


class V3CandidateSet(BaseModel):
    """Candidates recalled for exactly one Stage 1 item."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=96)
    item_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=320)
    candidates: list[V3CanonicalCandidate] = Field(default_factory=list, max_length=16)
    recall_status: Literal["recalled", "no_candidate", "not_applicable"] = (
        "no_candidate"
    )
    recall_version: str = Field(default="", max_length=120)


class V3ItemVerificationRaw(BaseModel):
    """Raw item-keyed Stage 2 verification output."""

    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=160)
    assertion_verification: V3AssertionVerification
    mapping_status: V3CanonicalMappingStatus
    selected_candidate_id: str | None = Field(default=None, max_length=96)
    participant_verification: V3ParticipantVerification
    temporal_verification: V3ObservationVerification = (
        V3ObservationVerification.NOT_APPLICABLE
    )
    measurement_verification: V3ObservationVerification = (
        V3ObservationVerification.NOT_APPLICABLE
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class V3VerifiedEvidence(BaseModel):
    """One final report-only evidence item assembled by deterministic code."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=96)
    segment_id: str = Field(min_length=1, max_length=96)
    item_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1, max_length=320)
    initial_assertion: V3AssertionState
    assertion_verification: V3AssertionVerification
    mapping_status: V3CanonicalMappingStatus
    selected_candidate_id: str | None = Field(default=None, max_length=96)
    canonical_id: str | None = Field(default=None, max_length=96)
    candidates: list[V3CanonicalCandidate] = Field(default_factory=list, max_length=16)
    subject: V3EntityBinding
    participants: list[V3ParticipantBinding] = Field(default_factory=list, max_length=8)
    participant_verification: V3ParticipantVerification
    temporal_verification: V3ObservationVerification
    measurement_verification: V3ObservationVerification
    review_required: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class V3Stage2Output(BaseModel):
    """Assembled item verifier results."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3"] = "v3"
    observations: list[V3VerifiedEvidence] = Field(default_factory=list, max_length=256)


class V3QualityGateResult(BaseModel):
    """Serializable result of one synchronous V3 gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: V3QualityGateStatus
    severity: Literal[
        "blocking", "critical", "major", "minor", "observability_only"
    ]
    reason_code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: V3QualityGateAction
    review_required: bool = False


class V3InputAnalysisResult(BaseModel):
    """Report-only V3 result retained for trace and evaluation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v3"] = "v3"
    strategy: Literal["v3_split_stage_item_verifier_shadow"] = (
        "v3_split_stage_item_verifier_shadow"
    )
    turn_context: V3TurnContext
    stage1: V3Stage1Output
    candidate_sets: list[V3CandidateSet] = Field(default_factory=list, max_length=256)
    stage2: V3Stage2Output
    gates: list[V3QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    stage_attempts: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)
    candidate_recall_version: str = Field(default="", max_length=120)

    def failed_blocking_gates(self) -> list[V3QualityGateResult]:
        """Return gates that block any hypothetical downstream consumption."""

        return [
            gate
            for gate in self.gates
            if gate.status == V3QualityGateStatus.FAILED
            and gate.severity == "blocking"
        ]
