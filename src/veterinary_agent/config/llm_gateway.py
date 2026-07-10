##################################################################################################
# 文件: src/veterinary_agent/config/llm_gateway.py
# 作用: 定义 LlmGateway 组件运行配置、模型 profile、供应商路由、能力、超时与重试策略。
# 边界: 仅负责配置结构、校验与加载；不创建网络客户端、不执行模型调用、不读取供应商密钥明文。
##################################################################################################

from pathlib import Path
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_LLM_GATEWAY_CONFIG_PATH = Path("configs/llm_gateway.yaml")

_KNOWN_LLM_GATEWAY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "LLM_GATEWAY_NOT_READY",
        "LLM_PROFILE_NOT_FOUND",
        "LLM_PROFILE_UNAVAILABLE",
        "LLM_CAPABILITY_MISMATCH",
        "LLM_CONTEXT_LENGTH_EXCEEDED",
        "LLM_TIMEOUT",
        "LLM_FIRST_TOKEN_TIMEOUT",
        "LLM_PROXY_UNAVAILABLE",
        "LLM_PROVIDER_UNAVAILABLE",
        "LLM_RATE_LIMITED",
        "LLM_INVALID_REQUEST",
        "LLM_SAFETY_BLOCKED",
        "LLM_MALFORMED_RESPONSE",
        "LLM_RETRY_EXHAUSTED",
        "LLM_CONCURRENCY_LIMITED",
        "LLM_CANCELLED",
    }
)


class _LlmGatewayConfigModel(BaseModel):
    """LlmGateway 严格配置模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串配置值。

        :param value: 原始配置字段值。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class LlmModelCapabilityConfig(_LlmGatewayConfigModel):
    """模型路由能力声明。"""

    max_context_tokens: int = Field(
        default=8192,
        ge=256,
        description="模型最大上下文长度。",
    )
    supports_streaming: bool = Field(
        default=True,
        description="模型是否支持流式调用。",
    )
    supports_structured_output: bool = Field(
        default=False,
        description="模型是否支持 JSON 对象或 JSON Schema 结构化输出。",
    )
    supports_tools: bool = Field(
        default=False,
        description="模型是否支持工具调用。",
    )
    supports_vision: bool = Field(
        default=False,
        description="模型是否支持视觉输入。",
    )


class LlmRequiredCapabilityConfig(_LlmGatewayConfigModel):
    """ModelProfile 对下游路由的最低能力要求。"""

    streaming: bool = Field(
        default=False,
        description="是否要求下游路由支持流式调用。",
    )
    structured_output: bool = Field(
        default=False,
        description="是否要求下游路由支持结构化输出。",
    )
    tools: bool = Field(
        default=False,
        description="是否要求下游路由支持工具调用。",
    )
    vision: bool = Field(
        default=False,
        description="是否要求下游路由支持视觉输入。",
    )


class LlmTimeoutPolicyConfig(_LlmGatewayConfigModel):
    """单个 ModelProfile 的调用超时策略。"""

    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60.0,
        description="连接模型代理的超时时间。",
    )
    first_token_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=300.0,
        description="流式调用等待首个有效事件的超时时间。",
    )
    read_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600.0,
        description="读取模型代理响应的超时时间。",
    )
    total_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        le=900.0,
        description="单次物理模型请求的总超时时间。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验调用超时参数之间的关系。

        :return: 已通过关系校验的超时策略。
        :raises ValueError: 当连接、首 token 或读取超时大于总超时时间时抛出。
        """

        if self.connect_timeout_seconds > self.total_timeout_seconds:
            raise ValueError("connect_timeout_seconds 不得大于 total_timeout_seconds")
        if self.first_token_timeout_seconds > self.total_timeout_seconds:
            raise ValueError(
                "first_token_timeout_seconds 不得大于 total_timeout_seconds"
            )
        if self.read_timeout_seconds > self.total_timeout_seconds:
            raise ValueError("read_timeout_seconds 不得大于 total_timeout_seconds")
        return self


