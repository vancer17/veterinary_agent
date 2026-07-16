from __future__ import annotations

from typing import Any

import httpx

from vet_agent.config import Settings


class QwenEmbeddingClient:
    """OpenAI-compatible LiteLLM proxy embedding client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return self.settings.litellm_configured

    def embed(self, text: str) -> list[float]:
        if not self.available:
            raise RuntimeError("LiteLLM proxy is not configured")
        payload: dict[str, Any] = {
            "model": self.settings.qwen_embedding_model,
            "input": text,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.post(
                f"{self.settings.litellm_base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return [float(value) for value in data["data"][0]["embedding"]]
