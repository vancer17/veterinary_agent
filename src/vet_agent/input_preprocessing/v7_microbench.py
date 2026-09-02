"""Model adapters for the V7 attribution core microbench."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .v7_contracts import (
    V7ExperimentId,
    V7IntentBatchRawOutput,
    V7ParticipantRawOutput,
    V7QuoteSelectionRawOutput,
    V7RelationRawOutput,
    V7ThinExtractionRawOutput,
)
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value

V7_PROMPT_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V7_PROMPT_VERSION",
    "v7-attribution-dev-20260827-1",
)


class V7MicrobenchError(RuntimeError):
    """A model adapter failed after the bounded same-contract retry."""

    def __init__(
        self,
        *,
        experiment_id: str,
        first_attempt_status: str,
        first_attempt_error: str,
        attempt_count: int,
    ) -> None:
        super().__init__(f"v7_microbench_failed:{experiment_id}:{first_attempt_error}")
        self.experiment_id = experiment_id
        self.first_attempt_status = first_attempt_status
        self.first_attempt_error = first_attempt_error
        self.attempt_count = attempt_count


class V7StructuredClient(Protocol):
    """Minimal structured-output interface required by V7."""

    @property
    def available(self) -> bool: ...

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type[BaseModel],
        model: str,
        temperature: float = 0.0,
    ) -> Any: ...


@dataclass(frozen=True)
class V7ModelExecution:
    """Execution audit for one cached model run unit."""

    output: Any
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0


class V7MicroAnalyzer:
    """Invoke one narrow V7 schema at a time and audit retry/cache behavior."""

    def __init__(
        self,
        *,
        qwen: V7StructuredClient,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
    ) -> None:
        if not qwen.available:
            raise ValueError("v7_structured_client_unavailable")
        self.qwen = qwen
        self.model = model
        self.cache = cache

    async def run_intent(
        self,
        *,
        experiment_id: V7ExperimentId,
        units: list[dict[str, Any]],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        return await self._run(
            experiment_id=experiment_id,
            schema_version="v7-intent-binary-raw",
            response_model=V7IntentBatchRawOutput,
            task="identify_one_binary_input_intent",
            payload={
                "intent_kind": experiment_id.value,
                "units": units,
            },
            turn_context_digest=turn_context_digest,
        )

    def run_intent_for_test(
        self,
        *,
        experiment_id: V7ExperimentId,
        units: list[dict[str, Any]],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        """Synchronous cache/adapter test helper."""

        return asyncio.run(
            self.run_intent(
                experiment_id=experiment_id,
                units=units,
                turn_context_digest=turn_context_digest,
            )
        )

    async def run_quote_selection(
        self,
        *,
        units: list[dict[str, Any]],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        return await self._run(
            experiment_id=V7ExperimentId.QUOTE_GOLDEN_SELECT,
            schema_version="v7-quote-selection-raw",
            response_model=V7QuoteSelectionRawOutput,
            task="select_sub_quotes_inside_golden_evidence",
            payload={"units": units},
            turn_context_digest=turn_context_digest,
        )

    async def run_thin_min(
        self,
        *,
        unit: dict[str, Any],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        return await self._run(
            experiment_id=V7ExperimentId.THIN_LIVE_MIN,
            schema_version="v7-thin-min-raw",
            response_model=V7ThinExtractionRawOutput,
            task="extract_minimal_thin_user_claims",
            payload={"unit": unit},
            turn_context_digest=turn_context_digest,
        )

    async def run_relation(
        self,
        *,
        units: list[dict[str, Any]],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        return await self._run(
            experiment_id=V7ExperimentId.RELATION_GOLDEN,
            schema_version="v7-relation-raw",
            response_model=V7RelationRawOutput,
            task="classify_relation_from_golden_quote",
            payload={"units": units},
            turn_context_digest=turn_context_digest,
        )

    async def run_participant(
        self,
        *,
        units: list[dict[str, Any]],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        return await self._run(
            experiment_id=V7ExperimentId.PART_GOLDEN,
            schema_version="v7-participant-raw",
            response_model=V7ParticipantRawOutput,
            task="select_action_participants_from_turn_context_candidates",
            payload={"units": units},
            turn_context_digest=turn_context_digest,
        )

    async def _run(
        self,
        *,
        experiment_id: V7ExperimentId,
        schema_version: str,
        response_model: type[BaseModel],
        task: str,
        payload: dict[str, Any],
        turn_context_digest: str,
    ) -> V7ModelExecution:
        key = V7RunUnitKey(
            experiment_id=experiment_id.value,
            model=self.model,
            prompt_version=V7_PROMPT_VERSION,
            schema_version=schema_version,
            input_digest=digest_value(payload),
            turn_context_digest=turn_context_digest,
        )
        if self.cache is not None:
            cached = self.cache.get(
                key,
                response_model_name=response_model.__name__,
            )
            if cached is not None:
                return V7ModelExecution(
                    output=response_model.model_validate(cached),
                    attempt_count=0,
                    first_attempt_status="cache_hit",
                    cache_hit=True,
                )

        started = time.perf_counter()
        first_status = "succeeded"
        first_error = ""
        attempt_count = 0
        output: Any = None
        last_error: Exception | None = None
        for attempt in range(1, 3):
            attempt_count = attempt
            try:
                output = await self.qwen.chat_structured(
                    _messages(task=task, payload=payload),
                    response_model=response_model,
                    model=self.model,
                    temperature=0.0,
                )
                break
            except ValidationError as exc:
                last_error = exc
                first_status = "schema_invalid"
                first_error = str(exc)
            except (OSError, RuntimeError, ValueError) as exc:
                last_error = exc
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"

        if output is None:
            assert last_error is not None
            raise V7MicrobenchError(
                experiment_id=experiment_id.value,
                first_attempt_status=first_status,
                first_attempt_error=first_error,
                attempt_count=attempt_count,
            ) from last_error

        latency = max(0, int((time.perf_counter() - started) * 1000))
        if self.cache is not None:
            self.cache.put(
                key,
                response_model_name=response_model.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempt_count,
            )
        return V7ModelExecution(
            output=output,
            attempt_count=attempt_count,
            first_attempt_status=first_status,
            first_attempt_error=first_error[:500],
            latency_ms=latency,
        )


def _messages(*, task: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是兽医 Agent 输入前置预处理 V7 专项归因实验器。"
                "只输出结构化 JSON，不诊断、不判断风险、不生成建议、"
                "不补造事实、不修复 quote。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"task={task}\n"
                "rules:\n"
                "1. quote 必须逐字复制输入文本，可保守处理空白和标点。\n"
                "2. 无法确定时使用 false / unresolved / 空字符串。\n"
                "3. 不输出任务说明中未要求的字段。\n"
                f"payload={payload}"
            ),
        },
    ]
