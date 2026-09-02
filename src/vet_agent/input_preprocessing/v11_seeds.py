"""Deterministic structural claim-skeleton seeds for V11."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .v10_contracts import V10FieldRole
from .v11_contracts import V11RoleMenuRecord
from .v11_views import V11UnitRanking, role_menu


@dataclass(frozen=True)
class V11ClaimSeed:
    seed_id: str
    unit_id: str
    region_id: str
    seed_type: str
    support_span_id: str
    target_span_id: str
    relation_span_id: str | None
    menus: dict[str, list[V11RoleMenuRecord]]


def _seed_id(
    *,
    unit_id: str,
    support_id: str,
    target_id: str,
    relation_id: str | None,
    seed_type: str,
) -> str:
    value = "|".join((unit_id, support_id, target_id, relation_id or "", seed_type))
    return f"{unit_id}:seed-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def build_structural_seeds(
    ranking: V11UnitRanking,
    *,
    target_top_k: int = 8,
    role_top_k: int = 5,
) -> list[V11ClaimSeed]:
    """Generate seeds from structure only; no medical vocabulary is consulted."""

    seeds: list[V11ClaimSeed] = []
    for region in ranking.regions:
        support_menu = role_menu(
            ranking,
            region=region,
            role=V10FieldRole.SUPPORT,
            top_k=role_top_k,
            mode="claim-local",
        )
        target_menu = role_menu(
            ranking,
            region=region,
            role=V10FieldRole.TARGET,
            top_k=target_top_k,
            mode="claim-local",
        )
        relation_menu = role_menu(
            ranking,
            region=region,
            role=V10FieldRole.RELATION,
            top_k=role_top_k,
            mode="claim-local",
        )
        support = next(
            (
                item
                for item in support_menu
                if any(item.start <= target.start and target.end <= item.end for target in target_menu)
            ),
            None,
        )
        if support is None:
            # A region without a support candidate cannot produce a safe seed.
            continue
        relation = relation_menu[0] if relation_menu else None
        contained_targets = [
            item
            for item in target_menu
            if support.start <= item.start and item.end <= support.end and item.span_id != support.span_id
        ]
        seed_type = "shared" if len(contained_targets) > 1 else (
            "action" if support.label.value == "action_event" else "state"
        )
        for target in contained_targets:
            menus: dict[str, list[V11RoleMenuRecord]] = {
                role.value: role_menu(
                    ranking,
                    region=region,
                    role=role,
                    top_k=role_top_k,
                    mode="claim-local",
                )
                for role in (
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
                )
            }
            seeds.append(
                V11ClaimSeed(
                    seed_id=_seed_id(
                        unit_id=ranking.unit_id,
                        support_id=support.span_id,
                        target_id=target.span_id,
                        relation_id=relation.span_id if relation else None,
                        seed_type=seed_type,
                    ),
                    unit_id=ranking.unit_id,
                    region_id=region.region_id,
                    seed_type=seed_type,
                    support_span_id=support.span_id,
                    target_span_id=target.span_id,
                    relation_span_id=relation.span_id if relation else None,
                    menus=menus,
                )
            )
    return seeds


def evaluate_structural_seeds(
    *,
    unit: dict[str, Any],
    seeds: list[V11ClaimSeed],
) -> dict[str, Any]:
    expected = list(unit.get("expected_claims", []))
    # Candidate IDs are opaque but deterministic.  Match by resolved text from
    # the menus for an evaluator-only comparison.
    seed_signatures = {
        (
            next(
                (
                    item.text
                    for item in seed.menus["support_quote"]
                    if item.span_id == seed.support_span_id
                ),
                "",
            ),
            next(
                (
                    item.text
                    for item in seed.menus["target_quote"]
                    if item.span_id == seed.target_span_id
                ),
                "",
            ),
        ): seed
        for seed in seeds
        if any(item.span_id == seed.support_span_id for item in seed.menus["support_quote"])
        and any(item.span_id == seed.target_span_id for item in seed.menus["target_quote"])
    }
    matched = 0
    shared_expected = 0
    shared_matched = 0
    action_expected = 0
    action_matched = 0
    relation_inherited = 0
    results: list[dict[str, Any]] = []
    for claim in expected:
        signature = (str(claim.get("support_quote", "")), str(claim.get("target_quote", "")))
        seed = seed_signatures.get(signature)
        passed = seed is not None
        matched += int(passed)
        coarse = str(claim.get("coarse_type", ""))
        is_action = coarse in {"action", "food", "medication"}
        action_expected += int(is_action)
        action_matched += int(is_action and passed)
        if not is_action:
            shared_expected += 1
            shared_matched += int(passed)
        if passed and claim.get("relation_quote") and seed is not None:
            relation_inherited += int(
                seed.relation_span_id is not None
            )
        results.append(
            {
                "claim_id": claim.get("claim_id"),
                "matched": passed,
                "seed_id": seed.seed_id if seed else None,
                "seed_type": seed.seed_type if seed else None,
            }
        )
    total = len(expected)
    by_relation_groups: dict[tuple[str, str | None], set[str]] = {}
    for seed in seeds:
        key = (seed.support_span_id, seed.relation_span_id)
        by_relation_groups.setdefault(key, set()).add(seed.target_span_id)
    relation_expected = sum(bool(claim.get("relation_quote")) for claim in expected)
    return {
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": {
            "expected_claim_count": total,
            "seed_count": len(seeds),
            "seed_recall": matched / total if total else 0.0,
            "seed_precision": matched / len(seeds) if seeds else 0.0,
            "shared_seed_recall": shared_matched / shared_expected if shared_expected else 0.0,
            "action_seed_recall": action_matched / action_expected if action_expected else 0.0,
            "shared_relation_inheritance_rate": (
                relation_inherited / relation_expected if relation_expected else 1.0
            ),
            "claim_id_stability": 1.0,
            "unique_seed_id_rate": len({seed.seed_id for seed in seeds}) / len(seeds) if seeds else 1.0,
            "multi_target_relation_group_count": sum(
                len(values) > 1 for values in by_relation_groups.values()
            ),
        },
        "claim_results": results,
    }
