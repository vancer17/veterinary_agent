##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/dto.py
# 作用: 定义 GuardrailFramework 公共 DTO，覆盖策略、运行上下文、发现项、动作、结果和 trace 写入契约。
# 边界: 仅承载协议无关结构化数据，不执行 handler 调度、兽医业务判决、fallback 渲染或 trace 持久化。
##################################################################################################

import re
from typing import Any, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.guardrail_framework.enums import (
    GuardActionType,
    GuardrailFailureStrategy,
    GuardrailFindingSeverity,
    GuardrailFrameworkErrorCode,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceWriteStatus,
)

JsonMap: TypeAlias = dict[str, object]

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class GuardrailFrameworkDto(BaseModel):
    """GuardrailFramework DTO 严格模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串字段值。

        :param value: 原始 DTO 字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class GuardrailTimeoutPolicyDto(GuardrailFrameworkDto):
    """单个护栏策略的超时策略。"""

    stage_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=300.0,
        description="当前护栏阶段总超时，单位为秒。",
    )
    handler_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=300.0,
        description="单次 handler 调用超时，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relation(self) -> Self:
        """校验 handler 超时不超过阶段总超时。

        :return: 已通过关系校验的超时策略。
        :raises ValueError: 当 handler 超时大于阶段总超时时抛出。
        """

        if self.handler_timeout_seconds > self.stage_timeout_seconds:
            raise ValueError("handler_timeout_seconds 不得大于 stage_timeout_seconds")
        return self


class GuardrailRetryPolicyDto(GuardrailFrameworkDto):
    """单个护栏策略的有限重试策略。"""

    max_attempts: int = Field(
        default=1,
        ge=1,
        le=5,
        description="handler 最大尝试次数，包含首次调用。",
    )
    retry_on_timeout: bool = Field(
        default=False,
        description="handler 超时时是否允许重试。",
    )
    retry_on_handler_error: bool = Field(
        default=False,
        description="handler 抛出普通异常时是否允许重试。",
    )


class GuardrailFailurePolicyDto(GuardrailFrameworkDto):
    """单个护栏策略的失败处理策略。"""

    strategy: GuardrailFailureStrategy = Field(description="handler 失败后的处理策略。")
    fallback_template_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="失败策略为 fallback 时使用的模板引用。",
    )

    @model_validator(mode="after")
    def _validate_fallback_relation(self) -> Self:
        """校验 fallback 策略必须携带模板引用。

        :return: 已通过关系校验的失败策略。
        :raises ValueError: 当 fallback 策略缺少模板引用时抛出。
        """

        if (
            self.strategy is GuardrailFailureStrategy.FALLBACK
            and self.fallback_template_ref is None
        ):
            raise ValueError("failure strategy 为 fallback 时必须携带模板引用")
        return self


class GuardrailTracePolicyDto(GuardrailFrameworkDto):
    """单个护栏策略的 trace 策略。"""

    emit_events: bool = Field(default=True, description="是否写入护栏 trace 事件。")
    persist_full_text: bool = Field(
        default=False,
        description="是否允许 trace 写入完整正文；默认仅写引用、hash 或摘要。",
    )
    capture_policy_ref: str = Field(
        default="guardrail.trace.v1",
        min_length=1,
        max_length=128,
        description="当前护栏事件使用的捕获策略引用。",
    )


class GuardrailPolicyDto(GuardrailFrameworkDto):
    """版本化护栏策略。"""

    policy_id: str = Field(min_length=1, max_length=128, description="护栏策略 ID。")
    policy_version: str = Field(
        min_length=1,
        max_length=128,
        description="护栏策略版本。",
    )
    stage: GuardrailStage = Field(description="策略所属护栏阶段。")
    handler_ref: str = Field(
        min_length=1,
        max_length=256,
        description="策略绑定的 handler 引用。",
    )
    enabled: bool = Field(default=True, description="当前策略是否启用。")
    timeout_policy: GuardrailTimeoutPolicyDto = Field(
        default_factory=GuardrailTimeoutPolicyDto,
        description="策略超时配置。",
    )
    retry_policy: GuardrailRetryPolicyDto = Field(
        default_factory=GuardrailRetryPolicyDto,
        description="策略重试配置。",
    )
    failure_policy: GuardrailFailurePolicyDto = Field(
        default_factory=lambda: GuardrailFailurePolicyDto(
            strategy=GuardrailFailureStrategy.FAIL_CLOSED_BLOCK
        ),
        description="策略失败处理配置。",
    )
    trace_policy: GuardrailTracePolicyDto = Field(
        default_factory=GuardrailTracePolicyDto,
        description="策略 trace 配置。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="策略轻量元信息。")

    @field_validator("policy_id", "policy_version", "handler_ref")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        """校验策略标识字段格式。

        :param value: 待校验的标识字段。
        :return: 已通过格式校验的字段值。
        :raises ValueError: 当标识字段包含不允许字符时抛出。
        """

        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("标识仅允许字母、数字、点、下划线、冒号和连字符")
        return value


class GuardrailRunContextDto(GuardrailFrameworkDto):
    """单次护栏运行上下文。"""

    run_id: str = Field(min_length=1, description="图运行 ID。")
    trace_id: str = Field(min_length=1, description="全链路 trace ID。")
    request_id: str = Field(min_length=1, description="入口请求 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    pet_id: str = Field(min_length=1, description="宠物 ID。")
    task_id: str = Field(min_length=1, description="业务子任务 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="可选 segment ID。",
    )
    generation_profile: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="业务生成剖面。",
    )
    params_version: str = Field(min_length=1, description="业务参数版本。")
    config_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        description="配置快照 ID。",
    )


class GuardrailRunRequestDto(GuardrailFrameworkDto):
    """单次护栏阶段运行请求。"""

    stage: GuardrailStage = Field(description="请求执行的护栏阶段。")
    context: GuardrailRunContextDto = Field(description="护栏运行上下文。")
    task_input: JsonMap = Field(default_factory=dict, description="任务输入摘要。")
    candidate_text_ref: str | None = Field(
        default=None,
        min_length=1,
        description="候选文本引用、摘要或受控正文引用。",
    )
    runtime_metadata: JsonMap = Field(
        default_factory=dict,
        description="运行时轻量元信息，不得承载敏感长正文。",
    )


class GuardrailFindingDto(GuardrailFrameworkDto):
    """护栏发现项。"""

    finding_id: str = Field(min_length=1, max_length=128, description="发现项 ID。")
    category: str = Field(min_length=1, max_length=128, description="发现项类别。")
    severity: GuardrailFindingSeverity = Field(description="发现项严重程度。")
    reason_code: str = Field(
        min_length=1,
        max_length=128,
        description="业务或框架 reason code。",
    )
    evidence_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="证据引用或摘要引用。",
    )
    source_handler: str = Field(
        min_length=1,
        max_length=256,
        description="产生该发现项的 handler 引用。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="发现项轻量元信息。")


class GuardActionDto(GuardrailFrameworkDto):
    """护栏动作记录。"""

    action_id: str = Field(min_length=1, max_length=160, description="动作 ID。")
    stage: GuardrailStage = Field(description="动作所属护栏阶段。")
    action_type: GuardActionType = Field(description="动作类型。")
    reason_code: str = Field(
        min_length=1,
        max_length=128,
        description="动作原因码。",
    )
    handler_ref: str = Field(
        min_length=1,
        max_length=256,
        description="产生动作的 handler 引用。",
    )
    before_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="动作前文本或对象引用。",
    )
    after_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="动作后文本或对象引用。",
    )
    policy_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="关联护栏策略 ID。",
    )
    policy_version: str = Field(
        min_length=1,
        max_length=128,
        description="关联护栏策略版本。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="动作轻量元信息。")


class GuardrailRunResultDto(GuardrailFrameworkDto):
    """单次护栏阶段运行结果。"""

    status: GuardrailStatus = Field(description="阶段执行状态。")
    reviewed_text_ref: str | None = Field(
        default=None,
        min_length=1,
        description="审查或改写后的候选文本引用。",
    )
    final_text_ref: str | None = Field(
        default=None,
        min_length=1,
        description="通过 gate 或 fallback 后的最终文本引用。",
    )
    publish_allowed: bool = Field(
        default=False,
        description="当前结果是否允许进入用户可见发布队列。",
    )
    fallback_triggered: bool = Field(
        default=False,
        description="当前阶段是否触发 fallback。",
    )
    fallback_template_version: str | None = Field(
        default=None,
        min_length=1,
        description="触发 fallback 时使用的模板版本。",
    )
    findings: list[GuardrailFindingDto] = Field(
        default_factory=list,
        description="标准化护栏发现项。",
    )
    actions: list[GuardActionDto] = Field(
        default_factory=list,
        description="标准化护栏动作。",
    )
    degraded_mode: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="降级模式标识。",
    )
    error_code: GuardrailFrameworkErrorCode | None = Field(
        default=None,
        description="阶段失败或降级时的框架错误码。",
    )
    trace_degraded: bool = Field(default=False, description="trace 写入是否降级。")
    metadata: JsonMap = Field(default_factory=dict, description="结果轻量元信息。")

    @model_validator(mode="after")
    def _validate_result_relations(self) -> Self:
        """校验运行结果的发布与 fallback 关系。

        :return: 已通过关系校验的护栏运行结果。
        :raises ValueError: 当发布许可或 fallback 字段组合非法时抛出。
        """

        if self.fallback_triggered and self.fallback_template_version is None:
            raise ValueError("fallback_triggered=true 时必须携带模板版本")
        if self.status is GuardrailStatus.FALLBACK and not self.fallback_triggered:
            raise ValueError("status=fallback 时必须声明 fallback_triggered=true")
        if self.publish_allowed and self.status in {
            GuardrailStatus.BLOCKED,
            GuardrailStatus.DEGRADED,
            GuardrailStatus.FAILED,
            GuardrailStatus.FALLBACK,
        }:
            raise ValueError("阻断、降级、失败或待 gate 的 fallback 结果不得发布")
        return self


class GuardrailTraceRecordDto(GuardrailFrameworkDto):
    """GuardrailFramework 写入 trace sink 的标准记录。"""

    request: GuardrailRunRequestDto = Field(description="护栏运行请求。")
    result: GuardrailRunResultDto = Field(description="护栏运行结果。")
    policies: list[GuardrailPolicyDto] = Field(
        default_factory=list,
        description="本阶段实际参与执行的策略。",
    )
    duration_ms: int = Field(ge=0, description="阶段执行耗时，单位为毫秒。")


class GuardrailTraceWriteResultDto(GuardrailFrameworkDto):
    """GuardrailFramework trace 写入结果。"""

    status: GuardrailTraceWriteStatus = Field(description="trace 写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="trace 降级或失败时的错误码。",
    )
    retryable: bool = Field(default=False, description="trace 写入是否可补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="面向工程排障的写入说明。",
    )


class FallbackTemplateDto(GuardrailFrameworkDto):
    """GuardrailFramework 使用的 fallback 模板结果。"""

    template_ref: str = Field(min_length=1, description="fallback 模板引用。")
    template_version: str = Field(min_length=1, description="fallback 模板版本。")
    text_ref: str = Field(min_length=1, description="fallback 文本引用或受控正文。")
    metadata: JsonMap = Field(default_factory=dict, description="模板轻量元信息。")


__all__: tuple[str, ...] = (
    "FallbackTemplateDto",
    "GuardActionDto",
    "GuardrailFailurePolicyDto",
    "GuardrailFindingDto",
    "GuardrailFrameworkDto",
    "GuardrailPolicyDto",
    "GuardrailRetryPolicyDto",
    "GuardrailRunContextDto",
    "GuardrailRunRequestDto",
    "GuardrailRunResultDto",
    "GuardrailTimeoutPolicyDto",
    "GuardrailTracePolicyDto",
    "GuardrailTraceRecordDto",
    "GuardrailTraceWriteResultDto",
    "JsonMap",
)