class LlmRetryPolicyConfig(_LlmGatewayConfigModel):
    """单个 ModelProfile 的有限重试策略。"""

    max_attempts: int = Field(
        default=1,
        ge=1,
        le=5,
        description="当前 profile 最多执行的物理请求次数，包含首次调用。",
    )
    initial_backoff_seconds: float = Field(
        default=0.25,
        ge=0,
        le=30.0,
        description="首次重试前的等待时间。",
    )
    backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="连续重试等待时间的指数退避系数。",
    )
    max_backoff_seconds: float = Field(
        default=4.0,
        ge=0,
        le=60.0,
        description="单次重试等待时间上限。",
    )
    jitter: bool = Field(
        default=True,
        description="是否为重试等待时间加入随机抖动。",
    )
    retryable_error_codes: list[str] = Field(
        default_factory=lambda: [
            "LLM_TIMEOUT",
            "LLM_PROXY_UNAVAILABLE",
            "LLM_PROVIDER_UNAVAILABLE",
            "LLM_RATE_LIMITED",
        ],
        description="允许在当前 profile 内重试的 LlmGateway 标准错误码。",
    )

    @field_validator("retryable_error_codes")
    @classmethod
    def _normalize_retryable_error_codes(cls, values: list[str]) -> list[str]:
        """规范化可重试错误码列表。

        :param values: 原始可重试错误码列表。
        :return: 转为大写、去重且保持顺序的错误码列表。
        :raises ValueError: 当列表包含空错误码时抛出。
        """

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized):
            raise ValueError("retryable_error_codes 不得包含空值")
        unknown_codes = sorted(
            set(normalized).difference(_KNOWN_LLM_GATEWAY_ERROR_CODES)
        )
        if unknown_codes:
            raise ValueError(
                "retryable_error_codes 包含未知 LlmGateway 错误码: "
                + ", ".join(unknown_codes)
            )
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def _validate_backoff_relations(self) -> Self:
        """校验重试退避参数之间的关系。

        :return: 已通过关系校验的重试策略。
        :raises ValueError: 当首次退避时间大于最大退避时间时抛出。
        """

        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("initial_backoff_seconds 不得大于 max_backoff_seconds")
        return self


class LlmTracePolicyConfig(_LlmGatewayConfigModel):
    """模型调用摘要留痕策略。"""

    emit_logic_trace_summary: bool = Field(
        default=True,
        description="是否向模型调用摘要端口提交脱敏调用摘要。",
    )


