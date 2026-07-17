"""
文件：scripts/import_knowledge_dir.py
作用：提供开发、导入与初始化脚本。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vet_agent import Settings
from vet_agent.db import KnowledgeChunkModel, make_session_factory
from vet_agent.runtime import QwenEmbeddingClient


def main() -> None:
    """执行命令行入口逻辑。

    :return: 返回函数执行结果。
    """
    parser = argparse.ArgumentParser(description="Import local veterinary RAG documents into knowledge_chunks.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--source-dir", default="rag_sources")
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--species", default=None)
    parser.add_argument("--public-citation", default="true", choices=["true", "false"])
    parser.add_argument("--copyright-risk", default="low")
    parser.add_argument("--chunk-chars", type=int, default=1200)
    parser.add_argument("--with-embeddings", action="store_true")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    source_dir = Path(args.source_dir)
    files = [path for path in source_dir.rglob("*") if path.suffix.lower() in {".txt", ".md"}]
    if not files:
        raise SystemExit(f"No .txt/.md files found under {source_dir}")

    embedding_client = QwenEmbeddingClient(Settings.from_env()) if args.with_embeddings else None
    session_factory = make_session_factory(args.database_url)
    with session_factory() as session:
        for path in files:
            text = path.read_text(encoding="utf-8")
            for index, chunk in enumerate(chunk_text(text, args.chunk_chars), start=1):
                embedding = embedding_client.embed(chunk) if embedding_client else None
                session.add(
                    KnowledgeChunkModel(
                        source=args.source,
                        title=f"{path.stem} #{index}",
                        content=chunk,
                        embedding=embedding,
                        public_citation=args.public_citation == "true",
                        copyright_risk=args.copyright_risk,
                        domain=args.domain,
                        species=args.species,
                        source_url=args.source_url,
                        metadata_json={"file": str(path), "chunk_index": index},
                    )
                )
        session.commit()


def chunk_text(text: str, chunk_chars: int) -> list[str]:
    """执行 chunk_text 业务逻辑。

    :param text: 待处理文本。
    :param chunk_chars: 参数 chunk_chars。
    :return: 返回函数执行结果。
    """
    clean = re.sub(r"\n{3,}", "\n\n", text.strip())
    paragraphs = [item.strip() for item in clean.split("\n\n") if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


if __name__ == "__main__":
    main()
