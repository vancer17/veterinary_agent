"""Role-specific views, offset graphs, and deterministic ranking for V11."""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from .v10_contracts import V10CalibratedSpan, V10FieldRole
from .v11_contracts import V11RoleMenuRecord

ViewMode = Literal["global", "role", "role-fallback", "claim-local"]
RankMode = Literal["base", "cross"]

_REGION_SPLIT = re.compile(r"[，。！？；;]")
_ROLE_OPTIMAL_LENGTH: dict[V10FieldRole, int] = {
    V10FieldRole.SUPPORT: 12,
    V10FieldRole.TARGET: 5,
    V10FieldRole.RELATION: 4,
    V10FieldRole.SUBJECT: 2,
    V10FieldRole.ACTION_AGENT: 2,
    V10FieldRole.ACTION_RECIPIENT: 2,
    V10FieldRole.EXPERIENCER: 2,
    V10FieldRole.OBJECT: 3,
    V10FieldRole.TEMPORAL: 5,
    V10FieldRole.MEASUREMENT: 4,
    V10FieldRole.EVIDENCE: 12,
}
_ROLE_QUERIES: dict[V10FieldRole, str] = {
    V10FieldRole.SUPPORT: "当前局部子句中承载一个独立用户声明的完整支持短语",
    V10FieldRole.TARGET: "当前局部子句中被陈述或动作指向的核心目标对象或事件短语",
    V10FieldRole.RELATION: "当前局部子句中的否定、正常、变化、比较或状态关系表达",
    V10FieldRole.SUBJECT: "当前局部子句中被陈述的主体 mention",
    V10FieldRole.ACTION_AGENT: "当前局部子句中执行动作的人或动物 mention",
    V10FieldRole.ACTION_RECIPIENT: "当前局部子句中承受动作的人或动物 mention",
    V10FieldRole.EXPERIENCER: "当前局部子句中体验状态的人或动物 mention",
    V10FieldRole.OBJECT: "当前局部子句中动作涉及的物体、食物或药物 mention",
    V10FieldRole.TEMPORAL: "当前局部子句中的时间、起点、持续时间或频率表达",
    V10FieldRole.MEASUREMENT: "当前局部子句中的数量、单位、频率或量度表达",
    V10FieldRole.EVIDENCE: "当前子句中支持 discourse act 的直接证据短语",
}


@dataclass(frozen=True)
class V11Region:
    region_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class V11RankedCandidate:
    item: V10CalibratedSpan
    role: V10FieldRole
    region: V11Region
    source: Literal["primary", "fallback"]
    base_score: float
    rerank_score: float | None = None
    final_score: float = 0.0
    rank: int = 0
    reason: str = ""


@dataclass(frozen=True)
class V11UnitRanking:
    unit_id: str
    text: str
    regions: tuple[V11Region, ...]
    rankings: tuple[tuple[V11Region, V10FieldRole, tuple[V11RankedCandidate, ...]], ...]


