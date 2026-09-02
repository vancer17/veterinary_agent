"""Stable contracts for the tenth input-preprocessing repair round.

V10 is a diagnostic repair round.  Its fixture and report contracts make the
distinction between an exact offset, a field role, and a label explicit; they
do not alter V8 phase admission or introduce a production pipeline.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .v8_contracts import (
    V8CoarseType,
    V8DiscourseActType,
    V8SpanCandidate,
    V8SpanLabel,
    V8UserStatementType,
)

V10_REPORT_VERSION = "v10-experiment-report-1"
V10_FIXTURE_VERSION = "v10-explicit-offset-1"
V10_MACRO_SCHEMA_VERSION = "v10-macro-raw-1"
V10_MACRO_PROMPT_VERSION = "v10-macro-repair-dev-20260830-4"
V10_RELATION_PROMPT_VERSION = "v10-relation-fixed-contract-dev-20260830-2"
V10_GOLD_POOL_VERSION = "v10-explicit-offset-gold-20260830-1"
V10_BOUNDARY_CALIBRATION_VERSION = "v10-boundary-calibration-dev-20260830-1"


class V10ExperimentId(StrEnum):
    FIXTURE_OFFSET = "FIXTURE-OFFSET"
    FIELD_ROLE_SPLIT = "FIELD-ROLE-SPLIT"
    RELATION_SPAN_COMPLETE = "RELATION-SPAN-COMPLETE"
    INTERFACE_AUDIT = "INTERFACE-AUDIT"

    SPAN_RAW = "SPAN-RAW"
    SPAN_CALIBRATE = "SPAN-CALIBRATE"
    SPAN_BUDGET = "SPAN-BUDGET"
    SPAN_MODEL = "SPAN-MODEL"
    SPANMARKER_CHINESE = "SPANMARKER-CHINESE"

    MACRO_ACT = "MACRO-ACT"
    MACRO_SKELETON = "MACRO-SKELETON"
    MACRO_BINDING = "MACRO-BINDING"
    MACRO_FULL = "MACRO-FULL"
    MACRO_CANDIDATE_LOAD = "MACRO-CANDIDATE-LOAD"

    REL_SINGLE = "REL-SINGLE"
    REL_BATCH_FIXED = "REL-BATCH-FIXED"
    REL_VERSIONED_FEWSHOT = "REL-VERSIONED-FEWSHOT"
    REL_MISSING = "REL-MISSING"

    CAN_REGRESSION = "CAN-REGRESSION"
    PARTICIPANT_REGRESSION = "PARTICIPANT-REGRESSION"
    TEMPORAL_MEASUREMENT_REGRESSION = "TEMPORAL-MEASUREMENT-REGRESSION"

    EARLY_MINIMAL = "EARLY-MINIMAL"
    EARLY_VOI = "EARLY-VOI"
    EARLY_BUDGET = "EARLY-BUDGET"
    EARLY_ROUTER = "EARLY-ROUTER"
    EARLY_FAILURE = "EARLY-FAILURE"

    REP_COLD = "REP-COLD"
    HELD_OUT_V10 = "HELD-OUT-V10"
    NEG_V10 = "NEG-V10"
    ASYNC_V10 = "ASYNC-V10"


class V10FieldRole(StrEnum):
    EVIDENCE = "evidence_quote"
    SUPPORT = "support_quote"
    TARGET = "target_quote"
    RELATION = "relation_quote"
    SUBJECT = "subject_quote"
    ACTION_AGENT = "action_agent_quote"
    ACTION_RECIPIENT = "action_recipient_quote"
    EXPERIENCER = "experiencer_quote"
    OBJECT = "object_quote"
    TEMPORAL = "temporal_quote"
    MEASUREMENT = "measurement_quote"


class V10Lane(StrEnum):
    DETERMINISTIC = "deterministic"
    GOLDEN = "golden"
    LIVE = "live"
    REGRESSION = "regression"
    EARLY_EXIT = "early-exit"


class V10ExpectedField(BaseModel):
    """An owner-scoped expected field whose offset is authoritative."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    claim_owner: str = Field(min_length=1, max_length=128)
    field_role: V10FieldRole
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=480)
    expected_label_candidates: list[V8SpanLabel] = Field(min_length=1, max_length=8)
    source_block_id: str = Field(default="block-001", min_length=1, max_length=96)
    coarse_type: str = ""
    act_type: str = ""
    status: Literal["active", "fixture_incomplete"] = "active"
    incomplete_reason: str = ""

    @model_validator(mode="after")
    def validate_boundary(self) -> V10ExpectedField:
        if self.end <= self.start:
            raise ValueError("v10_field_end_must_be_after_start")
        return self


def field_from_raw(raw: dict[str, Any], unit_id: str) -> V10ExpectedField:
    """Build a field while retaining only explicit fixture metadata."""

    return V10ExpectedField.model_validate(
        {
            "unit_id": unit_id,
            "claim_owner": str(raw["claim_owner"]),
            "field_role": str(raw["field_role"]),
            "start": int(raw["start"]),  # type: ignore[arg-type]
            "end": int(raw["end"]),  # type: ignore[arg-type]
            "text": str(raw["text"]),
            "expected_label_candidates": raw.get("expected_label_candidates", []),
            "source_block_id": str(raw.get("source_block_id", "block-001")),
            "coarse_type": str(raw.get("coarse_type", "")),
            "act_type": str(raw.get("act_type", "")),
            "status": str(raw.get("status", "active")),
            "incomplete_reason": str(raw.get("incomplete_reason", "")),
        }
    )


class V10MacroActRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    act_type: V8DiscourseActType
    evidence_span_id: str = Field(min_length=1, max_length=128)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V10MacroClaimRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    claim_id: str = Field(min_length=1, max_length=128)
    statement_type: V8UserStatementType
    coarse_type: V8CoarseType
    support_anchor_span_ids: list[str] = Field(min_length=1, max_length=8)
    target_span_id: str = Field(min_length=1, max_length=128)
    relation_span_id: str | None = Field(default=None, max_length=128)
    subject_span_id: str | None = Field(default=None, max_length=128)
    action_agent_span_id: str | None = Field(default=None, max_length=128)
    action_recipient_span_id: str | None = Field(default=None, max_length=128)
    experiencer_span_id: str | None = Field(default=None, max_length=128)
    object_span_id: str | None = Field(default=None, max_length=128)
    temporal_span_id: str | None = Field(default=None, max_length=128)
    measurement_span_id: str | None = Field(default=None, max_length=128)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V10MacroSemanticRawOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v10-macro-raw-1"] = "v10-macro-raw-1"
    no_act_reason: str = Field(default="", max_length=160)
    acts: list[V10MacroActRaw] = Field(default_factory=list, max_length=64)
    claims: list[V10MacroClaimRaw] = Field(default_factory=list, max_length=128)


class V10RelationRecordRaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=160)
    relation: Literal["absolute_status", "no_change", "change", "unclear"]


class V10RelationRawOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v10-relation-raw-1"] = "v10-relation-raw-1"
    records: list[V10RelationRecordRaw] = Field(default_factory=list, max_length=64)


class V10CalibratedSpan(BaseModel):
    """A calibrated candidate plus non-production role diagnostics."""

    model_config = ConfigDict(extra="forbid")

    span: V8SpanCandidate
    eligible_roles: frozenset[V10FieldRole]

    @model_validator(mode="after")
    def validate_roles(self) -> V10CalibratedSpan:
        if not self.eligible_roles:
            raise ValueError("v10_candidate_requires_role_eligibility")
        return self
