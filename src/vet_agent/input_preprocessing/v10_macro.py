"""V10 macro semantic repair experiments over explicit-offset pools."""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import ValidationError

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v8_contracts import (
    V8CoarseType,
    V8DiscourseActType,
    V8EntityCandidate,
    V8MacroClaimRaw,
    V8MacroDiscourseActRaw,
    V8MacroSemanticRawOutput,
    V8UserStatementType,
)
from .v8_macro_analyzer import V8BaseStructuredClient
from .v8_span_governance import V8GovernedMacroResult, V8SpanGovernance, V8SpanPool
from .v10_contracts import (
    V10_MACRO_PROMPT_VERSION,
    V10_MACRO_SCHEMA_VERSION,
    V10CalibratedSpan,
    V10FieldRole,
    V10MacroActRaw,
    V10MacroClaimRaw,
    V10MacroSemanticRawOutput,
)
from .v10_fixture import V10GoldenPool

MacroPoolMode = Literal["full", "role-filtered", "budgeted"]


@dataclass(frozen=True)
class V10MacroExecution:
    output: V10MacroSemanticRawOutput
    adapter: str
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    model_call_count: int = 1
    internal_retry_limit: int = 0


def compact_candidates(spans: list[V10CalibratedSpan]) -> list[dict[str, Any]]:
    return [
        {
            "span_id": item.span.span_id,
            "text": item.span.text,
            "label": item.span.label.value,
            "start": item.span.start,
            "end": item.span.end,
            "eligible_roles": sorted(role.value for role in item.eligible_roles),
        }
        for item in spans
    ]


def role_candidate_index(spans: list[V10CalibratedSpan]) -> dict[str, list[str]]:
    """Return an opaque, role-oriented index without leaking owner/role IDs."""

    index: dict[str, list[V10CalibratedSpan]] = {}
    for item in spans:
        for role in item.eligible_roles:
            index.setdefault(role.value, []).append(item)
    return {
        role: [
            item.span.span_id
            for item in sorted(
                items,
                key=lambda item: (item.span.start, item.span.end, item.span.span_id),
            )
        ]
        for role, items in sorted(index.items())
    }


def golden_candidates(pool: V10GoldenPool) -> list[V10CalibratedSpan]:
    return [
        V10CalibratedSpan(span=item.span, eligible_roles=item.eligible_roles)
        for item in pool.spans
    ]


def apply_candidate_mode(
    spans: list[V10CalibratedSpan],
    mode: MacroPoolMode,
    *,
    budget: int = 48,
) -> list[V10CalibratedSpan]:
    if mode == "full":
        return spans
    if mode == "role-filtered":
        return [item for item in spans if item.eligible_roles]
    return spans[:budget]


def ideal_macro_output(pool: V10GoldenPool, unit: dict[str, Any]) -> V10MacroSemanticRawOutput:
    acts = [
        V10MacroActRaw(
            unit_id=pool.unit_id,
            act_type=V8DiscourseActType(str(act["act_type"])),
            evidence_span_id=pool.field_to_span[(f"act-{index}", getattr_v10_role("evidence_quote"))].span.span_id,
            confidence=1.0,
        )
        for index, act in enumerate(unit.get("expected_acts", []))
    ]
    claims: list[V10MacroClaimRaw] = []
    role_map = {
        "support_quote": "support_anchor_span_ids",
        "target_quote": "target_span_id",
        "relation_quote": "relation_span_id",
        "subject_quote": "subject_span_id",
        "action_agent_quote": "action_agent_span_id",
        "action_recipient_quote": "action_recipient_span_id",
        "experiencer_quote": "experiencer_span_id",
        "object_quote": "object_span_id",
        "temporal_quote": "temporal_span_id",
        "measurement_quote": "measurement_span_id",
    }
    for claim in unit.get("expected_claims", []):
        owner = str(claim["claim_id"])
        values: dict[str, Any] = {
            "unit_id": pool.unit_id,
            "claim_id": owner,
            "statement_type": V8UserStatementType(str(claim["statement_type"])),
            "coarse_type": V8CoarseType(str(claim["coarse_type"])),
            "confidence": 1.0,
        }
        for source_role, target_field in role_map.items():
            value = claim.get(source_role)
            if not value:
                continue
            span = pool.field_to_span[(owner, getattr_v10_role(source_role))]
            if target_field.endswith("span_ids"):
                values[target_field] = [span.span.span_id]
            else:
                values[target_field] = span.span.span_id
        claims.append(V10MacroClaimRaw.model_validate(values))
    return V10MacroSemanticRawOutput(
        no_act_reason="" if acts else "no_communicative_act_identified",
        acts=acts,
        claims=claims,
    )


