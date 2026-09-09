"""
统一严格 JSON 模型调用网关。

该模块是语义层模型调用的生产主路径，组合角色配置、并发、预算、传输重试、
完成协议校验、Strict JSON Gateway 和执行审计；它不解释兽医语义。
"""

from __future__ import annotations

import hashlib
import random
import time
import uuid
from collections.abc import Callable
from typing import Protocol, TypeVar

from vet_agent.core import errors as core_errors
from vet_agent.core.execution import ExecutionContext
from vet_agent.core.llm.budget import BudgetLease, LLMBudget
from vet_agent.core.llm.concurrency import LLMConcurrencyLimiter
from vet_agent.core.llm.contracts import (
    ChatCompletionClient,
    ChatCompletionRequest,
    LLMCallEvent,
    LLMCallStatus,
    LLMEventSink,
    LLMResponse,
    LLMResponseMetadata,
    LLMSettings,
    LLMUsage,
    ModelRole,
    ModelRoleConfig,
    NormalizedChatCompletion,
)
from vet_agent.core.llm.retry import (
    DefaultTransportRetrySleeper,
    TransportRetryPolicy,
    TransportRetrySleeper,
    calculate_transport_delay,
)
from vet_agent.core.prompts import RenderedPrompt, validate_prompt_binding
from vet_agent.core.schema import StrictJSONGateway, StrictModel

T = TypeVar("T", bound=StrictModel)


class MonotonicClock(Protocol):
    """可替换的单调时钟端口。

    测试替身可提供确定性时间；生产实现必须使用单调时间以避免系统时钟回拨。
    """

    def monotonic_ns(self) -> int:
        """读取当前单调时间。

        :return: 纳秒级单调时间戳。
        """


class SystemMonotonicClock:
    """基于 ``time.monotonic_ns`` 的生产时钟。"""

    def monotonic_ns(self) -> int:
        """读取系统单调时间。

        :return: 纳秒级单调时间戳。
        """
        return time.monotonic_ns()


class UUIDLLMIdGenerator:
    """基于 UUID 的模型调用标识生成器。"""

    def new_request_id(self) -> str:
        """生成逻辑请求标识。

        :return: 带模型请求前缀的 UUID 字符串。
        """
        return f"llm_req_{uuid.uuid4()}"

    def new_attempt_id(self) -> str:
        """生成物理尝试标识。

        :return: 带模型尝试前缀的 UUID 字符串。
        """
        return f"llm_attempt_{uuid.uuid4()}"


