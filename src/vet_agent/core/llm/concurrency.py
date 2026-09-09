"""
模型调用并发治理。

该模块实现全局与模型角色两级异步并发上限。并发 slot 只约束物理请求执行，
不保存语义状态，也不改变模型调用失败语义。
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Self

from vet_agent.core import errors as core_errors
from vet_agent.core.llm.contracts import LLMSettings, ModelRole


class LLMConcurrencySlot:
    """一次物理请求占用的全局与角色并发资源。

    :ivar _global_semaphore: 全局并发信号量。
    :ivar _role_semaphore: 角色并发信号量。
    """

    def __init__(
        self,
        *,
        global_semaphore: asyncio.Semaphore,
        role_semaphore: asyncio.Semaphore,
    ) -> None:
        """初始化并发资源持有器。

        :param global_semaphore: 全局并发信号量。
        :param role_semaphore: 模型角色并发信号量。
        :return: 无返回值；保存并发资源引用。
        """
        self._global_semaphore = global_semaphore
        self._role_semaphore = role_semaphore

    async def __aenter__(self) -> Self:
        """按固定顺序获取全局与角色并发资源。

        :return: 当前并发资源持有器。
        :raises asyncio.CancelledError: 当等待并发资源被取消时原样向上传播。
        """
        await self._global_semaphore.acquire()
        try:
            await self._role_semaphore.acquire()
        except BaseException:
            self._global_semaphore.release()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """按逆序释放角色与全局并发资源。

        :param exc_type: 退出上下文时的异常类型。
        :param exc_value: 退出上下文时的异常对象。
        :param traceback: 退出上下文时的回溯对象。
        :return: 无返回值；本上下文不吞没异常。
        """
        del exc_type, exc_value, traceback
        self._role_semaphore.release()
        self._global_semaphore.release()


class LLMConcurrencyLimiter:
    """模型调用全局与角色并发限制器。

    :ivar _global_semaphore: 全局并发信号量。
    :ivar _role_semaphores: 角色到并发信号量的映射。
    """

    def __init__(self, settings: LLMSettings) -> None:
        """从不可变模型配置构造并发限制器。

        :param settings: 已通过启动校验的模型网关配置。
        :return: 无返回值；构造全局与角色信号量。
        :raises vet_agent.core.errors.ConfigurationError: 当角色缺少并发配置。
        """
        self._global_semaphore = asyncio.Semaphore(settings.max_global_concurrency)
        self._role_semaphores = {
            role: asyncio.Semaphore(config.max_concurrency)
            for role, config in settings.role_configs.items()
        }

    def slot(self, role: ModelRole) -> LLMConcurrencySlot:
        """创建指定角色的并发资源上下文。

        :param role: 稳定模型角色。
        :return: 可异步进入和退出的并发资源持有器。
        :raises vet_agent.core.errors.ConfigurationError: 当角色未在配置中声明。
        """
        role_semaphore = self._role_semaphores.get(role)
        if role_semaphore is None:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="模型角色缺少并发配置",
                context={"model_role": role.value},
            )
        return LLMConcurrencySlot(
            global_semaphore=self._global_semaphore,
            role_semaphore=role_semaphore,
        )


__all__ = ["LLMConcurrencyLimiter", "LLMConcurrencySlot"]
