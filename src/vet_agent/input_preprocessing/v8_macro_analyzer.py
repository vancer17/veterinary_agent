"""Model-facing adapters for V8 macro semantic perception.

All adapters emit the same Pydantic contract and receive the same span pool.
The model is explicitly forbidden from returning quote strings; deterministic
schema validation is the first gate, not a repair mechanism.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ValidationError

from .runtime_helpers import make_runtime_settings
from .v7_run_cache import V7RunCache, V7RunUnitKey, digest_value
from .v8_contracts import V8MacroSemanticRawOutput, V8SpanCandidate

V8_PROMPT_VERSION = os.getenv(
    "INPUT_PREPROCESSING_V8_PROMPT_VERSION",
    "v8-span-macro-dev-20260827-1",
)
V8_SCHEMA_VERSION = "v8-macro-raw"
V8_DEFAULT_OUTER_ATTEMPTS = 2
V8StructuredAdapter = Literal["base", "instructor", "baml"]


class V8StructuredClient(Protocol):
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
class V8ModelExecution:
    output: V8MacroSemanticRawOutput
    adapter: str
    attempt_count: int
    first_attempt_status: str
    first_attempt_error: str = ""
    cache_hit: bool = False
    latency_ms: int = 0
    model_call_count: int = 1
    internal_retry_limit: int = 0


class V8BaseStructuredClient:
    """LiteLLM response_format + Pydantic baseline adapter."""

    adapter_name = "response_format"
    internal_retry_limit = 0

    def __init__(self, qwen: Any, *, internal_retry_limit: int = 0) -> None:
        if not qwen.available:
            raise ValueError("v8_structured_client_unavailable")
        if internal_retry_limit not in {0, 1}:
            raise ValueError("v8_base_internal_retry_limit_must_be_0_or_1")
        self.qwen = qwen
        self.internal_retry_limit = internal_retry_limit

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
    ) -> Any:
        return await self.qwen.chat_structured(
            messages,
            response_model=response_model,
            model=model,
            temperature=0.0,
        )


class V8InstructorStructuredClient:
    """Optional Instructor adapter using the same OpenAI-compatible endpoint."""

    adapter_name = "instructor"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 45.0,
        internal_retry_limit: int = 0,
    ) -> None:
        if internal_retry_limit < 0 or internal_retry_limit > 1:
            raise ValueError("v8_instructor_internal_retry_limit_must_be_0_or_1")
        try:
            import instructor  # type: ignore[import-not-found]
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise ValueError("v8_instructor_unavailable") from exc
        self.internal_retry_limit = internal_retry_limit
        self.client = instructor.from_openai(
            AsyncOpenAI(
                base_url=base_url,
                api_key=api_key,
                max_retries=0,
                timeout=timeout_seconds,
            )
        )

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
    ) -> Any:
        return await self.client.chat.completions.create(
            model=model,
            messages=cast(Any, messages),
            response_model=response_model,
            temperature=0.0,
            # Instructor patches this keyword at the call site. Passing the
            # same option to from_openai() makes this SDK version inject it a
            # second time. Keep transport and semantic retries separated here.
            max_retries=self.internal_retry_limit,
        )


class V8BamlStructuredClient:
    """Optional BAML adapter loaded from a configured generated module.

    BAML generated code is project-specific.  The experiment deliberately does
    not import a generic package or silently emulate BAML; the module must
    expose ``async extract_v8_macro(payload)`` and return ``V8MacroSemanticRawOutput``.
    """

    adapter_name = "baml"

    def __init__(self) -> None:
        module_name = os.getenv(
            "INPUT_PREPROCESSING_V8_BAML_MODULE",
            "vet_agent.input_preprocessing.v8_baml_client",
        )
        if not module_name:
            raise ValueError("v8_baml_module_required")
        import importlib

        module = importlib.import_module(module_name)
        function = getattr(module, "extract_v8_macro", None)
        if function is None:
            raise ValueError("v8_baml_function_missing")
        self.function = function

    async def run_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
        model: str,
    ) -> Any:
        if response_model is not V8MacroSemanticRawOutput:
            raise ValueError("v8_baml_unsupported_response_model")
        return await self.function(messages=messages, model=model)

    @property
    def internal_retry_limit(self) -> int:
        return int(getattr(self.function, "INTERNAL_RETRY_LIMIT", 0))


class V8MacroAnalyzer:
    """Run one macro schema call with cache, bounded retry, and execution audit."""

    def __init__(
        self,
        *,
        client: V8StructuredClient,
        model: str = "qwen-plus",
        cache: V7RunCache | None = None,
        max_attempts: int = V8_DEFAULT_OUTER_ATTEMPTS,
    ) -> None:
        self.client = client
        self.model = model
        self.cache = cache
        if max_attempts not in {1, 2}:
            raise ValueError("v8_macro_max_attempts_must_be_1_or_2")
        self.max_attempts = max_attempts

    async def run(
        self,
        *,
        experiment_id: str,
        user_text: str,
        spans: list[V8SpanCandidate],
        turn_context: dict[str, Any],
    ) -> V8ModelExecution:
        compact_spans = [
            {
                "span_id": span.span_id,
                "text": span.text,
                "label": span.label.value,
                "start": span.start,
                "end": span.end,
            }
            for span in spans
        ]
        payload = {
            "user_text": user_text,
            "span_candidates": compact_spans,
            "turn_context": turn_context,
        }
        input_digest = digest_value(payload)
        context_digest = digest_value(turn_context)
        key = V7RunUnitKey(
            experiment_id=experiment_id,
            model=self.model,
            prompt_version=V8_PROMPT_VERSION,
            schema_version=V8_SCHEMA_VERSION,
            input_digest=input_digest,
            turn_context_digest=context_digest,
            adapter=self.client.adapter_name,
        )
        if self.cache is not None:
            cached_raw = self.cache.get(
                key, response_model_name=V8MacroSemanticRawOutput.__name__
            )
            cached = (
                V8MacroSemanticRawOutput.model_validate(cached_raw)
                if isinstance(cached_raw, dict)
                else None
            )
            if isinstance(cached, V8MacroSemanticRawOutput):
                return V8ModelExecution(
                    output=cached,
                    adapter=self.client.adapter_name,
                    attempt_count=0,
                    first_attempt_status="cache_hit",
                    cache_hit=True,
                    model_call_count=0,
                )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是输入前置预处理实验器。你一次阅读完整用户输入和候选 span，"
                    "只输出结构化 JSON。你不得输出自由 quote 字符串；所有证据只能引用 span_id。"
                    "你不诊断、不判断医学风险、不生成建议、不决定系统路由。"
                    "多个 discourse act 可以同时为 true。无法判断时输出空列表，不要编造。"
                ),
            },
            {
                "role": "user",
                # qwen-plus through LiteLLM rejects a raw mapping as message
                # content. Serialize the complete payload instead of dropping
                # span or TurnContext context.
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ]
        started = time.perf_counter()
        first_status = "ok"
        first_error = ""
        attempts = 0
        output: V8MacroSemanticRawOutput | None = None
        for _ in range(self.max_attempts):
            attempts += 1
            try:
                output = V8MacroSemanticRawOutput.model_validate(
                    await self.client.run_structured(
                        messages=messages,
                        response_model=V8MacroSemanticRawOutput,
                        model=self.model,
                    )
                )
                break
            except ValidationError as exc:
                first_status = "schema_invalid"
                first_error = str(exc)[:1200]
                if _ == 0:
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                first_status = "dependency_failed"
                first_error = f"{type(exc).__name__}:{exc}"[:1200]
                if _ == 0:
                    continue
                break
        latency_ms = int((time.perf_counter() - started) * 1000)
        if output is None:
            raise RuntimeError(f"v8_macro_adapter_failed:{first_status}:{first_error}")
        if self.cache is not None:
            self.cache.put(
                key,
                response_model_name=V8MacroSemanticRawOutput.__name__,
                output=output.model_dump(mode="json"),
                attempt_count=attempts,
            )
        return V8ModelExecution(
            output=output,
            adapter=self.client.adapter_name,
            attempt_count=attempts,
            first_attempt_status=first_status,
            first_attempt_error=first_error,
            latency_ms=latency_ms,
            model_call_count=attempts * (1 + self._internal_retry_limit()),
            internal_retry_limit=self._internal_retry_limit(),
        )

    def _internal_retry_limit(self) -> int:
        return self.client.internal_retry_limit


def build_v8_structured_client(adapter: V8StructuredAdapter) -> V8StructuredClient:
    if adapter == "base":
        from vet_agent.runtime import QwenClient

        settings = make_runtime_settings()
        base_internal_retries = int(
            os.getenv("INPUT_PREPROCESSING_V8_BASE_INTERNAL_RETRIES", "0")
        )
        if base_internal_retries not in {0, 1}:
            raise ValueError("v8_base_internal_retry_limit_must_be_0_or_1")
        settings = replace(
            settings,
            qwen_max_retries=base_internal_retries,
        )
        return V8BaseStructuredClient(
            QwenClient(settings),
            internal_retry_limit=base_internal_retries,
        )
    if adapter == "instructor":
        settings = make_runtime_settings()
        return V8InstructorStructuredClient(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key or "",
            timeout_seconds=settings.request_timeout_seconds,
            internal_retry_limit=int(
                os.getenv(
                    "INPUT_PREPROCESSING_V8_INSTRUCTOR_INTERNAL_RETRIES",
                    "0",
                ),
            ),
        )
    if adapter == "baml":
        return V8BamlStructuredClient()
    raise ValueError(f"unsupported_v8_adapter:{adapter}")