@dataclass(frozen=True)
class V11BgeReranker:
    """A local cross-encoder adapter that only scores existing candidates."""

    model_path: str
    adapter: str = "transformers-auto-sequence-classification"
    device: str = "cpu"
    batch_size: int = 4
    max_length: int = 256

    def __post_init__(self) -> None:
        if not self.model_path:
            raise ValueError("v11_reranker_model_required")
        try:
            import torch  # type: ignore[import-not-found]
            from transformers import (  # type: ignore[import-not-found]
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            raise ValueError("v11_reranker_unavailable") from exc
        if self.device != "cpu":
            raise ValueError("v11_reranker_cpu_only")
        self.__dict__["tokenizer"] = AutoTokenizer.from_pretrained(
            self.model_path,
            local_files_only=True,
        )
        self.__dict__["model"] = AutoModelForSequenceClassification.from_pretrained(
            self.model_path,
            local_files_only=True,
        ).to(self.device)
        self.__dict__["model"].eval()
        self.__dict__["torch"] = torch

    def score(self, queries: list[str], documents: list[str]) -> list[float]:
        if len(queries) != len(documents):
            raise ValueError("v11_reranker_query_document_length_mismatch")
        if not queries:
            return []
        torch = self.__dict__["torch"]
        tokenizer = self.__dict__["tokenizer"]
        model = self.__dict__["model"]
        result: list[float] = []
        with torch.inference_mode():
            for offset in range(0, len(queries), self.batch_size):
                chunk_queries = queries[offset : offset + self.batch_size]
                chunk_documents = documents[offset : offset + self.batch_size]
                encoded = tokenizer(
                    chunk_queries,
                    chunk_documents,
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                logits = model(**encoded.to(self.device)).logits
                # BGE rerankers expose relevance as a single classification logit.
                values = logits[:, 0].detach().float().cpu().tolist()
                result.extend(float(value) for value in values)
        if any(not math.isfinite(value) for value in result):
            raise ValueError("v11_reranker_non_finite_score")
        return result


def split_runtime_regions(text: str) -> tuple[V11Region, ...]:
    """Derive claim-local regions using only generic punctuation."""

    regions: list[V11Region] = []
    cursor = 0
    for match in _REGION_SPLIT.finditer(text):
        end = match.end()
        raw = text[cursor:end]
        stripped = raw.strip()
        if stripped:
            relative = len(raw) - len(raw.lstrip())
            start = cursor + relative
            regions.append(
                V11Region(
                    region_id=hashlib.sha256(
                        f"{start}:{end}:{stripped}".encode()
                    ).hexdigest()[:16],
                    start=start,
                    end=start + len(stripped),
                    text=stripped,
                )
            )
        cursor = end
    tail = text[cursor:].strip()
    if tail:
        relative = len(text[cursor:]) - len(text[cursor:].lstrip())
        start = cursor + relative
        regions.append(
            V11Region(
                region_id=hashlib.sha256(f"{start}:{start + len(tail)}:{tail}".encode()).hexdigest()[:16],
                start=start,
                end=start + len(tail),
                text=tail,
            )
        )
    return tuple(regions)


def build_span_graph(
    *,
    unit_id: str,
    text: str,
    candidates: list[V10CalibratedSpan],
    regions: tuple[V11Region, ...],
) -> dict[str, Any]:
    """Build an in-memory NetworkX graph without persistent infrastructure."""

    import networkx as nx  # type: ignore[import-untyped]

    graph = nx.MultiDiGraph(unit_id=unit_id)
    for index, item in enumerate(candidates):
        graph.add_node(
            f"span:{item.span.span_id}",
            kind="span",
            start=item.span.start,
            end=item.span.end,
            label=item.span.label.value,
        )
    for region in regions:
        graph.add_node(
            f"region:{region.region_id}",
            kind="claim_region",
            start=region.start,
            end=region.end,
        )
    edge_count = 0
    for region in regions:
        for item in candidates:
            if overlaps(region.start, region.end, item.span.start, item.span.end):
                graph.add_edge(
                    f"span:{item.span.span_id}",
                    f"region:{region.region_id}",
                    relation="ROLE_CANDIDATE_FOR",
                    roles=sorted(role.value for role in item.eligible_roles),
                )
                edge_count += 1
    for left in candidates:
        for right in candidates:
            if left is right:
                continue
            if left.span.start <= right.span.start and right.span.end <= left.span.end:
                graph.add_edge(
                    f"span:{left.span.span_id}",
                    f"span:{right.span.span_id}",
                    relation="CONTAINED_IN",
                )
                edge_count += 1
            elif overlaps(left.span.start, left.span.end, right.span.start, right.span.end):
                graph.add_edge(
                    f"span:{left.span.span_id}",
                    f"span:{right.span.span_id}",
                    relation="OVERLAPS",
                )
                edge_count += 1
    valid_offsets = all(
        0 <= item.span.start < item.span.end <= len(text)
        and text[item.span.start : item.span.end] == item.span.text
        for item in candidates
    )
    return {
        "graph": graph,
        "metrics": {
            "node_count": graph.number_of_nodes(),
            "edge_count": edge_count,
            "region_count": len(regions),
            "graph_edge_valid_rate": 1.0 if valid_offsets else 0.0,
            "graph_latency_note": "included_in_view_construction",
        },
    }


def overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) < min(left_end, right_end)


def _base_score(
    item: V10CalibratedSpan,
    role: V10FieldRole,
    *,
    primary: bool,
) -> float:
    length = item.span.end - item.span.start
    optimal = _ROLE_OPTIMAL_LENGTH[role]
    provenance = item.span.extractor_version
    parser_bonus = 0.20
    if role is V10FieldRole.TEMPORAL and "deterministic-generic-expression" in provenance or role is V10FieldRole.MEASUREMENT and "deterministic-generic-expression" in provenance:
        parser_bonus = 0.35
    primary_bonus = 0.45 if primary else 0.0
    parent_bonus = 0.10 if ":v10-D" not in provenance else 0.0
    length_score = 1.0 / (1.0 + abs(length - optimal))
    raw_score = max(0.0, min(1.0, item.span.score))
    return min(
        1.0,
        primary_bonus + parser_bonus + parent_bonus + raw_score * 0.25 + length_score * 0.20,
    )


def base_rank_candidates(
    *,
    role: V10FieldRole,
    region: V11Region,
    candidates: list[V10CalibratedSpan],
) -> list[V11RankedCandidate]:
    result: list[V11RankedCandidate] = []
    for item in candidates:
        if not overlaps(region.start, region.end, item.span.start, item.span.end):
            continue
        primary = role in item.eligible_roles
        score = _base_score(item, role, primary=primary)
        result.append(
            V11RankedCandidate(
                item=item,
                role=role,
                region=region,
                source="primary" if primary else "fallback",
                base_score=score,
                final_score=score,
                reason="" if primary else "role_fallback_candidate",
            )
        )
    result.sort(
        key=lambda value: (
            0 if value.source == "primary" else 1,
            -value.final_score,
            abs((value.item.span.end - value.item.span.start) - _ROLE_OPTIMAL_LENGTH[role]),
            value.item.span.start,
            value.item.span.end,
            value.item.span.span_id,
        )
    )
    return [
        V11RankedCandidate(**{**value.__dict__, "rank": index})
        for index, value in enumerate(result, start=1)
    ]


def rank_unit(
    *,
    unit_id: str,
    text: str,
    candidates: list[V10CalibratedSpan],
    mode: RankMode,
    reranker: V11BgeReranker | None = None,
    prefilter: int = 64,
) -> V11UnitRanking:
    regions = split_runtime_regions(text)
    ranked_groups: list[tuple[V11Region, V10FieldRole, tuple[V11RankedCandidate, ...]]] = []
    for region in regions:
        for role in V10FieldRole:
            ranked = base_rank_candidates(
                role=role,
                region=region,
                candidates=candidates,
            )
            if mode == "cross":
                if reranker is None:
                    raise ValueError("v11_cross_rank_requires_reranker")
                pool = ranked[: max(1, prefilter)]
                queries = [
                    f"{_ROLE_QUERIES[role]}。候选来自局部上下文：{region.text}"
                    for _ in pool
                ]
                documents = [
                    (
                        f"上下文：{region.text}\n候选：{value.item.span.text}\n"
                        f"标签：{value.item.span.label.value}\n"
                        f"来源：{value.item.span.extractor_version}"
                    )
                    for value in pool
                ]
                started = time.perf_counter()
                scores = reranker.score(queries, documents)
                latency = int((time.perf_counter() - started) * 1000)
                reranked = [
                    V11RankedCandidate(
                        **{
                            **value.__dict__,
                            "rerank_score": score,
                            # A stable sigmoid keeps report scores bounded.  It
                            # does not alter offsets, text, or candidate IDs.
                            "final_score": 1.0 / (1.0 + math.exp(-score)),
                            "reason": (
                                value.reason
                                if value.reason
                                else "cross_encoder_primary_rank"
                            ),
                        }
                    )
                    for value, score in zip(pool, scores, strict=True)
                ]
                reranked.sort(
                    key=lambda value: (
                        0 if value.source == "primary" else 1,
                        -value.final_score,
                        value.item.span.start,
                        value.item.span.end,
                        value.item.span.span_id,
                    )
                )
                ranked = [
                    V11RankedCandidate(**{**value.__dict__, "rank": index})
                    for index, value in enumerate(reranked, start=1)
                ]
                # Rerank latency is measured at suite level to avoid mutating a
                # frozen candidate record after ranking.
                _ = latency
            ranked_groups.append((region, role, tuple(ranked)))
    return V11UnitRanking(
        unit_id=unit_id,
        text=text,
        regions=regions,
        rankings=tuple(ranked_groups),
    )


def role_menu(
    ranking: V11UnitRanking,
    *,
    region: V11Region,
    role: V10FieldRole,
    top_k: int,
    mode: ViewMode,
) -> list[V11RoleMenuRecord]:
    group = next(
        (
            value
            for current_region, current_role, value in ranking.rankings
            if current_region.region_id == region.region_id and current_role is role
        ),
        (),
    )
    if mode == "global":
        group = tuple(
            sorted(
                (
                    value
                    for current_region, current_role, values in ranking.rankings
                    if current_role is role
                    for value in values
                ),
                key=lambda value: value.rank,
            )
        )
    filtered: list[V11RankedCandidate] = []
    for value in group:
        if mode == "role" and value.source != "primary":
            continue
        if mode in {"role", "role-fallback", "claim-local"} and not overlaps(
            region.start, region.end, value.item.span.start, value.item.span.end
        ):
            continue
        filtered.append(value)
        if len(filtered) >= top_k:
            break
    return [
        V11RoleMenuRecord(
            role=role.value,
            span_id=value.item.span.span_id,
            source=value.source,
            reason=value.reason,
            rank=value.rank,
            score=round(value.final_score, 6),
            label=value.item.span.label,
            text=value.item.span.text,
            start=value.item.span.start,
            end=value.item.span.end,
        )
        for value in filtered
    ]


def build_reranker_from_environment() -> V11BgeReranker:
    return V11BgeReranker(
        model_path=os.environ["INPUT_PREPROCESSING_V11_RERANKER_MODEL"],
        adapter=os.getenv("INPUT_PREPROCESSING_V11_RERANKER_ADAPTER", "transformers-auto-sequence-classification"),
        device=os.getenv("INPUT_PREPROCESSING_V11_RERANKER_DEVICE", "cpu"),
        batch_size=int(os.getenv("INPUT_PREPROCESSING_V11_RERANKER_BATCH_SIZE", "4")),
        max_length=int(os.getenv("INPUT_PREPROCESSING_V11_RERANKER_MAX_LENGTH", "256")),
    )


def _field_region(
    ranking: V11UnitRanking,
    *,
    start: int,
    end: int,
) -> V11Region:
    midpoint = (start + end) // 2
    overlapping = [
        region
        for region in ranking.regions
        if region.start <= midpoint < region.end or overlaps(region.start, region.end, start, end)
    ]
    if overlapping:
        return min(
            overlapping,
            key=lambda region: (
                max(0, min(region.end, end) - max(region.start, start)),
                region.start,
            ),
        )
    return max(
        ranking.regions,
        key=lambda region: min(
            abs(midpoint - region.start),
            abs(midpoint - region.end),
        ),
    )


def _group(
    ranking: V11UnitRanking,
    *,
    region: V11Region,
    role: V10FieldRole,
) -> tuple[V11RankedCandidate, ...]:
    return next(
        (
            values
            for current_region, current_role, values in ranking.rankings
            if current_region.region_id == region.region_id and current_role is role
        ),
        (),
    )


def evaluate_ranking(
    *,
    fields: list[Any],
    rankings: dict[str, V11UnitRanking],
    ks: tuple[int, ...] = (1, 3, 5, 16),
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "expected_field_count": 0,
    }
    for value in ks:
        metrics[f"gold_in_top_{value}"] = 0
        metrics[f"precision_at_{value}"] = 0.0
        metrics[f"role_coverage_at_{value}"] = 0
    primary_gold = 0
    fallback_gold = 0
    menu_count = 0
    candidate_count = 0
    results: list[dict[str, Any]] = []
    role_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"expected": 0, "gold_top_5": 0})

    for field in fields:
        if field.status != "active":
            continue
        ranking = rankings.get(field.unit_id)
        if ranking is None:
            continue
        metrics["expected_field_count"] += 1
        role_totals[field.field_role.value]["expected"] += 1
        region = _field_region(ranking, start=field.start, end=field.end)
        group = _group(ranking, region=region, role=field.field_role)
        exact = [
            value
            for value in group
            if value.item.span.start == field.start and value.item.span.end == field.end
        ]
        exact_rank = exact[0].rank if exact else 0
        if exact and exact_rank <= 5:
            role_totals[field.field_role.value]["gold_top_5"] += 1
        primary_gold += int(bool(exact and exact[0].source == "primary"))
        fallback_gold += int(bool(exact and exact[0].source == "fallback"))
        for value in ks:
            metrics[f"gold_in_top_{value}"] += int(bool(exact and exact_rank <= value))
            top = group[:value]
            metrics[f"precision_at_{value}"] += sum(
                item.item.span.start == field.start and item.item.span.end == field.end
                for item in top
            )
            metrics[f"role_coverage_at_{value}"] += int(
                bool(exact and exact_rank <= value and exact[0].source == "primary")
            )
        results.append(
            {
                "unit_id": field.unit_id,
                "role": field.field_role.value,
                "field_role": field.field_role.value,
                "expected_start": field.start,
                "expected_end": field.end,
                "region_id": region.region_id,
                "exact": bool(exact),
                "rank": exact_rank,
                "source": exact[0].source if exact else "",
            }
        )
    denominator = max(1, metrics["expected_field_count"])
    for value in ks:
        metrics[f"gold_in_top_{value}"] = metrics[f"gold_in_top_{value}"] / denominator
        metrics[f"precision_at_{value}"] = metrics[f"precision_at_{value}"] / denominator
        metrics[f"role_coverage_at_{value}"] = metrics[f"role_coverage_at_{value}"] / denominator
    for ranking in rankings.values():
        for _, _, group in ranking.rankings:
            menu_count += 1
            candidate_count += len(group)
    metrics.update(
        {
            "primary_gold_rate": primary_gold / denominator,
            "fallback_gold_rate": fallback_gold / denominator,
            "menu_count": menu_count,
            "ranked_candidate_count": candidate_count,
        }
    )
    return {
        "status": "completed",
        "diagnostic_only": True,
        "can_unblock_v8_phase": False,
        "metrics": metrics,
        "per_role": {
            role: {
                "expected": values["expected"],
                "gold_top_5": values["gold_top_5"] / values["expected"] if values["expected"] else 0.0,
            }
            for role, values in sorted(role_totals.items())
        },
        "field_results": results,
    }


