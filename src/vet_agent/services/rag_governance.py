from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from vet_agent.db.models import KnowledgeChunkModel, RagAuditEventModel
from vet_agent.db.session import make_session_factory
from vet_agent.stores.json_store import JsonDocumentStore


VALID_REVIEW_STATUSES = {"approved", "pending", "rejected", "quarantined"}


class RagGovernanceService:
    def __init__(self, store: "RagGovernanceStore") -> None:
        self.store = store

    async def list_chunks(self, *, review_status: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return await self.store.list_chunks(review_status=review_status, limit=limit, offset=offset)

    async def update_chunk(
        self,
        chunk_id: int,
        *,
        enabled: bool | None = None,
        review_status: str | None = None,
        quality_score: float | None = None,
        disabled_reason: str | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if review_status is not None and review_status not in VALID_REVIEW_STATUSES:
            raise ValueError(f"review_status must be one of {sorted(VALID_REVIEW_STATUSES)}")
        if quality_score is not None and not 0 <= quality_score <= 1:
            raise ValueError("quality_score must be between 0 and 1")
        return await self.store.update_chunk(
            chunk_id,
            enabled=enabled,
            review_status=review_status,
            quality_score=quality_score,
            disabled_reason=disabled_reason,
            actor_id=actor_id,
            reason=reason,
        )

    async def stats(self) -> dict[str, Any]:
        return await self.store.stats()


class RagGovernanceStore:
    async def list_chunks(self, *, review_status: str | None, limit: int, offset: int) -> dict[str, Any]:
        raise NotImplementedError

    async def update_chunk(self, chunk_id: int, **kwargs) -> dict[str, Any]:
        raise NotImplementedError

    async def stats(self) -> dict[str, Any]:
        raise NotImplementedError


class JsonRagGovernanceStore(RagGovernanceStore):
    def __init__(self, seed_dir: Path, store: JsonDocumentStore) -> None:
        self.seed_dir = seed_dir
        self.store = store

    async def list_chunks(self, *, review_status: str | None, limit: int, offset: int) -> dict[str, Any]:
        rows = self._chunks()
        if review_status:
            rows = [row for row in rows if row.get("review_status") == review_status]
        return {"items": rows[offset : offset + limit], "total": len(rows), "backend": "json_seed"}

    async def update_chunk(self, chunk_id: int, **kwargs) -> dict[str, Any]:
        data = self.store.load()
        states = data.setdefault("chunk_states", {})
        current = dict(states.get(str(chunk_id)) or {})
        before = dict(current)
        for key in ("enabled", "review_status", "quality_score", "disabled_reason"):
            if kwargs.get(key) is not None:
                current[key] = kwargs[key]
        current["last_reviewed_at"] = datetime.now(UTC).isoformat()
        states[str(chunk_id)] = current
        data.setdefault("audit_events", []).append(
            {
                "chunk_id": chunk_id,
                "action": "update_chunk",
                "actor_id": kwargs.get("actor_id"),
                "reason": kwargs.get("reason"),
                "before": before,
                "after": current,
                "created_at": current["last_reviewed_at"],
            }
        )
        self.store.save(data)
        return {"chunk_id": chunk_id, **current}

    async def stats(self) -> dict[str, Any]:
        rows = self._chunks()
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row["review_status"]] = by_status.get(row["review_status"], 0) + 1
        return {"total": len(rows), "by_review_status": by_status, "backend": "json_seed"}

    def _chunks(self) -> list[dict[str, Any]]:
        path = self.seed_dir / "knowledge_chunks.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        state_data = self.store.load().get("chunk_states", {})
        rows = []
        for index, item in enumerate(raw, start=1):
            state = state_data.get(str(index), {})
            rows.append(
                {
                    "id": index,
                    "source": item.get("source"),
                    "title": item.get("title"),
                    "domain": item.get("domain"),
                    "species": item.get("species"),
                    "source_url": item.get("source_url"),
                    "enabled": state.get("enabled", item.get("enabled", True)),
                    "review_status": state.get("review_status", item.get("review_status", "approved")),
                    "quality_score": state.get("quality_score", item.get("quality_score", 0.8)),
                    "disabled_reason": state.get("disabled_reason"),
                    "content_preview": str(item.get("content") or "")[:240],
                }
            )
        return rows


class PostgresRagGovernanceStore(RagGovernanceStore):
    def __init__(self, database_url: str) -> None:
        self.session_factory = make_session_factory(database_url)

    async def list_chunks(self, *, review_status: str | None, limit: int, offset: int) -> dict[str, Any]:
        filters = []
        if review_status:
            filters.append(KnowledgeChunkModel.review_status == review_status)
        with self.session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(KnowledgeChunkModel).where(*filters)) or 0)
            rows = session.scalars(
                select(KnowledgeChunkModel)
                .where(*filters)
                .order_by(KnowledgeChunkModel.id)
                .offset(offset)
                .limit(limit)
            ).all()
        return {"items": [self._chunk_dict(row) for row in rows], "total": total, "backend": "postgres"}

    async def update_chunk(self, chunk_id: int, **kwargs) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            row = session.scalar(select(KnowledgeChunkModel).where(KnowledgeChunkModel.id == chunk_id))
            if row is None:
                raise KeyError("knowledge chunk not found")
            before = self._chunk_dict(row)
            values: dict[str, Any] = {"updated_at": now, "last_reviewed_at": now}
            for key in ("enabled", "review_status", "quality_score", "disabled_reason"):
                if kwargs.get(key) is not None:
                    values[key] = kwargs[key]
            session.execute(update(KnowledgeChunkModel).where(KnowledgeChunkModel.id == chunk_id).values(**values))
            after_row = session.scalar(select(KnowledgeChunkModel).where(KnowledgeChunkModel.id == chunk_id))
            after = self._chunk_dict(after_row)
            session.add(
                RagAuditEventModel(
                    chunk_id=chunk_id,
                    action="update_chunk",
                    actor_id=kwargs.get("actor_id"),
                    reason=kwargs.get("reason"),
                    before=before,
                    after=after,
                )
            )
        return after

    async def stats(self) -> dict[str, Any]:
        with self.session_factory() as session:
            rows = session.execute(
                select(KnowledgeChunkModel.review_status, func.count()).group_by(KnowledgeChunkModel.review_status)
            ).all()
            total = int(session.scalar(select(func.count()).select_from(KnowledgeChunkModel)) or 0)
            enabled = int(session.scalar(select(func.count()).select_from(KnowledgeChunkModel).where(KnowledgeChunkModel.enabled.is_(True))) or 0)
        return {
            "total": total,
            "enabled": enabled,
            "by_review_status": {status: int(count) for status, count in rows},
            "backend": "postgres",
        }

    def _chunk_dict(self, row: KnowledgeChunkModel | None) -> dict[str, Any]:
        if row is None:
            return {}
        return {
            "id": row.id,
            "source": row.source,
            "title": row.title,
            "domain": row.domain,
            "species": row.species,
            "source_url": row.source_url,
            "enabled": row.enabled,
            "review_status": row.review_status,
            "quality_score": row.quality_score,
            "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
            "disabled_reason": row.disabled_reason,
            "ingestion_batch": row.ingestion_batch,
            "content_preview": row.content[:240],
            "metadata": row.metadata_json or {},
        }
