"""
=============================================================================
文件：tests/test_persistent_background_tasks.py
作用：验证可持久化后台任务迁移后的入队载荷与长期记忆候选处理器行为。
范围：仅使用内存测试替身，不连接真实 PostgreSQL、不启动 worker 常驻循环、
      不调用真实 LiteLLM 或 Mem0 服务。
说明：测试重点是主回合后台化边界、业务幂等键、显式来源快照和后台处理器
      的保守事实写入条件。
=============================================================================
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from vet_agent import (
    AgentTurnRequest,
    AgentTurnResponse,
    InputItem,
    RequestContext,
    ScopeAssertion,
    ScopeAssertionAuthorization,
    ScopeAssertionProfile,
    ScopeAssertionSessionPolicy,
    ScopeAssertionSource,
    Settings,
    TrustedIdentity,
    TurnOptions,
    VetContext,
)
from vet_agent.background_tasks import (
    BackgroundTaskRecord,
    BackgroundTaskService,
    BackgroundTaskStatus,
    BackgroundTaskType,
    MemoryCandidateExtractionTaskHandler,
    check_background_task_worker_health,
)
from vet_agent.memory_extraction import (
    MemoryCandidateProposal,
    MemoryExtractionAssertionStatus,
    MemoryExtractionDurability,
    MemoryExtractionEvidenceKind,
    MemoryExtractionFactType,
    MemoryExtractionResult,
    MemoryExtractionStrategy,
    MemoryExtractionSubjectScope,
    MemoryExtractionTemporalScope,
)


class FakeBackgroundTaskRepository:
    """提供后台任务入队测试使用的内存仓储替身。

    :return: 无返回值。
    """

    def __init__(self) -> None:
        """初始化内存后台任务仓储替身。

        :return: 无返回值。
        """
        self.enqueue_calls: list[dict[str, Any]] = []

    def enqueue_task(
        self,
        identity: TrustedIdentity,
        *,
        task_type: BackgroundTaskType,
        business_key: str,
        ordering_key: str,
        payload: dict[str, Any],
        source_turn_id: str | None = None,
        source_request_id: str | None = None,
        source_trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: int = 100,
        run_after: datetime | None = None,
        max_attempts: int = 5,
    ) -> BackgroundTaskRecord:
        """记录后台任务入队调用并返回任务快照。

        :param identity: 任务来源可信身份范围。
        :param task_type: 后台任务类型。
        :param business_key: 任务业务幂等键。
        :param ordering_key: 任务顺序约束键。
        :param payload: 任务执行载荷。
        :param source_turn_id: 任务来源回合标识。
        :param source_request_id: 任务来源请求标识。
        :param source_trace_id: 任务来源追踪标识。
        :param metadata: 任务附加审计元数据。
        :param priority: 任务优先级。
        :param run_after: 任务最早可执行时间。
        :param max_attempts: 任务最大执行尝试次数。
        :return: 返回内存任务记录。
        """
        now = datetime.now(UTC)
        call = {
            "identity": identity,
            "task_type": task_type,
            "business_key": business_key,
            "ordering_key": ordering_key,
            "payload": payload,
            "source_turn_id": source_turn_id,
            "source_request_id": source_request_id,
            "source_trace_id": source_trace_id,
            "metadata": metadata or {},
            "priority": priority,
            "run_after": run_after or now,
            "max_attempts": max_attempts,
        }
        self.enqueue_calls.append(call)
        return BackgroundTaskRecord(
            task_id="bt_test",
            task_type=task_type,
            business_key=business_key,
            ordering_key=ordering_key,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            source_turn_id=source_turn_id,
            source_request_id=source_request_id,
            source_trace_id=source_trace_id,
            status=BackgroundTaskStatus.PENDING,
            priority=priority,
            run_after=run_after or now,
            attempt_count=0,
            max_attempts=max_attempts,
            locked_by=None,
            locked_until=None,
            payload=payload,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )

    def is_ready(self) -> bool:
        """声明内存后台任务仓储替身可用。

        :return: 始终返回 True。
        """
        return True


class FakeMemoryExtractionAgent:
    """提供后台任务处理器测试使用的长期记忆抽取替身。

    :param result: 固定返回的长期记忆抽取结果。
    :return: 无返回值。
    """

    def __init__(self, result: MemoryExtractionResult) -> None:
        """初始化长期记忆抽取替身。

        :param result: 固定返回的长期记忆抽取结果。
        :return: 无返回值。
        """
        self.result = result

    async def extract(
        self,
        *,
        identity: TrustedIdentity,
        user_text: str,
        response: AgentTurnResponse,
        model: str,
    ) -> MemoryExtractionResult:
        """返回预设的长期记忆候选抽取结果。

        :param identity: 可信身份信息。
        :param user_text: 当前用户文本。
        :param response: 当前回合响应。
        :param model: 模型名称。
        :return: 返回预设抽取结果。
        """
        del identity, user_text, response, model
        return self.result


class FakeReadinessRepository:
    """提供 worker 健康检查测试使用的仓储就绪替身。

    :param ready: 仓储是否声明就绪。
    :return: 无返回值。
    """

    def __init__(self, ready: bool) -> None:
        """初始化仓储就绪替身。

        :param ready: 仓储是否声明就绪。
        :return: 无返回值。
        """
        self.ready = ready

    def is_ready(self) -> bool:
        """返回预设的仓储就绪状态。

        :return: 仓储可访问时返回 True。
        """
        return self.ready


def test_background_task_service_enqueues_memory_extraction_payload() -> None:
    """验证主回合长期记忆候选抽取会封装为持久化后台任务。

    :return: 无返回值；断言通过表示任务类型、幂等键和来源快照符合预期。
    """
    identity = TrustedIdentity(user_id="u_bg", pet_id="p_bg", session_id="s_bg")
    request = _request(identity, idempotency_key="idem_bg")
    response = _response(
        request,
        output_text="已完成回复。",
        sources=[
            {
                "source_id": "task_a",
                "entry_kind": "task",
                "user_text": "我家狗对鸡肉过敏",
                "assistant_text": "已记录为候选。",
                "task_key": "medical",
            }
        ],
    )
    repository = FakeBackgroundTaskRepository()
    service = BackgroundTaskService(
        Settings(background_tasks_max_attempts=3),
        repository,
    )

    task = asyncio.run(service.enqueue_memory_candidate_extraction(request, response))

    assert task is not None
    assert task.task_type == BackgroundTaskType.MEMORY_CANDIDATE_EXTRACTION
    assert task.business_key == "idem_bg"
    assert task.ordering_key == "u_bg:p_bg:s_bg"
    assert repository.enqueue_calls[0]["source_turn_id"] == "turn_bg"
    assert repository.enqueue_calls[0]["source_request_id"] == "req_bg"
    assert repository.enqueue_calls[0]["max_attempts"] == 3
    payload = repository.enqueue_calls[0]["payload"]
    assert payload["identity"]["user_id"] == "u_bg"
    assert payload["source_count"] == 1
    assert payload["response_snapshot"]["metadata"]["memory_extraction_sources"][0]["source_id"] == "task_a"


def test_memory_candidate_extraction_handler_records_candidates_without_fact_write() -> None:
    """验证后台处理器只记录长期记忆候选而不写入权威事实。

    :return: 无返回值；断言通过表示候选抽取与长期事实写入治理保持分离。
    """
    identity = TrustedIdentity(user_id="u_handler", pet_id="p_handler", session_id="s_handler")
    request = _request(identity, idempotency_key="idem_handler")
    response = _response(
        request,
        output_text="已完成候选抽取测试。",
        sources=[
            {
                "source_id": "task_a",
                "entry_kind": "task",
                "user_text": "我家狗对鸡肉过敏，最近好像也有点怕鸡肉。",
                "assistant_text": "已记录候选。",
            }
        ],
    )
    payload = {
        "identity": identity.model_dump(mode="json"),
        "user_text": request.joined_text(),
        "response_snapshot": response.model_dump(mode="json"),
        "source_count": 1,
    }
    task = _task_record(identity, payload=payload)
    handler = MemoryCandidateExtractionTaskHandler(
        FakeMemoryExtractionAgent(_memory_extraction_result()),
    )

    outcome = asyncio.run(handler.handle(task))

    assert outcome.status == BackgroundTaskStatus.SUCCEEDED
    assert outcome.result["proposal_count"] == 4
    assert outcome.result["proposal_keys"] == [
        "medical:allergy",
        "TODO:unknown",
        "medical:possible_food_avoidance",
        "medical:assistant_guess",
    ]
    assert outcome.result["stored_fact_count"] == 0
    assert outcome.result["stored_fact_keys"] == []
    assert outcome.result["fact_write_status"] == "TODO"
    assert outcome.result["fact_write_reason"] == "long_term_fact_write_governance_not_implemented"


def test_background_task_worker_healthcheck_requires_database_url() -> None:
    """验证 worker 健康检查在缺少数据库连接串时 Fail Fast。

    :return: 无返回值；断言通过表示容器入口不会在无任务可信源时误报健康。
    """
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        check_background_task_worker_health(
            Settings(database_url=None),
            repository=FakeReadinessRepository(True),
        )


def test_background_task_worker_healthcheck_uses_repository_readiness() -> None:
    """验证 worker 健康检查以后台任务仓储可访问性为健康依据。

    :return: 无返回值；断言通过表示健康检查不会调用模型或执行任务。
    """
    check_background_task_worker_health(
        Settings(database_url="postgresql://example"),
        repository=FakeReadinessRepository(True),
    )
    with pytest.raises(RuntimeError, match="not ready"):
        check_background_task_worker_health(
            Settings(database_url="postgresql://example"),
            repository=FakeReadinessRepository(False),
        )


def _request(identity: TrustedIdentity, *, idempotency_key: str) -> AgentTurnRequest:
    """构造后台任务测试使用的 Agent 回合请求。

    :param identity: 可信身份范围。
    :param idempotency_key: 幂等键。
    :return: 返回测试请求对象。
    """
    return AgentTurnRequest(
        request_context=RequestContext(
            request_id="req_bg",
            trace_id="tr_bg",
            response_mode="sync",
        ),
        scope_assertion=_scope_assertion(identity),
        trusted_identity=identity,
        input=[InputItem(role="user", content="我家狗对鸡肉过敏")],
        vet_context=VetContext(),
        turn_options=TurnOptions(idempotency_key=idempotency_key),
    )


def _response(
    request: AgentTurnRequest,
    *,
    output_text: str,
    sources: list[dict[str, Any]],
) -> AgentTurnResponse:
    """构造后台任务测试使用的 Agent 回合响应。

    :param request: 当前测试请求。
    :param output_text: 响应文本。
    :param sources: 长期记忆候选抽取来源片段。
    :return: 返回测试响应对象。
    """
    return AgentTurnResponse(
        id="turn_bg",
        request_id=request.request_context.request_id,
        trace_id=request.request_context.trace_id,
        model="qwen-plus",
        status="completed",
        output_text=output_text,
        metadata={"memory_extraction_sources": sources},
    )


def _scope_assertion(identity: TrustedIdentity) -> ScopeAssertion:
    """构造后台任务测试使用的范围声明。

    :param identity: 可信身份范围。
    :return: 返回测试范围声明。
    """
    now = datetime.now(UTC)
    return ScopeAssertion(
        schema_version="v1",
        issuer="test-bff",
        issued_at=now,
        expires_at=None,
        user_id=identity.user_id,
        pet_id=identity.pet_id,
        session_id=identity.session_id,
        authorization=ScopeAssertionAuthorization(
            ownership_verified=True,
            pet_active=True,
            pet_status="active",
            pet_deleted=False,
        ),
        profile=ScopeAssertionProfile(species="dog"),
        source=ScopeAssertionSource(
            system="test-main-service",
            table="master_pet_info",
            record_id=identity.pet_id,
            record_updated_at=now,
        ),
        session_policy=ScopeAssertionSessionPolicy(),
    )


def _task_record(identity: TrustedIdentity, *, payload: dict[str, Any]) -> BackgroundTaskRecord:
    """构造已领取后台任务记录。

    :param identity: 可信身份范围。
    :param payload: 任务载荷。
    :return: 返回后台任务记录。
    """
    now = datetime.now(UTC)
    return BackgroundTaskRecord(
        task_id="bt_handler",
        task_type=BackgroundTaskType.MEMORY_CANDIDATE_EXTRACTION,
        business_key="idem_handler",
        ordering_key="u_handler:p_handler:s_handler",
        user_id=identity.user_id,
        pet_id=identity.pet_id,
        session_id=identity.session_id,
        source_turn_id="turn_handler",
        source_request_id="req_handler",
        source_trace_id="tr_handler",
        status=BackgroundTaskStatus.RUNNING,
        priority=100,
        run_after=now,
        attempt_count=1,
        max_attempts=5,
        locked_by="worker_test",
        locked_until=now,
        payload=payload,
        created_at=now,
        updated_at=now,
    )


def _memory_extraction_result() -> MemoryExtractionResult:
    """构造长期记忆候选抽取结果。

    :return: 返回包含可写和不可写候选的抽取结果。
    """
    proposals = (
        _proposal(
            fact_type=MemoryExtractionFactType.MEDICAL,
            fact_key="allergy",
            fact_value="鸡肉过敏",
            assertion_status=MemoryExtractionAssertionStatus.CONFIRMED,
            durability=MemoryExtractionDurability.DURABLE,
            source_kind=MemoryExtractionEvidenceKind.USER_TEXT,
        ),
        _proposal(
            fact_type=MemoryExtractionFactType.TODO,
            fact_key="unknown",
            fact_value="未知候选",
            assertion_status=MemoryExtractionAssertionStatus.CONFIRMED,
            durability=MemoryExtractionDurability.DURABLE,
            source_kind=MemoryExtractionEvidenceKind.USER_TEXT,
        ),
        _proposal(
            fact_type=MemoryExtractionFactType.MEDICAL,
            fact_key="possible_food_avoidance",
            fact_value="好像怕鸡肉",
            assertion_status=MemoryExtractionAssertionStatus.UNCERTAIN,
            durability=MemoryExtractionDurability.DURABLE,
            source_kind=MemoryExtractionEvidenceKind.USER_TEXT,
        ),
        _proposal(
            fact_type=MemoryExtractionFactType.MEDICAL,
            fact_key="assistant_guess",
            fact_value="助手推测",
            assertion_status=MemoryExtractionAssertionStatus.CONFIRMED,
            durability=MemoryExtractionDurability.DURABLE,
            source_kind=MemoryExtractionEvidenceKind.ASSISTANT_OUTPUT,
        ),
    )
    return MemoryExtractionResult(
        proposals=proposals,
        strategy=MemoryExtractionStrategy.LITELLM_RESPONSE_FORMAT,
        confidence=0.93,
        source_text="我家狗对鸡肉过敏",
    )


def _proposal(
    *,
    fact_type: MemoryExtractionFactType,
    fact_key: str,
    fact_value: str,
    assertion_status: MemoryExtractionAssertionStatus,
    durability: MemoryExtractionDurability,
    source_kind: MemoryExtractionEvidenceKind,
) -> MemoryCandidateProposal:
    """构造长期记忆候选提议。

    :param fact_type: 候选事实类型。
    :param fact_key: 候选事实键。
    :param fact_value: 候选事实值。
    :param assertion_status: 候选断言状态。
    :param durability: 候选持久性。
    :param source_kind: 候选证据来源类型。
    :return: 返回长期记忆候选提议。
    """
    return MemoryCandidateProposal(
        source_id="task_a",
        subject_scope=MemoryExtractionSubjectScope.PET,
        fact_type=fact_type,
        fact_key=fact_key,
        fact_value=fact_value,
        assertion_status=assertion_status,
        durability=durability,
        temporal_scope=MemoryExtractionTemporalScope.HISTORICAL,
        confidence=0.9,
        source_kind=source_kind,
        source_text="我家狗对鸡肉过敏",
        rationale="测试候选。",
    )
