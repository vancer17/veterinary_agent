##################################################################################################
# 文件: src/veterinary_agent/config/runtime_config.py
# 作用: 定义配置与参数组件 RuntimeConfig，聚合各组件 Settings，生成不可变配置快照并提供应用内只读访问入口。
# 边界: 仅负责配置加载、校验、快照、版本与 trace-safe 摘要；不初始化数据库、不创建业务组件、不执行 Agent 编排。
##################################################################################################

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from veterinary_agent.config.api_ingress import (
    ApiIngressSettings,
    load_api_ingress_settings,
)
from veterinary_agent.config.checkpoint_store import (
    CheckpointStoreSettings,
    load_checkpoint_store_settings,
)
from veterinary_agent.config.conversation_store import (
    ConversationStoreSettings,
    load_conversation_store_settings,
)
from veterinary_agent.config.observability import (
    ObservabilitySettings,
    load_observability_settings,
)
from veterinary_agent.config.vet_safety_trigger import (
    SafetyTriggerAgentSettings,
    load_safety_trigger_agent_settings,
)
from veterinary_agent.config.vet_education import (
    EducationAgentSettings,
    load_education_agent_settings,
)
from veterinary_agent.config.vet_nonmedical_pet_care import (
    NonmedicalPetCareAgentSettings,
    load_nonmedical_pet_care_agent_settings,
)
from veterinary_agent.config.vet_context_builder import (
    VetContextBuilderSettings,
    load_vet_context_builder_settings,
)
from veterinary_agent.config.vet_standard_consultation import (
    StandardConsultationAgentSettings,
    load_standard_consultation_agent_settings,
)
from veterinary_agent.config.vet_task_decomposer import (
    VetTaskDecomposerSettings,
    load_vet_task_decomposer_settings,
)
from veterinary_agent.config.llm_gateway import (
    LlmGatewaySettings,
    load_llm_gateway_settings,
)

DEFAULT_RUNTIME_CONFIG_PATH = Path("configs/runtime_config.yaml")
RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION = "runtime-config.trace-safe.v1"

JsonMap: TypeAlias = dict[str, object]

_SENSITIVE_SUMMARY_KEY_PARTS: frozenset[str] = frozenset(
    {
        "api_key",
        "authorization",
        "connection",
        "credential",
        "database_url",
        "dsn",
        "password",
        "secret",
        "token",
    }
)
_SENSITIVE_VALUE_PREFIXES: tuple[str, ...] = (
    "postgres://",
    "postgresql://",
    "postgresql+psycopg://",
    "mysql://",
    "redis://",
)


class RuntimeConfigErrorCode(StrEnum):
    """RuntimeConfig 稳定错误码。"""

    CONFIG_SOURCE_UNAVAILABLE = "CONFIG_SOURCE_UNAVAILABLE"
    CONFIG_SCHEMA_INVALID = "CONFIG_SCHEMA_INVALID"
    CONFIG_TYPE_INVALID = "CONFIG_TYPE_INVALID"
    CONFIG_RANGE_INVALID = "CONFIG_RANGE_INVALID"
    CONFIG_RELATION_INVALID = "CONFIG_RELATION_INVALID"
    CONFIG_SAFETY_LOCK_VIOLATION = "CONFIG_SAFETY_LOCK_VIOLATION"
    CONFIG_SNAPSHOT_NOT_FOUND = "CONFIG_SNAPSHOT_NOT_FOUND"
    CONFIG_TRACE_SUMMARY_UNSAFE = "CONFIG_TRACE_SUMMARY_UNSAFE"


class RuntimeConfigOperation(StrEnum):
    """RuntimeConfig 对外操作名。"""

    LOAD_RUNTIME_CONFIG = "LoadRuntimeConfig"
    BUILD_CONFIG_SNAPSHOT = "BuildConfigSnapshot"
    GET_CURRENT_CONFIG_SNAPSHOT = "GetCurrentConfigSnapshot"
    GET_CONFIG_VALUE = "GetConfigValue"
    GET_CONFIG_NAMESPACE = "GetConfigNamespace"
    GET_TRACE_SAFE_CONFIG_SUMMARY = "GetTraceSafeConfigSummary"
    VALIDATE_CANDIDATE_CONFIG = "ValidateCandidateConfig"


class RuntimeConfigNamespace(StrEnum):
    """RuntimeConfig 快照内的配置命名空间。"""

    RUNTIME_CONFIG = "runtime_config"
    API_INGRESS = "api_ingress"
    CHECKPOINT_STORE = "checkpoint_store"
    CONVERSATION_STORE = "conversation_store"
    LLM_GATEWAY = "llm_gateway"
    OBSERVABILITY = "observability"
    VET_TASK_DECOMPOSER = "vet_task_decomposer"
    VET_CONTEXT_BUILDER = "vet_context_builder"
    STANDARD_CONSULTATION = "standard_consultation"
    SAFETY_TRIGGER = "safety_trigger"
    EDUCATION_AGENT = "education_agent"
    NONMEDICAL_PET_CARE = "nonmedical_pet_care"