class LlmProviderRouteConfig(_LlmGatewayConfigModel):
    """实际下游模型代理路由配置。"""

    provider_route_id: str = Field(
        min_length=1,
        max_length=128,
        description="系统内稳定供应商路由 ID。",
    )
    adapter_type: Literal["openai_compatible"] = Field(
        default="openai_compatible",
        description="当前路由使用的协议适配器类型。",
    )
    provider_name: str = Field(
        min_length=1,
        max_length=128,
        description="用于观测聚合的供应商或代理类型名称。",
    )
    base_url: str = Field(
        min_length=1,
        max_length=1024,
        description="OpenAI-compatible 模型代理基础地址；不会进入 trace-safe 摘要。",
    )
    request_path: str = Field(
        default="/v1/chat/completions",
        min_length=1,
        max_length=256,
        description="聊天补全接口路径。",
    )
    health_path: str | None = Field(
        default="/health",
        max_length=256,
        description="代理健康检查路径；为空时仅检查适配器本地就绪状态。",
    )
    model_alias: str = Field(
        min_length=1,
        max_length=256,
        description="发送给模型代理的稳定模型别名。",
    )
    api_key_env: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="保存代理访问令牌的环境变量名；不会进入 trace-safe 摘要。",
    )
    auth_required: bool = Field(
        default=False,
        description="当前路由是否强制要求环境变量提供代理访问令牌。",
    )
    include_stream_usage: bool = Field(
        default=True,
        description="流式请求是否要求代理在末尾返回 usage。",
    )
    max_concurrency: int = Field(
        default=16,
        ge=1,
        le=4096,
        description="当前物理路由的实例内最大并发调用数。",
    )
    capability: LlmModelCapabilityConfig = Field(
        default_factory=LlmModelCapabilityConfig,
        description="当前路由实际模型能力声明。",
    )

    @field_validator("request_path", "health_path")
    @classmethod
    def _validate_optional_path(cls, value: str | None) -> str | None:
        """校验模型代理接口路径。

        :param value: 原始接口路径或空值。
        :return: 以斜杠开头的接口路径或空值。
        :raises ValueError: 当非空路径不是绝对路径时抛出。
        """

        if value is not None and not value.startswith("/"):
            raise ValueError("模型代理接口路径必须以 / 开头")
        return value

    @model_validator(mode="after")
    def _validate_auth_configuration(self) -> Self:
        """校验路由鉴权配置。

        :return: 已通过鉴权关系校验的路由配置。
        :raises ValueError: 当要求鉴权但未配置令牌环境变量名时抛出。
        """

        if self.auth_required and self.api_key_env is None:
            raise ValueError("auth_required=true 时必须配置 api_key_env")
        return self


class LlmModelProfileConfig(_LlmGatewayConfigModel):
    """系统内版本化模型调用 profile。"""

    model_profile_id: str = Field(
        min_length=1,
        max_length=128,
        description="业务 Agent 使用的稳定模型 profile ID。",
    )
    profile_version: str = Field(
        min_length=1,
        max_length=128,
        description="当前模型 profile 版本。",
    )
    provider_route_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前 profile 首选下游路由 ID。",
    )
    required_capability: LlmRequiredCapabilityConfig = Field(
        default_factory=LlmRequiredCapabilityConfig,
        description="当前 profile 对路由的最低能力要求。",
    )
    timeout_policy: LlmTimeoutPolicyConfig = Field(
        default_factory=LlmTimeoutPolicyConfig,
        description="当前 profile 的物理请求超时策略。",
    )
    retry_policy: LlmRetryPolicyConfig = Field(
        default_factory=LlmRetryPolicyConfig,
        description="当前 profile 的有限重试策略。",
    )
    fallback_profile_ids: list[str] = Field(
        default_factory=list,
        description="当前 profile 失败后按顺序尝试的备用 profile ID。",
    )
    fallback_on_error_codes: list[str] = Field(
        default_factory=lambda: [
            "LLM_TIMEOUT",
            "LLM_PROXY_UNAVAILABLE",
            "LLM_PROVIDER_UNAVAILABLE",
            "LLM_RATE_LIMITED",
            "LLM_RETRY_EXHAUSTED",
        ],
        description="允许触发 profile 降级的 LlmGateway 标准错误码。",
    )
    reserved_output_tokens: int = Field(
        default=1024,
        ge=1,
        description="调用方未指定输出上限时为模型输出预留的 token 数。",
    )
    max_concurrency: int = Field(
        default=8,
        ge=1,
        le=4096,
        description="当前 profile 的实例内最大并发调用数。",
    )
    trace_policy: LlmTracePolicyConfig = Field(
        default_factory=LlmTracePolicyConfig,
        description="当前 profile 的模型调用摘要留痕策略。",
    )

    @field_validator("fallback_profile_ids")
    @classmethod
    def _normalize_fallback_profile_ids(cls, values: list[str]) -> list[str]:
        """规范化备用 profile ID 列表。

        :param values: 原始备用 profile ID 列表。
        :return: 去除空白、去重且保持顺序的备用 profile ID 列表。
        :raises ValueError: 当列表包含空 profile ID 时抛出。
        """

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("fallback_profile_ids 不得包含空值")
        return list(dict.fromkeys(normalized))

    @field_validator("fallback_on_error_codes")
    @classmethod
    def _normalize_fallback_error_codes(cls, values: list[str]) -> list[str]:
        """规范化允许触发降级的错误码列表。

        :param values: 原始降级错误码列表。
        :return: 转为大写、去重且保持顺序的错误码列表。
        :raises ValueError: 当列表包含空错误码时抛出。
        """

        normalized = [value.strip().upper() for value in values]
        if any(not value for value in normalized):
            raise ValueError("fallback_on_error_codes 不得包含空值")
        unknown_codes = sorted(
            set(normalized).difference(_KNOWN_LLM_GATEWAY_ERROR_CODES)
        )
        if unknown_codes:
            raise ValueError(
                "fallback_on_error_codes 包含未知 LlmGateway 错误码: "
                + ", ".join(unknown_codes)
            )
        return list(dict.fromkeys(normalized))


