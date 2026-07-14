##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/service.py
# 作用: 实现 GuardrailFramework 默认应用内服务，统一调度 pre-gen、post-gen review 与 deterministic gate。
# 边界: 不实现兽医安全规则、不直接发布用户回复、不写数据库；仅编排策略、handler、fallback、trace 与指标。
##################################################################################################

import asyncio
from collections.abc import Awaitable
from time import perf_counter
from typing import Protocol, TypeVar

from pydantic import ValidationError

from veterinary_agent.config import (
    GuardrailFrameworkSettings,
    GuardrailFrameworkStageSettings,
    RuntimeConfigError,
    RuntimeConfigProvider,
)
from veterinary_agent.guardrail_framework.dto import (
    GuardActionDto,
    GuardrailFailurePolicyDto,
    GuardrailFindingDto,
    GuardrailPolicyDto,
    GuardrailRetryPolicyDto,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailTimeoutPolicyDto,
    GuardrailTracePolicyDto,
    GuardrailTraceRecordDto,
)
from veterinary_agent.guardrail_framework.enums import (
    GuardActionType,
    GuardrailFailureStrategy,
    GuardrailFindingSeverity,
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceWriteStatus,
)
from veterinary_agent.guardrail_framework.errors import GuardrailFrameworkError
from veterinary_agent.guardrail_framework.ports import (
    FallbackTemplateProvider,
    GuardrailHandlerRegistry,
    GuardrailPolicyRegistry,
    GuardrailTraceSink,
    TodoFallbackTemplateProvider,
    TodoGuardrailHandler,
    TodoGuardrailTraceSink,
)
from veterinary_agent.guardrail_framework.registry import (
    InMemoryGuardrailHandlerRegistry,
    InMemoryGuardrailPolicyRegistry,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)

_COMPONENT_NAME = "GuardrailFramework"
_UNKNOWN_PROFILE = "unknown"
_T = TypeVar("_T")


