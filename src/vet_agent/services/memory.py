from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from vet_agent.contracts import TrustedIdentity
from vet_agent.stores.json_store import JsonDocumentStore


class MemoryService:
    def __init__(self, store: JsonDocumentStore) -> None:
        self.store = store
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._idempotency_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def turn_lock(self, identity: TrustedIdentity):
        key = f"{identity.user_id}:{identity.pet_id}:{identity.session_id}"
        lock = self._turn_locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield

    async def read(self, identity: TrustedIdentity) -> dict[str, Any]:
        data = self.store.load()
        pet_memory = dict(data.get("pets", {}).get(identity.pet_id, {}))
        facts = pet_memory.get("facts")
        if isinstance(facts, dict):
            pet_memory["facts"] = list(facts.values())
        return {
            "owner": data.get("owners", {}).get(identity.user_id, {}),
            "pet": pet_memory,
            "session": data.get("sessions", {}).get(identity.session_id, {}),
        }

    async def remember_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        medical: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        pet_memory = data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        item = {
            "at": datetime.now(UTC).isoformat(),
            "user_text": user_text[:500],
            "summary": summary[:1000],
            "medical": medical,
            "metadata": metadata or {},
        }
        pet_memory.setdefault("turns", []).append(item)
        session_memory.setdefault("turns", []).append(item)
        pet_memory["last_summary"] = summary[:1000]
        session_memory["last_summary"] = summary[:1000]
        self.store.save(data)

    async def read_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        data = self.store.load()
        return (
            data.get("sessions", {})
            .get(identity.session_id, {})
            .get("consultation_state", {})
        )

    async def save_consultation_state(
        self,
        identity: TrustedIdentity,
        state: dict[str, Any],
    ) -> None:
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        session_memory["consultation_state"] = state
        data["pets"][identity.pet_id]["consultation_state"] = state
        self.store.save(data)

    async def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        data = self.store.load()
        return (
            data.get("sessions", {})
            .get(identity.session_id, {})
            .get("task_consultation_states", {})
        )

    async def save_task_consultation_states(
        self,
        identity: TrustedIdentity,
        states: dict[str, Any],
    ) -> None:
        data = self.store.load()
        data.setdefault("owners", {}).setdefault(identity.user_id, {})
        data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        session_memory = data.setdefault("sessions", {}).setdefault(identity.session_id, {"turns": []})
        session_memory["task_consultation_states"] = states
        data["pets"][identity.pet_id]["task_consultation_states"] = states
        self.store.save(data)

    async def clear_consultation_state(self, identity: TrustedIdentity) -> None:
        data = self.store.load()
        data.get("sessions", {}).get(identity.session_id, {}).pop("consultation_state", None)
        data.get("sessions", {}).get(identity.session_id, {}).pop("task_consultation_states", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("consultation_state", None)
        data.get("pets", {}).get(identity.pet_id, {}).pop("task_consultation_states", None)
        self.store.save(data)

    async def delete_pet_memory(self, pet_id: str, user_id: str | None = None) -> None:
        data = self.store.load()
        data.get("pets", {}).pop(pet_id, None)
        self.store.save(data)

    async def upsert_pet_fact(
        self,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float = 1.0,
        source_turn_id: str | None = None,
        source_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        data = self.store.load()
        pet_memory = data.setdefault("pets", {}).setdefault(identity.pet_id, {"turns": []})
        facts = pet_memory.setdefault("facts", {})
        key = f"{fact_type}:{fact_key}"
        facts[key] = {
            "fact_type": fact_type,
            "fact_key": fact_key,
            "fact_value": fact_value,
            "confidence": confidence,
            "source_turn_id": source_turn_id,
            "source_text": source_text,
            "metadata": metadata or {},
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.store.save(data)

    async def read_idempotency_response(
        self,
        identity: TrustedIdentity,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        data = self.store.load()
        key = self._idempotency_key(identity, idempotency_key)
        record = data.get("idempotency_records", {}).get(key)
        if isinstance(record, dict):
            snapshot = record.get("response_snapshot")
            return dict(snapshot) if isinstance(snapshot, dict) else None
        return None

    async def begin_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        wait_seconds: float,
        processing_ttl_seconds: float,
    ) -> dict[str, Any]:
        key = self._idempotency_key(identity, idempotency_key)
        lock = self._idempotency_locks.setdefault(key, asyncio.Lock())
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            async with lock:
                data = self.store.load()
                records = data.setdefault("idempotency_records", {})
                record = records.get(key)
                if isinstance(record, dict):
                    snapshot = record.get("response_snapshot")
                    if record.get("status") == "completed" and isinstance(snapshot, dict):
                        return {"state": "replayed", "response_snapshot": dict(snapshot)}
                    if self._is_stale(self._parse_time(record.get("updated_at")), processing_ttl_seconds):
                        records[key] = self._processing_record(request_id, trace_id)
                        self.store.save(data)
                        return {"state": "claimed"}
                else:
                    records[key] = self._processing_record(request_id, trace_id)
                    self.store.save(data)
                    return {"state": "claimed"}
            if asyncio.get_running_loop().time() >= deadline:
                return {"state": "busy"}
            await asyncio.sleep(0.05)

    async def save_idempotency_response(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        data = self.store.load()
        key = self._idempotency_key(identity, idempotency_key)
        data.setdefault("idempotency_records", {})[key] = {
            "request_id": request_id,
            "trace_id": trace_id,
            "response_id": response_snapshot.get("id"),
            "status": "completed",
            "response_snapshot": response_snapshot,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.store.save(data)

    async def mark_idempotency_failed(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        error_type: str,
    ) -> None:
        data = self.store.load()
        key = self._idempotency_key(identity, idempotency_key)
        data.setdefault("idempotency_records", {})[key] = {
            "request_id": request_id,
            "trace_id": trace_id,
            "status": "failed",
            "response_snapshot": None,
            "error_type": error_type,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.store.save(data)

    def _idempotency_key(self, identity: TrustedIdentity, idempotency_key: str) -> str:
        return f"{identity.user_id}:{identity.pet_id}:{identity.session_id}:{idempotency_key}"

    def _processing_record(self, request_id: str, trace_id: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "status": "processing",
            "response_snapshot": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _parse_time(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _is_stale(self, updated_at: datetime | None, ttl_seconds: float) -> bool:
        if updated_at is None:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated_at).total_seconds() > ttl_seconds
