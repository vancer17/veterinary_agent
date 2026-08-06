"""
文件：src/vet_agent/runtime/qwen.py
作用：封装模型调用、向量生成与外部运行时能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from vet_agent import Settings


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
                except Exception as exc:
                    last_error = exc
                    if not self._retryable_exception(exc):
                        break
            self._record_failure()
        raise RuntimeError("Qwen chat request failed") from last_error

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
                except Exception as exc:
                    last_error = exc
                    if not self._retryable_exception(exc):
                        break
            self._record_failure()
        raise RuntimeError("Qwen vision request failed") from last_error

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
