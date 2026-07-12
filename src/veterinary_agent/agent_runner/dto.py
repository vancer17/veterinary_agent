##################################################################################################
# 文件: src/veterinary_agent/agent_runner/dto.py
# 作用: 定义 AgentRunner 公共 DTO，覆盖 Agent 规格、运行请求、运行结果、工具摘要与运行摘要留痕契约。
# 边界: 仅承载协议无关结构化数据，不执行模型调用、prompt 渲染、工具执行或业务安全判决。
##################################################################################################

import re
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.agent_runner.enums import (
    AgentResponseFormat,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
    AgentRunnerTraceWriteStatus,
    AgentRunStatus,
    AgentToolBindingStatus,
    AgentType,
)
from veterinary_agent.llm_gateway import LlmToolSchemaDto

JsonMap: TypeAlias = dict[str, object]

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class AgentRunnerDto(BaseModel):
    """AgentRunner DTO 严格模型基类。"""

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

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class AgentRunnerErrorDto(AgentRunnerDto):
    """AgentRunner 统一错误 DTO。"""

    code: AgentRunnerErrorCode = Field(description="AgentRunner 稳定错误码。")
    operation: AgentRunnerOperation = Field(
        description="发生错误的 AgentRunner 操作名。",
    )
    message: str = Field(min_length=1, description="面向工程排障的简短错误说明。")
    retryable: bool = Field(description="调用方是否可以稍后重试。")
    run_id: str | None = Field(default=None, min_length=1, description="运行 ID。")
    request_id: str | None = Field(
        default=None,
        min_length=1,
        description="入口请求 ID。",
    )
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        description="全链路追踪 ID。",
    )
    agent_id: str | None = Field(default=None, min_length=1, description="Agent ID。")
    agent_version: str | None = Field(
        default=None,
        min_length=1,
        description="Agent 版本。",
    )
    model_profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="模型 profile ID。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="不含敏感正文的冲突或下游状态摘要。",
    )


class PromptBlockDto(AgentRunnerDto):
    """已编译 prompt 上下文块。"""

    block_id: str = Field(min_length=1, max_length=128, description="上下文块 ID。")
    block_type: str = Field(
        min_length=1,
        max_length=128,
        description="上下文块类型。",
    )
    content_ref_or_text: str = Field(
        min_length=1,
        description="上下文块正文或上游已解析的受控引用文本。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通元信息。")

    @field_validator("block_id", "block_type")
    @classmethod
    def _validate_block_identity(cls, value: str) -> str:
        """校验上下文块标识字段。

        :param value: 原始上下文块标识字段值。
        :return: 通过格式校验的字段值。
        :raises ValueError: 当字段包含控制字符或不允许字符时抛出。
        """

        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("上下文块标识仅允许字母、数字、点、下划线、冒号和连字符")
        return value


class AgentToolPolicyDto(AgentRunnerDto):
    """Agent 工具权限策略摘要。"""

    policy_id: str = Field(
        default="default-deny",
        min_length=1,
        max_length=128,
        description="工具策略 ID。",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="当前 Agent 被授权绑定的工具名列表。",
    )
    tool_limits: JsonMap = Field(default_factory=dict, description="工具限制摘要。")

    @field_validator("policy_id")
    @classmethod
    def _validate_policy_id(cls, value: str) -> str:
        """校验工具策略 ID。

        :param value: 原始工具策略 ID。
        :return: 通过格式校验的工具策略 ID。
        :raises ValueError: 当工具策略 ID 包含非法字符时抛出。
        """

        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("工具策略 ID 仅允许字母、数字、点、下划线、冒号和连字符")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def _normalize_allowed_tools(cls, values: list[str]) -> list[str]:
        """规范化允许绑定的工具名列表。

        :param values: 原始工具名列表。
        :return: 去重且保持顺序的工具名列表。
        :raises ValueError: 当工具名为空或包含非法字符时抛出。
        """

        normalized: list[str] = []
        for value in values:
            tool_name = value.strip()
            if not tool_name:
                raise ValueError("allowed_tools 不得包含空工具名")
            if _TOOL_NAME_PATTERN.fullmatch(tool_name) is None:
                raise ValueError("工具名仅允许字母、数字、下划线和连字符")
            if tool_name not in normalized:
                normalized.append(tool_name)
        return normalized


