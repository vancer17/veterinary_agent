##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/ports.py
# 作用: 定义 LlmGateway 服务、ProviderAdapter 与模型调用摘要存储的稳定应用内端口。
# 边界: 仅声明协议，不实现具体代理、LogicTraceStore、AgentRunner 或 HTTP 接入。
##################################################################################################

from collections.abc import AsyncIterator
from typing import Protocol

from veterinary_agent.llm_gateway.dto import (
    LlmCallSummaryDto,
    LlmInvocationRequestDto,
    LlmInvocationResultDto,
    LlmModelProfileStatusDto,
    LlmProviderRouteHealthDto,
    LlmStreamEventDto,
    LlmTokenEstimateDto,
    LlmTraceWriteResultDto,
    ProviderInvocationRequestDto,
    ProviderInvocationResponseDto,
    ProviderStreamEventDto,
)


class ProviderAdapter(Protocol):
    """模型供应商或模型代理协议适配器端口。"""

    def is_ready(self) -> bool:
        """判断适配器是否具备执行条件。

        :return: 若适配器本地配置完整且客户端可用，则返回 True。
        """

        ...

    async def invoke(
        self,
        request: ProviderInvocationRequestDto,
    ) -> ProviderInvocationResponseDto:
        """执行一次非流式物理模型调用。

        :param request: 已解析路由和模型别名的物理调用请求。
        :return: 协议无关的模型响应。
        """

        ...

    def stream(
        self,
        request: ProviderInvocationRequestDto,
    ) -> AsyncIterator[ProviderStreamEventDto]:
        """执行一次流式物理模型调用。

        :param request: 已解析路由和模型别名的流式物理调用请求。
        :return: 协议无关的异步模型事件迭代器。
        """

        ...

    async def healthcheck(self) -> LlmProviderRouteHealthDto:
        """检查当前适配器对应的供应商路由健康状态。

        :return: 不包含响应正文的路由健康检查结果。
        """

        ...

    async def close(self) -> None:
        """关闭适配器持有的网络资源。

        :return: None。
        """

        ...


class LlmCallTraceStore(Protocol):
    """模型调用摘要存储端口。"""

    def is_ready(self) -> bool:
        """判断摘要存储端口是否已接入真实实现。

        :return: 若摘要存储端口可正常写入，则返回 True。
        """

        ...

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """写入一次不含 prompt 和 completion 正文的模型调用摘要。

        :param summary: 脱敏模型调用摘要。
        :return: 摘要写入状态。
        """

        ...


class LlmGateway(Protocol):
    """LlmGateway 应用内稳定服务端口。"""

    def is_ready(self) -> bool:
        """判断 LlmGateway 是否具备执行模型调用的条件。

        :return: 若配置启用且至少一个模型 profile 的适配器就绪，则返回 True。
        """

        ...

    async def invoke(
        self,
        request: LlmInvocationRequestDto,
    ) -> LlmInvocationResultDto:
        """执行一次非流式逻辑模型调用。

        :param request: 协议无关模型调用请求。
        :return: 成功的归一化模型调用结果。
        """

        ...

    def stream(
        self,
        request: LlmInvocationRequestDto,
    ) -> AsyncIterator[LlmStreamEventDto]:
        """执行一次流式逻辑模型调用。

        :param request: 协议无关流式模型调用请求。
        :return: 归一化模型流式事件异步迭代器。
        """

        ...

    def estimate_tokens(
        self,
        request: LlmInvocationRequestDto,
    ) -> LlmTokenEstimateDto:
        """估算一次模型调用的上下文预算。

        :param request: 协议无关模型调用请求。
        :return: 输入估算、输出预留和上下文上限。
        """

        ...

    def check_model_profile(
        self,
        model_profile_id: str,
    ) -> LlmModelProfileStatusDto:
        """检查指定模型 profile 静态可用性。

        :param model_profile_id: 需要检查的模型 profile ID。
        :return: profile 版本、路由和适配器可用状态。
        """

        ...

    async def check_provider_route_health(
        self,
        provider_route_id: str,
    ) -> LlmProviderRouteHealthDto:
        """检查指定供应商路由健康状态。

        :param provider_route_id: 需要检查的供应商路由 ID。
        :return: 路由健康检查结果。
        """

        ...

    async def close(self) -> None:
        """关闭 LlmGateway 及其适配器资源。

        :return: None。
        """

        ...


__all__: tuple[str, ...] = (
    "LlmCallTraceStore",
    "LlmGateway",
    "ProviderAdapter",
)
