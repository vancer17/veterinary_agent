"""
文件：src/vet_agent/services/turn_execution.py
作用：提供 Agent 单回合执行门禁，统一处理 turn lock、幂等 claim、响应重放、失败标记与完成快照保存。
范围：位于 VetOrchestrator 主链路入口之前，仅决定本轮是否可执行或是否直接重放已完成响应。
说明：本服务不承载临床安全、问诊状态、RAG、记忆写入等业务状态机，避免基础设施状态侵入 Agent 编排逻辑。
说明：本文件位于 src-layout 下的 services 包内，跨包调用应通过 vet_agent.services 顶层导出。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from vet_agent import AgentTurnRequest, AgentTurnResponse, Settings
from vet_agent.repositories import (
    TurnExecutionRepository,
    TurnExecutionRepositoryError,
    TurnIdempotencyClaim,
    TurnIdempotencyClaimStatus,
)


TurnExecutor = Callable[[], Awaitable[AgentTurnResponse]]


class TurnExecutionError(RuntimeError):
    """表示 Agent 单回合执行门禁的基础异常。

    :return: 无返回值。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化 turn execution 异常。

        :param message: 错误描述。
        :param details: 结构化错误详情。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class TurnExecutionBusyError(TurnExecutionError, TimeoutError):
    """表示相同幂等键的首个请求仍在执行。

    :return: 无返回值。
    """


class TurnExecutionConflictError(TurnExecutionError):
    """表示相同范围与幂等键被不同请求语义复用。

    :return: 无返回值。
    """


class TurnExecutionDependencyError(TurnExecutionError):
    """表示 turn execution 依赖的数据库仓储不可用。

    :return: 无返回值。
    """


class TurnExecutionGateProtocol(Protocol):
    """定义 VetOrchestrator 依赖的单回合执行门禁协议。

    :return: 无返回值。
    """

    async def run(self, request: AgentTurnRequest, execute: TurnExecutor) -> AgentTurnResponse:
        """在执行门禁保护下运行一个 Agent 回合。

        :param request: 当前 Agent 回合请求。
        :param execute: 真正执行 Agent 主链路的异步函数。
        :return: 返回新生成或幂等重放的 Agent 回合响应。
        """
        ...

    def is_ready(self) -> bool:
        """检查单回合执行门禁是否就绪。

        :return: 依赖仓储可用时返回 True。
        """
        ...