class AgentTimeoutPolicyDto(AgentRunnerDto):
    """AgentRunner 单次运行超时策略。"""

    total_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=900.0,
        description="AgentRunner 单次运行总超时，单位为秒。",
    )


class AgentRetryPolicyDto(AgentRunnerDto):
    """AgentRunner 格式修复重试策略。"""

    max_format_repair_attempts: int = Field(
        default=0,
        ge=0,
        le=3,
        description="结构化解析或 schema 校验失败后的最多格式修复次数。",
    )


class AgentTracePolicyDto(AgentRunnerDto):
    """AgentRunner 运行摘要留痕策略。"""

    emit_run_summary: bool = Field(
        default=True,
        description="是否向运行摘要端口提交脱敏 Agent 运行摘要。",
    )
    persist_prompt: bool = Field(
        default=False,
        description="是否允许持久化完整 prompt；默认关闭。",
    )
    persist_raw_output: bool = Field(
        default=False,
        description="是否允许持久化模型原始输出；默认关闭。",
    )


class AgentSpecDto(AgentRunnerDto):
    """版本化 Agent 执行规格。"""

    agent_id: str = Field(min_length=1, max_length=128, description="Agent ID。")
    agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="Agent 版本。",
    )
    agent_type: AgentType = Field(
        default=AgentType.GENERIC,
        description="Agent 类型。",
    )
    model_profile: str = Field(
        min_length=1,
        max_length=128,
        description="LlmGateway 模型 profile ID。",
    )
    prompt_template_ref: str = Field(
        default="inline",
        min_length=1,
        max_length=256,
        description="prompt 模板引用。",
    )
    prompt_template: str = Field(
        min_length=1,
        description="首版内联 prompt 模板正文。",
    )
    output_schema_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="输出 JSON Schema 引用。",
    )
    output_schema: JsonMap | None = Field(
        default=None,
        description="首版内联输出 JSON Schema。",
    )
    output_schema_description: str | None = Field(
        default=None,
        max_length=4096,
        description="输出 schema 用途说明。",
    )
    response_format: AgentResponseFormat = Field(
        default=AgentResponseFormat.AUTO,
        description="期望模型响应格式。",
    )
    tool_policy: AgentToolPolicyDto = Field(
        default_factory=AgentToolPolicyDto,
        description="工具权限策略。",
    )
    timeout_policy: AgentTimeoutPolicyDto = Field(
        default_factory=AgentTimeoutPolicyDto,
        description="AgentRunner 超时策略。",
    )
    retry_policy: AgentRetryPolicyDto = Field(
        default_factory=AgentRetryPolicyDto,
        description="AgentRunner 格式修复重试策略。",
    )
    trace_policy: AgentTracePolicyDto = Field(
        default_factory=AgentTracePolicyDto,
        description="AgentRunner 运行摘要留痕策略。",
    )
    generation_params: JsonMap = Field(
        default_factory=dict,
        description="允许透传给 LlmGateway 的生成参数。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通元信息。")

    @field_validator("agent_id", "agent_version", "model_profile")
    @classmethod
    def _validate_spec_identity(cls, value: str) -> str:
        """校验 Agent 规格标识字段。

        :param value: 原始 Agent 规格标识字段值。
        :return: 通过格式校验的字段值。
        :raises ValueError: 当字段包含控制字符或不允许字符时抛出。
        """

        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError("Agent 规格标识仅允许字母、数字、点、下划线、冒号和连字符")
        return value

    @model_validator(mode="after")
    def _validate_response_schema_relation(self) -> "AgentSpecDto":
        """校验响应格式与输出 schema 的关系。

        :return: 当前 Agent 规格。
        :raises ValueError: 当 JSON Schema 响应格式缺少 schema 时抛出。
        """

        if (
            self.response_format is AgentResponseFormat.JSON_SCHEMA
            and self.output_schema is None
        ):
            raise ValueError("response_format=json_schema 时必须提供 output_schema")
        return self


