"""
=============================================================================
文件：src/vet_agent/answer_rag/retriever.py
作用：实现回答相关 RAG 的 PostgreSQL/pgvector 仓储与 LlamaIndex 检索适配。
范围：生产仓储只读取现有 knowledge_chunks 表中的已审核启用向量知识；
      LlamaIndex 仅用于标准 Node 封装和检索抽象，不创建第二套生产知识表。
说明：本文件是回答 RAG 的数据召回边界。无 embedding、无数据库、无合格
      向量命中或依赖异常均 Fail Fast，不回退文本相似度、seed 文件或默认知识。
=============================================================================
"""

from __future__ import annotations

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from vet_agent.db import KnowledgeChunkModel, make_session_factory
from vet_agent.repositories import KnowledgeHit
from vet_agent.runtime import EmbeddingClient

from .errors import AnswerRagDependencyError
from .models import AnswerRagRetrievalResult
from .ports import AnswerRagKnowledgeRepository, AnswerRagRetriever


class PostgresAnswerRagKnowledgeRepository(AnswerRagKnowledgeRepository):
    """通过 SQLAlchemy 读取 knowledge_chunks 的回答 RAG 生产仓储。

    :return: 无返回值；本实现是回答 RAG 的 PostgreSQL/pgvector 主路径。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL 回答 RAG 知识仓储。

        :param database_url: PostgreSQL 数据库连接串。
        :return: 无返回值。
        """
        self.database_url = database_url
        self.session_factory = make_session_factory(database_url)

    def retrieve_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
        domain: str | None,
    ) -> list[KnowledgeHit]:
        """根据 embedding 向量召回答案生成可用的已审核知识。

        :param query_embedding: 检索 query 的 embedding 向量。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与回答召回的知识 chunk 类型。
        :param domain: 可选任务域过滤条件；为空时不按领域过滤。
        :return: 返回已通过治理字段过滤的知识命中列表。
        :raises AnswerRagDependencyError: 数据库查询失败或 embedding 为空时抛出。
        """
        if not query_embedding:
            raise AnswerRagDependencyError(
                "answer RAG query embedding is empty",
                details={"reason": "empty_query_embedding"},
            )
        distance = KnowledgeChunkModel.embedding.cosine_distance(query_embedding)
        score = (1 - distance).label("score")
        statement = (
            select(KnowledgeChunkModel, score)
            .where(
                KnowledgeChunkModel.enabled.is_(True),
                KnowledgeChunkModel.review_status == "approved",
                KnowledgeChunkModel.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(max(1, limit))
        )
        if allowed_chunk_types:
            statement = statement.where(
                KnowledgeChunkModel.metadata_json["chunk_type"].astext.in_(allowed_chunk_types)
            )
        if domain:
            statement = statement.where(
                or_(
                    KnowledgeChunkModel.domain == domain,
                    KnowledgeChunkModel.domain == "general",
                    KnowledgeChunkModel.domain.is_(None),
                )
            )
        try:
            with self.session_factory() as session:
                rows = session.execute(statement).all()
        except Exception as exc:
            raise AnswerRagDependencyError(
                "answer RAG pgvector retrieval failed",
                details={"error_type": type(exc).__name__},
            ) from exc

        hits: list[KnowledgeHit] = []
        for chunk, score_value in rows:
            score_float = float(score_value or 0.0)
            if score_float < min_score:
                continue
            metadata = dict(chunk.metadata_json or {})
            metadata.setdefault("chunk_id", f"knowledge_chunk:{chunk.id}")
            metadata.setdefault("domain", chunk.domain)
            metadata.setdefault("species", chunk.species)
            metadata.setdefault("review_status", chunk.review_status)
            metadata.setdefault("enabled", chunk.enabled)
            hits.append(
                KnowledgeHit(
                    title=chunk.title,
                    summary=chunk.content,
                    source=chunk.source,
                    public_citation=bool(chunk.public_citation),
                    score=score_float,
                    source_url=chunk.source_url,
                    metadata=metadata,
                )
            )
        return hits

    def is_ready(self) -> bool:
        """检查仓储是否存在可用于回答 RAG 的已审核向量知识。

        :return: 存在启用、已审核且有 embedding 的知识片段时返回 True。
        """
        try:
            with self.session_factory() as session:
                return session.scalar(
                    select(KnowledgeChunkModel.id)
                    .where(
                        KnowledgeChunkModel.enabled.is_(True),
                        KnowledgeChunkModel.review_status == "approved",
                        KnowledgeChunkModel.embedding.is_not(None),
                    )
                    .limit(1)
                ) is not None
        except SQLAlchemyError:
            return False


class LlamaIndexAnswerKnowledgeRetriever(AnswerRagRetriever):
    """将业务知识仓储结果封装为 LlamaIndex 节点的回答 RAG 检索器。

    :return: 无返回值；本实现不让 LlamaIndex 管理独立生产向量表。
    """

    def __init__(
        self,
        *,
        repository: AnswerRagKnowledgeRepository,
        embedding_client: EmbeddingClient,
    ) -> None:
        """初始化 LlamaIndex 回答 RAG 检索适配器。

        :param repository: 已审核知识仓储。
        :param embedding_client: 检索 query 的 embedding 客户端。
        :return: 无返回值。
        """
        self.repository = repository
        self.embedding_client = embedding_client

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
        domain: str | None,
    ) -> AnswerRagRetrievalResult:
        """执行回答 RAG 知识召回并封装 LlamaIndex 节点。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与回答召回的知识 chunk 类型。
        :param domain: 可选任务域过滤条件；为空时不按领域过滤。
        :return: 返回检索结果与审计摘要。
        :raises AnswerRagDependencyError: embedding 或仓储召回不可用时抛出。
        """
        if not query.strip():
            raise AnswerRagDependencyError(
                "answer RAG retrieval query is empty",
                details={"reason": "empty_query"},
            )
        if not self.embedding_client.available:
            raise AnswerRagDependencyError(
                "answer RAG embedding client is unavailable",
                details={"reason": "embedding_unavailable"},
            )
        try:
            embedding = self.embedding_client.embed(query)
            hits = self.repository.retrieve_by_embedding(
                embedding,
                limit=limit,
                min_score=min_score,
                allowed_chunk_types=allowed_chunk_types,
                domain=domain,
            )
        except AnswerRagDependencyError:
            raise
        except Exception as exc:
            raise AnswerRagDependencyError(
                "answer RAG retrieval failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        if not hits:
            raise AnswerRagDependencyError(
                "answer RAG retrieval returned no approved vector hits",
                details={
                    "reason": "no_approved_vector_hits",
                    "limit": limit,
                    "min_score": min_score,
                    "allowed_chunk_types": list(allowed_chunk_types),
                    "domain": domain,
                },
            )
        nodes = self._nodes_from_hits(hits)
        llamaindex_retriever = _PreloadedLlamaIndexRetriever(nodes)
        retrieved_nodes = llamaindex_retriever.retrieve(QueryBundle(query_str=query))
        return AnswerRagRetrievalResult(
            query=query,
            hits=self._hits_from_nodes(retrieved_nodes, hits),
            node_count=len(retrieved_nodes),
            backend="llamaindex_node_adapter_pgvector_knowledge_chunks",
            min_score=min_score,
            top_k=limit,
        )

    def is_ready(self) -> bool:
        """检查检索器是否具备回答 RAG 召回条件。

        :return: embedding 客户端和知识仓储均就绪时返回 True。
        """
        return self.embedding_client.available and self.repository.is_ready()

    def _nodes_from_hits(self, hits: list[KnowledgeHit]) -> list[NodeWithScore]:
        """将业务 KnowledgeHit 封装为 LlamaIndex 节点。

        :param hits: 已审核知识命中列表。
        :return: 返回 LlamaIndex 节点列表。
        """
        nodes: list[NodeWithScore] = []
        for index, hit in enumerate(hits):
            metadata = dict(hit.metadata or {})
            evidence_id = str(metadata.get("chunk_id") or f"answer_hit_{index + 1}")
            metadata.update(
                {
                    "title": hit.title,
                    "source": hit.source,
                    "source_url": hit.source_url,
                    "public_citation": hit.public_citation,
                    "score": hit.score,
                    "evidence_id": evidence_id,
                }
            )
            nodes.append(
                NodeWithScore(
                    node=TextNode(
                        id_=evidence_id,
                        text=hit.summary,
                        metadata=metadata,
                    ),
                    score=hit.score,
                )
            )
        return nodes

    def _hits_from_nodes(
        self,
        nodes: list[NodeWithScore],
        hits: list[KnowledgeHit],
    ) -> list[KnowledgeHit]:
        """按 LlamaIndex 节点顺序恢复业务 KnowledgeHit。

        :param nodes: LlamaIndex 检索节点。
        :param hits: 原始业务命中列表。
        :return: 返回与节点顺序一致的业务命中列表。
        """
        hit_by_id = {
            str(dict(hit.metadata or {}).get("chunk_id") or f"answer_hit_{index + 1}"): hit
            for index, hit in enumerate(hits)
        }
        ordered: list[KnowledgeHit] = []
        for node in nodes:
            node_id = str(node.node.node_id)
            hit = hit_by_id.get(node_id)
            if hit is not None:
                ordered.append(hit)
        return ordered or hits


class _PreloadedLlamaIndexRetriever(BaseRetriever):
    """基于已召回节点的 LlamaIndex 检索器适配层。

    :return: 无返回值；该内部类只包装业务仓储结果，不访问数据库。
    """

    def __init__(self, nodes: list[NodeWithScore]) -> None:
        """初始化预加载 LlamaIndex 节点检索器。

        :param nodes: 已由业务仓储召回并封装的节点。
        :return: 无返回值。
        """
        super().__init__()
        self.nodes = nodes

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        """返回已预加载的 LlamaIndex 节点。

        :param query_bundle: LlamaIndex 查询对象；本适配器仅保留接口契约。
        :return: 返回本轮业务仓储已召回节点。
        """
        del query_bundle
        return list(self.nodes)
