"""Frozen V10 candidate snapshot construction and integrity evaluation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .v10_boundary_calibration import (
    V10BoundaryCalibrator,
    build_v10_gliner_extractor,
)
from .v10_contracts import V10CalibratedSpan
from .v10_fixture import V10Fixture, build_v10_golden_pool
from .v11_contracts import (
    V11CandidateSnapshot,
    V11SnapshotUnit,
)

_HELD_OUT_PATH = re.compile(r"held[_-]?out", re.IGNORECASE)
_FORBIDDEN_RUNTIME_KEYS = {
    "expected_start",
    "expected_end",
    "expected_label",
    "expected_label_candidates",
    "gold_quote",
    "claim_owner",
    "owner_id",
}


def build_ideal_snapshot(fixture: V10Fixture) -> V11CandidateSnapshot:
    """Build a deterministic control snapshot from explicit golden fields."""

    units: list[V11SnapshotUnit] = []
    for unit in fixture.units:
        pool = build_v10_golden_pool(unit)
        units.append(
            V11SnapshotUnit(
                unit_id=str(unit["unit_id"]),
                source_text=str(unit["user_text"]),
                candidates=[
                    V10CalibratedSpan(span=item.span, eligible_roles=item.eligible_roles)
                    for item in pool.spans
                ],
            )
        )
    return V11CandidateSnapshot(
        schema_version="v11-candidate-snapshot-1",
        snapshot_version="v11-ideal-golden-control-20260831-1",
        matrix_sha256=fixture.sha256,
        source_kind="ideal-golden-control",
        span_extractor_version="ideal-golden-control",
        boundary_calibration_version="owner-scoped-explicit-offset-control",
        units=units,
    )


def build_live_snapshot(
    fixture: V10Fixture,
    *,
    model_path: str,
    model_revision: str,
    tokenizer_path: str,
    threshold: float = 0.1,
) -> V11CandidateSnapshot:
    """Run the frozen V10 coarse locator once and persist its candidate pool."""

    extractor = build_v10_gliner_extractor(
        model_path=model_path,
        revision=model_revision,
        threshold=threshold,
        label_mode="bilingual",
    )
    calibrator = V10BoundaryCalibrator(
        variant="F",
        tokenizer_path=tokenizer_path,
        per_role_top_k=16,
        per_turn_limit=192,
    )
    units: list[V11SnapshotUnit] = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        text = str(unit["user_text"])
        raw = extractor.extract(
            source_id=unit_id,
            source_block_id="block-001",
            text=text,
        )
        candidates = calibrator.calibrate(
            source_id=unit_id,
            source_block_id="block-001",
            text=text,
            raw_spans=raw,
        )
        units.append(
            V11SnapshotUnit(
                unit_id=unit_id,
                source_text=text,
                candidates=candidates,
            )
        )
    return V11CandidateSnapshot(
        schema_version="v11-candidate-snapshot-1",
        snapshot_version=(
            "v10-small-t010-bilingual-variant-F-full-20260831-1"
        ),
        matrix_sha256=fixture.sha256,
        source_kind="v10-live-calibration",
        span_extractor_version=extractor.extractor_version,
        boundary_calibration_version=calibrator.calibration_version,
        units=units,
    )


def load_snapshot(path: Path) -> V11CandidateSnapshot:
    if _HELD_OUT_PATH.search(path.name):
        raise ValueError("v11_held_out_snapshot_not_allowed")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        snapshot = V11CandidateSnapshot.model_validate(raw)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid_v11_snapshot:{path}") from exc
    if contains_forbidden_runtime_keys(snapshot.model_dump(mode="json")):
        raise ValueError("v11_snapshot_contains_expected_fixture_fields")
    return snapshot


def save_snapshot(snapshot: V11CandidateSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def contains_forbidden_runtime_keys(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in _FORBIDDEN_RUNTIME_KEYS for key in value):
            return True
        return any(contains_forbidden_runtime_keys(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_forbidden_runtime_keys(item) for item in value)
    return False


def evaluate_snapshot(
    *,
    fixture: V10Fixture,
    snapshot: V11CandidateSnapshot,
) -> dict[str, Any]:
    if snapshot.matrix_sha256 != fixture.sha256:
        raise ValueError("v11_snapshot_fixture_sha_mismatch")

    candidates_by_unit: dict[str, list[V10CalibratedSpan]] = {
        unit.unit_id: unit.candidates for unit in snapshot.units
    }
    offset_valid = 0
    text_match = 0
    candidate_count = 0
    for unit in snapshot.units:
        for item in unit.candidates:
            candidate_count += 1
            valid = 0 <= item.span.start < item.span.end <= len(unit.source_text)
            offset_valid += int(valid)
            text_match += int(
                valid and unit.source_text[item.span.start : item.span.end] == item.span.text
            )

    exact_fields = 0
    role_fields = 0
    label_fields = 0
    unambiguous_total = 0
    label_matches = 0
    near_fields = 0
    matched_candidates: set[str] = set()
    per_role: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "exact": 0, "role": 0})
    field_results: list[dict[str, Any]] = []
    label_boundary_roles: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for field in fixture.fields:
        if field.status != "active":
            continue
        label_boundary_roles[(field.unit_id, field.start, field.end)].update(
            label.value for label in field.expected_label_candidates
        )
    for field in fixture.fields:
        if field.status != "active":
            continue
        candidates = candidates_by_unit.get(field.unit_id, [])
        exact = [
            item
            for item in candidates
            if item.span.start == field.start and item.span.end == field.end
        ]
        role_exact = [item for item in exact if field.field_role in item.eligible_roles]
        label_exact = [
            item for item in exact if item.span.label in field.expected_label_candidates
        ]
        near = any(
            max(0, min(field.end, item.span.end) - max(field.start, item.span.start)) > 0
            for item in candidates
        )
        exact_fields += int(bool(exact))
        role_fields += int(bool(role_exact))
        label_fields += int(bool(label_exact))
        near_fields += int(bool(near))
        if exact:
            matched_candidates.add(exact[0].span.span_id)
        if len(label_boundary_roles[(field.unit_id, field.start, field.end)]) == 1:
            unambiguous_total += 1
            label_matches += int(bool(label_exact))
        stats = per_role[field.field_role.value]
        stats["expected"] += 1
        stats["exact"] += int(bool(exact))
        stats["role"] += int(bool(role_exact))
        field_results.append(
            {
                "unit_id": field.unit_id,
                "role": field.field_role.value,
                "start": field.start,
                "end": field.end,
                "exact": bool(exact),
                "role_eligible": bool(role_exact),
                "label_correct": bool(label_exact),
                "near_overlap": bool(near),
            }
        )
    fields_total = sum(field.status == "active" for field in fixture.fields)

    def rate(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    return {
        "experiment_id": "SNAP-INTEGRITY",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "snapshot_version": snapshot.snapshot_version,
        "source_kind": snapshot.source_kind,
        "span_extractor_version": snapshot.span_extractor_version,
        "boundary_calibration_version": snapshot.boundary_calibration_version,
        "metrics": {
            "candidate_count": candidate_count,
            "unit_count": len(snapshot.units),
            "fixture_field_count": fields_total,
            "offset_valid_rate": rate(offset_valid, candidate_count),
            "text_match_rate": rate(text_match, candidate_count),
            "exact_field_recall": rate(exact_fields, fields_total),
            "field_coverage": rate(exact_fields, fields_total),
            "boundary_precision": rate(len(matched_candidates), candidate_count),
            "role_coverage": rate(role_fields, fields_total),
            "unambiguous_label_accuracy": rate(label_matches, unambiguous_total),
            "near_or_exact": rate(near_fields + exact_fields, fields_total),
        },
        "per_role": {
            role: {
                "expected": values["expected"],
                "exact_recall": rate(values["exact"], values["expected"]),
                "role_coverage": rate(values["role"], values["expected"]),
            }
            for role, values in sorted(per_role.items())
        },
        "field_results": field_results,
    }
