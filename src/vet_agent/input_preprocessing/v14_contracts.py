"""Contracts for the V14 one-pass governance convergence experiment."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .v13_contracts import (
    V13ClaimKind,
    V13CoarseType,
    V13EpistemicStatus,
    V13ModalityType,
    V13Polarity,
    V13UserStatementType,
    V13VerifierStatus,
)

V14_REPORT_VERSION = "v14-experiment-report-1"
V14_INTENT_SCHEMA_VERSION = "v14-fixed-field-intent-1"
V14_CLAIM_SCHEMA_VERSION = "v14-onepass-inventory-claim-1"
V14_INTENT_PROMPT_VERSION = "v14-intent-fixed-field-dev-20260901-1"
V14_CLAIM_PROMPT_VERSION = "v14-onepass-inventory-dev-20260901-1"
V14_SKILL_VERSION = "v14-claim-skills-20260901-1"
V14_ALIGNMENT_VERSION = "v14-claim-local-aligner-20260901-1"
V14_PARTICIPANT_VERSION = "v14-turncontext-candidate-only-1"
V14_CANONICAL_VERSION = "v14-dual-query-constrained-selector-1"


class V14ExperimentId(StrEnum):
    EXEC_OBS = "EXEC-OBS"
    INTENT_SPLIT = "INTENT-SPLIT"
    GEN_OPTION = "GEN-OPTION"
    SKILL_INVENTORY = "SKILL-INVENTORY"
    SKILL_SHARED = "SKILL-SHARED"
    SKILL_NULL = "SKILL-NULL"
    SKILL_PARTICIPANT = "SKILL-PARTICIPANT"
    ALIGN_LOCAL = "ALIGN-LOCAL"
    PARTICIPANT_V14 = "PARTICIPANT-V14"
    TEMPORAL_V14 = "TEMPORAL-V14"
    MEASUREMENT_V14 = "MEASUREMENT-V14"
    CAN_SELECT_V14 = "CAN-SELECT-V14"
    MINIMAL_LANE = "MINIMAL-LANE"
    REP_V14 = "REP-V14"
    NEG_V14 = "NEG-V14"
    ASYNC_V14 = "ASYNC-V14"
    HELD_OUT_V14 = "HELD-OUT-V14"


class V14SignalKey(StrEnum):
    ANSWER_NOW = "answer_now"
    WANTS_TRIAGE = "wants_triage"
    CORRECTION = "correction"
    CLARIFICATION_REQUEST = "clarification_request"
    FACT_STATEMENT_PRESENT = "fact_statement_present"
    QUESTION_PRESENT = "question_present"
    REPORT_CONTEXT_PRESENT = "report_context_present"


class V14OptionalStatus(StrEnum):
    NULL = "null"
    NOT_APPLICABLE = "not_applicable"
    AMBIGUOUS = "ambiguous"
    REVIEW_REQUIRED = "review_required"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class V14SignalDetection(_StrictModel):
    detected: bool
    evidence_phrase: str | None = Field(default=None, max_length=480)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False

    @model_validator(mode="after")
    def validate_signal(self) -> V14SignalDetection:
        if self.detected and not self.evidence_phrase:
            raise ValueError("detected_signal_requires_evidence_phrase")
        if not self.detected and self.evidence_phrase:
            raise ValueError("undetected_signal_must_not_have_evidence_phrase")
        return self


class V14TurnIntentRaw(_StrictModel):
    schema_version: Literal["v14-fixed-field-intent-1"]
    answer_now: V14SignalDetection
    wants_triage: V14SignalDetection
    correction: V14SignalDetection
    clarification_request: V14SignalDetection
    fact_statement_present: V14SignalDetection
    question_present: V14SignalDetection
    report_context_present: V14SignalDetection
    no_signal_reason: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_no_signal(self) -> V14TurnIntentRaw:
        signals = (
            self.answer_now,
            self.wants_triage,
            self.correction,
            self.clarification_request,
            self.fact_statement_present,
            self.question_present,
            self.report_context_present,
        )
        if not any(item.detected for item in signals) and not self.no_signal_reason:
            raise ValueError("no_signal_requires_reason")
        if any(item.detected for item in signals) and self.no_signal_reason:
            raise ValueError("detected_signal_must_not_have_no_signal_reason")
        return self


class V14ClaimInventoryItem(_StrictModel):
    ordinal: int = Field(
        ge=1,
        le=128,
        description="Unique, continuous claim ordinal starting at 1.",
    )
    evidence_phrase: str = Field(min_length=1, max_length=480)
    claim_kind: V13ClaimKind
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V14ClaimRecordRaw(_StrictModel):
    inventory_ordinal: int = Field(
        ge=1,
        le=128,
        description="Exactly one inventory ordinal; each ordinal is used once.",
    )
    claim_type: V13ClaimKind
    coarse_type: V13CoarseType
    evidence_phrase: str = Field(min_length=1, max_length=480)
    target_phrase: str = Field(min_length=1, max_length=240)

    subject_phrase: str | None = Field(default=None, max_length=160)
    experiencer_phrase: str | None = Field(default=None, max_length=160)
    action_agent_phrase: str | None = Field(default=None, max_length=160)
    action_recipient_phrase: str | None = Field(default=None, max_length=160)
    object_phrase: str | None = Field(default=None, max_length=160)

    user_statement_type: V13UserStatementType
    polarity: V13Polarity
    modality_type: V13ModalityType
    modality_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    epistemic_status: V13EpistemicStatus

    temporal_phrase: str | None = Field(default=None, max_length=160)
    temporal_relation: str = Field(default="", max_length=80)
    temporal_value: str = Field(default="", max_length=120)
    temporal_precision: str = Field(default="", max_length=80)

    measurement_phrase: str | None = Field(default=None, max_length=160)
    measurement_value: str = Field(default="", max_length=120)
    measurement_unit: str = Field(default="", max_length=80)
    measurement_relation: str = Field(default="", max_length=80)

    relation_phrase: str | None = Field(default=None, max_length=160)
    canonical_descriptor: str | None = Field(default=None, max_length=240)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False
    missing_field_reason: str = Field(default="", max_length=480)


class V14ClaimGenerationRaw(_StrictModel):
    schema_version: Literal["v14-onepass-inventory-claim-1"]
    claim_inventory: list[V14ClaimInventoryItem] = Field(
        default_factory=list, min_length=0, max_length=128
    )
    claims: list[V14ClaimRecordRaw] = Field(default_factory=list, max_length=128)
    coverage_gap_suspected: bool
    coverage_gap_reason: str = Field(max_length=240)

    @model_validator(mode="after")
    def validate_inventory(self) -> V14ClaimGenerationRaw:
        if not self.claim_inventory and not self.coverage_gap_suspected:
            raise ValueError("empty_inventory_requires_coverage_gap")
        if self.coverage_gap_suspected and not self.coverage_gap_reason:
            raise ValueError("coverage_gap_requires_reason")
        inventory_ordinals = [item.ordinal for item in self.claim_inventory]
        claim_ordinals = [item.inventory_ordinal for item in self.claims]
        if len(inventory_ordinals) != len(set(inventory_ordinals)):
            raise ValueError("inventory_ordinal_must_be_unique")
        if len(claim_ordinals) != len(inventory_ordinals):
            raise ValueError("claim_inventory_mismatch")
        if set(claim_ordinals) != set(inventory_ordinals):
            # Qwen occasionally emits a repeated procedural ordinal while the
            # claims still follow the inventory one-by-one.  Preserve the raw
            # output and audit this drift; do not silently rewrite the model.
            ordered_matches = (
                len(inventory_ordinals) == len(claim_ordinals)
                and all(
                    claim.evidence_phrase == item.evidence_phrase
                    for claim, item in zip(
                        sorted(self.claims, key=lambda value: value.inventory_ordinal),
                        sorted(
                            self.claim_inventory,
                            key=lambda value: value.ordinal,
                        ),
                        strict=True,
                    )
                )
            )
            if not ordered_matches:
                raise ValueError("claim_inventory_mismatch")
        inventory_by_ordinal = {
            item.ordinal: item for item in self.claim_inventory
        }
        for claim in self.claims:
            item = inventory_by_ordinal[claim.inventory_ordinal]
            if claim.evidence_phrase != item.evidence_phrase:
                raise ValueError("claim_evidence_must_match_inventory")
            compatible_kinds = {
                "state": {"state", "denial", "relation", "historical", "hypothetical"},
                "denial": {"denial", "state"},
                "relation": {"relation", "state"},
                "historical": {"historical", "state"},
                "hypothetical": {"hypothetical", "state"},
            }
            if claim.claim_type.value not in compatible_kinds.get(
                item.claim_kind.value,
                {item.claim_kind.value},
            ):
                raise ValueError("claim_kind_must_match_inventory")
        return self


class V14GenerationOptions(BaseModel):
    option_id: str = Field(min_length=1, max_length=32)
    temperature: float = 0.0
    top_p: float | None = None
    seed: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    max_tokens: int | None = None
    max_attempts: int = Field(default=1, ge=1, le=2)


class V14ExecutionMetadata(BaseModel):
    adapter: str = "qwen-response-format"
    model: str = ""
    provider_model: str = ""
    response_id: str = ""
    finish_reason: str = ""
    latency_ms: int = 0
    attempt_count: int = 1
    first_attempt_status: str = "ok"
    first_attempt_error: str = ""
    model_call_count: int = 1
    token_count_available: bool = False
    prompt_token_count: int = 0
    completion_token_count: int = 0
    total_token_count: int = 0
    cost_available: bool = False
    generation_options: dict[str, object] = Field(default_factory=dict)
    effective_parameter_status: str = "unverifiable"


class V14AlignmentStatus(StrEnum):
    EXACT = "exact"
    EXACT_NORMALIZED = "exact_normalized"
    FUZZY_VERIFIED = "fuzzy_verified"
    FUZZY_AMBIGUOUS = "fuzzy_ambiguous"
    WRONG_OCCURRENCE = "wrong_occurrence"
    OUTSIDE_PARENT = "outside_parent"
    CROSS_SOURCE_BLOCK = "cross_source_block"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    NEGATION_LOST = "negation_lost"
    TEMPORAL_LOST = "temporal_lost"
    SUBJECT_LOST = "subject_lost"
    EMPTY_PHRASE = "empty_phrase"
    NOT_FOUND = "not_found"


class V14AlignedField(BaseModel):
    field_name: str
    model_phrase: str = ""
    aligned_quote: str = ""
    start: int = -1
    end: int = -1
    source_block_id: str = "block-001"
    alignment_status: V14AlignmentStatus
    similarity: float = 0.0
    best_candidate: str = ""
    second_best_candidate: str = ""
    score_margin: float = 0.0
    alignment_method: str = ""
    verifier_status: V13VerifierStatus
    review_required: bool = False
    alignment_scope: str = "claim_local"
    resolution_method: str = ""


class V14GovernedClaim(BaseModel):
    source_id: str
    deterministic_claim_id: str
    raw_claim: V14ClaimRecordRaw
    evidence: V14AlignedField
    target: V14AlignedField
    fields: dict[str, V14AlignedField] = {}
    projection_ready: bool = False
    review_required: bool = True
    blocked_reasons: list[str] = []
