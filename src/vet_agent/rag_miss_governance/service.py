"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/service.py
作用：编排 RAG 无命中治理记录的标准化、脱敏摘要、聚合键生成与仓储写入。
范围：服务只把无命中事件沉淀为知识缺口治理材料；不生成知识、不调用 RAG、
      不执行关键词分类、不改变 Agent 本轮 Fail Fast 结果。
说明：该服务是运行时回答链路与离线知识治理链路之间的窄边界。
=============================================================================
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from .models import (
    RagMissRecord,
    RagMissRecordDraft,
    RagMissRecordRequest,
    RagMissScope,
    RagMissStatus,
)
from .ports import RagMissGovernanceProtocol, RagMissRepositoryProtocol


class RagMissGovernanceService(RagMissGovernanceProtocol):
    """执行 RAG 无命中治理记录的生产服务编排。

    :return: 无返回值；该服务只负责留痕，不提供任何运行时回退。
    """

    def __init__(self, repository: RagMissRepositoryProtocol) -> None:
        """初始化 RAG 无命中治理服务。

        :param repository: RAG 无命中治理仓储。
        :return: 无返回值。
        """
        self.repository = repository
        self._max_excerpt_chars = 500

    async def record_miss(self, request: RagMissRecordRequest) -> RagMissRecord:
        """记录一次 RAG 无命中治理事件。

        :param request: RAG 无命中治理请求。
        :return: 返回已持久化治理记录摘要。
        """
        draft = self._build_draft(request)
        return self.repository.record_miss(draft)

    def is_ready(self) -> bool:
        """检查 RAG 无命中治理服务是否具备记录能力。

        :return: 仓储可访问时返回 True。
        """
        return self.repository.is_ready()

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
        :raises ValueError: 查询参数不符合治理契约时抛出。
        """
        normalized_limit = self._positive_limit(limit)
        normalized_offset = self._non_negative_offset(offset)
        items, total = self.repository.list_misses(
            rag_scope=self._optional_scope(rag_scope),
            status=self._optional_status(status),
            task_domain=self._optional_text(task_domain),
            limit=normalized_limit,
            offset=normalized_offset,
        )
        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "backend": "rag_miss_governance",
        }

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
        :raises ValueError: 更新参数不符合治理契约时抛出。
        """
        normalized_miss_id = self._required_text(miss_id, field_name="miss_id")
        updated = self.repository.update_miss(
            normalized_miss_id,
            status=self._optional_status(status),
            review_notes=self._optional_text(review_notes),
            linked_ingestion_batch=self._optional_text(linked_ingestion_batch),
            linked_chunk_ids=linked_chunk_ids,
            actor_id=self._optional_text(actor_id),
            reason=self._optional_text(reason),
        )
        return updated.to_dict()

    def _build_draft(self, request: RagMissRecordRequest) -> RagMissRecordDraft:
        """将原始治理请求转换为可持久化记录草稿。

        :param request: RAG 无命中治理请求。
        :return: 返回治理记录草稿。
        """
        normalized_scope = self._scope(request.rag_scope)
        structured_query = self._structured_query(request.structured_query)
        user_text_excerpt = self._truncate(request.user_text, self._max_excerpt_chars)
        user_text_digest = self._digest(request.user_text.strip())
        retrieval_parameters = {
            "allowed_chunk_types": list(request.allowed_chunk_types),
            "top_k": request.top_k,
            "min_score": request.min_score,
            "domain_filter": request.domain_filter,
        }
        dedupe_key = self._dedupe_key(
            rag_scope=normalized_scope,
            task_domain=request.task_domain,
            structured_query=structured_query,
            failure_reason=request.failure_reason,
            allowed_chunk_types=request.allowed_chunk_types,
        )
        return RagMissRecordDraft(
            miss_id=f"rag_miss_{uuid4().hex}",
            request_id=request.request_id,
            trace_id=request.trace_id,
            user_id=request.user_id,
            pet_id=request.pet_id,
            session_id=request.session_id,
            rag_scope=normalized_scope,
            task_id=request.task_id,
            task_key=request.task_key,
            task_domain=request.task_domain,
            task_title=request.task_title,
            user_text_excerpt=user_text_excerpt,
            user_text_digest=user_text_digest,
            structured_query=structured_query,
            consultation_state=dict(request.consultation_state or {}),
            answerability=dict(request.answerability or {}),
            semantic_extraction=dict(request.semantic_extraction or {}),
            retrieval_parameters=retrieval_parameters,
            failure_reason=request.failure_reason,
            error_type=request.error_type,
            error_message=self._truncate(request.error_message, 300),
            error_details=self._json_object(request.error_details),
            dedupe_key=dedupe_key,
            metadata={
                **dict(request.metadata or {}),
                "governance_role": "knowledge_gap_record",
                "runtime_effect": "none",
            },
        )

    def _structured_query(self, value: str | None) -> dict[str, Any]:
        """将回答 RAG query 转换为 JSON 对象以便后续治理聚合。

        :param value: 回答 RAG 实际使用的 query 文本。
        :return: 返回 query 的 JSON 对象；无法解析时保留裁剪后的原文。
        """
        if value is None or not value.strip():
            return {}
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": self._truncate(value, 2_000)}
        if isinstance(loaded, dict):
            return loaded
        return {"raw": loaded}

    def _dedupe_key(
        self,
        *,
        rag_scope: RagMissScope,
        task_domain: str,
        structured_query: dict[str, Any],
        failure_reason: str,
        allowed_chunk_types: tuple[str, ...],
    ) -> str:
        """计算治理后台聚合同类 RAG 缺口使用的稳定键。

        :param rag_scope: 无命中所属 RAG 数据链范围。
        :param task_domain: 当前任务域。
        :param structured_query: 结构化检索 query。
        :param failure_reason: 无命中失败原因。
        :param allowed_chunk_types: 允许参与召回的 chunk 类型集合。
        :return: 返回 SHA-256 十六进制聚合键。
        """
        query_basis = {
            "answerability": self._limited_dict(structured_query.get("answerability")),
            "domain": structured_query.get("domain") or task_domain,
            "known_slots": self._limited_dict(structured_query.get("known_slots")),
            "semantic_extraction": self._limited_dict(structured_query.get("semantic_extraction")),
        }
        payload = {
            "rag_scope": rag_scope.value,
            "task_domain": task_domain,
            "failure_reason": failure_reason,
            "allowed_chunk_types": list(allowed_chunk_types),
            "query_basis": query_basis,
        }
        return self._digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _limited_dict(self, value: Any) -> dict[str, Any]:
        """归一化进入聚合键的结构化对象。

        :param value: 候选结构化对象。
        :return: 返回字典；非字典值返回空字典。
        """
        return dict(value) if isinstance(value, dict) else {}

    def _json_object(self, value: Any) -> dict[str, Any]:
        """归一化可写入 JSONB 对象字段的值。

        :param value: 候选 JSON 值。
        :return: 返回 JSON 对象；非对象值放入 raw 字段。
        """
        if isinstance(value, dict):
            return dict(value)
        return {"raw": value}

    def _scope(self, value: RagMissScope) -> RagMissScope:
        """校验治理事件所属 RAG 范围。

        :param value: RAG 无命中范围枚举。
        :return: 返回已校验的 RAG 范围。
        """
        return RagMissScope(value)

    def _optional_scope(self, value: str | None) -> RagMissScope | None:
        """校验可选 RAG 无命中范围过滤条件。

        :param value: 可选 RAG 范围字符串。
        :return: 输入为空时返回 None，否则返回 RAG 范围枚举。
        :raises ValueError: 范围值不合法时抛出。
        """
        text = self._optional_text(value)
        if text is None:
            return None
        try:
            return RagMissScope(text)
        except ValueError as exc:
            raise ValueError(f"rag_scope must be one of {[item.value for item in RagMissScope]}") from exc

    def _optional_status(self, value: str | None) -> RagMissStatus | None:
        """校验可选 RAG 无命中治理状态。

        :param value: 可选治理状态字符串。
        :return: 输入为空时返回 None，否则返回治理状态枚举。
        :raises ValueError: 状态值不合法时抛出。
        """
        text = self._optional_text(value)
        if text is None:
            return None
        try:
            return RagMissStatus(text)
        except ValueError as exc:
            raise ValueError(f"status must be one of {[item.value for item in RagMissStatus]}") from exc

    def _required_text(self, value: str, *, field_name: str) -> str:
        """校验必填文本字段。

        :param value: 候选文本。
        :param field_name: 字段名称。
        :return: 返回裁剪后的非空文本。
        :raises ValueError: 文本为空时抛出。
        """
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    def _optional_text(self, value: str | None) -> str | None:
        """归一化可选文本字段。

        :param value: 候选文本。
        :return: 非空时返回裁剪文本，否则返回 None。
        """
        if value is None:
            return None
        text = str(value or "").strip()
        return text or None

    def _positive_limit(self, value: int) -> int:
        """校验分页数量上限。

        :param value: 候选分页数量。
        :return: 返回分页数量。
        :raises ValueError: 分页数量不在允许范围内时抛出。
        """
        if value < 1 or value > 200:
            raise ValueError("limit must be between 1 and 200")
        return value

    def _non_negative_offset(self, value: int) -> int:
        """校验分页偏移量。

        :param value: 候选分页偏移量。
        :return: 返回分页偏移量。
        :raises ValueError: 分页偏移量为负数时抛出。
        """
        if value < 0:
            raise ValueError("offset must be non-negative")
        return value

    def _truncate(self, value: str, max_chars: int) -> str:
        """裁剪可写入治理记录的文本字段。

        :param value: 待裁剪文本。
        :param max_chars: 最大保留字符数。
        :return: 返回裁剪后的文本。
        """
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars]

    def _digest(self, value: str) -> str:
        """计算治理记录使用的 SHA-256 摘要。

        :param value: 待摘要文本。
        :return: 返回 SHA-256 十六进制字符串。
        """
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DisabledRagMissRecorder(RagMissGovernanceProtocol):
    """提供显式禁用的 RAG 无命中治理记录器。

    :return: 无返回值；该空壳仅用于无数据库的测试或嵌入场景，不作为生产回退。
    """

    async def record_miss(self, request: RagMissRecordRequest) -> RagMissRecord | None:
        """忽略 RAG 无命中治理请求。

        :param request: RAG 无命中治理请求。
        :return: 始终返回 None。
        """
        del request
        return None

    def is_ready(self) -> bool:
        """检查禁用记录器是否可作为显式空壳使用。

        :return: 始终返回 True。
        """
        return True

    async def list_misses(
        self,
        *,
        rag_scope: str | None = None,
        status: str | None = None,
        task_domain: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """读取禁用状态下的 RAG 无命中治理记录。

        :param rag_scope: 可选 RAG 数据链范围过滤条件。
        :param status: 可选治理状态过滤条件。
        :param task_domain: 可选任务域过滤条件。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回空分页结果。
        """
        del rag_scope, status, task_domain
        return {"items": [], "total": 0, "limit": limit, "offset": offset, "backend": "disabled"}

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
        """拒绝更新禁用状态下的 RAG 无命中治理记录。

        :param miss_id: 治理记录稳定标识。
        :param status: 新治理状态。
        :param review_notes: 治理备注。
        :param linked_ingestion_batch: 关联知识导入批次标识。
        :param linked_chunk_ids: 关联正式知识 chunk 内部主键集合。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 不返回；该方法始终抛出 ValueError。
        :raises ValueError: 始终抛出以说明治理记录器已禁用。
        """
        del miss_id, status, review_notes, linked_ingestion_batch, linked_chunk_ids, actor_id, reason
        raise ValueError("RAG miss governance recorder is disabled")
