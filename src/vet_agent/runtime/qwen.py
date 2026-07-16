from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from vet_agent.config import Settings


class QwenClient:
    """OpenAI-compatible LiteLLM proxy client for Qwen-family models."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(max(1, settings.qwen_max_concurrent_requests))
        self._pace_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._failure_count = 0
        self._circuit_open_until = 0.0

    @property
    def available(self) -> bool:
        return self.settings.litellm_configured

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
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
        await asyncio.sleep(0)

    async def _pace(self) -> None:
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
        primary = model or self.settings.default_model
        candidates = [primary]
        for fallback in self.settings.qwen_fallback_models:
            if fallback and fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _retry_delay(self, attempt: int) -> float:
        base = max(0.05, self.settings.qwen_retry_base_delay_seconds)
        jitter = random.uniform(0, base / 2)
        return min(8.0, base * (2**attempt) + jitter)

    def _retryable_exception(self, exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        return False

    def _record_success(self) -> None:
        self._failure_count = 0
        self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        self._failure_count += 1
        threshold = max(1, self.settings.qwen_circuit_breaker_failure_threshold)
        if self._failure_count >= threshold:
            self._circuit_open_until = time.monotonic() + max(1.0, self.settings.qwen_circuit_breaker_cooldown_seconds)

    def _circuit_open(self) -> bool:
        if self._circuit_open_until <= 0:
            return False
        if time.monotonic() >= self._circuit_open_until:
            self._circuit_open_until = 0.0
            self._failure_count = 0
            return False
        return True
