##################################################################################################
# 文件: src/veterinary_agent/api_ingress/rate_limit.py
# 作用: 定义 API 接入组件实例级限流器，消费 rate_limit.* 配置保护入口请求速率与活跃 SSE 连接。
# 边界: 仅实现单进程内存级入口治理；不实现分布式限流、用户鉴权、网关策略或编排层并发调度。
##################################################################################################

from asyncio import Lock
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from time import monotonic
from types import TracebackType
from typing import Final, Self

from fastapi import Request

from veterinary_agent.api_ingress.dto import ErrorDetailDto
from veterinary_agent.api_ingress.enums import ResponseMode
from veterinary_agent.config import ApiIngressSettings

RATE_LIMIT_WINDOW_SECONDS: Final[float] = 60.0
RATE_LIMIT_ANY_KEY_PART: Final[str] = "*"
RATE_LIMIT_UNKNOWN_CLIENT: Final[str] = "unknown"


@dataclass(frozen=True, slots=True)
class ApiIngressRateLimitKey:
    """API 接入组件实例级请求限流键。"""

    path: str
    client_source: str


@dataclass(frozen=True, slots=True)
class ApiIngressRateLimitDecision:
    """API 接入组件限流判定结果。"""

    allowed: bool
    details: list[ErrorDetailDto]
    retry_after_seconds: int | None = None
    stream_lease: "ApiIngressRateLimitStreamLease | None" = None


