"""Contracts for the thirteenth LLM-first structured-claim experiment.

V13 deliberately reverses the V8--V12 ordering: the model first proposes flat
linguistic phrases from the complete turn, then deterministic code aligns those
phrases back to source offsets.  A model phrase is never evidence until this
alignment succeeds.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

V13_REPORT_VERSION = "v13-experiment-report-1"
V13_SCHEMA_VERSION = "v13-llm-first-structured-claim-1"
V13_ALIGNER_VERSION = "v13-conservative-fuzzy-aligner-20260901-2"
V13_INTENT_PROMPT_VERSION = "v13-intent-dev-20260901-4"
V13_SEGMENTATION_PROMPT_VERSION = "v13-segmentation-dev-20260901-4"
V13_CLAIM_PROMPT_VERSION = "v13-flat-claim-dev-20260901-4"


class V13PhrasePolicy(StrEnum):
    """How freely the model may phrase a source-grounding proposal.

    ``literal`` is a conservative control. ``approximate`` is the V13 primary
    experiment: the model proposes a semantic phrase, while deterministic code
    remains the only component allowed to select an actual source quote.
    """

    LITERAL = "literal"
    APPROXIMATE = "approximate"


class V13IntentActType(StrEnum):
    """Closed turn-level act vocabulary used by the V13 intent analyzer."""

    ANSWER_NOW = "answer_now"
    WANTS_TRIAGE = "wants_triage"
    CORRECTION = "correction"
    CLARIFICATION_REQUEST = "clarification_request"
    FACT_STATEMENT = "fact_statement"
    QUESTION = "question"
    REPORT_CONTEXT = "report_context"


class V13ExperimentId(StrEnum):
    ALIGNER_CONTROL = "ALIGNER-CONTROL"
    TURN_INTENT = "TURN-INTENT"
    NEG_V13 = "NEG-V13"
    LLMF_SEG_ONLY = "LLMF-SEG-ONLY"
    LLMF_ONEPASS = "LLMF-ONEPASS"
    LLMF_TWOSTAGE = "LLMF-TWOSTAGE"
    CLAIM_ALIGN = "CLAIM-ALIGN"
    FUZZY_POLICY = "FUZZY-POLICY"
    STATEMENT_SEMANTICS = "STATEMENT-SEMANTICS"
    TEMPORAL_PROPOSAL = "TEMPORAL-PROPOSAL"
    MEASUREMENT_PROPOSAL = "MEASUREMENT-PROPOSAL"
    PARTICIPANT_RESOLVE = "PARTICIPANT-RESOLVE"
    CAN_DESCRIPTOR = "CAN-DESCRIPTOR"
    CLAIM_GRAPH = "CLAIM-GRAPH"
    PARADIGM_COMPARE = "PARADIGM-COMPARE"
    REP_V13 = "REP-V13"
    ASYNC_V13 = "ASYNC-V13"
    HELD_OUT_V13 = "HELD-OUT-V13"


class V13ClaimKind(StrEnum):
    ACTION = "action"
    STATE = "state"
    DENIAL = "denial"
    RELATION = "relation"
    QUESTION = "question"
    CORRECTION = "correction"
    HISTORICAL = "historical"
    HYPOTHETICAL = "hypothetical"


class V13UserStatementType(StrEnum):
    REPORTS = "reports"
    DENIES = "denies"
    REPORTS_NORMAL = "reports_normal"
    REPORTS_ABNORMAL = "reports_abnormal"
    UNCERTAIN = "uncertain"
    HISTORICAL = "historical"
    HYPOTHETICAL = "hypothetical"
    ASKS = "asks"
    CORRECTS = "corrects"


class V13Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class V13ModalityType(StrEnum):
    FACTUAL = "factual"
    HYPOTHETICAL = "hypothetical"
    HISTORICAL = "historical"
    REPORTED = "reported"
    UNCERTAIN = "uncertain"


class V13EpistemicStatus(StrEnum):
    CERTAIN = "certain"
    UNCERTAIN = "uncertain"
    SECONDHAND = "secondhand"
    UNKNOWN = "unknown"


class V13CoarseType(StrEnum):
    SYMPTOM = "symptom"
    STATE = "state"
    ACTION = "action"
    FOOD = "food"
    MEDICATION = "medication"
    TIME = "time"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class V13TurnIntentActRaw(_StrictModel):
    act_type: V13IntentActType
    evidence_phrase: str = Field(min_length=1, max_length=480)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V13TurnIntentRawOutput(_StrictModel):
    schema_version: Literal["v13-intent-1"]
    acts: list[V13TurnIntentActRaw] = Field(default_factory=list, max_length=16)
    no_act_reason: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def require_no_act_reason(self) -> V13TurnIntentRawOutput:
        if not self.acts and not self.no_act_reason:
            raise ValueError("empty_acts_require_no_act_reason")
        return self


class V13ClaimUnitRaw(_StrictModel):
    unit_id: str = Field(min_length=1, max_length=96)
    evidence_phrase: str = Field(min_length=1, max_length=480)
    core_phrase: str = Field(min_length=1, max_length=240)
    claim_kind: V13ClaimKind
    subject_hint: str = Field(default="", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    coverage_gap_reason: str = Field(default="", max_length=240)


class V13ClaimUnitRawOutput(_StrictModel):
    schema_version: Literal["v13-claim-units-1"]
    units: list[V13ClaimUnitRaw] = Field(default_factory=list, max_length=64)
    coverage_gap_suspected: bool = False
    coverage_gap_reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def require_coverage_gap(self) -> V13ClaimUnitRawOutput:
        if not self.units and not self.coverage_gap_suspected:
            raise ValueError("empty_claim_units_require_coverage_gap")
        if self.coverage_gap_suspected and not self.coverage_gap_reason:
            raise ValueError("coverage_gap_requires_reason")
        return self


class V13ClaimRecordRaw(_StrictModel):
    unit_id: str = Field(min_length=1, max_length=96)
    claim_type: V13ClaimKind
    coarse_type: V13CoarseType
    evidence_phrase: str = Field(min_length=1, max_length=480)
    target_phrase: str = Field(min_length=1, max_length=240)

    subject_phrase: str = Field(default="", max_length=160)
    action_agent_phrase: str = Field(default="", max_length=160)
    action_recipient_phrase: str = Field(default="", max_length=160)
    object_phrase: str = Field(default="", max_length=160)

    user_statement_type: V13UserStatementType
    polarity: V13Polarity
    modality_type: V13ModalityType
    modality_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    epistemic_status: V13EpistemicStatus

    temporal_phrase: str = Field(default="", max_length=160)
    temporal_relation: str = Field(default="", max_length=80)
    temporal_value: str = Field(default="", max_length=120)
    temporal_precision: str = Field(default="", max_length=80)

    measurement_phrase: str = Field(default="", max_length=160)
    measurement_value: str = Field(default="", max_length=120)
    measurement_unit: str = Field(default="", max_length=80)
    measurement_relation: str = Field(default="", max_length=80)

    relation_phrase: str = Field(default="", max_length=160)
    canonical_descriptor: str = Field(default="", max_length=240)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    missing_field_reason: str = Field(default="", max_length=240)


class V13ClaimRecordRawOutput(_StrictModel):
    schema_version: Literal["v13-claim-records-1"]
    claims: list[V13ClaimRecordRaw] = Field(default_factory=list, max_length=128)
    coverage_gap_suspected: bool = False
    coverage_gap_reason: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def require_coverage_gap(self) -> V13ClaimRecordRawOutput:
        if not self.claims and not self.coverage_gap_suspected:
            raise ValueError("empty_claims_require_coverage_gap")
        if self.coverage_gap_suspected and not self.coverage_gap_reason:
            raise ValueError("coverage_gap_requires_reason")
        return self


class V13AlignmentStatus(StrEnum):
    EXACT = "exact"
    EXACT_NORMALIZED = "exact_normalized"
    FUZZY_VERIFIED = "fuzzy_verified"
    FUZZY_AMBIGUOUS = "fuzzy_ambiguous"
    FUZZY_NOT_FOUND = "fuzzy_not_found"
    CROSS_SOURCE_BLOCK = "cross_source_block"
    EMPTY_PHRASE = "empty_phrase"


class V13VerifierStatus(StrEnum):
    VERIFIED = "verified"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    NEGATION_LOST = "negation_lost"
    TEMPORAL_LOST = "temporal_lost"
    SUBJECT_LOST = "subject_lost"
    BOUNDARY_CROSSING = "boundary_crossing"
    UNCERTAIN = "uncertain"


class V13AlignedEvidence(_StrictModel):
    field_name: str = Field(min_length=1, max_length=80)
    model_phrase: str = Field(default="", max_length=480)
    aligned_quote: str = Field(default="", max_length=480)
    start: int = Field(default=-1)
    end: int = Field(default=-1)
    source_block_id: str = Field(default="block-001", max_length=96)
    alignment_status: V13AlignmentStatus
    similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    best_candidate: str = Field(default="", max_length=480)
    second_best_candidate: str = Field(default="", max_length=480)
    score_margin: float = Field(default=0.0, ge=0.0, le=1.0)
    alignment_method: str = Field(default="", max_length=80)
    verifier_status: V13VerifierStatus = V13VerifierStatus.VERIFIED
    review_required: bool = False

    @model_validator(mode="after")
    def validate_offset(self) -> V13AlignedEvidence:
        accepted = {
            V13AlignmentStatus.EXACT,
            V13AlignmentStatus.EXACT_NORMALIZED,
            V13AlignmentStatus.FUZZY_VERIFIED,
        }
        if self.alignment_status in accepted:
            if not (0 <= self.start < self.end) or not self.aligned_quote:
                raise ValueError("accepted_alignment_requires_offset")
        elif self.start >= 0 and self.end <= self.start:
            raise ValueError("invalid_alignment_offset")
        return self


class V13GovernedClaim(_StrictModel):
    source_id: str = Field(min_length=1, max_length=96)
    deterministic_claim_id: str = Field(min_length=1, max_length=160)
    raw_claim: V13ClaimRecordRaw
    evidence: V13AlignedEvidence
    target: V13AlignedEvidence
    fields: dict[str, V13AlignedEvidence] = Field(default_factory=dict)
    projection_ready: bool = False
    review_required: bool = True
    blocked_reasons: list[str] = Field(default_factory=list)
