##################################################################################################
# 文件: src/veterinary_agent/config/checkpoint_store.py
# 作用: 定义 CheckpointStore 组件 RuntimeConfig 配置模型，并通过 Pydantic Settings 加载 YAML/env。
# 边界: 仅描述 CheckpointStore 当前已接入的运行参数；不承载数据库访问、LangGraph 适配或业务编排逻辑。
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

DEFAULT_CHECKPOINT_STORE_CONFIG_PATH = Path("configs/checkpoint_store.yaml")


class _StrictConfigModel(BaseModel):
    """CheckpointStore 配置模型基类。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

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


class CheckpointStoreSchemaConfig(_StrictConfigModel):
    """checkpoint 状态 schema 兼容配置。"""

    supported_state_schema_versions: list[str] = Field(
        default_factory=lambda: ["checkpoint.v1"],
        min_length=1,
        description="当前代码允许读取的 checkpoint 状态 schema 版本列表。",
    )

    @field_validator("supported_state_schema_versions")
    @classmethod
    def _validate_supported_state_schema_versions(
        cls,
        values: list[str],
    ) -> list[str]:
        """校验并规范化支持的 checkpoint 状态 schema 版本列表。

        :param values: YAML 或环境变量传入的 schema 版本列表。
        :return: 去重且保序的 schema 版本列表。
        :raises ValueError: 当列表为空、包含空字符串或重复版本时抛出。
        """

        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("supported_state_schema_versions 不得包含空字符串")
        deduplicated_values = list(dict.fromkeys(normalized_values))
        if len(deduplicated_values) != len(normalized_values):
            raise ValueError("supported_state_schema_versions 不得包含重复版本")
        return deduplicated_values


class CheckpointStoreRunLockConfig(_StrictConfigModel):
    """运行锁 RuntimeConfig。"""

    min_ttl_seconds: float = Field(
        default=1.0,
        gt=0,
        description="运行锁 TTL 允许的最小秒数。",
    )
    max_ttl_seconds: float = Field(
        default=900.0,
        gt=0,
        description="运行锁 TTL 允许的最大秒数。",
    )

    @model_validator(mode="after")
    def _validate_ttl_range(self) -> Self:
        """校验运行锁 TTL 上下界关系。

        :return: 通过校验后的当前配置对象。
        :raises ValueError: 当最小 TTL 大于最大 TTL 时抛出。
        """

        if self.min_ttl_seconds > self.max_ttl_seconds:
            raise ValueError("min_ttl_seconds 不得大于 max_ttl_seconds")
        return self


class CheckpointStoreHistoryConfig(_StrictConfigModel):
    """checkpoint 历史查询 RuntimeConfig。"""

    max_list_limit: int = Field(
        default=100,
        ge=1,
        le=100,
        description="ListCheckpoints 单次查询允许的最大返回条数。",
    )


class CheckpointStoreCheckpointConfig(_StrictConfigModel):
    """checkpoint 状态体 RuntimeConfig。"""

    max_state_bytes: int = Field(
        default=262_144,
        ge=1,
        description="单次 SaveCheckpoint 状态体允许的最大 UTF-8 JSON 字节数。",
    )


class CheckpointStoreSegmentPublishConfig(_StrictConfigModel):
    """segment 发布幂等 RuntimeConfig。"""

    max_metadata_bytes: int = Field(
        default=16_384,
        ge=1,
        description="MarkSegmentPublished metadata 允许的最大 UTF-8 JSON 字节数。",
    )


class CheckpointStoreSettings(BaseSettings):
    """CheckpointStore 组件 RuntimeConfig 根配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="CHECKPOINT_STORE_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_CHECKPOINT_STORE_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    operation_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="单次 CheckpointStore 对外操作允许的最大耗时，单位为秒。",
    )
    state_schema: CheckpointStoreSchemaConfig = Field(
        default_factory=CheckpointStoreSchemaConfig,
        description="checkpoint 状态 schema 兼容配置。",
    )
    run_lock: CheckpointStoreRunLockConfig = Field(
        default_factory=CheckpointStoreRunLockConfig,
        description="运行锁 RuntimeConfig。",
    )
    history: CheckpointStoreHistoryConfig = Field(
        default_factory=CheckpointStoreHistoryConfig,
        description="checkpoint 历史查询 RuntimeConfig。",
    )
    checkpoint: CheckpointStoreCheckpointConfig = Field(
        default_factory=CheckpointStoreCheckpointConfig,
        description="checkpoint 状态体 RuntimeConfig。",
    )
    segment_publish: CheckpointStoreSegmentPublishConfig = Field(
        default_factory=CheckpointStoreSegmentPublishConfig,
        description="segment 发布幂等 RuntimeConfig。",
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


def load_checkpoint_store_settings(
    config_path: str | Path | None = None,
) -> CheckpointStoreSettings:
    """加载 CheckpointStore 组件 RuntimeConfig。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用模型默认路径。
    :return: 已完成校验的 CheckpointStore 组件 RuntimeConfig。
    """

    if config_path is None:
        return CheckpointStoreSettings()

    class _PathBoundCheckpointStoreSettings(CheckpointStoreSettings):
        """绑定指定 YAML 文件路径的 CheckpointStore RuntimeConfig 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundCheckpointStoreSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_CHECKPOINT_STORE_CONFIG_PATH",
    "CheckpointStoreHistoryConfig",
    "CheckpointStoreCheckpointConfig",
    "CheckpointStoreRunLockConfig",
    "CheckpointStoreSchemaConfig",
    "CheckpointStoreSegmentPublishConfig",
    "CheckpointStoreSettings",
    "load_checkpoint_store_settings",
)