def evaluate_view_coverage(
    *,
    fields: list[Any],
    rankings: dict[str, V11UnitRanking],
    top_k: int,
    modes: tuple[ViewMode, ...],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for mode in modes:
        gold_in_view = 0
        primary_gold = 0
        fallback_gold = 0
        empty_menus = 0
        menu_count = 0
        candidate_count = 0
        false_pruned = 0
        fields_total = 0
        results: list[dict[str, Any]] = []
        for field in fields:
            if field.status != "active":
                continue
            fields_total += 1
            ranking = rankings.get(field.unit_id)
            if ranking is None:
                results.append({"unit_id": field.unit_id, "role": field.field_role.value, "in_view": False})
                false_pruned += 1
                continue
            region = _field_region(ranking, start=field.start, end=field.end)
            menu = role_menu(
                ranking,
                region=region,
                role=field.field_role,
                top_k=top_k,
                mode=mode,
            )
            exact = [
                item for item in menu if item.start == field.start and item.end == field.end
            ]
            gold_in_view += int(bool(exact))
            primary_gold += int(bool(exact and exact[0].source == "primary"))
            fallback_gold += int(bool(exact and exact[0].source == "fallback"))
            false_pruned += int(not exact)
            results.append(
                {
                    "unit_id": field.unit_id,
                    "role": field.field_role.value,
                    "in_view": bool(exact),
                    "source": exact[0].source if exact else "",
                }
            )
        for ranking in rankings.values():
            for region in ranking.regions:
                for role in V10FieldRole:
                    menu_count += 1
                    menu = role_menu(
                        ranking,
                        region=region,
                        role=role,
                        top_k=top_k,
                        mode=mode,
                    )
                    candidate_count += len(menu)
                    empty_menus += int(not menu)
        denominator = max(1, fields_total)
        reports.append(
            {
                "experiment_id": "VIEW-COVERAGE",
                "view_mode": mode,
                "status": "completed",
                "diagnostic_only": True,
                "can_unblock_v8_phase": False,
                "metrics": {
                    "gold_in_view_rate": gold_in_view / denominator,
                    "primary_gold_rate": primary_gold / denominator,
                    "fallback_gold_rate": fallback_gold / denominator,
                    "false_pruned_candidate_rate": false_pruned / denominator,
                    "empty_role_menu_rate": empty_menus / max(1, menu_count),
                    "empty_role_menu_count": empty_menus,
                    "menu_count": menu_count,
                    "view_candidate_count": candidate_count,
                },
                "field_results": results,
            }
        )
    return reports
