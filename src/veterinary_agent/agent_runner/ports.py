##################################################################################################
# 文件: src/veterinary_agent/agent_runner/ports.py
# 作用: 定义 AgentRunner 服务、AgentSpecRegistry、工具绑定治理与运行摘要写入的稳定应用内端口。
# 边界: 仅声明协议并提供 TODO 空壳；不实现真实 ToolRegistry、LogicTraceStore、RAG、记忆或业务 Agent。
##################################################################################################

from typing import Protocol

from veterinary_agent.agent_runner.dto import (
    AgentPromptEstimateDto,
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunSummaryDto,
    AgentRunnerTraceWriteResultDto,
    AgentSpecDto,
    AgentToolBindingResultDto,
    AgentValidationErrorDto,
)
from veterinary_agent.agent_runner.enums import (
    AgentRunnerErrorCode,
    AgentRunnerOperation,
    AgentRunnerTraceWriteStatus,
    AgentToolBindingStatus,
)
from veterinary_agent.agent_runner.errors import AgentRunnerError

TODO_AGENT_TOOL_REGISTRY_ERROR_CODE = "AGENT_TOOL_REGISTRY_NOT_IMPLEMENTED"
TODO_AGENT_RUNNER_TRACE_SINK_ERROR_CODE = "AGENT_RUNNER_TRACE_SINK_NOT_IMPLEMENTED"


class AgentSpecRegistry(Protocol):
    """Agent 规格注册表端口。"""

    def is_ready(self) -> bool:
        """判断 Agent 规格注册表是否可用于解析规格。

        :return: 若注册表已初始化并可读取，则返回 True。
        """

        ...

    def resolve_spec(
        self,
        *,
        agent_id: str,
        agent_version: str,
    ) -> AgentSpecDto:
        """解析指定版本的 Agent 规格。

        :param agent_id: Agent ID。
        :param agent_version: Agent 版本。
        :return: 已解析的 Agent 规格。
        :raises AgentRunnerError: 当规格不存在或版本不可用时抛出。
        """

        ...

    def validate_spec(self, spec: AgentSpecDto) -> list[AgentValidationErrorDto]:
        """校验 Agent 规格。

        :param spec: 待校验的 Agent 规格。
        :return: 结构化校验错误列表；空列表表示通过。
        """

        ...

    def list_specs(self) -> list[AgentSpecDto]:
        """列出当前注册表中的 Agent 规格。

        :return: 当前注册表中的 Agent 规格列表。
        """

        ...


class AgentToolRegistry(Protocol):
    """AgentRunner 使用的工具绑定治理端口。"""

    def is_ready(self) -> bool:
        """判断工具注册表是否具备绑定工具的条件。

        :return: 若工具注册表已接入并可解析工具权限，则返回 True。
        """

        ...

    async def build_agent_tool_bindings(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> AgentToolBindingResultDto:
        """构建当前 Agent 可传给模型的授权工具绑定。

        :param request: AgentRunner 本次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: 已授权工具 schema 与绑定摘要。
        :raises AgentRunnerError: 当工具权限拒绝或工具系统不可用时抛出。
        """

        ...


class AgentRunnerTraceSink(Protocol):
    """AgentRunner 运行摘要写入端口。"""

    def is_ready(self) -> bool:
        """判断运行摘要写入端口是否具备写入条件。

        :return: 若写入端口已接入真实实现，则返回 True。
        """

        ...

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """写入一次脱敏 Agent 运行摘要。

        :param summary: AgentRunner 提交的脱敏运行摘要。
        :return: 运行摘要写入状态。
        """

        ...


class AgentRunner(Protocol):
    """AgentRunner 应用内稳定服务端口。"""

    def is_ready(self) -> bool:
        """判断 AgentRunner 是否具备执行一次 Agent 调用的条件。

        :return: 若核心依赖已就绪，则返回 True。
        """

        ...

    async def run_agent(
        self,
        request: AgentRunRequestDto,
    ) -> AgentRunResultDto:
        """执行一次受控 Agent 调用。

        :param request: AgentRunner 单次运行请求。
        :return: 标准化 Agent 运行结果。
        """

        ...

    def estimate_agent_prompt(
        self,
        request: AgentRunRequestDto,
    ) -> AgentPromptEstimateDto:
        """估算一次 Agent prompt 的 token 预算。

        :param request: AgentRunner 单次运行请求。
        :return: prompt token 预算估算结果。
        :raises AgentRunnerError: 当规格、prompt 或模型 profile 不可用时抛出。
        """

        ...

    def validate_agent_spec(
        self,
        spec: AgentSpecDto,
    ) -> list[AgentValidationErrorDto]:
        """校验 Agent 规格。

        :param spec: 待校验的 Agent 规格。
        :return: 结构化校验错误列表；空列表表示通过。
        """

        ...

    async def close(self) -> None:
        """关闭 AgentRunner 持有的本地资源。

        :return: None。
        """

        ...


class TodoAgentToolRegistry:
    """ToolRegistry 尚未实现时使用的显式 TODO 空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO ToolRegistry 是否就绪。

        :return: 固定返回 False。
        """

        return False

    async def build_agent_tool_bindings(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> AgentToolBindingResultDto:
        """构建 TODO 工具绑定结果或拒绝有工具 Agent。

        :param request: AgentRunner 本次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: 无工具 Agent 的空绑定结果。
        :raises AgentRunnerError: 当 Agent 规格声明了工具但真实 ToolRegistry 尚未接入时抛出。
        """

        del request
        if spec.tool_policy.allowed_tools:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.TOOL_EXECUTION_FAILED,
                operation=AgentRunnerOperation.BIND_TOOLS,
                message="ToolRegistry 尚未接入，无法绑定当前 Agent 声明的工具",
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={
                    "reason": "tool_registry_missing",
                    "allowed_tools": spec.tool_policy.allowed_tools,
                },
            )
        return AgentToolBindingResultDto(
            status=AgentToolBindingStatus.SKIPPED,
            tool_schemas=[],
            trace_delivery_status=AgentRunnerTraceWriteStatus.SKIPPED,
        )


class TodoAgentRunnerTraceSink:
    """AgentRunner 运行摘要写入端口尚未实现时使用的显式降级空壳。"""

    def is_ready(self) -> bool:
        """判断 TODO 运行摘要写入端口是否就绪。

        :return: 固定返回 False。
        """

        return False

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """返回 AgentRunner 运行摘要写入降级结果。

        :param summary: AgentRunner 脱敏运行摘要；TODO 空壳不持久化该摘要。
        :return: 表示运行摘要写入端口尚未实现的降级结果。
        """

        del summary
        return AgentRunnerTraceWriteResultDto(
            status=AgentRunnerTraceWriteStatus.DEGRADED,
            error_code=TODO_AGENT_RUNNER_TRACE_SINK_ERROR_CODE,
            retryable=True,
            detail="AgentRunner 运行摘要写入端口尚未接入",
        )


__all__: tuple[str, ...] = (
    "AgentRunner",
    "AgentRunnerTraceSink",
    "AgentSpecRegistry",
    "AgentToolRegistry",
    "TODO_AGENT_RUNNER_TRACE_SINK_ERROR_CODE",
    "TODO_AGENT_TOOL_REGISTRY_ERROR_CODE",
    "TodoAgentRunnerTraceSink",
    "TodoAgentToolRegistry",
)
