##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/gateway.py
# 作用: 实现 LlmGateway 薄控制面，统一 profile 解析、能力检查、token 预算、重试、降级、流式规则与观测。
# 边界: 不构造业务 prompt、不解析业务结构化输出、不执行工具、不做兽医安全判决、不持久化业务状态。
##################################################################################################

import asyncio
from collections import deque
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass, field
from secrets import SystemRandom
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

from veterinary_agent.config import (
    LlmGatewaySettings,
    LlmModelProfileConfig,
)
from veterinary_agent.llm_gateway.concurrency import LlmConcurrencyController
from veterinary_agent.llm_gateway.dto import (
    LlmCallSummaryDto,
    LlmImageContentPartDto,
    LlmInvocationRequestDto,
    LlmInvocationResultDto,
    LlmModelProfileStatusDto,
    LlmProviderRouteHealthDto,
    LlmStreamEventDto,
    LlmTokenEstimateDto,
    LlmTraceWriteResultDto,
    LlmUsageSummaryDto,
    ProviderInvocationRequestDto,
    ProviderInvocationResponseDto,
    ProviderStreamEventDto,
)
from veterinary_agent.llm_gateway.enums import (
    LlmFinishReason,
    LlmGatewayErrorCode,
    LlmGatewayOperation,
    LlmResponseFormatType,
    LlmStreamEventType,
    LlmTraceWriteStatus,
    ProviderStreamEventType,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError
from veterinary_agent.llm_gateway.openai_compatible import (
    OpenAICompatibleAdapterFactory,
)
from veterinary_agent.llm_gateway.ports import (
    LlmCallTraceStore,
    ProviderAdapter,
)
from veterinary_agent.llm_gateway.profile_registry import (
    LlmProfileRegistry,
    ResolvedModelProfile,
)
from veterinary_agent.llm_gateway.token_estimator import ConservativeTokenEstimator
from veterinary_agent.llm_gateway.trace import TodoLlmCallTraceStore
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
)

_COMPONENT_NAME = "LlmGateway"
_JITTER_RANDOM = SystemRandom()


@dataclass(slots=True)
class _LogicalCallState:
    """一次逻辑模型调用的内部状态。"""

    call_id: str
    started_at: float
    deadline: float
    total_attempts: int = 0
    fallback_chain: list[str] = field(default_factory=list)
    actual_profile_id: str | None = None
    provider_route_id: str | None = None
    actual_model: str | None = None
    usage: LlmUsageSummaryDto = field(default_factory=LlmUsageSummaryDto)
    finish_reason: LlmFinishReason | None = None
    first_token_latency_ms: int | None = None

    @property
    def retry_count(self) -> int:
        """计算不含 profile 首次调用的重试次数。

        :return: 物理调用总次数减去实际尝试过的 profile 数量。
        """

        return max(0, self.total_attempts - len(self.fallback_chain))


@dataclass(slots=True)
class _StreamAttemptState:
    """单次流式物理调用的内部状态。"""

    provider_output_observed: bool = False
    event_emitted: bool = False
    actual_model: str | None = None
    usage: LlmUsageSummaryDto = field(default_factory=LlmUsageSummaryDto)
    finish_reason: LlmFinishReason | None = None


def _build_call_id() -> str:
    """生成逻辑模型调用 ID。

    :return: 带 ``llm_`` 前缀的随机调用 ID。
    """

    return f"llm_{uuid4().hex}"


