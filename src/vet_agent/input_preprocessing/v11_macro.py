"""One-call seeded macro perception over role-specific candidate views."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v8_contracts import (
    V8EntityCandidate,
    V8MacroSemanticRawOutput,
)
from .v8_macro_analyzer import V8BaseStructuredClient
from .v10_contracts import (
    V10MacroActRaw,
    V10MacroClaimRaw,
    V10MacroSemanticRawOutput,
)
from .v10_macro import V10MacroExecution, evaluate_v10_macro
from .v11_contracts import (
    V11_MACRO_PROMPT_VERSION,
    V11_MACRO_SCHEMA_VERSION,
    V11MacroClaimRaw,
    V11MacroSemanticRawOutput,
    V11RoleMenuRecord,
)
from .v11_seeds import V11ClaimSeed


@dataclass(frozen=True)
class V11MacroExecution:
    output: V11MacroSemanticRawOutput
    adapter: str
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    model_call_count: int = 1


def compact_menu(records: list[V11RoleMenuRecord]) -> list[dict[str, Any]]:
    return [
        {
            "span_id": item.span_id,
            "text": item.text,
            "label": item.label.value,
            "start": item.start,
            "end": item.end,
            "rank": item.rank,
            "source": item.source,
            "reason": item.reason,
        }
        for item in records
    ]


def seed_payload(seed: V11ClaimSeed) -> dict[str, Any]:
    return {
        "seed_id": seed.seed_id,
        "seed_type": seed.seed_type,
        "suggested_support_span_id": seed.support_span_id,
        "suggested_target_span_id": seed.target_span_id,
        "suggested_relation_span_id": seed.relation_span_id,
        "candidate_menus": {
            role: compact_menu(records) for role, records in seed.menus.items()
        },
    }


class V11MacroAnalyzer:
    """Run one seeded macro call; no runtime act/claim/binding model split."""

    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
        max_attempts: int = 2,
    ) -> None:
        from vet_agent.runtime import QwenClient

        retries = int(os.getenv("INPUT_PREPROCESSING_V10_BASE_INTERNAL_RETRIES", "0"))
        if retries not in {0, 1}:
            raise ValueError("v11_macro_internal_retry_limit_must_be_0_or_1")
        settings = replace(make_runtime_settings(), qwen_max_retries=retries)
        self.client = V8BaseStructuredClient(
            QwenClient(settings),
            internal_retry_limit=retries,
        )
        self.model = model
        self.cache = cache
        if max_attempts not in {1, 2}:
            raise ValueError("v11_macro_attempt_limit_must_be_1_or_2")
        self.max_attempts = max_attempts

    async def run(
        self,
        *,
        experiment_id: str,
        user_text: str,
        seeds: list[V11ClaimSeed],
        act_menu: list[V11RoleMenuRecord],
        turn_context: dict[str, Any],
    ) -> V11MacroExecution:
        payload = {
            "user_text": user_text,
            "turn_context": turn_context,
            "act_candidate_menu": compact_menu(act_menu),
            "claim_seed_views": [seed_payload(seed) for seed in seeds],
            "contract_rules": [
                "Emit each act_type at most once per unit; factual clauses require fact_statement once even when answer_now is also true.",
                "Claims may only use a supplied seed_id; never invent an unseeded claim.",
                "Each field value must be a span_id from that seed's candidate menu for the same field role.",
                "If a menu is empty, the optional field must be null; never borrow from another role.",
                "rejected seeds must not produce claim semantics; use review_required for suspicious but usable seeds.",
                "Reports may be split into multiple seeds when the candidate menus expose multiple target constituents.",
                "Every supplied seed that can represent a user claim must produce exactly one claims record.",
                "Use seed_decision=accepted for a usable seed and review_required for a suspicious but usable seed.",
                "If claims is empty, coverage_gap_suspected must be true with a non-empty coverage_gap_reason.",
            ],
        }
        key = V7RunUnitKey(
            experiment_id=experiment_id,
            model=self.model,
            prompt_version=V11_MACRO_PROMPT_VERSION,
            schema_version=V11_MACRO_SCHEMA_VERSION,
            input_digest=digest_value(payload),
            turn_context_digest=digest_value(turn_context),
            adapter=self.client.adapter_name,
        )
        if self.cache is not None:
            cached = self.cache.get(
                key,
                response_model_name=V11MacroSemanticRawOutput.__name__,
            )
            if cached is not None:
                return V11MacroExecution(
                    output=V11MacroSemanticRawOutput.model_validate(cached),
                    adapter=self.client.adapter_name,
                    attempt_count=0,
                    first_attempt_status="cache_hit",
                    cache_hit=True,
                    model_call_count=0,
                )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是输入前置预处理 V11 candidate-view 实验器。一次阅读完整输入和 seed 视图，"
                    "输出 turn acts、seed decisions、statement type 和 role bindings。"
                    "所有证据和绑定只能引用 candidate menu 中的 span_id，绝对禁止输出 quote。"
                    "不得新增 seed，不得跨 role 或跨 seed 借用 span。"
                    "acts 为空必须输出 no_act_reason。"
                    "reports 是中性报告；denies 需要明确否定；reports_normal 需要明确正常或绝对状态；"
                    "reports_abnormal 需要用户明确表述异常。不诊断、不判断医学风险、不生成建议。"
                    "不要输出额外 seed_decisions 字段；claims 列表本身就是 seed decision 输出。"
                    "除非所有 seed 都明确不代表用户声明，否则 claims 不能为空。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]
        started = time.perf_counter()
        first_status = "ok"
        first_error = ""
        attempts = 0
        output: V11MacroSemanticRawOutput | None = None
        for offset in range(self.max_attempts):
            attempts += 1
            try:
                output = V11MacroSemanticRawOutput.model_validate(
                    await self.client.run_structured(
                        messages=messages,
                        response_model=V11MacroSemanticRawOutput,
                        model=self.model,
                    )
                )
                break
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1200]
                if offset == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"[:1200]
                if offset == 0:
                    continue
                break
        if output is None:
            raise RuntimeError(f"v11_macro_adapter_failed:{first_status}:{first_error}")
        if self.cache is not None:
            self.cache.put(
                key,
                response_model_name=V11MacroSemanticRawOutput.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempts,
            )
        return V11MacroExecution(
            output=output,
            adapter=self.client.adapter_name,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model_call_count=attempts * (1 + self.client.internal_retry_limit),
        )


def to_v10_output(output: V11MacroSemanticRawOutput) -> V10MacroSemanticRawOutput:
    return V10MacroSemanticRawOutput(
        no_act_reason=output.no_act_reason,
        acts=[
            V10MacroActRaw(
                unit_id=act.unit_id,
                act_type=act.act_type,
                evidence_span_id=act.evidence_span_id,
                confidence=act.confidence,
            )
            for act in output.acts
        ],
        claims=[
            V10MacroClaimRaw(
                unit_id=claim.unit_id,
                claim_id=claim.seed_id,
                statement_type=claim.statement_type,
                coarse_type=claim.coarse_type,
                support_anchor_span_ids=[claim.support_anchor_span_id],
                target_span_id=claim.target_span_id,
                relation_span_id=claim.relation_span_id,
                subject_span_id=claim.subject_span_id,
                action_agent_span_id=claim.action_agent_span_id,
                action_recipient_span_id=claim.action_recipient_span_id,
                experiencer_span_id=claim.experiencer_span_id,
                object_span_id=claim.object_span_id,
                temporal_span_id=claim.temporal_span_id,
                measurement_span_id=claim.measurement_span_id,
                confidence=claim.confidence,
            )
            for claim in output.claims
            if claim.seed_decision != "rejected"
        ],
    )


def to_v8_output(output: V11MacroSemanticRawOutput) -> V8MacroSemanticRawOutput:
    v10 = to_v10_output(output)
    from .v10_macro import to_v8_output as v10_to_v8

    return v10_to_v8(v10)


def menu_violations(
    *,
    output: V11MacroSemanticRawOutput,
    seeds: list[V11ClaimSeed],
) -> list[dict[str, str]]:
    seed_index = {seed.seed_id: seed for seed in seeds}
    violations: list[dict[str, str]] = []

    def check(
        claim: V11MacroClaimRaw,
        role_name: str,
        span_id: str | None,
    ) -> None:
        if span_id is None:
            return
        seed = seed_index.get(claim.seed_id)
        menu = seed.menus.get(role_name, []) if seed else []
        if seed is None or span_id not in {item.span_id for item in menu}:
            violations.append(
                {
                    "unit_id": claim.unit_id,
                    "seed_id": claim.seed_id,
                    "field_role": role_name,
                    "span_id": span_id,
                    "reason_code": "candidate_menu_violation",
                }
            )

    for claim in output.claims:
        check(claim, "support_quote", claim.support_anchor_span_id)
        check(claim, "target_quote", claim.target_span_id)
        check(claim, "relation_quote", claim.relation_span_id)
        check(claim, "subject_quote", claim.subject_span_id)
        check(claim, "action_agent_quote", claim.action_agent_span_id)
        check(claim, "action_recipient_quote", claim.action_recipient_span_id)
        check(claim, "experiencer_quote", claim.experiencer_span_id)
        check(claim, "object_quote", claim.object_span_id)
        check(claim, "temporal_quote", claim.temporal_span_id)
        check(claim, "measurement_quote", claim.measurement_span_id)
    return violations


def selected_fallback_count(
    *,
    output: V11MacroSemanticRawOutput,
    seeds: list[V11ClaimSeed],
) -> int:
    seed_index = {seed.seed_id: seed for seed in seeds}
    count = 0
    for claim in output.claims:
        seed = seed_index.get(claim.seed_id)
        if seed is None:
            continue
        fields = {
            "support_quote": claim.support_anchor_span_id,
            "target_quote": claim.target_span_id,
            "relation_quote": claim.relation_span_id,
            "subject_quote": claim.subject_span_id,
            "action_agent_quote": claim.action_agent_span_id,
            "action_recipient_quote": claim.action_recipient_span_id,
            "experiencer_quote": claim.experiencer_span_id,
            "object_quote": claim.object_span_id,
            "temporal_quote": claim.temporal_span_id,
            "measurement_quote": claim.measurement_span_id,
        }
        for role_name, span_id in fields.items():
            if span_id is None:
                continue
            record = next(
                (
                    item
                    for item in seed.menus.get(role_name, [])
                    if item.span_id == span_id
                ),
                None,
            )
            count += int(record is not None and record.source == "fallback")
    return count


def selected_fallback_without_reason(
    *,
    output: V11MacroSemanticRawOutput,
    seeds: list[V11ClaimSeed],
) -> int:
    seed_index = {seed.seed_id: seed for seed in seeds}
    count = 0
    for claim in output.claims:
        seed = seed_index.get(claim.seed_id)
        if seed is None:
            continue
        for records in seed.menus.values():
            selected = {
                claim.support_anchor_span_id,
                claim.target_span_id,
                claim.relation_span_id,
                claim.subject_span_id,
                claim.action_agent_span_id,
                claim.action_recipient_span_id,
                claim.experiencer_span_id,
                claim.object_span_id,
                claim.temporal_span_id,
                claim.measurement_span_id,
            }
            count += sum(
                item.source == "fallback" and not item.reason and item.span_id in selected
                for item in records
            )
    return count


def evaluate_v11_macro(
    *,
    unit: dict[str, Any],
    output: V11MacroSemanticRawOutput,
    candidates: list[Any],
    entity_candidates: list[V8EntityCandidate],
    execution: V11MacroExecution,
    seeds: list[V11ClaimSeed],
) -> dict[str, Any]:
    v10_execution = V10MacroExecution(
        output=to_v10_output(output),
        adapter=execution.adapter,
        attempt_count=execution.attempt_count,
        first_attempt_status=execution.first_attempt_status,
        first_attempt_error=execution.first_attempt_error,
        cache_hit=execution.cache_hit,
        latency_ms=execution.latency_ms,
        model_call_count=execution.model_call_count,
    )
    base = evaluate_v10_macro(
        unit=unit,
        output=to_v10_output(output),
        spans=candidates,
        entity_candidates=entity_candidates,
        execution=v10_execution,
    )
    seed_ids = {seed.seed_id for seed in seeds}
    menu_errors = menu_violations(output=output, seeds=seeds)
    unseeded = [
        claim.seed_id for claim in output.claims if claim.seed_id not in seed_ids
    ]
    base["experiment_id"] = "MACRO-FULL"
    base["schema_version"] = V11_MACRO_SCHEMA_VERSION
    legacy_role_ineligible = int(base["metrics"]["role_ineligible_binding_count"])
    base["metrics"].update(
        {
            "seed_count": len(seeds),
            "accepted_seed_count": sum(
                claim.seed_decision == "accepted" for claim in output.claims
            ),
            "review_seed_count": sum(
                claim.seed_decision == "review_required" for claim in output.claims
            ),
            "rejected_seed_count": sum(
                claim.seed_decision == "rejected" for claim in output.claims
            ),
            "unseeded_claim_count": len(unseeded),
            "candidate_menu_violation_count": len(menu_errors),
            "legacy_role_ineligible_binding_count": legacy_role_ineligible,
            "fallback_selection_count": selected_fallback_count(
                output=output,
                seeds=seeds,
            ),
            "fallback_without_reason": selected_fallback_without_reason(
                output=output,
                seeds=seeds,
            ),
            "coverage_gap_suspected_count": int(output.coverage_gap_suspected),
        }
    )
    # V11 role menus may intentionally expose explained fallback candidates.
    # Legacy V10 eligibility is retained separately; the V11 blocking metric is
    # membership in the role-specific menu.
    base["metrics"]["role_ineligible_binding_count"] = len(menu_errors)
    base["candidate_menu_violations"] = menu_errors
    base["seed_decisions"] = [
        {
            "seed_id": claim.seed_id,
            "decision": claim.seed_decision,
            "statement_type": claim.statement_type.value,
        }
        for claim in output.claims
    ]
    return base


class V11StatementVerifier:
    """Report-only verifier for suspicious statement/relation combinations."""

    def __init__(self, *, model: str = "qwen-plus") -> None:
        from vet_agent.runtime import QwenClient

        retries = int(os.getenv("INPUT_PREPROCESSING_V10_BASE_INTERNAL_RETRIES", "0"))
        settings = replace(make_runtime_settings(), qwen_max_retries=retries)
        self.client = V8BaseStructuredClient(
            QwenClient(settings),
            internal_retry_limit=retries,
        )
        self.model = model

    async def verify(
        self,
        *,
        support_quote: str,
        target_quote: str,
        relation_quote: str,
        proposed_statement_type: str,
    ) -> tuple[Any, int]:
        from .v11_contracts import V11StatementVerificationRawOutput

        payload = {
            "support_quote": support_quote,
            "target_quote": target_quote,
            "relation_quote": relation_quote,
            "macro_proposed_statement_type": proposed_statement_type,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 report-only statement type 校验器。只根据三个已解析 quote 判断 proposed "
                    "statement type 是否匹配。不生成新 claim，不输出 quote，不诊断。"
                    "confirmed 表示匹配；mismatch 表示应纠正；uncertain 表示证据不足。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
        ]
        started = time.perf_counter()
        output = V11StatementVerificationRawOutput.model_validate(
            await self.client.run_structured(
                messages=messages,
                response_model=V11StatementVerificationRawOutput,
                model=self.model,
            )
        )
        return output, int((time.perf_counter() - started) * 1000)