def getattr_v10_role(value: str):
    from .v10_contracts import V10FieldRole

    return V10FieldRole(value)


class V10MacroAnalyzer:
    """One-call V10 macro perception with explicit acts/skeleton/binding sections."""

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
            raise ValueError("v10_base_internal_retry_limit_must_be_0_or_1")
        settings = make_runtime_settings()
        settings = replace(settings, qwen_max_retries=retries)
        self.client = V8BaseStructuredClient(
            QwenClient(settings),
            internal_retry_limit=retries,
        )
        self.model = model
        self.cache = cache
        if max_attempts not in {1, 2}:
            raise ValueError("v10_macro_attempt_limit_must_be_1_or_2")
        self.max_attempts = max_attempts

    async def run(
        self,
        *,
        experiment_id: str,
        user_text: str,
        spans: list[V10CalibratedSpan],
        turn_context: dict[str, Any],
    ) -> V10MacroExecution:
        payload = {
            "user_text": user_text,
            "span_candidates": compact_candidates(spans),
            "role_candidate_index": role_candidate_index(spans),
            "contract_rules": [
                "answer_now is only for an explicit request/instruction to answer now; a factual clause is never answer_now.",
                "If any user-provided factual clause exists, emit fact_statement once even when another act is also true.",
                "Emit each act_type at most once per unit; choose one primary evidence span for fact_statement instead of emitting one fact_statement per claim.",
                "denies requires an explicit negation of the target in the selected support envelope.",
                "reports_normal requires an explicit normal/absolute-status relation in the support envelope.",
                "reports_abnormal requires the user to frame the target as abnormal; a neutral action is not automatically abnormal.",
                "reports is for neutral reports that are neither explicitly normal, explicitly denied, nor explicitly framed abnormal.",
                "Use only the span IDs listed for the target field role in role_candidate_index.",
            ],
            "turn_context": turn_context,
        }
        key = V7RunUnitKey(
            experiment_id=experiment_id,
            model=self.model,
            prompt_version=V10_MACRO_PROMPT_VERSION,
            schema_version=V10_MACRO_SCHEMA_VERSION,
            input_digest=digest_value(payload),
            turn_context_digest=digest_value(turn_context),
            adapter=self.client.adapter_name,
        )
        if self.cache is not None:
            cached = self.cache.get(
                key,
                response_model_name=V10MacroSemanticRawOutput.__name__,
            )
            if cached is not None:
                return V10MacroExecution(
                    output=V10MacroSemanticRawOutput.model_validate(cached),
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
                    "你是输入前置预处理 V10 修复实验器。一次阅读完整输入，输出三部分："
                    "A discourse acts、B claim skeleton、C optional bindings。"
                    "所有证据和绑定只能引用输入 span_id，绝对禁止输出 quote 字符串。"
                    "多个 act 可同时为 true；acts 为空时必须填写 no_act_reason。"
                    "每个 claim 必须有 support 和 target；optional binding 不确定时用 null。"
                    "字段必须使用 role_candidate_index 中对应 role 的 span_id；"
                    "不得把 support/state span 填入 relation 字段。"
                    "fact_statement 表示每个用户提供的事实子句；answer_now 只能用于明确要求立即回答的请求子句，不能标注事实；"
                    "report_context 只用于背景补充，不得替代 fact_statement。"
                    "每种 act_type 在一个 unit 中最多输出一次；fact_statement 选择一个主要证据，不为每个 claim 重复输出。"
                    "reports 表示中性报告；reports_abnormal 只有用户明确把目标描述为异常时使用；"
                    "reports_normal 只有 support 内明确 normal/absolute status 时使用；"
                    "denies 只有 support 内明确否定 target 时使用。"
                    "中文 support 中“没有 / 无 / 不”明确否定 target 时用 denies；"
                    "support 或 relation span 明确“正常”时用 reports_normal 并绑定 relation；"
                    "“有一点软 / 变差 / 加重 / 减轻”等明确异常或变化描述用 reports_abnormal。"
                    "不诊断、不判断医学风险、不生成建议、不决定系统路由。"
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
        output: V10MacroSemanticRawOutput | None = None
        for attempt_offset in range(self.max_attempts):
            attempts += 1
            try:
                output = V10MacroSemanticRawOutput.model_validate(
                    await self.client.run_structured(
                        messages=messages,
                        response_model=V10MacroSemanticRawOutput,
                        model=self.model,
                    )
                )
                break
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1200]
                if attempt_offset == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = _error_chain(exc)[:1200]
                if attempt_offset == 0:
                    continue
                break
        if output is None:
            raise RuntimeError(f"v10_macro_adapter_failed:{first_status}:{first_error}")
        if self.cache is not None:
            self.cache.put(
                key,
                response_model_name=V10MacroSemanticRawOutput.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempts,
            )
        return V10MacroExecution(
            output=output,
            adapter=self.client.adapter_name,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            latency_ms=int((time.perf_counter() - started) * 1000),
            model_call_count=attempts * (1 + self.client.internal_retry_limit),
        )


