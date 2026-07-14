##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/ports.py
# 作用: 定义 GuardrailFramework 对策略注册表、handler、fallback 模板和 trace sink 的应用内端口。
# 边界: 不实现 L2 兽医业务安全规则，不直接调用模型、工具、数据库或其它领域组件内部实现。
##################################################################################################

from typing import Protocol

from veterinary_agent.guardrail_framework.dto import (
    FallbackTemplateDto,
    GuardrailFindingDto,
    GuardrailPolicyDto,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailTraceRecordDto,
    GuardrailTraceWriteResultDto,
)
from veterinary_agent.guardrail_framework.enums import (
    GuardrailFindingSeverity,
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceWriteStatus,
)
from veterinary_agent.guardrail_framework.errors import GuardrailFrameworkError

TODO_GUARDRAIL_HANDLER_ERROR_CODE = "GUARDRAIL_HANDLER_NOT_IMPLEMENTED"
TODO_GUARDRAIL_TRACE_ERROR_CODE = "GUARDRAIL_TRACE_SINK_NOT_IMPLEMENTED"
TODO_FALLBACK_TEMPLATE_ERROR_CODE = "GUARDRAIL_FALLBACK_TEMPLATE_NOT_IMPLEMENTED"


class GuardrailHandler(Protocol):
    """GuardrailFramework 可调度的单个业务 handler 端口。"""

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """执行一次护栏 handler。

        :param policy: 当前执行的护栏策略。
        :param request: 当前护栏运行请求。
        :return: handler 标准化护栏运行结果。
        """

        ...


class GuardrailPolicyRegistry(Protocol):
    """GuardrailFramework 使用的策略注册表端口。"""

    def register_policy(self, policy: GuardrailPolicyDto) -> None:
        """注册或覆盖一条护栏策略。

        :param policy: 待注册的护栏策略。
        :return: None。
        """

        ...

    def resolve_policies(
        self,
        *,
        stage: GuardrailStage,
        generation_profile: str | None = None,
    ) -> list[GuardrailPolicyDto]:
        """按阶段解析需要执行的护栏策略。

        :param stage: 待执行的护栏阶段。
        :param generation_profile: 可选业务生成剖面。
        :return: 与阶段匹配的已启用护栏策略列表。
        """

        ...

    def validate_policy(self, policy: GuardrailPolicyDto) -> None:
        """校验单条护栏策略是否可被框架调度。

        :param policy: 待校验的护栏策略。
        :return: None。
        """

        ...


class GuardrailHandlerRegistry(Protocol):
    """GuardrailFramework 使用的 handler 注册表端口。"""

    def register_handler(self, *, handler_ref: str, handler: GuardrailHandler) -> None:
        """注册或覆盖一个 handler。

        :param handler_ref: handler 稳定引用。
        :param handler: 待注册的 handler 实例。
        :return: None。
        """

        ...

    def get_handler(self, handler_ref: str) -> GuardrailHandler | None:
        """读取指定 handler。

        :param handler_ref: handler 稳定引用。
        :return: 已注册 handler；不存在时返回 None。
        """

        ...


class GuardrailTraceSink(Protocol):
    """GuardrailFramework trace 写入端口。"""

    async def write_guardrail_trace(
        self,
        record: GuardrailTraceRecordDto,
    ) -> GuardrailTraceWriteResultDto:
        """写入一次护栏运行 trace 摘要。

        :param record: 待写入的护栏运行记录。
        :return: trace 写入结果。
        """

        ...


class FallbackTemplateProvider(Protocol):
    """GuardrailFramework 使用的 fallback 模板端口。"""

    async def render_fallback_template(
        self,
        *,
        template_ref: str,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> FallbackTemplateDto:
        """渲染或解析 fallback 模板。

        :param template_ref: fallback 模板引用。
        :param request: 当前护栏运行请求。
        :param policy: 当前护栏策略。
        :return: fallback 模板结果。
        """

        ...


class TodoGuardrailHandler:
    """L2 业务护栏 handler 尚未接入时使用的显式 TODO 空壳。"""

    def __init__(self, *, handler_ref: str) -> None:
        """初始化 TODO handler。

        :param handler_ref: 当前 TODO handler 对外暴露的稳定引用。
        :return: None。
        """

        self._handler_ref = handler_ref

    async def run_guardrail(
        self,
        *,
        policy: GuardrailPolicyDto,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """返回 handler 未实现的显式失败结果。

        :param policy: 当前执行的护栏策略。
        :param request: 当前护栏运行请求。
        :return: 标记 handler 未实现的失败结果。
        """

        return GuardrailRunResultDto(
            status=GuardrailStatus.FAILED,
            findings=[
                GuardrailFindingDto(
                    finding_id=f"{request.context.run_id}:{policy.policy_id}:todo",
                    category="guardrail_handler",
                    severity=GuardrailFindingSeverity.HIGH,
                    reason_code=TODO_GUARDRAIL_HANDLER_ERROR_CODE,
                    evidence_ref=policy.handler_ref,
                    source_handler=self._handler_ref,
                )
            ],
            error_code=GuardrailFrameworkErrorCode.GUARDRAIL_HANDLER_NOT_IMPLEMENTED,
            metadata={
                "policy_id": policy.policy_id,
                "stage": request.stage.value,
            },
        )


class TodoGuardrailTraceSink:
    """LogicTraceStore 尚未接入时使用的显式 TODO trace 空壳。"""

    async def write_guardrail_trace(
        self,
        record: GuardrailTraceRecordDto,
    ) -> GuardrailTraceWriteResultDto:
        """返回 GuardrailFramework trace 写入降级结果。

        :param record: 待写入的护栏运行记录；TODO 空壳不会持久化该记录。
        :return: 标记 trace 写入已降级的结果。
        """

        del record
        return GuardrailTraceWriteResultDto(
            status=GuardrailTraceWriteStatus.DEGRADED,
            error_code=TODO_GUARDRAIL_TRACE_ERROR_CODE,
            retryable=True,
            detail="GuardrailFramework LogicTraceStore 尚未接入",
        )


class TodoFallbackTemplateProvider:
    """fallback 模板源尚未接入时使用的显式 TODO 空壳。"""

    async def render_fallback_template(
        self,
        *,
        template_ref: str,
        request: GuardrailRunRequestDto,
        policy: GuardrailPolicyDto,
    ) -> FallbackTemplateDto:
        """拒绝渲染 fallback 模板并抛出模板不可用错误。

        :param template_ref: fallback 模板引用。
        :param request: 当前护栏运行请求。
        :param policy: 当前护栏策略。
        :return: 当前实现不会返回模板结果。
        :raises GuardrailFrameworkError: 始终抛出 fallback 模板不可用错误。
        """

        raise GuardrailFrameworkError(
            code=GuardrailFrameworkErrorCode.GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE,
            operation=GuardrailFrameworkOperation.RENDER_FALLBACK_TEMPLATE,
            message="GuardrailFramework fallback 模板源尚未接入",
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


__all__: tuple[str, ...] = (
    "FallbackTemplateProvider",
    "GuardrailHandler",
    "GuardrailHandlerRegistry",
    "GuardrailPolicyRegistry",
    "GuardrailTraceSink",
    "TODO_FALLBACK_TEMPLATE_ERROR_CODE",
    "TODO_GUARDRAIL_HANDLER_ERROR_CODE",
    "TODO_GUARDRAIL_TRACE_ERROR_CODE",
    "TodoFallbackTemplateProvider",
    "TodoGuardrailHandler",
    "TodoGuardrailTraceSink",
)
