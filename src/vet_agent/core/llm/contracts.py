"""
``core.llm`` 的稳定公共契约。

该模块定义模型角色、角色配置、请求与归一响应、typed 返回值、执行事件和
唯一模型调用协议。语义层只能依赖这些契约，不能感知 LiteLLM 或厂商 SDK。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, TypeVar
from urllib.parse import urlparse

from pydantic import SecretStr

from vet_agent.core.execution import ExecutionContext
from vet_agent.core.prompts import PromptMessage, RenderedPrompt
from vet_agent.core.schema import StrictModel

T = TypeVar("T", bound=StrictModel)


class ModelRole(StrEnum):
    """语义预处理各子层共享的稳定模型角色。

    角色名是架构契约，具体模型名由配置映射替换；语义层不得直接指定模型。
    """

    #: Turn Intent 控制协议生成模型。
    INTENT_CONTROL_GENERATOR = "intent_control_generator"

    #: Turn Intent 控制协议审查模型。
    INTENT_CONTROL_REVIEW = "intent_control_review"

    #: 请求完整性审查模型。
    REQUEST_COMPLETENESS_REVIEW = "request_completeness_review"

    #: 请求忠实性审查模型。
    REQUEST_FIDELITY_REVIEW = "request_fidelity_review"

    #: 请求上下文引用审查模型。
    REQUEST_CONTEXT_REFERENCE_REVIEW = "request_context_reference_review"

    #: Turn Intent 控制协议修复模型。
    INTENT_CONTROL_REPAIR = "intent_control_repair"

    #: request items 修复模型。
    REQUEST_ITEMS_REPAIR = "request_items_repair"

    #: 语义锚点生成模型。
    ANCHOR_GENERATOR = "anchor_generator"

    #: 语义锚点完整性审查模型。
    ANCHOR_COMPLETENESS_REVIEW = "anchor_completeness_review"

    #: 语义锚点来源边界审查模型。
    ANCHOR_SUPPORT_BOUNDARY_REVIEW = "anchor_support_boundary_review"

    #: 语义锚点状态与确定性审查模型。
    ANCHOR_STATE_CERTAINTY_REVIEW = "anchor_state_certainty_review"

    #: 语义锚点测量关系审查模型。
    ANCHOR_MEASUREMENT_RELATION_REVIEW = "anchor_measurement_relation_review"

    #: 语义锚点主体与形式原子性审查模型。
    ANCHOR_SUBJECT_FORM_ATOMICITY_REVIEW = "anchor_subject_form_atomicity_review"

    #: 语义锚点修复模型。
    ANCHOR_REPAIR = "anchor_repair"

    #: 联合上下文 scope 规划模型。
    SCOPE_PLANNER = "scope_planner"

    #: 联合上下文 scope 支持边界审查模型。
    SCOPE_CONTEXT_SUPPORT_REVIEW = "scope_context_support_review"

    #: 联合上下文 scope 边界审查模型。
    SCOPE_BOUNDARY_REVIEW = "scope_boundary_review"

    #: 联合上下文 scope 时间冗余审查模型。
    SCOPE_TEMPORAL_REDUNDANCY_REVIEW = "scope_temporal_redundancy_review"

    #: 联合上下文 scope 修复模型。
    SCOPE_REPAIR = "scope_repair"

    #: 上下文查询 proposition 生成模型。
    CONTEXTUAL_PROPOSITION_GENERATOR = "contextual_proposition_generator"

    #: 上下文查询 proposition 支持审查模型。
    CONTEXTUAL_PROPOSITION_SUPPORT_REVIEW = "contextual_proposition_support_review"

    #: 上下文查询 proposition 形式与来源审查模型。
    CONTEXTUAL_PROPOSITION_FORM_SOURCE_REVIEW = (
        "contextual_proposition_form_source_review"
    )

    #: 上下文查询 proposition 状态确定性审查模型。
    CONTEXTUAL_PROPOSITION_STATE_CERTAINTY_REVIEW = (
        "contextual_proposition_state_certainty_review"
    )

    #: 上下文查询 proposition 测量关系审查模型。
    CONTEXTUAL_PROPOSITION_MEASUREMENT_RELATION_REVIEW = (
        "contextual_proposition_measurement_relation_review"
    )

    #: 上下文查询 proposition 边界审查模型。
    CONTEXTUAL_PROPOSITION_BOUNDARY_REVIEW = "contextual_proposition_boundary_review"

    #: 上下文查询 proposition 修复模型。
    CONTEXTUAL_PROPOSITION_REPAIR = "contextual_proposition_repair"


class StrictResponseFormat(StrEnum):
    """模型 JSON 响应格式的受控开关。

    即使使用 JSON mode，返回内容仍必须经过 Strict JSON Gateway；该格式
    只约束传输请求参数，不替代 schema 校验。
    """

    #: 请求 OpenAI-compatible JSON object mode。
    JSON_OBJECT = "json_object"

    #: 不向传输层发送 response_format；仅用于兼容性明确的显式配置。
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ModelRoleConfig:
    """单个模型角色的不可变调用配置。

    :ivar model: LiteLLM OpenAI-compatible 模型标识。
    :ivar max_tokens: 该角色允许的最大 completion token 数。
    :ivar timeout_seconds: 单次物理尝试超时时间。
    :ivar temperature: 固定采样温度，默认低随机性。
    :ivar max_concurrency: 该角色并发上限。
    :ivar response_format: 传输层 JSON 格式策略。
    """

    model: str
    max_tokens: int
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_concurrency: int = 4
    response_format: StrictResponseFormat = StrictResponseFormat.JSON_OBJECT

    def __post_init__(self) -> None:
        """校验模型角色配置的工程边界。

        :return: 无返回值；配置非法时在启动阶段显式失败。
        :raises vet_agent.core.errors.ConfigurationError: 当任一字段超出允许范围。
        """
        from vet_agent.core import errors as core_errors

        invalid_reasons: list[str] = []
        if not self.model:
            invalid_reasons.append("model_empty")
        if self.max_tokens <= 0:
            invalid_reasons.append("max_tokens_not_positive")
        if self.timeout_seconds <= 0:
            invalid_reasons.append("timeout_not_positive")
        if not 0.0 <= self.temperature <= 1.0:
            invalid_reasons.append("temperature_out_of_range")
        if self.max_concurrency <= 0:
            invalid_reasons.append("max_concurrency_not_positive")
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="模型角色配置非法",
                context={"reasons": ",".join(invalid_reasons)},
            )


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """模型网关的不可变全局配置。

    :ivar base_url: LiteLLM OpenAI-compatible 服务地址。
    :ivar api_key: 仅存在进程内的密钥对象。
    :ivar role_configs: 稳定模型角色到具体调用参数的映射。
    :ivar max_global_concurrency: 全局物理请求并发上限。
    :ivar max_transport_retries: 传输失败后的最大额外尝试次数。
    :ivar retry_initial_delay_seconds: 重试退避初始等待时间。
    :ivar retry_max_delay_seconds: 重试退避最大等待时间。
    :ivar max_request_attempts: 请求次数预算；``None`` 表示不启用。
    :ivar max_total_tokens: token 总量预算；``None`` 表示不启用。
    """

    base_url: str
    api_key: SecretStr
    role_configs: Mapping[ModelRole, ModelRoleConfig] = field(default_factory=dict)
    max_global_concurrency: int = 16
    max_transport_retries: int = 2
    retry_initial_delay_seconds: float = 0.2
    retry_max_delay_seconds: float = 30.0
    max_request_attempts: int | None = None
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        """校验并冻结模型网关全局配置。

        :return: 无返回值；配置非法时在对象构造阶段显式失败。
        :raises vet_agent.core.errors.ConfigurationError: 当连接、重试、预算或
            角色映射非法。
        """
        from vet_agent.core import errors as core_errors

        invalid_reasons: list[str] = []
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            invalid_reasons.append("base_url_invalid")
        if not self.api_key.get_secret_value():
            invalid_reasons.append("api_key_empty")
        if self.max_global_concurrency <= 0:
            invalid_reasons.append("max_global_concurrency_not_positive")
        if self.max_transport_retries < 0:
            invalid_reasons.append("max_transport_retries_negative")
        if self.retry_initial_delay_seconds <= 0:
            invalid_reasons.append("retry_initial_delay_not_positive")
        if self.retry_max_delay_seconds < self.retry_initial_delay_seconds:
            invalid_reasons.append("retry_max_delay_too_small")
        if self.max_request_attempts is not None and self.max_request_attempts <= 0:
            invalid_reasons.append("max_request_attempts_not_positive")
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            invalid_reasons.append("max_total_tokens_not_positive")
        if not self.role_configs:
            invalid_reasons.append("role_configs_empty")
        object.__setattr__(
            self,
            "role_configs",
            MappingProxyType(dict(self.role_configs)),
        )
        if invalid_reasons:
            raise core_errors.ConfigurationError(
                error_code=core_errors.CoreErrorCode.CONFIGURATION_INVALID,
                message="LLM 网关配置非法",
                context={"reasons": ",".join(invalid_reasons)},
            )


@dataclass(frozen=True, slots=True)
class ChatCompletionRequest:
    """传输层收到的一次物理模型请求。

    :ivar request_id: 逻辑调用标识；重试时保持不变。
    :ivar attempt_id: 物理尝试标识；每次请求重新生成。
    :ivar model: 已由角色配置解析出的模型标识。
    :ivar model_role: 稳定模型角色名。
    :ivar prompt_id: 版本化 prompt 标识。
    :ivar prompt_version: prompt 版本。
    :ivar prompt_sha256: prompt 模板摘要。
    :ivar messages: 已渲染模型消息。
    :ivar temperature: 固定采样温度。
    :ivar max_tokens: completion 上限。
    :ivar timeout_seconds: 本次尝试超时时间。
    :ivar response_format: 传输层 JSON 格式策略。
    """

    request_id: str
    attempt_id: str
    model: str
    model_role: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    messages: tuple[PromptMessage, ...]
    temperature: float
    max_tokens: int
    timeout_seconds: float
    response_format: StrictResponseFormat


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """归一化后的模型 token 用量。

    :ivar prompt_tokens: 输入 token 数。
    :ivar completion_tokens: 输出 token 数。
    :ivar total_tokens: 输入与输出 token 总数。
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class NormalizedChatCompletion:
    """通过完成协议校验后的模型响应。

    :ivar content: 非空模型文本内容，等待严格 JSON 解析。
    :ivar response_id: 提供方返回的响应标识。
    :ivar model: 提供方实际返回的模型名。
    :ivar finish_reason: 完成原因；当前只允许 ``stop``。
    :ivar usage: 必须存在的 token 用量。
    """

    content: str
    response_id: str | None
    model: str
    finish_reason: str
    usage: LLMUsage


