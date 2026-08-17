"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/ports.py
作用：定义 RAG 无命中治理链路的鸭子类型协议。
范围：隔离 Agent 编排器、治理服务和数据库仓储；业务层仅依赖协议，不直接
      操作 SQLAlchemy 数据表模型。
说明：协议方法只记录治理事件，不返回可用于回答生成的知识证据。
=============================================================================
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import (
    RagMissRecord,
    RagMissRecordDraft,
    RagMissRecordRequest,
    RagMissRecordView,
    RagMissScope,
    RagMissStatus,
)


class RagMissRecorderProtocol(Protocol):
    """定义 Agent 编排器依赖的 RAG 无命中治理记录器协议。

    :return: 无返回值；该协议位于运行时 Fail Fast 与离线治理之间。
    """

    async def record_miss(self, request: RagMissRecordRequest) -> RagMissRecord | None:
        """记录一次 RAG 无命中治理事件。

        :param request: RAG 无命中治理请求。
        :return: 返回已持久化记录摘要；记录器禁用时返回 None。
        """
        ...

    def is_ready(self) -> bool:
        """检查治理记录器是否可用。

        :return: 可记录或显式禁用时返回 True。
        """
        ...


class RagMissRepositoryProtocol(Protocol):
    """定义 RAG 无命中治理仓储协议。

    :return: 无返回值；该协议是数据库表模型与业务服务之间的唯一访问边界。
    """

    def record_miss(self, draft: RagMissRecordDraft) -> RagMissRecord:
        """持久化一条 RAG 无命中治理记录。

        :param draft: 已经标准化的治理记录草稿。
        :return: 返回持久化后的治理记录摘要。
        """
        ...

    def list_misses(
        self,
        *,
        rag_scope: RagMissScope | None,
        status: RagMissStatus | None,
        task_domain: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RagMissRecordView, ...], int]:
        """分页读取 RAG 无命中治理记录。

        :param rag_scope: 可选 RAG 数据链范围过滤条件。
        :param status: 可选治理状态过滤条件。
        :param task_domain: 可选任务域过滤条件。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回治理记录详情元组与总数。
        """
        ...

    def update_miss(
        self,
        miss_id: str,
        *,
        status: RagMissStatus | None,
        review_notes: str | None,
        linked_ingestion_batch: str | None,
        linked_chunk_ids: tuple[int, ...] | None,
        actor_id: str | None,
        reason: str | None,
    ) -> RagMissRecordView:
        """更新一条 RAG 无命中治理记录的人工治理字段。

        :param miss_id: 治理记录稳定标识。
        :param status: 新治理状态。
        :param review_notes: 治理备注。
        :param linked_ingestion_batch: 关联知识导入批次标识。
        :param linked_chunk_ids: 关联正式知识 chunk 内部主键集合。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回更新后的治理记录详情。
        """
        ...

    def is_ready(self) -> bool:
        """检查治理仓储是否可访问。

        :return: 数据仓储可用时返回 True。
        """
        ...


class RagMissGovernanceProtocol(RagMissRecorderProtocol, Protocol):
    """定义 RAG 无命中治理服务的完整管理协议。

    :return: 无返回值；该协议覆盖运行时记录和管理端治理查询更新能力。
    """

    async def list_misses(
        self,
        *,
        rag_scope: str | None = None,
        status: str | None = None,
        task_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页读取 RAG 无命中治理记录。

        :param rag_scope: 可选 RAG 数据链范围过滤条件。
        :param status: 可选治理状态过滤条件。
        :param task_domain: 可选任务域过滤条件。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回治理记录分页结果。
        """
        ...

    async def update_miss(
        self,
        miss_id: str,
        *,
        status: str | None = None,
        review_notes: str | None = None,
        linked_ingestion_batch: str | None = None,
        linked_chunk_ids: tuple[int, ...] | None = None,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """更新一条 RAG 无命中治理记录。

        :param miss_id: 治理记录稳定标识。
        :param status: 新治理状态。
        :param review_notes: 治理备注。
        :param linked_ingestion_batch: 关联知识导入批次标识。
        :param linked_chunk_ids: 关联正式知识 chunk 内部主键集合。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回更新后的治理记录详情。
        """
        ...