def _elapsed_ms(started_at: float) -> int:
    """计算从指定单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


class DefaultLlmGateway:
    """应用内 LlmGateway 默认实现。"""

    def __init__(
        self,
        *,
        settings: LlmGatewaySettings,
        adapters_by_profile: Mapping[str, ProviderAdapter],
        observability_provider: ObservabilityProvider | None = None,
        trace_store: LlmCallTraceStore | None = None,
        config_snapshot_id: str | None = None,
    ) -> None:
        """初始化 LlmGateway 默认实现。

        :param settings: 已校验的 LlmGateway RuntimeConfig。
        :param adapters_by_profile: 按模型 profile ID 注册的 ProviderAdapter。
        :param observability_provider: 可选项目 Observability provider。
        :param trace_store: 可选模型调用摘要存储端口；未传入时使用 TODO 空壳。
        :param config_snapshot_id: 可选 RuntimeConfig 快照 ID。
        :return: None。
        """

        self._settings = settings
        self._registry = LlmProfileRegistry(settings=settings)
        self._adapters_by_profile = dict(adapters_by_profile)
        self._observability = observability_provider
        self._trace_store = trace_store or TodoLlmCallTraceStore()
        self._config_snapshot_id = config_snapshot_id
        self._token_estimator = ConservativeTokenEstimator(
            settings=settings.token_estimation
        )
        self._concurrency = LlmConcurrencyController(settings=settings)
        self._closed = False

    def is_ready(self) -> bool:
        """判断 LlmGateway 是否具备执行模型调用的条件。

        :return: 若组件启用、注册表可用且至少一个 profile 适配器就绪，则返回 True。
        """

        if self._closed or not self._settings.enabled or not self._registry.is_ready():
            return False
        for profile in self._settings.model_profiles:
            adapter = self._adapters_by_profile.get(profile.model_profile_id)
            if adapter is not None and adapter.is_ready():
                return True
        return False

    def check_model_profile(
        self,
        model_profile_id: str,
    ) -> LlmModelProfileStatusDto:
        """检查指定模型 profile 静态可用性。

        :param model_profile_id: 需要检查的模型 profile ID。
        :return: profile 版本、路由和适配器可用状态。
        :raises LlmGatewayError: 当 profile 不存在时抛出。
        """

        resolved = self._registry.resolve_profile(model_profile_id)
        adapter = self._adapters_by_profile.get(model_profile_id)
        available = (
            self._settings.enabled
            and not self._closed
            and adapter is not None
            and adapter.is_ready()
        )
        reason = None
        if not self._settings.enabled:
            reason = "gateway_disabled"
        elif self._closed:
            reason = "gateway_closed"
        elif adapter is None:
            reason = "adapter_missing"
        elif not adapter.is_ready():
            reason = "adapter_not_ready"
        return LlmModelProfileStatusDto(
            model_profile_id=resolved.profile.model_profile_id,
            profile_version=resolved.profile.profile_version,
            provider_route_id=resolved.route.provider_route_id,
            available=available,
            reason=reason,
        )

    def estimate_tokens(
        self,
        request: LlmInvocationRequestDto,
    ) -> LlmTokenEstimateDto:
        """估算一次模型调用的上下文预算。

        :param request: 协议无关模型调用请求。
        :return: 输入估算、输出预留和上下文上限。
        :raises LlmGatewayError: 当 profile 不存在、能力不匹配或输出上限非法时抛出。
        """

        resolved = self._registry.resolve_profile(request.model_profile_id)
        self._validate_request_capability(request=request, resolved=resolved)
        return self._token_estimator.estimate(
            request=request,
            profile=resolved.profile,
            route=resolved.route,
        )

    async def invoke(
        self,
        request: LlmInvocationRequestDto,
    ) -> LlmInvocationResultDto:
        """执行一次非流式逻辑模型调用。

        :param request: 协议无关非流式模型调用请求。
        :return: 成功的归一化模型调用结果。
        :raises LlmGatewayError: 当组件未就绪、调用失败或重试与降级耗尽时抛出。
        """

        normalized_request = self._prepare_request(
            request=request,
            expected_stream=False,
        )
        state = self._new_call_state(call_id=cast(str, normalized_request.call_id))
        try:
            result = await self._invoke_with_policies(
                request=normalized_request,
                state=state,
            )
        except asyncio.CancelledError:
            await self._finalize_cancelled_call(
                request=normalized_request,
                state=state,
            )
            raise
        except LlmGatewayError as error:
            contextual_error = self._with_request_context(
                error=error,
                request=normalized_request,
                state=state,
            )
            trace_result = await self._write_summary(
                request=normalized_request,
                state=state,
                status="failed",
                error=contextual_error,
            )
            self._record_observability(
                request=normalized_request,
                state=state,
                status="failed",
                error=contextual_error,
                trace_result=trace_result,
            )
            raise contextual_error from error
        trace_result = await self._write_summary(
            request=normalized_request,
            state=state,
            status="succeeded",
            error=None,
        )
        self._record_observability(
            request=normalized_request,
            state=state,
            status="succeeded",
            error=None,
            trace_result=trace_result,
        )
        return result.model_copy(update={"trace_write_status": trace_result.status})

    async def _invoke_with_policies(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
    ) -> LlmInvocationResultDto:
        """按 profile 重试和降级策略执行非流式调用。

        :param request: 已补齐调用 ID 的非流式请求。
        :param state: 当前逻辑调用状态。
        :return: 成功的归一化模型调用结果。
        :raises LlmGatewayError: 当全部候选 profile 调用失败时抛出。
        """

        pending_profiles: deque[str] = deque([request.model_profile_id])
        queued_profiles: set[str] = {request.model_profile_id}
        last_error: LlmGatewayError | None = None
        while (
            pending_profiles
            and state.total_attempts < self._settings.max_total_attempts
        ):
            profile_id = pending_profiles.popleft()
            if profile_id not in state.fallback_chain:
                state.fallback_chain.append(profile_id)
            resolved = self._registry.resolve_profile(profile_id)
            self._validate_request_capability(request=request, resolved=resolved)
            self._token_estimator.ensure_within_context(
                request=request,
                profile=resolved.profile,
                route=resolved.route,
            )
            try:
                response = await self._invoke_profile(
                    request=request,
                    resolved=resolved,
                    state=state,
                )
            except LlmGatewayError as error:
                last_error = error
                if self._should_fallback(
                    profile=resolved.profile,
                    error=error,
                ):
                    self._enqueue_fallback_profiles(
                        profile=resolved.profile,
                        pending_profiles=pending_profiles,
                        queued_profiles=queued_profiles,
                    )
                continue
            state.actual_profile_id = resolved.profile.model_profile_id
            state.provider_route_id = resolved.route.provider_route_id
            state.actual_model = response.actual_model
            state.usage = response.usage
            state.finish_reason = response.finish_reason
            return LlmInvocationResultDto(
                call_id=state.call_id,
                model_profile_id=request.model_profile_id,
                actual_profile_id=resolved.profile.model_profile_id,
                provider_route_id=resolved.route.provider_route_id,
                actual_model=response.actual_model,
                content=response.content,
                tool_calls=response.tool_calls,
                finish_reason=response.finish_reason,
                usage=response.usage,
                latency_ms=_elapsed_ms(state.started_at),
                retry_count=state.retry_count,
                fallback_used=len(state.fallback_chain) > 1,
                fallback_chain=list(state.fallback_chain),
                trace_write_status=LlmTraceWriteStatus.SKIPPED,
                normalized_error=None,
            )
        if last_error is not None:
            raise self._resolve_final_policy_error(
                request=request,
                state=state,
                cause=last_error,
            )
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="没有可执行的模型 profile",
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=request.model_profile_id,
        )

    async def _invoke_profile(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
    ) -> ProviderInvocationResponseDto:
        """在单个 profile 内执行有限重试。

        :param request: 已补齐调用 ID 的非流式请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :return: ProviderAdapter 非流式归一化响应。
        :raises LlmGatewayError: 当当前 profile 调用失败或重试耗尽时抛出。
        """

        adapter = self._adapter_for_profile(
            model_profile_id=resolved.profile.model_profile_id,
            operation=LlmGatewayOperation.INVOKE_LLM,
        )
        last_error: LlmGatewayError | None = None
        attempts_for_profile = 0
        while (
            attempts_for_profile < resolved.profile.retry_policy.max_attempts
            and state.total_attempts < self._settings.max_total_attempts
        ):
            attempts_for_profile += 1
            state.total_attempts += 1
            try:
                response = await self._invoke_physical(
                    adapter=adapter,
                    request=request,
                    resolved=resolved,
                    state=state,
                )
                return response
            except LlmGatewayError as error:
                contextual_error = self._with_profile_context(
                    error=error,
                    request=request,
                    resolved=resolved,
                    state=state,
                )
                last_error = contextual_error
                if not self._can_retry_profile(
                    profile=resolved.profile,
                    error=contextual_error,
                    attempts_for_profile=attempts_for_profile,
                    state=state,
                ):
                    break
                await self._sleep_before_retry(
                    profile=resolved.profile,
                    attempts_for_profile=attempts_for_profile,
                    state=state,
                )
        if last_error is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="当前模型 profile 未执行任何物理调用",
                call_id=state.call_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                model_profile_id=resolved.profile.model_profile_id,
                provider_route_id=resolved.route.provider_route_id,
            )
        if attempts_for_profile > 1:
            raise self._build_retry_exhausted_error(
                request=request,
                state=state,
                cause=last_error,
                model_profile_id=resolved.profile.model_profile_id,
                provider_route_id=resolved.route.provider_route_id,
            )
        raise last_error

    async def _invoke_physical(
        self,
        *,
        adapter: ProviderAdapter,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
    ) -> ProviderInvocationResponseDto:
        """执行一次受并发和 deadline 控制的非流式物理调用。

        :param adapter: 当前 profile 使用的供应商适配器。
        :param request: 已补齐调用 ID 的非流式请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :return: ProviderAdapter 非流式归一化响应。
        :raises LlmGatewayError: 当并发等待、物理请求或总 deadline 失败时抛出。
        """

        operation = LlmGatewayOperation.INVOKE_LLM
        lease = await self._concurrency.acquire(
            operation=operation,
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=resolved.profile.model_profile_id,
            provider_route_id=resolved.route.provider_route_id,
        )
        async with lease:
            timeout_seconds = self._remaining_attempt_timeout(
                profile=resolved.profile,
                state=state,
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    return await adapter.invoke(
                        self._build_provider_request(
                            request=request,
                            resolved=resolved,
                        )
                    )
            except TimeoutError as exc:
                raise LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_TIMEOUT,
                    operation=operation,
                    message="模型物理调用超过总超时时间",
                    call_id=state.call_id,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    model_profile_id=resolved.profile.model_profile_id,
                    provider_route_id=resolved.route.provider_route_id,
                ) from exc

    def stream(
        self,
        request: LlmInvocationRequestDto,
    ) -> AsyncIterator[LlmStreamEventDto]:
        """执行一次流式逻辑模型调用。

        :param request: 协议无关流式模型调用请求。
        :return: 归一化模型流式事件异步迭代器。
        """

        normalized_request = self._prepare_request(
            request=request,
            expected_stream=True,
        )
        return self._stream_events(request=normalized_request)

    async def _stream_events(
        self,
        *,
        request: LlmInvocationRequestDto,
    ) -> AsyncGenerator[LlmStreamEventDto, None]:
        """生成一次流式逻辑模型调用的标准事件。

        :param request: 已补齐调用 ID 的流式请求。
        :return: 标准模型流式事件异步生成器。
        """

        state = self._new_call_state(call_id=cast(str, request.call_id))
        finalized = False
        yield LlmStreamEventDto(
            call_id=state.call_id,
            event_type=LlmStreamEventType.STARTED,
            model_profile_id=request.model_profile_id,
            latency_ms=0,
        )
        try:
            pending_profiles: deque[str] = deque([request.model_profile_id])
            queued_profiles: set[str] = {request.model_profile_id}
            last_error: LlmGatewayError | None = None
            while (
                pending_profiles
                and state.total_attempts < self._settings.max_total_attempts
            ):
                profile_id = pending_profiles.popleft()
                if profile_id not in state.fallback_chain:
                    state.fallback_chain.append(profile_id)
                resolved = self._registry.resolve_profile(profile_id)
                self._validate_request_capability(request=request, resolved=resolved)
                self._token_estimator.ensure_within_context(
                    request=request,
                    profile=resolved.profile,
                    route=resolved.route,
                )
                attempts_for_profile = 0
                while (
                    attempts_for_profile < resolved.profile.retry_policy.max_attempts
                    and state.total_attempts < self._settings.max_total_attempts
                ):
                    attempts_for_profile += 1
                    state.total_attempts += 1
                    attempt_state = _StreamAttemptState()
                    try:
                        async for event in self._stream_physical(
                            request=request,
                            resolved=resolved,
                            state=state,
                            attempt_state=attempt_state,
                        ):
                            yield event
                    except LlmGatewayError as error:
                        contextual_error = self._with_profile_context(
                            error=error,
                            request=request,
                            resolved=resolved,
                            state=state,
                        )
                        last_error = contextual_error
                        if attempt_state.event_emitted:
                            finalized = True
                            async for error_event in self._finalize_stream_error(
                                request=request,
                                state=state,
                                error=contextual_error,
                            ):
                                yield error_event
                            return
                        if self._can_retry_profile(
                            profile=resolved.profile,
                            error=contextual_error,
                            attempts_for_profile=attempts_for_profile,
                            state=state,
                        ):
                            await self._sleep_before_retry(
                                profile=resolved.profile,
                                attempts_for_profile=attempts_for_profile,
                                state=state,
                            )
                            continue
                        if attempts_for_profile > 1:
                            last_error = self._build_retry_exhausted_error(
                                request=request,
                                state=state,
                                cause=contextual_error,
                                model_profile_id=resolved.profile.model_profile_id,
                                provider_route_id=resolved.route.provider_route_id,
                            )
                        break
                    state.actual_profile_id = resolved.profile.model_profile_id
                    state.provider_route_id = resolved.route.provider_route_id
                    state.actual_model = (
                        attempt_state.actual_model or resolved.route.model_alias
                    )
                    state.usage = attempt_state.usage
                    state.finish_reason = (
                        attempt_state.finish_reason or LlmFinishReason.UNKNOWN
                    )
                    trace_result = await self._write_summary(
                        request=request,
                        state=state,
                        status="succeeded",
                        error=None,
                    )
                    self._record_observability(
                        request=request,
                        state=state,
                        status="succeeded",
                        error=None,
                        trace_result=trace_result,
                    )
                    finalized = True
                    yield LlmStreamEventDto(
                        call_id=state.call_id,
                        event_type=LlmStreamEventType.COMPLETED,
                        model_profile_id=request.model_profile_id,
                        actual_profile_id=state.actual_profile_id,
                        provider_route_id=state.provider_route_id,
                        actual_model=state.actual_model,
                        finish_reason=state.finish_reason,
                        usage=state.usage,
                        retry_count=state.retry_count,
                        fallback_chain=list(state.fallback_chain),
                        latency_ms=_elapsed_ms(state.started_at),
                        first_token_latency_ms=state.first_token_latency_ms,
                        trace_write_status=trace_result.status,
                    )
                    return
                if last_error is not None and self._should_fallback(
                    profile=resolved.profile,
                    error=last_error,
                ):
                    self._enqueue_fallback_profiles(
                        profile=resolved.profile,
                        pending_profiles=pending_profiles,
                        queued_profiles=queued_profiles,
                    )
            final_error = (
                self._resolve_final_policy_error(
                    request=request,
                    state=state,
                    cause=last_error,
                )
                if last_error is not None
                else LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                    operation=LlmGatewayOperation.STREAM_LLM,
                    message="没有可执行的流式模型 profile",
                )
            )
            finalized = True
            async for error_event in self._finalize_stream_error(
                request=request,
                state=state,
                error=final_error,
            ):
                yield error_event
        except (asyncio.CancelledError, GeneratorExit):
            if not finalized:
                await self._finalize_cancelled_call(request=request, state=state)
            raise
        except LlmGatewayError as error:
            contextual_error = self._with_request_context(
                error=error,
                request=request,
                state=state,
            )
            finalized = True
            async for error_event in self._finalize_stream_error(
                request=request,
                state=state,
                error=contextual_error,
            ):
                yield error_event

    async def _stream_physical(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
        attempt_state: _StreamAttemptState,
    ) -> AsyncGenerator[LlmStreamEventDto, None]:
        """执行一次受控流式物理调用并转发增量事件。

        :param request: 已补齐调用 ID 的流式请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :param attempt_state: 当前流式物理调用状态。
        :return: 文本、工具调用和 usage 标准事件异步生成器。
        :raises LlmGatewayError: 当首事件超时、总超时或流式响应非法时抛出。
        """

        adapter = self._adapter_for_profile(
            model_profile_id=resolved.profile.model_profile_id,
            operation=LlmGatewayOperation.STREAM_LLM,
        )
        lease = await self._concurrency.acquire(
            operation=LlmGatewayOperation.STREAM_LLM,
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=resolved.profile.model_profile_id,
            provider_route_id=resolved.route.provider_route_id,
        )
        async with lease:
            attempt_started_at = perf_counter()
            first_event_deadline = (
                attempt_started_at
                + resolved.profile.timeout_policy.first_token_timeout_seconds
            )
            attempt_deadline = min(
                state.deadline,
                attempt_started_at
                + resolved.profile.timeout_policy.total_timeout_seconds,
            )
            raw_iterator = adapter.stream(
                self._build_provider_request(
                    request=request,
                    resolved=resolved,
                )
            )
            iterator = raw_iterator
            try:
                while True:
                    wait_deadline = (
                        attempt_deadline
                        if attempt_state.provider_output_observed
                        else min(attempt_deadline, first_event_deadline)
                    )
                    remaining = wait_deadline - perf_counter()
                    if remaining <= 0:
                        raise self._build_stream_timeout_error(
                            request=request,
                            resolved=resolved,
                            state=state,
                            first_event=not attempt_state.provider_output_observed,
                        )
                    try:
                        async with asyncio.timeout(remaining):
                            provider_event = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise self._build_stream_timeout_error(
                            request=request,
                            resolved=resolved,
                            state=state,
                            first_event=not attempt_state.provider_output_observed,
                        ) from exc
                    events = self._normalize_provider_stream_event(
                        request=request,
                        resolved=resolved,
                        state=state,
                        attempt_state=attempt_state,
                        provider_event=provider_event,
                    )
                    for event in events:
                        attempt_state.event_emitted = True
                        yield event
            finally:
                close_method = getattr(iterator, "aclose", None)
                if close_method is not None:
                    await cast(Callable[[], Awaitable[None]], close_method)()
            if not attempt_state.provider_output_observed:
                raise LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                    operation=LlmGatewayOperation.STREAM_LLM,
                    message="模型代理流结束前未返回有效事件",
                )
            if attempt_state.finish_reason is None:
                raise LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                    operation=LlmGatewayOperation.STREAM_LLM,
                    message="模型代理流结束前未返回完成原因",
                )

    def _normalize_provider_stream_event(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
        attempt_state: _StreamAttemptState,
        provider_event: ProviderStreamEventDto,
    ) -> list[LlmStreamEventDto]:
        """转换单个 ProviderAdapter 事件为零个或多个标准流式事件。

        :param request: 已补齐调用 ID 的流式请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :param attempt_state: 当前流式物理调用状态。
        :param provider_event: ProviderAdapter 流式事件。
        :return: 需要向调用方发布的标准流式事件列表。
        """

        events: list[LlmStreamEventDto] = []
        if provider_event.actual_model is not None:
            attempt_state.actual_model = provider_event.actual_model
        if provider_event.usage is not None:
            attempt_state.usage = provider_event.usage
        if provider_event.finish_reason is not None:
            attempt_state.finish_reason = provider_event.finish_reason
        meaningful = bool(
            provider_event.delta
            or provider_event.tool_call_deltas
            or provider_event.usage is not None
            or provider_event.finish_reason is not None
        )
        if meaningful and not attempt_state.provider_output_observed:
            attempt_state.provider_output_observed = True
            state.first_token_latency_ms = _elapsed_ms(state.started_at)
        common_fields = {
            "call_id": state.call_id,
            "model_profile_id": request.model_profile_id,
            "actual_profile_id": resolved.profile.model_profile_id,
            "provider_route_id": resolved.route.provider_route_id,
            "actual_model": (provider_event.actual_model or attempt_state.actual_model),
            "retry_count": state.retry_count,
            "fallback_chain": list(state.fallback_chain),
            "first_token_latency_ms": state.first_token_latency_ms,
        }
        if (
            provider_event.event_type is ProviderStreamEventType.DELTA
            and provider_event.delta
        ):
            events.append(
                LlmStreamEventDto(
                    **common_fields,
                    event_type=LlmStreamEventType.DELTA,
                    delta=provider_event.delta,
                )
            )
        if provider_event.tool_call_deltas:
            events.append(
                LlmStreamEventDto(
                    **common_fields,
                    event_type=LlmStreamEventType.TOOL_CALL_DELTA,
                    tool_call_deltas=provider_event.tool_call_deltas,
                )
            )
        if (
            provider_event.event_type is ProviderStreamEventType.USAGE
            and provider_event.usage is not None
        ):
            events.append(
                LlmStreamEventDto(
                    **common_fields,
                    event_type=LlmStreamEventType.USAGE,
                    usage=provider_event.usage,
                )
            )
        return events

    async def _finalize_stream_error(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
        error: LlmGatewayError,
    ) -> AsyncGenerator[LlmStreamEventDto, None]:
        """写入失败摘要、记录观测并生成流式错误事件。

        :param request: 已补齐调用 ID 的流式请求。
        :param state: 当前逻辑调用状态。
        :param error: 当前最终 LlmGateway 错误。
        :return: 仅包含一个标准错误事件的异步生成器。
        """

        contextual_error = self._with_request_context(
            error=error,
            request=request,
            state=state,
        )
        trace_result = await self._write_summary(
            request=request,
            state=state,
            status="failed",
            error=contextual_error,
        )
        self._record_observability(
            request=request,
            state=state,
            status="failed",
            error=contextual_error,
            trace_result=trace_result,
        )
        yield LlmStreamEventDto(
            call_id=state.call_id,
            event_type=LlmStreamEventType.ERROR,
            model_profile_id=request.model_profile_id,
            actual_profile_id=state.actual_profile_id,
            provider_route_id=state.provider_route_id,
            actual_model=state.actual_model,
            retry_count=state.retry_count,
            fallback_chain=list(state.fallback_chain),
            latency_ms=_elapsed_ms(state.started_at),
            first_token_latency_ms=state.first_token_latency_ms,
            trace_write_status=trace_result.status,
            normalized_error=contextual_error.to_dto(),
        )

    async def check_provider_route_health(
        self,
        provider_route_id: str,
    ) -> LlmProviderRouteHealthDto:
        """检查指定供应商路由健康状态。

        :param provider_route_id: 需要检查的供应商路由 ID。
        :return: 路由健康检查结果。
        :raises LlmGatewayError: 当路由不存在或没有关联适配器时抛出。
        """

        route = self._registry.resolve_route(provider_route_id)
        profile = next(
            (
                item
                for item in self._settings.model_profiles
                if item.provider_route_id == route.provider_route_id
            ),
            None,
        )
        if profile is None:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.CHECK_PROVIDER_ROUTE_HEALTH,
                message="供应商路由没有关联模型 profile",
                provider_route_id=provider_route_id,
            )
        adapter = self._adapter_for_profile(
            model_profile_id=profile.model_profile_id,
            operation=LlmGatewayOperation.CHECK_PROVIDER_ROUTE_HEALTH,
        )
        return await adapter.healthcheck()

    async def close(self) -> None:
        """关闭 LlmGateway 持有的全部适配器资源。

        :return: None。
        """

        if self._closed:
            return
        seen_adapter_ids: set[int] = set()
        for adapter in self._adapters_by_profile.values():
            adapter_id = id(adapter)
            if adapter_id in seen_adapter_ids:
                continue
            seen_adapter_ids.add(adapter_id)
            await adapter.close()
        self._closed = True

    def _prepare_request(
        self,
        *,
        request: LlmInvocationRequestDto,
        expected_stream: bool,
    ) -> LlmInvocationRequestDto:
        """校验调用方式、组件就绪状态并补齐调用 ID。

        :param request: 调用方提供的模型调用请求。
        :param expected_stream: 当前入口是否要求流式请求。
        :return: 已补齐稳定调用 ID 的请求副本。
        :raises LlmGatewayError: 当组件未就绪或 stream 标记与入口不一致时抛出。
        """

        if not self.is_ready():
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_GATEWAY_NOT_READY,
                operation=(
                    LlmGatewayOperation.STREAM_LLM
                    if expected_stream
                    else LlmGatewayOperation.INVOKE_LLM
                ),
                message="LlmGateway 尚未启用或未完成适配器装配",
                call_id=request.call_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                model_profile_id=request.model_profile_id,
            )
        if request.stream is not expected_stream:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_INVALID_REQUEST,
                operation=(
                    LlmGatewayOperation.STREAM_LLM
                    if expected_stream
                    else LlmGatewayOperation.INVOKE_LLM
                ),
                message="调用入口与 request.stream 标记不一致",
                call_id=request.call_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                model_profile_id=request.model_profile_id,
            )
        return request.model_copy(
            update={"call_id": request.call_id or _build_call_id()}
        )

    def _new_call_state(self, *, call_id: str) -> _LogicalCallState:
        """创建一次逻辑模型调用状态。

        :param call_id: 稳定逻辑模型调用 ID。
        :return: 已设置总 deadline 的调用状态。
        """

        started_at = perf_counter()
        return _LogicalCallState(
            call_id=call_id,
            started_at=started_at,
            deadline=started_at + self._settings.max_call_duration_seconds,
        )

    def _validate_request_capability(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
    ) -> None:
        """校验当前请求所需能力不超过候选路由能力。

        :param request: 协议无关模型调用请求。
        :param resolved: 当前候选 profile 与路由。
        :return: None。
        :raises LlmGatewayError: 当流式、结构化、工具或视觉能力不满足时抛出。
        """

        capability = resolved.route.capability
        mismatches: list[str] = []
        if request.stream and not capability.supports_streaming:
            mismatches.append("streaming")
        if (
            request.response_format.type is not LlmResponseFormatType.TEXT
            and not capability.supports_structured_output
        ):
            mismatches.append("structured_output")
        if request.tool_schemas and not capability.supports_tools:
            mismatches.append("tools")
        if (
            self._request_contains_image(request=request)
            and not capability.supports_vision
        ):
            mismatches.append("vision")
        if mismatches:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_CAPABILITY_MISMATCH,
                operation=(
                    LlmGatewayOperation.STREAM_LLM
                    if request.stream
                    else LlmGatewayOperation.INVOKE_LLM
                ),
                message="模型路由能力不满足当前调用请求",
                call_id=request.call_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                model_profile_id=resolved.profile.model_profile_id,
                provider_route_id=resolved.route.provider_route_id,
                conflict_with={"missing_capabilities": mismatches},
            )

    def _request_contains_image(self, *, request: LlmInvocationRequestDto) -> bool:
        """判断模型请求是否包含视觉内容。

        :param request: 协议无关模型调用请求。
        :return: 若任一消息包含图片内容分片，则返回 True。
        """

        for message in request.messages:
            if isinstance(message.content, list) and any(
                isinstance(content_part, LlmImageContentPartDto)
                for content_part in message.content
            ):
                return True
        return False

    def _adapter_for_profile(
        self,
        *,
        model_profile_id: str,
        operation: LlmGatewayOperation,
    ) -> ProviderAdapter:
        """读取指定 profile 的供应商适配器。

        :param model_profile_id: 当前候选模型 profile ID。
        :param operation: 当前 LlmGateway 操作名。
        :return: 已装配且就绪的 ProviderAdapter。
        :raises LlmGatewayError: 当适配器缺失或未就绪时抛出。
        """

        adapter = self._adapters_by_profile.get(model_profile_id)
        if adapter is None or not adapter.is_ready():
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=operation,
                message="模型 profile 的供应商适配器不可用",
                model_profile_id=model_profile_id,
            )
        return adapter

    def _build_provider_request(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
    ) -> ProviderInvocationRequestDto:
        """构建 ProviderAdapter 物理调用请求。

        :param request: 已补齐调用 ID 的逻辑模型请求。
        :param resolved: 当前候选 profile 与路由。
        :return: 已替换模型别名的物理调用请求。
        """

        return ProviderInvocationRequestDto(
            call_id=cast(str, request.call_id),
            trace_id=request.trace_id,
            request_id=request.request_id,
            caller_component=request.caller_component,
            model_alias=resolved.route.model_alias,
            messages=request.messages,
            response_format=request.response_format,
            tool_schemas=request.tool_schemas,
            stream=request.stream,
            generation_params=request.generation_params,
        )

    def _remaining_attempt_timeout(
        self,
        *,
        profile: LlmModelProfileConfig,
        state: _LogicalCallState,
    ) -> float:
        """计算当前物理调用可使用的剩余超时时间。

        :param profile: 当前候选模型 profile。
        :param state: 当前逻辑调用状态。
        :return: profile 总超时与逻辑调用剩余时限的较小值。
        :raises LlmGatewayError: 当逻辑调用总时限已经耗尽时抛出。
        """

        remaining = state.deadline - perf_counter()
        if remaining <= 0:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_TIMEOUT,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="逻辑模型调用超过总时限",
                call_id=state.call_id,
            )
        return min(profile.timeout_policy.total_timeout_seconds, remaining)

    def _build_stream_timeout_error(
        self,
        *,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
        first_event: bool,
    ) -> LlmGatewayError:
        """构建流式首事件或总读取超时错误。

        :param request: 已补齐调用 ID 的流式请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :param first_event: 是否在首个有效模型事件之前超时。
        :return: 带完整调用上下文的 LlmGateway 超时错误。
        """

        return LlmGatewayError(
            code=(
                LlmGatewayErrorCode.LLM_FIRST_TOKEN_TIMEOUT
                if first_event
                else LlmGatewayErrorCode.LLM_TIMEOUT
            ),
            operation=LlmGatewayOperation.STREAM_LLM,
            message=(
                "流式模型调用等待首个有效事件超时"
                if first_event
                else "流式模型调用超过总时限"
            ),
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=resolved.profile.model_profile_id,
            provider_route_id=resolved.route.provider_route_id,
        )

    def _can_retry_profile(
        self,
        *,
        profile: LlmModelProfileConfig,
        error: LlmGatewayError,
        attempts_for_profile: int,
        state: _LogicalCallState,
    ) -> bool:
        """判断当前 profile 是否可以继续执行物理重试。

        :param profile: 当前候选模型 profile。
        :param error: 最近一次物理调用错误。
        :param attempts_for_profile: 当前 profile 已执行的物理调用次数。
        :param state: 当前逻辑调用状态。
        :return: 若错误允许重试且 profile、全局尝试和总时限均有余量，则返回 True。
        """

        return (
            error.retryable
            and error.code.value in profile.retry_policy.retryable_error_codes
            and attempts_for_profile < profile.retry_policy.max_attempts
            and state.total_attempts < self._settings.max_total_attempts
            and perf_counter() < state.deadline
        )

    async def _sleep_before_retry(
        self,
        *,
        profile: LlmModelProfileConfig,
        attempts_for_profile: int,
        state: _LogicalCallState,
    ) -> None:
        """按 profile 指数退避策略等待下一次重试。

        :param profile: 当前候选模型 profile。
        :param attempts_for_profile: 当前 profile 已执行的物理调用次数。
        :param state: 当前逻辑调用状态。
        :return: None。
        """

        policy = profile.retry_policy
        delay = min(
            policy.max_backoff_seconds,
            policy.initial_backoff_seconds
            * (policy.backoff_factor ** max(0, attempts_for_profile - 1)),
        )
        if policy.jitter and delay > 0:
            delay = _JITTER_RANDOM.uniform(0, delay)
        remaining = max(0.0, state.deadline - perf_counter())
        if delay > 0 and remaining > 0:
            await asyncio.sleep(min(delay, remaining))

    def _should_fallback(
        self,
        *,
        profile: LlmModelProfileConfig,
        error: LlmGatewayError,
    ) -> bool:
        """判断当前 profile 错误是否允许触发语义降级。

        :param profile: 当前失败的模型 profile。
        :param error: 当前 profile 最终错误。
        :return: 若 profile 声明备用项且错误码允许降级，则返回 True。
        """

        return bool(
            profile.fallback_profile_ids
            and error.code.value in profile.fallback_on_error_codes
        )

    def _enqueue_fallback_profiles(
        self,
        *,
        profile: LlmModelProfileConfig,
        pending_profiles: deque[str],
        queued_profiles: set[str],
    ) -> None:
        """将尚未尝试的直接备用 profile 加入候选队列。

        :param profile: 当前失败的来源模型 profile。
        :param pending_profiles: 等待尝试的模型 profile 队列。
        :param queued_profiles: 已入队或已尝试的模型 profile 集合。
        :return: None。
        """

        for fallback_profile_id in profile.fallback_profile_ids:
            if fallback_profile_id in queued_profiles:
                continue
            pending_profiles.append(fallback_profile_id)
            queued_profiles.add(fallback_profile_id)

    def _build_retry_exhausted_error(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
        cause: LlmGatewayError | None,
        model_profile_id: str | None = None,
        provider_route_id: str | None = None,
    ) -> LlmGatewayError:
        """构建重试与降级耗尽错误。

        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :param cause: 最近一次标准错误。
        :param model_profile_id: 可选最终失败 profile ID。
        :param provider_route_id: 可选最终失败路由 ID。
        :return: 带最近错误码摘要的重试耗尽异常。
        """

        conflict_with = {
            "attempt_count": state.total_attempts,
            "fallback_chain": list(state.fallback_chain),
        }
        if cause is not None:
            conflict_with["last_error_code"] = cause.code.value
        return LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_RETRY_EXHAUSTED,
            operation=(
                LlmGatewayOperation.STREAM_LLM
                if request.stream
                else LlmGatewayOperation.INVOKE_LLM
            ),
            message="模型调用重试与降级策略已经耗尽",
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=model_profile_id or state.actual_profile_id,
            provider_route_id=provider_route_id or state.provider_route_id,
            conflict_with=conflict_with,
        )

    def _resolve_final_policy_error(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
        cause: LlmGatewayError,
    ) -> LlmGatewayError:
        """解析重试与降级流程结束后应向调用方暴露的最终错误。

        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :param cause: 最近一次标准错误。
        :return: 保留单次原始错误，或返回真正发生重试/降级后的耗尽错误。
        """

        if (
            cause.code is not LlmGatewayErrorCode.LLM_RETRY_EXHAUSTED
            and len(state.fallback_chain) <= 1
        ):
            return cause
        return self._build_retry_exhausted_error(
            request=request,
            state=state,
            cause=cause,
        )

    def _with_profile_context(
        self,
        *,
        error: LlmGatewayError,
        request: LlmInvocationRequestDto,
        resolved: ResolvedModelProfile,
        state: _LogicalCallState,
    ) -> LlmGatewayError:
        """为适配器错误补齐当前 profile 和请求上下文。

        :param error: 适配器或控制器返回的标准错误。
        :param request: 已补齐调用 ID 的模型请求。
        :param resolved: 当前 profile 与供应商路由。
        :param state: 当前逻辑调用状态。
        :return: 补齐上下文的新 LlmGateway 异常。
        """

        state.actual_profile_id = resolved.profile.model_profile_id
        state.provider_route_id = resolved.route.provider_route_id
        return error.with_context(
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=resolved.profile.model_profile_id,
            provider_route_id=resolved.route.provider_route_id,
        )

    def _with_request_context(
        self,
        *,
        error: LlmGatewayError,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
    ) -> LlmGatewayError:
        """为最终错误补齐逻辑调用和请求上下文。

        :param error: 当前最终 LlmGateway 错误。
        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :return: 补齐上下文的新 LlmGateway 异常。
        """

        return error.with_context(
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=state.actual_profile_id or request.model_profile_id,
            provider_route_id=state.provider_route_id,
        )

    async def _write_summary(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
        status: Literal["succeeded", "failed", "cancelled"],
        error: LlmGatewayError | None,
    ) -> LlmTraceWriteResultDto:
        """按 profile trace 策略写入脱敏模型调用摘要。

        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :param status: 模型调用最终状态。
        :param error: 可选最终标准错误。
        :return: 摘要写入、降级或跳过状态。
        """

        trace_profile_id = state.actual_profile_id or request.model_profile_id
        try:
            profile = self._registry.resolve_profile(trace_profile_id).profile
        except LlmGatewayError:
            profile = None
        if profile is not None and not profile.trace_policy.emit_logic_trace_summary:
            return LlmTraceWriteResultDto(
                status=LlmTraceWriteStatus.SKIPPED,
                reason="profile_trace_policy_disabled",
            )
        summary = LlmCallSummaryDto(
            call_id=state.call_id,
            trace_id=request.trace_id,
            request_id=request.request_id,
            caller_component=request.caller_component,
            requested_profile_id=request.model_profile_id,
            actual_profile_id=state.actual_profile_id,
            provider_route_id=state.provider_route_id,
            actual_model=state.actual_model,
            status=status,
            finish_reason=state.finish_reason,
            usage=state.usage,
            latency_ms=_elapsed_ms(state.started_at),
            first_token_latency_ms=state.first_token_latency_ms,
            retry_count=state.retry_count,
            fallback_chain=list(state.fallback_chain),
            error_code=error.code if error is not None else None,
            config_snapshot_id=self._config_snapshot_id,
        )
        try:
            return await self._trace_store.write_summary(summary)
        except Exception as exc:
            return LlmTraceWriteResultDto(
                status=LlmTraceWriteStatus.DEGRADED,
                reason=f"trace_store_error:{exc.__class__.__name__}",
            )

    async def _finalize_cancelled_call(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
    ) -> None:
        """记录被取消逻辑模型调用的摘要与观测。

        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :return: None。
        """

        error = LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_CANCELLED,
            operation=(
                LlmGatewayOperation.STREAM_LLM
                if request.stream
                else LlmGatewayOperation.INVOKE_LLM
            ),
            message="模型调用已被取消",
            call_id=state.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=state.actual_profile_id or request.model_profile_id,
            provider_route_id=state.provider_route_id,
        )
        trace_result = await self._write_summary(
            request=request,
            state=state,
            status="cancelled",
            error=error,
        )
        self._record_observability(
            request=request,
            state=state,
            status="cancelled",
            error=error,
            trace_result=trace_result,
        )

    def _record_observability(
        self,
        *,
        request: LlmInvocationRequestDto,
        state: _LogicalCallState,
        status: Literal["succeeded", "failed", "cancelled"],
        error: LlmGatewayError | None,
        trace_result: LlmTraceWriteResultDto,
    ) -> None:
        """记录一次逻辑模型调用的指标和结构化摘要。

        :param request: 已补齐调用 ID 的模型请求。
        :param state: 当前逻辑调用状态。
        :param status: 模型调用最终状态。
        :param error: 可选最终标准错误。
        :param trace_result: 模型调用摘要写入状态。
        :return: None。
        """

        if self._observability is None:
            return
        try:
            provider_name = "unresolved"
            model_name = state.actual_model or "unresolved"
            if state.provider_route_id is not None:
                try:
                    route = self._registry.resolve_route(state.provider_route_id)
                    provider_name = route.provider_name
                    if state.actual_model is None:
                        model_name = route.model_alias
                except LlmGatewayError:
                    pass
            generation_profile = request.metadata.get(
                "generation_profile",
                request.model_profile_id,
            )
            generation_profile_value = (
                generation_profile
                if isinstance(generation_profile, str)
                else request.model_profile_id
            )
            self._observability.record_llm_call(
                agent_name=request.caller_component,
                generation_profile=generation_profile_value,
                model_provider=provider_name,
                model_name=model_name,
                status=status,
                duration_seconds=_elapsed_ms(state.started_at) / 1000,
                prompt_tokens=state.usage.input_tokens,
                completion_tokens=state.usage.output_tokens,
                retry_count=state.retry_count,
                error_type=error.code.value if error is not None else None,
            )
            if len(state.fallback_chain) > 1:
                self._observability.record_metric(
                    metric_name="fallback_triggered_total",
                    value=1.0,
                    metric_type=MetricType.COUNTER,
                    labels={
                        "fallback_reason_code": (
                            error.code.value if error is not None else "recovered"
                        ),
                        "generation_profile": generation_profile_value,
                    },
                    description="LlmGateway profile 降级次数。",
                )
            if trace_result.status is LlmTraceWriteStatus.DEGRADED:
                self._observability.record_metric(
                    metric_name="llm_gateway_trace_degraded_total",
                    value=1.0,
                    metric_type=MetricType.COUNTER,
                    labels={"status": status},
                    description="LlmGateway 调用摘要留痕降级次数。",
                )
        except Exception:
            return


def create_default_llm_gateway(
    *,
    settings: LlmGatewaySettings,
    observability_provider: ObservabilityProvider | None = None,
    trace_store: LlmCallTraceStore | None = None,
    config_snapshot_id: str | None = None,
    adapters_by_profile: Mapping[str, ProviderAdapter] | None = None,
    adapter_factory: OpenAICompatibleAdapterFactory | None = None,
) -> DefaultLlmGateway:
    """创建应用内 LlmGateway 默认实现。

    :param settings: 已校验的 LlmGateway RuntimeConfig。
    :param observability_provider: 可选项目 Observability provider。
    :param trace_store: 可选模型调用摘要存储端口。
    :param config_snapshot_id: 可选 RuntimeConfig 快照 ID。
    :param adapters_by_profile: 可选按 profile 注入的适配器映射。
    :param adapter_factory: 可选 OpenAI-compatible 适配器工厂。
    :return: 已完成 profile、适配器、并发与 token 估算装配的 LlmGateway。
    """

    resolved_adapters = dict(adapters_by_profile or {})
    resolved_factory = adapter_factory or OpenAICompatibleAdapterFactory()
    route_by_id = {route.provider_route_id: route for route in settings.provider_routes}
    for profile in settings.model_profiles:
        if profile.model_profile_id in resolved_adapters:
            continue
        route = route_by_id[profile.provider_route_id]
        resolved_adapters[profile.model_profile_id] = resolved_factory.create(
            route=route,
            timeout_policy=profile.timeout_policy,
        )
    return DefaultLlmGateway(
        settings=settings,
        adapters_by_profile=resolved_adapters,
        observability_provider=observability_provider,
        trace_store=trace_store,
        config_snapshot_id=config_snapshot_id,
    )


__all__: tuple[str, ...] = (
    "DefaultLlmGateway",
    "create_default_llm_gateway",
)
