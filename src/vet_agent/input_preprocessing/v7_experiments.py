"""V7 attribution core microbench runner.

This runner intentionally does **not** execute the former large architecture
matrix.  Each experiment changes one component and reports a dedicated
attribution code.  Remote mode remains report-only and never invokes clinical
safety evaluator / OPA.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .runtime_helpers import make_runtime_settings
from .v6_canonical_linker import V6CandidateRetriever, V6EmbeddingClient
from .v7_contracts import (
    V7AttributionCode,
    V7CoarseType,
    V7ExperimentId,
    V7IntentBatchRawOutput,
    V7IntentBinaryRaw,
    V7ParticipantRawOutput,
    V7ParticipantSelectionRaw,
    V7QuoteSelectionRaw,
    V7QuoteSelectionRawOutput,
    V7RelationClass,
    V7RelationClassificationRaw,
    V7RelationRawOutput,
    V7ThinExtractionRawOutput,
    V7ThinUserClaimRaw,
    V7UserStatementType,
)
from .v7_microbench import (
    V7_PROMPT_VERSION,
    V7MicroAnalyzer,
    V7MicrobenchError,
    V7ModelExecution,
)
from .v7_quote_governance import check_source_quote
from .v7_run_cache import V7RunCache, digest_value
from .vocabulary import CanonicalVocabulary

V7_GATE_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V7_GATE_VERSION",
    "v7-attribution-gates-dev-20260827-1",
)


class V7IntentUnit(BaseModel):
    """One binary intent micro-sample."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    intent_experiment: V7ExperimentId
    user_text: str = Field(min_length=1, max_length=4000)
    expected_detected: bool
    expected_evidence_quote: str = Field(default="", max_length=480)
    tags: list[str] = Field(default_factory=list, max_length=8)


class V7QuoteUnit(BaseModel):
    """One golden evidence quote and expected sub-quotes."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    user_text: str = Field(min_length=1, max_length=4000)
    claim_hint: str = Field(min_length=1, max_length=240)
    evidence_quote: str = Field(min_length=1, max_length=480)
    expected_target_quote: str = Field(min_length=1, max_length=240)
    expected_relation_quote: str = Field(default="", max_length=240)
    expected_subject_evidence_quote: str = Field(min_length=1, max_length=480)
    expected_temporal_quote: str = Field(default="", max_length=240)
    expected_measurement_quote: str = Field(default="", max_length=240)


class V7ExpectedThinClaim(BaseModel):
    """Expected semantic identity for one minimal live claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=96)
    evidence_quote: str = Field(min_length=1, max_length=480)
    target_quote: str = Field(min_length=1, max_length=240)
    user_statement_type: str
    coarse_type: str
    subject_evidence_quote: str = Field(min_length=1, max_length=480)
    temporal_quote: str = Field(default="", max_length=160)
    measurement_quote: str = Field(default="", max_length=160)
    relation_quote: str = Field(default="", max_length=160)
    relation_quote_required: bool = False


class V7ThinUnit(BaseModel):
    """One live minimal thin-extraction sample."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    user_text: str = Field(min_length=1, max_length=8000)
    expected_claims: list[V7ExpectedThinClaim] = Field(min_length=1, max_length=64)


class V7RelationUnit(BaseModel):
    """One golden relation-classification sample."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    user_text: str = Field(min_length=1, max_length=4000)
    target_quote: str = Field(min_length=1, max_length=240)
    relation_quote: str = Field(min_length=1, max_length=240)
    expected_relation: V7RelationClass


class V7CanonicalUnit(BaseModel):
    """One golden direct canonical-recall probe."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    target_quote: str = Field(min_length=1, max_length=240)
    coarse_type: str
    expected_canonical_ids: list[str] = Field(min_length=1, max_length=4)


class V7ParticipantUnit(BaseModel):
    """One golden candidate-only participant sample."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(min_length=1, max_length=96)
    user_text: str = Field(min_length=1, max_length=4000)
    entity_candidates: list[dict[str, Any]] = Field(min_length=1, max_length=12)
    expected_action_agent: str
    expected_action_recipient: str
    expected_object_mention: str = Field(min_length=1, max_length=160)


class V7ExperimentDocument(BaseModel):
    """Versioned V7 core microbench fixture."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v7-attribution-1"]
    dataset_type: Literal["development", "held_out"]
    intent_units: list[V7IntentUnit] = Field(default_factory=list, max_length=64)
    quote_units: list[V7QuoteUnit] = Field(default_factory=list, max_length=64)
    thin_units: list[V7ThinUnit] = Field(default_factory=list, max_length=32)
    relation_units: list[V7RelationUnit] = Field(default_factory=list, max_length=64)
    canonical_units: list[V7CanonicalUnit] = Field(default_factory=list, max_length=64)
    participant_units: list[V7ParticipantUnit] = Field(
        default_factory=list, max_length=64
    )


class CountingEmbeddingClient:
    """Wrap an embedding client so V7 reports embedding-call costs."""

    def __init__(self, inner: V6EmbeddingClient) -> None:
        self.inner = inner
        self.call_count = 0

    @property
    def available(self) -> bool:
        return self.inner.available

    def embed(self, text: str) -> list[float]:
        self.call_count += 1
        return self.inner.embed(text)


class HashingAliasEmbeddingClient:
    """Deterministic local embedding used only by ideal control."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * 128
        for token in _text_tokens(text):
            index = sum(ord(character) for character in token) % len(vector)
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class V7QwenClientProtocol(Protocol):
    """Structural protocol used by test doubles and runtime construction."""

    @property
    def available(self) -> bool: ...

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type,
        model: str,
        temperature: float = 0.0,
    ) -> Any: ...


