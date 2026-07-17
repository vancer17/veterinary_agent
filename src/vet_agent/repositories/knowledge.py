"""
文件：src/vet_agent/repositories/knowledge.py
作用：提供规则库与 RAG 知识库的数据访问能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import desc, func, literal, or_, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent import Evidence
from vet_agent.db import KnowledgeChunkModel, make_session_factory


@dataclass(frozen=True)
class KnowledgeHit:
    title: str
    summary: str
    source: str
    public_citation: bool
    score: float = 0.0
    source_url: str | None = None


class KnowledgeRepository(Protocol):
    def retrieve(self, query: str, limit: int = 4) -> list[KnowledgeHit]:
        """检索与查询相关的知识片段。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        ...

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        ...


class FileKnowledgeRepository:
    def __init__(self, seed_dir: Path) -> None:
        """初始化当前对象。

        :param seed_dir: 参数 seed_dir。
        :return: 无返回值。
        """
        self.seed_dir = seed_dir

    def retrieve(self, query: str, limit: int = 4) -> list[KnowledgeHit]:
        """检索与查询相关的知识片段。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        chunks = self._load()
        scored: list[tuple[float, dict]] = []
        for item in chunks:
            score = self._score(query, f"{item.get('title', '')}\n{item.get('content', '')}")
            if score > 0:
                scored.append((score, item))
        if not scored:
            scored = [(0.0, item) for item in chunks[:limit]]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            KnowledgeHit(
                title=item["title"],
                summary=item["content"],
                source=item["source"],
                public_citation=bool(item.get("public_citation", True)),
                score=score,
                source_url=item.get("source_url"),
            )
            for score, item in scored[:limit]
        ]

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return (self.seed_dir / "knowledge_chunks.json").exists()

    def _load(self) -> list[dict]:
        """执行 _load 内部辅助逻辑。

        :return: 返回函数执行结果。
        """
        return json.loads((self.seed_dir / "knowledge_chunks.json").read_text(encoding="utf-8"))

    def _score(self, query: str, text: str) -> float:
        """执行 _score 内部辅助逻辑。

        :param query: 检索查询。
        :param text: 待处理文本。
        :return: 返回函数执行结果。
        """
        if not query.strip():
            return 0.0
        query_chars = set(query.lower())
        text_lower = text.lower()
        return float(sum(1 for char in query_chars if char.strip() and char in text_lower))


class PostgresKnowledgeRepository:
    def __init__(self, database_url: str, embedding_client=None) -> None:
        """初始化当前对象。

        :param database_url: 数据库连接地址。
        :param embedding_client: 参数 embedding_client。
        :return: 无返回值。
        """
        self.database_url = database_url
        self.embedding_client = embedding_client
        self.session_factory = make_session_factory(database_url)

    def retrieve(self, query: str, limit: int = 4) -> list[KnowledgeHit]:
        """检索与查询相关的知识片段。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        if self.embedding_client is not None:
            try:
                hits = self._retrieve_by_vector(query, limit)
                if hits:
                    return hits
            except Exception:
                pass

        return self._retrieve_by_text(query, limit)

    def _retrieve_by_vector(self, query: str, limit: int) -> list[KnowledgeHit]:
        """执行 _retrieve_by_vector 内部辅助逻辑。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        embedding = self.embedding_client.embed(query)
        distance = KnowledgeChunkModel.embedding.cosine_distance(embedding)
        score = (1 - distance).label("score")
        statement = (
            select(KnowledgeChunkModel, score)
            .where(
                KnowledgeChunkModel.enabled.is_(True),
                KnowledgeChunkModel.review_status == "approved",
                KnowledgeChunkModel.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        with self.session_factory() as session:
            rows = session.execute(statement).all()
        return [
            KnowledgeHit(
                title=chunk.title,
                summary=chunk.content,
                source=chunk.source,
                public_citation=bool(chunk.public_citation),
                score=float(score_value or 0.0),
                source_url=chunk.source_url,
            )
            for chunk, score_value in rows
        ]

    def _retrieve_by_text(self, query: str, limit: int) -> list[KnowledgeHit]:
        """执行 _retrieve_by_text 内部辅助逻辑。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        query_literal = literal(query)
        like_literal = literal(f"%{query}%")
        title_similarity = func.similarity(func.lower(KnowledgeChunkModel.title), func.lower(query_literal))
        content_similarity = func.similarity(func.lower(KnowledgeChunkModel.content), func.lower(query_literal))
        score = func.greatest(title_similarity, content_similarity).label("score")
        statement = (
            select(KnowledgeChunkModel, score)
            .where(
                KnowledgeChunkModel.enabled.is_(True),
                KnowledgeChunkModel.review_status == "approved",
                or_(
                    func.lower(KnowledgeChunkModel.title).like(func.lower(like_literal)),
                    func.lower(KnowledgeChunkModel.content).like(func.lower(like_literal)),
                    title_similarity > 0.05,
                    content_similarity > 0.05,
                ),
            )
            .order_by(desc(score), desc(KnowledgeChunkModel.id))
            .limit(limit)
        )
        with self.session_factory() as session:
            rows = session.execute(statement).all()
            if not rows:
                rows = session.execute(
                    select(KnowledgeChunkModel, literal(0.0).label("score"))
                    .where(KnowledgeChunkModel.enabled.is_(True), KnowledgeChunkModel.review_status == "approved")
                    .order_by(KnowledgeChunkModel.id)
                    .limit(limit)
                ).all()
        return [
            KnowledgeHit(
                title=chunk.title,
                summary=chunk.content,
                source=chunk.source,
                public_citation=bool(chunk.public_citation),
                score=float(score_value or 0.0),
                source_url=chunk.source_url,
            )
            for chunk, score_value in rows
        ]

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        try:
            with self.session_factory() as session:
                return session.scalar(
                    select(KnowledgeChunkModel.id)
                    .where(KnowledgeChunkModel.enabled.is_(True), KnowledgeChunkModel.review_status == "approved")
                    .limit(1)
                ) is not None
        except SQLAlchemyError:
            return False


class FallbackKnowledgeRepository:
    def __init__(self, primary: KnowledgeRepository, fallback: KnowledgeRepository) -> None:
        """初始化当前对象。

        :param primary: 参数 primary。
        :param fallback: 参数 fallback。
        :return: 无返回值。
        """
        self.primary = primary
        self.fallback = fallback

    def retrieve(self, query: str, limit: int = 4) -> list[KnowledgeHit]:
        """检索与查询相关的知识片段。

        :param query: 检索查询。
        :param limit: 返回数量上限。
        :return: 返回函数执行结果。
        """
        try:
            hits = self.primary.retrieve(query, limit)
            return hits or self.fallback.retrieve(query, limit)
        except Exception:
            return self.fallback.retrieve(query, limit)

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return self.primary.is_ready() or self.fallback.is_ready()


def evidence_from_hits(hits: list[KnowledgeHit]) -> list[Evidence]:
    """执行 evidence_from_hits 业务逻辑。

    :param hits: 命中的知识片段列表。
    :return: 返回函数执行结果。
    """
    return [
        Evidence(
            source=hit.source,
            detail=hit.summary,
            public_citation=hit.public_citation,
            metadata={"score": hit.score, "title": hit.title, "source_url": hit.source_url, "type": "knowledge"},
        )
        for hit in hits
    ]
