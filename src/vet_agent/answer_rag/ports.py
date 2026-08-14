"""
=============================================================================
文件：src/vet_agent/answer_rag/ports.py
作用：定义回答相关 RAG 领域的鸭子类型协议。
范围：隔离 PostgreSQL 知识仓储、LlamaIndex 检索适配与业务服务；
      业务编排层只依赖本文件协议，不直接操作 SQLAlchemy 表模型。
说明：实现类应显式继承对应 Protocol，以便追溯调用栈并区分生产实现与测试替身。
=============================================================================
"""

from __future__ import annotations

from typing import Protocol

from vet_agent.repositories import KnowledgeHit

from .models import AnswerRagRequest, AnswerRagResult, AnswerRagRetrievalResult


class AnswerRagKnowledgeRepository(Protocol):
    """定义回答相关 RAG 的已审核知识仓储协议。

    :return: 无返回值；生产实现通过 SQLAlchemy 读取 knowledge_chunks。
    """

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
        """
        ...

    def is_ready(self) -> bool:
        """检查仓储是否具备线上回答 RAG 召回条件。

        :return: 存在可用的已审核向量知识时返回 True。
        """
        ...


class AnswerRagRetriever(Protocol):
    """定义回答相关 RAG 的 LlamaIndex 检索适配协议。

    :return: 无返回值；该协议隐藏 LlamaIndex 节点封装细节。
    """

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
        domain: str | None,
    ) -> AnswerRagRetrievalResult:
        """执行回答 RAG 知识召回。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与回答召回的知识 chunk 类型。
        :param domain: 可选任务域过滤条件；为空时不按领域过滤。
        :return: 返回检索结果与审计摘要。
        """
        ...

    def is_ready(self) -> bool:
        """检查检索适配器是否就绪。

        :return: embedding 客户端和知识仓储均就绪时返回 True。
        """
        ...


class AnswerRagServiceProtocol(Protocol):
    """定义回答相关 RAG 服务协议。

    :return: 无返回值；主编排器通过该协议隔离检索实现和数据库实现。
    """

    async def retrieve(self, request: AnswerRagRequest) -> AnswerRagResult:
        """在 OPA answer 裁决后生成证据驱动的回答上下文。

        :param request: 回答 RAG 结构化请求。
        :return: 返回已通过契约校验的回答 RAG 证据上下文。
        """
        ...

    def is_ready(self) -> bool:
        """检查回答 RAG 服务是否就绪。

        :return: 检索器具备生产召回条件时返回 True。
        """
        ...
