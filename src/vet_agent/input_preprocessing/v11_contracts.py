"""Contracts for the eleventh candidate-view and reranking repair round.

V11 freezes a V10 candidate snapshot, constructs role-specific claim-local
views, ranks existing candidates, and repairs macro skeletons.  Rerankers and
views never create spans or alter offsets; every report remains diagnostic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .v8_contracts import (
    V8CoarseType,
    V8DiscourseActType,
    V8SpanLabel,
    V8UserStatementType,
)
from .v10_contracts import V10CalibratedSpan

V11_REPORT_VERSION = "v11-experiment-report-1"
V11_SNAPSHOT_VERSION = "v11-candidate-snapshot-1"
V11_VIEW_VERSION = "v11-role-view-20260831-1"
V11_RERANKER_VERSION = "v11-bge-rerank-20260831-1"
V11_SEED_VERSION = "v11-structural-seed-20260831-1"
V11_MACRO_SCHEMA_VERSION = "v11-macro-seeded-1"
V11_MACRO_PROMPT_VERSION = "v11-macro-candidate-view-dev-20260831-2"
V11_STATEMENT_SCHEMA_VERSION = "v11-statement-verify-1"
V11_STATEMENT_PROMPT_VERSION = "v11-statement-verifier-dev-20260831-1"


class V11ExperimentId(StrEnum):
    SNAP_INTEGRITY = "SNAP-INTEGRITY"
    VIEW_COVERAGE = "VIEW-COVERAGE"
    RANK_BASE = "RANK-BASE"
    RANK_CROSS = "RANK-CROSS"
    RANK_BUDGET = "RANK-BUDGET"
    RANK_MACRO_LOAD = "RANK-MACRO-LOAD"
    MACRO_VIEW_PRUNE = "MACRO-VIEW-PRUNE"
    SEED_SHARED = "SEED-SHARED"
    SEED_ACTION = "SEED-ACTION"
    MACRO_FULL = "MACRO-FULL"
    STATE_VERIFY = "STATE-VERIFY"
    REL_COLD3 = "REL-COLD3"
    REL_BATCH_SENSITIVITY = "REL-BATCH-SENSITIVITY"
    DOWNSTREAM_GOLD = "DOWNSTREAM-GOLD"
    DOWNSTREAM_LIVE = "DOWNSTREAM-LIVE"
    EARLY_MINIMAL = "EARLY-MINIMAL"
    EARLY_VOI = "EARLY-VOI"
    EARLY_FAILURE = "EARLY-FAILURE"
    REP_MACRO = "REP-MACRO"
    NEG_V11 = "NEG-V11"
    HELD_OUT_V11 = "HELD-OUT-V11"


class V11SnapshotUnit(BaseModel):
    """Runtime candidate snapshot for one source unit."""

    unit_id: str = Field(min_length=1, max_length=96)
    source_text: str = Field(min_length=1)
    # A live coarse locator may legitimately return no candidates for a unit;
    # SNAP-INTEGRITY must expose that as zero coverage rather than reject it.
    candidates: list[V10CalibratedSpan] = Field(default_factory=list)


class V11CandidateSnapshot(BaseModel):
    """A frozen candidate pool; expected fixture fields are never included."""

    schema_version: Literal["v11-candidate-snapshot-1"]
    snapshot_version: str = Field(min_length=1, max_length=160)
    matrix_sha256: str = Field(min_length=64, max_length=64)
    source_kind: Literal["v10-live-calibration", "ideal-golden-control"]
    span_extractor_version: str = Field(min_length=1, max_length=240)
    boundary_calibration_version: str = Field(min_length=1, max_length=240)
    units: list[V11SnapshotUnit] = Field(min_length=1)


class V11MacroActRaw(BaseModel):
    unit_id: str = Field(min_length=1, max_length=96)
    act_type: V8DiscourseActType
    evidence_span_id: str = Field(min_length=1, max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V11MacroClaimRaw(BaseModel):
    unit_id: str = Field(min_length=1, max_length=96)
    seed_id: str = Field(min_length=1, max_length=160)
    seed_decision: Literal["accepted", "review_required", "rejected"]
    statement_type: V8UserStatementType
    coarse_type: V8CoarseType
    support_anchor_span_id: str = Field(min_length=1, max_length=160)
    target_span_id: str = Field(min_length=1, max_length=160)
    relation_span_id: str | None = Field(default=None, max_length=160)
    subject_span_id: str | None = Field(default=None, max_length=160)
    action_agent_span_id: str | None = Field(default=None, max_length=160)
    action_recipient_span_id: str | None = Field(default=None, max_length=160)
    experiencer_span_id: str | None = Field(default=None, max_length=160)
    object_span_id: str | None = Field(default=None, max_length=160)
    temporal_span_id: str | None = Field(default=None, max_length=160)
    measurement_span_id: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def reject_non_accepted_claim_without_reason(self) -> V11MacroClaimRaw:
        if self.seed_decision != "accepted" and self.confidence > 0.95:
            raise ValueError("non_accepted_seed_requires_low_confidence")
        return self


class V11MacroSemanticRawOutput(BaseModel):
    schema_version: Literal["v11-macro-seeded-1"]
    no_act_reason: str = Field(default="", max_length=160)
    acts: list[V11MacroActRaw] = Field(default_factory=list, max_length=64)
    claims: list[V11MacroClaimRaw] = Field(default_factory=list, max_length=128)
    coverage_gap_suspected: bool = False
    coverage_gap_reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def require_coverage_gap_when_no_claims(self) -> V11MacroSemanticRawOutput:
        if not self.acts and not self.no_act_reason:
            raise ValueError("empty_acts_require_no_act_reason")
        if not self.claims and not self.coverage_gap_suspected:
            raise ValueError("empty_claims_require_coverage_gap")
        if self.coverage_gap_suspected and not self.coverage_gap_reason:
            raise ValueError("coverage_gap_requires_reason")
        return self


class V11StatementVerificationRawOutput(BaseModel):
    schema_version: Literal["v11-statement-verify-1"]
    verdict: Literal["confirmed", "mismatch", "uncertain"]
    corrected_statement_type: V8UserStatementType
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_required: bool
    reason: str = Field(default="", max_length=240)


class V11RoleMenuRecord(BaseModel):
    role: str
    span_id: str
    source: Literal["primary", "fallback"]
    reason: str = Field(default="", max_length=120)
    rank: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    label: V8SpanLabel
    text: str = Field(min_length=1, max_length=480)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_fallback_reason(self) -> V11RoleMenuRecord:
        if self.source == "fallback" and not self.reason:
            raise ValueError("fallback_candidate_requires_reason")
        if self.end <= self.start:
            raise ValueError("invalid_candidate_boundary")
        return self