class _RuntimeConfigModel(BaseModel):
    """RuntimeConfig Pydantic 模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串配置值。

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class RuntimeConfigErrorDto(_RuntimeConfigModel):
    """RuntimeConfig 统一错误 DTO。"""

    code: RuntimeConfigErrorCode = Field(
        description="RuntimeConfig 稳定错误码。",
    )
    operation: RuntimeConfigOperation = Field(
        description="发生错误的 RuntimeConfig 操作名。",
    )
    message: str = Field(
        min_length=1,
        description="面向工程排障的简短错误说明。",
    )
    retryable: bool = Field(
        description="调用方是否可以稍后重试。",
    )
    conflict_with: JsonMap | None = Field(
        default=None,
        description="配置冲突对象摘要。",
    )


def build_runtime_config_error_dto(
    *,
    code: RuntimeConfigErrorCode,
    operation: RuntimeConfigOperation,
    message: str,
    retryable: bool,
    conflict_with: JsonMap | None = None,
) -> RuntimeConfigErrorDto:
    """构建 RuntimeConfig 统一错误 DTO。

    :param code: RuntimeConfig 稳定错误码。
    :param operation: 发生错误的 RuntimeConfig 操作名。
    :param message: 面向工程排障的简短错误说明。
    :param retryable: 调用方是否可以稍后重试。
    :param conflict_with: 配置冲突对象摘要。
    :return: RuntimeConfig 统一错误 DTO。
    """

    return RuntimeConfigErrorDto(
        code=code,
        operation=operation,
        message=message,
        retryable=retryable,
        conflict_with=conflict_with,
    )


class RuntimeConfigError(Exception):
    """RuntimeConfig 领域异常。"""

    def __init__(
        self,
        *,
        code: RuntimeConfigErrorCode,
        operation: RuntimeConfigOperation,
        message: str,
        retryable: bool,
        conflict_with: JsonMap | None = None,
    ) -> None:
        """初始化 RuntimeConfig 领域异常。

        :param code: RuntimeConfig 稳定错误码。
        :param operation: 发生错误的 RuntimeConfig 操作名。
        :param message: 面向工程排障的简短错误说明。
        :param retryable: 调用方是否可以稍后重试。
        :param conflict_with: 配置冲突对象摘要。
        :return: None。
        """

        self.error = build_runtime_config_error_dto(
            code=code,
            operation=operation,
            message=message,
            retryable=retryable,
            conflict_with=conflict_with,
        )
        super().__init__(self.error.message)

    @property
    def code(self) -> RuntimeConfigErrorCode:
        """读取 RuntimeConfig 稳定错误码。

        :return: 当前异常对应的 RuntimeConfig 错误码。
        """

        return self.error.code

    @property
    def operation(self) -> RuntimeConfigOperation:
        """读取发生错误的 RuntimeConfig 操作名。

        :return: 当前异常对应的 RuntimeConfig 操作名。
        """

        return self.error.operation

    @property
    def retryable(self) -> bool:
        """读取当前错误是否可重试。

        :return: 若调用方可以稍后重试，则返回 True。
        """

        return self.error.retryable

    def to_dto(self) -> RuntimeConfigErrorDto:
        """转换为 RuntimeConfig 统一错误 DTO。

        :return: 当前异常携带的错误 DTO。
        """

        return self.error

    def __str__(self) -> str:
        """转换为便于日志记录的简短字符串。

        :return: 包含操作名、错误码与错误说明的字符串。
        """

        return f"{self.error.operation}:{self.error.code}:{self.error.message}"


class RuntimeConfigSafetyLockSettings(_RuntimeConfigModel):
    """RuntimeConfig 安全锁定项。"""

    enforce_pet_session_policy: bool = Field(
        default=True,
        description="是否强制保留一 session 一宠策略；MVP 阶段不得关闭。",
    )
    require_output_safety_review: bool = Field(
        default=True,
        description="是否要求用户可见输出经过输出安全审查；MVP 阶段不得关闭。",
    )
    fail_closed_guardrails: bool = Field(
        default=True,
        description="护栏组件不可用时是否按 fail-closed 策略处理；MVP 阶段不得关闭。",
    )
    prevent_direct_model_publish: bool = Field(
        default=True,
        description="是否禁止模型原始输出绕过合成与安全审查直接发布；MVP 阶段不得关闭。",
    )
    forbid_sensitive_observability_labels: bool = Field(
        default=True,
        description="是否禁止将高敏字段写入可观测性指标标签；MVP 阶段不得关闭。",
    )


class RuntimeConfigSettings(BaseSettings):
    """RuntimeConfig 组件自身配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="RUNTIME_CONFIG_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_RUNTIME_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    params_version: str = Field(
        default="params.v1",
        min_length=1,
        max_length=128,
        description="业务运行参数版本，写入 checkpoint 与逻辑链留痕。",
    )
    config_schema_version: str = Field(
        default="runtime-config.v1",
        min_length=1,
        max_length=128,
        description="RuntimeConfig 配置结构版本。",
    )
    safety_locks: RuntimeConfigSafetyLockSettings = Field(
        default_factory=RuntimeConfigSafetyLockSettings,
        description="RuntimeConfig 安全锁定项集合。",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_root_string_value(cls, value: Any) -> Any:
        """清理根配置中的字符串值。

        :param value: 原始字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定制 Pydantic Settings 的配置来源顺序。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置来源。
        :param env_settings: 环境变量配置来源。
        :param dotenv_settings: .env 文件配置来源。
        :param file_secret_settings: 文件密钥配置来源。
        :return: 按优先级排列后的配置来源元组。
        """

        if cls.yaml_config_path is None:
            yaml_source = YamlConfigSettingsSource(settings_cls)
        else:
            yaml_source = YamlConfigSettingsSource(
                settings_cls,
                yaml_file=cls.yaml_config_path,
            )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


class RuntimeConfigSnapshot(_RuntimeConfigModel):
    """RuntimeConfig 已校验不可变快照。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    config_snapshot_id: str = Field(
        min_length=1,
        description="当前有效配置快照 ID。",
    )
    params_version: str = Field(
        min_length=1,
        description="业务运行参数版本。",
    )
    config_schema_version: str = Field(
        min_length=1,
        description="RuntimeConfig 配置结构版本。",
    )
    trace_safe_schema_version: str = Field(
        min_length=1,
        description="trace-safe 摘要结构版本。",
    )
    created_at: datetime = Field(
        description="配置快照创建时间。",
    )
    runtime_config: RuntimeConfigSettings = Field(
        description="RuntimeConfig 组件自身配置。",
    )
    api_ingress: ApiIngressSettings = Field(
        description="API 接入组件配置。",
    )
    checkpoint_store: CheckpointStoreSettings = Field(
        description="CheckpointStore RuntimeConfig。",
    )
    conversation_store: ConversationStoreSettings = Field(
        description="ConversationStore RuntimeConfig。",
    )
    llm_gateway: LlmGatewaySettings = Field(
        description="LlmGateway RuntimeConfig。",
    )
    observability: ObservabilitySettings = Field(
        description="Observability RuntimeConfig。",
    )
    vet_task_decomposer: VetTaskDecomposerSettings = Field(
        description="VetTaskDecomposer RuntimeConfig。",
    )
    vet_context_builder: VetContextBuilderSettings = Field(
        description="VetContextBuilder RuntimeConfig。",
    )
    standard_consultation: StandardConsultationAgentSettings = Field(
        description="StandardConsultationAgent RuntimeConfig。",
    )
    safety_trigger: SafetyTriggerAgentSettings = Field(
        description="SafetyTriggerAgent RuntimeConfig。",
    )
    education_agent: EducationAgentSettings = Field(
        description="EducationAgent RuntimeConfig。",
    )
    nonmedical_pet_care: NonmedicalPetCareAgentSettings = Field(
        description="NonmedicalPetCareAgent RuntimeConfig。",
    )
    trace_safe_summary: JsonMap = Field(
        description="可写入逻辑链的脱敏配置摘要。",
    )


def _build_runtime_config_trace_summary(settings: RuntimeConfigSettings) -> JsonMap:
    """构建 RuntimeConfig 自身 trace-safe 摘要。

    :param settings: RuntimeConfig 组件自身配置。
    :return: RuntimeConfig 自身 trace-safe 摘要。
    """

    return {
        "params_version": settings.params_version,
        "config_schema_version": settings.config_schema_version,
        "safety_locks": settings.safety_locks.model_dump(mode="json"),
    }


def _build_api_ingress_trace_summary(settings: ApiIngressSettings) -> JsonMap:
    """构建 ApiIngress trace-safe 摘要。

    :param settings: API 接入组件配置。
    :return: API 接入组件 trace-safe 摘要。
    """

    return {
        "enabled": settings.enabled,
        "service_name": settings.service_name,
        "environment": settings.environment,
        "config_version": settings.config_version,
        "request_limits": settings.request_limits.model_dump(mode="json"),
        "attachment_limits": settings.attachment_limits.model_dump(mode="json"),
        "response_mode": settings.response_mode.model_dump(mode="json"),
        "sse": settings.sse.model_dump(mode="json"),
        "rate_limit": settings.rate_limit.model_dump(mode="json"),
        "readiness": settings.readiness.model_dump(mode="json"),
        "openai_compatibility": settings.openai_compatibility.model_dump(mode="json"),
    }


def _build_checkpoint_store_trace_summary(
    settings: CheckpointStoreSettings,
) -> JsonMap:
    """构建 CheckpointStore trace-safe 摘要。

    :param settings: CheckpointStore RuntimeConfig。
    :return: CheckpointStore trace-safe 摘要。
    """

    return {
        "operation_timeout_seconds": settings.operation_timeout_seconds,
        "state_schema": settings.state_schema.model_dump(mode="json"),
        "run_lock": settings.run_lock.model_dump(mode="json"),
        "history": settings.history.model_dump(mode="json"),
        "checkpoint": settings.checkpoint.model_dump(mode="json"),
        "segment_publish": settings.segment_publish.model_dump(mode="json"),
    }


def _build_conversation_store_trace_summary(
    settings: ConversationStoreSettings,
) -> JsonMap:
    """构建 ConversationStore trace-safe 摘要。

    :param settings: ConversationStore RuntimeConfig。
    :return: ConversationStore trace-safe 摘要。
    """

    return {
        "enabled": settings.enabled,
        "operation_timeout_seconds": settings.operation_timeout_seconds,
        "message": settings.message.model_dump(mode="json"),
        "history": settings.history.model_dump(mode="json"),
    }


def _build_observability_trace_summary(settings: ObservabilitySettings) -> JsonMap:
    """构建 Observability trace-safe 摘要。

    :param settings: Observability RuntimeConfig。
    :return: Observability trace-safe 摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "metrics": settings.metrics.model_dump(mode="json"),
        "logging": settings.logging.model_dump(mode="json"),
        "tracing": {
            "enabled": settings.tracing.enabled,
            "sample_rate": settings.tracing.sample_rate,
            "service_name": settings.tracing.service_name,
            "environment": settings.tracing.environment,
            "exporter_timeout_seconds": settings.tracing.exporter_timeout_seconds,
        },
        "label_policy": settings.label_policy.model_dump(mode="json"),
    }