@dataclass(frozen=True, slots=True)
class LLMResponseMetadata:
    """一次逻辑模型调用的执行 metadata。

    :ivar request_id: 逻辑调用标识。
    :ivar model: 提供方实际返回的模型名。
    :ivar model_role: 稳定模型角色名。
    :ivar prompt_id: 版本化 prompt 标识。
    :ivar prompt_version: prompt 版本。
    :ivar prompt_sha256: prompt 模板摘要。
    :ivar response_id: 提供方响应标识。
    :ivar latency_ms: 逻辑调用耗时。
    :ivar prompt_tokens: 输入 token 数。
    :ivar completion_tokens: 输出 token 数。
    :ivar total_tokens: 输入与输出 token 总数。
    :ivar finish_reason: 完成原因。
    :ivar attempt_count: 物理尝试次数。
    """

    request_id: str
    model: str
    model_role: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    response_id: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class LLMResponse[T: StrictModel]:
    """typed 模型输出及其执行 metadata 的统一定义。

    :ivar output: 已通过严格 schema 校验的语义层输出对象。
    :ivar metadata: 不包含完整 prompt 与原始输出的执行 metadata。
    """

    output: T
    metadata: LLMResponseMetadata


class LLMGateway(Protocol):
    """语义层可见的唯一模型调用协议。

    协议保持窄接口，只允许版本化 prompt 和严格 schema 调用，避免语义层
    绕过角色配置、审计、预算和 Strict JSON Gateway。
    """

    async def complete_json(
        self,
        *,
        role: ModelRole,
        prompt: RenderedPrompt,
        output_schema: type[T],
        context: ExecutionContext,
    ) -> LLMResponse[T]:
        """执行一次严格 JSON 模型调用。

        :param role: 稳定模型角色。
        :param prompt: 已渲染版本化 prompt。
        :param output_schema: 严格输出 schema。
        :param context: 当前执行上下文。
        :return: typed 输出与执行 metadata。
        """


