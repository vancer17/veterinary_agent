"""Versioned, fixed-contract relation adapter experiments for V10."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v10_contracts import (
    V10_RELATION_PROMPT_VERSION,
    V10RelationRawOutput,
    V10RelationRecordRaw,
)
from .v10_fixture import V10Fixture, V10GoldenPool, build_v10_golden_pool

Relation = Literal["absolute_status", "no_change", "change", "unclear"]


@dataclass(frozen=True)
class V10RelationRecord:
    unit_id: str
    claim_id: str
    support_quote: str
    target_quote: str
    relation_quote: str
    expected_relation: str
    input_available: bool


@dataclass(frozen=True)
class V10RelationExecution:
    output: V10RelationRawOutput
    prompt_version: str
    batch_size: int
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    model_call_count: int = 1


def relation_records(fixture: V10Fixture) -> list[V10RelationRecord]:
    fields = fixture.fields_by_unit
    records: list[V10RelationRecord] = []
    for unit in fixture.units:
        unit_id = str(unit["unit_id"])
        for claim in unit.get("expected_claims", []):
            expected = str(claim.get("expected_relation", ""))
            if not expected:
                continue
            owner = str(claim["claim_id"])

            def field(role: str, *, unit_id: str = unit_id, owner: str = owner) -> Any:
                return next(
                    (
                        item
                        for item in fields.get(unit_id, [])
                        if item.claim_owner == owner and item.field_role.value == role
                    ),
                    None,
                )

            support = field("support_quote")
            target = field("target_quote")
            relation = field("relation_quote")
            assert support is not None and target is not None
            records.append(
                V10RelationRecord(
                    unit_id=unit_id,
                    claim_id=owner,
                    support_quote=support.text,
                    target_quote=target.text,
                    relation_quote=relation.text if relation else "",
                    expected_relation=expected,
                    input_available=relation is not None and relation.status == "active",
                )
            )
    return records


class V10RelationAdapter:
    """A fixed serialization/prompt contract; no runtime calibration fallback."""

    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
        fewshot: bool = False,
    ) -> None:
        from vet_agent.runtime import QwenClient

        self.model = model
        self.cache = cache
        self.fewshot = fewshot
        settings = replace_retry_settings(make_runtime_settings())
        self.qwen = QwenClient(settings)

    async def run(
        self,
        *,
        records: list[V10RelationRecord],
        batch_size: int = 1,
        reverse_order: bool = False,
    ) -> list[V10RelationExecution]:
        if batch_size not in {1, 4, 8}:
            raise ValueError("v10_relation_batch_size_must_be_1_4_or_8")
        ordered = list(reversed(records)) if reverse_order else list(records)
        executions: list[V10RelationExecution] = []
        for offset in range(0, len(ordered), batch_size):
            chunk = ordered[offset : offset + batch_size]
            payload = [
                {
                    "unit_id": f"{record.unit_id}:{record.claim_id}",
                    "support_quote": record.support_quote,
                    "target_quote": record.target_quote,
                    "relation_quote": record.relation_quote,
                }
                for record in chunk
            ]
            key = V7RunUnitKey(
                experiment_id="V10-RELATION-FIXED",
                model=self.model,
                prompt_version=self.prompt_version,
                schema_version="v10-relation-raw-1",
                input_digest=digest_value(
                    {"records": payload, "batch_size": batch_size, "reverse": reverse_order}
                ),
                turn_context_digest=digest_value({"v10": "fixed-relation-contract"}),
                adapter="base",
            )
            cached = (
                self.cache.get(
                    key,
                    response_model_name=V10RelationRawOutput.__name__,
                )
                if self.cache is not None
                else None
            )
            if cached is not None:
                executions.append(
                    V10RelationExecution(
                        output=V10RelationRawOutput.model_validate(cached),
                        prompt_version=self.prompt_version,
                        batch_size=batch_size,
                        attempt_count=0,
                        first_attempt_status="cache_hit",
                        cache_hit=True,
                        model_call_count=0,
                    )
                )
                continue
            started = time.perf_counter()
            output = await self.qwen.chat_structured(
                self._messages(payload),
                response_model=V10RelationRawOutput,
                model=self.model,
                temperature=0.0,
            )
            validated = V10RelationRawOutput.model_validate(output)
            if self.cache is not None:
                self.cache.put(
                    key,
                    response_model_name=V10RelationRawOutput.__name__,
                    output=validated.model_dump(mode="json"),
                    attempt_count=1,
                )
            executions.append(
                V10RelationExecution(
                    output=validated,
                    prompt_version=self.prompt_version,
                    batch_size=batch_size,
                    attempt_count=1,
                    first_attempt_status="ok",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
        return executions

    @property
    def prompt_version(self) -> str:
        return (
            f"{V10_RELATION_PROMPT_VERSION}:fewshot-{'on' if self.fewshot else 'off'}"
        )

    def _messages(self, payload: list[dict[str, str]]) -> list[dict[str, str]]:
        fewshot = ""
        if self.fewshot:
            fewshot = (
                "\nfixed_examples="
                "[{relation_quote:'正常',relation:'absolute_status'},"
                "{relation_quote:'没有变化',relation:'no_change'},"
                "{relation_quote:'开始',relation:'change'},"
                "{relation_quote:'有一点软',relation:'change'}]"
            )
        return [
            {
                "role": "system",
                "content": (
                    "你是输入前置预处理 relation 分类实验器。只根据 relation_quote 判断"
                    " absolute_status / no_change / change / unclear。不诊断、不补 quote、"
                    "不使用医学知识。输入缺失时无法评估，不会出现在 payload 中。"
                    "absolute_status 仅用于直接断言一个绝对状态，如“正常”。"
                    "no_change 仅用于相对先前基线/过去状态明确说没有变化。"
                    "change 用于开始、加重、减轻、变软、变差等变化或趋势。"
                    "无法归入前三类时才输出 unclear。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "task=classify_relation_with_fixed_contract\n"
                    "rules:\n"
                    "1. records 的 key 和顺序不可改变。\n"
                    "2. 只输出 records 中出现的 unit_id。\n"
                    "3. relation_quote 为空时不得猜测，该记录不会进入 payload。\n"
                    f"batch_size={len(payload)}{fewshot}\n"
                    f"records={payload}"
                ),
            },
        ]


def relation_pool_for_unit(unit: dict[str, Any]) -> V10GoldenPool:
    return build_v10_golden_pool(unit)


def ideal_relation_output(records: list[V10RelationRecord]) -> list[V10RelationExecution]:
    available = [record for record in records if record.input_available]
    return [
        V10RelationExecution(
            output=V10RelationRawOutput(
                records=[
                    V10RelationRecordRaw(
                        unit_id=f"{record.unit_id}:{record.claim_id}",
                        relation=cast(
                            Literal["absolute_status", "no_change", "change", "unclear"],
                            record.expected_relation,
                        ),
                    )
                    for record in available
                ]
            ),
            prompt_version="v10-ideal-fixture-control",
            batch_size=len(available),
            attempt_count=0,
            first_attempt_status="ideal_control",
            model_call_count=0,
        )
    ]


def evaluate_relation_executions(
    records: list[V10RelationRecord],
    executions: list[V10RelationExecution],
) -> dict[str, Any]:
    available = [record for record in records if record.input_available]
    actual = {
        record.unit_id: record.relation
        for execution in executions
        for record in execution.output.records
    }
    correct = 0
    unclear = 0
    results: list[dict[str, Any]] = []
    for record in available:
        key = f"{record.unit_id}:{record.claim_id}"
        predicted = actual.get(key, "__missing__")
        passed = predicted == record.expected_relation
        correct += int(passed)
        unclear += int(predicted == "unclear")
        results.append(
            {
                "unit_id": key,
                "expected_relation": record.expected_relation,
                "actual_relation": predicted,
                "passed": passed,
            }
        )
    return {
        "metrics": {
            "evaluable_record_count": len(available),
            "relation_accuracy": _rate(correct, len(available)),
            "unclear_rate": _rate(unclear, len(available)),
            "format_error_count": sum(
                f"{record.unit_id}:{record.claim_id}" not in actual
                for record in available
            ),
            "model_call_count": sum(execution.model_call_count for execution in executions),
            "p95_latency_ms": _p95([execution.latency_ms for execution in executions]),
        },
        "records": results,
        "executions": [
            {
                "prompt_version": execution.prompt_version,
                "batch_size": execution.batch_size,
                "attempt_count": execution.attempt_count,
                "first_attempt_status": execution.first_attempt_status,
                "cache_hit": execution.cache_hit,
                "latency_ms": execution.latency_ms,
                "model_call_count": execution.model_call_count,
            }
            for execution in executions
        ],
    }


def missing_relation_report(records: list[V10RelationRecord]) -> dict[str, Any]:
    missing = [record for record in records if not record.input_available]
    return {
        "experiment_id": "REL-MISSING",
        "status": "completed",
        "diagnostic_only": True,
        "metrics": {
            "missing_relation_count": len(missing),
            "relation_classifier_call_count": 0,
            "relation_input_not_evaluable_count": len(missing),
            "review_required_count": len(missing),
            "misclassified_as_unclear_count": 0,
        },
        "records": [
            {
                "unit_id": f"{record.unit_id}:{record.claim_id}",
                "failure_attribution": "relation_span_missing",
                "relation_input_status": "relation_input_not_evaluable",
                "review_required": True,
            }
            for record in missing
        ],
    }


def replace_retry_settings(settings: Any) -> Any:
    from dataclasses import replace

    retries = int(os.getenv("INPUT_PREPROCESSING_V10_BASE_INTERNAL_RETRIES", "0"))
    if retries not in {0, 1}:
        raise ValueError("v10_relation_internal_retry_limit_must_be_0_or_1")
    return replace(settings, qwen_max_retries=retries)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _p95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))])