def _build_vet_context_builder_trace_summary(
    settings: VetContextBuilderSettings,
) -> JsonMap:
    """构建 VetContextBuilder trace-safe 配置摘要。

    :param settings: VetContextBuilder RuntimeConfig。
    :return: 不含业务正文且避免敏感字段名的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "budgets": {
            "single_full_units": settings.budgets.single_full_tokens,
            "safety_minimal_units": settings.budgets.safety_minimal_tokens,
            "education_light_units": settings.budgets.education_light_tokens,
        },
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "p0_fields": settings.p0_fields,
        "recent_message_limit": settings.recent_message_limit,
        "recent_message_unit_budget": settings.recent_message_token_budget,
        "max_prompt_blocks": settings.max_prompt_blocks,
        "max_task_input_chars": settings.max_task_input_chars,
        "chars_per_unit": settings.chars_per_token,
        "budget_headroom_ratio": settings.budget_headroom_ratio,
    }


def _build_vet_task_decomposer_trace_summary(
    settings: VetTaskDecomposerSettings,
) -> JsonMap:
    """构建 VetTaskDecomposer trace-safe 配置摘要。

    :param settings: VetTaskDecomposer RuntimeConfig。
    :return: 不含用户原文、prompt 或敏感凭据的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "decomposer_version": settings.decomposer_version,
        "llm_enabled": settings.llm_enabled,
        "review_repair_enabled": settings.review_repair_enabled,
        "local_fallback_enabled": settings.local_fallback_enabled,
        "decompose_agent_id": settings.decompose_agent_id,
        "decompose_agent_version": settings.decompose_agent_version,
        "review_agent_id": settings.review_agent_id,
        "review_agent_version": settings.review_agent_version,
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "confidence": settings.confidence.model_dump(mode="json"),
        "max_tasks_per_turn": settings.max_tasks_per_turn,
        "max_user_message_chars": settings.max_user_message_chars,
    }


def _build_standard_consultation_trace_summary(
    settings: StandardConsultationAgentSettings,
) -> JsonMap:
    """构建 StandardConsultationAgent trace-safe 配置摘要。

    :param settings: StandardConsultationAgent RuntimeConfig。
    :return: 不含 prompt、业务正文或敏感凭据的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "standard_agent_version": settings.standard_agent_version,
        "orchestrator_version": settings.orchestrator_version,
        "sub_agents": {
            "question_collector": (
                f"{settings.question_collector_agent_id}:"
                f"{settings.question_collector_agent_version}"
            ),
            "triage": f"{settings.triage_agent_id}:{settings.triage_agent_version}",
            "direction": (
                f"{settings.direction_agent_id}:{settings.direction_agent_version}"
            ),
            "differential": (
                f"{settings.differential_agent_id}:"
                f"{settings.differential_agent_version}"
            ),
            "care": f"{settings.care_agent_id}:{settings.care_agent_version}",
            "synthesizer": (
                f"{settings.synthesizer_agent_id}:{settings.synthesizer_agent_version}"
            ),
        },
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "question_budget": settings.question_budget.model_dump(mode="json"),
        "readiness": settings.readiness.model_dump(mode="json"),
        "rag": settings.rag.model_dump(mode="json"),
    }


def _build_safety_trigger_trace_summary(
    settings: SafetyTriggerAgentSettings,
) -> JsonMap:
    """构建 SafetyTriggerAgent trace-safe 配置摘要。

    :param settings: SafetyTriggerAgent RuntimeConfig。
    :return: 不含 prompt、业务正文或敏感凭据的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "safety_trigger_agent_version": settings.safety_trigger_agent_version,
        "writer_version": settings.writer_version,
        "confirmation_planner_version": settings.confirmation_planner_version,
        "fallback_template_version": settings.fallback_template_version,
        "requirement_set_version": settings.requirement_set_version,
        "writer_agent": f"{settings.writer_agent_id}:{settings.writer_agent_version}",
        "confirmation_planner_agent": (
            f"{settings.confirmation_planner_agent_id}:"
            f"{settings.confirmation_planner_agent_version}"
        ),
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "requirements": settings.requirements.model_dump(mode="json"),
        "rag_policy": {"allowed": False},
    }


