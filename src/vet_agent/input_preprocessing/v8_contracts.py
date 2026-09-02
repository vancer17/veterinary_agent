"""V8 span-anchored macro-semantic experiment contracts.

V8 reverses the V7 runtime tendency to split context-starved micro-tasks.
The model reads the complete turn and emits only structural decisions and
``span_id`` references.  It never emits a free-form quote; deterministic code
resolves every accepted reference back to the source text.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class V8ExperimentId(StrEnum):
    """Experiments in the staged V8 attribution suite."""

    SPAN_GOLDEN = "SPAN-GOLDEN"
    SPAN_POOL_COVERAGE = "SPAN-POOL-COVERAGE"
    NEG_V8 = "NEG-V8"
    STRUCT_BASE = "STRUCT-BASE"
    STRUCT_INSTRUCTOR = "STRUCT-INSTRUCTOR"
    STRUCT_BAML = "STRUCT-BAML"
    MACRO_INTENT = "MACRO-INTENT"
    MACRO_CLAIM = "MACRO-CLAIM"
    MACRO_BINDING = "MACRO-BINDING"
    PARTICIPANT_RESOLVE = "PARTICIPANT-RESOLVE"
    RELATION_LIVE = "RELATION-LIVE"
    CAN_LIVE = "CAN-LIVE"
    WINNER_INTEGRATION = "WINNER-INTEGRATION"
    DSPY_OPT = "DSPY-OPT"
    REP_V8 = "REP-V8"
    HELD_OUT_V8 = "HELD-OUT-V8"
    ASYNC_V8 = "ASYNC-V8"


class V8SpanLabel(StrEnum):
    """Generic linguistic labels used by a span extractor."""

    CANDIDATE_SPAN = "candidate_span"
    TARGET_MENTION = "target_mention"
    STATE_MENTION = "state_mention"
    ACTION_EVENT = "action_event"
    AGENT_MENTION = "agent_mention"
    RECIPIENT_MENTION = "recipient_mention"
    SUBJECT_MENTION = "subject_mention"
    OBJECT_MENTION = "object_mention"
    TEMPORAL_EXPRESSION = "temporal_expression"
    MEASUREMENT_EXPRESSION = "measurement_expression"
    RELATION_EXPRESSION = "relation_expression"
    CONTROL_INTENT_EXPRESSION = "control_intent_expression"
    QUESTION_EXPRESSION = "question_expression"


class V8DiscourseActType(StrEnum):
    """Turn-level acts. Several acts can be true at once."""

    ANSWER_NOW = "answer_now"
    WANTS_TRIAGE = "wants_triage"
    CORRECTION = "correction"
    CLARIFICATION_REQUEST = "clarification_request"
    FACT_STATEMENT = "fact_statement"
    QUESTION = "question"
    REPORT_CONTEXT = "report_context"


class V8UserStatementType(StrEnum):
    REPORTS = "reports"
    DENIES = "denies"
    REPORTS_NORMAL = "reports_normal"
    REPORTS_ABNORMAL = "reports_abnormal"
    UNCERTAIN = "uncertain"
    HISTORICAL = "historical"
    HYPOTHETICAL = "hypothetical"
    ASKS = "asks"
    CORRECTS = "corrects"


class V8CoarseType(StrEnum):
    SYMPTOM = "symptom"
    STATE = "state"
    ACTION = "action"
    EXPOSURE = "exposure"
    FOOD = "food"
    MEDICATION = "medication"
    TIME = "time"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class V8AttributionCode(StrEnum):
    SPAN_RECALL_MISS = "span_recall_miss"
    SPAN_BOUNDARY_ERROR = "span_boundary_error"
    SPAN_LABEL_ERROR = "span_label_error"
    INVALID_SPAN_REFERENCE = "invalid_span_reference"
    INVALID_SPAN_BINDING = "invalid_span_binding"
    TARGET_BINDING_ERROR = "target_binding_error"
    RELATION_BINDING_ERROR = "relation_binding_error"
    TEMPORAL_BINDING_ERROR = "temporal_binding_error"
    MEASUREMENT_BINDING_ERROR = "measurement_binding_error"
    PARTICIPANT_BINDING_ERROR = "participant_binding_error"
    INTENT_EVIDENCE_MISSING = "intent_evidence_missing"
    SCHEMA_ADAPTER_FAILURE = "schema_adapter_failure"
    MACRO_SEMANTIC_ERROR = "macro_semantic_error"
    RELATION_CLASSIFIER_ERROR = "relation_classifier_error"
    CANONICAL_RECALL_ERROR = "canonical_recall_error"
    GATE_BLOCKED_EXPECTED = "gate_blocked_expected"
    UPSTREAM_BLOCKED = "upstream_blocked"
    MIDDLEWARE_NOT_CONFIGURED = "middleware_not_configured"
    RUNNER_ERROR = "runner_error"


class V8SpanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str = Field(min_length=1, max_length=96)
    source_id: str = Field(min_length=1, max_length=96)
    source_block_id: str = Field(min_length=1, max_length=96)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=480)
    label: V8SpanLabel = V8SpanLabel.CANDIDATE_SPAN
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    extractor_version: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_offsets(self) -> V8SpanCandidate:
        if self.end <= self.start:
            raise ValueError("span_end_must_be_after_start")
        return self


class V8EntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_id: str = Field(min_length=1, max_length=96)
    entity_type: Literal[
        "current_pet",
        "other_pet",
        "user",
        "caregiver",
        "medical_actor",
        "food",
        "medication",
        "environment",
    ]
    display_name: str = Field(default="", max_length=120)
    mention_aliases: list[str] = Field(default_factory=list, max_length=24)


class V8MacroDiscourseActRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    act_type: V8DiscourseActType
    evidence_span_ids: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V8MacroClaimRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    claim_id: str = Field(min_length=1, max_length=96)
    statement_type: V8UserStatementType
    coarse_type: V8CoarseType
    support_span_ids: list[str] = Field(min_length=1, max_length=8)
    target_span_ids: list[str] = Field(min_length=1, max_length=8)
    relation_span_ids: list[str] = Field(default_factory=list, max_length=8)
    subject_span_ids: list[str] = Field(default_factory=list, max_length=8)
    action_agent_span_ids: list[str] = Field(default_factory=list, max_length=8)
    action_recipient_span_ids: list[str] = Field(default_factory=list, max_length=8)
    experiencer_span_ids: list[str] = Field(default_factory=list, max_length=8)
    object_span_ids: list[str] = Field(default_factory=list, max_length=8)
    temporal_span_ids: list[str] = Field(default_factory=list, max_length=8)
    measurement_span_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V8MacroSemanticRawOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v8-macro-raw"] = "v8-macro-raw"
    acts: list[V8MacroDiscourseActRaw] = Field(default_factory=list, max_length=64)
    claims: list[V8MacroClaimRaw] = Field(default_factory=list, max_length=128)


class V8ResolvedSpanBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_ids: list[str] = Field(min_length=1, max_length=8)
    source_id: str = Field(min_length=1, max_length=96)
    source_block_id: str = Field(min_length=1, max_length=96)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=480)
    status: Literal["resolved", "unresolved", "invalid"]


class V8ResolvedEntityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_ids: list[str] = Field(default_factory=list, max_length=8)
    mention_quote: str = Field(default="", max_length=480)
    selected_reference_id: str | None = Field(default=None, max_length=96)
    entity_type: str | None = None
    resolution_status: Literal[
        "resolved", "ambiguous", "unresolved", "missing", "failed"
    ]
    subject_candidates: list[str] = Field(default_factory=list, max_length=8)


class V8GovernedUserClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    claim_id: str = Field(min_length=1, max_length=96)
    statement_type: V8UserStatementType
    coarse_type: V8CoarseType
    support: V8ResolvedSpanBinding
    target: V8ResolvedSpanBinding
    relation: V8ResolvedSpanBinding | None = None
    subject: V8ResolvedEntityBinding | None = None
    action_agent: V8ResolvedEntityBinding | None = None
    action_recipient: V8ResolvedEntityBinding | None = None
    experiencer: V8ResolvedEntityBinding | None = None
    object_mention: V8ResolvedSpanBinding | None = None
    temporal: V8ResolvedSpanBinding | None = None
    measurement: V8ResolvedSpanBinding | None = None
    projection_ready: bool = False
    review_required: bool = False


class V8QualityGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate_id: str = Field(min_length=1, max_length=96)
    status: Literal["passed", "failed", "warning", "skipped", "not_applicable"]
    severity: Literal["blocking", "critical", "major", "minor"]
    reason_code: str = Field(min_length=1, max_length=120)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
