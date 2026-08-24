"""Stable contracts for the input-preprocessing shadow pipeline.

This module only defines structured input, evidence, subject, temporal, and
quality contracts.  It deliberately contains no medical rules and never
interprets raw text in Python.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssertionState(StrEnum):
    """Assertion semantics kept separately from canonical concepts."""

    PRESENT = "present"
    DENIED = "denied"
    NORMAL = "normal"
    ABNORMAL = "abnormal"
    UNCERTAIN = "uncertain"
    POSSIBLE = "possible"
    HYPOTHETICAL = "hypothetical"
    HISTORICAL = "historical"
    RESOLVED = "resolved"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class DiscourseRole(StrEnum):
    """Discourse roles produced by the structured preprocessing model."""

    FACT_STATEMENT = "fact_statement"
    USER_QUESTION = "user_question"
    CONTROL_INTENT = "control_intent"
    HISTORICAL_STATEMENT = "historical_statement"
    HYPOTHETICAL_STATEMENT = "hypothetical_statement"
    UNCERTAIN_STATEMENT = "uncertain_statement"
    OTHER = "other"


class SubjectResolutionMethod(StrEnum):
    """Explicit provenance used to bind a mention to a trusted subject."""

    TRUSTED_CURRENT_PET = "trusted_current_pet"
    PREVIOUS_QUESTION_TARGET = "previous_question_target"
    DISCOURSE_CONTINUITY = "discourse_continuity"
    EXPLICIT_COREFERENCE = "explicit_coreference"
    SUBJECT_AMBIGUOUS = "subject_ambiguous"
    SUBJECT_MISSING = "subject_missing"


class CanonicalStatus(StrEnum):
    """Status of a canonical-concept mapping."""

    CONFIRMED = "confirmed"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    TYPE_MISMATCH = "type_mismatch"
    SUBJECT_MISMATCH = "subject_mismatch"
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"


class CanonicalTerm(BaseModel):
    """One controlled canonical concept and its non-rule projection metadata."""

    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_type: Literal[
        "symptom",
        "status",
        "intake_output",
        "behavior",
        "intervention",
        "exposure",
        "measurement",
        "question_intent",
    ]
    allowed_subject_types: list[str] = Field(min_length=1, max_length=16)
    aliases: list[str] = Field(min_length=1, max_length=48)
    consultation_projection: dict[str, Any] = Field(default_factory=dict)
    clinical_safety_projection: Literal["none", "symptom", "exposure", "status"] = (
        "none"
    )


class TemporalRelation(StrEnum):
    """Relation between a temporal expression and an evidence event."""

    STARTED_AT = "started_at"
    DURATION = "duration"
    FREQUENCY = "frequency"
    ENDED_AT = "ended_at"
    UNSTRUCTURED = "unstructured"


class TemporalPrecision(StrEnum):
    """Precision retained after temporal normalization."""

    EXACT = "exact"
    DAY = "day"
    APPROXIMATE_DURATION = "approximate_duration"
    FREQUENCY = "frequency"
    UNRESOLVED = "unresolved"


class ResolutionStatus(StrEnum):
    """Whether a normalized value is trusted or explicitly unresolved."""

    CONFIRMED = "confirmed"
    IMPRECISE = "imprecise"
    UNRESOLVED = "unresolved"


class QualityGateStatus(StrEnum):
    """Status of an input-preprocessing quality gate."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_REVIEW = "needs_review"


class QualityGateAction(StrEnum):
    """Finite action emitted by a gate result."""

    PASS = "pass"
    PASS_WITH_METADATA = "pass_with_metadata"
    RETRY_SAME_CONTRACT = "retry_same_contract"
    REQUIRE_CLARIFICATION = "require_clarification"
    ROUTE_TO_REVIEW = "route_to_review"
    FAIL_TURN = "fail_turn"


SubjectType = Literal[
    "current_pet",
    "other_pet",
    "user",
    "caregiver",
    "food",
    "environment",
    "medical_actor",
    "sample",
    "unknown",
]


class SubjectReference(BaseModel):
    """A trusted subject reference assembled by the service boundary."""

    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=64)
    subject_type: SubjectType
    display_name: str = Field(default="", max_length=80)
    trusted: bool = True


class TurnContext(BaseModel):
    """Trusted turn boundary used by every preprocessing stage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    request_id: str = Field(min_length=1, max_length=80)
    trace_id: str = Field(min_length=1, max_length=80)
    user_id: str = Field(min_length=1, max_length=80)
    pet_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    task_key: str = Field(default="__default__", max_length=120)
    reference_time: datetime
    current_pet_subject: SubjectReference
    other_subjects: list[SubjectReference] = Field(default_factory=list)
    previous_question_target: SubjectReference | None = None
    verified_pet_profile: dict[str, Any] = Field(default_factory=dict)
    input_channel: Literal[
        "api_user_message", "uploaded_document", "server_context"
    ] = "api_user_message"

    def subject_references(self) -> dict[str, SubjectReference]:
        """Return trusted subject references indexed by reference ID."""

        references = [self.current_pet_subject, *self.other_subjects]
        if self.previous_question_target is not None:
            references.append(self.previous_question_target)
        return {item.reference_id: item for item in references}


class InputContentProfile(BaseModel):
    """A structured, non-keyword profile used by coverage gates."""

    model_config = ConfigDict(extra="forbid")

    expected_fact_candidate_count: int = Field(ge=0, le=64)
    has_user_question: bool = False
    has_control_intent: bool = False
    has_uncertainty: bool = False
    has_historical_statement: bool = False
    has_hypothetical_statement: bool = False


class SegmentModel(BaseModel):
    """A claim-level segment anchored to the original user input."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1, max_length=64)
    source_text: str = Field(min_length=1, max_length=320)
    analysis_text: str = Field(min_length=1, max_length=360)
    discourse_role: DiscourseRole
    requires_evidence_analysis: bool = True
    confidence: float = Field(ge=0.0, le=1.0)