class LlmTokenEstimationConfig(_LlmGatewayConfigModel):
    """调用前保守 token 估算参数。"""

    chars_per_token: float = Field(
        default=2.0,
        gt=0,
        le=8.0,
        description="文本字符到 token 的保守换算比例。",
    )
    message_overhead_tokens: int = Field(
        default=4,
        ge=0,
        le=128,
        description="每条消息附加的协议开销 token 数。",
    )
    tool_overhead_tokens: int = Field(
        default=16,
        ge=0,
        le=1024,
        description="每个工具 schema 附加的协议开销 token 数。",
    )
    response_format_overhead_tokens: int = Field(
        default=16,
        ge=0,
        le=1024,
        description="结构化输出约束附加的协议开销 token 数。",
    )


class LlmGatewaySettings(BaseSettings):
    """LlmGateway 组件运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="LLM_GATEWAY_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_LLM_GATEWAY_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(
        default=False,
        description="是否启用真实 LlmGateway 模型调用能力。",
    )
    config_version: str = Field(
        default="llm-gateway.v1",
        min_length=1,
        max_length=128,
        description="LlmGateway 配置结构版本。",
    )
    max_total_attempts: int = Field(
        default=4,
        ge=1,
        le=16,
        description="一次逻辑模型调用跨重试和降级的最大物理请求次数。",
    )
    max_call_duration_seconds: float = Field(
        default=120.0,
        gt=0,
        le=1800.0,
        description="一次逻辑模型调用跨重试和降级的总时限。",
    )
    global_max_concurrency: int = Field(
        default=32,
        ge=1,
        le=8192,
        description="当前 LlmGateway 实例的全局最大并发调用数。",
    )
    concurrency_acquire_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60.0,
        description="等待实例内并发额度的最大时间。",
    )
    token_estimation: LlmTokenEstimationConfig = Field(
        default_factory=LlmTokenEstimationConfig,
        description="调用前保守 token 估算配置。",
    )
    provider_routes: list[LlmProviderRouteConfig] = Field(
        default_factory=list,
        description="可用下游供应商路由列表。",
    )
    model_profiles: list[LlmModelProfileConfig] = Field(
        default_factory=list,
        description="业务 Agent 可引用的版本化模型 profile 列表。",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定制 LlmGateway 配置来源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置来源。
        :param env_settings: 环境变量配置来源。
        :param dotenv_settings: ``.env`` 文件配置来源。
        :param file_secret_settings: 文件密钥配置来源。
        :return: 按优先级排列的配置来源元组。
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

    @model_validator(mode="after")
    def _validate_gateway_relations(self) -> Self:
        """校验路由、profile、降级链与能力关系。

        :return: 已通过完整关系校验的 LlmGateway 配置。
        :raises ValueError: 当 ID 重复、引用缺失、降级链成环或能力不兼容时抛出。
        """

        route_by_id = self._build_route_index()
        profile_by_id = self._build_profile_index()
        if self.enabled and not route_by_id:
            raise ValueError("enabled=true 时至少需要一个 provider route")
        if self.enabled and not profile_by_id:
            raise ValueError("enabled=true 时至少需要一个 model profile")
        self._validate_profile_routes(
            route_by_id=route_by_id,
            profile_by_id=profile_by_id,
        )
        self._validate_fallback_graph(profile_by_id=profile_by_id)
        return self

    def _build_route_index(self) -> dict[str, LlmProviderRouteConfig]:
        """构建供应商路由索引并校验 ID 唯一性。

        :return: 以 ``provider_route_id`` 为键的路由索引。
        :raises ValueError: 当存在重复路由 ID 时抛出。
        """

        route_by_id = {route.provider_route_id: route for route in self.provider_routes}
        if len(route_by_id) != len(self.provider_routes):
            raise ValueError("provider_routes 中存在重复 provider_route_id")
        return route_by_id

    def _build_profile_index(self) -> dict[str, LlmModelProfileConfig]:
        """构建模型 profile 索引并校验 ID 唯一性。

        :return: 以 ``model_profile_id`` 为键的 profile 索引。
        :raises ValueError: 当存在重复 profile ID 时抛出。
        """

        profile_by_id = {
            profile.model_profile_id: profile for profile in self.model_profiles
        }
        if len(profile_by_id) != len(self.model_profiles):
            raise ValueError("model_profiles 中存在重复 model_profile_id")
        return profile_by_id

    def _validate_profile_routes(
        self,
        *,
        route_by_id: dict[str, LlmProviderRouteConfig],
        profile_by_id: dict[str, LlmModelProfileConfig],
    ) -> None:
        """校验 profile 的路由引用与静态能力要求。

        :param route_by_id: 供应商路由索引。
        :param profile_by_id: 模型 profile 索引。
        :return: None。
        :raises ValueError: 当路由引用缺失、能力不满足或备用 profile 不兼容时抛出。
        """

        for profile in profile_by_id.values():
            route = route_by_id.get(profile.provider_route_id)
            if route is None:
                raise ValueError(
                    f"profile {profile.model_profile_id} 引用了不存在的 provider route"
                )
            self._validate_required_capability(
                profile=profile,
                route=route,
            )
            if profile.reserved_output_tokens >= route.capability.max_context_tokens:
                raise ValueError(
                    f"profile {profile.model_profile_id} 的输出预留必须小于上下文长度"
                )
            for fallback_profile_id in profile.fallback_profile_ids:
                fallback_profile = profile_by_id.get(fallback_profile_id)
                if fallback_profile is None:
                    raise ValueError(
                        f"profile {profile.model_profile_id} 引用了不存在的备用 profile"
                    )
                if fallback_profile_id == profile.model_profile_id:
                    raise ValueError("fallback_profile_ids 不得包含自身")
                fallback_route = route_by_id.get(fallback_profile.provider_route_id)
                if fallback_route is None:
                    raise ValueError(
                        f"备用 profile {fallback_profile_id} 引用了不存在的路由"
                    )
                self._validate_fallback_compatibility(
                    source_profile=profile,
                    source_route=route,
                    fallback_profile=fallback_profile,
                    fallback_route=fallback_route,
                )

    def _validate_required_capability(
        self,
        *,
        profile: LlmModelProfileConfig,
        route: LlmProviderRouteConfig,
    ) -> None:
        """校验路由满足 profile 的静态最低能力要求。

        :param profile: 待校验的模型 profile。
        :param route: profile 引用的供应商路由。
        :return: None。
        :raises ValueError: 当路由能力不满足 profile 要求时抛出。
        """

        required = profile.required_capability
        actual = route.capability
        mismatches = [
            name
            for name, required_value, actual_value in (
                ("streaming", required.streaming, actual.supports_streaming),
                (
                    "structured_output",
                    required.structured_output,
                    actual.supports_structured_output,
                ),
                ("tools", required.tools, actual.supports_tools),
                ("vision", required.vision, actual.supports_vision),
            )
            if required_value and not actual_value
        ]
        if mismatches:
            raise ValueError(
                f"profile {profile.model_profile_id} 的路由能力不满足: "
                + ", ".join(mismatches)
            )

    def _validate_fallback_compatibility(
        self,
        *,
        source_profile: LlmModelProfileConfig,
        source_route: LlmProviderRouteConfig,
        fallback_profile: LlmModelProfileConfig,
        fallback_route: LlmProviderRouteConfig,
    ) -> None:
        """校验备用 profile 不弱于来源 profile 的静态能力。

        :param source_profile: 声明降级链的来源 profile。
        :param source_route: 来源 profile 对应路由。
        :param fallback_profile: 备用 profile。
        :param fallback_route: 备用 profile 对应路由。
        :return: None。
        :raises ValueError: 当备用 profile 能力或上下文长度不足时抛出。
        """

        source_required = source_profile.required_capability
        fallback_actual = fallback_route.capability
        capability_pairs = (
            (source_required.streaming, fallback_actual.supports_streaming),
            (
                source_required.structured_output,
                fallback_actual.supports_structured_output,
            ),
            (source_required.tools, fallback_actual.supports_tools),
            (source_required.vision, fallback_actual.supports_vision),
        )
        if any(required and not actual for required, actual in capability_pairs):
            raise ValueError(
                f"备用 profile {fallback_profile.model_profile_id} "
                f"不满足来源 profile {source_profile.model_profile_id} 的能力要求"
            )
        if (
            fallback_route.capability.max_context_tokens
            < source_route.capability.max_context_tokens
        ):
            raise ValueError(
                f"备用 profile {fallback_profile.model_profile_id} 的上下文长度"
                f"小于来源 profile {source_profile.model_profile_id}"
            )

    def _validate_fallback_graph(
        self,
        *,
        profile_by_id: dict[str, LlmModelProfileConfig],
    ) -> None:
        """校验 profile 降级图不存在环。

        :param profile_by_id: 模型 profile 索引。
        :return: None。
        :raises ValueError: 当降级链存在环时抛出。
        """

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(profile_id: str) -> None:
            """深度优先遍历一个 profile 的降级链。

            :param profile_id: 当前遍历的模型 profile ID。
            :return: None。
            :raises ValueError: 当遍历命中正在访问的 profile 时抛出。
            """

            if profile_id in visited:
                return
            if profile_id in visiting:
                raise ValueError("model profile fallback chain 不得形成环")
            visiting.add(profile_id)
            profile = profile_by_id[profile_id]
            for fallback_profile_id in profile.fallback_profile_ids:
                visit(fallback_profile_id)
            visiting.remove(profile_id)
            visited.add(profile_id)

        for profile_id in profile_by_id:
            visit(profile_id)


def load_llm_gateway_settings(
    config_path: str | Path | None = None,
) -> LlmGatewaySettings:
    """加载 LlmGateway 组件配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认配置源。
    :return: 已完成 Pydantic 校验的 LlmGateway 配置。
    """

    if config_path is None:
        return LlmGatewaySettings()

    class _PathBoundLlmGatewaySettings(LlmGatewaySettings):
        """绑定指定 YAML 文件路径的 LlmGateway Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundLlmGatewaySettings()


__all__: tuple[str, ...] = (
    "DEFAULT_LLM_GATEWAY_CONFIG_PATH",
    "LlmGatewaySettings",
    "LlmModelCapabilityConfig",
    "LlmModelProfileConfig",
    "LlmProviderRouteConfig",
    "LlmRequiredCapabilityConfig",
    "LlmRetryPolicyConfig",
    "LlmTimeoutPolicyConfig",
    "LlmTokenEstimationConfig",
    "LlmTracePolicyConfig",
    "load_llm_gateway_settings",
)
