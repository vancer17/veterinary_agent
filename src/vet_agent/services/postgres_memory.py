from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from vet_agent.contracts import TrustedIdentity
from vet_agent.db.models import (
    ConsultationStateModel,
    ConversationTurnModel,
    IdempotencyRecordModel,
    PetMemoryEpisodeModel,
    PetMemoryFactModel,
)
from vet_agent.db.session import make_session_factory
from vet_agent.services.semantic_memory import DisabledSemanticMemory


DEFAULT_TASK_KEY = "__default__"


class PostgresMemoryService:
    def __init__(self, database_url: str, semantic_memory=None) -> None:
        self.session_factory = make_session_factory(database_url)
        self.semantic_memory = semantic_memory or DisabledSemanticMemory()

    @asynccontextmanager
    async def turn_lock(self, identity: TrustedIdentity):
        lock_key = self._lock_key(identity)
        session = self.session_factory()
        try:
            session.execute(select(func.pg_advisory_lock(lock_key)))
            yield
        finally:
            try:
                session.execute(select(func.pg_advisory_unlock(lock_key)))
            finally:
                session.close()

    async def read(self, identity: TrustedIdentity) -> dict[str, Any]:
        with self.session_factory() as session:
            turns = session.scalars(
                select(ConversationTurnModel)
                .where(
                    ConversationTurnModel.user_id == identity.user_id,
                    ConversationTurnModel.pet_id == identity.pet_id,
                    ConversationTurnModel.session_id == identity.session_id,
                )
                .order_by(desc(ConversationTurnModel.created_at))
                .limit(20)
            ).all()
            facts = session.scalars(
                select(PetMemoryFactModel)
                .where(
                    PetMemoryFactModel.user_id == identity.user_id,
                    PetMemoryFactModel.pet_id == identity.pet_id,
                    PetMemoryFactModel.is_active.is_(True),
                )
                .order_by(PetMemoryFactModel.fact_type, PetMemoryFactModel.fact_key)
            ).all()
            episodes = session.scalars(
                select(PetMemoryEpisodeModel)
                .where(
                    PetMemoryEpisodeModel.user_id == identity.user_id,
                    PetMemoryEpisodeModel.pet_id == identity.pet_id,
                )
                .order_by(desc(PetMemoryEpisodeModel.created_at))
                .limit(10)
            ).all()
            state_rows = session.scalars(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                )
            ).all()

        semantic, semantic_error = await self._semantic_search(identity, self._semantic_query(turns), limit=5)
        last_summary = turns[0].summary if turns else ""
        return {
            "owner": {},
            "pet": {
                "last_summary": last_summary,
                "turns": [self._turn_dict(row) for row in turns],
                "facts": [self._fact_dict(row) for row in facts],
                "episodes": [self._episode_dict(row) for row in episodes],
                "semantic_memories": semantic,
                "semantic_memory_error": semantic_error,
            },
            "session": {
                "last_summary": last_summary,
                "turns": [self._turn_dict(row) for row in turns],
                "consultation_state": next(
                    (row.state for row in state_rows if row.task_key == DEFAULT_TASK_KEY),
                    {},
                ),
                "task_consultation_states": {
                    row.task_key: row.state
                    for row in state_rows
                    if row.task_key != DEFAULT_TASK_KEY
                },
            },
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
        metadata = metadata or {}
        turn_id = str(metadata.get("turn_id") or f"turn_memory_{uuid4().hex}")
        request_id = str(metadata.get("request_id") or f"memory_req_{uuid4().hex}")
        trace_id = str(metadata.get("trace_id") or request_id)
        status = str(metadata.get("status") or "completed")
        with self.session_factory.begin() as session:
            statement = pg_insert(ConversationTurnModel).values(
                turn_id=turn_id,
                request_id=request_id,
                trace_id=trace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                pet_id=identity.pet_id,
                input_text=user_text[:4000],
                summary=summary[:4000],
                status=status,
                medical=medical,
                metadata_json=metadata,
                response_snapshot=metadata.get("response_snapshot"),
            )
            statement = statement.on_conflict_do_nothing(index_elements=["request_id"])
            session.execute(statement)
            if medical:
                session.add(
                    PetMemoryEpisodeModel(
                        user_id=identity.user_id,
                        pet_id=identity.pet_id,
                        session_id=identity.session_id,
                        turn_id=turn_id,
                        title=self._episode_title(user_text),
                        summary=summary[:1200],
                        memory_scope="medium",
                        metadata_json={"source": "conversation_turn", **metadata},
                    )
                )
            if metadata.get("source") == "memory_correction":
                self._upsert_fact_in_session(
                    session,
                    identity,
                    fact_type="owner_preference",
                    fact_key="answer_style",
                    fact_value=summary[:1000],
                    confidence=1.0,
                    source_turn_id=turn_id,
                    source_text=user_text,
                    metadata={"source": "user_correction"},
                )
        await self._semantic_add_turn(identity, user_text=user_text, summary=summary, metadata=metadata)

    async def read_consultation_state(self, identity: TrustedIdentity) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.scalar(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key == DEFAULT_TASK_KEY,
                )
            )
        return dict(row.state) if row else {}

    async def save_consultation_state(self, identity: TrustedIdentity, state: dict[str, Any]) -> None:
        self._upsert_state(identity, DEFAULT_TASK_KEY, state)

    async def read_task_consultation_states(self, identity: TrustedIdentity) -> dict[str, Any]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                    ConsultationStateModel.task_key != DEFAULT_TASK_KEY,
                )
            ).all()
        return {row.task_key: dict(row.state) for row in rows}

    async def save_task_consultation_states(self, identity: TrustedIdentity, states: dict[str, Any]) -> None:
        for task_key, state in states.items():
            self._upsert_state(identity, task_key, state)

    async def clear_consultation_state(self, identity: TrustedIdentity) -> None:
        with self.session_factory.begin() as session:
            session.execute(
                delete(ConsultationStateModel).where(
                    ConsultationStateModel.user_id == identity.user_id,
                    ConsultationStateModel.pet_id == identity.pet_id,
                    ConsultationStateModel.session_id == identity.session_id,
                )
            )

    async def delete_pet_memory(self, pet_id: str, user_id: str | None = None) -> None:
        with self.session_factory.begin() as session:
            turn_where = [ConversationTurnModel.pet_id == pet_id]
            state_where = [ConsultationStateModel.pet_id == pet_id]
            episode_where = [PetMemoryEpisodeModel.pet_id == pet_id]
            fact_where = [PetMemoryFactModel.pet_id == pet_id]
            if user_id:
                turn_where.append(ConversationTurnModel.user_id == user_id)
                state_where.append(ConsultationStateModel.user_id == user_id)
                episode_where.append(PetMemoryEpisodeModel.user_id == user_id)
                fact_where.append(PetMemoryFactModel.user_id == user_id)
            session.execute(delete(ConversationTurnModel).where(*turn_where))
            session.execute(delete(ConsultationStateModel).where(*state_where))
            session.execute(delete(PetMemoryEpisodeModel).where(*episode_where))
            session.execute(delete(PetMemoryFactModel).where(*fact_where))
        await self._semantic_delete_pet(pet_id, user_id=user_id)

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
        with self.session_factory.begin() as session:
            self._upsert_fact_in_session(
                session,
                identity,
                fact_type=fact_type,
                fact_key=fact_key,
                fact_value=fact_value,
                confidence=confidence,
                source_turn_id=source_turn_id,
                source_text=source_text,
                metadata=metadata or {"source": "manual_correction"},
            )

    async def read_idempotency_response(self, identity: TrustedIdentity, idempotency_key: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(IdempotencyRecordModel).where(
                    IdempotencyRecordModel.user_id == identity.user_id,
                    IdempotencyRecordModel.pet_id == identity.pet_id,
                    IdempotencyRecordModel.session_id == identity.session_id,
                    IdempotencyRecordModel.idempotency_key == idempotency_key,
                )
            )
        return dict(row.response_snapshot) if row and row.response_snapshot else None

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
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            inserted = self._insert_processing_idempotency(identity, idempotency_key, request_id, trace_id)
            if inserted:
                return {"state": "claimed"}

            with self.session_factory() as session:
                row = session.scalar(
                    select(IdempotencyRecordModel).where(
                        IdempotencyRecordModel.user_id == identity.user_id,
                        IdempotencyRecordModel.pet_id == identity.pet_id,
                        IdempotencyRecordModel.session_id == identity.session_id,
                        IdempotencyRecordModel.idempotency_key == idempotency_key,
                    )
                )
                if row and row.status == "completed" and row.response_snapshot:
                    return {"state": "replayed", "response_snapshot": dict(row.response_snapshot)}
                if row and self._is_stale(row.updated_at, processing_ttl_seconds):
                    self._claim_stale_idempotency(identity, idempotency_key, request_id, trace_id)
                    return {"state": "claimed"}

            if asyncio.get_running_loop().time() >= deadline:
                return {"state": "busy"}
            await asyncio.sleep(0.08)

    async def save_idempotency_response(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        statement = pg_insert(IdempotencyRecordModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            trace_id=trace_id,
            response_id=response_snapshot.get("id"),
            status="completed",
            response_snapshot=response_snapshot,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_idempotency_scope_key",
            set_={
                "request_id": request_id,
                "trace_id": trace_id,
                "response_id": response_snapshot.get("id"),
                "status": "completed",
                "response_snapshot": response_snapshot,
                "updated_at": datetime.now(UTC),
            },
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    async def mark_idempotency_failed(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        error_type: str,
    ) -> None:
        statement = update(IdempotencyRecordModel).where(
            IdempotencyRecordModel.user_id == identity.user_id,
            IdempotencyRecordModel.pet_id == identity.pet_id,
            IdempotencyRecordModel.session_id == identity.session_id,
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        ).values(
            request_id=request_id,
            trace_id=trace_id,
            status="failed",
            response_snapshot=None,
            updated_at=datetime.now(UTC),
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    def _upsert_state(self, identity: TrustedIdentity, task_key: str, state: dict[str, Any]) -> None:
        statement = pg_insert(ConsultationStateModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            task_key=task_key,
            state=state,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_consultation_states_scope",
            set_={
                "state": state,
                "version": ConsultationStateModel.version + 1,
                "updated_at": datetime.now(UTC),
            },
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    def _upsert_fact_in_session(
        self,
        session,
        identity: TrustedIdentity,
        *,
        fact_type: str,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_turn_id: str | None,
        source_text: str | None,
        metadata: dict[str, Any],
    ) -> None:
        statement = pg_insert(PetMemoryFactModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            fact_type=fact_type,
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_turn_id=source_turn_id,
            source_text=source_text,
            is_active=True,
            metadata_json=metadata,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_pet_memory_facts_key",
            set_={
                "fact_value": fact_value,
                "confidence": confidence,
                "source_turn_id": source_turn_id,
                "source_text": source_text,
                "is_active": True,
                "metadata": metadata,
                "updated_at": datetime.now(UTC),
            },
        )
        session.execute(statement)

    def _insert_processing_idempotency(
        self,
        identity: TrustedIdentity,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
    ) -> bool:
        statement = pg_insert(IdempotencyRecordModel).values(
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            trace_id=trace_id,
            response_id=None,
            status="processing",
            response_snapshot=None,
            updated_at=datetime.now(UTC),
        )
        statement = statement.on_conflict_do_nothing(
            constraint="uq_idempotency_scope_key",
        ).returning(IdempotencyRecordModel.id)
        with self.session_factory.begin() as session:
            return session.scalar(statement) is not None

    def _claim_stale_idempotency(
        self,
        identity: TrustedIdentity,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
    ) -> None:
        statement = update(IdempotencyRecordModel).where(
            IdempotencyRecordModel.user_id == identity.user_id,
            IdempotencyRecordModel.pet_id == identity.pet_id,
            IdempotencyRecordModel.session_id == identity.session_id,
            IdempotencyRecordModel.idempotency_key == idempotency_key,
        ).values(
            request_id=request_id,
            trace_id=trace_id,
            response_id=None,
            status="processing",
            response_snapshot=None,
            updated_at=datetime.now(UTC),
        )
        with self.session_factory.begin() as session:
            session.execute(statement)

    def _is_stale(self, updated_at: datetime | None, ttl_seconds: float) -> bool:
        if updated_at is None:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated_at).total_seconds() > ttl_seconds

    async def _semantic_search(
        self,
        identity: TrustedIdentity,
        query: str,
        *,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        try:
            return await self.semantic_memory.search(identity, query, limit=limit), None
        except Exception as exc:
            return [], type(exc).__name__

    async def _semantic_add_turn(
        self,
        identity: TrustedIdentity,
        *,
        user_text: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> None:
        try:
            await self.semantic_memory.add_turn(identity, user_text=user_text, summary=summary, metadata=metadata)
        except Exception:
            return None

    async def _semantic_delete_pet(self, pet_id: str, *, user_id: str | None = None) -> None:
        try:
            await self.semantic_memory.delete_pet(pet_id, user_id=user_id)
        except Exception:
            return None

    def _lock_key(self, identity: TrustedIdentity) -> int:
        raw = f"{identity.user_id}:{identity.pet_id}:{identity.session_id}".encode("utf-8")
        value = int.from_bytes(hashlib.sha256(raw).digest()[:8], byteorder="big", signed=False)
        return value - (1 << 63)

    def _semantic_query(self, turns: list[ConversationTurnModel]) -> str:
        return turns[0].input_text if turns else "pet memory"

    def _episode_title(self, user_text: str) -> str:
        return (user_text.strip().splitlines()[0] or "本轮咨询")[:80]

    def _turn_dict(self, row: ConversationTurnModel) -> dict[str, Any]:
        return {
            "turn_id": row.turn_id,
            "request_id": row.request_id,
            "trace_id": row.trace_id,
            "user_text": row.input_text,
            "summary": row.summary,
            "medical": row.medical,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _fact_dict(self, row: PetMemoryFactModel) -> dict[str, Any]:
        return {
            "fact_type": row.fact_type,
            "fact_key": row.fact_key,
            "fact_value": row.fact_value,
            "confidence": row.confidence,
            "source_turn_id": row.source_turn_id,
            "source_text": row.source_text,
            "metadata": row.metadata_json or {},
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _episode_dict(self, row: PetMemoryEpisodeModel) -> dict[str, Any]:
        return {
            "title": row.title,
            "summary": row.summary,
            "memory_scope": row.memory_scope,
            "metadata": row.metadata_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
