##################################################################################################
# 文件: tests/guardrail_framework/helpers.py
# 作用: 提供 GuardrailFramework 组件级测试使用的内存 handler、trace sink、fallback provider 与请求构造工具。
# 边界: 仅服务测试契约验证，不实现兽医业务安全规则、不连接真实 LogicTraceStore 或外部服务。
##################################################################################################

import asyncio
from collections.abc import Sequence
from typing import cast

from veterinary_agent.config import (
    GuardrailFrameworkSettings,
    GuardrailFrameworkStageSettings,
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.guardrail_framework import (
    FallbackTemplateDto,
    FallbackTemplateProvider,
    GuardActionDto,
    GuardActionType,
    GuardrailFindingDto,
    GuardrailFrameworkError,
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailFramework,
    GuardrailHandler,
    GuardrailPolicyDto,
    GuardrailRunContextDto,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceRecordDto,
    GuardrailTraceWriteResultDto,
    GuardrailTraceWriteStatus,
    InMemoryGuardrailHandlerRegistry,
    create_default_guardrail_framework,
)


class StaticGuardrailHandler:
    """返回固定 GuardrailRunResult 的测试 handler。"""

    def __init__(self, result: GuardrailRunResultDto) -> None:
        """初始化固定结果 handler。

        :param result: 每次调用时返回的护栏结果。
        :return: None。
        """

        self.result = result
        self.call_count = 0
        self.requests: list[GuardrailRunRequestDto] = []
        self.policies: list[GuardrailPolicyDto] = []

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """记录调用并返回固定护栏结果。

        :param policy: 当前执行的护栏策略。
        :param request: 当前护栏运行请求。
        :return: 初始化时传入的护栏结果。
        """

        self.call_count += 1
        self.requests.append(request)
        self.policies.append(policy)
        return self.result


class TimeoutGuardrailHandler:
    """通过 sleep 模拟 handler 超时的测试 handler。"""

    def __init__(self, *, sleep_seconds: float = 0.05) -> None:
        """初始化超时 handler。

        :param sleep_seconds: 每次调用等待的秒数。
        :return: None。
        """

        self.sleep_seconds = sleep_seconds
        self.call_count = 0

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """等待指定时间并返回允许结果。

        :param policy: 当前执行的护栏策略；本替身不读取该字段。
        :param request: 当前护栏运行请求；本替身不读取该字段。
        :return: 如果没有超时则返回允许结果。
        """

        del policy, request
        self.call_count += 1
        await asyncio.sleep(self.sleep_seconds)
        return GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)


class InvalidOutputGuardrailHandler:
    """返回非法输出对象的测试 handler。"""

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> object:
        """返回无法校验为 GuardrailRunResultDto 的对象。

        :param policy: 当前执行的护栏策略；本替身不读取该字段。
        :param request: 当前护栏运行请求；本替身不读取该字段。
        :return: 非法 handler 输出。
        """

        del policy, request
        return {"status": "not-a-valid-status"}


