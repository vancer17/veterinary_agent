"""
=============================================================================
文件：tests/test_rag_miss_governance.py
作用：验证 RAG 无命中治理链路的事件标准化、聚合键生成和协议边界。
范围：仅覆盖无数据库单元测试，不连接真实 PostgreSQL、LiteLLM 或回答 RAG。
说明：测试仓储显式继承治理仓储协议，避免业务层绕过仓储边界。
=============================================================================
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from vet_agent.rag_miss_governance import (
    RagMissGovernanceService,
    RagMissRecord,
    RagMissRecordDraft,
    RagMissRecordRequest,
    RagMissRecordView,
    RagMissRepositoryProtocol,
    RagMissScope,
    RagMissStatus,
)


class InMemoryRagMissRepository(RagMissRepositoryProtocol):
    """为 RAG 无命中治理单元测试提供内存仓储实现。

    :return: 无返回值；该仓储只记录治理草稿，不提供运行时知识回退。
    """

    def __init__(self) -> None:
        """初始化内存 RAG miss 仓储。

        :return: 无返回值。
        """
        self.drafts: list[RagMissRecordDraft] = []

    def record_miss(self, draft: RagMissRecordDraft) -> RagMissRecord:
        """保存测试治理记录草稿并返回摘要。

        :param draft: 已标准化的治理记录草稿。
        :return: 返回治理记录摘要。
        """
        self.drafts.append(draft)
        return RagMissRecord(
            miss_id=draft.miss_id,
            request_id=draft.request_id,
            trace_id=draft.trace_id,
            rag_scope=draft.rag_scope,
            task_domain=draft.task_domain,
            failure_reason=draft.failure_reason,
            dedupe_key=draft.dedupe_key,
            status=draft.status,
            created_at=datetime.now(UTC),
        )

    def is_ready(self) -> bool:
        """检查测试仓储是否就绪。

        :return: 始终返回 True。
        """
        return True

    def list_misses(
        self,
        *,
        rag_scope: RagMissScope | None,
        status: RagMissStatus | None,
        task_domain: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RagMissRecordView, ...], int]:
        """分页读取测试 RAG 无命中治理记录。

        :param rag_scope: 可选 RAG 数据链范围过滤条件。
        :param status: 可选治理状态过滤条件。
        :param task_domain: 可选任务域过滤条件。
        :param limit: 返回数量上限。
        :param offset: 分页偏移量。
        :return: 返回治理记录详情元组与总数。
        """
        rows = [
            self._view_from_draft(draft)
            for draft in self.drafts
            if (rag_scope is None or draft.rag_scope is rag_scope)
            and (status is None or draft.status is status)
            and (task_domain is None or draft.task_domain == task_domain)
        ]
        return tuple(rows[offset : offset + limit]), len(rows)

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
        """更新测试 RAG 无命中治理记录。

        :param miss_id: 治理记录稳定标识。
        :param status: 新治理状态。
        :param review_notes: 治理备注。
        :param linked_ingestion_batch: 关联知识导入批次标识。
        :param linked_chunk_ids: 关联正式知识 chunk 内部主键集合。
        :param actor_id: 操作人标识。
        :param reason: 操作原因。
        :return: 返回更新后的治理记录详情。
        """
        for index, draft in enumerate(self.drafts):
            if draft.miss_id != miss_id:
                continue
            metadata = {
                **draft.metadata,
                "last_governance_update": {
                    "actor_id": actor_id,
                    "reason": reason,
                },
            }
            updated = RagMissRecordDraft(
                **{
                    **draft.__dict__,
                    "status": status or draft.status,
                    "metadata": metadata,
                }
            )
            self.drafts[index] = updated
            view = self._view_from_draft(updated)
            return RagMissRecordView(
                **{
                    **view.__dict__,
                    "review_notes": review_notes,
                    "linked_ingestion_batch": linked_ingestion_batch,
                    "linked_chunk_ids": linked_chunk_ids or (),
                }
            )
        raise KeyError("RAG retrieval miss not found")

    def _view_from_draft(self, draft: RagMissRecordDraft) -> RagMissRecordView:
        """将测试治理记录草稿转换为管理端详情。

        :param draft: 治理记录草稿。
        :return: 返回管理端治理记录详情。
        """
        return RagMissRecordView(
            miss_id=draft.miss_id,
            request_id=draft.request_id,
            trace_id=draft.trace_id,
            user_id=draft.user_id,
            pet_id=draft.pet_id,
            session_id=draft.session_id,
            rag_scope=draft.rag_scope,
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
            status=draft.status,
            review_notes=None,
            linked_ingestion_batch=None,
            linked_chunk_ids=(),
            metadata=draft.metadata,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def _request(user_text: str = "我家猫一直去猫砂盆但尿量少，需要注意什么？") -> RagMissRecordRequest:
    """构造 RAG 无命中治理测试请求。

    :param user_text: 当前任务消费的用户文本。
    :return: 返回治理测试请求。
    """
    return RagMissRecordRequest(
        request_id="req_rag_miss",
        trace_id="tr_rag_miss",
        user_id="u1",
        pet_id="p1",
        session_id="s1",
        rag_scope=RagMissScope.ANSWER_RAG,
        task_id="task_1",
        task_key="task_key_1",
        task_domain="urinary",
        task_title="排尿异常咨询",
        user_text=user_text,
        structured_query=(
            '{"domain":"urinary","known_slots":{"urination":"尿量少"},'
            '"semantic_extraction":{"chief_complaint":"排尿异常"},'
            '"answerability":{"decision":"answer"}}'
        ),
        consultation_state={"domain": "urinary", "slots": {"urination": "尿量少"}},
        answerability={"decision": "answer"},
        semantic_extraction={"chief_complaint": "排尿异常"},
        allowed_chunk_types=("condition_overview", "triage", "red_flags", "home_advice"),
        top_k=5,
        min_score=0.35,
        domain_filter=None,
        failure_reason="no_approved_vector_hits",
        error_type="AnswerRagDependencyError",
        error_message="answer RAG retrieval returned no approved vector hits",
        error_details={"reason": "no_approved_vector_hits"},
    )


def test_rag_miss_governance_records_structured_gap_without_runtime_fallback() -> None:
    """验证 RAG 无命中治理服务只记录知识缺口，不生成可回答知识。

    :return: 无返回值；断言通过表示治理记录包含聚合键和运行时无效标识。
    """
    repository = InMemoryRagMissRepository()
    service = RagMissGovernanceService(repository)

    record = asyncio.run(service.record_miss(_request()))

    assert record.rag_scope is RagMissScope.ANSWER_RAG
    assert record.status is RagMissStatus.OPEN
    assert record.failure_reason == "no_approved_vector_hits"
    assert len(repository.drafts) == 1
    draft = repository.drafts[0]
    assert draft.structured_query["domain"] == "urinary"
    assert draft.retrieval_parameters["allowed_chunk_types"] == [
        "condition_overview",
        "triage",
        "red_flags",
        "home_advice",
    ]
    assert draft.metadata["runtime_effect"] == "none"


def test_rag_miss_governance_uses_stable_dedupe_key_for_same_structured_gap() -> None:
    """验证同类 RAG 无命中事件会生成稳定聚合键。

    :return: 无返回值；断言通过表示聚合不依赖请求标识或原文完整内容。
    """
    first_repository = InMemoryRagMissRepository()
    second_repository = InMemoryRagMissRepository()
    first_service = RagMissGovernanceService(first_repository)
    second_service = RagMissGovernanceService(second_repository)

    first = asyncio.run(first_service.record_miss(_request("猫尿很少，需要注意什么？")))
    second = asyncio.run(second_service.record_miss(_request("猫一直蹲猫砂盆，尿量很少。")))

    assert first.dedupe_key == second.dedupe_key


def test_rag_miss_governance_lists_and_updates_manual_status() -> None:
    """验证 RAG 无命中治理记录可分页读取并更新人工状态。

    :return: 无返回值；断言通过表示治理状态不会进入运行时回答链路。
    """
    repository = InMemoryRagMissRepository()
    service = RagMissGovernanceService(repository)
    record = asyncio.run(service.record_miss(_request()))

    listed = asyncio.run(service.list_misses(rag_scope="answer_rag", status="open", task_domain="urinary"))
    updated = asyncio.run(
        service.update_miss(
            record.miss_id,
            status="triaged",
            review_notes="确认需要补充排尿异常知识资产。",
            linked_ingestion_batch="batch_urinary",
            linked_chunk_ids=(101, 102),
            actor_id="admin_1",
            reason="knowledge_gap_triage",
        )
    )

    assert listed["total"] == 1
    assert listed["items"][0]["miss_id"] == record.miss_id
    assert updated["status"] == "triaged"
    assert updated["linked_ingestion_batch"] == "batch_urinary"
    assert updated["linked_chunk_ids"] == [101, 102]