class ApiIngressRateLimitStreamLease:
    """API 接入组件活跃 SSE 连接限流许可。"""

    def __init__(self, limiter: "ApiIngressRateLimiter") -> None:
        """初始化活跃 SSE 连接限流许可。

        :param limiter: 授予当前许可的 API 接入限流器。
        :return: 无返回值。
        """

        self._limiter = limiter
        self._released = False
        self._lock = Lock()

    async def __aenter__(self) -> Self:
        """进入异步上下文管理器。

        :return: 当前活跃 SSE 连接限流许可。
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
        """释放当前活跃 SSE 连接限流许可。

        :return: 无返回值。
        """

        async with self._lock:
            if self._released:
                return
            self._released = True
        await self._limiter.release_stream()


class ApiIngressRateLimiter:
    """API 接入组件实例级限流器。"""

    def __init__(
        self,
        *,
        enabled: bool,
        max_requests_per_minute: int,
        max_active_streams: int,
        per_path_enabled: bool,
        per_client_source_enabled: bool,
        time_provider: Callable[[], float] = monotonic,
    ) -> None:
        """初始化 API 接入组件实例级限流器。

        :param enabled: 是否启用入口限流。
        :param max_requests_per_minute: 每个限流键在 60 秒窗口内允许的最大请求数。
        :param max_active_streams: 当前实例允许的最大活跃 SSE 连接数。
        :param per_path_enabled: 请求速率限流是否按路径拆分。
        :param per_client_source_enabled: 请求速率限流是否按客户端来源拆分。
        :param time_provider: 单调时钟函数，用于测试或运行时计算滑动窗口。
        :return: 无返回值。
        :raises ValueError: 当请求速率或活跃流上限小于 1 时抛出。
        """

        if max_requests_per_minute < 1:
            raise ValueError("max_requests_per_minute 必须大于等于 1")
        if max_active_streams < 1:
            raise ValueError("max_active_streams 必须大于等于 1")
        self._enabled = enabled
        self._max_requests_per_minute = max_requests_per_minute
        self._max_active_streams = max_active_streams
        self._per_path_enabled = per_path_enabled
        self._per_client_source_enabled = per_client_source_enabled
        self._time_provider = time_provider
        self._request_timestamps_by_key: dict[ApiIngressRateLimitKey, deque[float]] = {}
        self._active_stream_count = 0
        self._lock = Lock()

    @classmethod
    def from_settings(
        cls,
        settings: ApiIngressSettings,
        *,
        time_provider: Callable[[], float] = monotonic,
    ) -> Self:
        """根据 API 接入组件配置创建限流器。

        :param settings: 已加载的 API 接入组件配置。
        :param time_provider: 单调时钟函数，用于测试或运行时计算滑动窗口。
        :return: 已按 rate_limit 配置初始化的限流器。
        """

        rate_limit = settings.rate_limit
        return cls(
            enabled=rate_limit.enabled,
            max_requests_per_minute=rate_limit.max_requests_per_minute,
            max_active_streams=rate_limit.max_active_streams,
            per_path_enabled=rate_limit.per_path_enabled,
            per_client_source_enabled=rate_limit.per_client_source_enabled,
            time_provider=time_provider,
        )

    @property
    def enabled(self) -> bool:
        """读取限流器启用状态。

        :return: 当前限流器是否启用。
        """

        return self._enabled

    async def active_stream_count(self) -> int:
        """读取当前活跃 SSE 连接许可数量。

        :return: 当前活跃 SSE 连接许可数量。
        """

        async with self._lock:
            return self._active_stream_count

    async def request_count(self, request: Request) -> int:
        """读取当前请求所属限流键在滑动窗口内的请求数量。

        :param request: 当前 HTTP 请求对象。
        :return: 当前限流键仍在窗口内的请求数量。
        """

        key = self._build_key(request)
        now = self._time_provider()
        async with self._lock:
            timestamps = self._request_timestamps_by_key.get(key)
            if timestamps is None:
                return 0
            self._prune_expired_timestamps(timestamps=timestamps, now=now)
            if not timestamps:
                self._request_timestamps_by_key.pop(key, None)
                return 0
            return len(timestamps)

    async def try_acquire(
        self,
        *,
        request: Request,
        response_mode: ResponseMode,
    ) -> ApiIngressRateLimitDecision:
        """尝试通过入口限流检查并按需获取活跃 SSE 连接许可。

        :param request: 当前 HTTP 请求对象。
        :param response_mode: 已归一化并通过可用性校验的入口响应模式。
        :return: 限流判定结果；通过时可能携带活跃 SSE 连接许可。
        """

        if not self._enabled:
            return ApiIngressRateLimitDecision(allowed=True, details=[])

        request_limit_decision = await self._try_record_request(request)
        if not request_limit_decision.allowed:
            return request_limit_decision

        if response_mode is not ResponseMode.STREAM:
            return request_limit_decision

        stream_lease = await self._try_acquire_stream()
        if stream_lease is None:
            return ApiIngressRateLimitDecision(
                allowed=False,
                details=[
                    ErrorDetailDto(
                        field="rate_limit.max_active_streams",
                        reason="exceeded",
                    )
                ],
            )
        return ApiIngressRateLimitDecision(
            allowed=True,
            details=[],
            stream_lease=stream_lease,
        )

    async def release_stream(self) -> None:
        """释放一个已获取的活跃 SSE 连接许可。

        :return: 无返回值。
        :raises RuntimeError: 当释放次数超过获取次数时抛出。
        """

        async with self._lock:
            if self._active_stream_count <= 0:
                raise RuntimeError("活跃 SSE 连接限流许可释放次数超过获取次数")
            self._active_stream_count -= 1

    def _build_key(self, request: Request) -> ApiIngressRateLimitKey:
        """构建当前请求的速率限流键。

        :param request: 当前 HTTP 请求对象。
        :return: 当前请求对应的速率限流键。
        """

        path = request.url.path if self._per_path_enabled else RATE_LIMIT_ANY_KEY_PART
        client_source = (
            self._resolve_client_source(request)
            if self._per_client_source_enabled
            else RATE_LIMIT_ANY_KEY_PART
        )
        return ApiIngressRateLimitKey(path=path, client_source=client_source)

    def _resolve_client_source(self, request: Request) -> str:
        """解析限流使用的客户端来源。

        :param request: 当前 HTTP 请求对象。
        :return: 客户端来源字符串；无法解析时返回 unknown。
        """

        client = request.client
        if client is None:
            return RATE_LIMIT_UNKNOWN_CLIENT
        return client.host

    def _prune_expired_timestamps(
        self,
        *,
        timestamps: deque[float],
        now: float,
    ) -> None:
        """清理滑动窗口外的请求时间戳。

        :param timestamps: 某个限流键对应的请求时间戳队列。
        :param now: 当前单调时间。
        :return: 无返回值。
        """

        while timestamps and now - timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS:
            timestamps.popleft()

    def _calculate_retry_after_seconds(
        self,
        *,
        oldest_timestamp: float,
        now: float,
    ) -> int:
        """计算建议客户端等待后重试的秒数。

        :param oldest_timestamp: 当前窗口内最早的请求时间戳。
        :param now: 当前单调时间。
        :return: 至少为 1 的整数秒重试等待时间。
        """

        remaining_seconds = RATE_LIMIT_WINDOW_SECONDS - (now - oldest_timestamp)
        return max(1, ceil(remaining_seconds))

    async def _try_record_request(
        self,
        request: Request,
    ) -> ApiIngressRateLimitDecision:
        """尝试记录当前请求并执行每分钟请求数限制。

        :param request: 当前 HTTP 请求对象。
        :return: 请求速率限流判定结果。
        """

        key = self._build_key(request)
        now = self._time_provider()
        async with self._lock:
            timestamps = self._request_timestamps_by_key.setdefault(key, deque())
            self._prune_expired_timestamps(timestamps=timestamps, now=now)
            if len(timestamps) >= self._max_requests_per_minute:
                retry_after_seconds = self._calculate_retry_after_seconds(
                    oldest_timestamp=timestamps[0],
                    now=now,
                )
                return ApiIngressRateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after_seconds,
                    details=[
                        ErrorDetailDto(
                            field="rate_limit.max_requests_per_minute",
                            reason="exceeded",
                        ),
                        ErrorDetailDto(
                            field="rate_limit.retry_after_seconds",
                            reason=str(retry_after_seconds),
                        ),
                    ],
                )
            timestamps.append(now)
        return ApiIngressRateLimitDecision(allowed=True, details=[])

    async def _try_acquire_stream(
        self,
    ) -> ApiIngressRateLimitStreamLease | None:
        """尝试获取活跃 SSE 连接许可。

        :return: 获取成功时返回许可；达到最大活跃流数量时返回 None。
        """

        async with self._lock:
            if self._active_stream_count >= self._max_active_streams:
                return None
            self._active_stream_count += 1
        return ApiIngressRateLimitStreamLease(self)


__all__: tuple[str, ...] = (
    "ApiIngressRateLimitDecision",
    "ApiIngressRateLimitKey",
    "ApiIngressRateLimitStreamLease",
    "ApiIngressRateLimiter",
)
