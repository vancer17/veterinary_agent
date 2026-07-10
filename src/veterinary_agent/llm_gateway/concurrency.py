##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/concurrency.py
# 作用: 实现 LlmGateway 实例、profile 与供应商路由三级并发额度控制。
# 边界: 仅管理进程内并发租约，不实现入口限流、分布式配额、供应商 RPM/TPM 或预算管理。
##################################################################################################

import asyncio
from dataclasses import dataclass

from veterinary_agent.config import LlmGatewaySettings
from veterinary_agent.llm_gateway.enums import (
    LlmGatewayErrorCode,
    LlmGatewayOperation,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError


@dataclass(slots=True)
class LlmConcurrencyLease:
    """一次物理模型调用持有的三级并发租约。"""

    semaphores: tuple[asyncio.Semaphore, ...]
    released: bool = False

    def release(self) -> None:
        """释放当前租约持有的全部并发额度。

        :return: None。
        """

        if self.released:
            return
        for semaphore in reversed(self.semaphores):
            semaphore.release()
        self.released = True

    async def __aenter__(self) -> "LlmConcurrencyLease":
        """进入异步上下文并返回当前并发租约。

        :return: 当前并发租约。
        """

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """退出异步上下文并释放并发租约。

        :param exc_type: 可选异常类型。
        :param exc: 可选异常对象。
        :param traceback: 可选异常 traceback。
        :return: None。
        """

        del exc_type, exc, traceback
        self.release()


class LlmConcurrencyController:
    """LlmGateway 三级进程内并发控制器。"""

    def __init__(self, *, settings: LlmGatewaySettings) -> None:
        """初始化并发控制器。

        :param settings: 已校验的 LlmGateway 配置。
        :return: None。
        """

        self._acquire_timeout_seconds = settings.concurrency_acquire_timeout_seconds
        self._global_semaphore = asyncio.Semaphore(settings.global_max_concurrency)
        self._profile_semaphores = {
            profile.model_profile_id: asyncio.Semaphore(profile.max_concurrency)
            for profile in settings.model_profiles
        }
        self._route_semaphores = {
            route.provider_route_id: asyncio.Semaphore(route.max_concurrency)
            for route in settings.provider_routes
        }

    async def acquire(
        self,
        *,
        operation: LlmGatewayOperation,
        call_id: str,
        request_id: str,
        trace_id: str,
        model_profile_id: str,
        provider_route_id: str,
    ) -> LlmConcurrencyLease:
        """获取实例、profile 与路由三级并发额度。

        :param operation: 当前 LlmGateway 操作名。
        :param call_id: 逻辑模型调用 ID。
        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param model_profile_id: 当前物理调用使用的模型 profile ID。
        :param provider_route_id: 当前物理调用使用的供应商路由 ID。
        :return: 已获取全部并发额度的租约。
        :raises LlmGatewayError: 当等待并发额度超时时抛出。
        """

        profile_semaphore = self._profile_semaphores.get(model_profile_id)
        route_semaphore = self._route_semaphores.get(provider_route_id)
        if profile_semaphore is None or route_semaphore is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=operation,
                message="模型 profile 或供应商路由并发控制器不存在",
                call_id=call_id,
                request_id=request_id,
                trace_id=trace_id,
                model_profile_id=model_profile_id,
                provider_route_id=provider_route_id,
            )
        semaphores = (
            self._global_semaphore,
            profile_semaphore,
            route_semaphore,
        )
        acquired: list[asyncio.Semaphore] = []
        try:
            for semaphore in semaphores:
                await asyncio.wait_for(
                    semaphore.acquire(),
                    timeout=self._acquire_timeout_seconds,
                )
                acquired.append(semaphore)
        except TimeoutError as exc:
            for semaphore in reversed(acquired):
                semaphore.release()
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_CONCURRENCY_LIMITED,
                operation=operation,
                message="等待 LlmGateway 并发额度超时",
                call_id=call_id,
                request_id=request_id,
                trace_id=trace_id,
                model_profile_id=model_profile_id,
                provider_route_id=provider_route_id,
            ) from exc
        except BaseException:
            for semaphore in reversed(acquired):
                semaphore.release()
            raise
        return LlmConcurrencyLease(semaphores=tuple(acquired))


__all__: tuple[str, ...] = (
    "LlmConcurrencyController",
    "LlmConcurrencyLease",
)