class GuardrailFramework(Protocol):
    """GuardrailFramework 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断 GuardrailFramework 是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且组件已启用则返回 True。
        """

        ...

    async def run_pre_generation_guard(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行生成前护栏。

        :param request: 生成前护栏运行请求。
        :return: 标准化护栏运行结果。
        """

        ...

    async def run_post_generation_review(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行生成后输出安全审查。

        :param request: 生成后审查运行请求。
        :return: 标准化护栏运行结果。
        """

        ...

    async def run_deterministic_gate(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行确定性发布门。

        :param request: 确定性发布门运行请求。
        :return: 标准化护栏运行结果。
        """

        ...

    async def run_guardrail_stage(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行请求声明的护栏阶段。

        :param request: 护栏阶段运行请求。
        :return: 标准化护栏运行结果。
        """

        ...


def _elapsed_ms(started_at: float) -> int:
    """计算从单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


async def _with_timeout(
    awaitable: Awaitable[_T],
    *,
    timeout_seconds: float,
) -> _T:
    """在指定超时预算内等待异步操作完成。

    :param awaitable: 需要等待的异步操作。
    :param timeout_seconds: 超时秒数。
    :return: 异步操作返回值。
    :raises TimeoutError: 当操作超过超时预算时抛出。
    """

    return await asyncio.wait_for(awaitable, timeout=timeout_seconds)


def _stage_to_operation(stage: GuardrailStage) -> GuardrailFrameworkOperation:
    """将护栏阶段映射为对外操作名。

    :param stage: 护栏阶段。
    :return: 阶段对应的稳定操作名。
    """

    if stage is GuardrailStage.PRE_GENERATION:
        return GuardrailFrameworkOperation.RUN_PRE_GENERATION_GUARD
    if stage is GuardrailStage.POST_GENERATION_REVIEW:
        return GuardrailFrameworkOperation.RUN_POST_GENERATION_REVIEW
    return GuardrailFrameworkOperation.RUN_DETERMINISTIC_GATE


def _stage_settings(
    *,
    settings: GuardrailFrameworkSettings,
    stage: GuardrailStage,
) -> GuardrailFrameworkStageSettings:
    """读取指定阶段配置。

    :param settings: GuardrailFramework 运行配置。
    :param stage: 需要读取配置的护栏阶段。
    :return: 指定阶段的运行配置。
    """

    if stage is GuardrailStage.PRE_GENERATION:
        return settings.pre_generation
    if stage is GuardrailStage.POST_GENERATION_REVIEW:
        return settings.post_generation_review
    return settings.deterministic_gate


def _failure_strategy(value: str) -> GuardrailFailureStrategy:
    """将配置字符串转换为失败策略枚举。

    :param value: RuntimeConfig 中的失败策略字符串。
    :return: 对应的失败策略枚举。
    :raises ValueError: 当配置字符串不受支持时抛出。
    """

    return GuardrailFailureStrategy(value)


def _build_policy_from_stage_settings(
    *,
    stage: GuardrailStage,
    settings: GuardrailFrameworkSettings,
) -> GuardrailPolicyDto:
    """根据 RuntimeConfig 阶段配置构建默认护栏策略。

    :param stage: 护栏阶段。
    :param settings: GuardrailFramework 运行配置。
    :return: 当前阶段默认护栏策略。
    """

    stage_settings = _stage_settings(settings=settings, stage=stage)
    return GuardrailPolicyDto(
        policy_id=stage_settings.policy_id,
        policy_version=stage_settings.policy_version,
        stage=stage,
        handler_ref=stage_settings.handler_ref,
        enabled=stage_settings.enabled,
        timeout_policy=GuardrailTimeoutPolicyDto(
            stage_timeout_seconds=stage_settings.stage_timeout_seconds,
            handler_timeout_seconds=stage_settings.handler_timeout_seconds,
        ),
        retry_policy=GuardrailRetryPolicyDto(
            max_attempts=stage_settings.max_attempts,
            retry_on_timeout=stage_settings.retry_on_timeout,
            retry_on_handler_error=stage_settings.retry_on_handler_error,
        ),
        failure_policy=GuardrailFailurePolicyDto(
            strategy=_failure_strategy(stage_settings.failure_strategy),
            fallback_template_ref=stage_settings.fallback_template_ref,
        ),
        trace_policy=GuardrailTracePolicyDto(
            emit_events=stage_settings.emit_trace_events,
            persist_full_text=settings.persist_full_text,
            capture_policy_ref=settings.capture_policy_version,
        ),
        metadata={
            "config_version": settings.config_version,
            "framework_version": settings.framework_version,
            "trace_schema_version": settings.trace_schema_version,
        },
    )


def build_default_guardrail_policy_registry(
    settings: GuardrailFrameworkSettings,
) -> InMemoryGuardrailPolicyRegistry:
    """构建 GuardrailFramework 默认内存策略注册表。

    :param settings: GuardrailFramework 运行配置。
    :return: 已注册三个标准阶段默认策略的内存策略注册表。
    """

    policies = [
        _build_policy_from_stage_settings(
            stage=GuardrailStage.PRE_GENERATION,
            settings=settings,
        ),
        _build_policy_from_stage_settings(
            stage=GuardrailStage.POST_GENERATION_REVIEW,
            settings=settings,
        ),
        _build_policy_from_stage_settings(
            stage=GuardrailStage.DETERMINISTIC_GATE,
            settings=settings,
        ),
    ]
    return InMemoryGuardrailPolicyRegistry(policies)


def build_default_guardrail_handler_registry(
    settings: GuardrailFrameworkSettings,
) -> InMemoryGuardrailHandlerRegistry:
    """构建 GuardrailFramework 默认内存 handler 注册表。

    :param settings: GuardrailFramework 运行配置。
    :return: 注册了三个 TODO handler 的内存 handler 注册表。
    """

    handler_registry = InMemoryGuardrailHandlerRegistry()
    for stage in (
        GuardrailStage.PRE_GENERATION,
        GuardrailStage.POST_GENERATION_REVIEW,
        GuardrailStage.DETERMINISTIC_GATE,
    ):
        stage_settings = _stage_settings(settings=settings, stage=stage)
        handler_registry.register_handler(
            handler_ref=stage_settings.handler_ref,
            handler=TodoGuardrailHandler(handler_ref=stage_settings.handler_ref),
        )
    return handler_registry


def _action_type_for_status(status: GuardrailStatus) -> GuardActionType:
    """根据阶段状态推导默认动作类型。

    :param status: 护栏阶段状态。
    :return: 阶段状态对应的动作类型。
    """

    if status is GuardrailStatus.ALLOWED:
        return GuardActionType.ALLOW
    if status is GuardrailStatus.REWRITTEN:
        return GuardActionType.REWRITE
    if status is GuardrailStatus.BLOCKED:
        return GuardActionType.BLOCK
    if status is GuardrailStatus.FALLBACK:
        return GuardActionType.FALLBACK
    return GuardActionType.DEGRADE


class DefaultGuardrailFramework:
    """GuardrailFramework 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        policy_registry: GuardrailPolicyRegistry,
        handler_registry: GuardrailHandlerRegistry,
        fallback_template_provider: FallbackTemplateProvider | None = None,
        trace_sink: GuardrailTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 GuardrailFramework 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param policy_registry: 护栏策略注册表。
        :param handler_registry: 护栏 handler 注册表。
        :param fallback_template_provider: 可选 fallback 模板端口。
        :param trace_sink: 可选护栏 trace 写入端口。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._policy_registry = policy_registry
        self._handler_registry = handler_registry
        self._fallback_template_provider = (
            fallback_template_provider or TodoFallbackTemplateProvider()
        )
        self._trace_sink = trace_sink or TodoGuardrailTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断 GuardrailFramework 是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且组件已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.guardrail_framework.enabled

    async def run_pre_generation_guard(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行生成前护栏。

        :param request: 生成前护栏运行请求。
        :return: 标准化护栏运行结果。
        :raises GuardrailFrameworkError: 当请求阶段不匹配或组件不可用时抛出。
        """

        self._ensure_stage(
            request=request,
            expected_stage=GuardrailStage.PRE_GENERATION,
        )
        return await self.run_guardrail_stage(request)

    async def run_post_generation_review(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行生成后输出安全审查。

        :param request: 生成后审查运行请求。
        :return: 标准化护栏运行结果。
        :raises GuardrailFrameworkError: 当请求阶段不匹配或组件不可用时抛出。
        """

        self._ensure_stage(
            request=request,
            expected_stage=GuardrailStage.POST_GENERATION_REVIEW,
        )
        return await self.run_guardrail_stage(request)

    async def run_deterministic_gate(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行确定性发布门。

        :param request: 确定性发布门运行请求。
        :return: 标准化护栏运行结果。
        :raises GuardrailFrameworkError: 当请求阶段不匹配或组件不可用时抛出。
        """

        self._ensure_stage(
            request=request,
            expected_stage=GuardrailStage.DETERMINISTIC_GATE,
        )
        return await self.run_guardrail_stage(request)

    async def run_guardrail_stage(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """运行请求声明的护栏阶段。

        :param request: 护栏阶段运行请求。
        :return: 标准化护栏运行结果。
        :raises GuardrailFrameworkError: 当组件不可用、阶段禁用或策略缺失时抛出。
        """

        started_at = perf_counter()
        settings = self._load_settings_or_raise(request)
        self._ensure_stage_enabled(request=request, settings=settings)
        policies = self._policy_registry.resolve_policies(
            stage=request.stage,
            generation_profile=request.context.generation_profile,
        )
        results: list[GuardrailRunResultDto] = []
        for policy in policies:
            if policy.stage is not request.stage:
                raise self._build_error(
                    code=GuardrailFrameworkErrorCode.GUARDRAIL_STAGE_MISMATCH,
                    operation=GuardrailFrameworkOperation.RESOLVE_POLICY,
                    message="策略阶段与请求阶段不匹配",
                    request=request,
                    policy=policy,
                    retryable=False,
                )
            result = await self._execute_policy(request=request, policy=policy)
            results.append(result)
            if result.status in {GuardrailStatus.BLOCKED, GuardrailStatus.FALLBACK}:
                break
        aggregate = self._aggregate_results(
            request=request,
            policies=policies,
            results=results,
        )
        duration_ms = _elapsed_ms(started_at)
        traced_result = await self._write_trace(
            request=request,
            policies=policies,
            result=aggregate,
            duration_ms=duration_ms,
        )
        self._record_observability(
            request=request,
            result=traced_result,
            policies=policies,
            duration_ms=duration_ms,
        )
        return traced_result

    def _ensure_stage(
        self,
        *,
        request: GuardrailRunRequestDto,
        expected_stage: GuardrailStage,
    ) -> None:
        """校验请求声明的阶段与当前入口匹配。

        :param request: 护栏阶段运行请求。
        :param expected_stage: 当前入口期望的护栏阶段。
        :return: None。
        :raises GuardrailFrameworkError: 当请求阶段不匹配时抛出。
        """

        if request.stage is expected_stage:
            return
        raise self._build_error(
            code=GuardrailFrameworkErrorCode.GUARDRAIL_STAGE_MISMATCH,
            operation=_stage_to_operation(expected_stage),
            message="护栏入口与请求 stage 不匹配",
            request=request,
            retryable=False,
            conflict_with={
                "expected_stage": expected_stage.value,
                "actual_stage": request.stage.value,
            },
        )

    def _load_settings_or_raise(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailFrameworkSettings:
        """读取 GuardrailFramework RuntimeConfig。

        :param request: 当前护栏运行请求。
        :return: 当前有效 GuardrailFramework 配置。
        :raises GuardrailFrameworkError: 当 RuntimeConfig 不可用或组件未启用时抛出。
        """

        if not self._runtime_config_provider.is_ready():
            raise self._build_error(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_RUNTIME_CONFIG_UNAVAILABLE,
                operation=_stage_to_operation(request.stage),
                message="RuntimeConfig provider 未就绪",
                request=request,
                retryable=True,
            )
        try:
            settings = (
                self._runtime_config_provider.current_snapshot().guardrail_framework
            )
        except RuntimeConfigError as exc:
            raise self._build_error(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_RUNTIME_CONFIG_UNAVAILABLE,
                operation=_stage_to_operation(request.stage),
                message="读取 GuardrailFramework RuntimeConfig 失败",
                request=request,
                retryable=exc.retryable,
                conflict_with={
                    "runtime_config_error": exc.to_dto().model_dump(mode="json")
                },
            ) from exc
        if not settings.enabled:
            raise self._build_error(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_NOT_READY,
                operation=_stage_to_operation(request.stage),
                message="RuntimeConfig 禁用了 GuardrailFramework",
                request=request,
                retryable=True,
            )
        return settings

    def _ensure_stage_enabled(
        self,
        *,
        request: GuardrailRunRequestDto,
        settings: GuardrailFrameworkSettings,
    ) -> None:
        """校验请求阶段当前已启用。

        :param request: 当前护栏运行请求。
        :param settings: GuardrailFramework 运行配置。
        :return: None。
        :raises GuardrailFrameworkError: 当请求阶段被禁用时抛出。
        """

        stage_settings = _stage_settings(settings=settings, stage=request.stage)
        if stage_settings.enabled:
            return
        raise self._build_error(
            code=GuardrailFrameworkErrorCode.GUARDRAIL_STAGE_DISABLED,
            operation=_stage_to_operation(request.stage),
            message="RuntimeConfig 禁用了当前护栏阶段",
            request=request,
            retryable=True,
        )

    async def _execute_policy(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> GuardrailRunResultDto:
        """执行单条护栏策略并处理失败策略。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :return: 已按失败策略处理后的护栏运行结果。
        """

        raw_result = await self._execute_policy_with_retry(
            request=request,
            policy=policy,
        )
        normalized_result = self._normalize_result(
            request=request,
            policy=policy,
            result=raw_result,
        )
        if normalized_result.status is GuardrailStatus.FAILED:
            return await self._apply_failure_policy(
                request=request,
                policy=policy,
                failed_result=normalized_result,
            )
        return normalized_result

    async def _execute_policy_with_retry(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> GuardrailRunResultDto:
        """带超时和有限重试执行 handler。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :return: handler 返回或框架构造的护栏运行结果。
        """

        handler = self._handler_registry.get_handler(policy.handler_ref)
        if handler is None:
            return self._build_failed_result(
                request=request,
                policy=policy,
                code=GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_NOT_REGISTERED,
                reason_code="GUARDRAIL_HANDLER_NOT_REGISTERED",
                detail="护栏 handler 未注册",
            )
        attempts = policy.retry_policy.max_attempts
        last_result: GuardrailRunResultDto | None = None
        for attempt_index in range(attempts):
            try:
                result = await _with_timeout(
                    handler.run_guardrail(policy=policy, request=request),
                    timeout_seconds=policy.timeout_policy.handler_timeout_seconds,
                )
                return GuardrailRunResultDto.model_validate(result)
            except TimeoutError:
                last_result = self._build_failed_result(
                    request=request,
                    policy=policy,
                    code=GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_TIMEOUT,
                    reason_code="GUARDRAIL_HANDLER_TIMEOUT",
                    detail="护栏 handler 执行超时",
                )
                self._record_retry_metric(
                    request=request,
                    policy=policy,
                    attempt_index=attempt_index,
                )
                if not self._should_retry_timeout(
                    policy=policy, attempt_index=attempt_index
                ):
                    return last_result
            except GuardrailFrameworkError as exc:
                last_result = self._build_failed_result(
                    request=request,
                    policy=policy,
                    code=exc.code,
                    reason_code=exc.code.value,
                    detail=exc.error.message,
                )
                self._record_retry_metric(
                    request=request,
                    policy=policy,
                    attempt_index=attempt_index,
                )
                if not self._should_retry_handler_error(
                    policy=policy,
                    attempt_index=attempt_index,
                ):
                    return last_result
            except (ValidationError, ValueError, TypeError) as exc:
                return self._build_failed_result(
                    request=request,
                    policy=policy,
                    code=GuardrailFrameworkErrorCode.GUARDRAIL_OUTPUT_SCHEMA_INVALID,
                    reason_code="GUARDRAIL_OUTPUT_SCHEMA_INVALID",
                    detail=str(exc),
                )
            except Exception as exc:
                last_result = self._build_failed_result(
                    request=request,
                    policy=policy,
                    code=GuardrailFrameworkErrorCode.GUARDRAIL_INTERNAL_ERROR,
                    reason_code="GUARDRAIL_INTERNAL_ERROR",
                    detail=str(exc),
                )
                self._record_retry_metric(
                    request=request,
                    policy=policy,
                    attempt_index=attempt_index,
                )
                if not self._should_retry_handler_error(
                    policy=policy,
                    attempt_index=attempt_index,
                ):
                    return last_result
        return last_result or self._build_failed_result(
            request=request,
            policy=policy,
            code=GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_RETRY_EXHAUSTED,
            reason_code="GUARDRAIL_HANDLER_RETRY_EXHAUSTED",
            detail="护栏 handler 重试耗尽",
        )

    def _should_retry_timeout(
        self,
        *,
        policy: GuardrailPolicyDto,
        attempt_index: int,
    ) -> bool:
        """判断 handler 超时后是否继续重试。

        :param policy: 当前执行的护栏策略。
        :param attempt_index: 当前零基尝试序号。
        :return: 若仍允许继续重试则返回 True。
        """

        return (
            policy.retry_policy.retry_on_timeout
            and attempt_index + 1 < policy.retry_policy.max_attempts
        )

    def _should_retry_handler_error(
        self,
        *,
        policy: GuardrailPolicyDto,
        attempt_index: int,
    ) -> bool:
        """判断 handler 普通错误后是否继续重试。

        :param policy: 当前执行的护栏策略。
        :param attempt_index: 当前零基尝试序号。
        :return: 若仍允许继续重试则返回 True。
        """

        return (
            policy.retry_policy.retry_on_handler_error
            and attempt_index + 1 < policy.retry_policy.max_attempts
        )

    def _build_failed_result(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        code: GuardrailFrameworkErrorCode,
        reason_code: str,
        detail: str,
    ) -> GuardrailRunResultDto:
        """构建 handler 执行失败的中间结果。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param code: 框架错误码。
        :param reason_code: 标准 reason code。
        :param detail: 失败说明。
        :return: 标记失败的护栏运行结果。
        """

        return GuardrailRunResultDto(
            status=GuardrailStatus.FAILED,
            findings=[
                GuardrailFindingDto(
                    finding_id=f"{request.context.run_id}:{policy.policy_id}:{reason_code}",
                    category="guardrail_framework",
                    severity=GuardrailFindingSeverity.HIGH,
                    reason_code=reason_code,
                    evidence_ref=policy.handler_ref,
                    source_handler=policy.handler_ref,
                    metadata={"detail": detail},
                )
            ],
            error_code=code,
            metadata={
                "policy_id": policy.policy_id,
                "policy_version": policy.policy_version,
                "handler_ref": policy.handler_ref,
            },
        )

    def _normalize_result(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        result: GuardrailRunResultDto,
    ) -> GuardrailRunResultDto:
        """补齐 handler 结果中的动作与发布许可。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param result: handler 返回的原始结果。
        :return: 补齐框架字段后的护栏运行结果。
        """

        actions = self._normalize_actions(
            request=request,
            policy=policy,
            result=result,
        )
        publish_allowed = (
            request.stage is GuardrailStage.DETERMINISTIC_GATE
            and result.status is GuardrailStatus.ALLOWED
        )
        if result.status is GuardrailStatus.REWRITTEN:
            publish_allowed = False
        return result.model_copy(
            update={
                "actions": actions,
                "publish_allowed": publish_allowed,
            }
        )

    def _normalize_actions(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        result: GuardrailRunResultDto,
    ) -> list[GuardActionDto]:
        """补齐或生成护栏动作记录。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param result: handler 返回的护栏运行结果。
        :return: 补齐策略字段后的护栏动作列表。
        """

        if not result.actions:
            action_type = _action_type_for_status(result.status)
            return [
                self._build_action(
                    request=request,
                    policy=policy,
                    action_type=action_type,
                    reason_code=(
                        result.error_code.value
                        if result.error_code is not None
                        else action_type.value
                    ),
                    before_ref=request.candidate_text_ref,
                    after_ref=result.reviewed_text_ref or result.final_text_ref,
                )
            ]
        normalized_actions: list[GuardActionDto] = []
        for action in result.actions:
            normalized_actions.append(
                action.model_copy(
                    update={
                        "policy_id": action.policy_id or policy.policy_id,
                        "policy_version": action.policy_version
                        or policy.policy_version,
                    }
                )
            )
        return normalized_actions

    def _build_action(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        action_type: GuardActionType,
        reason_code: str,
        before_ref: str | None = None,
        after_ref: str | None = None,
    ) -> GuardActionDto:
        """构建一条标准护栏动作。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param action_type: 护栏动作类型。
        :param reason_code: 动作原因码。
        :param before_ref: 可选动作前文本或对象引用。
        :param after_ref: 可选动作后文本或对象引用。
        :return: 标准护栏动作。
        """

        segment_part = request.context.segment_id or "task"
        action_id = (
            f"{request.context.run_id}:{segment_part}:{policy.policy_id}:"
            f"{action_type.value}"
        )
        return GuardActionDto(
            action_id=action_id,
            stage=request.stage,
            action_type=action_type,
            reason_code=reason_code,
            handler_ref=policy.handler_ref,
            before_ref=before_ref,
            after_ref=after_ref,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
        )

    async def _apply_failure_policy(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        failed_result: GuardrailRunResultDto,
    ) -> GuardrailRunResultDto:
        """按照策略失败配置处理 handler 失败结果。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param failed_result: handler 失败中间结果。
        :return: 按失败策略转换后的护栏运行结果。
        """

        if (
            policy.failure_policy.strategy
            is GuardrailFailureStrategy.FAIL_OPEN_DEGRADED
        ):
            return failed_result.model_copy(
                update={
                    "status": GuardrailStatus.DEGRADED,
                    "degraded_mode": (
                        failed_result.error_code.value
                        if failed_result.error_code is not None
                        else "guardrail_handler_failed"
                    ),
                    "actions": failed_result.actions
                    + [
                        self._build_action(
                            request=request,
                            policy=policy,
                            action_type=GuardActionType.DEGRADE,
                            reason_code="guardrail_fail_open_degraded",
                            before_ref=request.candidate_text_ref,
                        )
                    ],
                }
            )
        if policy.failure_policy.strategy is GuardrailFailureStrategy.FALLBACK:
            return await self._build_fallback_result(
                request=request,
                policy=policy,
                failed_result=failed_result,
            )
        return failed_result.model_copy(
            update={
                "status": GuardrailStatus.BLOCKED,
                "actions": failed_result.actions
                + [
                    self._build_action(
                        request=request,
                        policy=policy,
                        action_type=GuardActionType.BLOCK,
                        reason_code=(
                            failed_result.error_code.value
                            if failed_result.error_code is not None
                            else "guardrail_fail_closed_block"
                        ),
                        before_ref=request.candidate_text_ref,
                    )
                ],
            }
        )

    async def _build_fallback_result(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        failed_result: GuardrailRunResultDto,
    ) -> GuardrailRunResultDto:
        """构建 fallback 失败策略对应的护栏结果。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param failed_result: handler 失败中间结果。
        :return: fallback 结果；模板不可用时返回阻断结果。
        """

        template_ref = policy.failure_policy.fallback_template_ref
        if template_ref is None:
            return self._fallback_unavailable_block(
                request=request,
                policy=policy,
                failed_result=failed_result,
                detail="fallback 策略缺少模板引用",
            )
        try:
            template = await self._fallback_template_provider.render_fallback_template(
                template_ref=template_ref,
                request=request,
                policy=policy,
            )
        except GuardrailFrameworkError as exc:
            return self._fallback_unavailable_block(
                request=request,
                policy=policy,
                failed_result=failed_result,
                detail=exc.error.message,
            )
        return GuardrailRunResultDto(
            status=GuardrailStatus.FALLBACK,
            final_text_ref=template.text_ref,
            fallback_triggered=True,
            fallback_template_version=template.template_version,
            findings=failed_result.findings,
            actions=failed_result.actions
            + [
                self._build_action(
                    request=request,
                    policy=policy,
                    action_type=GuardActionType.FALLBACK,
                    reason_code="guardrail_failure_fallback",
                    before_ref=request.candidate_text_ref,
                    after_ref=template.text_ref,
                )
            ],
            error_code=failed_result.error_code,
            metadata={
                **failed_result.metadata,
                "fallback_template_ref": template.template_ref,
            },
        )

    def _fallback_unavailable_block(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        failed_result: GuardrailRunResultDto,
        detail: str,
    ) -> GuardrailRunResultDto:
        """构建 fallback 模板不可用时的阻断结果。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param failed_result: handler 失败中间结果。
        :param detail: fallback 模板不可用说明。
        :return: 标记 fallback 模板不可用的阻断结果。
        """

        fallback_finding = GuardrailFindingDto(
            finding_id=f"{request.context.run_id}:{policy.policy_id}:fallback_unavailable",
            category="guardrail_fallback",
            severity=GuardrailFindingSeverity.HIGH,
            reason_code="GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE",
            evidence_ref=policy.failure_policy.fallback_template_ref,
            source_handler=policy.handler_ref,
            metadata={"detail": detail},
        )
        return GuardrailRunResultDto(
            status=GuardrailStatus.BLOCKED,
            findings=failed_result.findings + [fallback_finding],
            actions=failed_result.actions
            + [
                self._build_action(
                    request=request,
                    policy=policy,
                    action_type=GuardActionType.BLOCK,
                    reason_code="GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE",
                    before_ref=request.candidate_text_ref,
                )
            ],
            error_code=(
                GuardrailFrameworkErrorCode.GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE
            ),
            metadata={**failed_result.metadata, "fallback_error": detail},
        )

    def _aggregate_results(
        self,
        *,
        request: GuardrailRunRequestDto,
        policies: list[GuardrailPolicyDto],
        results: list[GuardrailRunResultDto],
    ) -> GuardrailRunResultDto:
        """聚合当前阶段多个策略的执行结果。

        :param request: 当前护栏运行请求。
        :param policies: 当前阶段实际解析到的策略列表。
        :param results: 当前阶段各策略执行结果。
        :return: 按保守优先级聚合后的阶段结果。
        """

        if not results:
            raise self._build_error(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_POLICY_NOT_FOUND,
                operation=GuardrailFrameworkOperation.RESOLVE_POLICY,
                message="当前阶段没有可聚合的护栏结果",
                request=request,
                retryable=False,
            )
        findings = [finding for result in results for finding in result.findings]
        actions = [action for result in results for action in result.actions]
        selected = self._select_highest_priority_result(results)
        publish_allowed = (
            request.stage is GuardrailStage.DETERMINISTIC_GATE
            and selected.status is GuardrailStatus.ALLOWED
            and selected.publish_allowed
        )
        return GuardrailRunResultDto(
            status=selected.status,
            reviewed_text_ref=selected.reviewed_text_ref,
            final_text_ref=selected.final_text_ref,
            publish_allowed=publish_allowed,
            fallback_triggered=selected.fallback_triggered,
            fallback_template_version=selected.fallback_template_version,
            findings=findings,
            actions=actions,
            degraded_mode=selected.degraded_mode,
            error_code=selected.error_code,
            trace_degraded=selected.trace_degraded,
            metadata={
                **selected.metadata,
                "policy_count": len(policies),
                "result_count": len(results),
            },
        )

    def _select_highest_priority_result(
        self,
        results: list[GuardrailRunResultDto],
    ) -> GuardrailRunResultDto:
        """按保守优先级选择阶段主结果。

        :param results: 当前阶段各策略执行结果。
        :return: 优先级最高的结果。
        """

        priorities: dict[GuardrailStatus, int] = {
            GuardrailStatus.BLOCKED: 0,
            GuardrailStatus.FALLBACK: 1,
            GuardrailStatus.FAILED: 2,
            GuardrailStatus.DEGRADED: 3,
            GuardrailStatus.REWRITTEN: 4,
            GuardrailStatus.ALLOWED: 5,
        }
        return min(results, key=lambda result: priorities[result.status])

    async def _write_trace(
        self,
        *,
        request: GuardrailRunRequestDto,
        policies: list[GuardrailPolicyDto],
        result: GuardrailRunResultDto,
        duration_ms: int,
    ) -> GuardrailRunResultDto:
        """写入护栏 trace 并把降级状态合并到结果。

        :param request: 当前护栏运行请求。
        :param policies: 当前阶段实际参与执行的策略。
        :param result: 当前阶段聚合结果。
        :param duration_ms: 当前阶段执行耗时，单位为毫秒。
        :return: 合并 trace 降级标记后的结果。
        """

        if not any(policy.trace_policy.emit_events for policy in policies):
            return result
        trace_result = await self._trace_sink.write_guardrail_trace(
            GuardrailTraceRecordDto(
                request=request,
                result=result,
                policies=policies,
                duration_ms=duration_ms,
            )
        )
        if trace_result.status is GuardrailTraceWriteStatus.RECORDED:
            return result
        self._record_metric(
            metric_name="guardrail_trace_degraded_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels={
                "component": _COMPONENT_NAME,
                "stage": request.stage.value,
                "status": result.status.value,
            },
            description="护栏留痕降级次数。",
        )
        return result.model_copy(update={"trace_degraded": True})

    def _record_observability(
        self,
        *,
        request: GuardrailRunRequestDto,
        result: GuardrailRunResultDto,
        policies: list[GuardrailPolicyDto],
        duration_ms: int,
    ) -> None:
        """记录护栏阶段指标和结构化事件。

        :param request: 当前护栏运行请求。
        :param result: 当前阶段聚合结果。
        :param policies: 当前阶段实际参与执行的策略。
        :param duration_ms: 当前阶段执行耗时，单位为毫秒。
        :return: None。
        """

        labels = self._base_metric_labels(request=request, result=result)
        self._record_metric(
            metric_name="guardrail_run_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="护栏运行总数。",
        )
        self._record_metric(
            metric_name="guardrail_stage_duration_ms",
            value=float(duration_ms),
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="各阶段执行耗时，单位为毫秒。",
        )
        if result.status in {GuardrailStatus.ALLOWED, GuardrailStatus.REWRITTEN}:
            self._record_metric(
                metric_name="guardrail_success_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="护栏成功完成总数。",
            )
        if result.status in {GuardrailStatus.BLOCKED, GuardrailStatus.FAILED}:
            self._record_metric(
                metric_name="guardrail_failed_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="护栏失败总数。",
            )
        for action in result.actions:
            action_labels = {
                **labels,
                "action_type": action.action_type.value,
                "handler_ref": action.handler_ref,
            }
            self._record_metric(
                metric_name="guardrail_action_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=action_labels,
                description="护栏动作次数。",
            )
        for metric_name, action_type in (
            ("guardrail_rewrite_total", GuardActionType.REWRITE),
            ("guardrail_block_total", GuardActionType.BLOCK),
            ("guardrail_fallback_total", GuardActionType.FALLBACK),
        ):
            if any(action.action_type is action_type for action in result.actions):
                self._record_metric(
                    metric_name=metric_name,
                    value=1.0,
                    metric_type=MetricType.COUNTER,
                    labels=labels,
                    description="护栏动作分类计数。",
                )
        if result.publish_allowed:
            self._record_metric(
                metric_name="guardrail_segment_publishable_total",
                value=1.0,
                metric_type=MetricType.COUNTER,
                labels=labels,
                description="通过护栏并可发布的 segment 数。",
            )
        self._record_event(
            event_name="guardrail.stage.completed",
            level=StructuredLogLevel.INFO,
            safe_fields={
                "stage": request.stage.value,
                "status": result.status.value,
                "generation_profile": (
                    request.context.generation_profile or _UNKNOWN_PROFILE
                ),
                "policy_count": len(policies),
                "duration_ms": duration_ms,
                "publish_allowed": result.publish_allowed,
                "trace_degraded": result.trace_degraded,
            },
        )

    def _base_metric_labels(
        self,
        *,
        request: GuardrailRunRequestDto,
        result: GuardrailRunResultDto,
    ) -> dict[str, str]:
        """构建护栏指标的低基数基础 label。

        :param request: 当前护栏运行请求。
        :param result: 当前阶段聚合结果。
        :return: 可写入 Observability 的低基数 label。
        """

        return {
            "component": _COMPONENT_NAME,
            "stage": request.stage.value,
            "status": result.status.value,
            "generation_profile": request.context.generation_profile
            or _UNKNOWN_PROFILE,
        }

    def _record_retry_metric(
        self,
        *,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
        attempt_index: int,
    ) -> None:
        """记录 handler 重试指标。

        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :param attempt_index: 当前零基尝试序号。
        :return: None。
        """

        if attempt_index + 1 >= policy.retry_policy.max_attempts:
            return
        self._record_metric(
            metric_name="guardrail_handler_retry_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels={
                "component": _COMPONENT_NAME,
                "stage": request.stage.value,
                "status": "retrying",
                "generation_profile": (
                    request.context.generation_profile or _UNKNOWN_PROFILE
                ),
                "handler_ref": policy.handler_ref,
            },
            description="handler 重试次数。",
        )

    def _record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: MetricType,
        labels: dict[str, str],
        description: str,
    ) -> None:
        """通过 ObservabilityProvider 记录指标。

        :param metric_name: 指标名称。
        :param value: 指标值。
        :param metric_type: 指标类型。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: None。
        """

        if self._observability_provider is None:
            return
        self._observability_provider.record_metric(
            metric_name=metric_name,
            value=value,
            metric_type=metric_type,
            labels=labels,
            description=description,
        )

    def _record_event(
        self,
        *,
        event_name: str,
        level: StructuredLogLevel,
        safe_fields: dict[str, object],
    ) -> None:
        """通过 ObservabilityProvider 记录结构化事件。

        :param event_name: 事件名称。
        :param level: 结构化日志级别。
        :param safe_fields: 安全结构化字段。
        :return: None。
        """

        if self._observability_provider is None:
            return
        self._observability_provider.record_event(
            event_name=event_name,
            component=_COMPONENT_NAME,
            level=level,
            safe_fields=safe_fields,
        )

    def _build_error(
        self,
        *,
        code: GuardrailFrameworkErrorCode,
        operation: GuardrailFrameworkOperation,
        message: str,
        request: GuardrailRunRequestDto,
        retryable: bool,
        policy: GuardrailPolicyDto | None = None,
        conflict_with: dict[str, object] | None = None,
    ) -> GuardrailFrameworkError:
        """构建 GuardrailFramework 领域异常。

        :param code: 稳定错误码。
        :param operation: 发生错误的操作名。
        :param message: 面向工程排障的错误说明。
        :param request: 当前护栏运行请求。
        :param retryable: 当前错误是否可重试。
        :param policy: 可选关联策略。
        :param conflict_with: 可选冲突摘要。
        :return: GuardrailFramework 领域异常。
        """

        return GuardrailFrameworkError(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            stage=request.stage,
            request_id=request.context.request_id,
            trace_id=request.context.trace_id,
            run_id=request.context.run_id,
            task_id=request.context.task_id,
            segment_id=request.context.segment_id,
            policy_id=policy.policy_id if policy is not None else None,
            handler_ref=policy.handler_ref if policy is not None else None,
            conflict_with=conflict_with,
        )


def create_default_guardrail_framework(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    observability_provider: ObservabilityProvider | None = None,
    policy_registry: GuardrailPolicyRegistry | None = None,
    handler_registry: GuardrailHandlerRegistry | None = None,
    fallback_template_provider: FallbackTemplateProvider | None = None,
    trace_sink: GuardrailTraceSink | None = None,
) -> GuardrailFramework:
    """创建默认 GuardrailFramework 应用内服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param observability_provider: 可选 Observability provider。
    :param policy_registry: 可选策略注册表；未传入时根据 RuntimeConfig 构建默认策略。
    :param handler_registry: 可选 handler 注册表；未传入时注册 TODO handler。
    :param fallback_template_provider: 可选 fallback 模板端口。
    :param trace_sink: 可选护栏 trace 写入端口。
    :return: GuardrailFramework 应用内服务。
    """

    try:
        settings = runtime_config_provider.current_snapshot().guardrail_framework
    except RuntimeConfigError:
        settings = GuardrailFrameworkSettings()
    resolved_policy_registry = (
        policy_registry or build_default_guardrail_policy_registry(settings)
    )
    resolved_handler_registry = (
        handler_registry or build_default_guardrail_handler_registry(settings)
    )
    return DefaultGuardrailFramework(
        runtime_config_provider=runtime_config_provider,
        policy_registry=resolved_policy_registry,
        handler_registry=resolved_handler_registry,
        fallback_template_provider=fallback_template_provider,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultGuardrailFramework",
    "GuardrailFramework",
    "build_default_guardrail_handler_registry",
    "build_default_guardrail_policy_registry",
    "create_default_guardrail_framework",
)
