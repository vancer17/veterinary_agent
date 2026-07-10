##################################################################################################
# 文件: src/veterinary_agent/config/conversation_store.py
# 作用: 定义 ConversationStore 组件 RuntimeConfig 配置模型，并通过 Pydantic Settings 加载 YAML/env。
# 边界: 仅描述 ConversationStore 当前已接入的运行参数；不承载数据库访问、业务策略或 Agent 编排逻辑。
##################################################################################################

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

DEFAULT_CONVERSATION_STORE_CONFIG_PATH = Path("configs/conversation_store.yaml")


class _StrictConfigModel(BaseModel):
    """ConversationStore 配置模型基类。"""

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


class ConversationStoreMessageConfig(_StrictConfigModel):
    """ConversationStore 消息写入 RuntimeConfig。"""

    max_message_bytes: int = Field(
        default=65_536,
        ge=1,
        description="单条 message 最终正文允许的最大 UTF-8 字节数。",
    )
    max_segment_bytes: int = Field(
        default=16_384,
        ge=1,
        description="单条 assistant segment 正文允许的最大 UTF-8 字节数。",
    )
    max_metadata_bytes: int = Field(
        default=16_384,
        ge=1,
        description="session、message、segment 或附件引用 metadata 允许的最大 UTF-8 JSON 字节数。",
    )
    max_attachment_refs_per_message: int = Field(
        default=20,
        ge=0,
        le=100,
        description="单条 message 允许保存的最大附件引用数量。",
    )


class ConversationStoreHistoryConfig(_StrictConfigModel):
    """ConversationStore 消息读取 RuntimeConfig。"""

    max_list_limit: int = Field(
        default=100,
        ge=1,
        le=200,
        description="ListMessagesBySession 单次查询允许的最大返回条数。",
    )
    max_recent_messages: int = Field(
        default=50,
        ge=1,
        le=100,
        description="GetRecentMessages 单次查询允许的最大返回条数。",
    )


class ConversationStoreSettings(BaseSettings):
    """ConversationStore 组件 RuntimeConfig 根配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="CONVERSATION_STORE_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_CONVERSATION_STORE_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(
        default=True,
        description="ConversationStore 组件是否启用。",
    )
    operation_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="单次 ConversationStore 对外操作允许的最大耗时，单位为秒。",
    )
    message: ConversationStoreMessageConfig = Field(
        default_factory=ConversationStoreMessageConfig,
        description="消息写入与 metadata 限制配置。",
    )
    history: ConversationStoreHistoryConfig = Field(
        default_factory=ConversationStoreHistoryConfig,
        description="消息读取与分页限制配置。",
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


def load_conversation_store_settings(
    config_path: str | Path | None = None,
) -> ConversationStoreSettings:
    """加载 ConversationStore 组件 RuntimeConfig。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用模型默认路径。
    :return: 已完成校验的 ConversationStore 组件 RuntimeConfig。
    """

    if config_path is None:
        return ConversationStoreSettings()

    class _PathBoundConversationStoreSettings(ConversationStoreSettings):
        """绑定指定 YAML 文件路径的 ConversationStore RuntimeConfig 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundConversationStoreSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_CONVERSATION_STORE_CONFIG_PATH",
    "ConversationStoreHistoryConfig",
    "ConversationStoreMessageConfig",
    "ConversationStoreSettings",
    "load_conversation_store_settings",
)
