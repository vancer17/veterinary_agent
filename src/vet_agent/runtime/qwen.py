"""
文件：src/vet_agent/runtime/qwen.py
作用：封装模型调用、向量生成与外部运行时能力。
范围：覆盖普通对话、Pydantic 结构化对话、M05 单次结构化传输、
      视觉理解和向量生成所需的 OpenAI 兼容 LiteLLM 调用。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import asyncio
import random
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from vet_agent import Settings

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


class StructuredChatResponse(BaseModel):
    """表示一次底层结构化模型传输的原始响应与调用元数据。

    :return: 无返回值；该对象不解析、修复或验证模型输出语义。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
    )

    content: object | None = Field(description="模型返回的原始 structured content。")
    requested_model: str = Field(description="调用方显式请求的模型名称。")
    response_model: str | None = Field(
        default=None,
        description="LiteLLM 响应中的实际模型快照。",
    )
    response_id: str | None = Field(
        default=None,
        description="LiteLLM 响应标识。",
    )
    finish_reason: str | None = Field(
        default=None,
        description="模型 finish reason。",
    )
    prompt_tokens: int | None = Field(
        default=None,
        ge=0,
        description="请求 token 数量。",
    )
    completion_tokens: int | None = Field(
        default=None,
        ge=0,
        description="响应 token 数量。",
    )
    total_tokens: int | None = Field(
        default=None,
        ge=0,
        description="总 token 数量。",
    )
    usage_available: bool = Field(
        default=False,
        description="是否返回完整可用的 token usage。",
    )


