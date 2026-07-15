from __future__ import annotations

from src.vet_agent.contracts import Evidence
from src.vet_agent.repositories.knowledge import KnowledgeHit, KnowledgeRepository, evidence_from_hits


class KnowledgeService:
    """Grounding facade backed by PostgreSQL/pgvector or seed-file fallback."""

    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    async def retrieve(self, query: str) -> tuple[list[KnowledgeHit], list[Evidence]]:
        hits = self.repository.retrieve(query)
        return hits, evidence_from_hits(hits)

    def is_ready(self) -> bool:
        return self.repository.is_ready()
