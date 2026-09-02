"""Support-first anchor selection over the reduced V12 span graph."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .v10_contracts import V10FieldRole
from .v12_contracts import V12_ANCHOR_ELIGIBILITY_VERSION
from .v12_graph import V12SpanGraph, V12SpanNode


@dataclass(frozen=True)
class V12Anchor:
    anchor: V12SpanNode
    rank: int
    reason: str
    priority: tuple[int, ...]
    child_role_count: int
    has_parser_child: bool
    over_broad: bool


_ANCHOR_CHILD_ROLES = {
    V10FieldRole.TARGET,
    V10FieldRole.RELATION,
    V10FieldRole.TEMPORAL,
    V10FieldRole.MEASUREMENT,
}
_PARSER_MARKERS = (
    "deterministic-generic-expression",
    "temporal_parser",
    "measurement_parser",
)


def _fragment(text: str) -> bool:
    return not text.strip() or all(not item.isalnum() for item in text)


def _boundary_complete(text: str) -> int:
    # Clause-like roots commonly end at punctuation or source end.  This is
    # generic boundary evidence, not semantic extraction.
    return int(not text[-1].isalnum())


def _has_parser_provenance(node: V12SpanNode) -> bool:
    return any(marker in provenance for provenance in node.provenances for marker in _PARSER_MARKERS)


def anchor_priority(
    anchor: V12SpanNode,
    children: Iterable[V12SpanNode],
) -> tuple[Any, ...]:
    children = list(children)
    child_roles = {role for child in children for role in child.eligible_roles}
    has_parser_child = any(
        _has_parser_provenance(child) for child in children
    )
    direct_count = len(children)
    child_count_penalty = abs(min(direct_count, 48) - 12)
    length_penalty = abs(anchor.width - 14)
    extractor_agreement = len(set(anchor.provenances))
    return (
        0 if has_parser_child else 1,
        -len(child_roles & set(V10FieldRole)),
        child_count_penalty,
        0 if _boundary_complete(anchor.text) else 1,
        length_penalty,
        -extractor_agreement,
        -anchor.max_score,
        anchor.start,
        anchor.end,
    )


def select_support_anchors(
    graph: V12SpanGraph,
    *,
    alternatives: int = 3,
    max_width: int = 48,
) -> list[V12Anchor]:
    """Select clause-like topological roots with deterministic priority."""

    if alternatives < 1:
        raise ValueError("v12_anchor_alternatives_must_be_positive")
    roots = [
        node
        for node in graph.nodes.values()
        if graph.containment.in_degree(node.node_id) == 0
    ]
    smaller_roots = any(node.width < len(graph.source_text) for node in roots)
    candidates: list[tuple[tuple[int, ...], V12SpanNode, list[V12SpanNode], bool, bool]] = []
    for node in roots:
        if _fragment(node.text) or node.width < 2 or node.width > max_width:
            continue
        children = graph.direct_children(node.node_id)
        child_roles = {role for child in children for role in child.eligible_roles}
        has_parser_child = any(
            _has_parser_provenance(child) for child in children
        )
        if not (child_roles & _ANCHOR_CHILD_ROLES or has_parser_child):
            continue
        covers_source = node.start == 0 and node.end == len(graph.source_text)
        if covers_source and smaller_roots:
            continue
        candidates.append(
            (
                anchor_priority(node, children),
                node,
                children,
                has_parser_child,
                covers_source,
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1].node_id))
    result: list[V12Anchor] = []
    for rank, (priority, node, children, has_parser_child, broad) in enumerate(
        candidates[:alternatives], start=1
    ):
        child_roles = {role for child in children for role in child.eligible_roles}
        result.append(
            V12Anchor(
                anchor=node,
                rank=rank,
                reason=(
                    "topological_root_with_parser_child"
                    if has_parser_child
                    else "topological_root_with_role_diverse_children"
                ),
                priority=priority,
                child_role_count=len(child_roles),
                has_parser_child=has_parser_child,
                over_broad=broad,
            )
        )
    return result


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_anchor_coverage(
    *,
    fixture: Any,
    anchors_by_unit: dict[str, list[V12Anchor]],
) -> dict[str, Any]:
    support_fields = [
        field
        for field in fixture.fields
        if field.status == "active" and field.field_role == V10FieldRole.SUPPORT
    ]
    recalls = {1: 0, 2: 0, 3: 0}
    for field in support_fields:
        anchors = anchors_by_unit.get(field.unit_id, [])
        for top_k in recalls:
            recalls[top_k] += int(
                any(
                    anchor.anchor.start == field.start and anchor.anchor.end == field.end
                    for anchor in anchors[:top_k]
                )
            )
    all_anchors = [anchor for anchors in anchors_by_unit.values() for anchor in anchors]
    selected = len(all_anchors)
    denominator = len(support_fields)
    return {
        "experiment_id": "ANCHOR-TOPO",
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "anchor_eligibility_version": V12_ANCHOR_ELIGIBILITY_VERSION,
        "metrics": {
            **{
                f"gold_support_anchor_recall@{key}": value / denominator
                for key, value in recalls.items()
            },
            "anchor_precision": _rate(recalls[1], selected),
            "selected_anchor_count": selected,
            "over_broad_anchor_rate": _rate(
                sum(anchor.over_broad for anchor in all_anchors), selected
            ),
            "anchor_without_valid_child_rate": _rate(
                sum(anchor.child_role_count == 0 for anchor in all_anchors), selected
            ),
        },
        "anchors": {
            unit_id: [
                {
                    "rank": anchor.rank,
                    "span_id": anchor.anchor.span_id,
                    "start": anchor.anchor.start,
                    "end": anchor.anchor.end,
                    "text": anchor.anchor.text,
                    "reason": anchor.reason,
                }
                for anchor in anchors
            ]
            for unit_id, anchors in anchors_by_unit.items()
        },
    }