class ChatCompletionClient(Protocol):
    """可替换的 OpenAI-compatible 传输端口。

    该端口隔离 LiteLLM 响应形态与网关治理逻辑；生产实现、测试替身或本地
    proxy 适配器都必须返回完成协议校验前的原始归一对象。
    """

    async def complete(
        self, request: ChatCompletionRequest
    ) -> NormalizedChatCompletion:
        """执行一次物理模型请求。

        :param request: 传输层请求对象。
        :return: 尚未进行 JSON schema 解释的归一完成结果。
        """


class LLMCallStatus(StrEnum):
    """模型调用审计状态。

    状态只描述工程执行结果，不表示语义质量或临床结论。
    """

    #: 逻辑调用成功完成并通过严格 schema 校验。
    COMPLETED = "completed"

    #: 某次物理尝试失败，后续可能重试。
    ATTEMPT_FAILED = "attempt_failed"

    #: 逻辑调用最终失败或被取消。
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LLMCallEvent:
    """模型调用审计事件。

    :ivar execution_id: 执行链标识。
    :ivar correlation_id: 可选跨服务关联标识。
    :ivar step: 执行步骤名。
    :ivar status: 调用状态。
    :ivar request_id: 逻辑请求标识。
    :ivar attempt_id: 当前物理尝试标识。
    :ivar model_role: 模型角色名。
    :ivar model: 实际或配置模型名。
    :ivar prompt_id: prompt 标识。
    :ivar prompt_version: prompt 版本。
    :ivar prompt_sha256: prompt 摘要。
    :ivar response_id: 提供方响应标识。
    :ivar latency_ms: 耗时。
    :ivar prompt_tokens: 输入 token 数。
    :ivar completion_tokens: 输出 token 数。
    :ivar total_tokens: token 总数。
    :ivar finish_reason: 完成原因。
    :ivar attempt_count: 物理尝试次数。
    :ivar error_code: 失败错误码；成功时为空。
    :ivar input_digest: prompt 摘要。
    :ivar output_digest: typed 输出摘要；仅成功时填写。
    """

    execution_id: str
    correlation_id: str | None
    step: str
    status: LLMCallStatus
    request_id: str
    attempt_id: str
    model_role: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    response_id: str | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str
    attempt_count: int
    error_code: str
    input_digest: str
    output_digest: str


class LLMEventSink(Protocol):
    """模型执行事件输出端口。

    生产实现可将事件写入专用审计存储；实现不得把事件内容输出到普通日志，
    也不得将事件转换成业务成功或空结果。
    """

    async def emit(self, event: LLMCallEvent) -> None:
        """写入一个模型调用审计事件。

        :param event: 已脱敏的模型调用事件。
        :return: 无返回值；写入失败时应显式抛错。
        """


class NullLLMEventSink:
    """显式空审计事件接收器。

    该实现仅用于单元测试或尚未接入审计存储的受控阶段，不作为生产默认
    审计策略。
    """

    async def emit(self, event: LLMCallEvent) -> None:
        """接受并丢弃测试事件。

        :param event: 测试产生的模型调用事件。
        :return: 无返回值。
        """


__all__ = [
    "ChatCompletionClient",
    "ChatCompletionRequest",
    "LLMCallEvent",
    "LLMCallStatus",
    "LLMEventSink",
    "LLMGateway",
    "LLMResponse",
    "LLMResponseMetadata",
    "LLMSettings",
    "LLMUsage",
    "ModelRole",
    "ModelRoleConfig",
    "NormalizedChatCompletion",
    "NullLLMEventSink",
    "StrictResponseFormat",
]
