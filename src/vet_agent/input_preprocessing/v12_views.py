"""Role-local candidate menus derived from V12 support anchors."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .v8_contracts import V8SpanLabel
from .v10_contracts import V10FieldRole
from .v11_contracts import V11RoleMenuRecord
from .v12_anchor import V12Anchor
from .v12_contracts import V12_VIEW_VERSION
from .v12_graph import V12SpanGraph, V12SpanNode


@dataclass(frozen=True)
class V12AnchorView:
    anchor: V12Anchor
    menus: dict[str, list[V11RoleMenuRecord]]


@dataclass(frozen=True)
class V12UnitView:
    unit_id: str
    anchors: list[V12AnchorView]
    act_menu: list[V11RoleMenuRecord]


_ROLE_LABELS: dict[V10FieldRole, tuple[V8SpanLabel, ...]] = {
    V10FieldRole.EVIDENCE: (
        V8SpanLabel.CONTROL_INTENT_EXPRESSION,
        V8SpanLabel.STATE_MENTION,
        V8SpanLabel.ACTION_EVENT,
    ),
    V10FieldRole.SUPPORT: (
        V8SpanLabel.ACTION_EVENT,
        V8SpanLabel.STATE_MENTION,
        V8SpanLabel.TARGET_MENTION,
    ),
    V10FieldRole.TARGET: (
        V8SpanLabel.TARGET_MENTION,
        V8SpanLabel.STATE_MENTION,
        V8SpanLabel.ACTION_EVENT,
        V8SpanLabel.OBJECT_MENTION,
        V8SpanLabel.MEASUREMENT_EXPRESSION,
    ),
    V10FieldRole.RELATION: (V8SpanLabel.RELATION_EXPRESSION,),
    V10FieldRole.SUBJECT: (V8SpanLabel.SUBJECT_MENTION,),
    V10FieldRole.ACTION_AGENT: (V8SpanLabel.AGENT_MENTION, V8SpanLabel.SUBJECT_MENTION),
    V10FieldRole.ACTION_RECIPIENT: (
        V8SpanLabel.RECIPIENT_MENTION,
        V8SpanLabel.SUBJECT_MENTION,
    ),
    V10FieldRole.EXPERIENCER: (V8SpanLabel.SUBJECT_MENTION,),
    V10FieldRole.OBJECT: (V8SpanLabel.OBJECT_MENTION, V8SpanLabel.TARGET_MENTION),
    V10FieldRole.TEMPORAL: (V8SpanLabel.TEMPORAL_EXPRESSION,),
    V10FieldRole.MEASUREMENT: (V8SpanLabel.MEASUREMENT_EXPRESSION,),
}
_OPTIMAL_LENGTH = {
    V10FieldRole.EVIDENCE: 12,
    V10FieldRole.SUPPORT: 12,
    V10FieldRole.TARGET: 5,
    V10FieldRole.RELATION: 3,
    V10FieldRole.SUBJECT: 2,
    V10FieldRole.ACTION_AGENT: 2,
    V10FieldRole.ACTION_RECIPIENT: 2,
    V10FieldRole.EXPERIENCER: 2,
    V10FieldRole.OBJECT: 3,
    V10FieldRole.TEMPORAL: 4,
    V10FieldRole.MEASUREMENT: 4,
}


def _label_for_role(node: V12SpanNode, role: V10FieldRole) -> V8SpanLabel:
    labels = set(node.labels)
    for preferred in _ROLE_LABELS[role]:
        if preferred.value in labels:
            return preferred
    return V8SpanLabel(node.labels[0]) if node.labels else V8SpanLabel.CANDIDATE_SPAN


def _node_label_rank(node: V12SpanNode, role: V10FieldRole) -> int:
    preferred = [item.value for item in _ROLE_LABELS[role]]
    for index, label in enumerate(preferred):
        if label in node.labels:
            return index
    return len(preferred) + 1


def rank_nodes_for_role(
    nodes: Iterable[V12SpanNode],
    *,
    role: V10FieldRole,
    anchor_id: str | None = None,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
) -> list[V12SpanNode]:
    values = list(nodes)

    def key(node: V12SpanNode) -> tuple[Any, ...]:
        primary = role in node.eligible_roles
        length_penalty = abs(node.width - _OPTIMAL_LENGTH[role])
        anchor_first = 0 if anchor_id is not None and node.node_id == anchor_id else 1
        boundary_aligned = (anchor_start is not None and node.start == anchor_start) or (
            anchor_end is not None and node.end == anchor_end
        )
        return (
            0
            if primary
            or (role == V10FieldRole.SUPPORT and anchor_first == 0)
            else 1,
            anchor_first if role == V10FieldRole.SUPPORT else 1,
            0 if role == V10FieldRole.TARGET and boundary_aligned else 1,
            _node_label_rank(node, role),
            length_penalty,
            0 if node.text[-1:].isalnum() else 1,
            -node.max_score,
            -len(node.labels),
            node.start,
            node.end,
            node.node_id,
        )

    return sorted(values, key=key)


def _menu_records(
    nodes: Iterable[V12SpanNode],
    *,
    role: V10FieldRole,
    top_k: int,
    anchor_id: str | None,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
) -> list[V11RoleMenuRecord]:
    result: list[V11RoleMenuRecord] = []
    for rank, node in enumerate(
        rank_nodes_for_role(
            nodes,
            role=role,
            anchor_id=anchor_id,
            anchor_start=anchor_start,
            anchor_end=anchor_end,
        )[: max(0, top_k)],
        start=1,
    ):
        primary = role in node.eligible_roles or (
            role == V10FieldRole.SUPPORT and node.node_id == anchor_id
        )
        result.append(
            V11RoleMenuRecord(
                role=role.value,
                span_id=node.span_id,
                source="primary" if primary else "fallback",
                reason="" if primary else "support_first_role_fallback",
                rank=rank,
                score=max(0.0, min(1.0, node.max_score)),
                label=_label_for_role(node, role),
                text=node.text,
                start=node.start,
                end=node.end,
            )
        )
    return result


def build_role_views(
    graph: V12SpanGraph,
    anchors: list[V12Anchor],
    *,
    role_top_k: int = 8,
    target_top_k: int = 12,
    include_fallback: bool = True,
) -> V12UnitView:
    if role_top_k < 1 or target_top_k < 1:
        raise ValueError("v12_menu_limit_must_be_positive")
    anchor_views: list[V12AnchorView] = []
    for anchor in anchors:
        node_ids = {anchor.anchor.node_id} | graph.descendants(
            anchor.anchor.node_id, limit=16
        )
        nodes = [graph.nodes[node_id] for node_id in node_ids]
        menus: dict[str, list[V11RoleMenuRecord]] = {}
        for role in V10FieldRole:
            eligible = [
                node for node in nodes if role in node.eligible_roles
            ]
            if role == V10FieldRole.SUPPORT:
                pool = [
                    anchor.anchor,
                    *(node for node in eligible if node.node_id != anchor.anchor.node_id),
                ]
            else:
                pool = eligible if eligible else (nodes if include_fallback else [])
            top_k = target_top_k if role == V10FieldRole.TARGET else role_top_k
            menus[role.value] = _menu_records(
                pool,
                role=role,
                top_k=top_k,
                anchor_id=anchor.anchor.node_id,
                anchor_start=anchor.anchor.start,
                anchor_end=anchor.anchor.end,
            )
        anchor_views.append(V12AnchorView(anchor=anchor, menus=menus))

    root_nodes = [
        node
        for node in graph.nodes.values()
        if graph.containment.in_degree(node.node_id) == 0
    ]
    act_menu = _menu_records(
        root_nodes,
        role=V10FieldRole.EVIDENCE,
        top_k=max(3, role_top_k),
        anchor_id=None,
    )
    return V12UnitView(
        unit_id=graph.unit_id,
        anchors=anchor_views,
        act_menu=act_menu,
    )


def view_metrics(
    *,
    fixture: Any,
    views: dict[str, V12UnitView],
    snapshot_unique_candidates: int,
) -> dict[str, Any]:
    presented = 0
    primary = 0
    fallback = 0
    empty = 0
    unique_ids: set[str] = set()
    gold_in_view = 0
    gold_in_primary = 0
    gold_in_fallback = 0
    fields = [field for field in fixture.fields if field.status == "active"]
    for field in fields:
        view = views.get(field.unit_id)
        records = []
        if view is not None:
            records = [
                record
                for anchor_view in view.anchors
                for record in anchor_view.menus.get(field.field_role.value, [])
            ]
        exact = [
            record
            for record in records
            if record.start == field.start and record.end == field.end
        ]
        gold_in_view += int(bool(exact))
        gold_in_primary += int(any(item.source == "primary" for item in exact))
        gold_in_fallback += int(any(item.source == "fallback" for item in exact))
    for view in views.values():
        for anchor_view in view.anchors:
            for records in anchor_view.menus.values():
                presented += len(records)
                primary += sum(item.source == "primary" for item in records)
                fallback += sum(item.source == "fallback" for item in records)
                empty += int(not records)
                unique_ids.update(item.span_id for item in records)
    return {
        "view_version": V12_VIEW_VERSION,
        "snapshot_unique_candidate_count": snapshot_unique_candidates,
        "gold_in_view": gold_in_view / len(fields) if fields else 0.0,
        "gold_in_primary": gold_in_primary / len(fields) if fields else 0.0,
        "gold_in_fallback": gold_in_fallback / len(fields) if fields else 0.0,
        "view_presented_slot_count": presented,
        "primary_menu_count": primary,
        "fallback_menu_count": fallback,
        "empty_menu_count": empty,
        "fallback_rate": fallback / presented if presented else 0.0,
        "unique_candidates_sent_to_macro": len(unique_ids),
        "duplicate_presentation_count": max(0, presented - len(unique_ids)),
        "macro_input_token_count_available": False,
        "macro_input_character_count": sum(
            len(item.text)
            for view in views.values()
            for anchor_view in view.anchors
            for records in anchor_view.menus.values()
            for item in records
        ),
    }