class AgentRunRequestDto(AgentRunnerDto):
    """一次 AgentRunner 运行请求。"""

    run_id: str = Field(min_length=1, max_length=128, description="Agent 运行 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    agent_id: str = Field(min_length=1, max_length=128, description="Agent ID。")
    agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="Agent 版本。",
    )
    task_input: JsonMap = Field(default_factory=dict, description="业务任务输入。")
    prompt_blocks: list[PromptBlockDto] = Field(
        default_factory=list,
        description="上游已编译上下文块。",
    )
    runtime_options: JsonMap = Field(
        default_factory=dict,
        description="本次运行普通选项。",
    )

    @field_validator(
        "run_id",
        "trace_id",
        "request_id",
        "session_id",
        "user_id",
        "agent_id",
        "agent_version",
    )
    @classmethod
    def _validate_request_identity(cls, value: str) -> str:
        """校验运行请求关联标识字段。

        :param value: 原始关联标识字段值。
        :return: 通过格式校验的字段值。
        :raises ValueError: 当字段包含控制字符或不允许字符时抛出。
        """

        if _IDENTITY_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "运行请求关联字段仅允许字母、数字、点、下划线、冒号和连字符"
            )
        return value


class AgentValidationErrorDto(AgentRunnerDto):
    """AgentRunner 结构化校验错误。"""

    path: str = Field(min_length=1, description="错误字段路径。")
    message: str = Field(min_length=1, description="校验错误说明。")
    error_type: str = Field(min_length=1, description="错误类型。")


