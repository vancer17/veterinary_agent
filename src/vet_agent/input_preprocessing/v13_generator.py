"""LLM-first generators and ideal controls for V13 structured claims."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v8_macro_analyzer import V8BaseStructuredClient
from .v10_relation import replace_retry_settings
from .v13_contracts import (
    V13_CLAIM_PROMPT_VERSION,
    V13_INTENT_PROMPT_VERSION,
    V13_SEGMENTATION_PROMPT_VERSION,
    V13ClaimKind,
    V13ClaimRecordRaw,
    V13ClaimRecordRawOutput,
    V13ClaimUnitRaw,
    V13ClaimUnitRawOutput,
    V13CoarseType,
    V13EpistemicStatus,
    V13IntentActType,
    V13ModalityType,
    V13PhrasePolicy,
    V13Polarity,
    V13TurnIntentActRaw,
    V13TurnIntentRawOutput,
    V13UserStatementType,
)


class V13StructuredClient(Protocol):
    adapter_name: str

    @property
    def internal_retry_limit(self) -> int: ...

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
    ) -> Any: ...


@dataclass(frozen=True)
class V13ModelExecution:
    output: Any
    adapter: str
    prompt_version: str
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    model_call_count: int = 1
    internal_retry_limit: int = 0


def build_v13_client() -> V8BaseStructuredClient:
    from vet_agent.runtime import QwenClient

    return V8BaseStructuredClient(
        QwenClient(replace_retry_settings(make_runtime_settings())),
        internal_retry_limit=0,
    )


class V13LLMFirstGenerator:
    """Run one V13 structured generation with bounded outer attempts."""

    def __init__(
        self,
        *,
        client: V13StructuredClient,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
        # Keep the retry budget bounded and audited. V13 payloads are smaller
        # than V12 candidate-menu requests, but qwen-plus has shown transient
        # structured-chat failures under the remote gateway.
        max_attempts: int = 2,
        phrase_policy: V13PhrasePolicy = V13PhrasePolicy.APPROXIMATE,
    ) -> None:
        if max_attempts not in {1, 2}:
            raise ValueError("v13_generator_max_attempts_must_be_1_or_2")
        self.client = client
        self.model = model
        self.cache = cache
        self.max_attempts = max_attempts
        self.phrase_policy = phrase_policy

    def _prompt_version(self, base: str) -> str:
        return f"{base}:{self.phrase_policy.value}"

    def _phrase_instruction(self) -> str:
        if self.phrase_policy == V13PhrasePolicy.LITERAL:
            return (
                "所有 phrase 必须是原文中的连续片段，必须逐字来自原文；"
                "禁止同义改写、翻译、缩写或重写。"
            )
        return (
            "phrase 是 approximate semantic proposal，不要求逐字复制原文，也不要求是原文连续片段；"
            "但必须只指向用户原文明示的内容，不得引入新信息，不得丢失否定、时间、主体、数量或关键关系。"
            "若能逐字复制原文，应优先逐字复制。phrase 不是 quote，也不是 evidence；"
            "后续系统只会在原文中保守定位，并由代码生成 aligned_quote。"
        )

    async def run(
        self,
        *,
        experiment_id: str,
        prompt_version: str,
        schema_version: str,
        system_prompt: str,
        payload: dict[str, Any],
        response_model: type[BaseModel],
    ) -> V13ModelExecution:
        input_digest = digest_value(payload)
        key = V7RunUnitKey(
            experiment_id=experiment_id,
            model=self.model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            input_digest=input_digest,
            turn_context_digest=digest_value({"schema": schema_version}),
            adapter=self.client.adapter_name,
        )
        if self.cache is not None:
            cached = self.cache.get(response_model_name=response_model.__name__, key=key)
            if cached is not None:
                return V13ModelExecution(
                    output=response_model.model_validate(cached),
                    adapter=self.client.adapter_name,
                    prompt_version=prompt_version,
                    attempt_count=0,
                    first_attempt_status="cache_hit",
                    cache_hit=True,
                    model_call_count=0,
                    internal_retry_limit=self.client.internal_retry_limit,
                )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]
        started = time.perf_counter()
        attempts = 0
        first_status = "ok"
        first_error = ""
        output: BaseModel | None = None
        for offset in range(self.max_attempts):
            attempts += 1
            try:
                raw_output: Any = self.client.run_structured(
                    messages=messages,
                    response_model=response_model,
                    model=self.model,
                )
                if isawaitable(raw_output):
                    raw_output = await raw_output
                output = response_model.model_validate(raw_output)
                break
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1600]
                if offset == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"[:1600]
                if offset == 0:
                    continue
                break
        latency_ms = int((time.perf_counter() - started) * 1000)
        if output is None:
            raise RuntimeError(
                f"v13_generator_failed:{first_status}:{first_error}",
            )
        if self.cache is not None:
            self.cache.put(
                key=key,
                response_model_name=response_model.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempts,
            )
        return V13ModelExecution(
            output=output,
            adapter=self.client.adapter_name,
            prompt_version=prompt_version,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            latency_ms=latency_ms,
            model_call_count=attempts * (1 + self.client.internal_retry_limit),
            internal_retry_limit=self.client.internal_retry_limit,
        )

    async def intent(self, *, unit_id: str, user_text: str) -> V13ModelExecution:
        return await self.run(
            experiment_id="V13-INTENT",
            prompt_version=self._prompt_version(V13_INTENT_PROMPT_VERSION),
            schema_version="v13-intent-1",
            response_model=V13TurnIntentRawOutput,
            system_prompt=(
                "你是输入前置预处理 V13 turn intent 实验器。只根据完整用户输入判断 discourse act。"
                "act_type 只能使用以下枚举：answer_now、wants_triage、correction、clarification_request、"
                "fact_statement、question、report_context；不得发明 request_advice、negation、reporting 等新类型。"
                "每种 act_type 在一个输入中最多输出一次；不要为每个 claim 重复输出 fact_statement。"
                "fact_statement 选择第一个完整事实子句作为 evidence_phrase；answer_now 可与 fact_statement 并行。"
                f"每个 true act 必须给出 evidence_phrase。{self._phrase_instruction()}"
                "同一 act_type 在一个 unit 中最多输出一次；fact_statement 是 turn-level act，"
                "不得为每个 claim 重复输出。"
                "acts 为空时必须输出 no_act_reason。不诊断、不建议、不判断医学风险。"
            ),
            payload={"unit_id": unit_id, "user_text": user_text},
        )

    async def segment(self, *, unit_id: str, user_text: str) -> V13ModelExecution:
        return await self.run(
            experiment_id="V13-SEGMENTATION",
            prompt_version=self._prompt_version(V13_SEGMENTATION_PROMPT_VERSION),
            schema_version="v13-claim-units-1",
            response_model=V13ClaimUnitRawOutput,
            system_prompt=(
                "你是输入前置预处理 V13 claim segmentation 实验器。"
                "把用户输入拆成原子 claim units：action/state/denial/question/correction 等可独立表达的用户陈述。"
                f"共享否定或正常范围必须逐项拆分。{self._phrase_instruction()}"
                "answer_now、阶段建议请求、不要追问、控制偏好不是事实 claim unit，不得输出为 claim。"
                "temporal/measurement 不单独生成 claim。units 为空必须显式 coverage gap。"
            ),
            payload={"unit_id": unit_id, "user_text": user_text},
        )

    async def records_onpass(
        self,
        *,
        unit_id: str,
        user_text: str,
    ) -> V13ModelExecution:
        return await self.records(
            unit_id=unit_id,
            user_text=user_text,
            units=[],
            experiment_suffix="ONEPASS",
        )

    async def records_twostage(
        self,
        *,
        unit_id: str,
        user_text: str,
        units: list[V13ClaimUnitRaw],
    ) -> V13ModelExecution:
        return await self.records(
            unit_id=unit_id,
            user_text=user_text,
            units=units,
            experiment_suffix="TWOSTAGE",
        )

    async def records(
        self,
        *,
        unit_id: str,
        user_text: str,
        units: list[V13ClaimUnitRaw],
        experiment_suffix: Literal["ONEPASS", "TWOSTAGE"],
    ) -> V13ModelExecution:
        payload: dict[str, Any] = {
            "unit_id": unit_id,
            "user_text": user_text,
            "claim_units": [item.model_dump(mode="json") for item in units],
        }
        unit_instruction = (
            "claim_units 已给出，必须逐个消费，不得新增 unit。"
            if units
            else "先在内部切分原子 claims，再直接输出 flat claim records。"
        )
        return await self.run(
            experiment_id=f"V13-RECORDS-{experiment_suffix}",
            prompt_version=self._prompt_version(V13_CLAIM_PROMPT_VERSION),
            schema_version="v13-claim-records-1",
            response_model=V13ClaimRecordRawOutput,
            system_prompt=(
                "你是输入前置预处理 V13 flat claim generator。"
                f"{unit_instruction}"
                f"输出扁平 claim records。所有 evidence/target/participant/temporal/measurement/relation phrase "
                f"遵循同一规则：{self._phrase_instruction()}"
                "必须逐条列出用户陈述的 action/state/denial claim；回答请求、控制偏好和系统指令不是 claim。"
                "不能因为包含请求句而输出空 claims；只有完全没有用户事实陈述时才允许 coverage gap。"
                "target_phrase 是谓词事件或状态短语：action claim 不能只给药物/食物/物品名词，"
                "物品应放在 object_phrase；state claim 的 target 是状态/症状短语。"
                "action claim 中出现的动作发出者、承受者和对象必须分别填 participant/object phrase；"
                "state/denial claim 的主体若由上下文指代，也必须输出 subject_phrase。"
                "不要输出 offset、span_id、entity_id、canonical_id。canonical_descriptor 只能是通用描述短语。"
                "reports 是中性报告；denies 是明确否定；reports_normal 是明确正常；reports_abnormal 是明确异常。"
                "用户明确描述问题、不舒服或异常状态（例如“有一点软”“不正常”“很难受”等）时，"
                "即使用户语气平静，也应输出 reports_abnormal，而不是中性 reports。"
                "共享否定范围内的每个 target 都必须继承 denies；共享“正常”范围内的每个 target 都必须继承 reports_normal。"
                "modality_type 表示话语模态：用户直接当前陈述用 factual；如果/假设用 hypothetical；"
                "明确过去经历用 historical；不确定/疑似用 uncertain；只有转述他人结论时用 reported。"
                "coarse_type 只能从 symptom/state/action/food/medication/measurement/time/context 中选择，"
                "表示通用结构类别，不做医学诊断。"
                "epistemic_status：用户直接陈述且没有犹豫词时用 certain；只有“好像/可能/不确定”才用 uncertain；"
                "只有转述他人结论才用 secondhand；不得因为内容是宠物状态而使用 unknown。"
                "historical/hypothetical/uncertain 必须与 factual 分开。"
                "时间起点或持续表达（如前天开始、这两天、今天）不表示 historical，仍应输出 factual。"
                "claims 为空必须显式 coverage gap。不诊断、不建议、不判断医学风险。"
            ),
            payload=payload,
        )


def ideal_intent(unit: dict[str, Any]) -> V13TurnIntentRawOutput:
    return V13TurnIntentRawOutput(
        schema_version="v13-intent-1",
        acts=[
            V13TurnIntentActRaw(
                act_type=V13IntentActType(str(act["act_type"])),
                evidence_phrase=str(act["evidence_quote"]),
                confidence=1.0,
            )
            for act in unit.get("expected_acts", [])
        ],
        no_act_reason="" if unit.get("expected_acts") else "no_communicative_act_identified",
    )


def ideal_units(unit: dict[str, Any]) -> V13ClaimUnitRawOutput:
    units: list[V13ClaimUnitRaw] = []
    for claim in unit.get("expected_claims", []):
        statement = str(claim.get("statement_type", "reports"))
        kind = (
            V13ClaimKind.DENIAL
            if statement == "denies"
            else V13ClaimKind.ACTION
            if str(claim.get("coarse_type")) in {"action", "food", "medication"}
            else V13ClaimKind.STATE
        )
        units.append(
            V13ClaimUnitRaw(
                unit_id=str(claim["claim_id"]),
                evidence_phrase=str(claim["support_quote"]),
                core_phrase=str(claim["target_quote"]),
                claim_kind=kind,
                subject_hint=str(claim.get("subject_quote", "")),
                confidence=1.0,
            )
        )
    return V13ClaimUnitRawOutput(
        schema_version="v13-claim-units-1",
        units=units,
        coverage_gap_suspected=not units,
        coverage_gap_reason="" if units else "no_user_claim_identified",
    )


def ideal_records(
    unit: dict[str, Any],
    *,
    canonical_descriptors: dict[tuple[str, str], str] | None = None,
) -> V13ClaimRecordRawOutput:
    descriptors = canonical_descriptors or {}
    records: list[V13ClaimRecordRaw] = []
    for claim in unit.get("expected_claims", []):
        statement = V13UserStatementType(str(claim["statement_type"]))
        coarse = str(claim.get("coarse_type", "state"))
        kind = (
            V13ClaimKind.DENIAL
            if statement == V13UserStatementType.DENIES
            else V13ClaimKind.ACTION
            if coarse in {"action", "food", "medication"}
            else V13ClaimKind.STATE
        )
        polarity = (
            V13Polarity.NEGATIVE
            if statement == V13UserStatementType.DENIES
            else V13Polarity.POSITIVE
        )
        records.append(
            V13ClaimRecordRaw(
                unit_id=str(claim["claim_id"]),
                claim_type=kind,
                coarse_type=V13CoarseType(str(claim["coarse_type"])),
                evidence_phrase=str(claim["support_quote"]),
                target_phrase=str(claim["target_quote"]),
                subject_phrase=str(claim.get("subject_quote", "")),
                action_agent_phrase=str(claim.get("action_agent_quote", "")),
                action_recipient_phrase=str(claim.get("action_recipient_quote", "")),
                object_phrase=str(claim.get("object_quote", "")),
                user_statement_type=statement,
                polarity=polarity,
                modality_type=V13ModalityType.FACTUAL,
                modality_strength=1.0,
                epistemic_status=V13EpistemicStatus.CERTAIN,
                temporal_phrase=str(claim.get("temporal_quote", "")),
                temporal_relation="reported",
                temporal_value="",
                temporal_precision="source_phrase",
                measurement_phrase=str(claim.get("measurement_quote", "")),
                measurement_value="",
                measurement_unit="",
                measurement_relation="",
                relation_phrase=str(claim.get("relation_quote", "")),
                canonical_descriptor=descriptors.get(
                    (str(unit["unit_id"]), str(claim["claim_id"])),
                    "",
                ),
                confidence=1.0,
                missing_field_reason="not_applicable_or_omitted_by_user",
            )
        )
    return V13ClaimRecordRawOutput(
        schema_version="v13-claim-records-1",
        claims=records,
        coverage_gap_suspected=not records,
        coverage_gap_reason="" if records else "no_user_claim_identified",
    )
