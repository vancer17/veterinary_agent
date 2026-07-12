##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/retry.py
# 作用: 基于 Tenacity 封装 LlmGateway 单 profile 物理调用重试策略与指数退避能力。
# 边界: 不执行 profile 降级、不构造模型请求、不吞掉 LlmGateway 标准错误。
##################################################################################################

from time import perf_counter
from typing import Protocol

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
)
from tenacity.wait import wait_base

from veterinary_agent.config import LlmModelProfileConfig
from veterinary_agent.llm_gateway.errors import LlmGatewayError


class RetryBudgetState(Protocol):
    """LlmGateway 重试预算状态只读协议。"""

    total_attempts: int
    deadline: float


class LlmGatewayRetryPredicate:
    """判断 Tenacity 是否应继续重试当前物理调用的谓词。"""

    def __init__(
        self,
        *,
        profile: LlmModelProfileConfig,
        state: RetryBudgetState,
        max_total_attempts: int,
    ) -> None:
        """初始化 LlmGateway 重试谓词。

        :param profile: 当前候选模型 profile。
        :param state: 当前逻辑调用重试预算状态。
        :param max_total_attempts: 当前逻辑调用允许的全局最大物理调用次数。
        :return: None。
        """

        self._profile = profile
        self._state = state
        self._max_total_attempts = max_total_attempts

    def __call__(self, exc: BaseException) -> bool:
        """判断指定异常是否允许由 Tenacity 再次重试。

        :param exc: 当前物理调用抛出的异常。
        :return: 若错误码、全局次数和逻辑 deadline 均允许重试，则返回 True。
        """

        return (
            isinstance(exc, LlmGatewayError)
            and exc.retryable
            and exc.code.value in self._profile.retry_policy.retryable_error_codes
            and self._state.total_attempts < self._max_total_attempts
            and perf_counter() < self._state.deadline
        )


class LlmGatewayRetryController:
    """基于 Tenacity 的 LlmGateway 单 profile 重试控制器。"""

    def __init__(self, *, max_total_attempts: int) -> None:
        """初始化 LlmGateway 重试控制器。

        :param max_total_attempts: 当前逻辑调用允许的全局最大物理调用次数。
        :return: None。
        """

        self._max_total_attempts = max_total_attempts

    def build_retrying(
        self,
        *,
        profile: LlmModelProfileConfig,
        state: RetryBudgetState,
    ) -> AsyncRetrying:
        """构建当前 profile 的 Tenacity 异步重试迭代器。

        :param profile: 当前候选模型 profile。
        :param state: 当前逻辑调用重试预算状态。
        :return: 配置了次数、退避和错误谓词的 Tenacity 异步重试器。
        """

        return AsyncRetrying(
            stop=stop_after_attempt(profile.retry_policy.max_attempts),
            wait=self.build_wait_strategy(profile=profile),
            retry=retry_if_exception(
                LlmGatewayRetryPredicate(
                    profile=profile,
                    state=state,
                    max_total_attempts=self._max_total_attempts,
                )
            ),
            reraise=True,
        )

    def build_wait_strategy(self, *, profile: LlmModelProfileConfig) -> wait_base:
        """构建当前 profile 的 Tenacity 等待策略。

        :param profile: 当前候选模型 profile。
        :return: Tenacity 指数退避等待策略。
        """

        policy = profile.retry_policy
        if policy.jitter:
            return wait_random_exponential(
                multiplier=policy.initial_backoff_seconds,
                max=policy.max_backoff_seconds,
                exp_base=policy.backoff_factor,
            )
        return wait_exponential(
            multiplier=policy.initial_backoff_seconds,
            max=policy.max_backoff_seconds,
            exp_base=policy.backoff_factor,
        )


__all__: tuple[str, ...] = (
    "LlmGatewayRetryController",
    "LlmGatewayRetryPredicate",
    "RetryBudgetState",
)
