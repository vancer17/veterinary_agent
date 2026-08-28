"""Deterministic attribution primitives for the ninth V8 diagnostic round.

V9 does not introduce a new runtime pipeline.  It provides small, controlled
diagnostics that separate fixture/evaluator errors, span-pool errors, macro
selection/binding errors, and downstream winner-input errors.

All helpers are development-set and report-only.  Gold injection bypasses a
component only to attribute a failure; it never becomes a production fallback.
"""

from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .runtime_helpers import make_runtime_settings
from .v6_canonical_linker import V6CandidateRetriever
from .v7_microbench import V7MicroAnalyzer
from .v7_run_cache import digest_value
from .v8_contracts import (
    V8EntityCandidate,
    V8MacroSemanticRawOutput,
    V8SpanCandidate,
    V8SpanLabel,
)
from .v8_experiments import _label_for_role, _rate
from .v8_macro_analyzer import (
    V8MacroAnalyzer,
    V8StructuredAdapter,
    V8StructuredClient,
    build_v8_structured_client,
)
from .v8_span_governance import V8SpanGovernance, V8SpanPool
from .vocabulary import CanonicalVocabulary

V9_GOLD_POOL_VERSION = "v9-owner-scoped-gold-20260828-1"
V9_REPORT_VERSION = "v9-attribution-report-1"

SpanIdMode = Literal["role-hinted", "opaque"]
ClientFactory = Callable[[V8StructuredAdapter], V8StructuredClient]


@dataclass(frozen=True)
class V9GoldField:
    """One owner-scoped required field in the development fixture."""

    unit_id: str
    owner_id: str
    role: str
    quote: str
    start: int
    end: int
    label: V8SpanLabel
    coarse_type: str = ""
    act_type: str = ""
    global_first_start: int = -1
    support_start: int | None = None
    support_end: int | None = None
    ambiguous: bool = False


@dataclass(frozen=True)
class V9GoldIntegrity:
    fields: list[V9GoldField]
    findings: list[dict[str, Any]]
    label_conflicts: list[dict[str, Any]]
    role_counts: dict[str, int]


@dataclass(frozen=True)
class V9IdealSpanPool:
    source_id: str
    text: str
    spans: list[V8SpanCandidate]
    role_span_ids: dict[str, str]
    entity_candidates: list[V8EntityCandidate]
    id_mode: SpanIdMode


class V9SpanExtractor(Protocol):
    @property
    def extractor_version(self) -> str: ...

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]: ...


def _all_occurrences(text: str, quote: str) -> list[int]:
    if not quote:
        return []
    result: list[int] = []
    cursor = 0
    while True:
        index = text.find(quote, cursor)
        if index < 0:
            return result
        result.append(index)
        cursor = index + max(1, len(quote))


def _max_overlap(
    start: int,
    end: int,
    candidates: Iterable[tuple[int, int]],
) -> tuple[float, int, int]:
    best = (0.0, -1, -1)
    for candidate_start, candidate_end in candidates:
        overlap = max(0, min(end, candidate_end) - max(start, candidate_start))
        union = max(end, candidate_end) - min(start, candidate_start)
        iou = overlap / union if union else 0.0
        if iou > best[0]:
            best = (iou, candidate_start, candidate_end)
    return best


