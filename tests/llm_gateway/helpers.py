##################################################################################################
# 文件: tests/llm_gateway/helpers.py
# 作用: 提供 LlmGateway 组件测试使用的配置构造、fake adapter 与异步事件收集工具。
# 边界: 仅服务测试；不连接真实模型代理、不访问网络、不实现业务 AgentRunner。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from veterinary_agent.llm_gateway import (
    LlmCallSummaryDto,
    LlmFinishReason,
    LlmGatewayError,
    LlmInvocationRequestDto,
    LlmMessageDto,
    LlmMessageRole,
    LlmProviderRouteHealthDto,
    LlmStreamEventDto,
    LlmTraceWriteResultDto,
    LlmTraceWriteStatus,
    LlmUsageSummaryDto,
    ProviderInvocationRequestDto,
    ProviderInvocationResponseDto,
    ProviderStreamEventDto,
)
from veterinary_agent.config import (
    LlmGatewaySettings,
    LlmModelCapabilityConfig,
    LlmModelProfileConfig,
    LlmProviderRouteConfig,
    LlmRequiredCapabilityConfig,
    LlmRetryPolicyConfig,
    LlmTimeoutPolicyConfig,
)


def build_test_settings(
    *,
    include_fallback: bool = False,
    max_context_tokens: int = 4096,
    retry_max_attempts: int = 1,
    first_token_timeout_seconds: float = 0.5,
    global_max_concurrency: int = 32,
    profile_max_concurrency: int = 8,
    route_max_concurrency: int = 16,
    concurrency_acquire_timeout_seconds: float = 2.0,
) -> LlmGatewaySettings:
    """构建测试用启用状态 LlmGateway 配置。

    :param include_fallback: 是否加入 fallback profile。
    :param max_context_tokens: 测试路由声明的上下文长度。
    :param retry_max_attempts: 首选 profile 允许的物理调用次数。
    :param first_token_timeout_seconds: 流式调用等待首个有效事件的超时时间。
    :param global_max_concurrency: LlmGateway 实例全局并发上限。
    :param profile_max_concurrency: 单个测试 profile 并发上限。
    :param route_max_concurrency: 单个测试路由并发上限。
    :param concurrency_acquire_timeout_seconds: 等待并发额度的最大时间。
    :return: 可直接创建 DefaultLlmGateway 的测试配置。
    """

    capability = LlmModelCapabilityConfig(
        max_context_tokens=max_context_tokens,
        supports_streaming=True,
        supports_structured_output=True,
        supports_tools=True,
        supports_vision=True,
    )
    primary_route = LlmProviderRouteConfig(
        provider_route_id="route_primary",
        provider_name="test-provider",
        base_url="http://model-proxy.test",
        model_alias="test-primary",
        max_concurrency=route_max_concurrency,
        capability=capability,
    )
    primary_profile = LlmModelProfileConfig(
        model_profile_id="profile_primary",
        profile_version="profile.v1",
        provider_route_id="route_primary",
        required_capability=LlmRequiredCapabilityConfig(streaming=True),
        timeout_policy=LlmTimeoutPolicyConfig(
            connect_timeout_seconds=0.1,
            first_token_timeout_seconds=first_token_timeout_seconds,
            read_timeout_seconds=1.0,
            total_timeout_seconds=2.0,
        ),
        retry_policy=LlmRetryPolicyConfig(
            max_attempts=retry_max_attempts,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            jitter=False,
        ),
        fallback_profile_ids=["profile_fallback"] if include_fallback else [],
        reserved_output_tokens=64,
        max_concurrency=profile_max_concurrency,
    )
    routes = [primary_route]
    profiles = [primary_profile]
    if include_fallback:
        fallback_route = LlmProviderRouteConfig(
            provider_route_id="route_fallback",
            provider_name="test-provider",
            base_url="http://model-proxy.test",
            model_alias="test-fallback",
            max_concurrency=route_max_concurrency,
            capability=capability,
        )
        fallback_profile = LlmModelProfileConfig(
            model_profile_id="profile_fallback",
            profile_version="profile.v1",
            provider_route_id="route_fallback",
            required_capability=LlmRequiredCapabilityConfig(streaming=True),
            timeout_policy=primary_profile.timeout_policy,
            retry_policy=LlmRetryPolicyConfig(max_attempts=1),
            reserved_output_tokens=64,
            max_concurrency=profile_max_concurrency,
        )
        routes.append(fallback_route)
        profiles.append(fallback_profile)
    return LlmGatewaySettings(
        enabled=True,
        max_total_attempts=4,
        max_call_duration_seconds=5.0,
        global_max_concurrency=global_max_concurrency,
        concurrency_acquire_timeout_seconds=concurrency_acquire_timeout_seconds,
        provider_routes=routes,
        model_profiles=profiles,
    )