def _build_education_agent_trace_summary(settings: EducationAgentSettings) -> JsonMap:
    """构建 EducationAgent trace-safe 配置摘要。

    :param settings: EducationAgent RuntimeConfig。
    :return: 不含 prompt、业务正文或敏感凭据的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "education_agent_version": settings.education_agent_version,
        "planner_version": settings.planner_version,
        "retrieval_planner_version": settings.retrieval_planner_version,
        "writer_version": settings.writer_version,
        "grounding_checker_version": settings.grounding_checker_version,
        "fallback_template_version": settings.fallback_template_version,
        "sub_agents": {
            "planner": f"{settings.planner_agent_id}:{settings.planner_agent_version}",
            "retrieval_planner": (
                f"{settings.retrieval_planner_agent_id}:"
                f"{settings.retrieval_planner_agent_version}"
            ),
            "writer": f"{settings.writer_agent_id}:{settings.writer_agent_version}",
            "grounding_checker": (
                f"{settings.grounding_checker_agent_id}:"
                f"{settings.grounding_checker_agent_version}"
            ),
        },
        "allowed_dimensions": settings.allowed_dimensions,
        "max_draft_chars": settings.max_draft_chars,
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "rag": settings.rag.model_dump(mode="json"),
    }


def _build_nonmedical_pet_care_trace_summary(
    settings: NonmedicalPetCareAgentSettings,
) -> JsonMap:
    """构建 NonmedicalPetCareAgent trace-safe 配置摘要。

    :param settings: NonmedicalPetCareAgent RuntimeConfig。
    :return: 不含 prompt、业务正文或敏感凭据的配置摘要。
    """

    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "nonmedical_agent_version": settings.nonmedical_agent_version,
        "planner_version": settings.planner_version,
        "retrieval_planner_version": settings.retrieval_planner_version,
        "writer_version": settings.writer_version,
        "self_checker_version": settings.self_checker_version,
        "fallback_template_version": settings.fallback_template_version,
        "sub_agents": {
            "planner": f"{settings.planner_agent_id}:{settings.planner_agent_version}",
            "retrieval_planner": (
                f"{settings.retrieval_planner_agent_id}:"
                f"{settings.retrieval_planner_agent_version}"
            ),
            "writer": f"{settings.writer_agent_id}:{settings.writer_agent_version}",
            "self_checker": (
                f"{settings.self_checker_agent_id}:"
                f"{settings.self_checker_agent_version}"
            ),
        },
        "allowed_dimensions": settings.allowed_dimensions,
        "max_draft_chars": settings.max_draft_chars,
        "timeouts": settings.timeouts.model_dump(mode="json"),
        "rag": settings.rag.model_dump(mode="json"),
        "rules": settings.rules.model_dump(mode="json"),
    }


def _build_llm_gateway_trace_summary(settings: LlmGatewaySettings) -> JsonMap:
    """构建 LlmGateway trace-safe 配置摘要。

    :param settings: LlmGateway RuntimeConfig。
    :return: 不包含代理地址、令牌环境变量名或密钥的配置摘要。
    """

    routes = [
        {
            "provider_route_id": route.provider_route_id,
            "adapter_type": route.adapter_type,
            "provider_name": route.provider_name,
            "model_alias": route.model_alias,
            "include_stream_usage": route.include_stream_usage,
            "max_concurrency": route.max_concurrency,
            "capability": {
                "max_context_size": route.capability.max_context_tokens,
                "supports_streaming": route.capability.supports_streaming,
                "supports_structured_output": (
                    route.capability.supports_structured_output
                ),
                "supports_tools": route.capability.supports_tools,
                "supports_vision": route.capability.supports_vision,
            },
        }
        for route in settings.provider_routes
    ]
    profiles = [
        {
            "model_profile_id": profile.model_profile_id,
            "profile_version": profile.profile_version,
            "provider_route_id": profile.provider_route_id,
            "required_capability": profile.required_capability.model_dump(mode="json"),
            "timeout_policy": {
                "connect_timeout_seconds": (
                    profile.timeout_policy.connect_timeout_seconds
                ),
                "first_event_timeout_seconds": (
                    profile.timeout_policy.first_token_timeout_seconds
                ),
                "read_timeout_seconds": profile.timeout_policy.read_timeout_seconds,
                "total_timeout_seconds": (profile.timeout_policy.total_timeout_seconds),
            },
            "retry_policy": profile.retry_policy.model_dump(mode="json"),
            "fallback_profile_ids": profile.fallback_profile_ids,
            "fallback_on_error_codes": profile.fallback_on_error_codes,
            "reserved_output_size": profile.reserved_output_tokens,
            "max_concurrency": profile.max_concurrency,
            "trace_policy": profile.trace_policy.model_dump(mode="json"),
        }
        for profile in settings.model_profiles
    ]
    return {
        "enabled": settings.enabled,
        "config_version": settings.config_version,
        "max_total_attempts": settings.max_total_attempts,
        "max_call_duration_seconds": settings.max_call_duration_seconds,
        "global_max_concurrency": settings.global_max_concurrency,
        "concurrency_acquire_timeout_seconds": (
            settings.concurrency_acquire_timeout_seconds
        ),
        "budget_estimation": {
            "chars_per_unit": settings.token_estimation.chars_per_token,
            "message_overhead": (settings.token_estimation.message_overhead_tokens),
            "tool_overhead": settings.token_estimation.tool_overhead_tokens,
            "response_format_overhead": (
                settings.token_estimation.response_format_overhead_tokens
            ),
        },
        "provider_routes": routes,
        "model_profiles": profiles,
    }


def _build_trace_safe_summary(
    *,
    runtime_config_settings: RuntimeConfigSettings,
    api_ingress_settings: ApiIngressSettings,
    checkpoint_store_settings: CheckpointStoreSettings,
    conversation_store_settings: ConversationStoreSettings,
    llm_gateway_settings: LlmGatewaySettings,
    observability_settings: ObservabilitySettings,
    vet_task_decomposer_settings: VetTaskDecomposerSettings,
    vet_context_builder_settings: VetContextBuilderSettings,
    standard_consultation_settings: StandardConsultationAgentSettings,
    safety_trigger_settings: SafetyTriggerAgentSettings,
    education_agent_settings: EducationAgentSettings,
    nonmedical_pet_care_settings: NonmedicalPetCareAgentSettings,
) -> JsonMap:
    """构建完整 trace-safe 配置摘要。

    :param runtime_config_settings: RuntimeConfig 组件自身配置。
    :param api_ingress_settings: API 接入组件配置。
    :param checkpoint_store_settings: CheckpointStore RuntimeConfig。
    :param conversation_store_settings: ConversationStore RuntimeConfig。
    :param llm_gateway_settings: LlmGateway RuntimeConfig。
    :param observability_settings: Observability RuntimeConfig。
    :param vet_task_decomposer_settings: VetTaskDecomposer RuntimeConfig。
    :param vet_context_builder_settings: VetContextBuilder RuntimeConfig。
    :param standard_consultation_settings: StandardConsultationAgent RuntimeConfig。
    :param safety_trigger_settings: SafetyTriggerAgent RuntimeConfig。
    :param education_agent_settings: EducationAgent RuntimeConfig。
    :param nonmedical_pet_care_settings: NonmedicalPetCareAgent RuntimeConfig。
    :return: 可写入逻辑链的脱敏配置摘要。
    """

    return {
        "trace_safe_schema_version": RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION,
        "runtime_config": _build_runtime_config_trace_summary(runtime_config_settings),
        "api_ingress": _build_api_ingress_trace_summary(api_ingress_settings),
        "checkpoint_store": _build_checkpoint_store_trace_summary(
            checkpoint_store_settings
        ),
        "conversation_store": _build_conversation_store_trace_summary(
            conversation_store_settings
        ),
        "llm_gateway": _build_llm_gateway_trace_summary(llm_gateway_settings),
        "observability": _build_observability_trace_summary(observability_settings),
        "vet_task_decomposer": _build_vet_task_decomposer_trace_summary(
            vet_task_decomposer_settings
        ),
        "vet_context_builder": _build_vet_context_builder_trace_summary(
            vet_context_builder_settings
        ),
        "standard_consultation": _build_standard_consultation_trace_summary(
            standard_consultation_settings
        ),
        "safety_trigger": _build_safety_trigger_trace_summary(safety_trigger_settings),
        "education_agent": _build_education_agent_trace_summary(
            education_agent_settings
        ),
        "nonmedical_pet_care": _build_nonmedical_pet_care_trace_summary(
            nonmedical_pet_care_settings
        ),
    }


def _is_sensitive_summary_key(key: str) -> bool:
    """判断 trace-safe 摘要字段名是否疑似敏感。

    :param key: trace-safe 摘要字段名。
    :return: 若字段名包含敏感片段则返回 True。
    """

    normalized_key = key.lower()
    return any(part in normalized_key for part in _SENSITIVE_SUMMARY_KEY_PARTS)


def _is_sensitive_summary_value(value: str) -> bool:
    """判断 trace-safe 摘要字符串值是否疑似敏感。

    :param value: trace-safe 摘要字符串值。
    :return: 若字符串值疑似为连接串或密钥类值则返回 True。
    """

    normalized_value = value.strip().lower()
    return normalized_value.startswith(_SENSITIVE_VALUE_PREFIXES)


def _validate_trace_safe_summary_value(
    *,
    path: str,
    value: object,
) -> None:
    """递归校验 trace-safe 摘要值。

    :param path: 当前摘要字段路径。
    :param value: 当前摘要字段值。
    :return: None。
    :raises RuntimeConfigError: 当摘要包含敏感字段名或疑似敏感字符串值时抛出。
    """

    if isinstance(value, dict):
        for key, child_value in value.items():
            if not isinstance(key, str):
                raise RuntimeConfigError(
                    code=RuntimeConfigErrorCode.CONFIG_TRACE_SUMMARY_UNSAFE,
                    operation=RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY,
                    message="trace-safe 摘要包含非字符串字段名",
                    retryable=False,
                    conflict_with={"path": path},
                )
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_summary_key(key):
                raise RuntimeConfigError(
                    code=RuntimeConfigErrorCode.CONFIG_TRACE_SUMMARY_UNSAFE,
                    operation=RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY,
                    message="trace-safe 摘要包含敏感字段名",
                    retryable=False,
                    conflict_with={"path": child_path},
                )
            _validate_trace_safe_summary_value(path=child_path, value=child_value)
        return

    if isinstance(value, list):
        for index, child_value in enumerate(value):
            _validate_trace_safe_summary_value(
                path=f"{path}[{index}]",
                value=child_value,
            )
        return

    if isinstance(value, str) and _is_sensitive_summary_value(value):
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_TRACE_SUMMARY_UNSAFE,
            operation=RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY,
            message="trace-safe 摘要包含疑似敏感字符串值",
            retryable=False,
            conflict_with={"path": path},
        )


def _validate_trace_safe_summary(summary: JsonMap) -> None:
    """校验 trace-safe 摘要是否可写入逻辑链。

    :param summary: 待校验的 trace-safe 摘要。
    :return: None。
    :raises RuntimeConfigError: 当摘要包含敏感内容时抛出。
    """

    _validate_trace_safe_summary_value(path="", value=summary)


def _json_dumps_for_snapshot(value: object) -> str:
    """将配置摘要序列化为稳定 JSON 字符串。

    :param value: 需要序列化的配置摘要。
    :return: 可用于 hash 的稳定 JSON 字符串。
    """

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _build_config_snapshot_id(summary: JsonMap) -> str:
    """根据 trace-safe 摘要构建配置快照 ID。

    :param summary: 已完成脱敏的配置摘要。
    :return: 当前配置快照 ID。
    """

    digest = sha256(_json_dumps_for_snapshot(summary).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _split_config_value_key(key: str) -> tuple[RuntimeConfigNamespace, list[str]]:
    """拆分 RuntimeConfig 点路径配置键。

    :param key: 点路径格式配置键，首段必须为 RuntimeConfig 命名空间。
    :return: 配置命名空间与命名空间内字段路径。
    :raises RuntimeConfigError: 当配置键为空、格式非法或命名空间未知时抛出。
    """

    key_parts = [part.strip() for part in key.split(".") if part.strip()]
    if len(key_parts) < 2:
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_SCHEMA_INVALID,
            operation=RuntimeConfigOperation.GET_CONFIG_VALUE,
            message="RuntimeConfig 配置键格式非法",
            retryable=False,
            conflict_with={"key": key},
        )
    namespace_value = key_parts[0]
    try:
        namespace = RuntimeConfigNamespace(namespace_value)
    except ValueError as exc:
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_SCHEMA_INVALID,
            operation=RuntimeConfigOperation.GET_CONFIG_VALUE,
            message="RuntimeConfig 配置键命名空间非法",
            retryable=False,
            conflict_with={"key": key, "namespace": namespace_value},
        ) from exc
    return namespace, key_parts[1:]


def _dump_namespace_for_lookup(
    settings: (
        RuntimeConfigSettings
        | ApiIngressSettings
        | CheckpointStoreSettings
        | ConversationStoreSettings
        | LlmGatewaySettings
        | ObservabilitySettings
        | VetTaskDecomposerSettings
        | VetContextBuilderSettings
        | StandardConsultationAgentSettings
        | SafetyTriggerAgentSettings
        | EducationAgentSettings
        | NonmedicalPetCareAgentSettings
    ),
) -> JsonMap:
    """将配置命名空间转换为可按点路径读取的映射。

    :param settings: RuntimeConfig 快照内的某个配置命名空间对象。
    :return: 可供配置值读取使用的 Python 映射。
    """

    return settings.model_dump(mode="python")


def _read_mapping_path(
    *,
    source: JsonMap,
    path: list[str],
    original_key: str,
) -> object:
    """从嵌套映射中读取指定路径的配置值。

    :param source: 配置命名空间映射。
    :param path: 命名空间内字段路径。
    :param original_key: 调用方传入的原始配置键。
    :return: 配置键对应的值。
    :raises RuntimeConfigError: 当路径不存在或路径穿过非映射值时抛出。
    """

    current_value: object = source
    traversed_path: list[str] = []
    for path_part in path:
        traversed_path.append(path_part)
        if not isinstance(current_value, dict) or path_part not in current_value:
            raise RuntimeConfigError(
                code=RuntimeConfigErrorCode.CONFIG_SCHEMA_INVALID,
                operation=RuntimeConfigOperation.GET_CONFIG_VALUE,
                message="RuntimeConfig 配置键不存在",
                retryable=False,
                conflict_with={
                    "key": original_key,
                    "missing_path": ".".join(traversed_path),
                },
            )
        current_value = current_value[path_part]
    return current_value


def _disabled_safety_lock_fields(
    settings: RuntimeConfigSafetyLockSettings,
) -> list[str]:
    """列出被关闭的安全锁定项。

    :param settings: RuntimeConfig 安全锁定项配置。
    :return: 被关闭的安全锁定项字段名列表。
    """

    values = settings.model_dump(mode="python")
    return [field_name for field_name, enabled in values.items() if enabled is not True]


def _validate_runtime_config_safety_locks(settings: RuntimeConfigSettings) -> None:
    """校验 RuntimeConfig 安全锁定项。

    :param settings: RuntimeConfig 组件自身配置。
    :return: None。
    :raises RuntimeConfigError: 当任一安全锁定项被关闭时抛出。
    """

    disabled_fields = _disabled_safety_lock_fields(settings.safety_locks)
    if not disabled_fields:
        return
    raise RuntimeConfigError(
        code=RuntimeConfigErrorCode.CONFIG_SAFETY_LOCK_VIOLATION,
        operation=RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG,
        message="RuntimeConfig 安全锁定项不得关闭",
        retryable=False,
        conflict_with={"disabled_fields": disabled_fields},
    )


def _validate_runtime_config_relations(
    *,
    api_ingress_settings: ApiIngressSettings,
    checkpoint_store_settings: CheckpointStoreSettings,
    conversation_store_settings: ConversationStoreSettings,
) -> None:
    """校验跨组件配置关系。

    :param api_ingress_settings: API 接入组件配置。
    :param checkpoint_store_settings: CheckpointStore RuntimeConfig。
    :param conversation_store_settings: ConversationStore RuntimeConfig。
    :return: None。
    :raises RuntimeConfigError: 当跨组件配置关系非法时抛出。
    """

    if (
        checkpoint_store_settings.operation_timeout_seconds
        > checkpoint_store_settings.run_lock.max_ttl_seconds
    ):
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_RELATION_INVALID,
            operation=RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG,
            message="CheckpointStore 操作超时不得大于运行锁最大 TTL",
            retryable=False,
            conflict_with={
                "operation_timeout_seconds": (
                    checkpoint_store_settings.operation_timeout_seconds
                ),
                "run_lock.max_ttl_seconds": (
                    checkpoint_store_settings.run_lock.max_ttl_seconds
                ),
            },
        )
    if (
        conversation_store_settings.history.max_recent_messages
        > conversation_store_settings.history.max_list_limit
    ):
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_RELATION_INVALID,
            operation=RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG,
            message="ConversationStore 最近消息上限不得大于历史分页上限",
            retryable=False,
            conflict_with={
                "conversation_store.history.max_recent_messages": (
                    conversation_store_settings.history.max_recent_messages
                ),
                "conversation_store.history.max_list_limit": (
                    conversation_store_settings.history.max_list_limit
                ),
            },
        )
    if (
        api_ingress_settings.response_mode.allow_stream
        and api_ingress_settings.sse.max_stream_duration_seconds
        > api_ingress_settings.orchestrator.stream_total_timeout_seconds
    ):
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_RELATION_INVALID,
            operation=RuntimeConfigOperation.VALIDATE_CANDIDATE_CONFIG,
            message="SSE 最大持续时间不得大于编排层流式总超时",
            retryable=False,
            conflict_with={
                "sse.max_stream_duration_seconds": (
                    api_ingress_settings.sse.max_stream_duration_seconds
                ),
                "orchestrator.stream_total_timeout_seconds": (
                    api_ingress_settings.orchestrator.stream_total_timeout_seconds
                ),
            },
        )


def validate_runtime_config_candidate(
    *,
    runtime_config_settings: RuntimeConfigSettings,
    api_ingress_settings: ApiIngressSettings,
    checkpoint_store_settings: CheckpointStoreSettings,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    vet_task_decomposer_settings: VetTaskDecomposerSettings | None = None,
    vet_context_builder_settings: VetContextBuilderSettings | None = None,
    standard_consultation_settings: StandardConsultationAgentSettings | None = None,
    safety_trigger_settings: SafetyTriggerAgentSettings | None = None,
    education_agent_settings: EducationAgentSettings | None = None,
    nonmedical_pet_care_settings: NonmedicalPetCareAgentSettings | None = None,
) -> None:
    """校验候选 RuntimeConfig 聚合配置。

    :param runtime_config_settings: RuntimeConfig 组件自身配置。
    :param api_ingress_settings: API 接入组件配置。
    :param checkpoint_store_settings: CheckpointStore RuntimeConfig。
    :param conversation_store_settings: 可选 ConversationStore RuntimeConfig；未传入时从默认配置源加载。
    :param llm_gateway_settings: 可选 LlmGateway RuntimeConfig；未传入时从默认配置源加载。
    :param observability_settings: 可选 Observability RuntimeConfig；未传入时从默认配置源加载。
    :param vet_task_decomposer_settings: 可选 VetTaskDecomposer RuntimeConfig；未传入时从默认配置源加载。
    :param vet_context_builder_settings: 可选 VetContextBuilder RuntimeConfig；未传入时从默认配置源加载。
    :param standard_consultation_settings: 可选 StandardConsultationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param safety_trigger_settings: 可选 SafetyTriggerAgent RuntimeConfig；未传入时从默认配置源加载。
    :param education_agent_settings: 可选 EducationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param nonmedical_pet_care_settings: 可选 NonmedicalPetCareAgent RuntimeConfig；未传入时从默认配置源加载。
    :return: None。
    :raises RuntimeConfigError: 当候选配置违反安全锁定项、跨组件关系或 trace-safe 约束时抛出。
    """

    resolved_observability_settings = (
        observability_settings
        if observability_settings is not None
        else load_observability_settings()
    )
    resolved_conversation_store_settings = (
        conversation_store_settings
        if conversation_store_settings is not None
        else load_conversation_store_settings()
    )
    resolved_llm_gateway_settings = (
        llm_gateway_settings
        if llm_gateway_settings is not None
        else load_llm_gateway_settings()
    )
    resolved_vet_task_decomposer_settings = (
        vet_task_decomposer_settings
        if vet_task_decomposer_settings is not None
        else load_vet_task_decomposer_settings()
    )
    resolved_vet_context_builder_settings = (
        vet_context_builder_settings
        if vet_context_builder_settings is not None
        else load_vet_context_builder_settings()
    )
    resolved_standard_consultation_settings = (
        standard_consultation_settings
        if standard_consultation_settings is not None
        else load_standard_consultation_agent_settings()
    )
    resolved_safety_trigger_settings = (
        safety_trigger_settings
        if safety_trigger_settings is not None
        else load_safety_trigger_agent_settings()
    )
    resolved_education_agent_settings = (
        education_agent_settings
        if education_agent_settings is not None
        else load_education_agent_settings()
    )
    resolved_nonmedical_pet_care_settings = (
        nonmedical_pet_care_settings
        if nonmedical_pet_care_settings is not None
        else load_nonmedical_pet_care_agent_settings()
    )
    _validate_runtime_config_safety_locks(runtime_config_settings)
    _validate_runtime_config_relations(
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
        conversation_store_settings=resolved_conversation_store_settings,
    )
    summary = _build_trace_safe_summary(
        runtime_config_settings=runtime_config_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
        conversation_store_settings=resolved_conversation_store_settings,
        llm_gateway_settings=resolved_llm_gateway_settings,
        observability_settings=resolved_observability_settings,
        vet_task_decomposer_settings=resolved_vet_task_decomposer_settings,
        vet_context_builder_settings=resolved_vet_context_builder_settings,
        standard_consultation_settings=resolved_standard_consultation_settings,
        safety_trigger_settings=resolved_safety_trigger_settings,
        education_agent_settings=resolved_education_agent_settings,
        nonmedical_pet_care_settings=resolved_nonmedical_pet_care_settings,
    )
    _validate_trace_safe_summary(summary)


def build_runtime_config_snapshot(
    *,
    runtime_config_settings: RuntimeConfigSettings,
    api_ingress_settings: ApiIngressSettings,
    checkpoint_store_settings: CheckpointStoreSettings,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    vet_task_decomposer_settings: VetTaskDecomposerSettings | None = None,
    vet_context_builder_settings: VetContextBuilderSettings | None = None,
    standard_consultation_settings: StandardConsultationAgentSettings | None = None,
    safety_trigger_settings: SafetyTriggerAgentSettings | None = None,
    education_agent_settings: EducationAgentSettings | None = None,
    nonmedical_pet_care_settings: NonmedicalPetCareAgentSettings | None = None,
) -> RuntimeConfigSnapshot:
    """构建 RuntimeConfig 不可变快照。

    :param runtime_config_settings: RuntimeConfig 组件自身配置。
    :param api_ingress_settings: API 接入组件配置。
    :param checkpoint_store_settings: CheckpointStore RuntimeConfig。
    :param conversation_store_settings: 可选 ConversationStore RuntimeConfig；未传入时从默认配置源加载。
    :param llm_gateway_settings: 可选 LlmGateway RuntimeConfig；未传入时从默认配置源加载。
    :param observability_settings: 可选 Observability RuntimeConfig；未传入时从默认配置源加载。
    :param vet_task_decomposer_settings: 可选 VetTaskDecomposer RuntimeConfig；未传入时从默认配置源加载。
    :param vet_context_builder_settings: 可选 VetContextBuilder RuntimeConfig；未传入时从默认配置源加载。
    :param standard_consultation_settings: 可选 StandardConsultationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param safety_trigger_settings: 可选 SafetyTriggerAgent RuntimeConfig；未传入时从默认配置源加载。
    :param education_agent_settings: 可选 EducationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param nonmedical_pet_care_settings: 可选 NonmedicalPetCareAgent RuntimeConfig；未传入时从默认配置源加载。
    :return: 已完成校验的 RuntimeConfig 快照。
    :raises RuntimeConfigError: 当配置校验失败或 trace-safe 摘要不安全时抛出。
    """

    resolved_observability_settings = (
        observability_settings
        if observability_settings is not None
        else load_observability_settings()
    )
    resolved_conversation_store_settings = (
        conversation_store_settings
        if conversation_store_settings is not None
        else load_conversation_store_settings()
    )
    resolved_llm_gateway_settings = (
        llm_gateway_settings
        if llm_gateway_settings is not None
        else load_llm_gateway_settings()
    )
    resolved_vet_task_decomposer_settings = (
        vet_task_decomposer_settings
        if vet_task_decomposer_settings is not None
        else load_vet_task_decomposer_settings()
    )
    resolved_vet_context_builder_settings = (
        vet_context_builder_settings
        if vet_context_builder_settings is not None
        else load_vet_context_builder_settings()
    )
    resolved_standard_consultation_settings = (
        standard_consultation_settings
        if standard_consultation_settings is not None
        else load_standard_consultation_agent_settings()
    )
    resolved_safety_trigger_settings = (
        safety_trigger_settings
        if safety_trigger_settings is not None
        else load_safety_trigger_agent_settings()
    )
    resolved_education_agent_settings = (
        education_agent_settings
        if education_agent_settings is not None
        else load_education_agent_settings()
    )
    resolved_nonmedical_pet_care_settings = (
        nonmedical_pet_care_settings
        if nonmedical_pet_care_settings is not None
        else load_nonmedical_pet_care_agent_settings()
    )
    validate_runtime_config_candidate(
        runtime_config_settings=runtime_config_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
        conversation_store_settings=resolved_conversation_store_settings,
        llm_gateway_settings=resolved_llm_gateway_settings,
        observability_settings=resolved_observability_settings,
        vet_task_decomposer_settings=resolved_vet_task_decomposer_settings,
        vet_context_builder_settings=resolved_vet_context_builder_settings,
        standard_consultation_settings=resolved_standard_consultation_settings,
        safety_trigger_settings=resolved_safety_trigger_settings,
        education_agent_settings=resolved_education_agent_settings,
        nonmedical_pet_care_settings=resolved_nonmedical_pet_care_settings,
    )
    trace_safe_summary = _build_trace_safe_summary(
        runtime_config_settings=runtime_config_settings,
        api_ingress_settings=api_ingress_settings,
        checkpoint_store_settings=checkpoint_store_settings,
        conversation_store_settings=resolved_conversation_store_settings,
        llm_gateway_settings=resolved_llm_gateway_settings,
        observability_settings=resolved_observability_settings,
        vet_task_decomposer_settings=resolved_vet_task_decomposer_settings,
        vet_context_builder_settings=resolved_vet_context_builder_settings,
        standard_consultation_settings=resolved_standard_consultation_settings,
        safety_trigger_settings=resolved_safety_trigger_settings,
        education_agent_settings=resolved_education_agent_settings,
        nonmedical_pet_care_settings=resolved_nonmedical_pet_care_settings,
    )
    config_snapshot_id = _build_config_snapshot_id(trace_safe_summary)
    summary_with_snapshot_id: JsonMap = {
        **trace_safe_summary,
        "config_snapshot_id": config_snapshot_id,
    }
    _validate_trace_safe_summary(summary_with_snapshot_id)
    return RuntimeConfigSnapshot(
        config_snapshot_id=config_snapshot_id,
        params_version=runtime_config_settings.params_version,
        config_schema_version=runtime_config_settings.config_schema_version,
        trace_safe_schema_version=RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION,
        created_at=datetime.now(UTC),
        runtime_config=runtime_config_settings,
        api_ingress=api_ingress_settings,
        checkpoint_store=checkpoint_store_settings,
        conversation_store=resolved_conversation_store_settings,
        llm_gateway=resolved_llm_gateway_settings,
        observability=resolved_observability_settings,
        vet_task_decomposer=resolved_vet_task_decomposer_settings,
        vet_context_builder=resolved_vet_context_builder_settings,
        standard_consultation=resolved_standard_consultation_settings,
        safety_trigger=resolved_safety_trigger_settings,
        education_agent=resolved_education_agent_settings,
        nonmedical_pet_care=resolved_nonmedical_pet_care_settings,
        trace_safe_summary=summary_with_snapshot_id,
    )


class RuntimeConfigProvider:
    """应用内 RuntimeConfig 只读 provider。"""

    def __init__(self, snapshot: RuntimeConfigSnapshot) -> None:
        """初始化 RuntimeConfig provider。

        :param snapshot: 当前有效配置快照。
        :return: None。
        """

        self._snapshot = snapshot

    def is_ready(self) -> bool:
        """判断 RuntimeConfig provider 是否可用。

        :return: 若 provider 持有有效快照则返回 True。
        """

        return bool(self._snapshot.config_snapshot_id)

    def current_snapshot(self) -> RuntimeConfigSnapshot:
        """读取当前有效配置快照。

        :return: 当前有效 RuntimeConfig 快照。
        :raises RuntimeConfigError: 当 provider 未持有有效快照时抛出。
        """

        if not self.is_ready():
            raise RuntimeConfigError(
                code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
                operation=RuntimeConfigOperation.GET_CURRENT_CONFIG_SNAPSHOT,
                message="RuntimeConfig 当前快照不存在",
                retryable=True,
            )
        return self._snapshot

    def get_namespace(
        self,
        namespace: RuntimeConfigNamespace,
    ) -> (
        RuntimeConfigSettings
        | ApiIngressSettings
        | CheckpointStoreSettings
        | ConversationStoreSettings
        | LlmGatewaySettings
        | ObservabilitySettings
        | VetTaskDecomposerSettings
        | VetContextBuilderSettings
        | StandardConsultationAgentSettings
        | SafetyTriggerAgentSettings
        | EducationAgentSettings
        | NonmedicalPetCareAgentSettings
    ):
        """按命名空间读取配置对象。

        :param namespace: 需要读取的配置命名空间。
        :return: 命名空间对应的配置对象。
        :raises RuntimeConfigError: 当命名空间不存在时抛出。
        """

        snapshot = self.current_snapshot()
        if namespace is RuntimeConfigNamespace.RUNTIME_CONFIG:
            return snapshot.runtime_config
        if namespace is RuntimeConfigNamespace.API_INGRESS:
            return snapshot.api_ingress
        if namespace is RuntimeConfigNamespace.CHECKPOINT_STORE:
            return snapshot.checkpoint_store
        if namespace is RuntimeConfigNamespace.CONVERSATION_STORE:
            return snapshot.conversation_store
        if namespace is RuntimeConfigNamespace.LLM_GATEWAY:
            return snapshot.llm_gateway
        if namespace is RuntimeConfigNamespace.OBSERVABILITY:
            return snapshot.observability
        if namespace is RuntimeConfigNamespace.VET_TASK_DECOMPOSER:
            return snapshot.vet_task_decomposer
        if namespace is RuntimeConfigNamespace.VET_CONTEXT_BUILDER:
            return snapshot.vet_context_builder
        if namespace is RuntimeConfigNamespace.STANDARD_CONSULTATION:
            return snapshot.standard_consultation
        if namespace is RuntimeConfigNamespace.SAFETY_TRIGGER:
            return snapshot.safety_trigger
        if namespace is RuntimeConfigNamespace.EDUCATION_AGENT:
            return snapshot.education_agent
        if namespace is RuntimeConfigNamespace.NONMEDICAL_PET_CARE:
            return snapshot.nonmedical_pet_care
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
            operation=RuntimeConfigOperation.GET_CONFIG_NAMESPACE,
            message="RuntimeConfig 命名空间不存在",
            retryable=False,
            conflict_with={"namespace": namespace.value},
        )

    def get_value(
        self,
        *,
        key: str,
        config_snapshot_id: str | None = None,
    ) -> object:
        """按点路径读取指定配置值。

        :param key: 点路径格式配置键，首段必须为 RuntimeConfig 命名空间。
        :param config_snapshot_id: 可选快照 ID；传入时必须与当前快照一致。
        :return: 配置键对应的值。
        :raises RuntimeConfigError: 当快照 ID 不匹配、配置键格式非法或配置键不存在时抛出。
        """

        snapshot = self.current_snapshot()
        if (
            config_snapshot_id is not None
            and config_snapshot_id != snapshot.config_snapshot_id
        ):
            raise RuntimeConfigError(
                code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
                operation=RuntimeConfigOperation.GET_CONFIG_VALUE,
                message="RuntimeConfig 指定快照不存在",
                retryable=False,
                conflict_with={
                    "expected_config_snapshot_id": snapshot.config_snapshot_id,
                    "actual_config_snapshot_id": config_snapshot_id,
                },
            )
        namespace, path = _split_config_value_key(key)
        namespace_settings = self.get_namespace(namespace)
        namespace_mapping = _dump_namespace_for_lookup(namespace_settings)
        return _read_mapping_path(
            source=namespace_mapping,
            path=path,
            original_key=key,
        )

    def trace_safe_summary(
        self,
        *,
        config_snapshot_id: str | None = None,
    ) -> JsonMap:
        """读取可写入逻辑链的配置摘要。

        :param config_snapshot_id: 可选快照 ID；传入时必须与当前快照一致。
        :return: 当前快照的 trace-safe 配置摘要。
        :raises RuntimeConfigError: 当快照 ID 不匹配或摘要不安全时抛出。
        """

        snapshot = self.current_snapshot()
        if (
            config_snapshot_id is not None
            and config_snapshot_id != snapshot.config_snapshot_id
        ):
            raise RuntimeConfigError(
                code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
                operation=RuntimeConfigOperation.GET_TRACE_SAFE_CONFIG_SUMMARY,
                message="RuntimeConfig 指定快照不存在",
                retryable=False,
                conflict_with={
                    "expected_config_snapshot_id": snapshot.config_snapshot_id,
                    "actual_config_snapshot_id": config_snapshot_id,
                },
            )
        _validate_trace_safe_summary(snapshot.trace_safe_summary)
        return dict(snapshot.trace_safe_summary)


def load_runtime_config_settings(
    config_path: str | Path | None = None,
) -> RuntimeConfigSettings:
    """加载 RuntimeConfig 组件自身配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用模型默认路径。
    :return: 已完成 Pydantic 校验的 RuntimeConfig 组件自身配置。
    """

    if config_path is None:
        return RuntimeConfigSettings()

    class _PathBoundRuntimeConfigSettings(RuntimeConfigSettings):
        """绑定指定 YAML 文件路径的 RuntimeConfig Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundRuntimeConfigSettings()


