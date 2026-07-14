##################################################################################################
# 文件: src/veterinary_agent/config/guardrail_framework.py
# 作用: 定义 GuardrailFramework 的阶段策略、超时、重试、失败处理和 trace 运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载，不执行护栏 handler 或业务安全规则。
##################################################################################################

from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_GUARDRAIL_FRAMEWORK_CONFIG_PATH = Path("configs/guardrail_framework.yaml")

_ALLOWED_FAILURE_STRATEGIES: frozenset[str] = frozenset(
    {
        "fail_closed_block",
        "fail_open_degraded",
        "fallback",
    }
)


class _GuardrailFrameworkConfigModel(BaseModel):
    """GuardrailFramework 严格配置模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串配置值。

        :param value: 原始配置字段值。
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class GuardrailFrameworkStageSettings(_GuardrailFrameworkConfigModel):
    """GuardrailFramework 单阶段默认策略配置。"""

    enabled: bool = Field(default=True, description="是否启用当前护栏阶段。")
    policy_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前阶段默认策略 ID。",
    )
    policy_version: str = Field(
        default="guardrail-policy.v1",
        min_length=1,
        max_length=128,
        description="当前阶段默认策略版本。",
    )
    handler_ref: str = Field(
        min_length=1,
        max_length=256,
        description="当前阶段默认 handler 引用。",
    )
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
    failure_strategy: str = Field(
        default="fail_closed_block",
        description="handler 失败后的默认处理策略。",
    )
    fallback_template_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="失败策略为 fallback 时使用的模板引用。",
    )
    emit_trace_events: bool = Field(
        default=True,
        description="当前阶段是否写入护栏 trace 事件。",
    )

    @field_validator("failure_strategy")
    @classmethod
    def _validate_failure_strategy(cls, value: str) -> str:
        """校验失败处理策略取值。

        :param value: 原始失败处理策略。
        :return: 已通过校验的失败处理策略。
        :raises ValueError: 当失败处理策略不受支持时抛出。
        """

        if value not in _ALLOWED_FAILURE_STRATEGIES:
            raise ValueError("GuardrailFramework failure_strategy 不受支持")
        return value

    @model_validator(mode="after")
    def _validate_stage_relations(self) -> Self:
        """校验阶段配置字段之间的关系。

        :return: 已通过关系校验的阶段配置。
        :raises ValueError: 当 handler 超时大于阶段超时或 fallback 策略缺少模板时抛出。
        """

        if self.handler_timeout_seconds > self.stage_timeout_seconds:
            raise ValueError("handler_timeout_seconds 不得大于 stage_timeout_seconds")
        if self.failure_strategy == "fallback" and self.fallback_template_ref is None:
            raise ValueError(
                "failure_strategy=fallback 时必须携带 fallback_template_ref"
            )
        return self


class GuardrailFrameworkSettings(BaseSettings):
    """GuardrailFramework 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="GUARDRAIL_FRAMEWORK_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_GUARDRAIL_FRAMEWORK_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 GuardrailFramework。")
    config_version: str = Field(
        default="guardrail-framework-config.v1",
        min_length=1,
        max_length=128,
        description="GuardrailFramework 配置版本。",
    )
    framework_version: str = Field(
        default="guardrail-framework.v1",
        min_length=1,
        max_length=128,
        description="GuardrailFramework 组件版本。",
    )
    trace_schema_version: str = Field(
        default="guardrail.trace.v1",
        min_length=1,
        max_length=128,
        description="护栏 trace 事件 schema 引用。",
    )
    capture_policy_version: str = Field(
        default="vet-trace-capture-policy.v1",
        min_length=1,
        max_length=128,
        description="护栏 trace 使用的 capture policy 版本。",
    )
    persist_full_text: bool = Field(
        default=False,
        description="是否允许护栏 trace 写入完整正文；默认关闭。",
    )
    pre_generation: GuardrailFrameworkStageSettings = Field(
        default_factory=lambda: GuardrailFrameworkStageSettings(
            policy_id="guardrail.pre_generation.default",
            handler_ref="todo_pre_generation_guard",
            stage_timeout_seconds=8.0,
            handler_timeout_seconds=6.0,
            max_attempts=1,
            failure_strategy="fail_closed_block",
        ),
        description="生成前护栏默认策略配置。",
    )
    post_generation_review: GuardrailFrameworkStageSettings = Field(
        default_factory=lambda: GuardrailFrameworkStageSettings(
            policy_id="guardrail.post_generation_review.default",
            handler_ref="todo_post_generation_review",
            stage_timeout_seconds=12.0,
            handler_timeout_seconds=10.0,
            max_attempts=1,
            failure_strategy="fail_closed_block",
        ),
        description="生成后审查默认策略配置。",
    )
    deterministic_gate: GuardrailFrameworkStageSettings = Field(
        default_factory=lambda: GuardrailFrameworkStageSettings(
            policy_id="guardrail.deterministic_gate.default",
            handler_ref="todo_deterministic_gate",
            stage_timeout_seconds=6.0,
            handler_timeout_seconds=5.0,
            max_attempts=1,
            failure_strategy="fail_closed_block",
        ),
        description="确定性发布门默认策略配置。",
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
        """定义 GuardrailFramework 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_GUARDRAIL_FRAMEWORK_CONFIG_PATH
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path),
            file_secret_settings,
        )


def load_guardrail_framework_settings(
    config_path: Path | None = None,
) -> GuardrailFrameworkSettings:
    """加载 GuardrailFramework 配置。

    :param config_path: 可选 YAML 配置路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 GuardrailFramework 配置。
    """

    if config_path is None:
        return GuardrailFrameworkSettings()

    class _PathBoundGuardrailFrameworkSettings(GuardrailFrameworkSettings):
        """绑定指定 YAML 文件路径的 GuardrailFramework Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = config_path

    return _PathBoundGuardrailFrameworkSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_GUARDRAIL_FRAMEWORK_CONFIG_PATH",
    "GuardrailFrameworkSettings",
    "GuardrailFrameworkStageSettings",
    "load_guardrail_framework_settings",
)
