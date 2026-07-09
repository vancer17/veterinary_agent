##################################################################################################
# 文件: src/veterinary_agent/api_ingress/concurrency.py
# 作用: 定义 API 接入组件面向编排入口的实例级并发闸门，消费 orchestrator.max_concurrency 配置。
# 边界: 仅提供入口层轻量并发保护；不实现队列、分布式限流、编排调用或领域业务调度。
##################################################################################################

from asyncio import Lock
from types import TracebackType
from typing import Self


class ApiIngressConcurrencyLease:
    """API 接入组件并发闸门许可。"""

    def __init__(self, gate: "ApiIngressConcurrencyGate") -> None:
        """初始化并发闸门许可。

        :param gate: 已授予当前许可的并发闸门。
        :return: 无返回值。
        """

        self._gate = gate
        self._released = False
        self._lock = Lock()

    async def __aenter__(self) -> Self:
        """进入异步上下文管理器。

        :return: 当前并发闸门许可。
        """

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """退出异步上下文管理器并释放许可。

        :param exc_type: 上下文中抛出的异常类型。
        :param exc: 上下文中抛出的异常对象。
        :param traceback: 上下文中抛出异常时的 traceback。
        :return: 固定返回 False，不吞掉上下文中的异常。
        """

        del exc_type, exc, traceback
        await self.release()
        return False

    async def release(self) -> None:
        """释放当前并发闸门许可。

        :return: 无返回值。
        """

        async with self._lock:
            if self._released:
                return
            self._released = True
        await self._gate.release()


class ApiIngressConcurrencyGate:
    """API 接入组件实例级并发闸门。"""

    def __init__(self, max_concurrency: int) -> None:
        """初始化实例级并发闸门。

        :param max_concurrency: 允许同时进入受保护下游阶段的最大请求数。
        :return: 无返回值。
        :raises ValueError: 当最大并发数小于 1 时抛出。
        """

        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须大于等于 1")
        self._max_concurrency = max_concurrency
        self._active_count = 0
        self._lock = Lock()

    @property
    def max_concurrency(self) -> int:
        """读取最大并发数。

        :return: 当前闸门允许的最大并发数。
        """

        return self._max_concurrency

    async def active_count(self) -> int:
        """读取当前已占用许可数量。

        :return: 当前活跃许可数量。
        """

        async with self._lock:
            return self._active_count

    async def try_acquire(self) -> ApiIngressConcurrencyLease | None:
        """尝试获取并发闸门许可。

        :return: 获取成功时返回许可；达到最大并发数时返回 None。
        """

        async with self._lock:
            if self._active_count >= self._max_concurrency:
                return None
            self._active_count += 1
        return ApiIngressConcurrencyLease(self)

    async def release(self) -> None:
        """释放一个已获取的并发闸门许可。

        :return: 无返回值。
        :raises RuntimeError: 当释放次数超过获取次数时抛出。
        """

        async with self._lock:
            if self._active_count <= 0:
                raise RuntimeError("并发闸门许可释放次数超过获取次数")
            self._active_count -= 1


__all__: tuple[str, ...] = (
    "ApiIngressConcurrencyGate",
    "ApiIngressConcurrencyLease",
)