class AgentToolCallSummaryDto(AgentRunnerDto):
    """Agent 工具调用摘要。"""

    call_id: str = Field(min_length=1, description="工具调用 ID。")
    tool_name: str = Field(min_length=1, description="工具名称。")
    status: str = Field(min_length=1, description="工具调用状态。")
    latency_ms: int = Field(default=0, ge=0, description="工具调用耗时。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="标准工具错误码。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通摘要元信息。")


class AgentUsageSummaryDto(AgentRunnerDto):
    """Agent 模型 token 使用摘要。"""

    input_tokens: int = Field(default=0, ge=0, description="输入 token 数。")
    output_tokens: int = Field(default=0, ge=0, description="输出 token 数。")
    total_tokens: int = Field(default=0, ge=0, description="总 token 数。")
    estimated: bool = Field(default=False, description="token 数是否为估算值。")

    @model_validator(mode="after")
    def _normalize_total_tokens(self) -> "AgentUsageSummaryDto":
        """补齐并校验总 token 数。

        :return: 已补齐总量的 token 使用摘要。
        :raises ValueError: 当总量小于输入与输出之和时抛出。
        """

        calculated_total = self.input_tokens + self.output_tokens
        if self.total_tokens == 0 and calculated_total > 0:
            self.total_tokens = calculated_total
        if self.total_tokens < calculated_total:
            raise ValueError("total_tokens 不得小于 input_tokens 与 output_tokens 之和")
        return self


class AgentToolBindingResultDto(AgentRunnerDto):
    """Agent 工具绑定结果。"""

    status: AgentToolBindingStatus = Field(description="工具绑定状态。")
    tool_schemas: list[LlmToolSchemaDto] = Field(
        default_factory=list,
        description="已授权并可传给 LlmGateway 的工具 schema。",
    )
    tool_call_summaries: list[AgentToolCallSummaryDto] = Field(
        default_factory=list,
        description="工具绑定或调用摘要。",
    )
    trace_delivery_status: AgentRunnerTraceWriteStatus = Field(
        default=AgentRunnerTraceWriteStatus.SKIPPED,
        description="工具绑定摘要 trace 写入状态。",
    )


class AgentPromptEstimateDto(AgentRunnerDto):
    """Agent prompt token 预算估算结果。"""

    agent_id: str = Field(min_length=1, description="Agent ID。")
    agent_version: str = Field(min_length=1, description="Agent 版本。")
    model_profile: str = Field(min_length=1, description="模型 profile ID。")
    input_tokens: int = Field(ge=0, description="估算输入 token 数。")
    reserved_output_tokens: int = Field(ge=1, description="预留输出 token 数。")
    total_budget_tokens: int = Field(ge=1, description="总 token 预算。")
    max_context_tokens: int = Field(ge=1, description="上下文窗口上限。")
    estimated: bool = Field(default=True, description="是否为估算值。")


class AgentRunnerTraceWriteResultDto(AgentRunnerDto):
    """AgentRunner 运行摘要写入结果。"""

    status: AgentRunnerTraceWriteStatus = Field(description="运行摘要写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="写入降级或失败错误码。",
    )
    retryable: bool = Field(default=False, description="调用方是否可稍后重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="脱敏写入状态说明。",
    )


class AgentRunSummaryDto(AgentRunnerDto):
    """可提交给 LogicTraceStore 的脱敏 Agent 运行摘要。"""

    run_id: str = Field(min_length=1, description="Agent 运行 ID。")
    trace_id: str = Field(min_length=1, description="全链路追踪 ID。")
    request_id: str = Field(min_length=1, description="入口请求 ID。")
    agent_id: str = Field(min_length=1, description="Agent ID。")
    agent_version: str = Field(min_length=1, description="Agent 版本。")
    model_profile: str = Field(min_length=1, description="请求的模型 profile。")
    actual_model: str | None = Field(
        default=None,
        min_length=1,
        description="供应商返回的实际模型标识。",
    )
    status: AgentRunStatus = Field(description="Agent 运行状态。")
    schema_valid: bool = Field(description="输出 schema 是否校验通过。")
    usage: AgentUsageSummaryDto = Field(description="token 使用摘要。")
    latency_ms: int = Field(ge=0, description="Agent 运行耗时。")
    retry_count: int = Field(ge=0, description="AgentRunner 格式修复重试次数。")
    error_code: AgentRunnerErrorCode | None = Field(
        default=None,
        description="失败时的 AgentRunner 标准错误码。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通摘要元信息。")


class AgentRunResultDto(AgentRunnerDto):
    """一次 AgentRunner 运行结果。"""

    status: AgentRunStatus = Field(description="Agent 运行状态。")
    agent_id: str = Field(min_length=1, description="Agent ID。")
    agent_version: str = Field(min_length=1, description="Agent 版本。")
    model_profile: str | None = Field(
        default=None,
        min_length=1,
        description="请求的模型 profile。",
    )
    model_id: str | None = Field(
        default=None,
        min_length=1,
        description="实际模型标识。",
    )
    parsed_output: JsonMap = Field(default_factory=dict, description="结构化输出。")
    raw_output_ref: str | None = Field(
        default=None,
        min_length=1,
        description="原始输出 artifact 引用或 hash。",
    )
    schema_valid: bool = Field(default=False, description="schema 是否校验通过。")
    validation_errors: list[AgentValidationErrorDto] = Field(
        default_factory=list,
        description="结构化解析或 schema 校验错误。",
    )
    tool_call_summaries: list[AgentToolCallSummaryDto] = Field(
        default_factory=list,
        description="工具调用摘要。",
    )
    usage: AgentUsageSummaryDto = Field(
        default_factory=AgentUsageSummaryDto,
        description="token 使用摘要。",
    )
    latency_ms: int = Field(default=0, ge=0, description="Agent 运行耗时。")
    retry_count: int = Field(default=0, ge=0, description="格式修复重试次数。")
    model_call_id: str | None = Field(
        default=None,
        min_length=1,
        description="LlmGateway 逻辑模型调用 ID。",
    )
    trace_delivery_status: AgentRunnerTraceWriteStatus = Field(
        default=AgentRunnerTraceWriteStatus.SKIPPED,
        description="AgentRunner 运行摘要 trace 写入状态。",
    )
    error: AgentRunnerErrorDto | None = Field(
        default=None,
        description="失败时的标准错误对象。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通结果元信息。")


__all__: tuple[str, ...] = (
    "AgentPromptEstimateDto",
    "AgentRetryPolicyDto",
    "AgentRunRequestDto",
    "AgentRunResultDto",
    "AgentRunSummaryDto",
    "AgentRunnerDto",
    "AgentRunnerErrorDto",
    "AgentRunnerTraceWriteResultDto",
    "AgentSpecDto",
    "AgentTimeoutPolicyDto",
    "AgentToolBindingResultDto",
    "AgentToolCallSummaryDto",
    "AgentToolPolicyDto",
    "AgentTracePolicyDto",
    "AgentUsageSummaryDto",
    "AgentValidationErrorDto",
    "JsonMap",
    "PromptBlockDto",
)