def _optional_response_string(value: object | None) -> str | None:
    """读取 LiteLLM 响应中的可选字符串字段。

    :param value: 原始响应字段值。
    :return: 返回非空字符串；缺失时返回 None。
    :raises ValueError: 字段存在但不是字符串或为空字符串时抛出。
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("structured chat response metadata must be a non-empty string")
    return value


def _read_structured_usage(data: dict[str, Any]) -> tuple[int | None, int | None, int | None, bool]:
    """读取结构化模型响应中的 token usage。

    :param data: LiteLLM OpenAI 兼容响应字典。
    :return: 返回请求、响应、总 token 数和 usage 是否完整的四元组。
    :raises TypeError: usage 存在但不是 object 时抛出。
    :raises ValueError: usage 字段缺失、类型非法或数值为负时抛出。
    """
    usage = data.get("usage")
    if usage is None:
        return None, None, None, False
    if not isinstance(usage, dict):
        raise TypeError("structured chat usage must be an object")
    values: list[int] = []
    for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"structured chat usage field is invalid: {field_name}")
        values.append(value)
    return values[0], values[1], values[2], True


class QwenClient:
    """OpenAI-compatible LiteLLM proxy client for Qwen-family models."""

    def __init__(self, settings: Settings) -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :return: 无返回值。
        """
        self.settings = settings
        self._semaphore = asyncio.Semaphore(max(1, settings.qwen_max_concurrent_requests))
        self._pace_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._failure_count = 0
        self._circuit_open_until = 0.0

    @property
    def available(self) -> bool:
        """执行 available 业务逻辑。

        :return: 返回函数执行结果。
        """
        return self.settings.litellm_configured

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """执行 chat 业务逻辑。

        :param messages: 参数 messages。
        :param model: 模型名称。
        :param temperature: 参数 temperature。
        :return: 返回函数执行结果。
        """
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")

        if self._circuit_open():
            raise RuntimeError("Qwen circuit breaker is open")

        model_candidates = self._model_candidates(model)
        last_error: Exception | None = None
        async with self._semaphore:
            for candidate in model_candidates:
                try:
                    result = await self._chat_with_retries(
                        messages,
                        model=candidate,
                        temperature=temperature,
                    )
                    self._record_success()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if not self._retryable_exception(exc):
                        break
            self._record_failure()
        raise RuntimeError("Qwen chat request failed") from last_error

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[StructuredOutputT],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredOutputT:
        """通过 LiteLLM response_format 执行结构化模型调用。

        :param messages: OpenAI 兼容消息列表。
        :param response_model: 用于生成 JSON Schema 与校验响应的 Pydantic 模型。
        :param model: 模型名称；未指定时使用默认文本模型。
        :param temperature: 采样温度。
        :return: 返回通过 Pydantic 校验后的结构化对象。
        """
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")

        if self._circuit_open():
            raise RuntimeError("Qwen circuit breaker is open")

        model_candidates = self._model_candidates(model)
        last_error: Exception | None = None
        async with self._semaphore:
            for candidate in model_candidates:
                try:
                    result = await self._chat_structured_with_retries(
                        messages,
                        response_model=response_model,
                        model=candidate,
                        temperature=temperature,
                    )
                    self._record_success()
                    return result
                except Exception as exc:
                    last_error = exc
                    if not self._retryable_exception(exc):
                        self._record_failure()
                        raise
            self._record_failure()
        raise RuntimeError("Qwen structured chat request failed") from last_error

    async def chat_with_images(
        self,
        *,
        prompt: str,
        image_urls: list[str],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """执行 chat_with_images 业务逻辑。

        :param prompt: 参数 prompt。
        :param image_urls: 参数 image_urls。
        :param model: 模型名称。
        :param temperature: 参数 temperature。
        :return: 返回函数执行结果。
        """
        if not image_urls:
            raise ValueError("image_urls is required")
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")

        if self._circuit_open():
            raise RuntimeError("Qwen circuit breaker is open")

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        model_candidates = [model or self.settings.qwen_vision_model]
        last_error: Exception | None = None
        async with self._semaphore:
            for candidate in model_candidates:
                try:
                    result = await self._chat_with_retries(
                        messages,
                        model=candidate,
                        temperature=temperature,
                    )
                    self._record_success()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if not self._retryable_exception(exc):
                        break
            self._record_failure()
        raise RuntimeError("Qwen vision request failed") from last_error

    async def structured_once(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str,
        temperature: float = 0.0,
        timeout_seconds: float | None = None,
    ) -> StructuredChatResponse:
        """执行一次不带内部重试或隐藏 fallback 的结构化模型调用。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 调用方提供的权威 JSON Schema。
        :param schema_name: 传给模型网关的稳定 schema 名称。
        :param model: 必须精确使用的模型名称。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选本次调用超时时间。
        :return: 返回原始内容、模型快照、usage 和 finish reason。
        :raises RuntimeError: LiteLLM 未配置或 circuit breaker 打开时抛出。
        """
        if not model:
            raise ValueError("model is required for one-shot structured chat")
        if not schema_name:
            raise ValueError("schema_name is required for one-shot structured chat")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")
        if self._circuit_open():
            raise RuntimeError("Qwen circuit breaker is open")

        async with self._semaphore:
            try:
                result = await self._send_raw_structured_chat(
                    messages,
                    json_schema=json_schema,
                    schema_name=schema_name,
                    model=model,
                    temperature=temperature,
                    timeout_seconds=timeout_seconds,
                )
            except Exception:
                self._record_failure()
                raise
            self._record_success()
            return result

    async def _chat_with_retries(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
    ) -> str:
        """执行 _chat_with_retries 内部辅助逻辑。

        :param messages: 参数 messages。
        :param model: 模型名称。
        :param temperature: 参数 temperature。
        :return: 返回函数执行结果。
        """
        last_error: Exception | None = None
        for attempt in range(self.settings.qwen_max_retries + 1):
            try:
                return await self._send_chat(messages, model=model, temperature=temperature)
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.qwen_max_retries or not self._retryable_exception(exc):
                    raise
                await asyncio.sleep(self._retry_delay(attempt))
        raise RuntimeError("Qwen chat retry loop exhausted") from last_error

    async def _chat_structured_with_retries(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type[StructuredOutputT],
        model: str,
        temperature: float,
    ) -> StructuredOutputT:
        """执行结构化模型调用重试流程。

        :param messages: OpenAI 兼容消息列表。
        :param response_model: Pydantic 响应模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回通过校验的结构化对象。
        """
        last_error: Exception | None = None
        for attempt in range(self.settings.qwen_max_retries + 1):
            try:
                return await self._send_structured_chat(
                    messages,
                    response_model=response_model,
                    model=model,
                    temperature=temperature,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.qwen_max_retries or not self._retryable_exception(exc):
                    raise
                await asyncio.sleep(self._retry_delay(attempt))
        raise RuntimeError("Qwen structured chat retry loop exhausted") from last_error

    async def _send_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
    ) -> str:
        """执行 _send_chat 内部辅助逻辑。

        :param messages: 参数 messages。
        :param model: 模型名称。
        :param temperature: 参数 temperature。
        :return: 返回函数执行结果。
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        await self._pace()
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.litellm_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    async def _send_structured_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        response_model: type[StructuredOutputT],
        model: str,
        temperature: float,
    ) -> StructuredOutputT:
        """发送带 response_format 的结构化模型请求。

        :param messages: OpenAI 兼容消息列表。
        :param response_model: Pydantic 响应模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回通过 Pydantic 校验的结构化对象。
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": response_model.model_json_schema(),
                    "strict": True,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        await self._pace()
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.litellm_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            return response_model.model_validate(content)
        if isinstance(content, str):
            return response_model.model_validate_json(content)
        raise ValueError("structured chat content must be a JSON object or JSON string")

    async def _send_raw_structured_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any],
        schema_name: str,
        model: str,
        temperature: float,
        timeout_seconds: float | None,
    ) -> StructuredChatResponse:
        """发送一次原始 strict JSON Schema 模型请求。

        :param messages: OpenAI 兼容消息列表。
        :param json_schema: 调用方提供的权威 JSON Schema。
        :param schema_name: 传给模型网关的稳定 schema 名称。
        :param model: 必须精确使用的模型名称。
        :param temperature: 采样温度。
        :param timeout_seconds: 可选本次调用超时时间。
        :return: 返回原始响应内容和调用 metadata。
        :raises httpx.HTTPError: HTTP 传输或状态校验失败时抛出。
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        timeout = timeout_seconds or self.settings.request_timeout_seconds
        await self._pace()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.settings.litellm_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise TypeError("structured chat response must be an object")
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ValueError("structured chat response must contain one choice")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TypeError("structured chat choice must be an object")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("structured chat message must be an object")
        prompt_tokens, completion_tokens, total_tokens, usage_available = (
            _read_structured_usage(data)
        )
        return StructuredChatResponse(
            content=message.get("content"),
            requested_model=model,
            response_model=_optional_response_string(data.get("model")),
            response_id=_optional_response_string(data.get("id")),
            finish_reason=_optional_response_string(choice.get("finish_reason")),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_available=usage_available,
        )

    async def close(self) -> None:
        """执行 close 业务逻辑。

        :return: 返回函数执行结果。
        """
        await asyncio.sleep(0)

    async def _pace(self) -> None:
        """执行 _pace 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        min_interval = max(0.0, self.settings.qwen_min_interval_seconds)
        if min_interval <= 0:
            return
        async with self._pace_lock:
            now = time.monotonic()
            wait_for = self._last_request_at + min_interval - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _model_candidates(self, model: str | None) -> list[str]:
        """执行 _model_candidates 内部辅助逻辑。

        :param model: 模型名称。
        :return: 返回函数执行结果。
        """
        primary = model or self.settings.default_model
        candidates = [primary]
        for fallback in self.settings.qwen_fallback_models:
            if fallback and fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _retry_delay(self, attempt: int) -> float:
        """执行 _retry_delay 内部辅助逻辑。

        :param attempt: 参数 attempt。
        :return: 返回函数执行结果。
        """
        base = max(0.05, self.settings.qwen_retry_base_delay_seconds)
        jitter = random.uniform(0, base / 2)
        return min(8.0, base * (2**attempt) + jitter)

    def _retryable_exception(self, exc: Exception) -> bool:
        """执行 _retryable_exception 内部辅助逻辑。

        :param exc: 异常对象。
        :return: 返回函数执行结果。
        """
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        return False

    def _record_success(self) -> None:
        """执行 _record_success 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        self._failure_count = 0
        self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        """执行 _record_failure 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        self._failure_count += 1
        threshold = max(1, self.settings.qwen_circuit_breaker_failure_threshold)
        if self._failure_count >= threshold:
            self._circuit_open_until = time.monotonic() + max(1.0, self.settings.qwen_circuit_breaker_cooldown_seconds)

    def _circuit_open(self) -> bool:
        """执行 _circuit_open 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        if self._circuit_open_until <= 0:
            return False
        if time.monotonic() >= self._circuit_open_until:
            self._circuit_open_until = 0.0
            self._failure_count = 0
            return False
        return True