def _text_tokens(text: str) -> list[str]:
    normalized: list[str] = []
    for size in (2, 3):
        normalized.extend(
            text[index : index + size] for index in range(len(text) - size + 1)
        )
    normalized.append(text)
    return normalized


def load_v7_experiment_document(path: Path) -> V7ExperimentDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return V7ExperimentDocument.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid V7 experiment document: {path}") from exc


class V7AttributionRunner:
    """Run only V7 core microbenches with isolated metrics and attribution."""

    def __init__(
        self,
        *,
        document: V7ExperimentDocument,
        vocabulary: CanonicalVocabulary,
        analyzer: V7MicroAnalyzer | None = None,
        analyzer_factory: Callable[[], V7MicroAnalyzer] | None = None,
        candidate_retriever: V6CandidateRetriever | None = None,
    ) -> None:
        self.document = document
        self.vocabulary = vocabulary
        self.analyzer = analyzer
        self.analyzer_factory = analyzer_factory
        if candidate_retriever is not None:
            self.candidate_retriever = candidate_retriever
            self.embedding_client = (
                candidate_retriever.embeddings
                if isinstance(candidate_retriever.embeddings, CountingEmbeddingClient)
                else CountingEmbeddingClient(
                    _UnsafeDirectEmbeddingAdapter(candidate_retriever)
                )
            )
        else:
            embeddings = CountingEmbeddingClient(HashingAliasEmbeddingClient())
            self.embedding_client = embeddings
            self.candidate_retriever = V6CandidateRetriever(
                vocabulary=vocabulary,
                embeddings=embeddings,
            )

    async def run(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        only_experiment_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        selected = only_experiment_ids or {item.value for item in V7ExperimentId}
        unknown = selected - {item.value for item in V7ExperimentId}
        if unknown:
            raise ValueError(f"unknown_v7_experiment:{','.join(sorted(unknown))}")
        reports: list[dict[str, Any]] = []
        for experiment_id in V7ExperimentId:
            if experiment_id.value not in selected:
                continue
            reports.append(await self._run_experiment(experiment_id, mode=mode))
        return reports

    def run_for_test(
        self,
        *,
        mode: Literal["ideal", "shadow"],
        only_experiment_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Synchronous test helper for deterministic ideal/mutation controls."""

        return asyncio.run(self.run(mode=mode, only_experiment_ids=only_experiment_ids))

    async def _run_experiment(
        self,
        experiment_id: V7ExperimentId,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        started = datetime.now().astimezone()
        try:
            if experiment_id in {
                V7ExperimentId.INTENT_ANSWER_NOW,
                V7ExperimentId.INTENT_FACT_DETECT,
                V7ExperimentId.INTENT_QUESTION,
            }:
                report = await self._run_intent(experiment_id, mode=mode)
            elif experiment_id == V7ExperimentId.QUOTE_GOLDEN_SELECT:
                report = await self._run_quote_selection(mode=mode)
            elif experiment_id == V7ExperimentId.THIN_LIVE_MIN:
                report = await self._run_thin(mode=mode)
            elif experiment_id == V7ExperimentId.RELATION_GOLDEN:
                report = await self._run_relation(mode=mode)
            elif experiment_id == V7ExperimentId.CAN_GOLDEN_DIRECT:
                report = self._run_canonical()
            elif experiment_id == V7ExperimentId.PART_GOLDEN:
                report = await self._run_participant(mode=mode)
            else:  # pragma: no cover - guarded by the enum
                raise ValueError(f"unsupported_v7_experiment:{experiment_id}")
        except V7MicrobenchError as exc:
            report = _adapter_failure_report(experiment_id, exc)
        report["suite"] = "core"
        report["mode"] = mode
        report["started_at"] = started.isoformat()
        report["completed_at"] = datetime.now().astimezone().isoformat()
        report["safety_boundary"] = {
            "consultation_state_written": False,
            "clinical_safety_evaluator_called": False,
            "clinical_safety_opa_called": False,
            "required_context_called": False,
        }
        return report

    async def _run_intent(
        self,
        experiment_id: V7ExperimentId,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        units = [
            item
            for item in self.document.intent_units
            if item.intent_experiment == experiment_id
        ]
        if not units:
            raise ValueError(f"v7_intent_units_missing:{experiment_id.value}")
        output = V7IntentBatchRawOutput(
            results=[
                V7IntentBinaryRaw(
                    unit_id=item.unit_id,
                    detected=item.expected_detected,
                    evidence_quote=item.expected_evidence_quote,
                    confidence=1.0,
                )
                for item in units
            ]
        )
        execution = V7ModelExecution(
            output=output,
            attempt_count=0,
            first_attempt_status="ideal",
        )
        if mode == "shadow":
            execution = await self._analyzer().run_intent(
                experiment_id=experiment_id,
                units=[
                    {"unit_id": item.unit_id, "user_text": item.user_text}
                    for item in units
                ],
                turn_context_digest=digest_value(
                    {"experiment": experiment_id.value, "units": len(units)}
                ),
            )
        expected = {item.unit_id: item for item in units}
        results = _result_list(execution.output)
        missing, unexpected, actual = _unit_maps(results, set(expected))
        unit_results: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        for unit_id in sorted(expected):
            item = expected[unit_id]
            raw = actual.get(unit_id)
            if raw is None:
                failures.append(f"{unit_id}:missing")
                attributions.append(
                    V7AttributionCode.INTENT_CONTRACT_CONTAMINATION.value
                )
                continue
            quote_check = check_source_quote(
                user_text=item.user_text,
                quote=raw.evidence_quote,
            )
            quote_valid = quote_check.valid if raw.detected else not raw.evidence_quote
            semantic_valid = raw.detected == item.expected_detected
            passed = semantic_valid and quote_valid
            if not passed:
                failures.append(
                    f"{unit_id}:{'intent' if not semantic_valid else 'quote'}"
                )
                attributions.append(
                    V7AttributionCode.INTENT_MODEL_ERROR.value
                    if not semantic_valid
                    else V7AttributionCode.QUOTE_SELECTOR_ERROR.value
                )
            unit_results.append(
                {
                    "unit_id": unit_id,
                    "tags": item.tags,
                    "expected_detected": item.expected_detected,
                    "actual_detected": raw.detected,
                    "actual_evidence_quote": raw.evidence_quote,
                    "quote_valid": quote_valid,
                    "passed": passed,
                }
            )
        for unit_id in sorted(unexpected):
            failures.append(f"{unit_id}:unexpected")
            attributions.append(V7AttributionCode.INTENT_CONTRACT_CONTAMINATION.value)
        del missing
        return _experiment_report(
            experiment_id=experiment_id,
            architecture_question=(
                "Can this intent attribute be identified independently with a valid quote?"
            ),
            unit_results=unit_results,
            failures=failures,
            attributions=attributions,
            execution=execution,
            extra_metrics=_intent_metrics(units, unit_results),
        )

    def _analyzer(self) -> V7MicroAnalyzer:
        if self.analyzer_factory is not None:
            return self.analyzer_factory()
        if self.analyzer is None:
            raise ValueError("v7_shadow_analyzer_unavailable")
        assert self.analyzer is not None
        return self.analyzer

    async def _run_quote_selection(
        self,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        units = self.document.quote_units
        if not units:
            raise ValueError("v7_quote_units_missing")
        output = V7QuoteSelectionRawOutput(
            results=[
                V7QuoteSelectionRaw(
                    unit_id=item.unit_id,
                    target_quote=item.expected_target_quote,
                    relation_quote=item.expected_relation_quote,
                    subject_evidence_quote=item.expected_subject_evidence_quote,
                    temporal_quote=item.expected_temporal_quote,
                    measurement_quote=item.expected_measurement_quote,
                    confidence=1.0,
                )
                for item in units
            ]
        )
        execution = V7ModelExecution(
            output=output,
            attempt_count=0,
            first_attempt_status="ideal",
        )
        if mode == "shadow":
            execution = await self._analyzer().run_quote_selection(
                units=[
                    {
                        "unit_id": item.unit_id,
                        "user_text": item.user_text,
                        "claim_hint": item.claim_hint,
                        "evidence_quote": item.evidence_quote,
                    }
                    for item in units
                ],
                turn_context_digest=digest_value({"units": len(units)}),
            )
        expected = {item.unit_id: item for item in units}
        actual = {item.unit_id: item for item in _result_list(execution.output)}
        unit_results: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        for unit_id in sorted(expected):
            item = expected[unit_id]
            raw = actual.get(unit_id)
            if raw is None:
                failures.append(f"{unit_id}:missing")
                attributions.append(V7AttributionCode.QUOTE_SELECTOR_ERROR.value)
                continue
            checks = {
                "target": _expected_quote_check(
                    user_text=item.user_text,
                    evidence_quote=item.evidence_quote,
                    expected=item.expected_target_quote,
                    actual=raw.target_quote,
                ),
                "relation": _expected_quote_check(
                    user_text=item.user_text,
                    evidence_quote=item.evidence_quote,
                    expected=item.expected_relation_quote,
                    actual=raw.relation_quote,
                ),
                "subject": _expected_quote_check(
                    user_text=item.user_text,
                    evidence_quote=item.evidence_quote,
                    expected=item.expected_subject_evidence_quote,
                    actual=raw.subject_evidence_quote,
                ),
                "temporal": _expected_quote_check(
                    user_text=item.user_text,
                    evidence_quote=item.evidence_quote,
                    expected=item.expected_temporal_quote,
                    actual=raw.temporal_quote,
                ),
                "measurement": _expected_quote_check(
                    user_text=item.user_text,
                    evidence_quote=item.evidence_quote,
                    expected=item.expected_measurement_quote,
                    actual=raw.measurement_quote,
                ),
            }
            passed = all(checks.values())
            if not passed:
                failures.append(
                    f"{unit_id}:{','.join(k for k, v in checks.items() if not v)}"
                )
                attributions.append(V7AttributionCode.QUOTE_SELECTOR_ERROR.value)
            unit_results.append(
                {
                    "unit_id": unit_id,
                    "actual_quotes": {
                        "target": raw.target_quote,
                        "relation": raw.relation_quote,
                        "subject": raw.subject_evidence_quote,
                        "temporal": raw.temporal_quote,
                        "measurement": raw.measurement_quote,
                    },
                    "checks": checks,
                    "passed": passed,
                }
            )
        for unit_id in sorted(set(actual) - set(expected)):
            failures.append(f"{unit_id}:unexpected")
            attributions.append(V7AttributionCode.QUOTE_SELECTOR_ERROR.value)
        valid_count = sum(all(item["checks"].values()) for item in unit_results)
        return _experiment_report(
            experiment_id=V7ExperimentId.QUOTE_GOLDEN_SELECT,
            architecture_question=(
                "Can sub-quotes be selected correctly from golden evidence quotes?"
            ),
            unit_results=unit_results,
            failures=failures,
            attributions=attributions,
            execution=execution,
            extra_metrics={
                "unit_count": len(units),
                "quote_selection_accuracy": _rate(valid_count, len(units)),
            },
        )

    async def _run_thin(self, *, mode: Literal["ideal", "shadow"]) -> dict[str, Any]:
        units = self.document.thin_units
        if not units:
            raise ValueError("v7_thin_units_missing")
        executions: list[V7ModelExecution] = []
        unit_reports: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        expected_count = 0
        matched_count = 0
        actual_count = 0
        quote_valid_count = 0
        for unit in units:
            ideal = V7ThinExtractionRawOutput(
                claims=[
                    V7ThinUserClaimRaw.model_validate(
                        claim.model_dump(exclude={"relation_quote_required"})
                    )
                    for claim in unit.expected_claims
                ]
            )
            execution = V7ModelExecution(
                output=ideal,
                attempt_count=0,
                first_attempt_status="ideal",
            )
            if mode == "shadow":
                execution = await self._analyzer().run_thin_min(
                    unit={
                        "unit_id": unit.unit_id,
                        "user_text": unit.user_text,
                    },
                    turn_context_digest=digest_value({"unit_id": unit.unit_id}),
                )
            executions.append(execution)
            output: V7ThinExtractionRawOutput = execution.output
            actual_by_key = {_thin_key(item): item for item in output.claims}
            expected_by_key = {_thin_key(item): item for item in unit.expected_claims}
            expected_count += len(expected_by_key)
            actual_count += len(actual_by_key)
            claim_results: list[dict[str, Any]] = []
            for key, expected in expected_by_key.items():
                actual = actual_by_key.get(key)
                if actual is None:
                    failures.append(f"{unit.unit_id}:{expected.claim_id}:missing_claim")
                    attributions.append(V7AttributionCode.QUOTE_EXTRACTION_ERROR.value)
                    claim_results.append(
                        {
                            "claim_id": expected.claim_id,
                            "semantic_match": False,
                            "quote_valid": False,
                            "passed": False,
                        }
                    )
                    continue
                matched_count += 1
                evidence_check = check_source_quote(
                    user_text=unit.user_text,
                    quote=actual.evidence_quote,
                )
                target_check = check_source_quote(
                    user_text=unit.user_text,
                    quote=actual.target_quote,
                    evidence_quote=actual.evidence_quote,
                )
                subject_check = check_source_quote(
                    user_text=unit.user_text,
                    quote=actual.subject_evidence_quote,
                    evidence_quote=actual.evidence_quote,
                )
                auxiliary_checks = {
                    name: (
                        not actual_quote
                        if not expected_quote
                        else check_source_quote(
                            user_text=unit.user_text,
                            quote=actual_quote,
                            evidence_quote=actual.evidence_quote,
                        ).valid
                        and _normalized(actual_quote) == _normalized(expected_quote)
                    )
                    for name, expected_quote, actual_quote in (
                        ("temporal", expected.temporal_quote, actual.temporal_quote),
                        (
                            "measurement",
                            expected.measurement_quote,
                            actual.measurement_quote,
                        ),
                        ("relation", expected.relation_quote, actual.relation_quote),
                    )
                }
                relation_available = not expected.relation_quote_required or bool(
                    actual.relation_quote
                )
                quote_valid = (
                    evidence_check.valid
                    and target_check.valid
                    and subject_check.valid
                    and all(auxiliary_checks.values())
                )
                passed = quote_valid and relation_available
                if not passed:
                    failures.append(
                        f"{unit.unit_id}:{expected.claim_id}:quote_or_relation"
                    )
                    if not target_check.contained_in_evidence:
                        attributions.append(
                            V7AttributionCode.TARGET_CONTAINMENT_ERROR.value
                        )
                    elif not relation_available:
                        attributions.append(
                            V7AttributionCode.RELATION_QUOTE_MISSING.value
                        )
                    else:
                        attributions.append(
                            V7AttributionCode.QUOTE_EXTRACTION_ERROR.value
                        )
                else:
                    quote_valid_count += 1
                claim_results.append(
                    {
                        "claim_id": expected.claim_id,
                        "semantic_match": True,
                        "quote_valid": quote_valid,
                        "relation_quote_available": relation_available,
                        "passed": passed,
                    }
                )
            unexpected = set(actual_by_key) - set(expected_by_key)
            for key in unexpected:
                failures.append(f"{unit.unit_id}:{key}:unexpected_claim")
                attributions.append(V7AttributionCode.QUOTE_EXTRACTION_ERROR.value)
            actual_summaries = [
                {
                    "target_quote": item.target_quote,
                    "user_statement_type": item.user_statement_type.value,
                    "coarse_type": item.coarse_type.value,
                    "evidence_quote": item.evidence_quote,
                    "temporal_quote": item.temporal_quote,
                    "measurement_quote": item.measurement_quote,
                    "relation_quote": item.relation_quote,
                }
                for item in output.claims
            ]
            unit_reports.append(
                {
                    "unit_id": unit.unit_id,
                    "claim_results": claim_results,
                    "expected_claim_count": len(expected_by_key),
                    "actual_claim_count": len(actual_by_key),
                    "actual_claims": actual_summaries,
                    "passed": bool(claim_results)
                    and all(item["passed"] for item in claim_results),
                }
            )
        execution_summary = _combine_executions(executions)
        return _experiment_report(
            experiment_id=V7ExperimentId.THIN_LIVE_MIN,
            architecture_question=(
                "Does removing relation class improve minimal live thin extraction?"
            ),
            unit_results=unit_reports,
            failures=failures,
            attributions=attributions,
            execution=execution_summary,
            extra_metrics={
                "unit_count": len(units),
                "claim_recall": _rate(matched_count, expected_count),
                "claim_precision": _rate(matched_count, actual_count),
                "quote_valid_rate": _rate(quote_valid_count, expected_count),
            },
        )

    async def _run_relation(
        self,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        units = self.document.relation_units
        if not units:
            raise ValueError("v7_relation_units_missing")
        output = V7RelationRawOutput(
            results=[
                V7RelationClassificationRaw(
                    unit_id=item.unit_id,
                    relation=item.expected_relation,
                    confidence=1.0,
                )
                for item in units
            ]
        )
        execution = V7ModelExecution(
            output=output,
            attempt_count=0,
            first_attempt_status="ideal",
        )
        if mode == "shadow":
            execution = await self._analyzer().run_relation(
                units=[
                    {
                        "unit_id": item.unit_id,
                        "target_quote": item.target_quote,
                        "relation_quote": item.relation_quote,
                    }
                    for item in units
                ],
                turn_context_digest=digest_value({"units": len(units)}),
            )
        expected = {item.unit_id: item for item in units}
        actual = {item.unit_id: item for item in _result_list(execution.output)}
        unit_results: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        confusion: Counter[tuple[str, str]] = Counter()
        correct = 0
        for unit_id in sorted(expected):
            item = expected[unit_id]
            raw = actual.get(unit_id)
            if raw is None:
                failures.append(f"{unit_id}:missing")
                attributions.append(
                    V7AttributionCode.RELATION_CLASSIFICATION_ERROR.value
                )
                continue
            is_correct = raw.relation == item.expected_relation
            correct += int(is_correct)
            confusion[(item.expected_relation.value, raw.relation.value)] += 1
            if not is_correct:
                failures.append(f"{unit_id}:relation_mismatch")
                attributions.append(
                    V7AttributionCode.RELATION_CLASSIFICATION_ERROR.value
                )
            unit_results.append(
                {
                    "unit_id": unit_id,
                    "expected": item.expected_relation.value,
                    "actual": raw.relation.value,
                    "passed": is_correct,
                }
            )
        for unit_id in sorted(set(actual) - set(expected)):
            failures.append(f"{unit_id}:unexpected")
            attributions.append(V7AttributionCode.RELATION_CLASSIFICATION_ERROR.value)
        return _experiment_report(
            experiment_id=V7ExperimentId.RELATION_GOLDEN,
            architecture_question=(
                "Can an independent classifier separate absolute/no-change/change/unclear?"
            ),
            unit_results=unit_results,
            failures=failures,
            attributions=attributions,
            execution=execution,
            extra_metrics={
                "unit_count": len(units),
                "relation_accuracy": _rate(correct, len(units)),
                "confusion": {
                    f"{expected}->{actual}": count
                    for (expected, actual), count in sorted(confusion.items())
                },
            },
        )

    def _run_canonical(self) -> dict[str, Any]:
        units = self.document.canonical_units
        if not units:
            raise ValueError("v7_canonical_units_missing")
        unit_results: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        recalled = 0
        filtered_miss = 0
        no_candidate = 0
        for unit in units:
            candidate_set = self.candidate_retriever.recall(
                claim_id=unit.unit_id,
                target_quote=unit.target_quote,
                coarse_type=unit.coarse_type,
            )
            candidate_ids = {item.canonical_id for item in candidate_set.candidates}
            expected_present = bool(set(unit.expected_canonical_ids) & candidate_ids)
            expected_filtered = any(
                item.canonical_id in unit.expected_canonical_ids
                for item in candidate_set.filtered_candidates
            )
            recalled += int(expected_present)
            filtered_miss += int(expected_filtered and not expected_present)
            no_candidate += int(not candidate_set.candidates)
            passed = expected_present
            if not passed:
                failures.append(f"{unit.unit_id}:expected_candidate_not_recalled")
                if expected_filtered:
                    attributions.append(V7AttributionCode.CANONICAL_FILTER_ERROR.value)
                else:
                    attributions.append(V7AttributionCode.CANONICAL_RECALL_MISS.value)
            unit_results.append(
                {
                    "unit_id": unit.unit_id,
                    "target_quote": unit.target_quote,
                    "coarse_type": unit.coarse_type,
                    "expected_canonical_ids": unit.expected_canonical_ids,
                    "candidate_ids": sorted(candidate_ids),
                    "filtered_candidates": [
                        {
                            "canonical_id": item.canonical_id,
                            "score": item.score,
                            "surface_form": item.surface_form,
                        }
                        for item in candidate_set.filtered_candidates
                    ],
                    "top_candidates": [
                        {
                            "candidate_id": item.candidate_id,
                            "canonical_id": item.canonical_id,
                            "score": item.score,
                            "surface_form": item.surface_form,
                        }
                        for item in candidate_set.candidates[:8]
                    ],
                    "passed": passed,
                }
            )
        execution = V7ModelExecution(
            output=None,
            attempt_count=0,
            first_attempt_status="deterministic_recall",
            latency_ms=0,
        )
        return _experiment_report(
            experiment_id=V7ExperimentId.CAN_GOLDEN_DIRECT,
            architecture_question=(
                "Can golden target quotes recall expected canonical candidates?"
            ),
            unit_results=unit_results,
            failures=failures,
            attributions=attributions,
            execution=execution,
            extra_metrics={
                "unit_count": len(units),
                "candidate_recall": _rate(recalled, len(units)),
                "filtered_miss_count": filtered_miss,
                "no_candidate_count": no_candidate,
                "embedding_call_count": self.embedding_client.call_count,
            },
        )

    async def _run_participant(
        self,
        *,
        mode: Literal["ideal", "shadow"],
    ) -> dict[str, Any]:
        units = self.document.participant_units
        if not units:
            raise ValueError("v7_participant_units_missing")
        output = V7ParticipantRawOutput(
            results=[
                V7ParticipantSelectionRaw(
                    unit_id=item.unit_id,
                    action_agent_selected_candidate=item.expected_action_agent,
                    action_recipient_selected_candidate=item.expected_action_recipient,
                    object_mention=item.expected_object_mention,
                    resolution_status="resolved",
                    confidence=1.0,
                )
                for item in units
            ]
        )
        execution = V7ModelExecution(
            output=output,
            attempt_count=0,
            first_attempt_status="ideal",
        )
        if mode == "shadow":
            execution = await self._analyzer().run_participant(
                units=[
                    {
                        "unit_id": item.unit_id,
                        "user_text": item.user_text,
                        "entity_candidates": item.entity_candidates,
                    }
                    for item in units
                ],
                turn_context_digest=digest_value({"units": len(units)}),
            )
        expected = {item.unit_id: item for item in units}
        actual = {item.unit_id: item for item in _result_list(execution.output)}
        unit_results: list[dict[str, Any]] = []
        failures: list[str] = []
        attributions: list[str] = []
        correct_agents = 0
        correct_recipients = 0
        invented_entities = 0
        cross_assignments = 0
        for unit_id in sorted(expected):
            item = expected[unit_id]
            raw = actual.get(unit_id)
            if raw is None:
                failures.append(f"{unit_id}:missing")
                attributions.append(V7AttributionCode.PARTICIPANT_ROLE_ERROR.value)
                continue
            candidate_ids = {
                str(candidate.get("reference_id"))
                for candidate in item.entity_candidates
            }
            selected = {
                raw.action_agent_selected_candidate,
                raw.action_recipient_selected_candidate,
            }
            has_invented = any(
                value is not None and value not in candidate_ids for value in selected
            )
            invented_entities += int(has_invented)
            agent_correct = (
                raw.action_agent_selected_candidate == item.expected_action_agent
            )
            recipient_correct = (
                raw.action_recipient_selected_candidate
                == item.expected_action_recipient
            )
            object_correct = (
                check_source_quote(
                    user_text=item.user_text,
                    quote=raw.object_mention,
                ).valid
                and raw.object_mention == item.expected_object_mention
            )
            correct_agents += int(agent_correct)
            correct_recipients += int(recipient_correct)
            role_correct = agent_correct and recipient_correct and object_correct
            wrong_but_trusted = (
                not role_correct
                and not has_invented
                and raw.resolution_status == "resolved"
            )
            cross_assignments += int(wrong_but_trusted)
            passed = (
                role_correct
                and not has_invented
                and raw.resolution_status == "resolved"
            )
            if not passed:
                failures.append(f"{unit_id}:participant")
                if has_invented:
                    attributions.append(
                        V7AttributionCode.PARTICIPANT_CANDIDATE_ERROR.value
                    )
                else:
                    attributions.append(V7AttributionCode.PARTICIPANT_ROLE_ERROR.value)
            unit_results.append(
                {
                    "unit_id": unit_id,
                    "expected_action_agent": item.expected_action_agent,
                    "actual_action_agent": raw.action_agent_selected_candidate,
                    "expected_action_recipient": item.expected_action_recipient,
                    "actual_action_recipient": raw.action_recipient_selected_candidate,
                    "expected_object_mention": item.expected_object_mention,
                    "actual_object_mention": raw.object_mention,
                    "resolution_status": raw.resolution_status,
                    "invented_entity": has_invented,
                    "passed": passed,
                }
            )
        for unit_id in sorted(set(actual) - set(expected)):
            failures.append(f"{unit_id}:unexpected")
            attributions.append(V7AttributionCode.PARTICIPANT_ROLE_ERROR.value)
        return _experiment_report(
            experiment_id=V7ExperimentId.PART_GOLDEN,
            architecture_question=(
                "Can action participants be selected only from trusted candidates?"
            ),
            unit_results=unit_results,
            failures=failures,
            attributions=attributions,
            execution=execution,
            extra_metrics={
                "unit_count": len(units),
                "action_agent_accuracy": _rate(correct_agents, len(units)),
                "action_recipient_accuracy": _rate(correct_recipients, len(units)),
                "invented_entity_count": invented_entities,
                "cross_claim_assignment_count": cross_assignments,
            },
        )


class _UnsafeDirectEmbeddingAdapter:
    """Fallback adapter for supplied retrievers without an exposed client."""

    def __init__(self, retriever: V6CandidateRetriever) -> None:
        self.retriever = retriever

    @property
    def available(self) -> bool:
        return self.retriever.embeddings.available

    def embed(self, text: str) -> list[float]:
        return self.retriever.embeddings.embed(text)


def _result_list(output: Any) -> list[Any]:
    return list(output.results)


def _adapter_failure_report(
    experiment_id: V7ExperimentId,
    error: V7MicrobenchError,
) -> dict[str, Any]:
    attribution_by_experiment = {
        V7ExperimentId.INTENT_ANSWER_NOW: V7AttributionCode.INTENT_MODEL_ERROR,
        V7ExperimentId.INTENT_FACT_DETECT: V7AttributionCode.INTENT_MODEL_ERROR,
        V7ExperimentId.INTENT_QUESTION: V7AttributionCode.INTENT_MODEL_ERROR,
        V7ExperimentId.QUOTE_GOLDEN_SELECT: V7AttributionCode.QUOTE_SELECTOR_ERROR,
        V7ExperimentId.THIN_LIVE_MIN: V7AttributionCode.QUOTE_EXTRACTION_ERROR,
        V7ExperimentId.RELATION_GOLDEN: V7AttributionCode.RELATION_CLASSIFICATION_ERROR,
        V7ExperimentId.PART_GOLDEN: V7AttributionCode.PARTICIPANT_ROLE_ERROR,
    }
    attribution = attribution_by_experiment[experiment_id]
    return _experiment_report(
        experiment_id=experiment_id,
        architecture_question="Did the narrow model adapter satisfy its contract?",
        unit_results=[],
        failures=[f"adapter:{error.first_attempt_status}:{error.experiment_id}"],
        attributions=[attribution.value],
        execution=V7ModelExecution(
            output=None,
            attempt_count=error.attempt_count,
            first_attempt_status=error.first_attempt_status,
            first_attempt_error=error.first_attempt_error[:500],
        ),
        extra_metrics={
            "adapter_failure": True,
            "first_attempt_status": error.first_attempt_status,
        },
    )


def _unit_maps(
    results: list[Any],
    expected_ids: set[str],
) -> tuple[set[str], set[str], dict[str, Any]]:
    actual = {item.unit_id: item for item in results}
    return expected_ids - set(actual), set(actual) - expected_ids, actual


def _intent_metrics(
    units: list[V7IntentUnit],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    by_unit = {item["unit_id"]: item for item in results}
    total = len(units)
    correct = sum(
        by_unit[item.unit_id]["expected_detected"]
        == by_unit[item.unit_id]["actual_detected"]
        for item in units
        if item.unit_id in by_unit
    )
    positive = [item for item in units if item.expected_detected]
    negative = [item for item in units if not item.expected_detected]
    true_positive = sum(
        bool(by_unit[item.unit_id]["actual_detected"])
        for item in positive
        if item.unit_id in by_unit
    )
    false_positive_count = sum(
        bool(by_unit[item.unit_id]["actual_detected"])
        for item in negative
        if item.unit_id in by_unit
    )
    quote_valid = sum(
        bool(by_unit[item.unit_id]["quote_valid"])
        for item in units
        if item.unit_id in by_unit
    )
    long_units = [item for item in units if "long_input" in item.tags]
    long_correct = sum(
        bool(by_unit[item.unit_id]["passed"])
        for item in long_units
        if item.unit_id in by_unit
    )
    return {
        "unit_count": total,
        "accuracy": _rate(correct, total),
        "recall": _rate(true_positive, len(positive)),
        "false_positive_count": false_positive_count,
        "quote_valid_rate": _rate(quote_valid, total),
        "long_input_pass_rate": (
            _rate(long_correct, len(long_units)) if long_units else None
        ),
    }


def _expected_quote_check(
    *,
    user_text: str,
    evidence_quote: str,
    expected: str,
    actual: str,
) -> bool:
    if not expected:
        return actual == ""
    check = check_source_quote(
        user_text=user_text,
        quote=actual,
        evidence_quote=evidence_quote,
    )
    return check.valid and check.normalized_quote == _normalized(expected)


def _thin_key(
    claim: V7ThinUserClaimRaw | V7ExpectedThinClaim,
) -> tuple[str, str, str]:
    statement = claim.user_statement_type
    coarse = claim.coarse_type
    return (
        _normalized(claim.target_quote),
        statement.value if isinstance(statement, V7UserStatementType) else statement,
        coarse.value if isinstance(coarse, V7CoarseType) else coarse,
    )


def _normalized(value: str) -> str:
    from .v6_quote_governance import normalize_quote_text

    return normalize_quote_text(value)


def _experiment_report(
    *,
    experiment_id: V7ExperimentId,
    architecture_question: str,
    unit_results: list[dict[str, Any]],
    failures: list[str],
    attributions: list[str],
    execution: V7ModelExecution,
    extra_metrics: dict[str, Any],
) -> dict[str, Any]:
    all_units_passed = bool(unit_results) and all(
        bool(item.get("passed", False)) for item in unit_results
    )
    status = "passed" if all_units_passed and not failures else "failed"
    if not unit_results:
        status = "failed"
        failures.append("no_evaluable_units")
    return {
        "schema_version": "v7-microbench-report-1",
        "experiment_id": experiment_id.value,
        "architecture_question": architecture_question,
        "status": status,
        "metrics": extra_metrics,
        "unit_results": unit_results,
        "failures": failures,
        "attribution_distribution": dict(Counter(attributions)),
        "execution": _execution_report(execution),
    }


def _execution_report(execution: V7ModelExecution) -> dict[str, Any]:
    return {
        "attempt_count": execution.attempt_count,
        "first_attempt_status": execution.first_attempt_status,
        "first_attempt_error": execution.first_attempt_error,
        "cache_hit": execution.cache_hit,
        "latency_ms": execution.latency_ms,
        "model_call_count": 0 if execution.cache_hit else execution.attempt_count,
    }


def _combine_executions(executions: list[V7ModelExecution]) -> V7ModelExecution:
    return V7ModelExecution(
        output=None,
        attempt_count=sum(item.attempt_count for item in executions),
        first_attempt_status=",".join(item.first_attempt_status for item in executions),
        first_attempt_error=";".join(
            item.first_attempt_error for item in executions if item.first_attempt_error
        )[:500],
        cache_hit=bool(executions) and all(item.cache_hit for item in executions),
        latency_ms=sum(item.latency_ms for item in executions),
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def write_v7_experiment_report(
    *,
    output_dir: Path,
    reports: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"input-preprocessing-v7-{uuid4().hex[:12]}.json"
    passed = sum(item.get("status") == "passed" for item in reports)
    payload = {
        "schema_version": "v7-attribution-report-1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "metadata": metadata,
        "summary": {
            "suite": "core",
            "experiment_count": len(reports),
            "passed": passed,
            "failed": len(reports) - passed,
            "total_model_call_count": sum(
                item.get("execution", {}).get("model_call_count", 0) for item in reports
            ),
            "attribution_distribution": dict(
                Counter(
                    code
                    for item in reports
                    for code in item.get("attribution_distribution", {})
                )
            ),
        },
        "experiments": reports,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(
            "tests/fixtures/input_preprocessing/seventh_round_attribution_matrix.json"
        ),
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=Path(
            "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
        ),
    )
    parser.add_argument("--mode", choices=("ideal", "shadow"), default="ideal")
    parser.add_argument(
        "--phase", choices=("exploratory", "confirmatory"), default="exploratory"
    )
    parser.add_argument("--experiment", action="append", default=[])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".data/evaluations/input-preprocessing-v7-core-microbench"),
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=Path(".data/cache/input-preprocessing-v7/run-cache.json"),
    )
    parser.add_argument("--no-cache", action="store_true")
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    document = load_v7_experiment_document(args.matrix)
    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    cache = None if args.no_cache else V7RunCache(args.cache_path)
    analyzer: V7MicroAnalyzer | None = None
    analyzer_factory: Callable[[], V7MicroAnalyzer] | None = None
    candidate_retriever: V6CandidateRetriever | None = None
    if args.mode == "shadow":
        from vet_agent.runtime import QwenClient, QwenEmbeddingClient

        settings = make_runtime_settings()
        model = os.getenv("INPUT_PREPROCESSING_V7_MODEL", "qwen-plus")
        counting_embeddings = CountingEmbeddingClient(QwenEmbeddingClient(settings))
        candidate_retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=counting_embeddings,
        )

        def fresh_analyzer() -> V7MicroAnalyzer:
            return V7MicroAnalyzer(
                qwen=QwenClient(settings),
                model=model,
                cache=cache,
            )

        analyzer_factory = fresh_analyzer
    runner = V7AttributionRunner(
        document=document,
        vocabulary=vocabulary,
        analyzer=analyzer,
        analyzer_factory=analyzer_factory,
        candidate_retriever=candidate_retriever,
    )
    reports = await runner.run(
        mode=args.mode,
        only_experiment_ids=set(args.experiment) if args.experiment else None,
    )
    path = write_v7_experiment_report(
        output_dir=args.output_dir,
        reports=reports,
        metadata={
            "phase": args.phase,
            "suite": "core",
            "model": os.getenv("INPUT_PREPROCESSING_V7_MODEL", "qwen-plus"),
            "prompt_version": V7_PROMPT_VERSION,
            "schema_version": "v7-attribution-1",
            "gate_version": V7_GATE_VERSION,
            "vocabulary_version": vocabulary.version,
            "fixture_path": str(args.matrix),
            "fixture_sha256": hashlib.sha256(args.matrix.read_bytes()).hexdigest(),
            "cache_path": None if args.no_cache else str(args.cache_path),
            "cache_hit_count": 0 if cache is None else cache.hit_count,
            "cache_miss_count": 0 if cache is None else cache.miss_count,
            "analyzer_isolation": "per-experiment-fresh-qwen-client-shared-run-cache",
        },
    )
    failed = [item for item in reports if item.get("status") != "passed"]
    print(f"report={path}")
    print(
        f"experiments={len(reports)} "
        f"passed={len(reports) - len(failed)} failed={len(failed)}"
    )
    for item in failed:
        print(
            f"FAILED experiment={item['experiment_id']} "
            f"attributions={','.join(item.get('attribution_distribution', {}))} "
            f"failures={','.join(item.get('failures', [])[:5])}"
        )
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
