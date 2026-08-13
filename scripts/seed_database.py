"""
文件：scripts/seed_database.py
作用：提供 PostgreSQL 初始化与离线种子数据写入能力。
说明：本脚本只负责将标准 seed、知识片段与临床安全资产同步到数据库，不承载在线业务逻辑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vet_agent import Settings
from vet_agent.clinical_safety import FileClinicalSafetyRepository, validate_clinical_safety_publish_contract
from vet_agent.db import (
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    ConsultationDomainModel,
    ConsultationSlotModel,
    KnowledgeChunkModel,
    make_session_factory,
)
from vet_agent.runtime import QwenEmbeddingClient


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    :return: 返回命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="Seed PostgreSQL rule, knowledge and clinical safety tables.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--seed-dir", default="assets/seeds")
    parser.add_argument("--clinical-safety-dir", default="assets/clinical_safety")
    parser.add_argument("--with-embeddings", action="store_true")
    parser.add_argument(
        "--clinical-safety-review-status",
        default="pending",
        choices=("approved", "pending"),
    )
    parser.add_argument(
        "--clinical-safety-dry-run",
        action="store_true",
        help="只执行临床安全发布态严格契约校验，不写入数据库。",
    )
    parser.add_argument(
        "--allow-missing-clinical-safety-embeddings",
        action="store_true",
        help="发布态严格校验时允许 chunk 暂缺 embedding 元信息；仅开发 dry-run 使用。",
    )
    return parser.parse_args()


def main() -> None:
    """执行命令行入口逻辑。

    :return: 无返回值。
    """
    args = parse_args()
    clinical_safety_dir = Path(args.clinical_safety_dir)
    if args.clinical_safety_dry_run:
        validate_clinical_safety_assets_for_publish(
            clinical_safety_dir,
            require_embeddings=not args.allow_missing_clinical_safety_embeddings,
        )
        return
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    settings = Settings.from_env()
    embedding_client = QwenEmbeddingClient(settings) if args.with_embeddings else None
    seed_dir = Path(args.seed_dir)
    session_factory = make_session_factory(args.database_url)
    with session_factory() as session:
        seed_consultation(session, seed_dir / "consultation_rules.json")
        seed_knowledge(session, seed_dir / "knowledge_chunks.json", embedding_client)
        seed_clinical_safety(
            session,
            clinical_safety_dir,
            embedding_client,
            embedding_model=settings.qwen_embedding_model,
            review_status=args.clinical_safety_review_status,
            require_publish_embeddings=not args.allow_missing_clinical_safety_embeddings,
        )
        session.commit()


def seed_consultation(session: Session, path: Path) -> None:
    """写入问诊分流 seed。

    :param session: 数据库会话。
    :param path: 问诊规则 JSON 文件路径。
    :return: 无返回值。
    """
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


def seed_knowledge(
    session: Session,
    path: Path,
    embedding_client: QwenEmbeddingClient | None,
) -> None:
    """写入知识库 seed。

    :param session: 数据库会话。
    :param path: 知识 chunk JSON 文件路径。
    :param embedding_client: 可选的 embedding 客户端。
    :return: 无返回值。
    """
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


def seed_clinical_safety(
    session: Session,
    asset_dir: Path,
    embedding_client: QwenEmbeddingClient | None,
    *,
    embedding_model: str,
    review_status: str = "approved",
    require_publish_embeddings: bool = True,
) -> None:
    """写入临床安全独立表。

    :param session: 数据库会话。
    :param asset_dir: 标准临床安全资产目录。
    :param embedding_client: 可选的 embedding 客户端。
    :param embedding_model: 生成向量时使用的模型名称。
    :param review_status: 写入数据库时采用的审核状态。
    :param require_publish_embeddings: 发布态导入是否要求静态 chunk 已具备 embedding 元信息。
    :return: 无返回值。
    """
    if review_status == "approved":
        validate_clinical_safety_assets_for_publish(
            asset_dir,
            require_embeddings=require_publish_embeddings
            and not (embedding_client is not None and embedding_client.available),
        )
    repository = FileClinicalSafetyRepository(asset_dir)
    assets = repository.assets(published_only=False)
    chunks = repository.chunks(published_only=False)
    now = datetime.now(UTC)
    approved = review_status == "approved"

    for asset in assets:
        model = session.get(ClinicalSafetyAssetModel, asset.asset_id)
        if model is None:
            model = ClinicalSafetyAssetModel(asset_id=asset.asset_id)
            session.add(model)
        model.code = asset.code
        model.asset_type = asset.asset_type
        model.canonical_name = asset.canonical_name
        model.category = asset.category
        model.species_scope = list(asset.species_scope)
        model.sex_scope = list(asset.sex_scope)
        model.age_scope = list(asset.age_scope)
        model.severity = asset.severity
        model.action_class = asset.action_class
        model.aliases = list(asset.aliases)
        model.carriers = list(asset.carriers)
        model.user_expressions = list(asset.user_expressions)
        model.symptoms = list(asset.symptoms)
        model.recognition_phrases = list(asset.recognition_phrases)
        model.required_context = {key: list(value) for key, value in asset.required_context.items()}
        model.decision_hints = dict(asset.decision_hints)
        model.clinical_risk_summary = asset.clinical_risk_summary
        model.triage_message = asset.triage_message
        model.source = dict(asset.source)
        model.raw_text = dict(asset.raw_text)
        model.version = asset.version
        model.enabled = approved
        model.review_status = review_status
        model.published_at = now if approved else None
        model.metadata_json = dict(asset.metadata)

    for chunk in chunks:
        model = session.get(ClinicalSafetyChunkModel, chunk.chunk_id)
        if model is None:
            model = ClinicalSafetyChunkModel(chunk_id=chunk.chunk_id, asset_id=chunk.asset_id)
            session.add(model)
        model.asset_id = chunk.asset_id
        model.chunk_type = chunk.chunk_type
        model.title = chunk.title
        model.embedding_text = chunk.embedding_text
        model.enabled = approved and chunk.enabled
        model.review_status = review_status
        model.version = chunk.version
        model.metadata_json = dict(chunk.metadata)
        model.content_hash = chunk.content_hash or _content_hash(chunk.embedding_text)
        if embedding_client is not None and embedding_client.available:
            try:
                embedding = embedding_client.embed(chunk.embedding_text)
            except Exception as exc:
                if approved:
                    raise RuntimeError(
                        f"clinical safety published chunk embedding failed: {chunk.chunk_id}"
                    ) from exc
                embedding = None
            if approved and not embedding:
                raise RuntimeError(f"clinical safety published chunk embedding is empty: {chunk.chunk_id}")
            model.embedding = embedding
            model.embedding_model = embedding_model if embedding else None
            model.embedding_dimension = len(embedding) if embedding else None
        else:
            if approved:
                raise RuntimeError("clinical safety approved import requires an available embedding client")
            model.embedding = None
            model.embedding_model = None
            model.embedding_dimension = None


def validate_clinical_safety_assets_for_publish(
    asset_dir: Path,
    *,
    require_embeddings: bool = True,
) -> None:
    """执行临床安全静态资产发布态严格契约 dry-run。

    :param asset_dir: 标准临床安全资产目录。
    :param require_embeddings: 是否要求发布态 chunk 已具备 embedding 元信息。
    :return: 无返回值；校验通过表示资产可以进入发布态导入流程。
    """
    asset_document = json.loads((asset_dir / "vet_safety_assets.v1.json").read_text(encoding="utf-8"))
    chunk_document = json.loads((asset_dir / "vet_safety_chunks.v1.json").read_text(encoding="utf-8"))
    validate_clinical_safety_publish_contract(
        asset_document,
        chunk_document,
        require_embeddings=require_embeddings,
    )


def _content_hash(text: str) -> str:
    """生成 chunk 内容哈希。

    :param text: 待哈希的 chunk 文本。
    :return: 返回十六进制摘要。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
