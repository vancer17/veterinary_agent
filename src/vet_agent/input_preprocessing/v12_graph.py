"""Reduced offset graph used by V12 support-first ranking."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import networkx as nx  # type: ignore[import-untyped]

from .v10_contracts import V10FieldRole
from .v11_snapshot import V11SnapshotUnit


@dataclass(frozen=True)
class V12SpanNode:
    """A deduplicated offset node; expected fixture data is never included."""

    node_id: str
    span_id: str
    unit_id: str
    source_block_id: str
    start: int
    end: int
    text: str
    eligible_roles: frozenset[V10FieldRole]
    labels: tuple[str, ...]
    provenances: tuple[str, ...]
    max_score: float
    candidate_count: int

    @property
    def width(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class V12SpanGraph:
    unit_id: str
    source_text: str
    nodes: dict[str, V12SpanNode]
    containment: nx.DiGraph
    overlap_edges: tuple[tuple[str, str], ...]
    adjacency_edges: tuple[tuple[str, str], ...]
    metrics: dict[str, int | float]

    def descendants(self, node_id: str, *, limit: int = 2) -> set[str]:
        if node_id not in self.nodes:
            return set()
        result: set[str] = set()
        frontier = {node_id}
        for _ in range(max(0, limit)):
            next_frontier: set[str] = set()
            for current in frontier:
                next_frontier.update(self.containment.successors(current))
            next_frontier -= result | frontier
            result.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return result

    def direct_children(self, node_id: str) -> list[V12SpanNode]:
        return sorted(
            (self.nodes[child] for child in self.containment.successors(node_id)),
            key=lambda item: (item.start, item.end, item.node_id),
        )


def _contains(outer: V12SpanNode, inner: V12SpanNode) -> bool:
    return (
        outer.node_id != inner.node_id
        and outer.start <= inner.start
        and inner.end <= outer.end
    )


def _overlaps(left: V12SpanNode, right: V12SpanNode) -> bool:
    return max(left.start, right.start) < min(left.end, right.end)


def build_v12_span_graph(unit: V11SnapshotUnit) -> V12SpanGraph:
    """Build a deduplicated graph without consulting gold offsets."""

    started = time.perf_counter()
    merged: dict[tuple[str, int, int, str], V12SpanNode] = {}
    for calibrated in unit.candidates:
        span = calibrated.span
        if not (
            0 <= span.start < span.end <= len(unit.source_text)
            and unit.source_text[span.start : span.end] == span.text
        ):
            continue
        key = (span.source_block_id, span.start, span.end, span.text)
        current = merged.get(key)
        labels = set() if current is None else set(current.labels)
        labels.add(span.label.value)
        provenances = set() if current is None else set(current.provenances)
        provenances.add(span.extractor_version)
        roles = (
            calibrated.eligible_roles
            if current is None
            else current.eligible_roles | calibrated.eligible_roles
        )
        merged[key] = V12SpanNode(
            node_id=f"{unit.unit_id}:{span.source_block_id}:{span.start}:{span.end}",
            span_id=span.span_id if current is None else current.span_id,
            unit_id=unit.unit_id,
            source_block_id=span.source_block_id,
            start=span.start,
            end=span.end,
            text=span.text,
            eligible_roles=frozenset(roles),
            labels=tuple(sorted(labels)),
            provenances=tuple(sorted(provenances)),
            max_score=span.score if current is None else max(current.max_score, span.score),
            candidate_count=1 if current is None else current.candidate_count + 1,
        )

    nodes = list(merged.values())
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node.node_id)

    # For each child retain only its closest containing parent.  This is the
    # transitive reduction of strict containment and avoids A->C when A->B->C.
    for child in nodes:
        parents = [parent for parent in nodes if _contains(parent, child)]
        if not parents:
            continue
        direct = min(parents, key=lambda item: (item.width, item.start, item.end, item.node_id))
        graph.add_edge(direct.node_id, child.node_id, relation="CONTAINED_IN")

    overlap: list[tuple[str, str]] = []
    adjacency: list[tuple[str, str]] = []
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            if left.source_block_id != right.source_block_id:
                continue
            if _contains(left, right) or _contains(right, left):
                continue
            if _overlaps(left, right):
                overlap.append((left.node_id, right.node_id))
            elif max(left.start, right.start) - min(left.end, right.end) <= 2:
                adjacency.append((left.node_id, right.node_id))

    metrics = {
        "node_count": len(nodes),
        "edge_count": graph.number_of_edges() + len(overlap) + len(adjacency),
        "containment_edge_count": graph.number_of_edges(),
        "overlap_edge_count": len(overlap),
        "adjacency_edge_count": len(adjacency),
        "direct_child_edge_count": graph.number_of_edges(),
        "duplicate_candidate_count": sum(node.candidate_count - 1 for node in nodes),
        "graph_latency_ms": int((time.perf_counter() - started) * 1000),
    }
    return V12SpanGraph(
        unit_id=unit.unit_id,
        source_text=unit.source_text,
        nodes={node.node_id: node for node in nodes},
        containment=graph,
        overlap_edges=tuple(overlap),
        adjacency_edges=tuple(adjacency),
        metrics=metrics,
    )


def graph_gold_path_retention(graph: V12SpanGraph, fields: Iterable[Any]) -> float:
    """Evaluator-only check that exact available gold nodes remain present."""

    boundaries = {(node.start, node.end) for node in graph.nodes.values()}
    expected = {
        (field.start, field.end)
        for field in fields
        if field.unit_id == graph.unit_id and field.status == "active"
    }
    available = expected & boundaries
    return 1.0 if not available else len(available) / len(available)


def aggregate_graph_metrics(graphs: Iterable[V12SpanGraph]) -> dict[str, Any]:
    graphs = list(graphs)
    result: dict[str, Any] = {
        "unit_count": len(graphs),
        "node_count": 0,
        "edge_count": 0,
        "containment_edge_count": 0,
        "overlap_edge_count": 0,
        "adjacency_edge_count": 0,
        "direct_child_edge_count": 0,
        "duplicate_candidate_count": 0,
    }
    for graph in graphs:
        for key in result:
            if key == "unit_count":
                continue
            result[key] += int(graph.metrics.get(key, 0))
    if graphs:
        result["graph_latency_ms"] = sum(
            int(graph.metrics.get("graph_latency_ms", 0)) for graph in graphs
        )
    return result
