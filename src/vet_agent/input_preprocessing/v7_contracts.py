"""Contracts for the V7 attribution microbench suite.

V7 intentionally uses small model-facing schemas.  Each contract answers one
architecture question; it must not grow back into a full NLP pipeline schema.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class V7ExperimentId(StrEnum):
    """Core microbench identifiers defined by the V7 attribution suite."""

    INTENT_ANSWER_NOW = "INTENT-ANSWER-NOW"
    INTENT_FACT_DETECT = "INTENT-FACT-DETECT"
    INTENT_QUESTION = "INTENT-QUESTION"
    QUOTE_GOLDEN_SELECT = "QUOTE-GOLDEN-SELECT"
    THIN_LIVE_MIN = "THIN-LIVE-MIN"
    RELATION_GOLDEN = "RELATION-GOLDEN"
    CAN_GOLDEN_DIRECT = "CAN-GOLDEN-DIRECT"
    PART_GOLDEN = "PART-GOLDEN"


class V7UserStatementType(StrEnum):
    """Speech-act semantics retained from the V5/V6 experiments."""

    REPORTS = "reports"
    DENIES = "denies"
    REPORTS_NORMAL = "reports_normal"
    REPORTS_ABNORMAL = "reports_abnormal"
    UNCERTAIN = "uncertain"
    HISTORICAL = "historical"
    HYPOTHETICAL = "hypothetical"
    ASKS = "asks"
    CORRECTS = "corrects"


class V7CoarseType(StrEnum):
    """Small structural type; this is not a veterinary taxonomy."""

    SYMPTOM = "symptom"
    STATE = "state"
    ACTION = "action"
    EXPOSURE = "exposure"
    FOOD = "food"
    MEDICATION = "medication"
    TIME = "time"
    MEASUREMENT = "measurement"
    CONTEXT = "context"


class V7RelationClass(StrEnum):
    """The only relation classes allowed in V7."""

    ABSOLUTE_STATUS = "absolute_status"
    NO_CHANGE = "no_change"
    CHANGE = "change"
    UNCLEAR = "unclear"


class V7AttributionCode(StrEnum):
    """Failure attribution shared by all V7 microbenches."""

    INTENT_CONTRACT_CONTAMINATION = "intent_contract_contamination"
    INTENT_MODEL_ERROR = "intent_model_error"
    QUOTE_EXTRACTION_ERROR = "quote_extraction_error"
    QUOTE_SELECTOR_ERROR = "quote_selector_error"
    TARGET_CONTAINMENT_ERROR = "target_containment_error"
    RELATION_QUOTE_MISSING = "relation_quote_missing"
    RELATION_CLASSIFICATION_ERROR = "relation_classification_error"
    CANONICAL_RECALL_MISS = "canonical_recall_miss"
    CANONICAL_FILTER_ERROR = "canonical_filter_error"
    PARTICIPANT_CANDIDATE_ERROR = "participant_candidate_error"
    PARTICIPANT_ROLE_ERROR = "participant_role_error"
    GATE_BLOCKED_EXPECTED = "gate_blocked_expected"
    UPSTREAM_BLOCKED = "upstream_blocked"
    RUNNER_ERROR = "runner_error"


class V7IntentBinaryRaw(BaseModel):
    """One binary intent result with a quote anchor."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    detected: bool
    evidence_quote: str = Field(default="", max_length=480)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V7IntentBatchRawOutput(BaseModel):
    """Batched output for one intent micro-classifier."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-intent-binary-raw"] = "v7-intent-binary-raw"
    results: list[V7IntentBinaryRaw] = Field(min_length=1, max_length=32)


class V7QuoteSelectionRaw(BaseModel):
    """Sub-quote selection for one golden evidence quote."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    target_quote: str = Field(min_length=1, max_length=240)
    relation_quote: str = Field(default="", max_length=240)
    subject_evidence_quote: str = Field(min_length=1, max_length=480)
    temporal_quote: str = Field(default="", max_length=240)
    measurement_quote: str = Field(default="", max_length=240)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V7QuoteSelectionRawOutput(BaseModel):
    """Batched golden-quote selector output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-quote-selection-raw"] = "v7-quote-selection-raw"
    results: list[V7QuoteSelectionRaw] = Field(min_length=1, max_length=32)


class V7ThinUserClaimRaw(BaseModel):
    """Minimal live thin claim; relation class is deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    source_id: Literal["current_turn"] = "current_turn"
    source_block_id: str = Field(default="block-current", max_length=96)
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    user_statement_type: V7UserStatementType
    coarse_type: V7CoarseType
    subject_role_hint: Literal[
        "subject", "action_agent", "action_recipient", "experiencer", "unknown"
    ] = "subject"
    subject_status: Literal["pending", "ambiguous", "unresolved"] = "pending"
    subject_evidence_quote: str = Field(min_length=1, max_length=480)
    temporal_quote: str = Field(default="", max_length=160)
    measurement_quote: str = Field(default="", max_length=160)
    relation_quote: str = Field(default="", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = False


class V7ThinExtractionRawOutput(BaseModel):
    """Minimal live thin extraction output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-thin-min-raw"] = "v7-thin-min-raw"
    claims: list[V7ThinUserClaimRaw] = Field(default_factory=list, max_length=64)


class V7RelationClassificationRaw(BaseModel):
    """Independent relation classification for one golden quote."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    relation: V7RelationClass
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V7RelationRawOutput(BaseModel):
    """Batched independent relation-classifier output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-relation-raw"] = "v7-relation-raw"
    results: list[V7RelationClassificationRaw] = Field(min_length=1, max_length=32)


class V7ParticipantSelectionRaw(BaseModel):
    """Candidate-only participant selection for one action claim."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    action_agent_selected_candidate: str | None = Field(default=None, max_length=64)
    action_recipient_selected_candidate: str | None = Field(default=None, max_length=64)
    object_mention: str = Field(default="", max_length=160)
    resolution_status: Literal["resolved", "ambiguous", "unresolved"] = "resolved"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class V7ParticipantRawOutput(BaseModel):
    """Batched candidate-only participant output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-participant-raw"] = "v7-participant-raw"
    results: list[V7ParticipantSelectionRaw] = Field(min_length=1, max_length=32)