def to_v8_output(output: V10MacroSemanticRawOutput) -> V8MacroSemanticRawOutput:
    return V8MacroSemanticRawOutput(
        acts=[
            V8MacroDiscourseActRaw(
                unit_id=act.unit_id,
                act_type=act.act_type,
                evidence_span_ids=[act.evidence_span_id],
                confidence=act.confidence,
            )
            for act in output.acts
        ],
        claims=[
            V8MacroClaimRaw(
                unit_id=claim.unit_id,
                claim_id=claim.claim_id,
                statement_type=claim.statement_type,
                coarse_type=claim.coarse_type,
                support_span_ids=claim.support_anchor_span_ids,
                target_span_ids=[claim.target_span_id],
                relation_span_ids=_one(claim.relation_span_id),
                subject_span_ids=_one(claim.subject_span_id),
                action_agent_span_ids=_one(claim.action_agent_span_id),
                action_recipient_span_ids=_one(claim.action_recipient_span_id),
                experiencer_span_ids=_one(claim.experiencer_span_id),
                object_span_ids=_one(claim.object_span_id),
                temporal_span_ids=_one(claim.temporal_span_id),
                measurement_span_ids=_one(claim.measurement_span_id),
                confidence=claim.confidence,
            )
            for claim in output.claims
        ],
    )


def role_eligibility_violations(
    *,
    output: V10MacroSemanticRawOutput,
    spans: list[V10CalibratedSpan],
) -> list[dict[str, str]]:
    """Return bindings whose selected span is ineligible for the target role."""

    eligibility = {item.span.span_id: item.eligible_roles for item in spans}

    def check(
        unit_id: str,
        claim_id: str,
        role: V10FieldRole,
        span_id: str | None,
    ) -> dict[str, str] | None:
        if span_id is None or span_id not in eligibility:
            # Missing IDs are invalid references and are handled by V8
            # governance; do not double-count them as role violations.
            return None
        if role in eligibility[span_id]:
            return None
        return {
            "unit_id": unit_id,
            "claim_id": claim_id,
            "field_role": role.value,
            "span_id": span_id,
            "reason_code": "role_ineligible_span_binding",
        }

    violations: list[dict[str, str]] = []
    for claim in output.claims:
        checks = (
            (V10FieldRole.SUPPORT, None, claim.support_anchor_span_ids),
            (V10FieldRole.TARGET, claim.target_span_id, None),
            (V10FieldRole.RELATION, claim.relation_span_id, None),
            (V10FieldRole.SUBJECT, claim.subject_span_id, None),
            (V10FieldRole.ACTION_AGENT, claim.action_agent_span_id, None),
            (V10FieldRole.ACTION_RECIPIENT, claim.action_recipient_span_id, None),
            (V10FieldRole.EXPERIENCER, claim.experiencer_span_id, None),
            (V10FieldRole.OBJECT, claim.object_span_id, None),
            (V10FieldRole.TEMPORAL, claim.temporal_span_id, None),
            (V10FieldRole.MEASUREMENT, claim.measurement_span_id, None),
        )
        for role, single, multiple in checks:
            values = multiple if multiple is not None else ([single] if single else [])
            for span_id in values:
                violation = check(claim.unit_id, claim.claim_id, role, span_id)
                if violation is not None:
                    violations.append(violation)
    for act in output.acts:
        violation = check(act.unit_id, "acts", V10FieldRole.EVIDENCE, act.evidence_span_id)
        if violation is not None:
            violations.append(violation)
    return violations