class PreprocessingIntent(BaseModel):
    """User-control intents separated from clinical facts."""

    model_config = ConfigDict(extra="forbid")

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = Field(default="", max_length=120)


class SegmentationOutput(BaseModel):
    """Structured output of segmentation and discourse-role analysis."""

    model_config = ConfigDict(extra="forbid")

    profile: InputContentProfile
    intent: PreprocessingIntent
    segments: list[SegmentModel] = Field(default_factory=list, max_length=64)


class CanonicalCandidate(BaseModel):
    """A recall candidate; it is not a fact by itself."""

    model_config = ConfigDict(extra="forbid")

    canonical_id: str = Field(min_length=1, max_length=96)
    surface_form: str = Field(min_length=1, max_length=160)
    score: float = Field(ge=0.0, le=1.0)
    recall_source: Literal["embedding"] = "embedding"


class SubjectBinding(BaseModel):
    """Explicit binding from an observation to a trusted subject reference."""

    model_config = ConfigDict(extra="forbid")

    subject_reference: str = Field(min_length=1, max_length=64)
    subject_type: SubjectType
    resolution_method: SubjectResolutionMethod
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_ambiguous_binding(self) -> SubjectBinding:
        """Keep ambiguous and missing bindings observable rather than defaulting."""

        if (
            self.resolution_method == SubjectResolutionMethod.SUBJECT_AMBIGUOUS
            and self.subject_type != "unknown"
        ):
            raise ValueError("subject_ambiguous requires unknown subject_type")
        if (
            self.resolution_method == SubjectResolutionMethod.SUBJECT_MISSING
            and self.subject_type != "unknown"
        ):
            raise ValueError("subject_missing requires unknown subject_type")
        return self


class TemporalObservation(BaseModel):
    """A temporal expression with precision and unresolved state."""

    model_config = ConfigDict(extra="forbid")

    temporal_id: str = Field(min_length=1, max_length=96)
    segment_id: str = Field(min_length=1, max_length=64)
    source_text: str = Field(min_length=1, max_length=160)
    relation: TemporalRelation
    precision: TemporalPrecision
    status: ResolutionStatus
    confidence: float = Field(ge=0.0, le=1.0)


class MeasurementObservation(BaseModel):
    """A measurement expression with unit and precision retained."""

    model_config = ConfigDict(extra="forbid")

    measurement_id: str = Field(min_length=1, max_length=96)
    segment_id: str = Field(min_length=1, max_length=64)
    source_text: str = Field(min_length=1, max_length=160)
    value_text: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="", max_length=40)
    status: ResolutionStatus
    confidence: float = Field(ge=0.0, le=1.0)


class EvidenceObservation(BaseModel):
    """One evidence-backed assertion in the unified evidence graph."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=96)
    segment_id: str = Field(min_length=1, max_length=64)
    source_text: str = Field(min_length=1, max_length=320)
    canonical_id: str = Field(min_length=1, max_length=96)
    canonical_status: CanonicalStatus
    assertion: AssertionState
    subject: SubjectBinding
    candidates: list[CanonicalCandidate] = Field(default_factory=list, max_length=12)
    temporal_observations: list[TemporalObservation] = Field(
        default_factory=list, max_length=8
    )
    measurement_observations: list[MeasurementObservation] = Field(
        default_factory=list, max_length=8
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=360)


class EvidenceAnalysisOutput(BaseModel):
    """Structured output of assertion, subject, temporal, and mapping stages."""

    model_config = ConfigDict(extra="forbid")

    observations: list[EvidenceObservation] = Field(default_factory=list, max_length=64)


class QualityGateResult(BaseModel):
    """Serializable result of one synchronous or model-supported gate."""

    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: QualityGateStatus
    severity: Literal[
        "blocking",
        "critical",
        "major",
        "minor",
        "observability_only",
    ]
    reason_code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: QualityGateAction
    review_required: bool = False


class InputAnalysisResult(BaseModel):
    """Shadow result retained for trace, evaluation, and review only."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v1"] = "v1"
    strategy: Literal["two_stage_structured_shadow"] = "two_stage_structured_shadow"
    turn_context: TurnContext
    segmentation: SegmentationOutput
    evidence: EvidenceAnalysisOutput
    gates: list[QualityGateResult] = Field(default_factory=list)
    stage_latency_ms: dict[str, int] = Field(default_factory=dict)
    model_name: str = Field(default="", max_length=120)
    vocabulary_version: str = Field(default="", max_length=120)

    def failed_blocking_gates(self) -> list[QualityGateResult]:
        """Return gates that block domain projection."""

        return [
            gate
            for gate in self.gates
            if gate.status == QualityGateStatus.FAILED and gate.severity == "blocking"
        ]
