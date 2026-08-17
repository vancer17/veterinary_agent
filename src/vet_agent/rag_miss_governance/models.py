"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/models.py
作用：定义 RAG 无命中治理链路的稳定领域模型。
范围：承载回答 RAG 无命中事件、治理记录草稿、持久化记录快照和可观察状态；
      不承载检索实现、知识生成规则、关键词分类或运行时回答策略。
说明：本模块中的对象只用于记录知识缺口和后续人工治理，不影响本轮 Agent
      Fail Fast 语义。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RagMissScope(StrEnum):
    """表示 RAG 无命中事件所属的数据链范围。

    :return: 无返回值；枚举值用于治理表、审计 metadata 和后续管理端聚合，覆盖回答与追问两条 RAG 链路。
    """

    ANSWER_RAG = "answer_rag"
    FOLLOWUP_RAG = "followup_rag"


class RagMissStatus(StrEnum):
    """表示 RAG 无命中治理记录的人工处理状态。

    :return: 无返回值；该状态仅用于知识治理，不参与 Agent 运行时裁决。
    """

    OPEN = "open"
    TRIAGED = "triaged"
    ASSET_DRAFTED = "asset_drafted"
    PUBLISHED = "published"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class RagMissRecordRequest:
    """表示从 Agent 编排边界传入的 RAG 无命中原始治理请求。

    :param request_id: 当前 Agent 请求标识。
    :param trace_id: 当前链路追踪标识。
    :param user_id: 当前可信用户标识。
    :param pet_id: 当前可信宠物标识。
    :param session_id: 当前可信会话标识。
    :param rag_scope: 无命中所属 RAG 数据链范围。
    :param task_id: 当前任务展示标识。
    :param task_key: 当前任务状态键。
    :param task_domain: 当前任务域。
    :param task_title: 当前任务标题。
    :param user_text: 当前任务消费的用户文本。
    :param structured_query: 当前 RAG 实际使用的结构化检索 query。
    :param consultation_state: 当前问诊状态快照。
    :param answerability: 当前回答充分性裁决快照。
    :param semantic_extraction: 当前问诊语义抽取快照。
    :param allowed_chunk_types: 本轮允许进入回答 RAG 的 chunk 类型集合。
    :param top_k: 本轮召回数量上限。
    :param min_score: 本轮最低召回分数。
    :param domain_filter: 本轮使用的硬领域过滤条件。
    :param failure_reason: 无命中失败原因。
    :param error_type: 原始异常类型。
    :param error_message: 原始异常消息。
    :param error_details: 原始异常结构化细节。
    :param metadata: 附加审计信息。
    :return: 无返回值；该对象尚未经过治理服务裁剪和哈希处理。
    """

    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str
    rag_scope: RagMissScope
    task_id: str
    task_key: str
    task_domain: str
    task_title: str
    user_text: str
    structured_query: str | None
    consultation_state: dict[str, Any]
    answerability: dict[str, Any]
    semantic_extraction: dict[str, Any]
    allowed_chunk_types: tuple[str, ...]
    top_k: int
    min_score: float
    domain_filter: str | None
    failure_reason: str
    error_type: str
    error_message: str
    error_details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagMissRecordDraft:
    """表示准备写入治理仓储的 RAG 无命中记录草稿。

    :param miss_id: 治理记录稳定标识。
    :param request_id: 当前 Agent 请求标识。
    :param trace_id: 当前链路追踪标识。
    :param user_id: 当前可信用户标识。
    :param pet_id: 当前可信宠物标识。
    :param session_id: 当前可信会话标识。
    :param rag_scope: 无命中所属 RAG 数据链范围。
    :param task_id: 当前任务展示标识。
    :param task_key: 当前任务状态键。
    :param task_domain: 当前任务域。
    :param task_title: 当前任务标题。
    :param user_text_excerpt: 经裁剪后的用户文本片段。
    :param user_text_digest: 用户文本摘要哈希。
    :param structured_query: 结构化检索 query 的 JSON 形态。
    :param consultation_state: 当前问诊状态快照。
    :param answerability: 当前回答充分性裁决快照。
    :param semantic_extraction: 当前问诊语义抽取快照。
    :param retrieval_parameters: 本轮召回参数摘要。
    :param failure_reason: 无命中失败原因。
    :param error_type: 原始异常类型。
    :param error_message: 原始异常消息。
    :param error_details: 原始异常结构化细节。
    :param dedupe_key: 用于后台聚合同类缺口的稳定哈希。
    :param status: 当前治理状态。
    :param metadata: 附加审计信息。
    :return: 无返回值；该对象只能由治理服务构造并传入仓储。
    """

    miss_id: str
    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str
    rag_scope: RagMissScope
    task_id: str
    task_key: str
    task_domain: str
    task_title: str
    user_text_excerpt: str
    user_text_digest: str
    structured_query: dict[str, Any]
    consultation_state: dict[str, Any]
    answerability: dict[str, Any]
    semantic_extraction: dict[str, Any]
    retrieval_parameters: dict[str, Any]
    failure_reason: str
    error_type: str
    error_message: str
    error_details: dict[str, Any]
    dedupe_key: str
    status: RagMissStatus = RagMissStatus.OPEN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagMissRecord:
    """表示已持久化的 RAG 无命中治理记录快照。

    :param miss_id: 治理记录稳定标识。
    :param request_id: 当前 Agent 请求标识。
    :param trace_id: 当前链路追踪标识。
    :param rag_scope: 无命中所属 RAG 数据链范围。
    :param task_domain: 当前任务域。
    :param failure_reason: 无命中失败原因。
    :param dedupe_key: 用于后台聚合同类缺口的稳定哈希。
    :param status: 当前治理状态。
    :param created_at: 记录创建时间。
    :return: 无返回值；该对象只用于审计和测试断言。
    """

    miss_id: str
    request_id: str
    trace_id: str
    rag_scope: RagMissScope
    task_domain: str
    failure_reason: str
    dedupe_key: str
    status: RagMissStatus
    created_at: datetime | None = None

    def to_metadata(self) -> dict[str, Any]:
        """转换为可序列化治理记录摘要。

        :return: 返回不包含用户长文本和完整 query 的治理记录摘要。
        """
        return {
            "miss_id": self.miss_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "rag_scope": self.rag_scope.value,
            "task_domain": self.task_domain,
            "failure_reason": self.failure_reason,
            "dedupe_key": self.dedupe_key,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RagMissRecordView:
    """表示管理端可见的 RAG 无命中治理记录详情。

    :param miss_id: 治理记录稳定标识。
    :param request_id: 当前 Agent 请求标识。
    :param trace_id: 当前链路追踪标识。
    :param user_id: 当前可信用户标识。
    :param pet_id: 当前可信宠物标识。
    :param session_id: 当前可信会话标识。
    :param rag_scope: 无命中所属 RAG 数据链范围。
    :param task_id: 当前任务展示标识。
    :param task_key: 当前任务状态键。
    :param task_domain: 当前任务域。
    :param task_title: 当前任务标题。
    :param user_text_excerpt: 经裁剪后的用户任务文本片段。
    :param user_text_digest: 用户任务文本摘要哈希。
    :param structured_query: 当前 RAG 实际使用的结构化检索 query。
    :param consultation_state: 触发无命中时的问诊状态快照。
    :param answerability: 触发无命中时的回答充分性裁决快照。
    :param semantic_extraction: 触发无命中时的问诊语义抽取快照。
    :param retrieval_parameters: 本轮召回参数摘要。
    :param failure_reason: 无命中失败原因。
    :param error_type: 原始异常类型。
    :param error_message: 原始异常消息。
    :param error_details: 原始异常结构化细节。
    :param dedupe_key: 用于后台聚合同类缺口的稳定哈希。
    :param status: 当前治理状态。
    :param review_notes: 治理人员处理备注。
    :param linked_ingestion_batch: 关联知识导入批次标识。
    :param linked_chunk_ids: 关联正式知识 chunk 内部主键集合。
    :param metadata: 附加审计信息。
    :param created_at: 记录创建时间。
    :param updated_at: 记录最近更新时间。
    :return: 无返回值；该对象只用于管理端治理，不进入回答生成。
    """

    miss_id: str
    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str
    rag_scope: RagMissScope
    task_id: str
    task_key: str
    task_domain: str
    task_title: str
    user_text_excerpt: str
    user_text_digest: str
    structured_query: dict[str, Any]
    consultation_state: dict[str, Any]
    answerability: dict[str, Any]
    semantic_extraction: dict[str, Any]
    retrieval_parameters: dict[str, Any]
    failure_reason: str
    error_type: str
    error_message: str
    error_details: dict[str, Any]
    dedupe_key: str
    status: RagMissStatus
    review_notes: str | None
    linked_ingestion_batch: str | None
    linked_chunk_ids: tuple[int, ...]
    metadata: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为管理端 API 可序列化字典。

        :return: 返回 RAG 无命中治理详情。
        """
        return {
            "miss_id": self.miss_id,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "pet_id": self.pet_id,
            "session_id": self.session_id,
            "rag_scope": self.rag_scope.value,
            "task_id": self.task_id,
            "task_key": self.task_key,
            "task_domain": self.task_domain,
            "task_title": self.task_title,
            "user_text_excerpt": self.user_text_excerpt,
            "user_text_digest": self.user_text_digest,
            "structured_query": self.structured_query,
            "consultation_state": self.consultation_state,
            "answerability": self.answerability,
            "semantic_extraction": self.semantic_extraction,
            "retrieval_parameters": self.retrieval_parameters,
            "failure_reason": self.failure_reason,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_details": self.error_details,
            "dedupe_key": self.dedupe_key,
            "status": self.status.value,
            "review_notes": self.review_notes,
            "linked_ingestion_batch": self.linked_ingestion_batch,
            "linked_chunk_ids": list(self.linked_chunk_ids),
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
