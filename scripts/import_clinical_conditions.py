"""
文件：scripts/import_clinical_conditions.py
作用：通过统一服务导入结构化临床病症卡，便于生产服务器首次初始化或运维批量导入。
说明：该脚本复用 ClinicalKnowledgeService，不绕过 SQLAlchemy、Alembic 与 RAG 治理链路。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vet_agent import Settings
from vet_agent.runtime import QwenEmbeddingClient
from vet_agent.services import ClinicalKnowledgeService, PostgresClinicalKnowledgeStore


async def main_async() -> None:
    """执行异步命令行入口逻辑。

    :return: 返回异步执行结果。
    """
    parser = argparse.ArgumentParser(description="Import structured veterinary condition cards.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--path", default="tmp/feature/data/vet_conditions.json")
    parser.add_argument("--source", default="common_conditions_handbook")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--actor-id", default="cli")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--with-embeddings", action="store_true")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    settings = Settings.from_env()
    embedding_client = QwenEmbeddingClient(settings) if args.with_embeddings else None
    service = ClinicalKnowledgeService(
        PostgresClinicalKnowledgeStore(args.database_url),
        embedding_client=embedding_client,
    )
    result = await service.import_conditions_from_file(
        Path(args.path),
        source=args.source,
        version=args.version,
        actor_id=args.actor_id,
        publish=args.publish,
    )
    print(result)


def main() -> None:
    """执行命令行入口逻辑。

    :return: 无返回值。
    """
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