@dataclass(slots=True)
class FakeProviderAdapter:
    """LlmGateway 测试用 ProviderAdapter。"""

    response: ProviderInvocationResponseDto | None = None
    error: LlmGatewayError | None = None
    invoke_outcomes: list[ProviderInvocationResponseDto | LlmGatewayError] = field(
        default_factory=list
    )
    stream_events: list[ProviderStreamEventDto] = field(default_factory=list)
    stream_error: LlmGatewayError | None = None
    stream_initial_delay_seconds: float = 0.0
    stream_event_delay_seconds: float = 0.0
    health_result: LlmProviderRouteHealthDto | None = None
    ready: bool = True
    invoke_requests: list[ProviderInvocationRequestDto] = field(default_factory=list)
    stream_requests: list[ProviderInvocationRequestDto] = field(default_factory=list)
    close_calls: int = 0

    def is_ready(self) -> bool:
        """判断 fake adapter 是否就绪。

        :return: 当前 fake adapter 的 ready 标记。
        """

        return self.ready

    async def invoke(
        self,
        request: ProviderInvocationRequestDto,
    ) -> ProviderInvocationResponseDto:
        """返回预设响应或抛出预设错误。

        :param request: LlmGateway 传入的物理请求。
        :return: 预设 ProviderInvocationResponseDto。
        :raises LlmGatewayError: 当 error 已设置时抛出。
        """

        self.invoke_requests.append(request)
        if self.invoke_outcomes:
            outcome = self.invoke_outcomes.pop(0)
            if isinstance(outcome, LlmGatewayError):
                raise outcome
            return outcome
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise RuntimeError("FakeProviderAdapter 未配置 invoke 响应")
        return self.response

    async def stream(
        self,
        request: ProviderInvocationRequestDto,
    ) -> AsyncIterator[ProviderStreamEventDto]:
        """按顺序产出预设流式事件。

        :param request: LlmGateway 传入的流式物理请求。
        :return: 预设 ProviderStreamEventDto 异步迭代器。
        :raises LlmGatewayError: 当 stream_error 已设置时在事件后抛出。
        """

        self.stream_requests.append(request)
        if self.stream_initial_delay_seconds > 0:
            await asyncio.sleep(self.stream_initial_delay_seconds)
        for event in self.stream_events:
            if self.stream_event_delay_seconds > 0:
                await asyncio.sleep(self.stream_event_delay_seconds)
            yield event
        if self.stream_error is not None:
            raise self.stream_error

    async def healthcheck(self) -> LlmProviderRouteHealthDto:
        """返回 fake adapter 健康状态。

        :return: 基于 ready 标记的路由健康检查结果。
        """

        if self.health_result is not None:
            return self.health_result
        return LlmProviderRouteHealthDto(
            provider_route_id="fake_route",
            healthy=self.ready,
        )

    async def close(self) -> None:
        """记录 fake adapter 关闭次数。

        :return: None。
        """

        self.close_calls += 1
        self.ready = False


@dataclass(slots=True)
class RecordingTraceStore:
    """测试用模型调用摘要记录器。"""

    status: LlmTraceWriteStatus = LlmTraceWriteStatus.DELIVERED
    summaries: list[LlmCallSummaryDto] = field(default_factory=list)

    def is_ready(self) -> bool:
        """判断测试摘要记录器是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """记录一次模型调用摘要。

        :param summary: LlmGateway 提交的脱敏模型调用摘要。
        :return: 预设写入状态。
        """

        self.summaries.append(summary)
        return LlmTraceWriteResultDto(status=self.status)


@dataclass(slots=True)
class RaisingTraceStore:
    """测试用异常模型调用摘要存储。"""

    exception: Exception

    def is_ready(self) -> bool:
        """判断异常摘要存储是否具备调用条件。

        :return: 固定返回 True，使测试进入真实写入异常分支。
        """

        return True

    async def write_summary(
        self,
        summary: LlmCallSummaryDto,
    ) -> LlmTraceWriteResultDto:
        """抛出预设异常以验证 LlmGateway 留痕降级。

        :param summary: LlmGateway 提交的脱敏模型调用摘要。
        :return: 本方法不会正常返回。
        :raises Exception: 固定抛出初始化时提供的异常。
        """

        del summary
        raise self.exception


async def collect_stream_events(
    events: AsyncIterator[LlmStreamEventDto],
) -> list[LlmStreamEventDto]:
    """收集 LlmGateway 流式事件。

    :param events: LlmGateway 返回的异步流式事件迭代器。
    :return: 按产出顺序收集的事件列表。
    """

    collected: list[LlmStreamEventDto] = []
    async for event in events:
        collected.append(event)
    return collected


def build_invocation_request(
    *,
    content: str = "猫咪呕吐",
    stream: bool = False,
    model_profile_id: str = "profile_primary",
    call_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> LlmInvocationRequestDto:
    """构建 LlmGateway 组件测试使用的标准调用请求。

    :param content: 用户消息文本。
    :param stream: 是否请求流式模型响应。
    :param model_profile_id: 调用方指定的模型 profile ID。
    :param call_id: 可选固定逻辑调用 ID。
    :param metadata: 可选安全调用元数据。
    :return: 可直接传入 LlmGateway 的调用请求。
    """

    return LlmInvocationRequestDto(
        call_id=call_id,
        trace_id="trace_gateway",
        request_id="req_gateway",
        caller_component="AgentRunner",
        model_profile_id=model_profile_id,
        messages=[LlmMessageDto(role=LlmMessageRole.USER, content=content)],
        stream=stream,
        metadata=metadata or {"generation_profile": "standard"},
    )


def build_success_response(
    *,
    content: str = "ok",
    actual_model: str = "test-primary",
) -> ProviderInvocationResponseDto:
    """构建测试用成功 ProviderAdapter 响应。

    :param content: 模型文本内容。
    :param actual_model: 实际模型标识。
    :return: ProviderInvocationResponseDto。
    """

    return ProviderInvocationResponseDto(
        actual_model=actual_model,
        content=content,
        finish_reason=LlmFinishReason.STOP,
        usage=LlmUsageSummaryDto(
            input_tokens=10,
            output_tokens=3,
            total_tokens=13,
        ),
    )


__all__: tuple[str, ...] = (
    "FakeProviderAdapter",
    "RaisingTraceStore",
    "RecordingTraceStore",
    "build_invocation_request",
    "build_success_response",
    "build_test_settings",
    "collect_stream_events",
)
