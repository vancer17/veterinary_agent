from __future__ import annotations

from typing import Any

import httpx

from vet_agent.config import Settings
from vet_agent.contracts import TrustedIdentity


class DisabledSemanticMemory:
    enabled = False

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def search(self, identity: TrustedIdentity, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return []

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        return None


class Mem0RestSemanticMemory:
    """Real Mem0 middleware client using the self-hosted REST API."""

    enabled = True

    def __init__(self, *, base_url: str, api_key: str | None, timeout_seconds: float) -> None:
        if not base_url:
            raise ValueError("MEM0_BASE_URL is required when ENABLE_MEM0=true")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload_metadata = {
            "user_id": identity.user_id,
            "pet_id": identity.pet_id,
            "session_id": identity.session_id,
            "memory_scope": "semantic",
            **(metadata or {}),
        }
        payload = {
            "messages": [
                {"role": "user", "content": user_text[:4000]},
                {"role": "assistant", "content": summary[:4000]},
            ],
            "user_id": identity.user_id,
            "run_id": identity.pet_id,
            "metadata": payload_metadata,
        }
        await self._request("POST", "/memories", json=payload)

    async def search(self, identity: TrustedIdentity, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        payload = {
            "query": query[:2000],
            "filters": {"user_id": identity.user_id, "run_id": identity.pet_id},
            "top_k": limit,
        }
        data = await self._request("POST", "/search", json=payload)
        if isinstance(data, dict):
            memories = data.get("results") or data.get("memories") or data.get("data") or []
        else:
            memories = data or []
        return [item for item in memories if isinstance(item, dict)]

    async def delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        params = {"run_id": pet_id}
        if user_id:
            params["user_id"] = user_id
        await self._request("DELETE", "/memories", params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()


def make_semantic_memory(settings: Settings):
    if not settings.enable_mem0:
        return DisabledSemanticMemory()
    return Mem0RestSemanticMemory(
        base_url=settings.mem0_base_url,
        api_key=settings.mem0_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