def create_runtime_config_provider(
    *,
    runtime_config_settings: RuntimeConfigSettings | None = None,
    api_ingress_settings: ApiIngressSettings | None = None,
    checkpoint_store_settings: CheckpointStoreSettings | None = None,
    conversation_store_settings: ConversationStoreSettings | None = None,
    llm_gateway_settings: LlmGatewaySettings | None = None,
    observability_settings: ObservabilitySettings | None = None,
    vet_task_decomposer_settings: VetTaskDecomposerSettings | None = None,
    vet_context_builder_settings: VetContextBuilderSettings | None = None,
    standard_consultation_settings: StandardConsultationAgentSettings | None = None,
    safety_trigger_settings: SafetyTriggerAgentSettings | None = None,
    education_agent_settings: EducationAgentSettings | None = None,
    nonmedical_pet_care_settings: NonmedicalPetCareAgentSettings | None = None,
) -> RuntimeConfigProvider:
    """创建应用内 RuntimeConfig provider。

    :param runtime_config_settings: 可选 RuntimeConfig 组件自身配置；未传入时从默认配置源加载。
    :param api_ingress_settings: 可选 API 接入组件配置；未传入时从默认配置源加载。
    :param checkpoint_store_settings: 可选 CheckpointStore RuntimeConfig；未传入时从默认配置源加载。
    :param conversation_store_settings: 可选 ConversationStore RuntimeConfig；未传入时从默认配置源加载。
    :param llm_gateway_settings: 可选 LlmGateway RuntimeConfig；未传入时从默认配置源加载。
    :param observability_settings: 可选 Observability RuntimeConfig；未传入时从默认配置源加载。
    :param vet_task_decomposer_settings: 可选 VetTaskDecomposer RuntimeConfig；未传入时从默认配置源加载。
    :param vet_context_builder_settings: 可选 VetContextBuilder RuntimeConfig；未传入时从默认配置源加载。
    :param standard_consultation_settings: 可选 StandardConsultationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param safety_trigger_settings: 可选 SafetyTriggerAgent RuntimeConfig；未传入时从默认配置源加载。
    :param education_agent_settings: 可选 EducationAgent RuntimeConfig；未传入时从默认配置源加载。
    :param nonmedical_pet_care_settings: 可选 NonmedicalPetCareAgent RuntimeConfig；未传入时从默认配置源加载。
    :return: 持有当前有效配置快照的 RuntimeConfig provider。
    :raises RuntimeConfigError: 当配置校验失败或 trace-safe 摘要不安全时抛出。
    """

    resolved_runtime_config_settings = (
        runtime_config_settings
        if runtime_config_settings is not None
        else load_runtime_config_settings()
    )
    resolved_api_ingress_settings = (
        api_ingress_settings
        if api_ingress_settings is not None
        else load_api_ingress_settings()
    )
    resolved_checkpoint_store_settings = (
        checkpoint_store_settings
        if checkpoint_store_settings is not None
        else load_checkpoint_store_settings()
    )
    resolved_conversation_store_settings = (
        conversation_store_settings
        if conversation_store_settings is not None
        else load_conversation_store_settings()
    )
    resolved_observability_settings = (
        observability_settings
        if observability_settings is not None
        else load_observability_settings()
    )
    resolved_llm_gateway_settings = (
        llm_gateway_settings
        if llm_gateway_settings is not None
        else load_llm_gateway_settings()
    )
    resolved_vet_task_decomposer_settings = (
        vet_task_decomposer_settings
        if vet_task_decomposer_settings is not None
        else load_vet_task_decomposer_settings()
    )
    resolved_vet_context_builder_settings = (
        vet_context_builder_settings
        if vet_context_builder_settings is not None
        else load_vet_context_builder_settings()
    )
    resolved_standard_consultation_settings = (
        standard_consultation_settings
        if standard_consultation_settings is not None
        else load_standard_consultation_agent_settings()
    )
    resolved_safety_trigger_settings = (
        safety_trigger_settings
        if safety_trigger_settings is not None
        else load_safety_trigger_agent_settings()
    )
    resolved_education_agent_settings = (
        education_agent_settings
        if education_agent_settings is not None
        else load_education_agent_settings()
    )
    resolved_nonmedical_pet_care_settings = (
        nonmedical_pet_care_settings
        if nonmedical_pet_care_settings is not None
        else load_nonmedical_pet_care_agent_settings()
    )
    snapshot = build_runtime_config_snapshot(
        runtime_config_settings=resolved_runtime_config_settings,
        api_ingress_settings=resolved_api_ingress_settings,
        checkpoint_store_settings=resolved_checkpoint_store_settings,
        conversation_store_settings=resolved_conversation_store_settings,
        llm_gateway_settings=resolved_llm_gateway_settings,
        observability_settings=resolved_observability_settings,
        vet_task_decomposer_settings=resolved_vet_task_decomposer_settings,
        vet_context_builder_settings=resolved_vet_context_builder_settings,
        standard_consultation_settings=resolved_standard_consultation_settings,
        safety_trigger_settings=resolved_safety_trigger_settings,
        education_agent_settings=resolved_education_agent_settings,
        nonmedical_pet_care_settings=resolved_nonmedical_pet_care_settings,
    )
    return RuntimeConfigProvider(snapshot)


__all__: tuple[str, ...] = (
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "RUNTIME_CONFIG_TRACE_SAFE_SCHEMA_VERSION",
    "JsonMap",
    "RuntimeConfigError",
    "RuntimeConfigErrorCode",
    "RuntimeConfigErrorDto",
    "RuntimeConfigNamespace",
    "RuntimeConfigOperation",
    "RuntimeConfigProvider",
    "RuntimeConfigSafetyLockSettings",
    "RuntimeConfigSettings",
    "RuntimeConfigSnapshot",
    "EducationAgentSettings",
    "NonmedicalPetCareAgentSettings",
    "StandardConsultationAgentSettings",
    "VetContextBuilderSettings",
    "VetTaskDecomposerSettings",
    "build_runtime_config_error_dto",
    "build_runtime_config_snapshot",
    "create_runtime_config_provider",
    "load_education_agent_settings",
    "load_nonmedical_pet_care_agent_settings",
    "load_standard_consultation_agent_settings",
    "load_runtime_config_settings",
    "load_vet_task_decomposer_settings",
    "load_vet_context_builder_settings",
    "validate_runtime_config_candidate",
)
