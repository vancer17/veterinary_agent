"""
文件：tests/test_turn_execution_gate.py
作用：验证 Agent 单回合执行门禁的幂等重放与冲突检测行为。
范围：仅覆盖 turn execution 服务协议与仓储协议的协作，不访问真实数据库和外部模型服务。
说明：测试仓储显式继承仓储协议，避免绕过业务层依赖边界。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from vet_agent import (
    AgentTurnRequest,
    AgentTurnResponse,
    InputItem,
    RequestContext,
    ScopeAssertion,
    Settings,
    TrustedIdentity,
    TurnOptions,
    VetContext,
)
from vet_agent.repositories import TurnExecutionRepository, TurnIdempotencyClaim, TurnIdempotencyClaimStatus
from vet_agent.services import TurnExecutionConflictError, TurnExecutionGate


@dataclass
class _StoredIdempotencyRecord:
    """表示测试仓储内保存的幂等记录。

    :param status: 幂等记录状态。
    :param request_hash: 请求语义哈希。
    :param response_snapshot: 首个成功响应快照。
    :param updated_at: 幂等记录最近更新时间。
    :return: 无返回值。
    """

    status: str
    request_hash: str
    response_snapshot: dict[str, Any] | None
    updated_at: datetime


class InMemoryTurnExecutionRepository(TurnExecutionRepository):
    """为 turn execution 单元测试提供内存仓储实现。

    :return: 无返回值。
    """

    def __init__(self) -> None:
        """初始化内存 turn execution 仓储。

        :return: 无返回值。
        """
        self.records: dict[tuple[str, str, str, str], _StoredIdempotencyRecord] = {}

    @asynccontextmanager
    async def turn_lock(self, identity: TrustedIdentity) -> AsyncIterator[None]:
        """模拟同一会话范围的 turn lock。

        :param identity: 本轮可信身份范围。
        :return: 返回异步上下文管理器。
        """
        del identity
        yield

    def claim_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        processing_ttl_seconds: float,
    ) -> TurnIdempotencyClaim:
        """声明测试幂等执行权或返回可重放快照。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 请求语义哈希。
        :param processing_ttl_seconds: processing 记录过期秒数；测试仓储不触发过期路径。
        :return: 返回幂等 claim 结果。
        """
        del request_id, trace_id, processing_ttl_seconds
        key = self._key(identity, idempotency_key)
        record = self.records.get(key)
        if record is None:
            self.records[key] = _StoredIdempotencyRecord(
                status="processing",
                request_hash=request_hash,
                response_snapshot=None,
                updated_at=datetime.now(UTC),
            )
            return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.CLAIMED)
        if record.request_hash != request_hash:
            return TurnIdempotencyClaim(
                status=TurnIdempotencyClaimStatus.CONFLICT,
                existing_request_hash=record.request_hash,
            )
        if record.status == "completed" and record.response_snapshot is not None:
            return TurnIdempotencyClaim(
                status=TurnIdempotencyClaimStatus.REPLAYED,
                response_snapshot=dict(record.response_snapshot),
                existing_request_hash=record.request_hash,
            )
        return TurnIdempotencyClaim(status=TurnIdempotencyClaimStatus.PROCESSING)

    def complete_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        response_snapshot: dict[str, Any],
    ) -> None:
        """保存测试幂等键对应的成功响应快照。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 请求语义哈希。
        :param response_snapshot: 首个成功响应快照。
        :return: 无返回值。
        """
        del request_id, trace_id
        self.records[self._key(identity, idempotency_key)] = _StoredIdempotencyRecord(
            status="completed",
            request_hash=request_hash,
            response_snapshot=dict(response_snapshot),
            updated_at=datetime.now(UTC),
        )

    def fail_idempotency(
        self,
        identity: TrustedIdentity,
        *,
        idempotency_key: str,
        request_id: str,
        trace_id: str,
        request_hash: str,
        error_type: str,
    ) -> None:
        """标记测试幂等键对应的失败结果。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_id: 本次请求标识。
        :param trace_id: 本次链路追踪标识。
        :param request_hash: 请求语义哈希。
        :param error_type: 失败异常类型。
        :return: 无返回值。
        """
        del request_id, trace_id, error_type
        self.records[self._key(identity, idempotency_key)] = _StoredIdempotencyRecord(
            status="failed",
            request_hash=request_hash,
            response_snapshot=None,
            updated_at=datetime.now(UTC),
        )

    def is_ready(self) -> bool:
        """检查测试仓储是否就绪。

        :return: 始终返回 True。
        """
        return True

    def _key(self, identity: TrustedIdentity, idempotency_key: str) -> tuple[str, str, str, str]:
        """构造测试幂等记录键。

        :param identity: 本轮可信身份范围。
        :param idempotency_key: 调用方提供的幂等键。
        :return: 返回测试仓储记录键。
        """
        return identity.user_id, identity.pet_id, identity.session_id, idempotency_key


def test_turn_execution_gate_replays_completed_response() -> None:
    """验证同一语义请求重复使用幂等键时重放首个响应。

    :return: 无返回值；断言通过表示门禁重放行为符合预期。
    """

    async def scenario() -> None:
        """执行幂等重放测试场景。

        :return: 无返回值。
        """
        repository = InMemoryTurnExecutionRepository()
        gate = TurnExecutionGate(_settings(), repository)
        request = _request("狗今天有点拉稀。", idempotency_key="idem_same")
        executions = 0

        async def execute() -> AgentTurnResponse:
            """模拟 Agent 主链路生成成功响应。

            :return: 返回测试 Agent 响应。
            """
            nonlocal executions
            executions += 1
            return _response(request, output_text="先少量多次补水，并观察精神食欲。")

        first = await gate.run(request, execute)
        second = await gate.run(request, execute)

        assert executions == 1
        assert second.id == first.id
        assert second.output_text == first.output_text

    asyncio.run(scenario())


def test_turn_execution_gate_rejects_reused_key_with_different_request() -> None:
    """验证同一幂等键被不同语义请求复用时快速失败。

    :return: 无返回值；断言通过表示门禁冲突检测符合预期。
    """

    async def scenario() -> None:
        """执行幂等冲突测试场景。

        :return: 无返回值。
        """
        repository = InMemoryTurnExecutionRepository()
        gate = TurnExecutionGate(_settings(), repository)
        scope_assertion = _scope_assertion()
        first_request = _request("狗今天有点拉稀。", idempotency_key="idem_conflict", scope_assertion=scope_assertion)
        second_request = _request("猫今天开始呼吸急促。", idempotency_key="idem_conflict", scope_assertion=scope_assertion)
        executions = 0

        async def execute_first() -> AgentTurnResponse:
            """模拟首个 Agent 主链路成功响应。

            :return: 返回首个测试响应。
            """
            nonlocal executions
            executions += 1
            return _response(first_request, output_text="先观察排便次数和精神状态。")

        async def execute_second() -> AgentTurnResponse:
            """模拟不应执行的第二个 Agent 主链路。

            :return: 返回第二个测试响应。
            """
            nonlocal executions
            executions += 1
            return _response(second_request, output_text="该响应不应生成。")

        await gate.run(first_request, execute_first)
        with pytest.raises(TurnExecutionConflictError):
            await gate.run(second_request, execute_second)

        assert executions == 1

    asyncio.run(scenario())


def test_turn_execution_gate_treats_metadata_as_request_semantics() -> None:
    """验证 metadata 不同的请求复用幂等键时按语义冲突处理。

    :return: 无返回值；断言通过表示请求哈希覆盖 metadata。
    """

    async def scenario() -> None:
        """执行 metadata 幂等冲突测试场景。

        :return: 无返回值。
        """
        repository = InMemoryTurnExecutionRepository()
        gate = TurnExecutionGate(_settings(), repository)
        scope_assertion = _scope_assertion()
        first_request = _request(
            "狗今天有点拉稀。",
            idempotency_key="idem_metadata",
            scope_assertion=scope_assertion,
            metadata={"client_feature": "standard"},
        )
        second_request = _request(
            "狗今天有点拉稀。",
            idempotency_key="idem_metadata",
            scope_assertion=scope_assertion,
            metadata={"client_feature": "triage"},
        )

        async def execute_first() -> AgentTurnResponse:
            """模拟首个带 metadata 的 Agent 主链路成功响应。

            :return: 返回首个测试响应。
            """
            return _response(first_request, output_text="先观察排便次数和精神状态。")

        async def execute_second() -> AgentTurnResponse:
            """模拟不应执行的 metadata 冲突请求。

            :return: 返回第二个测试响应。
            """
            return _response(second_request, output_text="该响应不应生成。")

        await gate.run(first_request, execute_first)
        with pytest.raises(TurnExecutionConflictError):
            await gate.run(second_request, execute_second)

    asyncio.run(scenario())


def _settings() -> Settings:
    """构造 turn execution 单元测试配置。

    :return: 返回测试配置对象。
    """
    return Settings(
        litellm_api_key="sk-test",
        idempotency_wait_seconds=0.01,
        idempotency_processing_ttl_seconds=300,
    )


def _request(
    text: str,
    *,
    idempotency_key: str,
    scope_assertion: ScopeAssertion | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentTurnRequest:
    """构造 turn execution 单元测试请求。

    :param text: 用户输入文本。
    :param idempotency_key: 调用方提供的幂等键。
    :param scope_assertion: 可复用的范围声明。
    :param metadata: 附加元数据。
    :return: 返回 Agent 回合请求。
    """
    assertion = scope_assertion or _scope_assertion()
    return AgentTurnRequest(
        request_context=RequestContext(
            request_id=f"req_{uuid4().hex}",
            trace_id=f"tr_{uuid4().hex}",
            response_mode="sync",
        ),
        scope_assertion=assertion,
        trusted_identity=assertion.trusted_identity(),
        input=[InputItem(role="user", type="message", content=text)],
        metadata=metadata or {},
        turn_options=TurnOptions(idempotency_key=idempotency_key),
        vet_context=VetContext(pet_info={"species": "犬", "age": "3岁"}),
    )


def _scope_assertion() -> ScopeAssertion:
    """构造 turn execution 单元测试范围声明。

    :return: 返回核心范围声明对象。
    """
    now = datetime.now(UTC)
    return ScopeAssertion.model_validate(
        {
            "schema_version": "v1",
            "issuer": "test-bff",
            "issued_at": now.isoformat(),
            "user_id": "u_gate",
            "pet_id": "p_gate",
            "session_id": "s_gate",
            "authorization": {
                "ownership_verified": True,
                "pet_active": True,
                "pet_status": "active",
                "pet_deleted": False,
            },
            "profile": {"species": "犬", "age": "3岁"},
            "source": {
                "system": "test-main-service",
                "database": "app_dev",
                "table": "master_pet_info",
                "record_id": "p_gate",
                "record_updated_at": now.isoformat(),
                "data_source": "test",
            },
            "session_policy": {"binding_mode": "single_user_pet_per_session"},
        }
    )


def _response(request: AgentTurnRequest, *, output_text: str) -> AgentTurnResponse:
    """构造 turn execution 单元测试响应。

    :param request: 当前 Agent 回合请求。
    :param output_text: 响应文本。
    :return: 返回 Agent 回合响应。
    """
    return AgentTurnResponse(
        id=f"turn_{uuid4().hex}",
        request_id=request.request_context.request_id,
        trace_id=request.request_context.trace_id,
        model=request.model or "qwen-plus",
        status="completed",
        output_text=output_text,
    )