def _one(value: str | None) -> list[str]:
    return [value] if value else []


def _error_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 8:
        parts.append(f"{type(current).__name__}:{current}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def evaluate_v10_macro(
    *,
    unit: dict[str, Any],
    output: V10MacroSemanticRawOutput,
    spans: list[V10CalibratedSpan],
    entity_candidates: list[V8EntityCandidate],
    execution: V10MacroExecution,
) -> dict[str, Any]:
    pool = V8SpanPool(
        sources={str(unit["unit_id"]): str(unit["user_text"])},
        spans=[item.span for item in spans],
    )
    governance = V8SpanGovernance(pool)
    governed = governance.govern(to_v8_output(output), entity_candidates=entity_candidates)
    role_violations = role_eligibility_violations(output=output, spans=spans)

    expected_acts = [
        (str(act["act_type"]), str(act["evidence_quote"]))
        for act in unit.get("expected_acts", [])
    ]
    actual_acts: list[tuple[str, str]] = []
    for act in output.acts:
        binding = governance.resolve_span_ids(span_ids=[act.evidence_span_id])
        actual_acts.append(
            (
                act.act_type.value,
                binding.quote if binding is not None and binding.status == "resolved" else "invalid",
            )
        )
    act_matches = _multiset_matches(expected_acts, actual_acts)

    expected_claims = [
        (
            str(claim["statement_type"]),
            str(claim["coarse_type"]),
            str(claim["support_quote"]),
            str(claim["target_quote"]),
        )
        for claim in unit.get("expected_claims", [])
    ]
    actual_claims = [
        (
            claim.statement_type.value,
            claim.coarse_type.value,
            claim.support.quote,
            claim.target.quote,
        )
        for claim in governed.governed_claims
    ]
    claim_matches = _multiset_matches(expected_claims, actual_claims)
    claim_pairs = _pair_claims(unit, governed)

    optional_expected = 0
    optional_correct = 0
    for expected, actual in claim_pairs:
        for role, field_name in (
            ("relation_quote", "relation"),
            ("subject_quote", "subject"),
            ("action_agent_quote", "action_agent"),
            ("action_recipient_quote", "action_recipient"),
            ("experiencer_quote", "experiencer"),
            ("object_quote", "object_mention"),
            ("temporal_quote", "temporal"),
            ("measurement_quote", "measurement"),
        ):
            if expected.get(role):
                optional_expected += 1
                actual_binding = getattr(actual, field_name)
                actual_quote = (
                    getattr(actual_binding, "mention_quote", None)
                    if field_name in {"subject", "action_agent", "action_recipient", "experiencer"}
                    else getattr(actual_binding, "quote", None)
                )
                optional_correct += int(
                    actual_binding is not None
                    and actual_quote == str(expected[role])
                )

    no_act_reason_valid = bool(output.acts) or bool(output.no_act_reason)
    return {
        "unit_id": str(unit["unit_id"]),
        "execution": _execution_report(execution),
        "metrics": {
            "act_output_count": len(actual_acts),
            "act_expected_count": len(expected_acts),
            "act_precision": _rate(act_matches, len(actual_acts)),
            "act_recall": _rate(act_matches, len(expected_acts)),
            "empty_act_rate": 1.0 if not output.acts else 0.0,
            "no_act_reason_valid": no_act_reason_valid,
            "evidence_span_valid_rate": _rate(
                sum(quote != "invalid" for _, quote in actual_acts),
                len(actual_acts),
            ),
            "claim_output_count": len(governed.governed_claims),
            "claim_expected_count": len(expected_claims),
            "claim_precision": _rate(claim_matches, len(actual_claims)),
            "claim_recall": _rate(claim_matches, len(expected_claims)),
            "statement_type_accuracy": _rate(claim_matches, len(expected_claims)),
            "target_binding_accuracy": _rate(claim_matches, len(expected_claims)),
            "support_envelope_valid_rate": _rate(
                sum(claim.projection_ready for claim in governed.governed_claims),
                len(governed.governed_claims),
            ),
            "binding_expected_count": optional_expected,
            "binding_accuracy": _rate(optional_correct, optional_expected),
            "invalid_span_reference_count": len(governed.invalid_span_references),
            "invalid_span_binding_count": len(governed.invalid_span_bindings),
            "role_ineligible_binding_count": len(role_violations),
            "model_free_quote_output": 0,
        },
        "gates": [gate.model_dump(mode="json") for gate in governed.gates],
        "role_eligibility_violations": role_violations,
        "governed_claims": [claim.model_dump(mode="json") for claim in governed.governed_claims],
        "raw_output": {
            "no_act_reason": output.no_act_reason,
            "acts": [
                {
                    "act_type": act.act_type.value,
                    "evidence_span_id": act.evidence_span_id,
                    "confidence": act.confidence,
                }
                for act in output.acts
            ],
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement_type": claim.statement_type.value,
                    "coarse_type": claim.coarse_type.value,
                    "support_anchor_span_ids": claim.support_anchor_span_ids,
                    "target_span_id": claim.target_span_id,
                    "relation_span_id": claim.relation_span_id,
                    "subject_span_id": claim.subject_span_id,
                    "action_agent_span_id": claim.action_agent_span_id,
                    "action_recipient_span_id": claim.action_recipient_span_id,
                    "experiencer_span_id": claim.experiencer_span_id,
                    "object_span_id": claim.object_span_id,
                    "temporal_span_id": claim.temporal_span_id,
                    "measurement_span_id": claim.measurement_span_id,
                }
                for claim in output.claims
            ],
        },
    }


