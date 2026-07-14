##################################################################################################
# 文件: src/veterinary_agent/config/vet_response_composer.py
# 作用: 定义 VetResponseComposer 的发布排序、大小限制、流式切片与 trace 运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载，不执行回复合成或存储写入。
##################################################################################################

from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_VET_RESPONSE_COMPOSER_CONFIG_PATH = Path("configs/vet_response_composer.yaml")


class _VetResponseComposerConfigModel(BaseModel):
    """VetResponseComposer 严格配置模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any, info: ValidationInfo) -> Any:
        """清理字符串配置值。

        :param value: 原始配置字段值。
        :param info: 当前字段校验上下文。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if info.field_name == "final_response_separator":
            return value
        if isinstance(value, str):
            return value.strip()
        return value


class VetResponseComposerTimeoutConfig(_VetResponseComposerConfigModel):
    """VetResponseComposer 发布链路超时配置。"""

    total_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120.0,
        description="回复合成与发布端到端软超时，单位为秒。",
    )
    conversation_store_seconds: float = Field(
        default=3.0,
        gt=0,
        le=60.0,
        description="单次 ConversationStore 写入软超时，单位为秒。",
    )
    checkpoint_store_seconds: float = Field(
        default=3.0,
        gt=0,
        le=60.0,
        description="单次 CheckpointStore 写入软超时，单位为秒。",
    )
    trace_store_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60.0,
        description="单次 trace 写入软超时，单位为秒。",
    )
    safety_first_wait_seconds: float = Field(
        default=2.0,
        ge=0,
        le=30.0,
        description="急症首发锁等待上游急症段或急症 fallback 的软上限。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验子操作超时不会超过组件总超时。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当任一子操作超时大于组件总超时时抛出。
        """

        child_timeouts = (
            self.conversation_store_seconds,
            self.checkpoint_store_seconds,
            self.trace_store_seconds,
            self.safety_first_wait_seconds,
        )
        if any(timeout > self.total_seconds for timeout in child_timeouts):
            raise ValueError("VetResponseComposer 子操作超时不得大于 total_seconds")
        return self


class VetResponseComposerPublishConfig(_VetResponseComposerConfigModel):
    """VetResponseComposer 分段发布配置。"""

    max_segments_per_turn: int = Field(
        default=8,
        ge=1,
        le=32,
        description="单轮最多允许发布的 segment 数量。",
    )
    max_segment_chars: int = Field(
        default=16384,
        ge=1,
        le=131072,
        description="单个 segment 用户可见正文最大字符数。",
    )
    stream_delta_chars: int = Field(
        default=512,
        ge=1,
        le=4096,
        description="段级流式发布时单个 delta 的最大字符数。",
    )
    final_response_separator: str = Field(
        default="\n\n",
        min_length=1,
        max_length=16,
        description="同步响应中多个 segment 拼接为最终正文时使用的分隔符。",
    )
    create_assistant_message: bool = Field(
        default=True,
        description="是否由 Composer 创建并完成助手消息容器。",
    )


class VetResponseComposerOrderingConfig(_VetResponseComposerConfigModel):
    """VetResponseComposer 业务排序配置。"""

    safety_priority: int = Field(
        default=0,
        ge=0,
        le=1000,
        description="急症 segment 排序优先级，数值越小越靠前。",
    )
    medical_priority: int = Field(
        default=10,
        ge=0,
        le=1000,
        description="普通医疗咨询 segment 排序优先级。",
    )
    ocr_priority: int = Field(
        default=20,
        ge=0,
        le=1000,
        description="独立 OCR 或病历解读 segment 排序优先级。",
    )
    education_priority: int = Field(
        default=30,
        ge=0,
        le=1000,
        description="科普 segment 排序优先级。",
    )
    nonmedical_priority: int = Field(
        default=40,
        ge=0,
        le=1000,
        description="非医疗养宠 segment 排序优先级。",
    )
    default_priority: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="未知 segment 类型的默认排序优先级。",
    )


class VetResponseComposerSettings(BaseSettings):
    """VetResponseComposer 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="VET_RESPONSE_COMPOSER_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_VET_RESPONSE_COMPOSER_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 VetResponseComposer。")
    config_version: str = Field(
        default="vet-response-composer-config.v1",
        min_length=1,
        max_length=128,
        description="VetResponseComposer 配置版本。",
    )
    composer_version: str = Field(
        default="vet-response-composer.v1",
        min_length=1,
        max_length=128,
        description="VetResponseComposer 业务版本。",
    )
    trace_schema_version: str = Field(
        default="vet.response-composer.trace.v1",
        min_length=1,
        max_length=128,
        description="Composer trace patch schema 引用。",
    )
    capture_policy_version: str = Field(
        default="vet-trace-capture-policy.v1",
        min_length=1,
        max_length=128,
        description="Composer trace 使用的 capture policy 版本。",
    )
    audit_tier_order: list[str] = Field(
        default_factory=lambda: ["C", "B", "A"],
        min_length=1,
        description="整轮 audit_tier 聚合顺序，越靠后表示风险等级越高。",
    )
    timeouts: VetResponseComposerTimeoutConfig = Field(
        default_factory=VetResponseComposerTimeoutConfig,
        description="发布链路超时配置。",
    )
    publish: VetResponseComposerPublishConfig = Field(
        default_factory=VetResponseComposerPublishConfig,
        description="分段发布配置。",
    )
    ordering: VetResponseComposerOrderingConfig = Field(
        default_factory=VetResponseComposerOrderingConfig,
        description="业务分段排序配置。",
    )

    @model_validator(mode="after")
    def _validate_audit_tier_order(self) -> Self:
        """校验 audit_tier 聚合顺序覆盖 A/B/C。

        :return: 已通过关系校验的 Composer 配置。
        :raises ValueError: 当 audit_tier_order 未完整覆盖 A/B/C 时抛出。
        """

        normalized_tiers = {tier.upper() for tier in self.audit_tier_order}
        if not {"A", "B", "C"}.issubset(normalized_tiers):
            raise ValueError("audit_tier_order 必须至少覆盖 A、B、C")
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
        """定义 VetResponseComposer 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_VET_RESPONSE_COMPOSER_CONFIG_PATH
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path),
            file_secret_settings,
        )


def load_vet_response_composer_settings(
    config_path: Path | None = None,
) -> VetResponseComposerSettings:
    """加载 VetResponseComposer 配置。

    :param config_path: 可选 YAML 配置路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 VetResponseComposer 配置。
    """

    if config_path is None:
        return VetResponseComposerSettings()

    class _PathBoundVetResponseComposerSettings(VetResponseComposerSettings):
        """绑定指定 YAML 文件路径的 VetResponseComposer Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = config_path

    return _PathBoundVetResponseComposerSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_VET_RESPONSE_COMPOSER_CONFIG_PATH",
    "VetResponseComposerOrderingConfig",
    "VetResponseComposerPublishConfig",
    "VetResponseComposerSettings",
    "VetResponseComposerTimeoutConfig",
    "load_vet_response_composer_settings",
)