class DefaultLLMGateway:
    """生产默认严格 JSON 模型网关。

    :ivar _settings: 不可变模型配置。
    :ivar _transport: OpenAI-compatible 传输端口。
    :ivar _strict_json_gateway: 严格 JSON 与 schema 网关。
    :ivar _event_sink: 模型执行审计事件端口。
    :ivar _clock: 单调时钟。
    :ivar _ids: 请求与尝试标识生成器。
    :ivar _retry_policy: 传输重试策略。
    :ivar _retry_sleeper: 重试等待端口。
    :ivar _random_unit: 重试 jitter 随机数函数。
    :ivar _budget: 请求与 token 预算。
    :ivar _concurrency: 全局与角色并发限制器。
    """

    def __init__(
        self,
        *,
        settings: LLMSettings,
        transport: ChatCompletionClient,
        strict_json_gateway: StrictJSONGateway,
        event_sink: LLMEventSink,
        clock: MonotonicClock | None = None,
        id_generator: UUIDLLMIdGenerator | None = None,
        retry_sleeper: TransportRetrySleeper | None = None,
        random_unit: Callable[[], float] | None = None,
    ) -> None:
        """初始化模型网关的不可变执行策略。

        :param settings: 已校验的模型配置。
        :param transport: 生产或测试传输端口。
        :param strict_json_gateway: 严格 JSON 解析网关。
        :param event_sink: 审计事件端口；测试可显式使用空实现。
        :param clock: 可选单调时钟；缺省使用系统时钟。
        :param id_generator: 可选标识生成器；缺省使用 UUID。
        :param retry_sleeper: 可选重试等待端口；缺省等待真实时间。
        :param random_unit: 可选 ``[0, 1)`` 随机函数；缺省使用系统随机数。
        :return: 无返回值；构造受治理模型调用网关。
        """
        self._settings = settings
        self._transport = transport
        self._strict_json_gateway = strict_json_gateway
        self._event_sink = event_sink
        self._clock = clock or SystemMonotonicClock()
        self._ids = id_generator or UUIDLLMIdGenerator()
        self._retry_policy = TransportRetryPolicy(
            max_retries=settings.max_transport_retries,
            initial_delay_seconds=settings.retry_initial_delay_seconds,
            max_delay_seconds=settings.retry_max_delay_seconds,
        )
        self._retry_sleeper = retry_sleeper or DefaultTransportRetrySleeper()
        self._random_unit = random_unit or random.random
        self._budget = LLMBudget(settings)
        self._concurrency = LLMConcurrencyLimiter(settings)

    def _resolve_role_config(self, role: ModelRole) -> ModelRoleConfig:
        """解析稳定模型角色配置。

        :param role: 稳定模型角色。
        :return: 该角色的不可变调用配置。
        :raises vet_agent.core.errors.ConfigurationError: 当角色未配置。
        """
        role_config = self._settings.role_configs.get(role)
        if role_config is None:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="模型角色未配置",
                context={"model_role": role.value},
            )
        return role_config

    def _estimate_prompt_tokens(self, prompt: RenderedPrompt) -> int:
        """保守估计输入 token 数。

        :param prompt: 已渲染模型消息。
        :return: 用于预算预留的非负输入 token 估计值。
        """
        estimated_tokens = 8
        for message in prompt.messages:
            cjk_characters = sum(
                1 for char in message.content if "\u4e00" <= char <= "\u9fff"
            )
            other_characters = len(message.content) - cjk_characters
            estimated_tokens += cjk_characters + other_characters // 4 + 4
        return estimated_tokens

    def _build_request(
        self,
        *,
        request_id: str,
        attempt_id: str,
        role: ModelRole,
        role_config: ModelRoleConfig,
        prompt: RenderedPrompt,
    ) -> ChatCompletionRequest:
        """构造一次物理模型请求。

        :param request_id: 逻辑请求标识。
        :param attempt_id: 物理尝试标识。
        :param role: 稳定模型角色。
        :param role_config: 角色调用配置。
        :param prompt: 已渲染 prompt。
        :return: 传输层请求对象。
        """
        return ChatCompletionRequest(
            request_id=request_id,
            attempt_id=attempt_id,
            model=role_config.model,
            model_role=role.value,
            prompt_id=prompt.record.prompt_id,
            prompt_version=prompt.record.prompt_version,
            prompt_sha256=prompt.record.prompt_sha256,
            messages=prompt.messages,
            temperature=role_config.temperature,
            max_tokens=role_config.max_tokens,
            timeout_seconds=role_config.timeout_seconds,
            response_format=role_config.response_format,
        )

    def _output_digest(self, output: StrictModel) -> str:
        """计算 typed 输出的确定性摘要。

        :param output: 已通过严格 schema 校验的输出对象。
        :return: SHA-256 十六进制摘要；不输出原始内容。
        """
        canonical_json = output.model_dump_json(by_alias=True)
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def _error_code(self, exception: BaseException) -> core_errors.CoreErrorCode:
        """提取异常稳定错误码。

        :param exception: 模型调用过程中抛出的异常。
        :return: 核心错误码；未分类异常映射为内部错误。
        """
        if isinstance(exception, core_errors.CoreError):
            return exception.error_code
        return core_errors.CoreErrorCode.CORE_INTERNAL_ERROR

    def _build_event(
        self,
        *,
        context: ExecutionContext,
        status: LLMCallStatus,
        request_id: str,
        attempt_id: str,
        role: ModelRole,
        role_config: ModelRoleConfig | None,
        prompt: RenderedPrompt,
        response_model: str | None,
        response_id: str | None,
        latency_ms: int,
        usage: LLMUsage | None,
        finish_reason: str,
        attempt_count: int,
        error_code: str,
        output_digest: str,
    ) -> LLMCallEvent:
        """构造脱敏模型调用审计事件。

        :param context: 当前执行上下文。
        :param status: 调用状态。
        :param request_id: 逻辑请求标识。
        :param attempt_id: 当前物理尝试标识。
        :param role: 稳定模型角色。
        :param role_config: 角色配置；配置缺失时可为 ``None``。
        :param prompt: 本次绑定的 prompt。
        :param response_model: 提供方返回模型名。
        :param response_id: 提供方响应标识。
        :param latency_ms: 逻辑调用耗时。
        :param usage: 可用 token 用量。
        :param finish_reason: 完成原因或失败占位值。
        :param attempt_count: 物理尝试次数。
        :param error_code: 错误码；成功时为空。
        :param output_digest: 成功输出摘要。
        :return: 不含完整输入输出的审计事件。
        """
        model_name = response_model or (
            role_config.model if role_config is not None else "<unresolved>"
        )
        return LLMCallEvent(
            execution_id=context.execution_id,
            correlation_id=context.correlation_id,
            step=context.step,
            status=status,
            request_id=request_id,
            attempt_id=attempt_id,
            model_role=role.value,
            model=model_name,
            prompt_id=prompt.record.prompt_id,
            prompt_version=prompt.record.prompt_version,
            prompt_sha256=prompt.record.prompt_sha256,
            response_id=response_id,
            latency_ms=latency_ms,
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            total_tokens=usage.total_tokens if usage is not None else 0,
            finish_reason=finish_reason,
            attempt_count=attempt_count,
            error_code=error_code,
            input_digest=prompt.record.prompt_sha256,
            output_digest=output_digest,
        )

    async def _emit_event(self, event: LLMCallEvent) -> None:
        """写入模型调用审计事件。

        :param event: 已脱敏审计事件。
        :return: 无返回值。
        :raises vet_agent.core.errors.LLMAuditError: 当审计端口写入失败。
        """
        try:
            await self._event_sink.emit(event)
        except Exception as exc:
            raise core_errors.LLMAuditError(
                error_code=core_errors.CoreErrorCode.LLM_AUDIT_FAILED,
                message="模型调用审计事件写入失败",
                context={"request_id": event.request_id, "status": event.status.value},
                cause=exc,
            ) from exc

    def _build_metadata(
        self,
        *,
        role: ModelRole,
        prompt: RenderedPrompt,
        completion: NormalizedChatCompletion,
        request_id: str,
        latency_ms: int,
        attempt_count: int,
    ) -> LLMResponseMetadata:
        """构造成功模型响应 metadata。

        :param role: 稳定模型角色。
        :param prompt: 本次调用 prompt。
        :param completion: 归一完成响应。
        :param request_id: 逻辑请求标识。
        :param latency_ms: 逻辑调用耗时。
        :param attempt_count: 物理尝试次数。
        :return: typed 输出附带的可审计 metadata。
        """
        return LLMResponseMetadata(
            request_id=request_id,
            model=completion.model,
            model_role=role.value,
            prompt_id=prompt.record.prompt_id,
            prompt_version=prompt.record.prompt_version,
            prompt_sha256=prompt.record.prompt_sha256,
            response_id=completion.response_id,
            latency_ms=latency_ms,
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
            total_tokens=completion.usage.total_tokens,
            finish_reason=completion.finish_reason,
            attempt_count=attempt_count,
        )

    async def _settle_budget(
        self,
        *,
        lease: BudgetLease,
        attempt_count: int,
        usage: LLMUsage | None,
        estimated_prompt_tokens: int,
    ) -> None:
        """按实际执行结果结算预算。

        :param lease: 调用前取得的预算凭证。
        :param attempt_count: 实际物理尝试次数。
        :param usage: 成功响应 usage；失败时可为 ``None``。
        :param estimated_prompt_tokens: 失败路径使用的输入 token 保守估计。
        :return: 无返回值；结算失败时显式抛出预算错误。
        """
        await self._budget.settle(
            lease,
            actual_attempts=attempt_count,
            actual_prompt_tokens=usage.prompt_tokens
            if usage is not None
            else estimated_prompt_tokens,
            actual_completion_tokens=usage.completion_tokens
            if usage is not None
            else 0,
        )

    async def complete_json(
        self,
        *,
        role: ModelRole,
        prompt: RenderedPrompt,
        output_schema: type[T],
        context: ExecutionContext,
    ) -> LLMResponse[T]:
        """执行一次受治理的严格 JSON 模型调用。

        :param role: 稳定模型角色。
        :param prompt: 已渲染版本化 prompt。
        :param output_schema: 严格输出 schema。
        :param context: 当前执行上下文。
        :return: typed 输出与执行 metadata。
        :raises vet_agent.core.errors.ConfigurationError: 当配置或 prompt 绑定非法。
        :raises vet_agent.core.errors.LLMBudgetExceededError: 当请求或 token 预算超限。
        :raises vet_agent.core.errors.LLMTransportError: 当可重试传输错误耗尽尝试次数。
        :raises vet_agent.core.errors.LLMProtocolError: 当完成响应协议非法。
        :raises vet_agent.core.errors.StrictJSONValidationError: 当输出不是单一
            严格 JSON。
        :raises vet_agent.core.errors.SchemaValidationError: 当输出未通过严格 schema。
        :raises vet_agent.core.errors.LLMAuditError: 当审计事件写入失败。
        """
        request_id = self._ids.new_request_id()
        current_attempt_id = self._ids.new_attempt_id()
        started_at = self._clock.monotonic_ns()
        lease: BudgetLease | None = None
        estimated_prompt_tokens = 0
        completion: NormalizedChatCompletion | None = None
        attempt_count = 0
        role_config: ModelRoleConfig | None = None
        final_audit_attempted = False
        failure: BaseException | None = None

        try:
            validate_prompt_binding(
                model_role=role.value,
                output_schema=output_schema,
                prompt=prompt,
            )
            role_config = self._resolve_role_config(role)
            estimated_prompt_tokens = self._estimate_prompt_tokens(prompt)
            lease = await self._budget.reserve(
                lease_id=request_id,
                role=role,
                estimated_prompt_tokens=estimated_prompt_tokens,
                reserved_completion_tokens=role_config.max_tokens,
            )
            max_attempts = self._retry_policy.max_retries + 1
            async with self._concurrency.slot(role):
                for attempt_number in range(1, max_attempts + 1):
                    attempt_count = attempt_number
                    current_attempt_id = self._ids.new_attempt_id()
                    request = self._build_request(
                        request_id=request_id,
                        attempt_id=current_attempt_id,
                        role=role,
                        role_config=role_config,
                        prompt=prompt,
                    )
                    try:
                        completion = await self._transport.complete(request)
                        break
                    except core_errors.CoreError as exc:
                        if not exc.retryable:
                            raise
                        latency_ms = (
                            self._clock.monotonic_ns() - started_at
                        ) // 1_000_000
                        await self._emit_event(
                            self._build_event(
                                context=context,
                                status=LLMCallStatus.ATTEMPT_FAILED,
                                request_id=request_id,
                                attempt_id=current_attempt_id,
                                role=role,
                                role_config=role_config,
                                prompt=prompt,
                                response_model=None,
                                response_id=None,
                                latency_ms=latency_ms,
                                usage=None,
                                finish_reason="transport_error",
                                attempt_count=attempt_count,
                                error_code=exc.error_code.value,
                                output_digest="",
                            )
                        )
                        if attempt_number == max_attempts:
                            raise
                        delay_seconds = calculate_transport_delay(
                            policy=self._retry_policy,
                            next_attempt_number=attempt_number + 1,
                            retry_after_seconds=None,
                            random_unit=self._random_unit(),
                        )
                        await self._retry_sleeper.sleep(delay_seconds)

            if completion is None:
                raise core_errors.LLMProtocolError(
                    error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
                    message="模型网关未获得完成响应",
                    context={"request_id": request_id},
                )
            output = self._strict_json_gateway.parse(
                content=completion.content,
                output_schema=output_schema,
            )
            latency_ms = (self._clock.monotonic_ns() - started_at) // 1_000_000
            metadata = self._build_metadata(
                role=role,
                prompt=prompt,
                completion=completion,
                request_id=request_id,
                latency_ms=latency_ms,
                attempt_count=attempt_count,
            )
            if lease is None:
                raise core_errors.ConfigurationError(
                    error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                    message="模型预算凭证缺失",
                    context={"request_id": request_id},
                )
            await self._settle_budget(
                lease=lease,
                attempt_count=attempt_count,
                usage=completion.usage,
                estimated_prompt_tokens=estimated_prompt_tokens,
            )
            final_audit_attempted = True
            await self._emit_event(
                self._build_event(
                    context=context,
                    status=LLMCallStatus.COMPLETED,
                    request_id=request_id,
                    attempt_id=current_attempt_id,
                    role=role,
                    role_config=role_config,
                    prompt=prompt,
                    response_model=completion.model,
                    response_id=completion.response_id,
                    latency_ms=latency_ms,
                    usage=completion.usage,
                    finish_reason=completion.finish_reason,
                    attempt_count=attempt_count,
                    error_code="",
                    output_digest=self._output_digest(output),
                )
            )
            return LLMResponse(output=output, metadata=metadata)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            latency_ms = (self._clock.monotonic_ns() - started_at) // 1_000_000
            if lease is not None and not lease.settled:
                await self._settle_budget(
                    lease=lease,
                    attempt_count=max(attempt_count, 1),
                    usage=completion.usage if completion is not None else None,
                    estimated_prompt_tokens=estimated_prompt_tokens,
                )
            if not final_audit_attempted:
                final_audit_attempted = True
                await self._emit_event(
                    self._build_event(
                        context=context,
                        status=LLMCallStatus.FAILED,
                        request_id=request_id,
                        attempt_id=current_attempt_id,
                        role=role,
                        role_config=role_config,
                        prompt=prompt,
                        response_model=completion.model
                        if completion is not None
                        else None,
                        response_id=completion.response_id
                        if completion is not None
                        else None,
                        latency_ms=latency_ms,
                        usage=completion.usage if completion is not None else None,
                        finish_reason=completion.finish_reason
                        if completion is not None
                        else "failed",
                        attempt_count=max(attempt_count, 1),
                        error_code=(
                            self._error_code(failure).value
                            if failure is not None
                            else core_errors.CoreErrorCode.CORE_INTERNAL_ERROR.value
                        ),
                        output_digest="",
                    )
                )


__all__ = [
    "DefaultLLMGateway",
    "MonotonicClock",
    "SystemMonotonicClock",
    "UUIDLLMIdGenerator",
]