def _pair_claims(
    unit: dict[str, Any],
    governed: V8GovernedMacroResult,
) -> list[tuple[dict[str, Any], Any]]:
    expected_by_key = {
        (
            str(claim["statement_type"]),
            str(claim["coarse_type"]),
            str(claim["support_quote"]),
            str(claim["target_quote"]),
        ): claim
        for claim in unit.get("expected_claims", [])
    }
    pairs: list[tuple[dict[str, Any], Any]] = []
    used: set[int] = set()
    for actual in governed.governed_claims:
        key = (
            actual.statement_type.value,
            actual.coarse_type.value,
            actual.support.quote,
            actual.target.quote,
        )
        expected = expected_by_key.get(key)
        if expected is None:
            continue
        index = next(
            (
                index
                for index, claim in enumerate(unit.get("expected_claims", []))
                if index not in used
                and (
                    str(claim["statement_type"]),
                    str(claim["coarse_type"]),
                    str(claim["support_quote"]),
                    str(claim["target_quote"]),
                )
                == key
            ),
            None,
        )
        if index is not None:
            used.add(index)
            pairs.append((unit["expected_claims"][index], actual))
    return pairs


def _multiset_matches(expected: list[tuple[Any, ...]], actual: list[tuple[Any, ...]]) -> int:
    available = Counter(expected)
    matched = 0
    for item in actual:
        if available[item] > 0:
            available[item] -= 1
            matched += 1
    return matched


def _execution_report(execution: V10MacroExecution) -> dict[str, Any]:
    return {
        "attempt_count": execution.attempt_count,
        "first_attempt_status": execution.first_attempt_status,
        "first_attempt_error": execution.first_attempt_error,
        "cache_hit": execution.cache_hit,
        "latency_ms": execution.latency_ms,
        "model_call_count": execution.model_call_count,
        "internal_retry_limit": execution.internal_retry_limit,
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
