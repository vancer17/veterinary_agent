"""
=============================================================================
文件：src/vet_agent/agents/task_router.py
作用：提供多任务拆分迁移后的 Agent 层任务路由适配器。
范围：位于记忆读取之后、问诊语义抽取之前；负责将可信请求上下文、用户输入和
      当前 session 活跃任务交给结构化任务路由服务，并返回不可变任务执行计划。
说明：本文件不实现关键词拆分、不访问数据库、不直接调用 OPA 或 LiteLLM；
      具体依赖通过 task_routing 包顶层公共能力注入。
=============================================================================
"""

from __future__ import annotations

from vet_agent.task_routing import (
    ActiveTaskState,
    TaskRoutingDecision,
    TaskRoutingRequestContext,
    TaskRoutingService,
)


class TaskRouterAgent:
    """提供主编排器使用的结构化任务路由 Agent 门面。

    :return: 无返回值；该门面不维护任务状态，只负责调用任务路由服务。
    """

    def __init__(self, service: TaskRoutingService) -> None:
        """初始化任务路由 Agent 门面。

        :param service: 结构化任务路由服务。
        :return: 无返回值。
        """
        self.service = service

    async def route(
        self,
        *,
        context: TaskRoutingRequestContext,
        user_text: str,
        pet_context_summary: str,
        active_tasks: tuple[ActiveTaskState, ...],
        model: str | None = None,
    ) -> TaskRoutingDecision:
        """执行本轮结构化任务路由。

        :param context: 当前回合可信请求范围摘要。
        :param user_text: 当前用户输入原文。
        :param pet_context_summary: 已验证宠物上下文摘要。
        :param active_tasks: 当前 session 活跃任务摘要。
        :param model: 模型名称。
        :return: 返回已通过策略准入的任务路由决策。
        """
        return await self.service.route(
            context=context,
            user_text=user_text,
            pet_context_summary=pet_context_summary,
            active_tasks=active_tasks,
            model=model,
        )

    def is_ready(self) -> bool:
        """检查任务路由 Agent 依赖是否就绪。

        :return: 结构化模型、任务域目录和策略客户端均就绪时返回 True。
        """
        return self.service.is_ready()