def _gold_fields_for_unit(unit: dict[str, Any]) -> tuple[list[V9GoldField], list[dict[str, Any]]]:
    text = str(unit["user_text"])
    unit_id = str(unit["unit_id"])
    fields: list[V9GoldField] = []
    findings: list[dict[str, Any]] = []

    for index, act in enumerate(unit.get("expected_acts", [])):
        quote = str(act.get("evidence_quote", ""))
        occurrences = _all_occurrences(text, quote)
        if not occurrences:
            findings.append(
                {
                    "unit_id": unit_id,
                    "owner_id": f"act-{index}",
                    "role": "evidence_quote",
                    "code": "quote_not_found",
                    "quote": quote,
                }
            )
            continue
        start = occurrences[0]
        fields.append(
            V9GoldField(
                unit_id=unit_id,
                owner_id=f"act-{index}",
                role="evidence_quote",
                quote=quote,
                start=start,
                end=start + len(quote),
                label=_label_for_role(
                    "evidence_quote",
                    act_type=str(act.get("act_type", "")),
                ),
                act_type=str(act.get("act_type", "")),
                global_first_start=text.find(quote),
                ambiguous=len(occurrences) > 1,
            )
        )
        if len(occurrences) > 1:
            findings.append(
                {
                    "unit_id": unit_id,
                    "owner_id": f"act-{index}",
                    "role": "evidence_quote",
                    "code": "ambiguous_global_quote",
                    "occurrence_count": len(occurrences),
                }
            )

    ancillary_roles = (
        "target_quote",
        "relation_quote",
        "subject_quote",
        "action_agent_quote",
        "action_recipient_quote",
        "experiencer_quote",
        "object_quote",
        "temporal_quote",
        "measurement_quote",
    )
    participant_roles = {
        "subject_quote",
        "action_agent_quote",
        "action_recipient_quote",
        "experiencer_quote",
    }
    for index, claim in enumerate(unit.get("expected_claims", [])):
        claim_id = str(claim["claim_id"])
        support_quote = str(claim.get("support_quote", ""))
        support_occurrences = _all_occurrences(text, support_quote)
        if not support_occurrences:
            findings.append(
                {
                    "unit_id": unit_id,
                    "owner_id": claim_id,
                    "role": "support_quote",
                    "code": "support_quote_not_found",
                    "quote": support_quote,
                }
            )
            continue

        def occurrence_score(
            support_start: int,
            *,
            support_quote_value: str = support_quote,
            claim_value: dict[str, Any] = claim,
        ) -> int:
            support_end = support_start + len(support_quote_value)
            return sum(
                1
                for role in ancillary_roles
                if claim_value.get(role)
                and claim_value[role] in text[support_start:support_end]
            )

        best_score = max(occurrence_score(item) for item in support_occurrences)
        best_occurrences = [
            item for item in support_occurrences if occurrence_score(item) == best_score
        ]
        support_start = best_occurrences[0]
        support_end = support_start + len(support_quote)
        if len(best_occurrences) > 1:
            findings.append(
                {
                    "unit_id": unit_id,
                    "owner_id": claim_id,
                    "role": "support_quote",
                    "code": "ambiguous_owner_scoped_support",
                    "occurrence_count": len(best_occurrences),
                }
            )

        fields.append(
            V9GoldField(
                unit_id=unit_id,
                owner_id=claim_id,
                role="support_quote",
                quote=support_quote,
                start=support_start,
                end=support_end,
                label=_label_for_role(
                    "support_quote",
                    coarse_type=str(claim.get("coarse_type", "")),
                ),
                coarse_type=str(claim.get("coarse_type", "")),
                support_start=support_start,
                support_end=support_end,
                global_first_start=text.find(support_quote),
                ambiguous=len(best_occurrences) > 1,
            )
        )

        for role in ancillary_roles:
            quote = str(claim.get(role, ""))
            if not quote:
                continue
            scoped_text = text[support_start:support_end]
            occurrences = _all_occurrences(scoped_text, quote)
            outside_support = False
            if not occurrences and role in participant_roles:
                # Participants may be omitted from a state support phrase and
                # still resolve from TurnContext. Pick the nearest preceding
                # global mention for the owner-scoped diagnostic pool.
                global_occurrences = _all_occurrences(text, quote)
                if global_occurrences:
                    outside_support = True
                    occurrences = [
                        min(
                            global_occurrences,
                            key=lambda item: (
                                abs(support_start - (item + len(quote))),
                                item,
                            ),
                        )
                    ]
            if not occurrences:
                findings.append(
                    {
                        "unit_id": unit_id,
                        "owner_id": claim_id,
                        "role": role,
                        "code": "role_not_inside_owner_scoped_support",
                        "quote": quote,
                        "support_start": support_start,
                        "support_end": support_end,
                    }
                )
                continue
            if len(occurrences) > 1:
                findings.append(
                    {
                        "unit_id": unit_id,
                        "owner_id": claim_id,
                        "role": role,
                        "code": "ambiguous_role_inside_support",
                        "occurrence_count": len(occurrences),
                    }
                )
            if outside_support:
                start = occurrences[0]
            else:
                start = support_start + occurrences[0]
            fields.append(
                V9GoldField(
                    unit_id=unit_id,
                    owner_id=claim_id,
                    role=role,
                    quote=quote,
                    start=start,
                    end=start + len(quote),
                    label=_label_for_role(
                        role,
                        coarse_type=str(claim.get("coarse_type", "")),
                    ),
                    coarse_type=str(claim.get("coarse_type", "")),
                    support_start=support_start,
                    support_end=support_end,
                    global_first_start=text.find(quote),
                    ambiguous=len(occurrences) > 1,
                )
            )

    return fields, findings


def audit_v9_gold_integrity(matrix: dict[str, Any]) -> V9GoldIntegrity:
    """Build owner-scoped gold fields and expose fixture/evaluator risks."""

    fields: list[V9GoldField] = []
    findings: list[dict[str, Any]] = []
    for unit in matrix["macro_units"]:
        unit_fields, unit_findings = _gold_fields_for_unit(unit)
        fields.extend(unit_fields)
        findings.extend(unit_findings)

    boundary_labels: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for field in fields:
        boundary_labels[(field.unit_id, field.start, field.end)].add(
            field.label.value
        )
    conflicts = [
        {
            "unit_id": unit_id,
            "start": start,
            "end": end,
            "labels": sorted(labels),
            "roles": sorted(
                field.role
                for field in fields
                if field.unit_id == unit_id
                and field.start == start
                and field.end == end
            ),
        }
        for (unit_id, start, end), labels in sorted(boundary_labels.items())
        if len(labels) > 1
    ]
    for conflict in conflicts:
        findings.append(
            {
                "unit_id": conflict["unit_id"],
                "role": "boundary_label_conflict",
                "code": "one_boundary_multiple_expected_labels",
                **{key: value for key, value in conflict.items() if key != "roles"},
            }
        )

    wrong_occurrences = [
        field
        for field in fields
        if field.support_start is not None
        and field.global_first_start != field.start
    ]
    for field in wrong_occurrences:
        findings.append(
            {
                "unit_id": field.unit_id,
                "owner_id": field.owner_id,
                "role": field.role,
                "code": "global_first_occurrence_is_not_owner_scoped",
                "owner_start": field.start,
                "global_first_start": field.global_first_start,
                "quote": field.quote,
            }
        )

    return V9GoldIntegrity(
        fields=fields,
        findings=findings,
        label_conflicts=conflicts,
        role_counts=dict(sorted(Counter(field.role for field in fields).items())),
    )


def gold_integrity_report(matrix: dict[str, Any]) -> dict[str, Any]:
    integrity = audit_v9_gold_integrity(matrix)
    conflict_keys = {
        (item["unit_id"], item["start"], item["end"])
        for item in integrity.label_conflicts
    }
    evaluable_fields = [
        field
        for field in integrity.fields
        if (field.unit_id, field.start, field.end) not in conflict_keys
    ]
    wrong_occurrence_count = sum(
        finding["code"] == "global_first_occurrence_is_not_owner_scoped"
        for finding in integrity.findings
    )
    containment_violation_count = sum(
        finding["code"] == "role_not_inside_owner_scoped_support"
        for finding in integrity.findings
    )
    return {
        "experiment_id": "ATT-GOLD-INTEGRITY",
        "status": "completed_with_findings" if integrity.findings else "completed",
        "diagnostic_only": True,
        "pool_version": V9_GOLD_POOL_VERSION,
        "metrics": {
            "required_field_count": len(integrity.fields),
            "unique_boundary_count": len(
                {(field.unit_id, field.start, field.end) for field in integrity.fields}
            ),
            "finding_count": len(integrity.findings),
            "wrong_occurrence_count": wrong_occurrence_count,
            "support_containment_violation_count": containment_violation_count,
            "conflicting_label_boundary_count": len(integrity.label_conflicts),
            "ambiguous_occurrence_count": sum(
                finding["code"].startswith("ambiguous_")
                for finding in integrity.findings
            ),
            "label_evaluable_field_count": len(evaluable_fields),
            "label_evaluable_rate": _rate(len(evaluable_fields), len(integrity.fields)),
        },
        "role_counts": integrity.role_counts,
        "findings": integrity.findings,
        "label_conflicts": integrity.label_conflicts,
    }


