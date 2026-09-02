"""Versioned V14 prompt skills and the minimal one-pass generators."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from inspect import isawaitable
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .runtime_helpers import make_runtime_settings
from .v13_generator import ideal_records
from .v14_contracts import (
    V14_CLAIM_PROMPT_VERSION,
    V14_INTENT_PROMPT_VERSION,
    V14ClaimGenerationRaw,
    V14ClaimInventoryItem,
    V14ClaimRecordRaw,
    V14ExecutionMetadata,
    V14GenerationOptions,
    V14TurnIntentRaw,
)


class V14StructuredClient(Protocol):

    @property
    def adapter_name(self) -> str: ...

    async def run_structured_with_details(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
        options: V14GenerationOptions,
    ) -> Any: ...


@dataclass(frozen=True)
class V14ModelExecution:
    output: Any
    prompt_version: str
    metadata: V14ExecutionMetadata


class V14QwenStructuredClient:
    """Response-format client that preserves provider response metadata."""

    def __init__(self) -> None:
        from vet_agent.runtime import QwenClient

        settings = replace(
            make_runtime_settings(),
            qwen_max_retries=0,
        )
        self.qwen = QwenClient(settings)

    @property
    def adapter_name(self) -> str:
        return "qwen-response-format"

    async def run_structured_with_details(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
        options: V14GenerationOptions,
    ) -> Any:
        return await self.qwen.chat_structured_with_details(
            messages,
            response_model=response_model,
            model=model,
            temperature=options.temperature,
            top_p=options.top_p,
            seed=options.seed,
            frequency_penalty=options.frequency_penalty,
            presence_penalty=options.presence_penalty,
            max_tokens=options.max_tokens,
        )


class V14LLMFirstGenerator:
    """Run fixed-field intent or inventory-based one-pass claim generation."""

    def __init__(
        self,
        *,
        client: V14StructuredClient,
        model: str = "qwen-plus",
        options: V14GenerationOptions | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.options = options or V14GenerationOptions(option_id="p0")

    async def _run(
        self,
        *,
        prompt_version: str,
        response_model: type[BaseModel],
        system_prompt: str,
        payload: dict[str, Any],
    ) -> V14ModelExecution:
        started = time.perf_counter()
        first_status = "ok"
        first_error = ""
        for attempts in range(1, self.options.max_attempts + 1):
            try:
                result = self.client.run_structured_with_details(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(
                                payload, ensure_ascii=False, sort_keys=True
                            ),
                        },
                    ],
                    response_model=response_model,
                    model=self.model,
                    options=self.options,
                )
                if isawaitable(result):
                    result = await result
                output = response_model.model_validate(result.output)
                metadata = self._metadata(
                    result,
                    attempts=attempts,
                    first_status=first_status,
                    first_error=first_error,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
                return V14ModelExecution(
                    output=output,
                    prompt_version=prompt_version,
                    metadata=metadata,
                )
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1600]
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"[:1600]
        raise RuntimeError(f"v14_generator_failed:{first_status}:{first_error}")

    def _metadata(
        self,
        result: Any,
        *,
        attempts: int,
        first_status: str,
        first_error: str,
        latency_ms: int,
    ) -> V14ExecutionMetadata:
        usage = getattr(result, "usage", None) or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(
            usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
        )
        return V14ExecutionMetadata(
            adapter=self.client.adapter_name,
            model=self.model,
            provider_model=str(getattr(result, "provider_model", "")),
            response_id=str(getattr(result, "response_id", "")),
            finish_reason=str(getattr(result, "finish_reason", "")),
            latency_ms=latency_ms,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            model_call_count=attempts,
            token_count_available=bool(usage),
            prompt_token_count=prompt_tokens,
            completion_token_count=completion_tokens,
            total_token_count=total_tokens,
            cost_available=False,
            generation_options=self.options.model_dump(mode="json"),
            effective_parameter_status="unverifiable",
        )

    async def intent(self, *, unit_id: str, user_text: str) -> V14ModelExecution:
        return await self._run(
            prompt_version=V14_INTENT_PROMPT_VERSION,
            response_model=V14TurnIntentRaw,
            system_prompt=(
                "你是 V14 turn intent 实验器。使用 fixed-field schema，每个信号只有一个字段。"
                "answer_now、wants_triage、correction、clarification_request 是 turn-level act；"
                "fact_statement_present、question_present、report_context_present 是 turn-level 输入属性。"
                "fact_statement_present 不枚举事实，只判断当前输入是否包含事实陈述。"
                "fact_statement_present 的 evidence_phrase 必须是原文中第一个完整事实子句，"
                "不要选择后续事实、建议请求或整段输入。"
                "answer_now 只在用户明确要求现在给出建议/结论时为 true；"
                "question_present 只在存在疑问/请求信息时为 true；"
                "wants_triage 只在明确表达分诊、急诊或就医诉求时为 true；"
                "report_context_present 仅用于背景信息，不得从事实陈述或建议请求中推断。"
                "detected=true 时必须给 evidence_phrase；所有信号都为 false 时必须给 no_signal_reason。"
                "不诊断、不判断医学风险、不生成建议。"
            ),
            payload={"unit_id": unit_id, "user_text": user_text},
        )

    async def claims(self, *, unit_id: str, user_text: str) -> V14ModelExecution:
        return await self._run(
            prompt_version=V14_CLAIM_PROMPT_VERSION,
            response_model=V14ClaimGenerationRaw,
            system_prompt=(
                "你是 V14 one-pass Structured Claim 实验器。先在 claim_inventory 中列出所有原子 claim，"
                "再让 claims 逐项对应 inventory ordinal；ordinal 必须从 1 开始连续递增、不得重复，"
                "每个 inventory item 必须且只能对应一条 claim。"
                "一个 relation/state 作用于多个 target 时，"
                "必须为每个 target 输出一条 claim 并继承 polarity/modality/relation。"
                "并列结构必须逐项拆分，不得把多个并列 target 合并成一个 phrase："
                "“没有呕吐、干呕、反流、流涎或舔唇”必须拆为呕吐、干呕、反流、流涎、舔唇 5 条 denies；"
                "“精神、食欲和饮水都正常”必须拆为精神、食欲、饮水 3 条 reports_normal；"
                "“没有血便和黑便”必须拆为血便、黑便 2 条 denies。"
                "action claim 应输出 action_agent_phrase、action_recipient_phrase 和 object_phrase。"
                "action claim 不得使用 experiencer_phrase 代替 action_agent/action_recipient；"
                "只有 state / symptom claim 才使用 experiencer_phrase 或 subject_phrase。"
                "原文中出现动作角色时不得省略：“医生给它开了药”必须输出 action_agent_phrase=医生、"
                "action_recipient_phrase=它、object_phrase=药；“我前天开始给它换新猫粮”必须输出 "
                "action_agent_phrase=我、action_recipient_phrase=它、object_phrase=新猫粮。"
                "state/symptom claim 的经历者在 evidence 中出现时必须输出 subject_phrase 或 experiencer_phrase，"
                "例如“这两天大便有一点软”应输出 subject_phrase/experiencer_phrase=它。"
                "每个 claim 的 evidence_phrase 必须是包含该 claim 的最小逗号/句号子句，"
                "不得把多个子句合并成整段输入作为 evidence。"
                "target_phrase 是这个 claim 的动作事件、状态或观测对象，不是代词或动作承受者："
                "“医生给它开了药”的 target 是“开了药”，“我给它换新猫粮”的 target 是“换新猫粮”，"
                "“主人给它喂了罐头”的 target 是“喂了罐头”；“它”只应出现在 action_recipient_phrase。"
                "状态 target 必须保留完整状态描述：“大便有一点软”的 target 是“大便有一点软”，不是“大便”；"
                "频率 / 剂量 / 数量 claim 的 target 和 measurement_phrase 都应是“一天一次”“5公斤”这类完整表达。"
                "user_statement_type 必须区分：中性动作/状态用 reports；明确异常用 reports_abnormal；"
                "明确正常用 reports_normal；“没有/无/未”表达的不存在用 denies；不确定用 uncertain；"
                "既往用 historical；假设用 hypothetical。换粮、喂食、用药等 action 通常应为 reports，"
                "不要因涉及医疗行为而误标 reports_abnormal。"
                "temporal_phrase 必须保留原时间表达和起点/持续时间语义，例如“前天开始”“这两天”；"
                "“一天一次”是 measurement/frequency，应输出到 measurement_phrase，不应仅作为 temporal_phrase；"
                "relation_phrase 必须保留否定、变化或无明显变化语义，不得把“没有明显变化”改写成“正常”。"
                "state / symptom claim 如有明确经历者应输出 experiencer_phrase。"
                "对症状/状态 target，如能归纳出通俗 descriptor，应输出 canonical_descriptor；"
                "例如“大便有一点软”可输出 canonical_descriptor=大便软。canonical_descriptor 不是 canonical_id。"
                "phrase 是 approximate semantic proposal，可以不逐字复制原文，但不得引入新信息，"
                "不得丢失否定、时间、主体、数量或比较关系；能逐字复制时优先逐字复制。"
                "phrase 不是 quote。字段缺失输出 null；不适用时在 missing_field_reason 说明 not_applicable；"
                "有线索但无法确定输出 ambiguous。禁止输出 span_id、start、end、entity_id、canonical_id。"
                "coverage_gap_suspected 和 coverage_gap_reason 是必填字段：有 claim 时前者为 false、后者为空字符串；"
                "没有 claim 时前者必须为 true，后者必须给出非空 reason。不得省略任一字段。"
            ),
            payload={"unit_id": unit_id, "user_text": user_text},
        )


def ideal_v14_records(
    unit: dict[str, Any],
    *,
    canonical_descriptors: dict[tuple[str, str], str] | None = None,
) -> V14ClaimGenerationRaw:
    """Build the fixture control from the existing V13 ideal records."""

    legacy = ideal_records(
        unit,
        canonical_descriptors=canonical_descriptors,
    )
    expected_by_id = {
        str(claim["claim_id"]): claim for claim in unit.get("expected_claims", [])
    }
    inventory: list[V14ClaimInventoryItem] = []
    claims: list[V14ClaimRecordRaw] = []
    for ordinal, raw in enumerate(legacy.claims, start=1):
        inventory.append(
            V14ClaimInventoryItem(
                ordinal=ordinal,
                evidence_phrase=raw.evidence_phrase,
                claim_kind=raw.claim_type,
                confidence=raw.confidence,
            )
        )
        data = raw.model_dump(mode="json")
        data.pop("unit_id", None)
        for key in (
            "subject_phrase",
            "experiencer_phrase",
            "action_agent_phrase",
            "action_recipient_phrase",
            "object_phrase",
            "temporal_phrase",
            "measurement_phrase",
            "relation_phrase",
            "canonical_descriptor",
        ):
            data[key] = data.get(key) or None
        expected = expected_by_id.get(raw.unit_id, {})
        if expected.get("experiencer_quote"):
            data["experiencer_phrase"] = str(expected["experiencer_quote"])
        data["inventory_ordinal"] = ordinal
        claims.append(V14ClaimRecordRaw.model_validate(data))
    return V14ClaimGenerationRaw(
        schema_version="v14-onepass-inventory-claim-1",
        claim_inventory=inventory,
        claims=claims,
        coverage_gap_suspected=False,
        coverage_gap_reason="",
    )
