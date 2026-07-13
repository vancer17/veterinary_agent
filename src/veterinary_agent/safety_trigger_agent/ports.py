##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/ports.py
# 作用: 定义 SafetyTriggerAgent 使用的工具权限证明端口与 TODO 空壳。
# 边界: 只声明跨领域依赖契约和显式降级行为，不实现真实 ToolRegistry、RAG 或工具执行。
##################################################################################################

from typing import Protocol

from veterinary_agent.safety_trigger_agent.dto import (
    SafetyRagPolicySummaryDto,
    SafetyTriggerRequestDto,
)

TODO_SAFETY_TOOL_PERMISSION_ERROR_CODE = "SAFETY_TOOL_PERMISSION_NOT_IMPLEMENTED"


class SafetyToolPermissionPort(Protocol):
    """急症链路工具权限证明端口。"""

    def is_ready(self) -> bool:
        """判断工具权限端口是否可用于权限证明。

        :return: 若端口已接入真实 ToolRegistry，则返回 True。
        """

        ...

    async def verify_no_rag_tools(
        self,
        *,
        request: SafetyTriggerRequestDto,
        agent_ids: list[str],
    ) -> SafetyRagPolicySummaryDto:
        """确认当前急症链路没有 RAG 检索工具权限。

        :param request: 当前急症草稿生成请求。
        :param agent_ids: 需要验证的内部 Agent ID 列表。
        :return: RAG 禁用证明摘要。
        """

        ...


class TodoSafetyToolPermissionPort:
    """ToolRegistry 尚未接入时使用的显式 TODO 权限空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 工具权限端口是否就绪。

        :return: 固定返回 False。
        """

        return False

    async def verify_no_rag_tools(
        self,
        *,
        request: SafetyTriggerRequestDto,
        agent_ids: list[str],
    ) -> SafetyRagPolicySummaryDto:
        """返回工具权限未接入的降级摘要。

        :param request: 当前急症草稿生成请求；TODO 空壳不读取业务正文。
        :param agent_ids: 需要验证的内部 Agent ID 列表；TODO 空壳仅记录语义。
        :return: 标记未完成权限证明的 RAG 禁用摘要。
        """

        del request, agent_ids
        return SafetyRagPolicySummaryDto(
            verified=False,
            degraded_reason=TODO_SAFETY_TOOL_PERMISSION_ERROR_CODE,
        )


__all__: tuple[str, ...] = (
    "SafetyToolPermissionPort",
    "TODO_SAFETY_TOOL_PERMISSION_ERROR_CODE",
    "TodoSafetyToolPermissionPort",
)