def build_v9_ideal_span_pool(
    unit: dict[str, Any],
    *,
    id_mode: SpanIdMode = "role-hinted",
) -> V9IdealSpanPool:
    fields, findings = _gold_fields_for_unit(unit)
    blocking = [
        item
        for item in findings
        if item["code"]
        not in {
            "ambiguous_global_quote",
            "ambiguous_owner_scoped_support",
            "ambiguous_role_inside_support",
            "one_boundary_multiple_expected_labels",
        }
    ]
    if blocking:
        raise ValueError(f"v9_gold_fixture_invalid:{unit.get('unit_id', '')}")

    source_id = str(unit["unit_id"])
    text = str(unit["user_text"])
    by_boundary: dict[tuple[int, int, str], V9GoldField] = {}
    for field in fields:
        by_boundary.setdefault(
            (field.start, field.end, field.label.value),
            field,
        )

    ordered_fields = sorted(
        by_boundary.values(), key=lambda item: (item.start, item.end, item.label.value)
    )
    spans: list[V8SpanCandidate] = []
    role_span_ids: dict[str, str] = {}
    opaque_ids = {
        (field.start, field.end, field.label.value): f"{source_id}:v9-span-{index:06d}"
        for index, field in enumerate(ordered_fields, start=1)
    }
    for field in ordered_fields:
        if id_mode == "role-hinted":
            span_id = (
                f"{source_id}:v9-{field.owner_id}-{field.role}-"
                f"{field.start:06d}-{field.end:06d}"
            )
        else:
            span_id = opaque_ids[(field.start, field.end, field.label.value)]
        spans.append(
            V8SpanCandidate(
                span_id=span_id,
                source_id=source_id,
                source_block_id="block-001",
                start=field.start,
                end=field.end,
                text=text[field.start : field.end],
                label=field.label,
                score=1.0,
                extractor_version=V9_GOLD_POOL_VERSION,
            )
        )

    field_to_span: dict[tuple[int, int, str], str] = {
        (field.start, field.end, field.label.value): span.span_id
        for field, span in zip(ordered_fields, spans, strict=True)
    }
    for field in fields:
        role_span_ids[f"{field.owner_id}:{field.role}"] = field_to_span[
            (field.start, field.end, field.label.value)
        ]

    entities = [
        V8EntityCandidate.model_validate(raw)
        for raw in unit.get("entity_candidates", [])
    ]
    return V9IdealSpanPool(
        source_id=source_id,
        text=text,
        spans=spans,
        role_span_ids=role_span_ids,
        entity_candidates=entities,
        id_mode=id_mode,
    )


def _span_intake_errors(spans: list[V8SpanCandidate], text: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for span in spans:
        valid = (
            0 <= span.start < span.end <= len(text)
            and text[span.start : span.end] == span.text
        )
        if not valid:
            errors.append(
                {
                    "span_id": span.span_id,
                    "code": "offset_text_mismatch",
                    "start": span.start,
                    "end": span.end,
                }
            )
    return errors


def evaluate_v9_span_pool(
    matrix: dict[str, Any],
    *,
    extractor: V9SpanExtractor,
    integrity: V9GoldIntegrity | None = None,
) -> dict[str, Any]:
    """Evaluate exact, relaxed, and label-attributable span metrics."""

    if integrity is None:
        integrity = audit_v9_gold_integrity(matrix)
    predicted_by_unit: dict[str, list[V8SpanCandidate]] = {}
    intake_errors: list[dict[str, Any]] = []
    latencies: list[int] = []
    for unit in matrix["macro_units"]:
        unit_id = str(unit["unit_id"])
        text = str(unit["user_text"])
        started = time.perf_counter()
        spans = extractor.extract(
            source_id=unit_id,
            source_block_id="block-001",
            text=text,
        )
        latencies.append(int((time.perf_counter() - started) * 1000))
        predicted_by_unit[unit_id] = spans
        intake_errors.extend(_span_intake_errors(spans, text))

    conflict_keys = {
        (item["unit_id"], item["start"], item["end"])
        for item in integrity.label_conflicts
    }
    details: list[dict[str, Any]] = []
    matched_predicted_ids: set[str] = set()
    confusion: Counter[tuple[str, str]] = Counter()
    role_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "exact": 0, "near": 0, "label_correct": 0}
    )
    exact_count = 0
    label_correct_count = 0
    evaluable_label_count = 0
    near_count = 0

    for field in integrity.fields:
        candidates = predicted_by_unit.get(field.unit_id, [])
        boundaries = [(span.start, span.end) for span in candidates]
        exact = [
            span
            for span in candidates
            if span.start == field.start and span.end == field.end
        ]
        iou, near_start, near_end = _max_overlap(
            field.start,
            field.end,
            boundaries,
        )
        label_exact = [span for span in exact if span.label is field.label]
        best_exact = max(
            label_exact or exact,
            key=lambda span: span.score,
            default=None,
        )
        if best_exact is not None:
            exact_count += 1
            matched_predicted_ids.add(best_exact.span_id)
            if best_exact.label is field.label:
                label_correct_count += 1
                confusion[(field.label.value, field.label.value)] += 1
            else:
                confusion[(field.label.value, best_exact.label.value)] += 1
        else:
            confusion[(field.label.value, "__missing__")] += 1
        if (field.unit_id, field.start, field.end) not in conflict_keys:
            evaluable_label_count += 1
        if not exact and iou > 0:
            near_count += 1
        role = role_stats[field.role]
        role["expected"] += 1
        role["exact"] += int(best_exact is not None)
        role["near"] += int(best_exact is None and iou > 0)
        role["label_correct"] += int(
            best_exact is not None and best_exact.label is field.label
        )
        details.append(
            {
                "unit_id": field.unit_id,
                "owner_id": field.owner_id,
                "role": field.role,
                "quote": field.quote,
                "expected_label": field.label.value,
                "expected_start": field.start,
                "expected_end": field.end,
                "exact_boundary": best_exact is not None,
                "predicted_span_id": best_exact.span_id if best_exact else None,
                "predicted_label": best_exact.label.value if best_exact else None,
                "best_iou": round(iou, 6),
                "best_overlap_start": near_start,
                "best_overlap_end": near_end,
                "attribution": (
                    "span_recall_miss"
                    if best_exact is None and iou == 0
                    else "span_boundary_error"
                    if best_exact is None
                    else "span_label_error"
                    if best_exact.label is not field.label
                    else "correct"
                ),
            }
        )

    predicted_count = sum(len(items) for items in predicted_by_unit.values())
    unmatched_predictions = [
        span
        for spans in predicted_by_unit.values()
        for span in spans
        if span.span_id not in matched_predicted_ids
    ]
    return {
        "experiment_id": "ATT-SPAN-POOL",
        "status": "completed",
        "diagnostic_only": True,
        "extractor_version": extractor.extractor_version,
        "metrics": {
            "required_field_count": len(integrity.fields),
            "predicted_span_count": predicted_count,
            "boundary_match_count": exact_count,
            "boundary_precision": _rate(len(matched_predicted_ids), predicted_count),
            "boundary_recall": _rate(exact_count, len(integrity.fields)),
            "near_boundary_count": near_count,
            "near_boundary_or_exact_rate": _rate(
                exact_count + near_count,
                len(integrity.fields),
            ),
            "label_correct_on_exact_count": label_correct_count,
            "label_accuracy_on_exact": _rate(
                label_correct_count,
                exact_count,
            ),
            "label_evaluable_field_count": evaluable_label_count,
            "label_conflict_field_count": len(integrity.fields) - evaluable_label_count,
            "span_intake_error_count": len(intake_errors),
            "p50_ms": _p50(latencies),
            "p95_ms": _p95(latencies),
        },
        "role_metrics": {
            role: {
                **stats,
                "recall": _rate(stats["exact"], stats["expected"]),
            }
            for role, stats in sorted(role_stats.items())
        },
        "label_confusion_matrix": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in sorted(confusion.items())
        ],
        "field_results": details,
        "unmatched_predictions": [
            {
                "unit_id": span.source_id,
                "span_id": span.span_id,
                "start": span.start,
                "end": span.end,
                "text": span.text,
                "label": span.label.value,
                "score": span.score,
            }
            for span in unmatched_predictions
        ],
        "span_intake_errors": intake_errors,
    }


