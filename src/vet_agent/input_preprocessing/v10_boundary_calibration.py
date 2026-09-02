"""Deterministic boundary calibration experiments for V10.

The calibrator receives only runtime-legal inputs: source text, coarse GLiNER
spans, generic tokenizer offsets, punctuation/whitespace structure, and generic
temporal/measurement expressions.  Fixture offsets are used by evaluators, never
by candidate generation.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .v8_contracts import V8SpanCandidate, V8SpanLabel
from .v8_span_extractors import (
    _GLINER_LABEL_MODES,
    _GLINER_LABEL_PROFILES,
    V8GlinerSpanExtractor,
)
from .v10_contracts import (
    V10_BOUNDARY_CALIBRATION_VERSION,
    V10CalibratedSpan,
    V10ExpectedField,
    V10FieldRole,
)

CalibrationVariant = Literal["A", "B", "C", "D", "E", "F", "G"]

_EDGE_TRIM = re.compile(r"^[\s，。！？；;：:、,.!?~～\s]+|[\s，。！？；;：:、,.!?~～\s]+$")
_TEMPORAL = re.compile(
    r"(?:最近|这)[一二两三四五六七八九十\d]+天|"
    r"[一二三四五六七八九十\d]+(?:天|周|小时|分钟)|"
    r"(?:今天|昨天|前天|大前天)(?:开始)?"
)
_MEASUREMENT = re.compile(
    r"(?:一天|每日|每天|每周|每月)[一二三四五六七八九十\d]+次|"
    r"\d+(?:\.\d+)?\s*(?:公斤|千克|克|毫克|毫升|升|次|片|粒|次/天|kg|g|mg|ml|l)",
    re.IGNORECASE,
)

_LABEL_ROLES: dict[V8SpanLabel, frozenset[V10FieldRole]] = {
    V8SpanLabel.TARGET_MENTION: frozenset({V10FieldRole.TARGET}),
    V8SpanLabel.STATE_MENTION: frozenset({V10FieldRole.SUPPORT}),
    V8SpanLabel.ACTION_EVENT: frozenset({V10FieldRole.SUPPORT}),
    V8SpanLabel.AGENT_MENTION: frozenset({V10FieldRole.ACTION_AGENT}),
    V8SpanLabel.RECIPIENT_MENTION: frozenset({V10FieldRole.ACTION_RECIPIENT}),
    V8SpanLabel.SUBJECT_MENTION: frozenset({V10FieldRole.SUBJECT, V10FieldRole.EXPERIENCER}),
    V8SpanLabel.OBJECT_MENTION: frozenset({V10FieldRole.OBJECT}),
    V8SpanLabel.TEMPORAL_EXPRESSION: frozenset({V10FieldRole.TEMPORAL}),
    V8SpanLabel.MEASUREMENT_EXPRESSION: frozenset({V10FieldRole.MEASUREMENT}),
    V8SpanLabel.RELATION_EXPRESSION: frozenset({V10FieldRole.RELATION}),
    V8SpanLabel.CONTROL_INTENT_EXPRESSION: frozenset({V10FieldRole.EVIDENCE}),
    V8SpanLabel.QUESTION_EXPRESSION: frozenset({V10FieldRole.EVIDENCE}),
    V8SpanLabel.CANDIDATE_SPAN: frozenset({V10FieldRole.SUPPORT, V10FieldRole.TARGET}),
}

_NESTED_LABEL_ROLES: dict[V8SpanLabel, frozenset[V10FieldRole]] = {
    V8SpanLabel.ACTION_EVENT: frozenset(
        {
            V10FieldRole.SUPPORT,
            V10FieldRole.TARGET,
            V10FieldRole.RELATION,
            V10FieldRole.SUBJECT,
            V10FieldRole.ACTION_AGENT,
            V10FieldRole.ACTION_RECIPIENT,
            V10FieldRole.EXPERIENCER,
            V10FieldRole.OBJECT,
            V10FieldRole.TEMPORAL,
            V10FieldRole.MEASUREMENT,
        }
    ),
    V8SpanLabel.STATE_MENTION: frozenset(
        {
            V10FieldRole.SUPPORT,
            V10FieldRole.TARGET,
            V10FieldRole.RELATION,
            V10FieldRole.SUBJECT,
            V10FieldRole.EXPERIENCER,
            V10FieldRole.TEMPORAL,
            V10FieldRole.MEASUREMENT,
        }
    ),
}


class V10SpanExtractor(Protocol):
    @property
    def extractor_version(self) -> str: ...

    def extract(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
    ) -> list[V8SpanCandidate]: ...


@dataclass(frozen=True)
class V10GlinerSpanExtractor(V8GlinerSpanExtractor):
    """Offline GLiNER loader used by V10 model comparisons."""

    local_files_only: bool = True
    resize_token_embeddings: bool = False

    def __post_init__(self) -> None:
        try:
            from gliner import GLiNER  # type: ignore[import-not-found, import-untyped]
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("v10_gliner_unavailable") from exc
        if self.label_profile not in _GLINER_LABEL_PROFILES:
            raise ValueError(f"unsupported_v10_gliner_profile:{self.label_profile}")
        if self.label_mode not in _GLINER_LABEL_MODES:
            raise ValueError(f"unsupported_v10_gliner_label_mode:{self.label_mode}")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("v10_gliner_threshold_out_of_range")
        self.__dict__["model"] = GLiNER.from_pretrained(
            self.model_name,
            map_location="cpu",
            local_files_only=self.local_files_only,
            resize_token_embeddings=self.resize_token_embeddings,
        )
        base_version = f"v10-gliner:{self.label_profile}:threshold-{self.threshold:.3f}:{self.label_mode}:{self.model_revision}"
        self.__dict__["extractor_version"] = base_version


@dataclass(frozen=True)
class V10BoundaryCalibrator:
    """Generate an exact-offset candidate pool from coarse spans."""

    variant: CalibrationVariant = "G"
    tokenizer_path: str = ""
    per_role_top_k: int = 8
    per_turn_limit: int = 256
    max_span_length: int = 32
    calibration_version: str = V10_BOUNDARY_CALIBRATION_VERSION

    def calibrate(
        self,
        *,
        source_id: str,
        source_block_id: str,
        text: str,
        raw_spans: list[V8SpanCandidate],
    ) -> list[V10CalibratedSpan]:
        if self.variant == "A":
            return self._finish(
                [
                    self._candidate(span, _LABEL_ROLES[span.label])
                    for span in self._raw(text, source_id, source_block_id, raw_spans)
                ],
                text,
            )
        raw_calibrated = self._raw(text, source_id, source_block_id, raw_spans)
        spans: list[V8SpanCandidate] = self._trim(raw_calibrated, text)
        if self.variant in {"C", "D", "E", "F", "G"}:
            spans = self._align(spans, text)
        if self.variant in {"D", "E", "F", "G"}:
            spans.extend(self._nested(spans, text))
        if self.variant in {"E", "F", "G"}:
            spans.extend(self._deterministic_expressions(text, source_id, source_block_id))
        spans = self._deduplicate(spans, text, source_id)
        calibrated: list[V10CalibratedSpan]
        if self.variant in {"F", "G"}:
            calibrated = self._with_role_eligibility(spans)
        else:
            calibrated = [
                self._candidate(
                    span,
                    frozenset({V10FieldRole.SUPPORT, V10FieldRole.TARGET}),
                )
                for span in spans
            ]
        if self.variant == "G":
            calibrated = self._budget(calibrated)
        return self._finish(calibrated, text)

    def _raw(
        self,
        text: str,
        source_id: str,
        source_block_id: str,
        raw_spans: list[V8SpanCandidate],
    ) -> list[V8SpanCandidate]:
        result = []
        for span in raw_spans:
            if 0 <= span.start < span.end <= len(text) and text[span.start : span.end] == span.text:
                result.append(
                    span.model_copy(
                        update={
                            "source_id": source_id,
                            "source_block_id": source_block_id,
                            "extractor_version": f"{span.extractor_version}:v10-A",
                        }
                    )
                )
        return result

    def _trim(self, spans: list[V8SpanCandidate], text: str) -> list[V8SpanCandidate]:
        result: list[V8SpanCandidate] = []
        for span in spans:
            trimmed = _EDGE_TRIM.sub("", text[span.start : span.end])
            if not trimmed:
                continue
            start = text.index(trimmed, span.start, span.end)
            result.append(
                span.model_copy(
                    update={
                        "start": start,
                        "end": start + len(trimmed),
                        "text": trimmed,
                        "extractor_version": f"{span.extractor_version}:v10-B",
                    }
                )
            )
        return result

    def _align(self, spans: list[V8SpanCandidate], text: str) -> list[V8SpanCandidate]:
        boundaries = self._tokenizer_boundaries(text)
        if not boundaries:
            return spans
        result: list[V8SpanCandidate] = []
        for span in spans:
            start = min(boundaries, key=lambda value: abs(value - span.start))
            end_candidates = [value for value in boundaries if value > start]
            if not end_candidates:
                continue
            end = min(end_candidates, key=lambda value: abs(value - span.end))
            if start >= end:
                continue
            result.append(
                span.model_copy(
                    update={
                        "start": start,
                        "end": end,
                        "text": text[start:end],
                        "extractor_version": f"{span.extractor_version}:v10-C",
                    }
                )
            )
        return result

    def _nested(
        self,
        spans: list[V8SpanCandidate],
        text: str,
    ) -> list[V8SpanCandidate]:
        boundaries = self._tokenizer_boundaries(text)
        if not boundaries:
            boundaries = list(range(len(text) + 1))
        result: list[V8SpanCandidate] = []
        for parent in spans:
            starts = [value for value in boundaries if parent.start <= value < parent.end]
            for start in starts:
                for end in [value for value in boundaries if start < value <= parent.end]:
                    if not 0 < end - start <= self.max_span_length:
                        continue
                    result.append(
                        parent.model_copy(
                            update={
                                "start": start,
                                "end": end,
                                "text": text[start:end],
                                "span_id": f"{parent.source_id}:v10-D-{start:06d}-{end:06d}-{parent.label.value}",
                                "score": round(parent.score * 0.5, 6),
                                "extractor_version": f"{parent.extractor_version}:v10-D",
                            }
                        )
                    )
        return result

    def _deterministic_expressions(
        self,
        text: str,
        source_id: str,
        source_block_id: str,
    ) -> list[V8SpanCandidate]:
        result: list[V8SpanCandidate] = []
        for label, pattern in (
            (V8SpanLabel.TEMPORAL_EXPRESSION, _TEMPORAL),
            (V8SpanLabel.MEASUREMENT_EXPRESSION, _MEASUREMENT),
        ):
            for match in pattern.finditer(text):
                result.append(
                    V8SpanCandidate(
                        span_id=f"{source_id}:v10-E-{label.value}-{match.start():06d}-{match.end():06d}",
                        source_id=source_id,
                        source_block_id=source_block_id,
                        start=match.start(),
                        end=match.end(),
                        text=match.group(0),
                        label=label,
                        score=0.9,
                        extractor_version="v10-deterministic-generic-expression",
                    )
                )
        return result

    def _deduplicate(
        self,
        spans: list[V8SpanCandidate],
        text: str,
        source_id: str,
    ) -> list[V8SpanCandidate]:
        by_key: dict[tuple[int, int, V8SpanLabel], V8SpanCandidate] = {}
        for span in spans:
            if not 0 <= span.start < span.end <= len(text) or text[span.start : span.end] != span.text:
                continue
            key = (span.start, span.end, span.label)
            previous = by_key.get(key)
            if previous is None or span.score > previous.score:
                by_key[key] = span
        return [
            by_key[key].model_copy(
                update={
                    "span_id": f"{source_id}:v10-{key[0]:06d}-{key[1]:06d}-{key[2].value}",
                    "source_id": source_id,
                }
            )
            for key in sorted(by_key, key=lambda item: (item[0], item[1], item[2].value))
        ]

    def _with_role_eligibility(
        self,
        spans: list[V8SpanCandidate],
    ) -> list[V10CalibratedSpan]:
        result: list[V10CalibratedSpan] = []
        for span in spans:
            roles = _LABEL_ROLES[span.label]
            # Nested constituents of a coarse action/state phrase may serve a
            # different micro-role from their parent.  This remains a candidate
            # pool: the V10 macro governance separately rejects an ineligible
            # binding and deterministic code still resolves every quote.
            if ":v10-D" in span.extractor_version:
                roles = roles | _NESTED_LABEL_ROLES.get(
                    V8SpanLabel.ACTION_EVENT,
                    frozenset(),
                )
            if roles:
                result.append(self._candidate(span, roles))
        return result

    def _budget(self, spans: list[V10CalibratedSpan]) -> list[V10CalibratedSpan]:
        by_role: dict[V10FieldRole, list[V10CalibratedSpan]] = defaultdict(list)
        for item in spans:
            for role in item.eligible_roles:
                by_role[role].append(item)
        selected: dict[int, V10CalibratedSpan] = {}

        def candidate_key(item: V10CalibratedSpan) -> tuple[Any, ...]:
            length = item.span.end - item.span.start
            nested = ":v10-D" in item.span.extractor_version
            # Preserve high-confidence parent boundaries first.  For equal
            # nested scores, prefer medium constituents over one-character
            # fragments and whole-sentence duplicates, while later rounds keep
            # ambiguity alternatives.
            return (
                -item.span.score,
                nested,
                abs(length - 4),
                length,
                item.span.start,
                item.span.end,
                item.span.span_id,
            )

        for role in sorted(by_role):
            by_start: dict[int, list[V10CalibratedSpan]] = defaultdict(list)
            for item in by_role[role]:
                by_start[item.span.start].append(item)
            for items in by_start.values():
                items.sort(key=candidate_key)
            starts = sorted(
                by_start,
                key=lambda start: candidate_key(by_start[start][0]),
            )
            maximum_rounds = max((len(items) for items in by_start.values()), default=0)
            role_selected = 0
            round_index = 0
            while role_selected < self.per_role_top_k and round_index < maximum_rounds:
                for start in starts:
                    if role_selected >= self.per_role_top_k:
                        break
                    if round_index >= len(by_start[start]):
                        continue
                    item = by_start[start][round_index]
                    previous = selected.get(id(item))
                    if previous is None:
                        selected[id(item)] = item
                        role_selected += 1
                round_index += 1
        ordered = sorted(
            selected.values(),
            key=lambda item: (item.span.start, item.span.end, item.span.label.value),
        )[: self.per_turn_limit]
        return ordered

    def _tokenizer_boundaries(self, text: str) -> list[int]:
        if not self.tokenizer_path:
            return []
        cache = _TOKENIZER_BOUNDARY_CACHE.get(self.tokenizer_path)
        if cache is None:
            from transformers import AutoTokenizer  # type: ignore[import-not-found]

            tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_path,
                local_files_only=True,
            )
            cache = tokenizer
            _TOKENIZER_BOUNDARY_CACHE[self.tokenizer_path] = tokenizer
        encoded = cache(text, return_offsets_mapping=True, add_special_tokens=False)
        return sorted(
            {
                boundary
                for start, end in encoded["offset_mapping"]
                for boundary in (start, end)
                if 0 <= boundary <= len(text)
            }
        )

    @staticmethod
    def _candidate(
        span: V8SpanCandidate,
        roles: frozenset[V10FieldRole],
    ) -> V10CalibratedSpan:
        return V10CalibratedSpan(span=span, eligible_roles=roles)

    @staticmethod
    def _finish(
        spans: list[V10CalibratedSpan],
        text: str,
    ) -> list[V10CalibratedSpan]:
        for item in spans:
            if text[item.span.start : item.span.end] != item.span.text:
                raise ValueError("v10_calibrated_offset_text_mismatch")
        return spans


_TOKENIZER_BOUNDARY_CACHE: dict[str, Any] = {}


def _percentile(values: list[int], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, round(quantile * (len(ordered) - 1)))])


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_v10_gliner_extractor(
    *,
    model_path: str,
    revision: str,
    threshold: float = 0.3,
    label_mode: str = "bilingual",
) -> V10GlinerSpanExtractor:
    return V10GlinerSpanExtractor(
        model_name=model_path,
        threshold=threshold,
        label_profile="staged",
        label_mode=label_mode,
        model_revision=revision,
        local_files_only=True,
        resize_token_embeddings=False,
    )


def evaluate_v10_span_pool(
    *,
    fields: list[V10ExpectedField],
    texts_by_unit: dict[str, str],
    extractor: V10SpanExtractor,
    calibrator: V10BoundaryCalibrator,
) -> dict[str, Any]:
    started = time.perf_counter()
    predicted: dict[str, list[V10CalibratedSpan]] = {}
    raw_counts: dict[str, int] = {}
    unit_latencies: list[int] = []
    unit_candidate_counts: dict[str, int] = {}
    for unit_id in sorted(texts_by_unit):
        text = texts_by_unit[unit_id]
        unit_started = time.perf_counter()
        raw = extractor.extract(source_id=unit_id, source_block_id="block-001", text=text)
        raw_counts[unit_id] = len(raw)
        predicted[unit_id] = calibrator.calibrate(
            source_id=unit_id,
            source_block_id="block-001",
            text=text,
            raw_spans=raw,
        )
        unit_latencies.append(int((time.perf_counter() - unit_started) * 1000))
        unit_candidate_counts[unit_id] = len(predicted[unit_id])

    exact_fields = 0
    role_fields = 0
    label_fields = 0
    unambiguous_total = 0
    near_fields = 0
    matched_ids: set[str] = set()
    role_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "exact": 0, "role": 0})
    field_results: list[dict[str, Any]] = []
    boundary_keys = {
        (field.unit_id, field.start, field.end) for field in fields if field.status == "active"
    }
    label_boundary_keys: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for field in fields:
        if field.status != "active":
            continue
        label_boundary_keys[(field.unit_id, field.start, field.end)].update(
            label.value for label in field.expected_label_candidates
        )
    for field in fields:
        if field.status != "active":
            continue
        candidates = predicted.get(field.unit_id, [])
        exact = [item for item in candidates if item.span.start == field.start and item.span.end == field.end]
        role_exact = [item for item in exact if field.field_role in item.eligible_roles]
        label_exact = [
            item
            for item in exact
            if item.span.label in field.expected_label_candidates
        ]
        near = any(
            max(0, min(field.end, item.span.end) - max(field.start, item.span.start)) > 0
            for item in candidates
        )
        exact_fields += int(bool(exact))
        role_fields += int(bool(role_exact))
        label_fields += int(bool(label_exact))
        near_fields += int(bool(near) and not exact)
        if exact:
            matched_ids.add(exact[0].span.span_id)
        if len(label_boundary_keys[(field.unit_id, field.start, field.end)]) == 1:
            unambiguous_total += 1
        stats = role_stats[field.field_role.value]
        stats["expected"] += 1
        stats["exact"] += int(bool(exact))
        stats["role"] += int(bool(role_exact))
        field_results.append(
            {
                "unit_id": field.unit_id,
                "owner_id": field.claim_owner,
                "role": field.field_role.value,
                "expected_start": field.start,
                "expected_end": field.end,
                "text": field.text,
                "exact": bool(exact),
                "role_eligible": bool(role_exact),
                "label_correct": bool(label_exact),
                "near_overlap_without_exact": bool(near and not exact),
            }
        )
    candidate_count = sum(len(items) for items in predicted.values())
    raw_count = sum(raw_counts.values())
    exact_candidate_count = sum(
        1
        for items in predicted.values()
        for item in items
        if (item.span.source_id, item.span.start, item.span.end) in boundary_keys
    )
    failed_thresholds: list[str] = []
    if _rate(exact_fields, len(fields)) < 0.90:
        failed_thresholds.append("field_coverage")
    if _rate(exact_candidate_count, candidate_count) < 0.75:
        failed_thresholds.append("boundary_precision")
    if _rate(label_fields, unambiguous_total) < 0.75:
        failed_thresholds.append("unambiguous_label_accuracy")
    return {
        "experiment_id": "SPAN-CALIBRATE",
        "status": "completed_with_findings" if failed_thresholds else "completed",
        "failed_thresholds": failed_thresholds,
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "extractor_version": extractor.extractor_version,
        "calibration_version": f"{calibrator.calibration_version}:variant-{calibrator.variant}",
        "candidate_policy": {
            "variant": calibrator.variant,
            "per_role_top_k": calibrator.per_role_top_k,
            "per_turn_limit": calibrator.per_turn_limit,
            "max_span_length": calibrator.max_span_length,
            "tokenizer_path": calibrator.tokenizer_path,
        },
        "metrics": {
            "required_field_count": len(fields),
            "raw_candidate_count": raw_count,
            "candidate_count": candidate_count,
            "exact_field_count": exact_fields,
            "field_coverage": _rate(exact_fields, len(fields)),
            "boundary_recall": _rate(exact_fields, len(fields)),
            "boundary_precision": _rate(exact_candidate_count, candidate_count),
            "role_binding_coverage": _rate(role_fields, len(fields)),
            "label_correct_on_exact": _rate(label_fields, exact_fields),
            "unambiguous_label_accuracy": _rate(label_fields, unambiguous_total),
            "near_boundary_or_exact_rate": _rate(exact_fields + near_fields, len(fields)),
            "duplicate_candidate_rate": _rate(candidate_count - len({item.span.span_id for items in predicted.values() for item in items}), candidate_count),
            "p50_ms": _percentile(unit_latencies, 0.5),
            "p95_ms": _percentile(unit_latencies, 0.95),
            "wall_latency_ms": int((time.perf_counter() - started) * 1000),
        },
        "unit_candidate_counts": unit_candidate_counts,
        "role_metrics": {
            role: {**stats, "coverage": _rate(stats["exact"], stats["expected"]), "role_coverage": _rate(stats["role"], stats["expected"])}
            for role, stats in sorted(role_stats.items())
        },
        "field_results": field_results,
    }
