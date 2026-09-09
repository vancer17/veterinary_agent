"""
模型调用请求与 token 预算治理。

该模块在调用前做保守预留，在调用后按实际尝试次数和 usage 结算。预算失败
必须显式抛错，不得通过降低输出上限或放宽 schema 继续执行。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from vet_agent.core import errors as core_errors
from vet_agent.core.llm.contracts import LLMSettings, ModelRole


@dataclass(slots=True)
class BudgetLease:
    """一次逻辑调用的预算预留凭证。

    :ivar lease_id: 预算凭证标识。
    :ivar role: 发起调用的模型角色。
    :ivar reserved_attempts: 预留的物理尝试次数。
    :ivar reserved_prompt_tokens: 单次尝试输入 token 保守估计。
    :ivar reserved_completion_tokens: 单次尝试输出 token 上限。
    :ivar settled: 是否已经完成实际用量结算。
    """

    lease_id: str
    role: ModelRole
    reserved_attempts: int
    reserved_prompt_tokens: int
    reserved_completion_tokens: int
    settled: bool = False


class LLMBudget:
    """进程内模型请求与 token 预算管理器。

    当前实现用于单进程启动周期；分布式预算应由后续核心基础设施按同一契约
    扩展，语义层不得绕开该端口自行限流。
    """

    def __init__(self, settings: LLMSettings) -> None:
        """初始化预算状态。

        :param settings: 包含请求与 token 上限的模型配置。
        :return: 无返回值；初始化进程内预算状态。
        """
        self._max_request_attempts = settings.max_request_attempts
        self._max_total_tokens = settings.max_total_tokens
        self._max_transport_retries = settings.max_transport_retries
        self._consumed_request_attempts = 0
        self._consumed_total_tokens = 0
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        *,
        lease_id: str,
        role: ModelRole,
        estimated_prompt_tokens: int,
        reserved_completion_tokens: int,
    ) -> BudgetLease:
        """在调用前保守预留请求次数与 token。

        :param lease_id: 逻辑调用预算凭证标识。
        :param role: 稳定模型角色。
        :param estimated_prompt_tokens: 单次尝试输入 token 保守估计。
        :param reserved_completion_tokens: 单次尝试输出 token 上限。
        :return: 预算预留凭证。
        :raises vet_agent.core.errors.LLMBudgetExceededError: 当请求或 token 预算不足。
        """
        reserved_attempts = self._max_transport_retries + 1
        reserved_total_tokens = (
            estimated_prompt_tokens + reserved_completion_tokens
        ) * reserved_attempts
        async with self._lock:
            next_requests = self._consumed_request_attempts + reserved_attempts
            next_tokens = self._consumed_total_tokens + reserved_total_tokens
            if (
                self._max_request_attempts is not None
                and next_requests > self._max_request_attempts
            ):
                raise core_errors.LLMBudgetExceededError(
                    error_code=core_errors.CoreErrorCode.LLM_BUDGET_EXCEEDED,
                    message="模型请求次数预算不足",
                    context={
                        "model_role": role.value,
                        "budget_kind": "request_attempts",
                    },
                )
            if (
                self._max_total_tokens is not None
                and next_tokens > self._max_total_tokens
            ):
                raise core_errors.LLMBudgetExceededError(
                    error_code=core_errors.CoreErrorCode.LLM_BUDGET_EXCEEDED,
                    message="模型 token 预算不足",
                    context={"model_role": role.value, "budget_kind": "total_tokens"},
                )
            self._consumed_request_attempts = next_requests
            self._consumed_total_tokens = next_tokens
            return BudgetLease(
                lease_id=lease_id,
                role=role,
                reserved_attempts=reserved_attempts,
                reserved_prompt_tokens=estimated_prompt_tokens,
                reserved_completion_tokens=reserved_completion_tokens,
            )

    async def settle(
        self,
        lease: BudgetLease,
        *,
        actual_attempts: int,
        actual_prompt_tokens: int,
        actual_completion_tokens: int,
    ) -> None:
        """按物理尝试次数和实际 usage 结算预算。

        :param lease: 调用前取得的预算凭证。
        :param actual_attempts: 实际物理请求次数。
        :param actual_prompt_tokens: 成功响应报告的单次输入 token 数。
        :param actual_completion_tokens: 成功响应报告的输出 token 数。
        :return: 无返回值；结算后释放过度预留。
        :raises vet_agent.core.errors.ConfigurationError: 当同一凭证被重复结算。
        :raises vet_agent.core.errors.LLMBudgetExceededError: 当实际用量超出总预算。
        """
        if lease.settled:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="模型预算凭证被重复结算",
                context={"lease_id": lease.lease_id},
            )
        reserved_attempts = lease.reserved_attempts
        reserved_total_tokens = (
            lease.reserved_prompt_tokens + lease.reserved_completion_tokens
        ) * reserved_attempts
        actual_total_tokens = (
            actual_prompt_tokens * actual_attempts + actual_completion_tokens
        )
        async with self._lock:
            self._consumed_request_attempts -= reserved_attempts - actual_attempts
            self._consumed_total_tokens -= reserved_total_tokens - actual_total_tokens
            if (
                self._max_request_attempts is not None
                and self._consumed_request_attempts > self._max_request_attempts
            ):
                lease.settled = True
                raise core_errors.LLMBudgetExceededError(
                    error_code=core_errors.CoreErrorCode.LLM_BUDGET_EXCEEDED,
                    message="模型实际请求次数超出预算",
                    context={
                        "model_role": lease.role.value,
                        "budget_kind": "request_attempts",
                    },
                )
            if (
                self._max_total_tokens is not None
                and self._consumed_total_tokens > self._max_total_tokens
            ):
                lease.settled = True
                raise core_errors.LLMBudgetExceededError(
                    error_code=core_errors.CoreErrorCode.LLM_BUDGET_EXCEEDED,
                    message="模型实际 token 用量超出预算",
                    context={
                        "model_role": lease.role.value,
                        "budget_kind": "total_tokens",
                    },
                )
            lease.settled = True


__all__ = ["BudgetLease", "LLMBudget"]