def _p50(values: list[int]) -> float:
    return float(values[(len(values) - 1) // 2]) if values else 0.0


def _p95(values: list[int]) -> float:
    return float(values[min(len(values) - 1, round(0.95 * (len(values) - 1)))]) if values else 0.0


def _execution_report(execution: Any) -> dict[str, Any]:
    return {
        "attempt_count": execution.attempt_count,
        "first_attempt_status": execution.first_attempt_status,
        "first_attempt_error": execution.first_attempt_error,
        "cache_hit": execution.cache_hit,
        "latency_ms": execution.latency_ms,
        "model_call_count": getattr(execution, "model_call_count", execution.attempt_count),
        "internal_retry_limit": getattr(execution, "internal_retry_limit", 0),
        "token_count_available": bool(
            getattr(execution, "token_count_available", False)
        ),
        "cost_available": bool(getattr(execution, "cost_available", False)),
    }


def _raw_output_report(output: V8MacroSemanticRawOutput) -> dict[str, Any]:
    return {
        "acts": [item.model_dump(mode="json") for item in output.acts],
        "claims": [item.model_dump(mode="json") for item in output.claims],
    }


def _expected_claim_key(claim: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(claim["statement_type"]),
        str(claim["coarse_type"]),
        str(claim["support_quote"]),
        str(claim["target_quote"]),
    )


def _actual_claim_key(claim: Any) -> tuple[str, str, str, str]:
    return (
        claim.statement_type.value,
        claim.coarse_type.value,
        claim.support.quote,
        claim.target.quote,
    )


@dataclass(frozen=True)
class V9MacroRun:
    unit: dict[str, Any]
    pool: V9IdealSpanPool
    execution: Any
    governed: Any


async def run_v9_macro_attribution(
    *,
    matrix: dict[str, Any],
    adapter: V8StructuredAdapter,
    id_mode: SpanIdMode,
    client_factory: ClientFactory = build_v8_structured_client,
    unit_ids: list[str] | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Run one macro call per unit and separate act/claim/binding failures."""

    client = client_factory(adapter)
    analyzer = V8MacroAnalyzer(
        client=client,
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=_optional_cache(cache_path),
    )
    runs: list[V9MacroRun] = []
    for unit in matrix["macro_units"]:
        if unit_ids and str(unit["unit_id"]) not in set(unit_ids):
            continue
        pool = build_v9_ideal_span_pool(unit, id_mode=id_mode)
        governance = V8SpanGovernance(
            V8SpanPool(sources={pool.source_id: pool.text}, spans=pool.spans)
        )
        execution = await analyzer.run(
            experiment_id=f"V9-MACRO-{id_mode.upper()}",
            user_text=pool.text,
            spans=pool.spans,
            turn_context={
                "unit_id": pool.source_id,
                "source_blocks": [
                    {
                        "source_id": pool.source_id,
                        "source_block_id": "block-001",
                        "text": pool.text,
                    }
                ],
            },
        )
        governed = governance.govern(
            execution.output,
            entity_candidates=pool.entity_candidates,
        )
        runs.append(
            V9MacroRun(
                unit=unit,
                pool=pool,
                execution=execution,
                governed=governed,
            )
        )

    unit_reports = [
        _evaluate_v9_macro_run(run) for run in runs
    ]
    aggregate = _aggregate_macro_reports(unit_reports)
    return {
        "experiment_id": "ATT-MACRO-IDEAL-POOL",
        "status": "completed",
        "diagnostic_only": True,
        "span_pool": {
            "source": "owner-scoped-gold",
            "id_mode": id_mode,
            "version": V9_GOLD_POOL_VERSION,
        },
        "adapter": client.adapter_name,
        **aggregate,
        "unit_results": unit_reports,
    }


def _optional_cache(path: Path | None) -> Any:
    if path is None:
        return None
    from .v7_run_cache import V7RunCache

    return V7RunCache(path)


def _evaluate_v9_macro_run(run: V9MacroRun) -> dict[str, Any]:
    governance = V8SpanGovernance(
        V8SpanPool(
            sources={run.pool.source_id: run.pool.text},
            spans=run.pool.spans,
        )
    )
    expected_acts = [
        (str(act["act_type"]), str(act["evidence_quote"]))
        for act in run.unit.get("expected_acts", [])
    ]
    expected_available = Counter(expected_acts)
    act_results: list[dict[str, Any]] = []
    matched_acts = 0
    valid_evidence = 0
    for act in run.execution.output.acts:
        binding = governance.resolve_span_ids(
            span_ids=act.evidence_span_ids,
            required=True,
        )
        quote = binding.quote if binding is not None and binding.status == "resolved" else ""
        valid = bool(quote)
        key = (act.act_type.value, quote)
        matched = expected_available[key] > 0
        if matched:
            expected_available[key] -= 1
            matched_acts += 1
        if valid:
            valid_evidence += 1
        act_results.append(
            {
                "act_type": act.act_type.value,
                "evidence_span_ids": act.evidence_span_ids,
                "resolved_quote": quote,
                "evidence_valid": valid,
                "matched_expected": matched,
                "type_only_match": any(
                    expected_type == act.act_type.value
                    for expected_type, _ in expected_acts
                ),
            }
        )

    expected_claims = list(run.unit.get("expected_claims", []))
    expected_claim_available = Counter(
        _expected_claim_key(claim) for claim in expected_claims
    )
    matched_by_expected: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    claim_results: list[dict[str, Any]] = []
    matched_claims = 0
    for claim in run.governed.governed_claims:
        claim_key = _actual_claim_key(claim)
        matched = expected_claim_available[claim_key] > 0
        if matched:
            expected_claim_available[claim_key] -= 1
            matched_claims += 1
            matched_by_expected[claim_key] = {
                "expected": next(
                    item
                    for item in expected_claims
                    if _expected_claim_key(item) == claim_key
                ),
                "actual": claim,
            }
        expected_support = next(
            (
                str(item["support_quote"])
                for item in expected_claims
                if str(item["support_quote"]) == claim.support.quote
            ),
            "",
        )
        expected_target = next(
            (
                str(item["target_quote"])
                for item in expected_claims
                if str(item["target_quote"]) == claim.target.quote
            ),
            "",
        )
        claim_results.append(
            {
                "claim_id": claim.claim_id,
                "statement_type": claim.statement_type.value,
                "coarse_type": claim.coarse_type.value,
                "support_quote": claim.support.quote,
                "target_quote": claim.target.quote,
                "projection_ready": claim.projection_ready,
                "matched_expected": matched,
                "support_only_match": bool(expected_support and not expected_target),
                "target_only_match": bool(expected_target and not expected_support),
                "relation_quote": claim.relation.quote if claim.relation else None,
                "temporal_quote": claim.temporal.quote if claim.temporal else None,
                "measurement_quote": claim.measurement.quote if claim.measurement else None,
            }
        )

    binding_expected = 0
    binding_correct = 0
    optional_missing = 0
    participant_expected = 0
    participant_mention_correct = 0
    participant_reference_correct = 0
    entity_bindings: list[dict[str, Any]] = []
    optional_roles = (
        ("relation_quote", "relation"),
        ("temporal_quote", "temporal"),
        ("measurement_quote", "measurement"),
        ("object_quote", "object_mention"),
    )
    for expected_key, match in matched_by_expected.items():
        expected = match["expected"]
        actual = match["actual"]
        for role, attribute in optional_roles:
            if expected.get(role):
                binding_expected += 1
                actual_binding = getattr(actual, attribute)
                actual_quote = actual_binding.quote if actual_binding else ""
                if actual_quote == str(expected[role]):
                    binding_correct += 1
                elif not actual_quote:
                    optional_missing += 1
        participant_roles = (
            ("subject_quote", "expected_subject_reference", "subject"),
            (
                "action_agent_quote",
                "expected_action_agent_reference",
                "action_agent",
            ),
            (
                "action_recipient_quote",
                "expected_action_recipient_reference",
                "action_recipient",
            ),
            ("experiencer_quote", "expected_experiencer_reference", "experiencer"),
        )
        for quote_role, reference_role, attribute in participant_roles:
            if expected.get(quote_role):
                participant_expected += 1
                entity = getattr(actual, attribute)
                mention_ok = entity is not None and entity.mention_quote == str(
                    expected[quote_role]
                )
                expected_reference = str(expected.get(reference_role, ""))
                expected_resolution = str(
                    expected.get("expected_experiencer_resolution", "")
                )
                reference_ok = entity is not None and (
                    entity.selected_reference_id == expected_reference
                    if expected_reference
                    else entity.resolution_status == expected_resolution
                )
                participant_mention_correct += int(mention_ok)
                participant_reference_correct += int(reference_ok)
                entity_bindings.append(
                    {
                        "claim_key": list(expected_key),
                        "role": quote_role,
                        "expected_quote": str(expected[quote_role]),
                        "actual_quote": entity.mention_quote if entity else None,
                        "expected_reference": expected_reference or expected_resolution,
                        "actual_reference": entity.selected_reference_id
                        if entity
                        else None,
                        "actual_resolution": entity.resolution_status if entity else None,
                    }
                )

    metrics = {
        "raw_act_count": len(run.execution.output.acts),
        "expected_act_count": len(expected_acts),
        "matched_act_count": matched_acts,
        "act_precision": _rate(matched_acts, len(run.execution.output.acts)),
        "act_recall": _rate(matched_acts, len(expected_acts)),
        "evidence_span_valid_rate": _rate(
            valid_evidence,
            len(run.execution.output.acts),
        ),
        "raw_claim_count": len(run.execution.output.claims),
        "governed_claim_count": len(run.governed.governed_claims),
        "expected_claim_count": len(expected_claims),
        "matched_claim_count": matched_claims,
        "claim_precision": _rate(matched_claims, len(run.governed.governed_claims)),
        "claim_recall": _rate(matched_claims, len(expected_claims)),
        "binding_expected_count": binding_expected,
        "binding_accuracy": _rate(binding_correct, binding_expected),
        "optional_binding_missing_count": optional_missing,
        "participant_expected_count": participant_expected,
        "participant_mention_recall": _rate(
            participant_mention_correct,
            participant_expected,
        ),
        "participant_resolution_accuracy": _rate(
            participant_reference_correct,
            participant_expected,
        ),
        "invalid_span_reference_count": len(run.governed.invalid_span_references),
        "invalid_span_binding_count": len(run.governed.invalid_span_bindings),
        "model_free_quote_output": 0,
    }
    return {
        "unit_id": run.pool.source_id,
        "execution": _execution_report(run.execution),
        "metrics": metrics,
        "raw_output": _raw_output_report(run.execution.output),
        "act_results": act_results,
        "claim_results": claim_results,
        "entity_binding_results": entity_bindings,
        "gates": [gate.model_dump(mode="json") for gate in run.governed.gates],
    }


def _aggregate_macro_reports(unit_reports: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = sorted(
        {
            metric_name
            for report in unit_reports
            for metric_name in report["metrics"]
            if not metric_name.endswith("_count")
        }
    )
    metrics: dict[str, float] = {}
    for metric_name in metric_names:
        values = [
            float(report["metrics"].get(metric_name, 0.0))
            for report in unit_reports
        ]
        metrics[f"mean_{metric_name}"] = (
            sum(values) / len(values) if values else 0.0
        )
    count_names = sorted(
        {
            metric_name
            for report in unit_reports
            for metric_name in report["metrics"]
            if metric_name.endswith("_count")
        }
    )
    for metric_name in count_names:
        metrics[f"total_{metric_name}"] = sum(
            int(report["metrics"].get(metric_name, 0)) for report in unit_reports
        )
    return {"metrics": metrics}


async def run_v9_relation_gold(
    *,
    matrix: dict[str, Any],
    mode: Literal["quick", "shadow"],
    cache_path: Path | None = None,
    calibration_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    integrity = audit_v9_gold_integrity(matrix)
    fields = {
        (field.unit_id, field.owner_id, field.role): field
        for field in integrity.fields
    }
    records: list[dict[str, Any]] = []
    for unit in matrix["macro_units"]:
        unit_id = str(unit["unit_id"])
        for claim in unit.get("expected_claims", []):
            if not claim.get("expected_relation"):
                continue
            claim_id = str(claim["claim_id"])
            relation = fields.get((unit_id, claim_id, "relation_quote"))
            target = fields[(unit_id, claim_id, "target_quote")]
            input_available = relation is not None
            records.append(
                {
                    "unit_id": unit_id,
                    "claim_id": claim_id,
                    "target_quote": target.quote,
                    "relation_quote": relation.quote if relation else "",
                    "expected_relation": str(claim["expected_relation"]),
                    "input_available": input_available,
                    "failure_attribution": None
                    if input_available
                    else "gold_relation_span_missing",
                }
            )

    available_records = [record for record in records if record["input_available"]]
    calibration_payload = [
        {
            "unit_id": f"v7-calibration:{item['unit_id']}",
            "target_quote": str(item["target_quote"]),
            "relation_quote": str(item["relation_quote"]),
        }
        for item in (calibration_units or [])
    ]

    if mode == "shadow":
        from vet_agent.runtime import QwenClient

        analyzer = V7MicroAnalyzer(
            qwen=QwenClient(make_runtime_settings()),
            model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
            cache=_optional_cache(cache_path),
        )
        v8_payload = [
            {
                "unit_id": f"{record['unit_id']}:{record['claim_id']}",
                "target_quote": record["target_quote"],
                "relation_quote": record["relation_quote"],
            }
            for record in available_records
        ]
        execution = await analyzer.run_relation(
            units=calibration_payload + v8_payload,
            turn_context_digest=digest_value({"v9": "relation-gold-injection"}),
        )
        actual = {
            item.unit_id: item.relation.value for item in execution.output.results
        }
        for record in available_records:
            key = f"{record['unit_id']}:{record['claim_id']}"
            record["actual_relation"] = actual.get(key, "")
        execution_report = _execution_report(execution)
        if calibration_payload:
            calibration_actual = {
                item.unit_id: item.relation.value for item in execution.output.results
            }
            calibration_expected = {
                item["unit_id"]: str(item["expected_relation"])
                for item in calibration_units or []
            }
    else:
        for record in records:
            record["actual_relation"] = record["expected_relation"]
        execution_report = {
            "attempt_count": 0,
            "first_attempt_status": "ideal_control",
            "cache_hit": False,
            "latency_ms": 0,
            "model_call_count": 0,
        }

    correct = sum(
        record["actual_relation"] == record["expected_relation"]
        for record in available_records
    )
    return {
        "experiment_id": "ATT-RELATION-GOLD",
        "status": "completed",
        "diagnostic_only": True,
        "input_source": "owner-scoped-gold-relation-span",
        "calibration_unit_count": len(calibration_units or []),
        "mode": mode,
        "execution": execution_report,
        "metrics": {
            "gold_relation_field_count": len(records),
            "gold_relation_span_missing_count": len(records) - len(available_records),
            "relation_input_availability": _rate(
                len(available_records),
                len(records),
            ),
            "relation_accuracy": _rate(correct, len(available_records)),
            "calibration_relation_accuracy": _rate(
                sum(
                    calibration_actual.get(f"v7-calibration:{unit_id}")
                    == expected_relation
                    for unit_id, expected_relation in calibration_expected.items()
                ),
                len(calibration_payload),
            )
            if calibration_payload
            else None,
        },
        "unit_results": records,
    }


def run_v9_canonical_gold(
    *,
    matrix: dict[str, Any],
    vocabulary: CanonicalVocabulary,
    mode: Literal["quick", "shadow"],
) -> dict[str, Any]:
    integrity = audit_v9_gold_integrity(matrix)
    fields = {
        (field.unit_id, field.owner_id, field.role): field
        for field in integrity.fields
    }
    if mode == "shadow":
        from vet_agent.runtime import QwenEmbeddingClient

        retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=QwenEmbeddingClient(make_runtime_settings()),
        )
        recall_version = retriever.recall_version
    else:
        retriever = V6CandidateRetriever(
            vocabulary=vocabulary,
            embeddings=_UnavailableEmbeddingControl(),
        )
        recall_version = "v9-gold-target-embedding-disabled-control"

    records: list[dict[str, Any]] = []
    for unit in matrix["macro_units"]:
        unit_id = str(unit["unit_id"])
        for claim in unit.get("expected_claims", []):
            expected_ids = claim.get("expected_canonical_ids")
            if not expected_ids:
                continue
            claim_id = str(claim["claim_id"])
            target = fields[(unit_id, claim_id, "target_quote")]
            if mode == "shadow":
                candidate_set = retriever.recall(
                    claim_id=claim_id,
                    target_quote=target.quote,
                    coarse_type=str(claim["coarse_type"]),
                )
                candidate_ids = [item.canonical_id for item in candidate_set.candidates]
            else:
                vocabulary_ids = {item.canonical_id for item in vocabulary.terms}
                candidate_ids = sorted(set(expected_ids) & vocabulary_ids)
            expected_set = set(expected_ids)
            records.append(
                {
                    "unit_id": unit_id,
                    "claim_id": claim_id,
                    "target_quote": target.quote,
                    "expected_canonical_ids": list(expected_ids),
                    "candidate_ids": candidate_ids,
                    "top_candidate_id": candidate_ids[0] if candidate_ids else None,
                    "passed": bool(expected_set & set(candidate_ids)),
                }
            )
    recalled = sum(record["passed"] for record in records)
    top_correct = sum(
        record["top_candidate_id"] in set(record["expected_canonical_ids"])
        for record in records
    )
    return {
        "experiment_id": "ATT-CANONICAL-GOLD",
        "status": "completed",
        "diagnostic_only": True,
        "input_source": "owner-scoped-gold-target-span",
        "vocabulary_version": vocabulary.version,
        "recall_version": recall_version,
        "mode": mode,
        "metrics": {
            "gold_target_field_count": len(records),
            "candidate_recall": _rate(recalled, len(records)),
            "canonical_accuracy": _rate(top_correct, len(records)),
            "under_confirmation_count": len(records) - recalled,
            "no_candidate_count": sum(not record["candidate_ids"] for record in records),
        },
        "unit_results": records,
    }


class _UnavailableEmbeddingControl:
    @property
    def available(self) -> bool:
        return False

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("v9_embedding_control_unavailable")


def run_v9_participant_gold(matrix: dict[str, Any]) -> dict[str, Any]:
    integrity = audit_v9_gold_integrity(matrix)
    participant_roles = {
        "subject_quote",
        "action_agent_quote",
        "action_recipient_quote",
        "experiencer_quote",
    }
    fields = [
        field for field in integrity.fields if field.role in participant_roles
    ]
    records: list[dict[str, Any]] = []
    entity_by_unit = {
        str(unit["unit_id"]): [
            V8EntityCandidate.model_validate(raw)
            for raw in unit.get("entity_candidates", [])
        ]
        for unit in matrix["macro_units"]
    }
    source_by_unit = {
        str(unit["unit_id"]): str(unit["user_text"]) for unit in matrix["macro_units"]
    }
    spans_by_unit: dict[str, list[V8SpanCandidate]] = defaultdict(list)
    for field in fields:
        spans_by_unit[field.unit_id].append(
            V8SpanCandidate(
                span_id=f"{field.unit_id}:participant:{field.owner_id}:{field.role}",
                source_id=field.unit_id,
                source_block_id="block-001",
                start=field.start,
                end=field.end,
                text=field.quote,
                label=field.label,
                score=1.0,
                extractor_version=V9_GOLD_POOL_VERSION,
            )
        )
    for field in fields:
        pool = V8SpanPool(
            sources={field.unit_id: source_by_unit[field.unit_id]},
            spans=spans_by_unit[field.unit_id],
        )
        governance = V8SpanGovernance(pool)
        binding = governance.resolve_span_ids(
            span_ids=[f"{field.unit_id}:participant:{field.owner_id}:{field.role}"]
        )
        assert binding is not None
        resolved = governance.resolve_entity(
            binding=binding,
            candidates=entity_by_unit[field.unit_id],
        )
        expected_reference = ""
        expected_resolution = ""
        for unit in matrix["macro_units"]:
            if str(unit["unit_id"]) != field.unit_id:
                continue
            for claim in unit.get("expected_claims", []):
                if str(claim["claim_id"]) != field.owner_id:
                    continue
                reference_role = {
                    "subject_quote": "expected_subject_reference",
                    "action_agent_quote": "expected_action_agent_reference",
                    "action_recipient_quote": "expected_action_recipient_reference",
                    "experiencer_quote": "expected_experiencer_reference",
                }[field.role]
                expected_reference = str(claim.get(reference_role, ""))
                if field.role == "experiencer_quote":
                    expected_resolution = str(
                        claim.get("expected_experiencer_resolution", "")
                    )
        passed = bool(
            expected_reference
            and resolved.selected_reference_id == expected_reference
        ) or bool(
            not expected_reference
            and expected_resolution
            and resolved.resolution_status == expected_resolution
        )
        records.append(
            {
                "unit_id": field.unit_id,
                "owner_id": field.owner_id,
                "role": field.role,
                "quote": field.quote,
                "expected_reference": expected_reference or expected_resolution,
                "selected_reference_id": resolved.selected_reference_id,
                "resolution_status": resolved.resolution_status,
                "passed": passed,
            }
        )
    correct = sum(record["passed"] for record in records)
    return {
        "experiment_id": "ATT-PARTICIPANT-GOLD",
        "status": "completed",
        "diagnostic_only": True,
        "input_source": "owner-scoped-gold-participant-span",
        "metrics": {
            "gold_participant_field_count": len(records),
            "participant_resolution_accuracy": _rate(correct, len(records)),
            "resolved_empty_count": sum(
                record["resolution_status"] == "resolved"
                and not record["selected_reference_id"]
                for record in records
            ),
        },
        "unit_results": records,
    }


async def run_v9_repeat_attribution(
    *,
    matrix: dict[str, Any],
    unit_id: str,
    adapter: V8StructuredAdapter,
    run_count: int = 3,
    client_factory: ClientFactory = build_v8_structured_client,
) -> dict[str, Any]:
    if run_count < 2:
        raise ValueError("v9_repeat_run_count_must_be_at_least_2")
    unit = next(
        (item for item in matrix["macro_units"] if str(item["unit_id"]) == unit_id),
        None,
    )
    if unit is None:
        raise ValueError(f"v9_repeat_unit_not_found:{unit_id}")
    pool = build_v9_ideal_span_pool(unit, id_mode="opaque")
    client = client_factory(adapter)
    analyzer = V8MacroAnalyzer(
        client=client,
        model=os.getenv("INPUT_PREPROCESSING_V8_MODEL", "qwen-plus"),
        cache=None,
    )
    runs: list[dict[str, Any]] = []
    raw_signatures: list[str] = []
    semantic_signatures: list[str] = []
    act_signatures: list[str] = []
    claim_signatures: list[str] = []
    binding_signatures: list[str] = []
    for index in range(run_count):
        execution = await analyzer.run(
            experiment_id=f"V9-REP-COLD-{index + 1}",
            user_text=pool.text,
            spans=pool.spans,
            turn_context={"unit_id": pool.source_id},
        )
        governance = V8SpanGovernance(
            V8SpanPool(sources={pool.source_id: pool.text}, spans=pool.spans)
        )
        governed = governance.govern(execution.output, entity_candidates=pool.entity_candidates)
        raw = execution.output.model_dump(mode="json")
        raw_signature = digest_value(raw)
        act_signature = digest_value(raw["acts"])
        claim_signature = digest_value(raw["claims"])
        binding_signature = digest_value(
            [claim.model_dump(mode="json") for claim in governed.governed_claims]
        )
        semantic_signature = digest_value([raw_signature, binding_signature])
        raw_signatures.append(raw_signature)
        semantic_signatures.append(semantic_signature)
        act_signatures.append(act_signature)
        claim_signatures.append(claim_signature)
        binding_signatures.append(binding_signature)
        runs.append(
            {
                "run_index": index + 1,
                "execution": _execution_report(execution),
                "raw_signature": raw_signature,
                "semantic_signature": semantic_signature,
                "act_signature": act_signature,
                "claim_signature": claim_signature,
                "binding_signature": binding_signature,
            }
        )

    def stability(values: list[str]) -> float:
        if not values:
            return 0.0
        return max(Counter(values).values()) / len(values)

    latencies = [int(run["execution"]["latency_ms"]) for run in runs]
    return {
        "experiment_id": "ATT-REP-DETERMINISM",
        "status": "completed",
        "diagnostic_only": True,
        "unit_id": unit_id,
        "run_count": run_count,
        "cache_enabled": False,
        "metrics": {
            "cold_run_count": run_count,
            "cache_hit_count": sum(bool(run["execution"]["cache_hit"]) for run in runs),
            "raw_output_stability": stability(raw_signatures),
            "semantic_signature_stability": stability(semantic_signatures),
            "act_signature_stability": stability(act_signatures),
            "claim_signature_stability": stability(claim_signatures),
            "binding_signature_stability": stability(binding_signatures),
            "unique_raw_output_count": len(set(raw_signatures)),
            "unique_semantic_signature_count": len(set(semantic_signatures)),
            "p50_ms": _p50(latencies),
            "p95_ms": _p95(latencies),
        },
        "runs": runs,
    }


async def run_v9_adapter_cold(
    *,
    matrix: dict[str, Any],
    adapters: list[V8StructuredAdapter],
    unit_id: str,
    run_count: int = 3,
    client_factory: ClientFactory = build_v8_structured_client,
) -> dict[str, Any]:
    """Compare adapters under identical opaque gold inputs and cold calls."""

    reports: list[dict[str, Any]] = []
    for adapter in dict.fromkeys(adapters):
        report = await run_v9_repeat_attribution(
            matrix=matrix,
            unit_id=unit_id,
            adapter=adapter,
            run_count=run_count,
            client_factory=client_factory,
        )
        report = report.model_copy() if hasattr(report, "model_copy") else dict(report)
        report["experiment_id"] = f"ATT-ADAPTER-COLD-{adapter.upper()}"
        report["adapter"] = adapter
        reports.append(report)

    latencies = [
        float(run["execution"]["latency_ms"])
        for report in reports
        for run in report["runs"]
    ]
    return {
        "experiment_id": "ATT-ADAPTER-COLD",
        "status": "completed",
        "diagnostic_only": True,
        "input_source": "owner-scoped-opaque-gold-pool",
        "unit_id": unit_id,
        "run_count_per_adapter": run_count,
        "metrics": {
            "adapter_count": len(reports),
            "cold_run_count": sum(report["metrics"]["cold_run_count"] for report in reports),
            "cache_hit_count": sum(report["metrics"]["cache_hit_count"] for report in reports),
            "p50_ms": _p50([int(value) for value in latencies]),
            "p95_ms": _p95([int(value) for value in latencies]),
        },
        "adapter_results": reports,
    }


def v9_safety_boundary() -> dict[str, bool]:
    return {
        "consultation_state_written": False,
        "clinical_safety_evaluator_called": False,
        "clinical_safety_opa_called": False,
        "required_context_called": False,
    }