class RecordingGuardrailTraceSink:
    """记录 GuardrailFramework trace 写入请求的测试 sink。"""

    def __init__(
        self,
        *,
        status: GuardrailTraceWriteStatus = GuardrailTraceWriteStatus.RECORDED,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :return: None。
        """

        self.status = status
        self.records: list[GuardrailTraceRecordDto] = []

    async def write_guardrail_trace(
        self,
        record: GuardrailTraceRecordDto,
    ) -> GuardrailTraceWriteResultDto:
        """记录护栏 trace 并返回预设状态。

        :param record: 待记录的护栏 trace。
        :return: 预设 trace 写入结果。
        """

        self.records.append(record)
        return GuardrailTraceWriteResultDto(
            status=self.status,
            error_code=(
                "GUARDRAIL_TRACE_TEST_DEGRADED"
                if self.status is GuardrailTraceWriteStatus.DEGRADED
                else None
            ),
            retryable=self.status is GuardrailTraceWriteStatus.DEGRADED,
            detail="测试 trace 降级"
            if self.status is GuardrailTraceWriteStatus.DEGRADED
            else None,
        )


class RecordingFallbackTemplateProvider:
    """返回稳定 fallback 模板的测试 provider。"""

    def __init__(self) -> None:
        """初始化测试 fallback provider。

        :return: None。
        """

        self.calls: list[tuple[str, GuardrailRunRequestDto, GuardrailPolicyDto]] = []

    async def render_fallback_template(
        self,
        *,
        template_ref: str,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> FallbackTemplateDto:
        """返回稳定 fallback 文本引用。

        :param template_ref: fallback 模板引用。
        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :return: fallback 模板结果。
        """

        self.calls.append((template_ref, request, policy))
        return FallbackTemplateDto(
            template_ref=template_ref,
            template_version="fallback-template.v-test",
            text_ref="fallback-text-ref-test",
        )


class UnavailableFallbackTemplateProvider:
    """始终报告 fallback 模板不可用的测试 provider。"""

    async def render_fallback_template(
        self,
        *,
        template_ref: str,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> FallbackTemplateDto:
        """抛出 fallback 模板不可用错误。

        :param template_ref: fallback 模板引用。
        :param request: 当前护栏运行请求。
        :param policy: 当前执行的护栏策略。
        :return: 当前实现不会返回模板结果。
        :raises GuardrailFrameworkError: 始终抛出 fallback 模板不可用错误。
        """

        raise GuardrailFrameworkError(
            code=GuardrailFrameworkErrorCode.GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE,
            operation=GuardrailFrameworkOperation.RENDER_FALLBACK_TEMPLATE,
            message="测试 fallback 模板不可用",
            retryable=True,
            stage=request.stage,
            request_id=request.context.request_id,
            trace_id=request.context.trace_id,
            run_id=request.context.run_id,
            task_id=request.context.task_id,
            segment_id=request.context.segment_id,
            policy_id=policy.policy_id,
            handler_ref=policy.handler_ref,
            conflict_with={"template_ref": template_ref},
        )


def build_provider(
    *,
    settings: GuardrailFrameworkSettings | None = None,
) -> RuntimeConfigProvider:
    """构建测试用 RuntimeConfigProvider。

    :param settings: 可选 GuardrailFramework 配置；未传入时使用默认配置。
    :return: RuntimeConfig provider。
    """

    return create_runtime_config_provider(guardrail_framework_settings=settings)


def build_settings(
    *,
    post_failure_strategy: str = "fail_closed_block",
    post_fallback_template_ref: str | None = None,
    post_max_attempts: int = 1,
    post_retry_on_timeout: bool = False,
    post_handler_timeout_seconds: float = 10.0,
    post_stage_timeout_seconds: float = 12.0,
) -> GuardrailFrameworkSettings:
    """构建测试用 GuardrailFrameworkSettings。

    :param post_failure_strategy: 生成后审查阶段失败策略。
    :param post_fallback_template_ref: 生成后审查阶段 fallback 模板引用。
    :param post_max_attempts: 生成后审查阶段最大尝试次数。
    :param post_retry_on_timeout: 生成后审查阶段超时后是否重试。
    :param post_handler_timeout_seconds: 生成后审查阶段单次 handler 超时。
    :param post_stage_timeout_seconds: 生成后审查阶段总超时。
    :return: GuardrailFramework 运行配置。
    """

    return GuardrailFrameworkSettings(
        post_generation_review=GuardrailFrameworkStageSettings(
            policy_id="guardrail.post_generation_review.test",
            policy_version="guardrail-policy.test",
            handler_ref="test_post_generation_review",
            stage_timeout_seconds=post_stage_timeout_seconds,
            handler_timeout_seconds=post_handler_timeout_seconds,
            max_attempts=post_max_attempts,
            retry_on_timeout=post_retry_on_timeout,
            retry_on_handler_error=False,
            failure_strategy=post_failure_strategy,
            fallback_template_ref=post_fallback_template_ref,
        )
    )


def build_request(
    *,
    stage: GuardrailStage,
    provider: RuntimeConfigProvider,
    task_id: str = "task-test",
    segment_id: str | None = "segment-test",
    candidate_text_ref: str | None = "draft-ref-test",
) -> GuardrailRunRequestDto:
    """构建测试用护栏运行请求。

    :param stage: 请求声明的护栏阶段。
    :param provider: 用于读取 params_version 与 config_snapshot_id 的配置 provider。
    :param task_id: 请求绑定的任务 ID。
    :param segment_id: 可选 segment ID。
    :param candidate_text_ref: 可选候选文本引用。
    :return: GuardrailFramework 运行请求。
    """

    snapshot = provider.current_snapshot()
    return GuardrailRunRequestDto(
        stage=stage,
        context=GuardrailRunContextDto(
            run_id="run-test",
            trace_id="trace-test",
            request_id="request-test",
            session_id="session-test",
            user_id="user-test",
            pet_id="pet-test",
            task_id=task_id,
            segment_id=segment_id,
            generation_profile="standard",
            params_version=snapshot.params_version,
            config_snapshot_id=snapshot.config_snapshot_id,
        ),
        candidate_text_ref=candidate_text_ref,
        task_input={"task_kind": "component_test"},
        runtime_metadata={"source": "guardrail_framework_component_test"},
    )


def build_handler_registry(
    *,
    provider: RuntimeConfigProvider,
    stage: GuardrailStage,
    handler: object | None,
) -> InMemoryGuardrailHandlerRegistry:
    """构建只注册指定阶段 handler 的内存 handler registry。

    :param provider: RuntimeConfig provider。
    :param stage: 需要读取 handler_ref 的护栏阶段。
    :param handler: 待注册 handler；为 None 时返回空 registry。
    :return: 内存 handler registry。
    """

    registry = InMemoryGuardrailHandlerRegistry()
    if handler is None:
        return registry
    settings = provider.current_snapshot().guardrail_framework
    handler_ref_by_stage = {
        GuardrailStage.PRE_GENERATION: settings.pre_generation.handler_ref,
        GuardrailStage.POST_GENERATION_REVIEW: (
            settings.post_generation_review.handler_ref
        ),
        GuardrailStage.DETERMINISTIC_GATE: settings.deterministic_gate.handler_ref,
    }
    registry.register_handler(
        handler_ref=handler_ref_by_stage[stage],
        handler=cast(GuardrailHandler, handler),
    )
    return registry


def build_framework_with_handler(
    *,
    provider: RuntimeConfigProvider,
    stage: GuardrailStage,
    handler: object | None,
    trace_sink: RecordingGuardrailTraceSink | None = None,
    fallback_provider: FallbackTemplateProvider | None = None,
) -> GuardrailFramework:
    """构建注入测试 handler 的 GuardrailFramework。

    :param provider: RuntimeConfig provider。
    :param stage: 需要替换 handler 的护栏阶段。
    :param handler: 待注册 handler；为 None 时不注册。
    :param trace_sink: 可选测试 trace sink。
    :param fallback_provider: 可选测试 fallback provider。
    :return: GuardrailFramework 公共服务对象。
    """

    return create_default_guardrail_framework(
        runtime_config_provider=provider,
        handler_registry=build_handler_registry(
            provider=provider,
            stage=stage,
            handler=handler,
        ),
        trace_sink=trace_sink,
        fallback_template_provider=fallback_provider,
    )


def action_types(result: GuardrailRunResultDto) -> list[GuardActionType]:
    """读取护栏结果中的动作类型列表。

    :param result: 护栏运行结果。
    :return: 按结果动作顺序排列的动作类型列表。
    """

    return [action.action_type for action in result.actions]


def reason_codes(findings: Sequence[GuardrailFindingDto]) -> set[str]:
    """读取发现项 reason code 集合。

    :param findings: 护栏发现项序列。
    :return: 发现项 reason code 集合。
    """

    return {finding.reason_code for finding in findings}


def custom_action() -> GuardActionDto:
    """构建未补齐 policy_id 的测试动作。

    :return: 测试用护栏动作。
    """

    return GuardActionDto(
        action_id="custom-action-test",
        stage=GuardrailStage.POST_GENERATION_REVIEW,
        action_type=GuardActionType.REWRITE,
        reason_code="custom_rewrite",
        handler_ref="test_post_generation_review",
        before_ref="draft-ref-test",
        after_ref="reviewed-ref-test",
        policy_version="guardrail-policy.test",
    )
