"""
模型传输层重试策略。

该模块只执行有界传输退避，不重试 schema invalid、JSON invalid 或语义失败；
语义修复必须由上层 Review / Targeted Repair 显式调度。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


class TransportRetrySleeper(Protocol):
    """可替换的异步等待端口。

    生产实现等待真实时间；测试替身可以立即返回或记录等待时长。
    """

    async def sleep(self, seconds: float) -> None:
        """等待指定时长。

        :param seconds: 非负等待秒数。
        :return: 无返回值。
        """


class DefaultTransportRetrySleeper:
    """基于 ``asyncio.sleep`` 的生产等待实现。"""

    async def sleep(self, seconds: float) -> None:
        """等待真实时间。

        :param seconds: 非负等待秒数。
        :return: 无返回值。
        """
        await asyncio.sleep(seconds)


@dataclass(frozen=True, slots=True)
class TransportRetryPolicy:
    """传输层有界退避策略。

    :ivar max_retries: 最大额外物理尝试次数。
    :ivar initial_delay_seconds: 第一次重试的基础等待时间。
    :ivar max_delay_seconds: 单次等待上限。
    """

    max_retries: int
    initial_delay_seconds: float
    max_delay_seconds: float


def calculate_transport_delay(
    *,
    policy: TransportRetryPolicy,
    next_attempt_number: int,
    retry_after_seconds: float | None,
    random_unit: float,
) -> float:
    """计算下一次传输重试等待时间。

    :param policy: 有界退避策略。
    :param next_attempt_number: 下一物理尝试序号，从 1 开始。
    :param retry_after_seconds: 提供方返回的等待建议；为空时忽略。
    :param random_unit: 调用方生成的 ``[0, 1)`` 随机数，用于确定性测试。
    :return: 限制在策略范围内的等待秒数。
    """
    if next_attempt_number < 1:
        return 0.0
    exponential_delay = policy.initial_delay_seconds * (2 ** (next_attempt_number - 1))
    jittered_delay = exponential_delay * (0.8 + 0.4 * random_unit)
    bounded_delay = min(jittered_delay, policy.max_delay_seconds)
    if retry_after_seconds is None:
        return float(bounded_delay)
    return min(max(retry_after_seconds, 0.0), policy.max_delay_seconds, 30.0)


__all__ = [
    "DefaultTransportRetrySleeper",
    "TransportRetryPolicy",
    "TransportRetrySleeper",
    "calculate_transport_delay",
]
