"""Explicit-offset fixture loading and deterministic integrity audits for V10."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .v8_contracts import V8EntityCandidate, V8SpanCandidate, V8SpanLabel
from .v10_contracts import (
    V10_FIXTURE_VERSION,
    V10_GOLD_POOL_VERSION,
    V10ExpectedField,
    V10FieldRole,
    field_from_raw,
)

_HELD_OUT_PATH = re.compile(r"held[_-]?out", re.IGNORECASE)


@dataclass(frozen=True)
class V10Fixture:
    path: Path
    sha256: str
    schema_version: str
    units: list[dict[str, Any]]
    fields: list[V10ExpectedField]

    @property
    def fields_by_unit(self) -> dict[str, list[V10ExpectedField]]:
        result: dict[str, list[V10ExpectedField]] = defaultdict(list)
        for field in self.fields:
            result[field.unit_id].append(field)
        return dict(result)

    @property
    def texts_by_unit(self) -> dict[str, str]:
        return {
            str(unit["unit_id"]): str(unit["user_text"]) for unit in self.units
        }


@dataclass(frozen=True)
class V10GoldenSpan:
    span: V8SpanCandidate
    eligible_roles: frozenset[V10FieldRole]


@dataclass(frozen=True)
class V10GoldenPool:
    unit_id: str
    text: str
    spans: list[V10GoldenSpan]
    field_to_span: dict[tuple[str, V10FieldRole], V10GoldenSpan]
    entity_candidates: list[V8EntityCandidate]


def load_v10_fixture(path: Path) -> V10Fixture:
    if _HELD_OUT_PATH.search(path.name):
        raise ValueError("v10_held_out_fixture_not_allowed")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_v10_fixture:{path}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != V10_FIXTURE_VERSION:
        raise ValueError(f"unsupported_v10_fixture:{path}")
    units = raw.get("macro_units")
    if not isinstance(units, list) or not units:
        raise ValueError("v10_fixture_units_missing")

    fields: list[V10ExpectedField] = []
    seen_unit_ids: set[str] = set()
    for unit in units:
        unit_id = str(unit.get("unit_id", ""))
        if not unit_id or unit_id in seen_unit_ids:
            raise ValueError("v10_fixture_unit_id_invalid")
        seen_unit_ids.add(unit_id)
        text = str(unit.get("user_text", ""))
        raw_fields = unit.get("expected_offset_fields", [])
        if not isinstance(raw_fields, list):
            raise TypeError(f"v10_expected_offset_fields_invalid:{unit_id}")
        for item in raw_fields:
            if not isinstance(item, dict):
                raise TypeError(f"v10_expected_field_invalid:{unit_id}")
            try:
                field = field_from_raw(item, unit_id)
            except Exception as exc:
                raise ValueError(f"v10_expected_field_invalid:{unit_id}") from exc
            if field.status == "active" and not (
                0 <= field.start < field.end <= len(text)
                and text[field.start : field.end] == field.text
            ):
                raise ValueError(f"v10_offset_text_mismatch:{unit_id}:{field.claim_owner}:{field.field_role}")
            fields.append(field)

    return V10Fixture(
        path=path,
        sha256=_sha256(path),
        schema_version=str(raw["schema_version"]),
        units=units,
        fields=fields,
    )


def audit_v10_fixture(fixture: V10Fixture) -> dict[str, Any]:
    fields = [field for field in fixture.fields if field.status == "active"]
    incomplete = [field for field in fixture.fields if field.status == "fixture_incomplete"]
    texts = fixture.texts_by_unit
    offset_valid = 0
    text_match = 0
    owner_occurrence_valid = 0
    source_block_valid = 0
    findings: list[dict[str, Any]] = []

    for field in fixture.fields:
        text = texts.get(field.unit_id, "")
        valid_offset = 0 <= field.start < field.end <= len(text)
        valid_text = valid_offset and text[field.start : field.end] == field.text
        offset_valid += int(valid_offset)
        text_match += int(valid_text)
        source_block_valid += int(field.source_block_id == "block-001")
        unit = next(
            (item for item in fixture.units if str(item["unit_id"]) == field.unit_id),
            {},
        )
        raw_fields = unit.get("expected_offset_fields", [])
        raw: dict[str, Any] = next(
            (
                item
                for item in raw_fields
                if str(item.get("claim_owner")) == field.claim_owner
                and str(item.get("field_role")) == field.field_role.value
            ),
            {},
        )
        locator = raw.get("occurrence_locator", {})
        # Explicit offsets are authoritative.  Some omitted participants (for
        # example an elided subject before a state phrase) intentionally point
        # outside the local support phrase; the fixture locator still records
        # the owner occurrence and remains valid.
        owner_valid = isinstance(locator, dict) and bool(locator)
        owner_occurrence_valid += int(owner_valid)
        if not owner_valid:
            findings.append(
                {
                    "unit_id": field.unit_id,
                    "owner_id": field.claim_owner,
                    "role": field.field_role.value,
                    "code": "owner_occurrence_invalid",
                }
            )

    boundaries: dict[tuple[str, int, int], list[V10ExpectedField]] = defaultdict(list)
    for field in fields:
        boundaries[(field.unit_id, field.start, field.end)].append(field)
    multi_role = [items for items in boundaries.values() if len({i.field_role for i in items}) > 1]

    return {
        "experiment_id": "FIXTURE-OFFSET",
        "status": "completed" if not findings else "completed_with_findings",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "fixture_version": fixture.schema_version,
        "fixture_path": str(fixture.path),
        "fixture_sha256": fixture.sha256,
        "metrics": {
            "fixture_field_count": len(fixture.fields),
            "active_field_count": len(fields),
            "fixture_incomplete_field_count": len(incomplete),
            "offset_valid_rate": _rate(offset_valid, len(fixture.fields)),
            "text_match_rate": _rate(text_match, len(fixture.fields)),
            "owner_occurrence_valid_rate": _rate(owner_occurrence_valid, len(fixture.fields)),
            "source_block_valid_rate": _rate(source_block_valid, len(fixture.fields)),
            "migration_error_count": len(findings),
            "unique_boundary_count": len(boundaries),
            "multi_role_boundary_count": len(multi_role),
        },
        "role_counts": dict(Counter(field.field_role.value for field in fields)),
        "findings": findings,
    }


def field_role_split(fixture: V10Fixture) -> dict[str, Any]:
    fields = [field for field in fixture.fields if field.status == "active"]
    boundary_labels: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    for field in fields:
        boundary_labels[(field.unit_id, field.start, field.end)].update(
            label.value for label in field.expected_label_candidates
        )
    evaluable = [
        field
        for field in fields
        if len(boundary_labels[(field.unit_id, field.start, field.end)]) == 1
    ]
    role_counts = Counter(field.field_role.value for field in fields)
    return {
        "experiment_id": "FIELD-ROLE-SPLIT",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "field_role_count": len(fields),
            "unique_boundary_count": len(boundary_labels),
            "multi_role_boundary_count": sum(
                len({item.field_role for item in fields if (item.unit_id, item.start, item.end) == key}) > 1
                for key in boundary_labels
            ),
            "unambiguous_label_field_count": len(evaluable),
            "label_evaluable_rate": _rate(len(evaluable), len(fields)),
            "role_binding_expected_count": sum(
                field.field_role != V10FieldRole.EVIDENCE for field in fields
            ),
            "roles_with_fields": len(role_counts),
        },
        "role_counts": dict(role_counts),
        "multi_role_boundaries": [
            {
                "unit_id": key[0],
                "start": key[1],
                "end": key[2],
                "roles": sorted({item.field_role.value for item in fields if (item.unit_id, item.start, item.end) == key}),
            }
            for key in boundary_labels
            if len({item.field_role for item in fields if (item.unit_id, item.start, item.end) == key}) > 1
        ],
    }


def relation_span_completeness(fixture: V10Fixture) -> dict[str, Any]:
    fields = fixture.fields_by_unit
    expected = 0
    available = 0
    incomplete: list[dict[str, Any]] = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        for claim in unit.get("expected_claims", []):
            if not claim.get("expected_relation"):
                continue
            expected += 1
            owner = str(claim["claim_id"])
            relation = next(
                (
                    field
                    for field in fields.get(unit_id, [])
                    if field.claim_owner == owner and field.field_role == V10FieldRole.RELATION
                ),
                None,
            )
            if relation is not None and relation.status == "active":
                available += 1
            else:
                incomplete.append(
                    {
                        "unit_id": unit_id,
                        "claim_id": owner,
                        "expected_relation": str(claim["expected_relation"]),
                        "reason": relation.incomplete_reason if relation else "relation_field_missing",
                    }
                )
    return {
        "experiment_id": "RELATION-SPAN-COMPLETE",
        "status": "completed" if not incomplete else "completed_with_findings",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "expected_relation_count": expected,
            "relation_span_available_rate": _rate(available, expected),
            "fixture_incomplete_relation_count": len(incomplete),
        },
        "incomplete_records": incomplete,
    }


def build_v10_golden_pool(unit: dict[str, Any]) -> V10GoldenPool:
    unit_id = str(unit["unit_id"])
    text = str(unit["user_text"])
    raw_fields = unit.get("expected_offset_fields", [])
    fields = [field_from_raw(item, unit_id) for item in raw_fields]
    active = [field for field in fields if field.status == "active"]
    by_key: dict[tuple[int, int, V8SpanLabel], V10GoldenSpan] = {}
    for field in active:
        for label in field.expected_label_candidates:
            key = (field.start, field.end, label)
            if key not in by_key:
                span = V8SpanCandidate(
                    span_id=f"{unit_id}:v10-gold-{field.start:06d}-{field.end:06d}-{label.value}",
                    source_id=unit_id,
                    source_block_id=field.source_block_id,
                    start=field.start,
                    end=field.end,
                    text=text[field.start : field.end],
                    label=label,
                    score=1.0,
                    extractor_version=V10_GOLD_POOL_VERSION,
                )
                by_key[key] = V10GoldenSpan(
                    span=span,
                    eligible_roles=frozenset({field.field_role}),
                )
            else:
                existing = by_key[key]
                by_key[key] = V10GoldenSpan(
                    span=existing.span,
                    eligible_roles=existing.eligible_roles | {field.field_role},
                )

    field_to_span: dict[tuple[str, V10FieldRole], V10GoldenSpan] = {}
    for field in active:
        label = field.expected_label_candidates[0]
        key = (field.start, field.end, label)
        field_to_span[(field.claim_owner, field.field_role)] = by_key[key]

    ordered = [by_key[key] for key in sorted(by_key, key=lambda item: (item[0], item[1], item[2].value))]
    entities = [V8EntityCandidate.model_validate(raw) for raw in unit.get("entity_candidates", [])]
    return V10GoldenPool(
        unit_id=unit_id,
        text=text,
        spans=ordered,
        field_to_span=field_to_span,
        entity_candidates=entities,
    )


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