class TurnExecutionGate(TurnExecutionGateProtocol):
    """默认单回合执行门禁实现。

    :return: 无返回值。
    """

    def __init__(self, settings: Settings, repository: TurnExecutionRepository) -> None:
        """初始化单回合执行门禁。

        :param settings: 当前运行环境配置。
        :param repository: turn execution 仓储。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self._poll_interval_seconds = 0.08

    async def run(self, request: AgentTurnRequest, execute: TurnExecutor) -> AgentTurnResponse:
        """在 turn lock 与幂等保护下执行 Agent 主链路。

        :param request: 当前 Agent 回合请求。
        :param execute: 真正执行 Agent 主链路的异步函数。
        :return: 返回新生成或幂等重放的 Agent 回合响应。
        """
        try:
            async with self.repository.turn_lock(request.trusted_identity):
                idempotency_key = request.turn_options.idempotency_key
                if not idempotency_key:
                    return await execute()
                return await self._run_idempotent(
                    request=request,
                    idempotency_key=idempotency_key,
                    execute=execute,
                )
        except TurnExecutionRepositoryError as exc:
            raise TurnExecutionDependencyError(
                "turn execution repository is unavailable",
                details={"error_type": type(exc).__name__},
            ) from exc

    def is_ready(self) -> bool:
        """检查单回合执行门禁所需仓储是否可用。

        :return: 仓储可用时返回 True。
        """
        return self.repository.is_ready()

    async def _run_idempotent(
        self,
        *,
        request: AgentTurnRequest,
        idempotency_key: str,
        execute: TurnExecutor,
    ) -> AgentTurnResponse:
        """执行带幂等键的 Agent 回合。

        :param request: 当前 Agent 回合请求。
        :param idempotency_key: 调用方提供的幂等键。
        :param execute: 真正执行 Agent 主链路的异步函数。
        :return: 返回新生成或幂等重放的 Agent 回合响应。
        """
        request_hash = self._request_hash(request)
        claim = await self._claim_with_wait(
            request=request,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if claim.status == TurnIdempotencyClaimStatus.REPLAYED and claim.response_snapshot:
            return AgentTurnResponse.model_validate(claim.response_snapshot)

        try:
            response = await execute()
        except Exception as exc:
            await self._mark_failed(
                request=request,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                error_type=type(exc).__name__,
            )
            raise

        self.repository.complete_idempotency(
            request.trusted_identity,
            idempotency_key=idempotency_key,
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            request_hash=request_hash,
            response_snapshot=response.model_dump(mode="json"),
        )
        return response

    async def _claim_with_wait(
        self,
        *,
        request: AgentTurnRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> TurnIdempotencyClaim:
        """在有限等待窗口内声明幂等执行权或读取重放快照。

        :param request: 当前 Agent 回合请求。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :return: 返回已声明执行权或可重放响应的 claim 结果。
        """
        deadline = asyncio.get_running_loop().time() + self.settings.idempotency_wait_seconds
        while True:
            claim = self.repository.claim_idempotency(
                request.trusted_identity,
                idempotency_key=idempotency_key,
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                request_hash=request_hash,
                processing_ttl_seconds=self.settings.idempotency_processing_ttl_seconds,
            )
            if claim.status in {TurnIdempotencyClaimStatus.CLAIMED, TurnIdempotencyClaimStatus.REPLAYED}:
                return claim
            if claim.status == TurnIdempotencyClaimStatus.CONFLICT:
                raise TurnExecutionConflictError(
                    "idempotency key conflicts with an existing request",
                    details={
                        "idempotency_key": idempotency_key,
                        "existing_request_hash": claim.existing_request_hash,
                        "request_hash": request_hash,
                    },
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise TurnExecutionBusyError(
                    "idempotent request is still processing",
                    details={"idempotency_key": idempotency_key},
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _mark_failed(
        self,
        *,
        request: AgentTurnRequest,
        idempotency_key: str,
        request_hash: str,
        error_type: str,
    ) -> None:
        """在主链路异常时尽力标记幂等记录失败。

        :param request: 当前 Agent 回合请求。
        :param idempotency_key: 调用方提供的幂等键。
        :param request_hash: 去除瞬时追踪字段后的请求语义哈希。
        :param error_type: 主链路异常类型。
        :return: 无返回值。
        """
        try:
            self.repository.fail_idempotency(
                request.trusted_identity,
                idempotency_key=idempotency_key,
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                request_hash=request_hash,
                error_type=error_type,
            )
        except TurnExecutionRepositoryError:
            return

    def _request_hash(self, request: AgentTurnRequest) -> str:
        """计算幂等冲突检测使用的请求语义哈希。

        :param request: 当前 Agent 回合请求。
        :return: 返回 SHA-256 十六进制哈希。
        """
        payload = {
            "attachments": [item.model_dump(mode="json") for item in request.attachments],
            "input": [item.model_dump(mode="json") for item in request.input],
            "metadata": request.metadata,
            "model": request.model,
            "scope": {
                "authorization": request.scope_assertion.authorization.model_dump(mode="json"),
                "identity": request.trusted_identity.model_dump(mode="json"),
                "profile": request.scope_assertion.profile.model_dump(mode="json"),
            },
            "turn_options": request.turn_options.model_dump(
                mode="json",
                exclude={"idempotency_key"},
            ),
            "vet_context": request.vet_context.model_dump(mode="json"),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
