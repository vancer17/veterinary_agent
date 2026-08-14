"""
=============================================================================
文件：src/vet_agent/followup_rag/ports.py
作用：定义追问相关 RAG 领域的鸭子类型协议。
范围：隔离数据库仓储、LlamaIndex 检索适配、结构化追问规划与业务服务；
      业务编排层只依赖本文件协议，不直接操作 SQLAlchemy 表模型。
说明：实现类应显式继承对应 Protocol，以便追溯调用栈并区分生产实现与
      测试替身。协议不承载具体检索、模型调用或状态写入逻辑。
=============================================================================
"""

from __future__ import annotations

from typing import Protocol

from vet_agent.repositories import KnowledgeHit

from .models import FollowupRagPlan, FollowupRagRequest, FollowupRagRetrievalResult
from .planner import FollowupQuestionPlannerOutput


class FollowupRagKnowledgeRepository(Protocol):
    """定义追问相关 RAG 的已审核知识仓储协议。

    :return: 无返回值；生产实现通过 SQLAlchemy 读取 knowledge_chunks。
    """

    def retrieve_by_embedding(
        self,
        query_embedding: list[float],
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
    ) -> list[KnowledgeHit]:
        """根据 embedding 向量召回已审核追问知识。

        :param query_embedding: 检索 query 的 embedding 向量。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与追问召回的知识 chunk 类型。
        :return: 返回已通过治理字段过滤的知识命中列表。
        """
        ...

    def is_ready(self) -> bool:
        """检查仓储是否具备线上追问 RAG 召回条件。

        :return: 存在可用的已审核向量知识时返回 True。
        """
        ...


class FollowupRagRetriever(Protocol):
    """定义追问相关 RAG 的 LlamaIndex 检索适配协议。

    :return: 无返回值；该协议隐藏 LlamaIndex 节点封装细节。
    """

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        min_score: float,
        allowed_chunk_types: tuple[str, ...],
    ) -> FollowupRagRetrievalResult:
        """执行追问 RAG 知识召回。

        :param query: 结构化检索查询。
        :param limit: 返回数量上限。
        :param min_score: 最低相似度阈值。
        :param allowed_chunk_types: 允许参与追问召回的知识 chunk 类型。
        :return: 返回检索结果与审计摘要。
        """
        ...

    def is_ready(self) -> bool:
        """检查检索适配器是否就绪。

        :return: embedding 客户端和知识仓储均就绪时返回 True。
        """
        ...


class FollowupQuestionPlanner(Protocol):
    """定义追问相关 RAG 的结构化问题规划协议。

    :return: 无返回值；生产实现通过 LiteLLM response_format 输出 Pydantic 对象。
    """

    async def generate(
        self,
        *,
        request: FollowupRagRequest,
        retrieval: FollowupRagRetrievalResult,
    ) -> FollowupQuestionPlannerOutput:
        """基于已审核知识证据生成结构化追问计划候选。

        :param request: 追问 RAG 结构化请求。
        :param retrieval: 本轮已审核知识召回结果。
        :return: 返回结构化追问计划候选。
        """
        ...

    def is_ready(self) -> bool:
        """检查结构化追问规划器是否就绪。

        :return: 模型客户端具备结构化调用条件时返回 True。
        """
        ...


class FollowupRagServiceProtocol(Protocol):
    """定义追问相关 RAG 服务协议。

    :return: 无返回值；主编排器通过该协议隔离检索和结构化模型实现。
    """

    async def plan(self, request: FollowupRagRequest) -> FollowupRagPlan:
        """在 OPA ask 裁决后生成证据驱动的追问计划。

        :param request: 追问 RAG 结构化请求。
        :return: 返回已通过契约校验的追问计划。
        """
        ...

    def is_ready(self) -> bool:
        """检查追问 RAG 服务是否就绪。

        :return: 召回器和结构化规划器均就绪时返回 True。
        """
        ...
