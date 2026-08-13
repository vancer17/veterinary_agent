"""
=============================================================================
文件：src/vet_agent/task_routing/ports.py
作用：定义任务路由领域依赖的数据仓储、模型客户端和策略客户端协议。
范围：隔离 PostgreSQL 任务域目录、LiteLLM 结构化模型调用、OPA 策略裁决与测试替身。
说明：业务层依赖本文件中的 Protocol，不直接访问 SQLAlchemy 表模型、httpx 传输细节
      或具体测试替身实现。
=============================================================================
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from .models import TaskRoutingDomainCatalog


StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


class TaskRoutingDomainRepository(Protocol):
    """定义任务路由任务域目录仓储协议。

    :return: 无返回值；实现类需显式继承该协议以便追溯调用栈。
    """

    def task_routing_domains(self) -> TaskRoutingDomainCatalog:
        """读取当前启用的任务路由任务域目录。

        :return: 返回任务域目录。
        """
        ...

    def is_ready(self) -> bool:
        """检查任务域目录仓储是否可访问且存在启用域。

        :return: 仓储可用且至少存在一个启用任务域时返回 True。
        """
        ...


class StructuredChatClient(Protocol):
    """定义任务路由所需的结构化模型客户端协议。

    :return: 无返回值；生产实现由 QwenClient 提供，测试可显式注入替身。
    """

    @property
    def available(self) -> bool:
        """读取结构化模型客户端是否具备运行配置。

        :return: 可调用 LiteLLM 结构化模型接口时返回 True。
        """
        ...

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_model: type[StructuredOutputT],
        model: str | None = None,
        temperature: float = 0.0,
    ) -> StructuredOutputT:
        """执行结构化模型调用并返回 Pydantic 对象。

        :param messages: OpenAI 兼容消息列表。
        :param response_model: 结构化响应 Pydantic 模型。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回通过模型客户端校验的结构化输出。
        """
        ...
