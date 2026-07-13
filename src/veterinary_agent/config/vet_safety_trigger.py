##################################################################################################
# 文件: src/veterinary_agent/config/vet_safety_trigger.py
# 作用: 定义 SafetyTriggerAgent 的急症生成、确认规划、自检、兜底与超时运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行急症判定、生成或发布。
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

DEFAULT_SAFETY_TRIGGER_CONFIG_PATH = Path("configs/vet_safety_trigger.yaml")


class _SafetyTriggerConfigModel(BaseModel):
    """SafetyTriggerAgent 严格配置模型基类。"""

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


class SafetyTriggerTimeoutConfig(_SafetyTriggerConfigModel):
    """SafetyTriggerAgent 外部能力调用超时配置。"""

    total_seconds: float = Field(
        default=6.0,
        gt=0,
        le=60.0,
        description="急症组件端到端软超时，单位为秒。",
    )
    planner_seconds: float = Field(
        default=1.0,
        gt=0,
        le=15.0,
        description="关键确认规划 Agent 调用超时，单位为秒。",
    )
    writer_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30.0,
        description="急症写作 Agent 调用超时，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验子调用超时不会超过组件总超时。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当规划或写作超时大于组件总超时时抛出。
        """

        if self.planner_seconds > self.total_seconds:
            raise ValueError("planner_seconds 不得大于 total_seconds")
        if self.writer_seconds > self.total_seconds:
            raise ValueError("writer_seconds 不得大于 total_seconds")
        return self


class SafetyTriggerRequirementConfig(_SafetyTriggerConfigModel):
    """SafetyTriggerAgent 最小安全要素配置。"""

    max_confirmation_count: int = Field(
        default=1,
        ge=0,
        le=1,
        description="急症简版最多允许的关键确认问题数。",
    )
    max_draft_chars: int = Field(
        default=2400,
        ge=400,
        le=8000,
        description="急症草稿正文最大字符数。",
    )
    require_disclaimer: bool = Field(
        default=True,
        description="是否要求急症草稿包含线上建议免责表述。",
    )
    forbidden_content_tags: list[str] = Field(
        default_factory=lambda: [
            "rag_citation",
            "t4_precise_dose",
            "full_differential",
            "delayed_care",
            "unrelated_longform",
        ],
        description="急症草稿禁止内容标签。",
    )


class SafetyTriggerAgentSettings(BaseSettings):
    """SafetyTriggerAgent 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="SAFETY_TRIGGER_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_SAFETY_TRIGGER_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 SafetyTriggerAgent。")
    config_version: str = Field(
        default="safety-trigger-config.v1",
        min_length=1,
        max_length=128,
        description="急症组件配置版本。",
    )
    safety_trigger_agent_version: str = Field(
        default="safety-trigger-agent.v1",
        min_length=1,
        max_length=128,
        description="急症组件业务版本。",
    )
    writer_version: str = Field(
        default="safety-trigger-writer.v1",
        min_length=1,
        max_length=128,
        description="急症写作策略版本。",
    )
    confirmation_planner_version: str = Field(
        default="safety-confirmation-planner.v1",
        min_length=1,
        max_length=128,
        description="关键确认规划策略版本。",
    )
    fallback_template_version: str = Field(
        default="safety-fallback-draft.v1",
        min_length=1,
        max_length=128,
        description="急症兜底模板版本。",
    )
    requirement_set_version: str = Field(
        default="safety-requirements.v1",
        min_length=1,
        max_length=128,
        description="最小安全要素版本。",
    )
    writer_agent_id: str = Field(
        default="safety_trigger_writer",
        min_length=1,
        max_length=128,
        description="急症写作 Agent 规格 ID。",
    )
    writer_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="急症写作 Agent 规格版本。",
    )
    confirmation_planner_agent_id: str = Field(
        default="safety_key_confirmation_planner",
        min_length=1,
        max_length=128,
        description="关键确认规划 Agent 规格 ID。",
    )
    confirmation_planner_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="关键确认规划 Agent 规格版本。",
    )
    timeouts: SafetyTriggerTimeoutConfig = Field(
        default_factory=SafetyTriggerTimeoutConfig,
        description="急症组件外部能力调用超时配置。",
    )
    requirements: SafetyTriggerRequirementConfig = Field(
        default_factory=SafetyTriggerRequirementConfig,
        description="急症生成最小安全要素配置。",
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
        """定义 SafetyTriggerAgent 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_SAFETY_TRIGGER_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 SafetyTriggerAgent YAML 文件的临时 Settings 类型。"""

            model_config = SettingsConfigDict(
                yaml_file=yaml_path,
                yaml_file_encoding="utf-8",
            )

        yaml_source = YamlConfigSettingsSource(_YamlBoundSettings)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


def load_safety_trigger_agent_settings(
    config_path: str | Path | None = None,
) -> SafetyTriggerAgentSettings:
    """加载 SafetyTriggerAgent 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 SafetyTriggerAgent 配置。
    """

    if config_path is None:
        return SafetyTriggerAgentSettings()

    class _PathBoundSafetyTriggerAgentSettings(SafetyTriggerAgentSettings):
        """绑定指定 YAML 文件路径的 SafetyTriggerAgent Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundSafetyTriggerAgentSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_SAFETY_TRIGGER_CONFIG_PATH",
    "SafetyTriggerAgentSettings",
    "SafetyTriggerRequirementConfig",
    "SafetyTriggerTimeoutConfig",
    "load_safety_trigger_agent_settings",
)
