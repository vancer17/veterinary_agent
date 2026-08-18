"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/postgres_repository.py
作用：实现 RAG 无命中治理记录的 PostgreSQL 仓储。
范围：仅本文件访问 rag_retrieval_misses SQLAlchemy 表模型；业务服务通过
      RagMissRepositoryProtocol 写入治理记录，不直接操作数据库模型。
说明：该仓储保存知识缺口审计材料，不提供检索、发布、审核或回答回退能力。
=============================================================================
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ColumnElement

from vet_agent.db import RagRetrievalMissModel, make_session_factory

from .errors import RagMissGovernanceError
from .models import (
    RagMissRecord,
    RagMissRecordDraft,
    RagMissRecordView,
    RagMissScope,
    RagMissStatus,
)
from .ports import RagMissRepositoryProtocol


class PostgresRagMissRepository(RagMissRepositoryProtocol):
    """通过 PostgreSQL 持久化 RAG 无命中治理记录。

    :return: 无返回值；该实现是生产治理记录的数据库边界。
    """

    def __init__(self, database_url: str) -> None:
        """初始化 PostgreSQL RAG 无命中治理仓储。

        :param database_url: PostgreSQL 数据库连接串。
        :return: 无返回值。
        """
        self.session_factory = make_session_factory(database_url)

    def record_miss(self, draft: RagMissRecordDraft) -> RagMissRecord:
        """写入一条 RAG 无命中治理记录。

        :param draft: 已经标准化的治理记录草稿。
        :return: 返回持久化后的治理记录摘要。
        :raises RagMissGovernanceError: 数据库写入失败时抛出。
        """
        try:
            with self.session_factory.begin() as session:
                row = RagRetrievalMissModel(
                    miss_id=draft.miss_id,
                    request_id=draft.request_id,
                    trace_id=draft.trace_id,
                    user_id=draft.user_id,
                    pet_id=draft.pet_id,
                    session_id=draft.session_id,
                    rag_scope=draft.rag_scope.value,
                    task_id=draft.task_id,
                    task_key=draft.task_key,
                    task_domain=draft.task_domain,
                    task_title=draft.task_title,
                    user_text_excerpt=draft.user_text_excerpt,
                    user_text_digest=draft.user_text_digest,
                    structured_query=draft.structured_query,
                    consultation_state=draft.consultation_state,
                    answerability=draft.answerability,
                    semantic_extraction=draft.semantic_extraction,
                    retrieval_parameters=draft.retrieval_parameters,
                    failure_reason=draft.failure_reason,
                    error_type=draft.error_type,
                    error_message=draft.error_message,
                    error_details=draft.error_details,
                    dedupe_key=draft.dedupe_key,
                    status=draft.status.value,
                    metadata_json=draft.metadata,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                return self._record_from_row(row)
        except SQLAlchemyError as exc:
            raise RagMissGovernanceError(
                "failed to record RAG retrieval miss",
                details={"error_type": type(exc).__name__},
            ) from exc

    def is_ready(self) -> bool:
        """检查 RAG 无命中治理表是否可访问。

        :return: 表可查询时返回 True。
        """
        try:
            with self.session_factory() as session:
                session.execute(select(RagRetrievalMissModel.id).limit(1))
            return True
        except SQLAlchemyError:
            return False

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
        :raises RagMissGovernanceError: 数据库读取失败时抛出。
        """
        filters = self._filters(rag_scope=rag_scope, status=status, task_domain=task_domain)
        try:
            with self.session_factory() as session:
                total = int(session.scalar(select(func.count()).select_from(RagRetrievalMissModel).where(*filters)) or 0)
                rows = session.scalars(
                    select(RagRetrievalMissModel)
                    .where(*filters)
                    .order_by(RagRetrievalMissModel.created_at.desc(), RagRetrievalMissModel.id.desc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            return tuple(self._view_from_row(row) for row in rows), total
        except SQLAlchemyError as exc:
            raise RagMissGovernanceError(
                "failed to list RAG retrieval misses",
                details={"error_type": type(exc).__name__},
            ) from exc

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
        :raises KeyError: 治理记录不存在时抛出。
        :raises RagMissGovernanceError: 数据库更新失败时抛出。
        """
        now = datetime.now(UTC)
        values: dict[str, object] = {"updated_at": now}
        if status is not None:
            values["status"] = status.value
        if review_notes is not None:
            values["review_notes"] = review_notes
        if linked_ingestion_batch is not None:
            values["linked_ingestion_batch"] = linked_ingestion_batch
        if linked_chunk_ids is not None:
            values["linked_chunk_ids"] = list(linked_chunk_ids)
        try:
            with self.session_factory.begin() as session:
                row = session.scalar(select(RagRetrievalMissModel).where(RagRetrievalMissModel.miss_id == miss_id))
                if row is None:
                    raise KeyError("RAG retrieval miss not found")
                metadata = dict(row.metadata_json or {})
                metadata["last_governance_update"] = {
                    "actor_id": actor_id,
                    "reason": reason,
                    "updated_at": now.isoformat(),
                }
                values["metadata_json"] = metadata
                session.execute(
                    update(RagRetrievalMissModel)
                    .where(RagRetrievalMissModel.miss_id == miss_id)
                    .values(**values)
                )
                updated_row = session.scalar(select(RagRetrievalMissModel).where(RagRetrievalMissModel.miss_id == miss_id))
                if updated_row is None:
                    raise KeyError("RAG retrieval miss not found after update")
                return self._view_from_row(updated_row)
        except KeyError:
            raise
        except SQLAlchemyError as exc:
            raise RagMissGovernanceError(
                "failed to update RAG retrieval miss",
                details={"error_type": type(exc).__name__},
            ) from exc

    def _filters(
        self,
        *,
        rag_scope: RagMissScope | None,
        status: RagMissStatus | None,
        task_domain: str | None,
    ) -> tuple[ColumnElement[bool], ...]:
        """构造 RAG 无命中治理记录查询过滤条件。

        :param rag_scope: 可选 RAG 数据链范围过滤条件。
        :param status: 可选治理状态过滤条件。
        :param task_domain: 可选任务域过滤条件。
        :return: 返回 SQLAlchemy 过滤条件元组。
        """
        filters: list[ColumnElement[bool]] = []
        if rag_scope is not None:
            filters.append(RagRetrievalMissModel.rag_scope == rag_scope.value)
        if status is not None:
            filters.append(RagRetrievalMissModel.status == status.value)
        if task_domain:
            filters.append(RagRetrievalMissModel.task_domain == task_domain)
        return tuple(filters)

    def _record_from_row(self, row: RagRetrievalMissModel) -> RagMissRecord:
        """将数据库行转换为治理记录摘要。

        :param row: RAG 无命中治理数据库行。
        :return: 返回治理记录摘要。
        """
        return RagMissRecord(
            miss_id=row.miss_id,
            request_id=row.request_id,
            trace_id=row.trace_id,
            rag_scope=RagMissScope(row.rag_scope),
            task_domain=row.task_domain,
            failure_reason=row.failure_reason,
            dedupe_key=row.dedupe_key,
            status=RagMissStatus(row.status),
            created_at=row.created_at,
        )

    def _view_from_row(self, row: RagRetrievalMissModel) -> RagMissRecordView:
        """将数据库行转换为管理端治理记录详情。

        :param row: RAG 无命中治理数据库行。
        :return: 返回管理端治理记录详情。
        """
        return RagMissRecordView(
            miss_id=row.miss_id,
            request_id=row.request_id,
            trace_id=row.trace_id,
            user_id=row.user_id,
            pet_id=row.pet_id,
            session_id=row.session_id,
            rag_scope=RagMissScope(row.rag_scope),
            task_id=row.task_id,
            task_key=row.task_key,
            task_domain=row.task_domain,
            task_title=row.task_title,
            user_text_excerpt=row.user_text_excerpt,
            user_text_digest=row.user_text_digest,
            structured_query=dict(row.structured_query or {}),
            consultation_state=dict(row.consultation_state or {}),
            answerability=dict(row.answerability or {}),
            semantic_extraction=dict(row.semantic_extraction or {}),
            retrieval_parameters=dict(row.retrieval_parameters or {}),
            failure_reason=row.failure_reason,
            error_type=row.error_type,
            error_message=row.error_message,
            error_details=dict(row.error_details or {}),
            dedupe_key=row.dedupe_key,
            status=RagMissStatus(row.status),
            review_notes=row.review_notes,
            linked_ingestion_batch=row.linked_ingestion_batch,
            linked_chunk_ids=tuple(int(item) for item in row.linked_chunk_ids or ()),
            metadata=dict(row.metadata_json or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
