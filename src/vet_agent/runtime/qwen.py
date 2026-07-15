from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from src.vet_agent.config import Settings


class QwenClient:
    """OpenAI-compatible DashScope/Qwen chat client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(max(1, settings.qwen_max_concurrent_requests))
        self._pace_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._failure_count = 0
        self._circuit_open_until = 0.0

    @property
    def available(self) -> bool:
        return self.settings.qwen_configured

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        if not self.available:
            if self.settings.allow_mock_llm:
                return self._mock_reply(messages)
            raise RuntimeError("Qwen API key is not configured")

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

    async def _chat_with_retries(
        self,
        messages: list[dict[str, str]],
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
        messages: list[dict[str, str]],
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
            "Authorization": f"Bearer {self.settings.qwen_api_key}",
            "Content-Type": "application/json",
        }
        await self._pace()
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.qwen_base_url}/chat/completions",
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

    def _mock_reply(self, messages: list[dict[str, str]]) -> str:
        user_text = "\n".join(message["content"] for message in messages if message.get("role") == "user")
        if "结构化问诊状态已足够" in user_text:
            return (
                "分诊/紧急度: 目前根据已补充的信息，暂未看到必须立即急诊的红旗，但仍需要继续观察变化。\n"
                "可能方向与依据: 更偏向轻度、短时的消化道不适或饮食刺激；依据是起病时间、精神食欲、呕吐和大便信息已经补充。\n"
                "现在可以做什么: 先保证饮水，暂停零食和新食物，少量多餐，观察精神、食欲、呕吐、腹泻次数和是否出现血便。不要自行喂人药。\n"
                "线下兽医兜底: 如果症状加重、持续超过 24 小时、出现血便/频繁呕吐/精神明显变差，或幼年、老年、基础病宠物，请尽快线下就诊。"
            )
        if "行为" in user_text or "乱叫" in user_text or "拆家" in user_text:
            return (
                "从现有信息看，这更像行为和环境管理问题，但仍要先排除突然疼痛、食欲下降或神经异常等医疗红旗。"
                "建议先记录发生时间、诱因和持续多久，增加可预测的运动与嗅闻活动，并用奖励训练替代惩罚。"
            )
        if "喂" in user_text or "吃" in user_text or "粮" in user_text:
            return (
                "饲养建议应结合物种、年龄、体重、体况和活动量。先按当前主粮包装建议作为基线，"
                "再根据体重趋势和活动量小幅调整，并避免突然换粮。"
            )
        return (
            "我会先做分诊:目前还需要确认症状开始时间、精神食欲、是否呕吐腹泻或咳喘。"
            "如果症状轻微且精神食欲正常，可短时观察；如果加重或出现红旗症状，请尽快就医。"
        )
