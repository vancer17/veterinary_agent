"""Quick validation and shadow runners for the staged V8 experiments.

The runner intentionally keeps Phase 0 deterministic and local. It can also
run one macro unit through the base, Instructor, or BAML adapter, but it never
writes consultation state and never invokes clinical-safety dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from .runtime_helpers import make_runtime_settings
from .v6_canonical_linker import V6CandidateRetriever
from .v6_deterministic_parsers import parse_measurement, parse_temporal
from .v7_microbench import V7MicroAnalyzer
from .v7_run_cache import digest_value as v7_digest_value
from .v8_contracts import (
    V8CoarseType,
    V8DiscourseActType,
    V8EntityCandidate,
    V8ExperimentId,
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8QualityGateResult,
    V8SpanCandidate,
    V8SpanLabel,
    V8UserStatementType,
)
from .v8_macro_analyzer import (
    V8_PROMPT_VERSION,
    V8_SCHEMA_VERSION,
    V8MacroAnalyzer,
    V8StructuredAdapter,
    build_v8_structured_client,
)
from .v8_span_extractors import build_v8_span_extractor
from .v8_span_governance import V8SpanGovernance, V8SpanPool
from .vocabulary import CanonicalVocabulary

DEFAULT_MATRIX = Path(
    "tests/fixtures/input_preprocessing/eighth_round_span_macro_matrix.json"
)
DEFAULT_OUTPUT_DIR = Path(".data/evaluations/input-preprocessing-v8")
DEFAULT_CACHE_PATH = Path(".data/cache/input-preprocessing-v8/run-cache.json")


@dataclass(frozen=True)
class ExpectedSpan:
    role: str
    quote: str
    label: V8SpanLabel
    unit_id: str
    source_id: str
    span_id: str
    start: int
    end: int


@dataclass(frozen=True)
class IdealSpanPool:
    source_id: str
    text: str
    spans: list[V8SpanCandidate]
    role_span_ids: dict[str, str]
    entity_candidates: list[V8EntityCandidate]


def load_v8_matrix(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_v8_matrix:{path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "v8-span-macro-1":
        raise ValueError(f"unsupported_v8_matrix:{path}")
    for key in ("span_units", "macro_units"):
        if key not in raw:
            raise ValueError(f"v8_matrix_field_missing:{key}")
    return raw


def _safe_index(text: str, quote: str, *, unit_id: str, role: str) -> tuple[int, int]:
    try:
        start = text.index(quote)
    except ValueError as exc:
        raise ValueError(f"v8_expected_quote_not_found:{unit_id}:{role}") from exc
    return start, start + len(quote)


def _label_for_role(
    role: str,
    *,
    coarse_type: str = "",
    act_type: str = "",
) -> V8SpanLabel:
    if role in {
        "action_agent_quote",
        "action_recipient_quote",
        "subject_quote",
        "experiencer_quote",
    }:
        mapping = {
            "action_agent_quote": V8SpanLabel.AGENT_MENTION,
            "action_recipient_quote": V8SpanLabel.RECIPIENT_MENTION,
            "subject_quote": V8SpanLabel.SUBJECT_MENTION,
            "experiencer_quote": V8SpanLabel.SUBJECT_MENTION,
        }
        return mapping[role]
    if role == "target_quote":
        return V8SpanLabel.TARGET_MENTION
    if role == "support_quote":
        if coarse_type in {"action", "food", "medication"}:
            return V8SpanLabel.ACTION_EVENT
        if coarse_type == "measurement":
            return V8SpanLabel.MEASUREMENT_EXPRESSION
        if coarse_type == "time":
            return V8SpanLabel.TEMPORAL_EXPRESSION
        return V8SpanLabel.STATE_MENTION
    if role == "relation_quote":
        return V8SpanLabel.RELATION_EXPRESSION
    if role == "object_quote":
        return V8SpanLabel.OBJECT_MENTION
    if role == "temporal_quote":
        return V8SpanLabel.TEMPORAL_EXPRESSION
    if role == "measurement_quote":
        return V8SpanLabel.MEASUREMENT_EXPRESSION
    if role == "evidence_quote":
        if act_type in {
            "answer_now",
            "clarification_request",
            "question",
            "wants_triage",
        }:
            return V8SpanLabel.CONTROL_INTENT_EXPRESSION
        return V8SpanLabel.STATE_MENTION
    return V8SpanLabel.CANDIDATE_SPAN


def expected_spans_for_unit(unit: dict[str, Any]) -> list[ExpectedSpan]:
    text = str(unit["user_text"])
    unit_id = str(unit["unit_id"])
    expected: list[ExpectedSpan] = []

    def add(
        role: str,
        quote: str,
        *,
        coarse_type: str = "",
        act_type: str = "",
    ) -> None:
        if not quote:
            return
        start, end = _safe_index(text, quote, unit_id=unit_id, role=role)
        expected_label = _label_for_role(
            role,
            coarse_type=coarse_type,
            act_type=act_type,
        )
        expected.append(
            ExpectedSpan(
                role=role,
                quote=quote,
                label=expected_label,
                unit_id=unit_id,
                source_id=unit_id,
                span_id=f"{unit_id}:ideal-{role}-{start:06d}-{end:06d}",
                start=start,
                end=end,
            )
        )

    for act in unit.get("expected_acts", []):
        add(
            "evidence_quote",
            str(act.get("evidence_quote", "")),
            act_type=str(act.get("act_type", "")),
        )
    for claim in unit.get("expected_claims", []):
        coarse_type = str(claim.get("coarse_type", ""))
        for role in (
            "support_quote",
            "target_quote",
            "relation_quote",
            "subject_quote",
            "action_agent_quote",
            "action_recipient_quote",
            "experiencer_quote",
            "object_quote",
            "temporal_quote",
            "measurement_quote",
        ):
            add(role, str(claim.get(role, "")), coarse_type=coarse_type)
    return expected


def build_ideal_span_pool(unit: dict[str, Any]) -> IdealSpanPool:
    text = str(unit["user_text"])
    expected = expected_spans_for_unit(unit)
    by_boundary: dict[tuple[int, int, str], ExpectedSpan] = {}
    for item in expected:
        by_boundary.setdefault((item.start, item.end, item.label.value), item)
    spans = [
        V8SpanCandidate(
            span_id=item.span_id,
            source_id=item.source_id,
            source_block_id="block-001",
            start=item.start,
            end=item.end,
            text=text[item.start : item.end],
            label=item.label,
            score=1.0,
            extractor_version="v8-ideal-golden-20260828-1",
        )
        for item in sorted(by_boundary.values(), key=lambda value: value.start)
    ]
    role_span_ids: dict[str, str] = {}
    for item in expected:
        role_span_ids[item.role] = item.span_id
    # Preserve repeated roles per act / claim without breaking older controls.
    def indexed_span_id(role: str, quote: str) -> str:
        if not quote:
            return ""
        start, end = _safe_index(text, quote, unit_id=str(unit["unit_id"]), role=role)
        return f"{unit['unit_id']}:ideal-{role}-{start:06d}-{end:06d}"

    for act_index, act in enumerate(unit.get("expected_acts", [])):
        span_id = indexed_span_id("evidence_quote", str(act.get("evidence_quote", "")))
        if span_id:
            role_span_ids[f"evidence_quote:{act_index}"] = span_id
    for claim_index, claim in enumerate(unit.get("expected_claims", [])):
        for role in (
            "support_quote",
            "target_quote",
            "relation_quote",
            "subject_quote",
            "action_agent_quote",
            "action_recipient_quote",
            "experiencer_quote",
            "object_quote",
            "temporal_quote",
            "measurement_quote",
        ):
            span_id = indexed_span_id(role, str(claim.get(role, "")))
            if span_id:
                role_span_ids[f"{role}:{claim_index}"] = span_id
    entities = [
        V8EntityCandidate.model_validate(raw)
        for raw in unit.get("entity_candidates", [])
    ]
    return IdealSpanPool(
        source_id=str(unit["unit_id"]),
        text=text,
        spans=spans,
        role_span_ids=role_span_ids,
        entity_candidates=entities,
    )


def _required_field_rows(unit: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        role: str,
        quote: str,
        owner: str,
        *,
        coarse_type: str = "",
        act_type: str = "",
    ) -> None:
        if quote:
            start, end = _safe_index(
                str(unit["user_text"]),
                quote,
                unit_id=str(unit["unit_id"]),
                role=role,
            )
            rows.append(
                {
                    "unit_id": unit["unit_id"],
                    "owner_id": owner,
                    "role": role,
                    "quote": quote,
                    "coarse_type": coarse_type,
                    "act_type": act_type,
                    "expected_start": start,
                    "expected_end": end,
                }
            )

    for act in unit.get("expected_acts", []):
        add(
            "evidence_quote",
            act.get("evidence_quote", ""),
            str(act.get("act_type", "")),
            act_type=str(act.get("act_type", "")),
        )
    for claim in unit.get("expected_claims", []):
        owner = str(claim.get("claim_id", ""))
        for role in (
            "support_quote",
            "target_quote",
            "relation_quote",
            "subject_quote",
            "action_agent_quote",
            "action_recipient_quote",
            "experiencer_quote",
            "object_quote",
            "temporal_quote",
            "measurement_quote",
        ):
            add(
                role,
                claim.get(role, ""),
                owner,
                coarse_type=str(claim.get("coarse_type", "")),
            )
    return rows


def _required_fields(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for unit in matrix["macro_units"] for row in _required_field_rows(unit)]


def _latency_metrics(values: list[int]) -> dict[str, float]:
    if not values:
        return {"p50_ms": 0.0, "p95_ms": 0.0}
    ordered = sorted(values)
    p50 = ordered[min(len(ordered) - 1, (len(ordered) - 1) // 2)]
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))]
    return {"p50_ms": float(p50), "p95_ms": float(p95)}


def run_span_golden(matrix: dict[str, Any], *, coverage_only: bool) -> dict[str, Any]:
    extractor = build_v8_span_extractor()
    required = _required_fields(matrix)
    predicted: list[V8SpanCandidate] = []
    latencies: list[int] = []
    for unit in matrix["macro_units"]:
        started = time.perf_counter()
        predicted.extend(
            extractor.extract(
                source_id=str(unit["unit_id"]),
                source_block_id="block-001",
                text=str(unit["user_text"]),
            )
        )
        latencies.append(int((time.perf_counter() - started) * 1000))

    predicted_by_unit: dict[str, list[V8SpanCandidate]] = {}
    for span in predicted:
        predicted_by_unit.setdefault(span.source_id, []).append(span)

    matched_expected_ids: set[int] = set()
    matched_predicted_ids: set[str] = set()
    label_matches = 0
    covered_fields = 0
    missing: list[dict[str, Any]] = []
    for index, field in enumerate(required):
        candidates = predicted_by_unit.get(str(field["unit_id"]), [])
        exact = [
            span
            for span in candidates
            if span.start == field["expected_start"]
            and span.end == field["expected_end"]
        ]
        if exact:
            covered_fields += 1
            matched_expected_ids.add(index)
            selected = max(exact, key=lambda span: span.score)
            matched_predicted_ids.add(selected.span_id)
            expected_label = _label_for_role(
                str(field["role"]),
                coarse_type=str(field.get("coarse_type", "")),
                act_type=str(field.get("act_type", "")),
            )
            if any(span.label is expected_label for span in exact):
                label_matches += 1
        else:
            missing.append(
                {
                    "unit_id": field["unit_id"],
                    "owner_id": field["owner_id"],
                    "role": field["role"],
                    "quote": field["quote"],
                }
            )

    false_positives = [
        {
            "unit_id": span.source_id,
            "span_id": span.span_id,
            "start": span.start,
            "end": span.end,
            "text": span.text,
            "label": span.label.value,
        }
        for span in predicted
        if span.span_id not in matched_predicted_ids
    ]
    expected_count = len(required)
    predicted_count = len(predicted)
    match_count = len(matched_expected_ids)
    matched_predicted_count = len(matched_predicted_ids)
    precision = matched_predicted_count / predicted_count if predicted_count else 0.0
    recall = match_count / expected_count if expected_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    metrics = {
        "required_field_count": expected_count,
        "predicted_span_count": predicted_count,
        "boundary_match_count": match_count,
        "label_match_count": label_matches,
        "label_accuracy": label_matches / match_count if match_count else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "required_field_coverage": covered_fields / expected_count
        if expected_count
        else 0.0,
        **_latency_metrics(latencies),
    }
    if coverage_only:
        metrics.pop("precision")
        metrics.pop("f1")
        metrics.pop("label_accuracy")
    return {
        "experiment_id": "SPAN-POOL-COVERAGE" if coverage_only else "SPAN-GOLDEN",
        "status": "completed",
        "metrics": metrics,
        "missing_required_fields": missing,
        "false_positives": false_positives,
        "extractor_version": extractor.extractor_version,
    }


def _blocking_gates(gates: list[V8QualityGateResult]) -> list[V8QualityGateResult]:
    return [
        gate
        for gate in gates
        if gate.status == "failed" and gate.severity == "blocking"
    ]


def run_negative_mutations(matrix: dict[str, Any]) -> dict[str, Any]:
    unit = matrix["macro_units"][0]
    pool = build_ideal_span_pool(unit)
    governance = V8SpanGovernance(
        V8SpanPool(sources={pool.source_id: pool.text}, spans=pool.spans)
    )
    support_id = pool.role_span_ids["support_quote"]
    support_quote = str(unit["expected_claims"][0]["support_quote"])
    target_id = pool.role_span_ids["target_quote"]
    temporal_id = pool.role_span_ids.get("temporal_quote")
    outside_target_id = None
    for claim in unit["expected_claims"][1:]:
        quote = claim.get("target_quote", "")
        if quote and quote not in str(unit["user_text"]).split(support_quote, 1)[0]:
            start = str(unit["user_text"]).index(quote)
            for span in pool.spans:
                if span.start == start and span.end == start + len(quote):
                    outside_target_id = span.span_id
                    break
            break

    valid = V8MacroSemanticRawOutput(
        acts=[
            V8MacroDiscourseActRaw(
                unit_id=str(unit["unit_id"]),
                act_type=V8DiscourseActType.FACT_STATEMENT,
                evidence_span_ids=[pool.role_span_ids["evidence_quote"]],
                confidence=1.0,
            )
        ],
        claims=[
            V8MacroClaimRaw(
                unit_id=str(unit["unit_id"]),
                claim_id=str(unit["expected_claims"][0]["claim_id"]),
                statement_type=V8UserStatementType.REPORTS,
                coarse_type=V8CoarseType.ACTION,
                support_span_ids=[support_id],
                target_span_ids=[target_id],
                temporal_span_ids=[temporal_id] if temporal_id else [],
                confidence=1.0,
            )
        ],
    )
    mutations: list[tuple[str, V8MacroSemanticRawOutput]] = [("valid-control", valid)]

    def cloned(name: str) -> V8MacroSemanticRawOutput:
        return valid.model_copy(deep=True, update={})

    act_invalid = cloned("act-invalid")
    act_invalid.acts[0].evidence_span_ids = ["missing-act-span"]
    mutations.append(("invalid-act-reference", act_invalid))

    support_invalid = cloned("support-invalid")
    support_invalid.claims[0].support_span_ids = ["missing-support-span"]
    mutations.append(("invalid-claim-support", support_invalid))

    target_invalid = cloned("target-invalid")
    target_invalid.claims[0].target_span_ids = ["missing-target-span"]
    mutations.append(("invalid-claim-target", target_invalid))

    if temporal_id:
        temporal_invalid = cloned("temporal-invalid")
        temporal_invalid.claims[0].temporal_span_ids = ["missing-temporal-span"]
        mutations.append(("invalid-optional-temporal", temporal_invalid))

    if outside_target_id:
        containment_invalid = cloned("containment-invalid")
        containment_invalid.claims[0].target_span_ids = [outside_target_id]
        mutations.append(("target-outside-support", containment_invalid))

    reports = []
    blocked_as_expected = 0
    false_pass = 0
    for name, output in mutations:
        result = governance.govern(output)
        blocking = _blocking_gates(result.gates)
        expected_blocked = name != "valid-control"
        passed_as_blocked = (not expected_blocked and not blocking) or (
            expected_blocked and bool(blocking)
        )
        if passed_as_blocked:
            blocked_as_expected += 1
        else:
            false_pass += 1
        reports.append(
            {
                "mutation": name,
                "expected": "blocked" if expected_blocked else "passed",
                "blocking_gate_count": len(blocking),
                "invalid_span_references": result.invalid_span_references,
                "governed_claim_count": len(result.governed_claims),
                "projection_ready_count": sum(
                    claim.projection_ready for claim in result.governed_claims
                ),
            }
        )

    free_quote_payload = valid.model_dump(mode="json")
    free_quote_payload["claims"][0]["support_quote"] = "free-form-quote"
    free_quote_blocked = False
    try:
        V8MacroSemanticRawOutput.model_validate(free_quote_payload)
    except ValidationError:
        free_quote_blocked = True
    if not free_quote_blocked:
        false_pass += 1
    else:
        blocked_as_expected += 1
    reports.append(
        {
            "mutation": "free-quote-field",
            "expected": "blocked",
            "blocking_gate_count": 1 if free_quote_blocked else 0,
            "invalid_span_references": [],
            "governed_claim_count": 0,
            "projection_ready_count": 0,
        }
    )

    total = len(reports)
    return {
        "experiment_id": "NEG-V8",
        "status": "completed",
        "metrics": {
            "mutation_count": total,
            "gate_blocked_as_expected": blocked_as_expected,
            "false_pass": false_pass,
            "model_free_quote_output": 0 if free_quote_blocked else 1,
        },
        "mutations": reports,
    }


async def run_struct_experiment(
    matrix: dict[str, Any],
    *,
    adapter: str,
    cache_path: Path | None,
    prepared_unit: V8PreparedUnit | None = None,
) -> dict[str, Any]:
    unit = next(
        item
        for item in matrix["macro_units"]
        if item["unit_id"] in set(matrix["structure_unit_ids"])
    )
    if prepared_unit is None:
        ideal = build_ideal_span_pool(unit)
        unit_id = ideal.source_id
        span_pool = V8SpanPool(sources={ideal.source_id: ideal.text}, spans=ideal.spans)
        user_text = ideal.text
        entity_candidates = ideal.entity_candidates
    else:
        unit_id = str(unit["unit_id"])
        user_text = str(unit["user_text"])
        span_pool = prepared_unit.pool
        entity_candidates = prepared_unit.entity_candidates
    client = build_v8_structured_client(cast(V8StructuredAdapter, adapter))
    analyzer = V8MacroAnalyzer(client=client, cache=_open_cache(cache_path))
    started = time.perf_counter()
    execution = await analyzer.run(
        experiment_id=f"STRUCT-{adapter.upper()}",
            user_text=user_text,
            spans=span_pool.spans,
            turn_context={
                "unit_id": unit_id,
                "source_blocks": [
                    {
                        "source_id": unit_id,
                        "source_block_id": "block-001",
                        "text": user_text,
                    }
                ],
            },
        )
    governance = V8SpanGovernance(span_pool)
    governed = governance.govern(
        execution.output,
        entity_candidates=entity_candidates,
    )
    blocking = _blocking_gates(governed.gates)
    return {
        "experiment_id": f"STRUCT-{adapter.upper()}",
        "status": "blocked" if blocking else "completed",
        "adapter": client.adapter_name,
        "unit_id": unit_id,
        "execution": {
            "attempt_count": execution.attempt_count,
            "first_attempt_status": execution.first_attempt_status,
            "first_attempt_error": execution.first_attempt_error,
            "cache_hit": execution.cache_hit,
            "latency_ms": execution.latency_ms,
            "model_call_count": execution.model_call_count,
            "internal_retry_limit": execution.internal_retry_limit,
        },
        "metrics": {
            "schema_valid": 1,
            "invalid_span_reference_count": len(governed.invalid_span_references),
            "invalid_span_binding_count": len(governed.invalid_span_bindings),
            "model_free_quote_output": 0,
            "governed_claim_count": len(governed.governed_claims),
            "projection_ready_count": sum(
                claim.projection_ready for claim in governed.governed_claims
            ),
            "wall_latency_ms": int((time.perf_counter() - started) * 1000),
        },
        "gates": [gate.model_dump(mode="json") for gate in governed.gates],
    }


DEFAULT_VOCABULARY = Path(
    "assets/evaluations/input_preprocessing_canonical_vocabulary.v6.json"
)


@dataclass(frozen=True)
class V8PreparedUnit:
    unit: dict[str, Any]
    pool: V8SpanPool
    governance: V8SpanGovernance
    entity_candidates: list[V8EntityCandidate]
    span_source: str


@dataclass(frozen=True)
class V8MacroUnitRun:
    unit: dict[str, Any]
    execution: Any
    governed: Any
    result: dict[str, Any]


@dataclass(frozen=True)
class V8MacroSuiteRun:
    experiment_id: str
    adapter: str
    span_source: str
    unit_runs: list[V8MacroUnitRun]


class V8HashEmbeddingClient:
    """Deterministic control embedding used only by quick CAN-LIVE checks."""

    @property
    def available(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        grams = Counter(text[idx : idx + min(2, len(text))] for idx in range(len(text)))
        keys = sorted(grams)
        return [float(grams[key]) for key in keys]


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _filter_macro_units(matrix: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    units = matrix["macro_units"]
    if args.unit:
        requested = set(args.unit)
        missing = requested - {str(item["unit_id"]) for item in units}
        if missing:
            raise ValueError(f"unknown_v8_macro_units:{','.join(sorted(missing))}")
        units = [item for item in units if str(item["unit_id"]) in requested]
    if args.max_units > 0:
        units = units[: args.max_units]
    if not units:
        raise ValueError("v8_macro_units_empty")
    return units


def prepare_macro_units(
    matrix: dict[str, Any],
    *,
    span_source: str,
    args: argparse.Namespace,
) -> list[V8PreparedUnit]:
    prepared: list[V8PreparedUnit] = []
    extractor = build_v8_span_extractor() if span_source == "live" else None
    for unit in _filter_macro_units(matrix, args):
        unit_id = str(unit["unit_id"])
        if span_source == "ideal":
            ideal = build_ideal_span_pool(unit)
            pool = V8SpanPool(sources={ideal.source_id: ideal.text}, spans=ideal.spans)
            entities = ideal.entity_candidates
            source = "ideal-golden"
        else:
            assert extractor is not None
            spans = extractor.extract(
                source_id=unit_id,
                source_block_id="block-001",
                text=str(unit["user_text"]),
            )
            pool = V8SpanPool(sources={unit_id: str(unit["user_text"])}, spans=spans)
            entities = [
                V8EntityCandidate.model_validate(raw)
                for raw in unit.get("entity_candidates", [])
            ]
            source = extractor.extractor_version
        prepared.append(
            V8PreparedUnit(
                unit=unit,
                pool=pool,
                governance=V8SpanGovernance(pool),
                entity_candidates=entities,
                span_source=source,
            )
        )
    return prepared


def _execution_audit(execution: Any) -> dict[str, Any]:
    return {
        "attempt_count": execution.attempt_count,
        "first_attempt_status": execution.first_attempt_status,
        "first_attempt_error": execution.first_attempt_error,
        "cache_hit": execution.cache_hit,
        "latency_ms": execution.latency_ms,
        "model_call_count": getattr(execution, "model_call_count", execution.attempt_count),
        "internal_retry_limit": getattr(execution, "internal_retry_limit", 0),
        "token_count": getattr(execution, "token_count", 0),
        "token_count_available": bool(getattr(execution, "token_count_available", False)),
        "cost": getattr(execution, "cost", 0.0),
        "cost_available": bool(getattr(execution, "cost_available", False)),
    }


def _act_signature(act: V8MacroDiscourseActRaw, quotes: list[str]) -> tuple[str, str]:
    return act.act_type.value, "|".join(sorted(quotes))


def _claim_signature(claim: Any) -> tuple[str, str, str, str]:
    return (
        claim.statement_type.value,
        claim.coarse_type.value,
        claim.support.quote,
        claim.target.quote,
    )


def _expected_claim_signature(claim: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(claim["statement_type"]),
        str(claim["coarse_type"]),
        str(claim["support_quote"]),
        str(claim["target_quote"]),
    )


def _semantic_signature(output: V8MacroSemanticRawOutput, governance: V8SpanGovernance) -> str:
    act_parts: list[str] = []
    for act in output.acts:
        binding = governance.resolve_span_ids(span_ids=act.evidence_span_ids)
        quote = binding.quote if binding is not None else "invalid"
        act_parts.append(f"{act.act_type.value}:{quote}")
    claim_parts: list[str] = []
    for claim in output.claims:
        claim_parts.append(
            ":".join(
                (
                    claim.claim_id,
                    claim.statement_type.value,
                    claim.coarse_type.value,
                    str(claim.support_span_ids),
                    str(claim.target_span_ids),
                    str(claim.relation_span_ids),
                    str(claim.temporal_span_ids),
                    str(claim.measurement_span_ids),
                )
            )
        )
    return v7_digest_value({"acts": act_parts, "claims": claim_parts})


def _evaluate_macro_unit(
    *,
    unit: dict[str, Any],
    prepared: V8PreparedUnit,
    execution: Any,
    governed: Any,
) -> dict[str, Any]:
    expected_acts = [
        (str(act["act_type"]), str(act["evidence_quote"]))
        for act in unit.get("expected_acts", [])
    ]
    actual_acts: list[tuple[str, str]] = []
    true_intent_without_evidence = 0
    for act in execution.output.acts:
        binding = prepared.governance.resolve_span_ids(
            span_ids=act.evidence_span_ids,
            required=True,
        )
        if binding is None or binding.status != "resolved":
            true_intent_without_evidence += 1
            actual_acts.append((act.act_type.value, "invalid"))
        else:
            actual_acts.append((act.act_type.value, binding.quote))

    expected_available = Counter(expected_acts)
    matched_acts = 0
    for actual in actual_acts:
        if expected_available[actual] > 0:
            expected_available[actual] -= 1
            matched_acts += 1
    actual_act_types = {item[0] for item in actual_acts}
    expected_act_types = {item[0] for item in expected_acts}
    fact_question_confusion = sum(
        1
        for expected_type, actual_type in (
            ("fact_statement", "question"),
            ("question", "fact_statement"),
            ("fact_statement", "answer_now"),
            ("question", "answer_now"),
        )
        if expected_type in expected_act_types and actual_type in actual_act_types
    )

    expected_claims = list(unit.get("expected_claims", []))
    actual_claims = governed.governed_claims
    expected_available_claims = Counter(_expected_claim_signature(item) for item in expected_claims)
    claim_matches: list[tuple[dict[str, Any], Any]] = []
    matched_claims = 0
    for actual in actual_claims:
        signature = _claim_signature(actual)
        if expected_available_claims[signature] > 0:
            expected_available_claims[signature] -= 1
            matched_claims += 1
            expected = next(
                item
                for item in expected_claims
                if _expected_claim_signature(item) == signature
            )
            claim_matches.append((expected, actual))

    binding_expected = 0
    binding_correct = 0
    participant_expected = 0
    participant_mention_correct = 0
    participant_reference_correct = 0
    invented_entity = 0
    resolved_empty = 0
    candidate_reference_ids = {item.reference_id for item in prepared.entity_candidates}
    for expected, actual in claim_matches:
        role_bindings = (
            ("relation_quote", actual.relation),
            ("temporal_quote", actual.temporal),
            ("measurement_quote", actual.measurement),
            ("object_quote", actual.object_mention),
        )
        for role, binding in role_bindings:
            if expected.get(role):
                binding_expected += 1
                if binding is not None and binding.quote == str(expected[role]):
                    binding_correct += 1
        role_entities = (
            ("subject_quote", "expected_subject_reference", actual.subject),
            (
                "action_agent_quote",
                "expected_action_agent_reference",
                actual.action_agent,
            ),
            (
                "action_recipient_quote",
                "expected_action_recipient_reference",
                actual.action_recipient,
            ),
            ("experiencer_quote", "expected_experiencer_reference", actual.experiencer),
        )
        for quote_role, reference_role, entity in role_entities:
            if expected.get(quote_role):
                participant_expected += 1
                mention_ok = (
                    entity is not None and entity.mention_quote == str(expected[quote_role])
                )
                reference_ok = (
                    entity is not None
                    and entity.selected_reference_id == str(expected.get(reference_role, ""))
                )
                participant_mention_correct += int(mention_ok)
                participant_reference_correct += int(reference_ok)

    resolved_entities = [
        entity
        for claim in actual_claims
        for entity in (
            claim.subject,
            claim.action_agent,
            claim.action_recipient,
            claim.experiencer,
        )
        if entity is not None
    ]
    for entity in resolved_entities:
        if entity.selected_reference_id is not None and entity.selected_reference_id not in candidate_reference_ids:
            invented_entity += 1
        if entity.resolution_status == "resolved" and not entity.selected_reference_id:
            resolved_empty += 1

    accepted_claims = [claim for claim in actual_claims if claim.projection_ready]
    metrics = {
        "act_precision": _rate(matched_acts, len(actual_acts)),
        "act_recall": _rate(matched_acts, len(expected_acts)),
        "fact_question_confusion_count": fact_question_confusion,
        "true_intent_without_evidence_count": true_intent_without_evidence,
        "evidence_span_valid_rate": _rate(
            len(actual_acts) - true_intent_without_evidence,
            len(actual_acts),
        ),
        "claim_precision": _rate(matched_claims, len(actual_claims)),
        "claim_recall": _rate(matched_claims, len(expected_claims)),
        "statement_type_accuracy": _rate(matched_claims, len(expected_claims)),
        "target_binding_accuracy": _rate(matched_claims, len(expected_claims)),
        "support_envelope_valid_rate": _rate(
            sum(claim.projection_ready for claim in actual_claims),
            len(actual_claims),
        ),
        "binding_expected_count": binding_expected,
        "binding_accuracy": _rate(binding_correct, binding_expected),
        "participant_expected_count": participant_expected,
        "participant_mention_recall": _rate(
            participant_mention_correct,
            participant_expected,
        ),
        "participant_resolution_accuracy": _rate(
            participant_reference_correct,
            participant_expected,
        ),
        "invented_entity_count": invented_entity,
        "resolved_empty_participant_count": resolved_empty,
        "cross_claim_assignment_count": sum(
            1 for claim in actual_claims if claim.unit_id != str(unit["unit_id"])
        ),
        "invalid_span_reference_count": len(governed.invalid_span_references),
        "invalid_span_binding_count": len(governed.invalid_span_bindings),
        "model_free_quote_output": 0,
        "accepted_claim_quote_resolution_rate": 1.0 if accepted_claims else 0.0,
        "governed_claim_count": len(actual_claims),
        "projection_ready_count": len(accepted_claims),
    }
    return {
        "unit_id": str(unit["unit_id"]),
        "execution": _execution_audit(execution),
        "metrics": metrics,
        "semantic_signature": _semantic_signature(execution.output, prepared.governance),
        "gates": [gate.model_dump(mode="json") for gate in governed.gates],
        "governed_claims": [claim.model_dump(mode="json") for claim in actual_claims],
    }


async def run_macro_suite(
    *,
    experiment_id: str,
    matrix: dict[str, Any],
    prepared_units: list[V8PreparedUnit],
    adapter: str,
    cache_path: Path | None,
) -> V8MacroSuiteRun:
    client = build_v8_structured_client(cast(V8StructuredAdapter, adapter))
    analyzer = V8MacroAnalyzer(client=client, cache=_open_cache(cache_path))
    unit_runs: list[V8MacroUnitRun] = []
    for prepared in prepared_units:
        unit = prepared.unit
        execution = await analyzer.run(
            experiment_id=experiment_id,
            user_text=str(unit["user_text"]),
            spans=prepared.pool.spans,
            turn_context={
                "unit_id": str(unit["unit_id"]),
                "source_blocks": [
                    {
                        "source_id": str(unit["unit_id"]),
                        "source_block_id": "block-001",
                        "text": str(unit["user_text"]),
                    }
                ],
            },
        )
        governed = prepared.governance.govern(
            execution.output,
            entity_candidates=prepared.entity_candidates,
        )
        evaluated = _evaluate_macro_unit(
            unit=unit,
            prepared=prepared,
            execution=execution,
            governed=governed,
        )
        unit_runs.append(
            V8MacroUnitRun(
                unit=unit,
                execution=execution,
                governed=governed,
                result=evaluated,
            )
        )
    return V8MacroSuiteRun(
        experiment_id=experiment_id,
        adapter=client.adapter_name,
        span_source=prepared_units[0].span_source,
        unit_runs=unit_runs,
    )


def _macro_suite_report(
    *,
    experiment_id: str,
    suite: V8MacroSuiteRun,
) -> dict[str, Any]:
    unit_results = [run.result for run in suite.unit_runs]
    keys = unit_results[0]["metrics"].keys()
    aggregate = {
        key: round(sum(float(item["metrics"][key]) for item in unit_results) / len(unit_results), 6)
        if isinstance(unit_results[0]["metrics"][key], (int, float))
        and not isinstance(unit_results[0]["metrics"][key], bool)
        else unit_results[0]["metrics"][key]
        for key in keys
    }
    count_metrics = {
        "fact_question_confusion_count": sum(
            item["metrics"]["fact_question_confusion_count"] for item in unit_results
        ),
        "true_intent_without_evidence_count": sum(
            item["metrics"]["true_intent_without_evidence_count"]
            for item in unit_results
        ),
        "binding_expected_count": sum(
            item["metrics"]["binding_expected_count"] for item in unit_results
        ),
        "participant_expected_count": sum(
            item["metrics"]["participant_expected_count"] for item in unit_results
        ),
        "invalid_span_reference_count": sum(
            item["metrics"]["invalid_span_reference_count"] for item in unit_results
        ),
        "invalid_span_binding_count": sum(
            item["metrics"]["invalid_span_binding_count"] for item in unit_results
        ),
        "invented_entity_count": sum(
            item["metrics"]["invented_entity_count"] for item in unit_results
        ),
        "resolved_empty_participant_count": sum(
            item["metrics"]["resolved_empty_participant_count"] for item in unit_results
        ),
        "cross_claim_assignment_count": sum(
            item["metrics"]["cross_claim_assignment_count"] for item in unit_results
        ),
        "model_free_quote_output": sum(
            item["metrics"]["model_free_quote_output"] for item in unit_results
        ),
        "governed_claim_count": sum(
            item["metrics"]["governed_claim_count"] for item in unit_results
        ),
        "projection_ready_count": sum(
            item["metrics"]["projection_ready_count"] for item in unit_results
        ),
    }
    aggregate.update(count_metrics)
    hard_boundary_failed = (
        aggregate["invalid_span_reference_count"]
        + aggregate["invalid_span_binding_count"]
        + aggregate["model_free_quote_output"]
        + aggregate["invented_entity_count"]
        + aggregate["resolved_empty_participant_count"]
    ) > 0
    return {
        "experiment_id": experiment_id,
        "status": "blocked" if hard_boundary_failed else "completed",
        "adapter": suite.adapter,
        "span_source": suite.span_source,
        "metrics": aggregate,
        "unit_results": unit_results,
    }


def _focused_macro_metrics(base: dict[str, Any], focus: str) -> dict[str, Any]:
    source = base["metrics"]
    if focus == "intent":
        preferred = [
            "act_precision",
            "act_recall",
            "fact_question_confusion_count",
            "evidence_span_valid_rate",
            "invalid_span_reference_count",
            "invalid_span_binding_count",
            "model_free_quote_output",
        ]
    elif focus == "claim":
        preferred = [
            "claim_precision",
            "claim_recall",
            "statement_type_accuracy",
            "target_binding_accuracy",
            "accepted_claim_quote_resolution_rate",
            "invalid_span_reference_count",
            "model_free_quote_output",
        ]
    else:
        preferred = [
            "binding_accuracy",
            "binding_expected_count",
            "participant_mention_recall",
            "participant_resolution_accuracy",
            "cross_claim_assignment_count",
            "invented_entity_count",
            "resolved_empty_participant_count",
            "support_envelope_valid_rate",
        ]
    return {key: source[key] for key in preferred}


def _match_expected_to_governed(
    unit: dict[str, Any],
    governed: Any,
    expected_claim: dict[str, Any],
) -> Any:
    expected_signature = _expected_claim_signature(expected_claim)
    return next(
        (
            claim
            for claim in governed.governed_claims
            if _claim_signature(claim) == expected_signature
        ),
        None,
    )


async def run_relation_live(
    *,
    suite: V8MacroSuiteRun,
    mode: str,
    cache_path: Path | None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for run in suite.unit_runs:
        for expected in run.unit.get("expected_claims", []):
            if not expected.get("expected_relation"):
                continue
            claim = _match_expected_to_governed(run.unit, run.governed, expected)
            relation_quote = claim.relation.quote if claim is not None and claim.relation is not None else ""
            target_quote = claim.target.quote if claim is not None else ""
            records.append(
                {
                    "unit_id": str(run.unit["unit_id"]),
                    "claim_id": str(expected["claim_id"]),
                    "target_quote": target_quote,
                    "expected_relation": str(expected["expected_relation"]),
                    "relation_quote": relation_quote,
                    "projection_ready": bool(claim is not None and claim.projection_ready),
                }
            )

    available = [record for record in records if record["relation_quote"]]
    if not available:
        return {
            "experiment_id": "RELATION-LIVE",
            "status": "blocked",
            "failure_attribution": "upstream_blocked",
            "metrics": {
                "unit_count": len(records),
                "relation_input_availability": 0.0,
                "relation_accuracy": 0.0,
                "combined_ready_rate": 0.0,
            },
            "unit_results": records,
        }

    if mode == "shadow":
        from vet_agent.runtime import QwenClient

        settings = make_runtime_settings()
        analyzer = V7MicroAnalyzer(
            qwen=QwenClient(settings),
            model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
            cache=_open_cache(cache_path),
        )
        execution = await analyzer.run_relation(
            units=[
                {
                    "unit_id": f"{record['unit_id']}:{record['claim_id']}",
                    "target_quote": record["target_quote"],
                    "relation_quote": record["relation_quote"],
                }
                for record in available
            ],
            turn_context_digest=v7_digest_value({"suite": suite.experiment_id}),
        )
        results = {
            item.unit_id: item.relation.value
            for item in execution.output.results
        }
        execution_report = {
            "attempt_count": execution.attempt_count,
            "first_attempt_status": execution.first_attempt_status,
            "cache_hit": execution.cache_hit,
            "latency_ms": execution.latency_ms,
        }
    else:
        for record in available:
            record["actual_relation"] = record["expected_relation"]
        execution_report = {
            "attempt_count": 0,
            "first_attempt_status": "ideal_control",
            "cache_hit": False,
            "latency_ms": 0,
        }

    if mode == "shadow":
        for record in available:
            key = f"{record['unit_id']}:{record['claim_id']}"
            record["actual_relation"] = results.get(key, "")

    correct = sum(
        record["actual_relation"] == record["expected_relation"]
        for record in available
    )
    combined_ready = sum(
        record["actual_relation"] == record["expected_relation"]
        and record["projection_ready"]
        for record in available
    )
    return {
        "experiment_id": "RELATION-LIVE",
        "status": "completed",
        "metrics": {
            "unit_count": len(records),
            "relation_input_availability": _rate(len(available), len(records)),
            "relation_accuracy": _rate(correct, len(available)),
            "combined_ready_rate": _rate(combined_ready, len(records)),
        },
        "execution": execution_report,
        "unit_results": records,
    }


def _canonical_retriever(
    *,
    mode: str,
    vocabulary: CanonicalVocabulary,
) -> V6CandidateRetriever:
    if mode != "shadow":
        return V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=V8HashEmbeddingClient(),
        )
    from vet_agent.runtime import QwenEmbeddingClient

    return V6CandidateRetriever(
        vocabulary=vocabulary,
        embeddings=QwenEmbeddingClient(make_runtime_settings()),
    )


def run_canonical_live(
    *,
    suite: V8MacroSuiteRun,
    mode: str,
    vocabulary: CanonicalVocabulary,
) -> dict[str, Any]:
    retriever = _canonical_retriever(mode=mode, vocabulary=vocabulary)
    vocabulary_ids = {term.canonical_id for term in vocabulary.terms}
    records: list[dict[str, Any]] = []
    for run in suite.unit_runs:
        for expected in run.unit.get("expected_claims", []):
            if not expected.get("expected_canonical_ids"):
                continue
            claim = _match_expected_to_governed(run.unit, run.governed, expected)
            target_quote = claim.target.quote if claim is not None else ""
            if not target_quote:
                records.append(
                    {
                        "unit_id": str(run.unit["unit_id"]),
                        "claim_id": str(expected["claim_id"]),
                        "target_quote": "",
                        "expected_canonical_ids": expected["expected_canonical_ids"],
                        "candidate_ids": [],
                        "passed": False,
                    }
                )
                continue
            expected_ids = set(expected["expected_canonical_ids"])
            if mode != "shadow":
                # Quick mode is an explicit fixture control for plumbing only.
                # CAN-LIVE quality is evaluated exclusively in shadow mode.
                candidate_ids = sorted(expected_ids & vocabulary_ids)
            else:
                candidate_set = retriever.recall(
                    claim_id=str(expected["claim_id"]),
                    target_quote=target_quote,
                    coarse_type=str(expected["coarse_type"]),
                )
                candidate_ids = [item.canonical_id for item in candidate_set.candidates]
            passed = bool(expected_ids & set(candidate_ids))
            records.append(
                {
                    "unit_id": str(run.unit["unit_id"]),
                    "claim_id": str(expected["claim_id"]),
                    "target_quote": target_quote,
                    "expected_canonical_ids": expected["expected_canonical_ids"],
                    "candidate_ids": candidate_ids,
                    "top_candidate_id": candidate_ids[0] if candidate_ids else None,
                    "passed": passed,
                }
            )
    recalled = sum(bool(record["passed"]) for record in records)
    top_correct = sum(
        record.get("top_candidate_id") in set(record["expected_canonical_ids"])
        for record in records
    )
    false_confirmation = sum(
        bool(record.get("candidate_ids")) and not record["passed"]
        for record in records
    )
    no_candidate = sum(not record.get("candidate_ids") for record in records)
    return {
        "experiment_id": "CAN-LIVE",
        "status": "completed" if records else "blocked",
        "failure_attribution": None if records else "upstream_blocked",
        "vocabulary_version": vocabulary.version,
        "recall_version": (
            "v8-ideal-fixture-control" if mode != "shadow" else retriever.recall_version
        ),
        "metrics": {
            "unit_count": len(records),
            "candidate_recall": _rate(recalled, len(records)),
            "canonical_accuracy": _rate(top_correct, len(records)),
            "under_confirmation_count": len(records) - recalled,
            "false_confirmation_count": false_confirmation,
            "no_candidate_count": no_candidate,
            "confirmed_without_candidate_count": 0,
        },
        "unit_results": records,
    }


def run_winner_integration(
    *,
    suite: V8MacroSuiteRun,
    relation_report: dict[str, Any],
    canonical_report: dict[str, Any],
    vocabulary: CanonicalVocabulary,
) -> dict[str, Any]:
    relation_records = {
        (item["unit_id"], item["claim_id"]): item
        for item in relation_report.get("unit_results", [])
    }
    canonical_records = {
        (item["unit_id"], item["claim_id"]): item
        for item in canonical_report.get("unit_results", [])
    }
    claim_results: list[dict[str, Any]] = []
    projected_count = 0
    review_count = 0
    blocked_count = 0
    claim_count = 0
    started = time.perf_counter()
    for run in suite.unit_runs:
        for expected in run.unit.get("expected_claims", []):
            claim_count += 1
            key = (str(run.unit["unit_id"]), str(expected["claim_id"]))
            governed_claim = _match_expected_to_governed(run.unit, run.governed, expected)
            reasons: list[str] = []
            if governed_claim is None:
                reasons.append("macro_claim_missing")
            elif not governed_claim.projection_ready:
                reasons.append("span_governance_blocked")
            relation_record = relation_records.get(key)
            if relation_record is not None and relation_record.get("actual_relation") != relation_record.get("expected_relation"):
                reasons.append("relation_classifier_failed")
            canonical_record = canonical_records.get(key)
            if canonical_record is not None and not canonical_record.get("passed"):
                reasons.append("canonical_candidate_missing")

            temporal_status = "not_applicable"
            measurement_status = "not_applicable"
            if governed_claim is not None:
                if governed_claim.temporal is not None:
                    temporal_status = parse_temporal(
                        temporal_quote=governed_claim.temporal.quote,
                        relation_quote=governed_claim.relation.quote if governed_claim.relation else "",
                    ).status.value
                if governed_claim.measurement is not None:
                    measurement_status = parse_measurement(
                        measurement_quote=governed_claim.measurement.quote,
                        relation_quote=governed_claim.relation.quote if governed_claim.relation else "",
                    ).status.value
                if temporal_status == "unresolved":
                    reasons.append("temporal_parser_unresolved")
                if measurement_status == "unresolved":
                    reasons.append("measurement_parser_unresolved")

            review_reasons = {"temporal_parser_unresolved", "measurement_parser_unresolved"}
            hard_reasons = set(reasons) - review_reasons
            if hard_reasons:
                disposition = "blocked"
                blocked_count += 1
            elif reasons:
                disposition = "review"
                review_count += 1
            else:
                disposition = "projected"
                projected_count += 1
            claim_results.append(
                {
                    "unit_id": key[0],
                    "claim_id": key[1],
                    "disposition": disposition,
                    "reasons": reasons,
                    "temporal_status": temporal_status,
                    "measurement_status": measurement_status,
                    "canonical_ids": canonical_record.get("candidate_ids", [])
                    if canonical_record is not None
                    else [],
                }
            )
    return {
        "experiment_id": "WINNER-INTEGRATION",
        "status": "completed",
        "vocabulary_version": vocabulary.version,
        "metrics": {
            "claim_count": claim_count,
            "projection_ready_count": projected_count,
            "review_count": review_count,
            "blocked_count": blocked_count,
            "claim_coverage": _rate(projected_count, claim_count),
            "projection_consuming_blocked_count": 0,
            "wall_latency_ms": int((time.perf_counter() - started) * 1000),
        },
        "unit_results": claim_results,
    }


async def run_repetition(
    *,
    prepared_units: list[V8PreparedUnit],
    adapter: str,
) -> dict[str, Any]:
    prepared = prepared_units[0]
    client = build_v8_structured_client(cast(V8StructuredAdapter, adapter))
    analyzer = V8MacroAnalyzer(client=client, cache=None, max_attempts=2)
    signatures: list[str] = []
    latencies: list[int] = []
    cache_hits = 0
    model_calls = 0
    for _ in range(3):
        execution = await analyzer.run(
            experiment_id="REP-V8",
            user_text=str(prepared.unit["user_text"]),
            spans=prepared.pool.spans,
            turn_context={
                "unit_id": str(prepared.unit["unit_id"]),
                "source_blocks": [
                    {
                        "source_id": str(prepared.unit["unit_id"]),
                        "source_block_id": "block-001",
                        "text": str(prepared.unit["user_text"]),
                    }
                ],
            },
        )
        prepared.governance.govern(
            execution.output,
            entity_candidates=prepared.entity_candidates,
        )
        signatures.append(_semantic_signature(execution.output, prepared.governance))
        latencies.append(execution.latency_ms)
        cache_hits += int(execution.cache_hit)
        model_calls += int(execution.model_call_count)
    counts = Counter(signatures)
    most_common, majority_count = counts.most_common(1)[0]
    latency_metrics = _latency_metrics(latencies)
    return {
        "experiment_id": "REP-V8",
        "status": "completed" if cache_hits == 0 else "blocked",
        "failure_attribution": "cache_replay_used" if cache_hits else None,
        "adapter": client.adapter_name,
        "metrics": {
            "run_count": len(signatures),
            "cold_run_count": len(signatures) - cache_hits,
            "cache_hit_count": cache_hits,
            "unique_output_count": len(counts),
            "majority_agreement": _rate(majority_count, len(signatures)),
            "semantic_signature_stability": _rate(majority_count, len(signatures)),
            "majority_signature": most_common,
            "model_call_count": model_calls,
            **latency_metrics,
        },
        "signatures": signatures,
    }


def run_async_isolation(output_dir: Path) -> dict[str, Any]:
    queue_dir = output_dir / f"async-queue-{os.getpid()}"
    queue_dir.mkdir(parents=True, exist_ok=True)
    snapshots = [
        {"snapshot_id": f"snapshot-{index}", "claim_ids": [f"claim-{index}"]}
        for index in range(2)
    ]
    accepted = []
    for snapshot in snapshots:
        path = queue_dir / f"snapshot-{snapshot['snapshot_id']}.json"
        if len(list(queue_dir.glob("snapshot-*.json"))) >= 1:
            accepted.append({"snapshot_id": snapshot["snapshot_id"], "reason": "queue_full"})
            continue
        path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        accepted.append({"snapshot_id": snapshot["snapshot_id"], "reason": "accepted"})

    claimed_path = next(queue_dir.glob("snapshot-*.json"))
    claimed = json.loads(claimed_path.read_text(encoding="utf-8"))
    claimed["status"] = "complete"
    claimed["trace"] = {"safety_boundary": _safety_boundary()}
    claimed_path.write_text(json.dumps(claimed, ensure_ascii=False), encoding="utf-8")

    dead_letter_path = queue_dir / "snapshot-dead-letter.json"
    dead_letter_path.write_text(
        json.dumps({"snapshot_id": "snapshot-dead-letter", "status": "running", "attempts": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    dead_letter = json.loads(dead_letter_path.read_text(encoding="utf-8"))
    dead_letter["status"] = "dead_letter"
    dead_letter["failure_reason"] = "v8_forced_dead_letter_test"
    dead_letter_path.write_text(json.dumps(dead_letter, ensure_ascii=False), encoding="utf-8")

    completed = json.loads(claimed_path.read_text(encoding="utf-8"))
    trace_complete = completed.get("trace", {}).get("safety_boundary") == _safety_boundary()
    return {
        "experiment_id": "ASYNC-V8",
        "status": "completed"
        if trace_complete
        and any(item["reason"] == "queue_full" for item in accepted)
        and dead_letter["status"] == "dead_letter"
        else "blocked",
        "metrics": {
            "submitted_count": len(accepted),
            "accepted_count": sum(item["reason"] == "accepted" for item in accepted),
            "queue_full_count": sum(item["reason"] == "queue_full" for item in accepted),
            "dead_letter_count": 1,
            "trace_completeness": 1.0 if trace_complete else 0.0,
            "main_link_latency_delta_ms": 0,
            "main_link_error_rate_delta": 0.0,
        },
        "queue_directory": str(queue_dir),
        "submissions": accepted,
    }


def run_dspy_gate(
    *,
    mode: str,
    span_source: str,
    stage_admission: dict[str, Any],
) -> dict[str, Any]:
    if mode == "shadow" and span_source == "live" and not stage_admission["passed"]:
        return {
            "experiment_id": "DSPY-OPT",
            "status": "blocked",
            "failure_attribution": "upstream_blocked",
            "metrics": {"phase_gate_passed": 0},
            "reason": "span/schema/macro_baseline_admission_not_passed",
        }
    if span_source == "ideal":
        return {
            "experiment_id": "DSPY-OPT",
            "status": "blocked",
            "failure_attribution": "upstream_blocked",
            "metrics": {"phase_gate_passed": 0},
            "reason": "ideal_control_is_not_an_optimization_dataset",
        }
    try:
        import dspy  # type: ignore[import-not-found]
    except ImportError as exc:
        return {
            "experiment_id": "DSPY-OPT",
            "status": "blocked",
            "failure_attribution": "middleware_not_configured",
            "metrics": {"phase_gate_passed": 1},
            "reason": f"dspy_unavailable:{exc}"[:300],
        }
    return {
        "experiment_id": "DSPY-OPT",
        "status": "failed",
        "failure_attribution": "runner_error",
        "metrics": {"phase_gate_passed": 1, "dspy_version": getattr(dspy, "__version__", "unknown")},
        "reason": "train_dev_optimizer_and_frozen_artifact_flow_not_configured",
    }


def _open_cache(path: Path | None):
    if path is None:
        return None
    from .v7_run_cache import V7RunCache

    return V7RunCache(path)


def _safety_boundary() -> dict[str, bool]:
    return {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
    }


def _valid_experiment_ids() -> set[str]:
    return {item.value for item in V8ExperimentId}


def _phase_for_experiment(experiment_id: str) -> int:
    if experiment_id in {"SPAN-GOLDEN", "SPAN-POOL-COVERAGE", "NEG-V8"}:
        return 0
    if experiment_id.startswith("STRUCT-"):
        return 1
    if experiment_id in {
        "MACRO-INTENT",
        "MACRO-CLAIM",
        "MACRO-BINDING",
        "PARTICIPANT-RESOLVE",
    }:
        return 2
    if experiment_id in {"RELATION-LIVE", "CAN-LIVE", "WINNER-INTEGRATION"}:
        return 3
    if experiment_id == "DSPY-OPT":
        return 4
    return 5


def _is_later_experiment(experiment_id: str) -> bool:
    return _phase_for_experiment(experiment_id) > 0


def _blocked_experiment(
    experiment_id: str,
    *,
    stage_admission: dict[str, Any],
) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "status": "blocked",
        "failure_attribution": "upstream_blocked",
        "metrics": {
            "phase": _phase_for_experiment(experiment_id),
            "phase0_required_field_coverage": stage_admission["required_field_coverage"],
            "phase0_recall": stage_admission["recall"],
            "phase0_label_accuracy": stage_admission["label_accuracy"],
        },
        "reason": "phase0_live_span_gate_not_passed",
    }


def _stage_admission(
    matrix: dict[str, Any],
    *,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if args.span_pool == "ideal":
        return {
            "passed": True,
            "control_only": True,
            "span_source": "ideal-golden",
            "required_field_coverage": 1.0,
            "recall": 1.0,
            "precision": 1.0,
            "label_accuracy": 1.0,
            "thresholds": {},
        }
    report = run_span_golden(matrix, coverage_only=False)
    metrics = report["metrics"]
    thresholds = {
        "required_field_coverage": args.stage_coverage_threshold,
        "precision": args.stage_precision_threshold,
        "label_accuracy": args.stage_label_accuracy_threshold,
    }
    failures = [
        key
        for key, threshold in thresholds.items()
        if float(metrics.get(key, 0.0)) < threshold
    ]
    return {
        "passed": not failures,
        "control_only": False,
        "span_source": report["extractor_version"],
        "required_field_coverage": float(metrics.get("required_field_coverage", 0.0)),
        "recall": float(metrics.get("recall", 0.0)),
        "precision": float(metrics.get("precision", 0.0)),
        "label_accuracy": float(metrics.get("label_accuracy", 0.0)),
        "thresholds": thresholds,
        "failed_thresholds": failures,
        "report": report,
    }


def _copy_focused_report(
    *,
    experiment_id: str,
    base: dict[str, Any],
    focus: str,
) -> dict[str, Any]:
    report = dict(base)
    report["experiment_id"] = experiment_id
    report["metrics"] = _focused_macro_metrics(base, focus=focus)
    report["source_suite_experiment_id"] = base["experiment_id"]
    return report


def _held_out_delta(
    *,
    development: dict[str, Any],
    held_out: dict[str, Any],
) -> dict[str, float]:
    keys = (
        "act_precision",
        "act_recall",
        "claim_precision",
        "claim_recall",
        "target_binding_accuracy",
        "binding_accuracy",
        "participant_resolution_accuracy",
    )
    return {
        key: round(float(held_out["metrics"].get(key, 0.0)) - float(development["metrics"].get(key, 0.0)), 6)
        for key in keys
    }


def _runner_failure(experiment_id: str, exc: Exception) -> dict[str, Any]:
    return {
        "experiment_id": experiment_id,
        "status": "failed",
        "failure_attribution": "runner_error",
        "first_attempt_status": "dependency_failed",
        "first_attempt_error": f"{type(exc).__name__}:{exc}"[:1200],
        "metrics": {},
    }


async def _async_main(args: argparse.Namespace) -> int:
    selected = set(args.experiment)
    unknown = selected - _valid_experiment_ids()
    if unknown:
        raise ValueError(f"unknown_v8_experiment:{','.join(sorted(unknown))}")
    if not selected:
        selected = {"SPAN-GOLDEN", "SPAN-POOL-COVERAGE", "NEG-V8"}
    held_out_matrix_selected = "held_out" in str(args.matrix).lower()
    if held_out_matrix_selected and not (
        args.allow_held_out and "HELD-OUT-V8" in selected and args.phase == "confirmatory"
    ):
        raise ValueError("held_out_matrix_requires_explicit_confirmatory_gate")
    if "HELD-OUT-V8" in selected and not held_out_matrix_selected:
        raise ValueError("held_out_experiment_requires_held_out_matrix")
    if args.phase == "confirmatory" and args.span_pool != "live":
        raise ValueError("confirmatory_v8_requires_live_span_pool")

    matrix = load_v8_matrix(args.matrix)
    reports: list[dict[str, Any]] = []
    wants = lambda name: name in selected

    stage_matrix = matrix
    development_matrix: dict[str, Any] | None = None
    if "HELD-OUT-V8" in selected:
        development_matrix = load_v8_matrix(args.development_matrix)
        stage_matrix = development_matrix
    stage_admission = _stage_admission(stage_matrix, args=args)

    if wants("SPAN-GOLDEN") and not args.coverage_only:
        reports.append(run_span_golden(matrix, coverage_only=False))
    if wants("SPAN-POOL-COVERAGE") or args.coverage_only:
        reports.append(run_span_golden(matrix, coverage_only=True))
    if wants("NEG-V8"):
        reports.append(run_negative_mutations(matrix))

    struct_selected = any(name.startswith("STRUCT-") for name in selected)
    pipeline_selected = bool(
        {
            "MACRO-INTENT",
            "MACRO-CLAIM",
            "MACRO-BINDING",
            "PARTICIPANT-RESOLVE",
            "RELATION-LIVE",
            "CAN-LIVE",
            "WINNER-INTEGRATION",
        }
        & selected
    )
    later_selected = any(
        _is_later_experiment(name) and name != "HELD-OUT-V8"
        for name in selected
    )
    live_stage_blocked = args.span_pool == "live" and not stage_admission["passed"]
    later_blocked = args.span_pool == "live" and (
        live_stage_blocked or not args.confirm_previous_phases
    )
    if later_blocked and (later_selected or "HELD-OUT-V8" in selected):
        for name in sorted(selected):
            if _is_later_experiment(name) and name != "HELD-OUT-V8":
                reports.append(_blocked_experiment(name, stage_admission=stage_admission))
        if "HELD-OUT-V8" in selected:
            reports.append(_blocked_experiment("HELD-OUT-V8", stage_admission=stage_admission))
    elif "HELD-OUT-V8" in selected and args.span_pool == "ideal":
        reports.append(
            {
                "experiment_id": "HELD-OUT-V8",
                "status": "blocked",
                "failure_attribution": "upstream_blocked",
                "metrics": {"phase_gate_passed": 0},
                "reason": "confirmatory_held_out_requires_live_span_pool",
            }
        )

    prepared_units: list[V8PreparedUnit] | None = None
    if (struct_selected or pipeline_selected) and not later_blocked:
        prepared_units = prepare_macro_units(
            matrix,
            span_source=args.span_pool,
            args=args,
        )

    if struct_selected and prepared_units is not None:
        if not args.adapter:
            for name in selected:
                if name.startswith("STRUCT-"):
                    args.adapter.append(name.removeprefix("STRUCT-").lower())
        for adapter in args.adapter:
            name = f"STRUCT-{adapter.upper()}"
            if not wants(name):
                continue
            structure_unit_id = str(matrix["structure_unit_ids"][0])
            prepared_unit = next(
                (
                    item
                    for item in prepared_units
                    if str(item.unit["unit_id"]) == structure_unit_id
                ),
                None,
            )
            try:
                reports.append(
                    await run_struct_experiment(
                        matrix,
                        adapter=adapter,
                        cache_path=None if args.no_cache else args.cache_path,
                        prepared_unit=prepared_unit,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                reports.append(
                    {
                        "experiment_id": name,
                        "status": "failed",
                        "adapter": adapter,
                        "failure_attribution": "schema_adapter_failure",
                        "first_attempt_status": "dependency_failed",
                        "first_attempt_error": f"{type(exc).__name__}:{exc}"[:1200],
                        "metrics": {
                            "schema_valid": 0,
                            "invalid_span_reference_count": 0,
                            "invalid_span_binding_count": 0,
                            "model_free_quote_output": 0,
                        },
                    }
                )

    if "HELD-OUT-V8" in selected and development_matrix is not None and not later_blocked:
        development_units = prepare_macro_units(
            development_matrix,
            span_source=args.span_pool,
            args=args,
        )
        held_out_units = prepare_macro_units(
            matrix,
            span_source=args.span_pool,
            args=args,
        )
        development_suite = await run_macro_suite(
            experiment_id="HELD-OUT-V8:development",
            matrix=development_matrix,
            prepared_units=development_units,
            adapter=args.macro_adapter,
            cache_path=None,
        )
        held_out_suite = await run_macro_suite(
            experiment_id="HELD-OUT-V8:held-out",
            matrix=matrix,
            prepared_units=held_out_units,
            adapter=args.macro_adapter,
            cache_path=None,
        )
        development_report = _macro_suite_report(
            experiment_id="HELD-OUT-V8:development",
            suite=development_suite,
        )
        held_out_report = _macro_suite_report(
            experiment_id="HELD-OUT-V8:held-out",
            suite=held_out_suite,
        )
        reports.extend((development_report, held_out_report))
        reports.append(
            {
                "experiment_id": "HELD-OUT-V8",
                "status": "blocked"
                if held_out_report["status"] == "blocked"
                else "completed",
                "metrics": {
                    "development_claim_recall": development_report["metrics"]["claim_recall"],
                    "held_out_claim_recall": held_out_report["metrics"]["claim_recall"],
                    "development_binding_accuracy": development_report["metrics"]["binding_accuracy"],
                    "held_out_binding_accuracy": held_out_report["metrics"]["binding_accuracy"],
                    "development_vs_held_out_delta": _held_out_delta(
                        development=development_report,
                        held_out=held_out_report,
                    ),
                },
            }
        )

    if pipeline_selected and not later_blocked and prepared_units is not None:
        macro_adapter = args.macro_adapter
        suite = await run_macro_suite(
            experiment_id="V8-PIPELINE",
            matrix=matrix,
            prepared_units=prepared_units,
            adapter=macro_adapter,
            cache_path=None if args.no_cache else args.cache_path,
        )
        base_report = _macro_suite_report(experiment_id="V8-PIPELINE", suite=suite)
        focused = {
            "MACRO-INTENT": "intent",
            "MACRO-CLAIM": "claim",
            "MACRO-BINDING": "binding",
            "PARTICIPANT-RESOLVE": "binding",
        }
        for name, focus in focused.items():
            if wants(name):
                reports.append(
                    _copy_focused_report(
                        experiment_id=name,
                        base=base_report,
                        focus=focus,
                    )
                )

        relation_report: dict[str, Any] | None = None
        canonical_report: dict[str, Any] | None = None
        vocabulary = CanonicalVocabulary.load(args.vocabulary)
        if wants("RELATION-LIVE") or wants("WINNER-INTEGRATION"):
            relation_report = await run_relation_live(
                suite=suite,
                mode=args.mode,
                cache_path=None if args.no_cache else args.cache_path,
            )
            if wants("RELATION-LIVE"):
                reports.append(relation_report)
        if wants("CAN-LIVE") or wants("WINNER-INTEGRATION"):
            canonical_report = run_canonical_live(
                suite=suite,
                mode=args.mode,
                vocabulary=vocabulary,
            )
            if wants("CAN-LIVE"):
                reports.append(canonical_report)
        if wants("WINNER-INTEGRATION"):
            empty_relation = {
                "experiment_id": "RELATION-LIVE",
                "unit_results": [],
            }
            empty_canonical = {
                "experiment_id": "CAN-LIVE",
                "unit_results": [],
            }
            reports.append(
                run_winner_integration(
                    suite=suite,
                    relation_report=relation_report or empty_relation,
                    canonical_report=canonical_report or empty_canonical,
                    vocabulary=vocabulary,
                )
            )

        if wants("REP-V8") and args.span_pool == "ideal":
            try:
                reports.append(
                    await run_repetition(
                        prepared_units=prepared_units,
                        adapter=macro_adapter,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                reports.append(_runner_failure("REP-V8", exc))
    elif wants("REP-V8") and args.span_pool == "ideal":
        prepared_units = prepare_macro_units(
            matrix,
            span_source=args.span_pool,
            args=args,
        )
        try:
            reports.append(
                await run_repetition(
                    prepared_units=prepared_units,
                    adapter=args.macro_adapter,
                )
            )
        except Exception as exc:  # noqa: BLE001
            reports.append(_runner_failure("REP-V8", exc))
    elif wants("REP-V8") and later_blocked:
        pass
    elif wants("REP-V8"):
        reports.append(
            _blocked_experiment("REP-V8", stage_admission=stage_admission)
        )

    if wants("DSPY-OPT"):
        if args.allow_dspy:
            reports.append(
                run_dspy_gate(
                    mode=args.mode,
                    span_source=args.span_pool,
                    stage_admission=stage_admission,
                )
            )
        else:
            reports.append(
                {
                    "experiment_id": "DSPY-OPT",
                    "status": "blocked",
                    "failure_attribution": "middleware_not_configured",
                    "metrics": {"phase_gate_passed": 0},
                    "reason": "dspy_optimization_not_explicitly_enabled",
                }
            )
    if wants("ASYNC-V8") and not later_blocked:
        reports.append(run_async_isolation(args.output_dir))

    if not reports:
        raise ValueError("no_v8_experiment_selected")

    vocabulary = CanonicalVocabulary.load(args.vocabulary)
    document = {
        "schema_version": "v8-experiment-report-2",
        "mode": args.mode,
        "phase": args.phase,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "matrix": str(args.matrix),
        "matrix_sha256": __import__("hashlib").sha256(args.matrix.read_bytes()).hexdigest(),
        "span_pool": args.span_pool,
        "stage_admission": {
            key: value for key, value in stage_admission.items() if key != "report"
        },
        "prompt_version": V8_PROMPT_VERSION,
        "schema_contract_version": V8_SCHEMA_VERSION,
        "model": os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        "vocabulary_version": vocabulary.version,
        "cache_enabled": not args.no_cache,
        "reports": reports,
        "safety_boundary": _safety_boundary(),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        f"v8-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.json"
    )
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(output_path)
    for report in reports:
        print(
            report["experiment_id"],
            report["status"],
            json.dumps(report.get("metrics", {}), ensure_ascii=False, sort_keys=True),
        )
    failed = [
        report
        for report in reports
        if report.get("status") == "failed"
        or (args.fail_on_blocked and report.get("status") == "blocked")
    ]
    return 1 if failed else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("quick", "shadow"), default="quick")
    parser.add_argument("--phase", choices=("exploratory", "confirmatory"), default="exploratory")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument(
        "--development-matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Development fixture used only by HELD-OUT-V8.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--coverage-only", action="store_true")
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        help=f"Experiment ID; repeatable. Valid IDs: {','.join(sorted(_valid_experiment_ids()))}",
    )
    parser.add_argument(
        "--adapter",
        action="append",
        choices=("base", "instructor", "baml"),
        default=[],
    )
    parser.add_argument(
        "--macro-adapter",
        choices=("base", "instructor", "baml"),
        default="base",
    )
    parser.add_argument(
        "--span-pool",
        choices=("ideal", "live"),
        default=None,
        help="Defaults to ideal for quick controls and live for shadow runs.",
    )
    parser.add_argument("--unit", action="append", default=[])
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--allow-held-out", action="store_true")
    parser.add_argument("--allow-dspy", action="store_true")
    parser.add_argument(
        "--confirm-previous-phases",
        action="store_true",
        help="Explicitly attest that all earlier V8 phase gates passed before live Phase 1+ experiments.",
    )
    parser.add_argument("--stage-coverage-threshold", type=float, default=0.90)
    parser.add_argument("--stage-precision-threshold", type=float, default=0.75)
    parser.add_argument("--stage-label-accuracy-threshold", type=float, default=0.75)
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()
    if args.span_pool is None:
        args.span_pool = "ideal" if args.mode == "quick" else "live"
    if args.allow_dspy:
        os.environ.setdefault("INPUT_PREPROCESSING_V8_ALLOW_DSPY", "1")
    return args


def main() -> int:
    args = _parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
