from __future__ import annotations

from typing import Any

from src.vet_agent.contracts import TrustedIdentity


class NullSemanticMemory:
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

    async def delete_pet(self, pet_id: str) -> None:
        return None


class Mem0SemanticMemory:
    """Optional mem0-backed semantic memory. Failures never block core storage."""

    enabled = True

    def __init__(self, *, api_key: str | None = None) -> None:
        self.client = self._make_client(api_key)

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
        messages = [
            {"role": "user", "content": user_text[:2000]},
            {"role": "assistant", "content": summary[:2000]},
        ]
        try:
            self.client.add(messages, user_id=identity.user_id, metadata=payload_metadata)
        except TypeError:
            try:
                self.client.add(messages, filters={"user_id": identity.user_id, "pet_id": identity.pet_id}, metadata=payload_metadata)
            except Exception:
                return None
        except Exception:
            return None
        return None

    async def search(self, identity: TrustedIdentity, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        filters = {"user_id": identity.user_id, "pet_id": identity.pet_id}
        try:
            result = self.client.search(query, user_id=identity.user_id, filters=filters, limit=limit)
        except TypeError:
            try:
                result = self.client.search(query, filters=filters, limit=limit)
            except Exception:
                return []
        except Exception:
            return []
        if isinstance(result, dict):
            memories = result.get("results") or result.get("memories") or []
        else:
            memories = result or []
        return [item for item in memories if isinstance(item, dict)]

    async def delete_pet(self, pet_id: str) -> None:
        try:
            self.client.delete_all(filters={"pet_id": pet_id})
        except Exception:
            return None

    def _make_client(self, api_key: str | None):
        try:
            if api_key:
                from mem0 import MemoryClient

                return MemoryClient(api_key=api_key)
            from mem0 import Memory

            return Memory()
        except Exception as exc:
            raise RuntimeError("mem0 is not installed or could not be initialized") from exc


def make_semantic_memory(*, enabled: bool, api_key: str | None = None):
    if not enabled:
        return NullSemanticMemory()
    try:
        return Mem0SemanticMemory(api_key=api_key)
    except Exception:
        return NullSemanticMemory()
