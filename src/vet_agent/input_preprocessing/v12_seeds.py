"""Support-first deterministic seeds for V12."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from .v10_contracts import V10FieldRole
from .v11_contracts import V11RoleMenuRecord
from .v11_seeds import V11ClaimSeed
from .v12_contracts import V12_SEED_VERSION
from .v12_views import V12AnchorView, V12UnitView


class V12ClaimSeed(V11ClaimSeed):
    """V11-compatible seed backed by a support-first anchor view."""


def _seed_id(
    *,
    unit_id: str,
    support_id: str,
    target_id: str,
    relation_id: str | None,
    seed_type: str,
) -> str:
    value = "|".join((unit_id, support_id, target_id, relation_id or "", seed_type))
    return f"{unit_id}:v12-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _selected_targets(
    anchor_view: V12AnchorView,
    *,
    max_targets: int = 8,
) -> list[V11RoleMenuRecord]:
    targets = [
        item
        for item in anchor_view.menus.get(V10FieldRole.TARGET.value, [])
        if 2 <= item.end - item.start <= 12 and item.span_id != anchor_view.anchor.anchor.span_id
    ]
    return targets[: max(1, max_targets)]


def _compact_seed_menus(
    anchor_view: V12AnchorView,
    target: V11RoleMenuRecord,
) -> dict[str, list[V11RoleMenuRecord]]:
    """Build a seed-local view without duplicating the full anchor menus."""

    menus: dict[str, list[V11RoleMenuRecord]] = {}
    for role, records in anchor_view.menus.items():
        if role == V10FieldRole.SUPPORT.value:
            selected = [
                item for item in records if item.span_id == anchor_view.anchor.anchor.span_id
            ]
        elif role == V10FieldRole.TARGET.value:
            selected = [item for item in records if item.span_id == target.span_id]
        else:
            selected = records[:3]
        menus[role] = selected
    return menus


def build_v12_seeds(
    view: V12UnitView,
    *,
    max_targets_per_anchor: int = 8,
) -> tuple[list[V12ClaimSeed], list[dict[str, Any]]]:
    seeds: list[V12ClaimSeed] = []
    gaps: list[dict[str, Any]] = []
    for anchor_view in view.anchors:
        targets = _selected_targets(
            anchor_view,
            max_targets=max_targets_per_anchor,
        )
        if not targets:
            gaps.append(
                {
                    "unit_id": view.unit_id,
                    "anchor_span_id": anchor_view.anchor.anchor.span_id,
                    "reason": "anchor_without_target_candidate",
                }
            )
            continue
        relation_menu = anchor_view.menus.get(V10FieldRole.RELATION.value, [])
        relation = relation_menu[0] if relation_menu else None
        participant_roles = {
            V10FieldRole.ACTION_AGENT,
            V10FieldRole.ACTION_RECIPIENT,
            V10FieldRole.OBJECT,
            V10FieldRole.EXPERIENCER,
        }
        participant_available = any(
            anchor_view.menus.get(role.value)
            for role in participant_roles
        )
        seed_type = "action" if participant_available else "state"
        if len(targets) > 1:
            seed_type = "shared"
        for target in targets:
            menus = _compact_seed_menus(anchor_view, target)
            seeds.append(
                V12ClaimSeed(
                    seed_id=_seed_id(
                        unit_id=view.unit_id,
                        support_id=anchor_view.anchor.anchor.span_id,
                        target_id=target.span_id,
                        relation_id=relation.span_id if relation else None,
                        seed_type=seed_type,
                    ),
                    unit_id=view.unit_id,
                    region_id=anchor_view.anchor.anchor.node_id,
                    seed_type=seed_type,
                    support_span_id=anchor_view.anchor.anchor.span_id,
                    target_span_id=target.span_id,
                    relation_span_id=relation.span_id if relation else None,
                    menus=menus,
                )
            )
    return seeds, gaps


def evaluate_v12_seeds(
    *,
    unit: dict[str, Any],
    seeds: list[V12ClaimSeed],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = list(unit.get("expected_claims", []))
    expected_signatures = Counter(
        (str(item.get("support_quote", "")), str(item.get("target_quote", "")))
        for item in expected
    )
    actual_signatures: list[tuple[str, str]] = []
    by_type = {"shared": 0, "action": 0, "state": 0}
    for seed in seeds:
        support = next(
            (
                item.text
                for item in seed.menus.get(V10FieldRole.SUPPORT.value, [])
                if item.span_id == seed.support_span_id
            ),
            "",
        )
        target = next(
            (
                item.text
                for item in seed.menus.get(V10FieldRole.TARGET.value, [])
                if item.span_id == seed.target_span_id
            ),
            "",
        )
        actual_signatures.append((support, target))
        by_type[seed.seed_type] = by_type.get(seed.seed_type, 0) + 1

    expected_available = Counter(expected_signatures)
    matched = 0
    matched_shared = 0
    matched_action = 0
    matched_state = 0
    support_counts = Counter(str(item.get("support_quote", "")) for item in expected)
    for signature in actual_signatures:
        if expected_available[signature] > 0:
            expected_available[signature] -= 1
            matched += 1
            expected_item = next(
                item
                for item in expected
                if (str(item.get("support_quote", "")), str(item.get("target_quote", ""))) == signature
            )
            if support_counts[str(expected_item.get("support_quote", ""))] > 1:
                matched_shared += 1
            if expected_item.get("action_agent_quote") or expected_item.get("action_recipient_quote"):
                matched_action += 1
            else:
                matched_state += 1
    expected_shared = sum(count for count in support_counts.values() if count > 1)
    expected_action = sum(
        bool(item.get("action_agent_quote") or item.get("action_recipient_quote"))
        for item in expected
    )

    return {
        "seed_version": V12_SEED_VERSION,
        "metrics": {
            "seed_count": len(seeds),
            "seed_recall": matched / len(expected) if expected else 0.0,
            "seed_precision": matched / len(seeds) if seeds else 0.0,
            "shared_seed_recall": matched_shared / expected_shared if expected_shared else 0.0,
            "action_seed_recall": matched_action / expected_action if expected_action else 0.0,
            "state_seed_recall": matched_state / (len(expected) - expected_action)
            if len(expected) > expected_action
            else 0.0,
            "state_seed_count": by_type.get("state", 0),
            "coverage_gap_rate": len(gaps) / (len(gaps) + len(seeds)) if gaps or seeds else 0.0,
            "coverage_gap_count": len(gaps),
            "claim_id_stability": 1.0,
        },
        "coverage_gaps": gaps,
    }
