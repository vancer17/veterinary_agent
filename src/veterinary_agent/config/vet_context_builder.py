##################################################################################################
# 文件: src/veterinary_agent/config/vet_context_builder.py
# 作用: 定义 VetContextBuilder 的上下文预算、来源超时、P0 字段和裁剪限制配置。
# 边界: 仅负责配置结构、关系校验与 YAML 加载，不读取业务来源、不构建 prompt 或写入 trace。
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

DEFAULT_VET_CONTEXT_BUILDER_CONFIG_PATH = Path("configs/vet_context_builder.yaml")


def _default_p0_fields() -> list[str]:
    """构建 VetContextBuilder 默认 P0 字段列表。

    :return: 包含物种、年龄、体重、性别和绝育状态的独立字段列表。
    """

    return [
        "species",
        "birth_date",
        "age",
        "weight_kg",
        "sex",
        "neutered",
    ]


class _VetContextConfigModel(BaseModel):
    """VetContextBuilder 严格配置模型基类。"""

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


class VetContextBudgetConfig(_VetContextConfigModel):
    """不同上下文压缩策略的 token 预算。"""

    single_full_tokens: int = Field(
        default=8192,
        ge=1024,
        le=131072,
        description="standard 单宠完整上下文 token 预算。",
    )
    safety_minimal_tokens: int = Field(
        default=4096,
        ge=512,
        le=32768,
        description="safety_trigger 最小上下文 token 预算。",
    )
    education_light_tokens: int = Field(
        default=4096,
        ge=512,
        le=65536,
        description="education 与轻量非医疗上下文 token 预算。",
    )


class VetContextTimeoutConfig(_VetContextConfigModel):
    """VetContextBuilder 总超时与单来源超时。"""

    total_seconds: float = Field(
        default=2.5,
        gt=0,
        le=30.0,
        description="普通上下文构建总超时。",
    )
    source_seconds: float = Field(
        default=0.8,
        gt=0,
        le=10.0,
        description="普通上下文单来源读取超时。",
    )
    safety_total_seconds: float = Field(
        default=0.3,
        gt=0,
        le=5.0,
        description="安全最小上下文构建总超时。",
    )
    safety_source_seconds: float = Field(
        default=0.12,
        gt=0,
        le=2.0,
        description="安全最小上下文单来源读取超时。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验总超时与单来源超时之间的关系。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当单来源超时大于对应总超时或安全总超时大于普通总超时时抛出。
        """

        if self.source_seconds > self.total_seconds:
            raise ValueError("source_seconds 不得大于 total_seconds")
        if self.safety_total_seconds > self.total_seconds:
            raise ValueError("safety_total_seconds 不得大于 total_seconds")
        if self.safety_source_seconds > self.safety_total_seconds:
            raise ValueError("safety_source_seconds 不得大于 safety_total_seconds")
        return self


class VetContextBuilderSettings(BaseSettings):
    """VetContextBuilder 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="VET_CONTEXT_BUILDER_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_VET_CONTEXT_BUILDER_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 VetContextBuilder。")
    config_version: str = Field(
        default="vet-context-builder-config.v1",
        min_length=1,
        max_length=128,
        description="VetContextBuilder 配置版本。",
    )
    budgets: VetContextBudgetConfig = Field(
        default_factory=VetContextBudgetConfig,
        description="按压缩策略划分的 token 预算。",
    )
    timeouts: VetContextTimeoutConfig = Field(
        default_factory=VetContextTimeoutConfig,
        description="上下文构建与来源读取超时。",
    )
    p0_fields: list[str] = Field(
        default_factory=_default_p0_fields,
        min_length=1,
        max_length=32,
        description="裁剪后必须重新检查并保留的宠物基础事实字段。",
    )
    recent_message_limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="单次读取近期消息的数量上限。",
    )
    recent_message_token_budget: int = Field(
        default=1536,
        ge=128,
        le=16384,
        description="recent_messages 块内部消息裁剪预算。",
    )
    max_prompt_blocks: int = Field(
        default=16,
        ge=4,
        le=64,
        description="单个上下文 bundle 允许输出的最大块数。",
    )
    max_task_input_chars: int = Field(
        default=4000,
        ge=256,
        le=32768,
        description="任务输入块允许保留的最大字符数。",
    )
    chars_per_token: float = Field(
        default=2.0,
        ge=1.0,
        le=8.0,
        description="近似 token 计数使用的平均字符数。",
    )
    budget_headroom_ratio: float = Field(
        default=0.1,
        ge=0.0,
        le=0.5,
        description="为模板、RAG 与协议开销保留的预算比例。",
    )

    @field_validator("p0_fields")
    @classmethod
    def _normalize_p0_fields(cls, values: list[str]) -> list[str]:
        """规范化 P0 字段列表并强制保留物种字段。

        :param values: 原始 P0 字段列表。
        :return: 去除空值、去重且保持顺序的 P0 字段列表。
        :raises ValueError: 当列表包含空字段或缺少 species 时抛出。
        """

        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("p0_fields 不得包含空字段")
        deduplicated = list(dict.fromkeys(normalized))
        if "species" not in deduplicated:
            raise ValueError("p0_fields 必须包含 species")
        return deduplicated

    @model_validator(mode="after")
    def _validate_budget_relations(self) -> Self:
        """校验消息子预算不会超过任一整体上下文预算。

        :return: 已通过关系校验的 VetContextBuilder 配置。
        :raises ValueError: 当近期消息预算大于任一整体预算时抛出。
        """

        minimum_budget = min(
            self.budgets.single_full_tokens,
            self.budgets.safety_minimal_tokens,
            self.budgets.education_light_tokens,
        )
        if self.recent_message_token_budget > minimum_budget:
            raise ValueError("recent_message_token_budget 不得大于整体上下文预算")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定义 VetContextBuilder 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_VET_CONTEXT_BUILDER_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 VetContextBuilder YAML 文件的临时 Settings 类型。"""

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


def load_vet_context_builder_settings(
    config_path: str | Path | None = None,
) -> VetContextBuilderSettings:
    """加载 VetContextBuilder 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 VetContextBuilder 配置。
    """

    if config_path is None:
        return VetContextBuilderSettings()

    class _PathBoundVetContextBuilderSettings(VetContextBuilderSettings):
        """绑定指定 YAML 文件路径的 VetContextBuilder Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundVetContextBuilderSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_VET_CONTEXT_BUILDER_CONFIG_PATH",
    "VetContextBudgetConfig",
    "VetContextBuilderSettings",
    "VetContextTimeoutConfig",
    "load_vet_context_builder_settings",
)
