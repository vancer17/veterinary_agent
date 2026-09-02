"""Local conflict-resolution controls for V12 role menus."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .v10_contracts import V10FieldRole
from .v12_anchor import V12Anchor
from .v12_contracts import V12_CONFLICT_RESOLUTION_VERSION, V12ConflictVariant
from .v12_graph import V12SpanGraph, V12SpanNode


def _contains(left: V12SpanNode, right: V12SpanNode) -> bool:
    return left.start <= right.start and right.end <= left.end


def _global_filter(nodes: list[V12SpanNode]) -> set[str]:
    selected: list[V12SpanNode] = []
    for node in sorted(nodes, key=lambda item: (-item.max_score, item.width, item.start, item.end)):
        if any(_contains(existing, node) or _contains(node, existing) for existing in selected):
            continue
        selected.append(node)
    return {node.node_id for node in selected}


def _same_role_filter(
    nodes: list[V12SpanNode],
    *,
    same_anchor: bool,
) -> set[str]:
    selected: list[V12SpanNode] = []
    for node in sorted(nodes, key=lambda item: (item.start, -item.width, -item.max_score)):
        competitors = selected
        if same_anchor:
            # At same-anchor scope all nodes in this list already share anchor.
            competitors = selected
        if any(_contains(existing, node) or _contains(node, existing) for existing in competitors):
            continue
        selected.append(node)
    return {node.node_id for node in selected}


def _score_margin_filter(nodes: list[V12SpanNode]) -> set[str]:
    selected: set[str] = set()
    ordered = sorted(nodes, key=lambda item: (item.start, -item.width, -item.max_score))
    for node in ordered:
        competitors = [existing for existing in ordered if existing.node_id in selected]
        close = [
            existing
            for existing in competitors
            if (_contains(existing, node) or _contains(node, existing))
            and abs(existing.max_score - node.max_score) <= 0.15
        ]
        if close:
            continue
        selected.add(node.node_id)
    return selected


def evaluate_conflict_variants(
    *,
    graph: V12SpanGraph,
    anchor: V12Anchor,
    fixture_fields: Iterable[Any],
) -> dict[str, Any]:
    node_ids = {anchor.anchor.node_id} | graph.descendants(anchor.anchor.node_id, limit=16)
    nodes = [graph.nodes[node_id] for node_id in node_ids]
    expected_boundaries = {
        (field.start, field.end)
        for field in fixture_fields
        if field.unit_id == graph.unit_id and field.status == "active"
    }
    available_gold = {
        node.node_id
        for node in nodes
        if (node.start, node.end) in expected_boundaries
    }
    variants: dict[str, set[str]] = {
        V12ConflictVariant.NO_PRUNING.value: {node.node_id for node in nodes},
        V12ConflictVariant.GLOBAL_FILTER_SPANS.value: _global_filter(nodes),
        V12ConflictVariant.SAME_ROLE.value: {
            node_id
            for role in V10FieldRole
            for node_id in _same_role_filter(
                [node for node in nodes if role in node.eligible_roles],
                same_anchor=False,
            )
        },
        V12ConflictVariant.SAME_ANCHOR_ROLE.value: {
            node_id
            for role in V10FieldRole
            for node_id in _same_role_filter(
                [node for node in nodes if role in node.eligible_roles],
                same_anchor=True,
            )
        },
        V12ConflictVariant.SCORE_MARGIN.value: _score_margin_filter(nodes),
    }
    result: dict[str, Any] = {
        "experiment_id": "ANCHOR-NMS",
        "conflict_resolution_version": V12_CONFLICT_RESOLUTION_VERSION,
        "unit_id": graph.unit_id,
        "anchor_span_id": anchor.anchor.span_id,
        "variants": {},
    }
    base_count = len(nodes)
    for name, retained in variants.items():
        retained_gold = len(available_gold & retained)
        result["variants"][name] = {
            "unique_candidate_count": len(retained),
            "candidate_reduction_rate": 1.0 - len(retained) / base_count if base_count else 0.0,
            "gold_retention_rate": retained_gold / len(available_gold) if available_gold else 1.0,
            "false_suppression_count": len(available_gold - retained),
        }
    return result
