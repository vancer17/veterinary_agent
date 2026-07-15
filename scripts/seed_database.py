from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vet_agent.config import Settings
from src.vet_agent.db.models import (
    ConsultationDomainModel,
    ConsultationSlotModel,
    KnowledgeChunkModel,
    SafetyRuleModel,
)
from src.vet_agent.db.session import make_session_factory
from src.vet_agent.runtime.embeddings import QwenEmbeddingClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PostgreSQL rule and RAG tables.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--seed-dir", default="data/seeds")
    parser.add_argument("--with-embeddings", action="store_true")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    embedding_client = QwenEmbeddingClient(Settings.from_env()) if args.with_embeddings else None
    seed_dir = Path(args.seed_dir)
    session_factory = make_session_factory(args.database_url)
    with session_factory() as session:
        seed_safety(session, seed_dir / "safety_rules.json")
        seed_consultation(session, seed_dir / "consultation_rules.json")
        seed_knowledge(session, seed_dir / "knowledge_chunks.json", embedding_client)
        session.commit()


def seed_safety(session, path: Path) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    for item in rows:
        for pattern in item.get("patterns", []):
            model = session.scalar(
                select(SafetyRuleModel).where(
                    SafetyRuleModel.code == item["code"],
                    SafetyRuleModel.rule_type == item["rule_type"],
                    SafetyRuleModel.match_type == item["match_type"],
                    SafetyRuleModel.pattern == pattern,
                )
            )
            if model is None:
                model = SafetyRuleModel(
                    code=item["code"],
                    rule_type=item["rule_type"],
                    match_type=item["match_type"],
                    pattern=pattern,
                )
                session.add(model)
            model.severity = item.get("severity", "caution")
            model.message = item["message"]
            model.response_template = item.get("response_template")
            model.metadata_json = item.get("metadata", {})


def seed_consultation(session, path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw.get("domains", []):
        model = session.get(ConsultationDomainModel, item["domain"])
        if model is None:
            model = ConsultationDomainModel(domain=item["domain"])
            session.add(model)
        model.required_slots = item.get("required_slots", [])
        model.classifier_keywords = item.get("classifier_keywords", [])
        model.priority = int(item.get("priority", 100))
    for item in raw.get("slots", []):
        model = session.get(ConsultationSlotModel, item["slot_name"])
        if model is None:
            model = ConsultationSlotModel(slot_name=item["slot_name"])
            session.add(model)
        model.question = item["question"]
        model.label = item["label"]
        model.extraction_rules = item.get("extraction_rules", [])
        model.priority = int(item.get("priority", 100))


def seed_knowledge(session, path: Path, embedding_client: QwenEmbeddingClient | None) -> None:
    rows = json.loads(path.read_text(encoding="utf-8"))
    for item in rows:
        embedding = embedding_client.embed(item["content"]) if embedding_client else None
        model = session.scalar(
            select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.source == item["source"],
                KnowledgeChunkModel.title == item["title"],
            )
        )
        if model is None:
            model = KnowledgeChunkModel(source=item["source"], title=item["title"], content=item["content"])
            session.add(model)
        model.content = item["content"]
        model.embedding = embedding
        model.public_citation = bool(item.get("public_citation", True))
        model.copyright_risk = item.get("copyright_risk", "low")
        model.domain = item.get("domain")
        model.species = item.get("species")
        model.source_url = item.get("source_url")
        model.metadata_json = item.get("metadata", {})


if __name__ == "__main__":
    main()
