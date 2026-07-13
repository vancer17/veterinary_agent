##################################################################################################
# 文件: src/veterinary_agent/config/vet_task_decomposer.py
# 作用: 定义 VetTaskDecomposer 的 LLM 拆解、审查修复、本地降级与输出归一化运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行任务拆解、不调用 AgentRunner 或写入 trace。
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

DEFAULT_VET_TASK_DECOMPOSER_CONFIG_PATH = Path("configs/vet_task_decomposer.yaml")


class _VetTaskDecomposerConfigModel(BaseModel):
    """VetTaskDecomposer 严格配置模型基类。"""

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


class VetTaskDecomposerTimeoutConfig(_VetTaskDecomposerConfigModel):
    """VetTaskDecomposer 外部能力调用超时配置。"""

    llm_seconds: float = Field(
        default=4.0,
        gt=0,
        le=60.0,
        description="LLM 主拆解调用超时。",
    )
    review_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="LLM 审查和格式修复调用超时。",
    )
    local_fallback_seconds: float = Field(
        default=0.5,
        gt=0,
        le=10.0,
        description="本地 span fallback 调用超时。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验审查与本地降级超时不会超过主路径超时。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当审查或本地降级超时大于主拆解超时时抛出。
        """

        if self.review_seconds > self.llm_seconds:
            raise ValueError("review_seconds 不得大于 llm_seconds")
        if self.local_fallback_seconds > self.llm_seconds:
            raise ValueError("local_fallback_seconds 不得大于 llm_seconds")
        return self


class VetTaskDecomposerConfidenceConfig(_VetTaskDecomposerConfigModel):
    """VetTaskDecomposer 输出置信度阈值配置。"""

    min_llm_confidence: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="LLM 拆解结果允许进入后续节点的最低置信度。",
    )
    min_local_fallback_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="本地 fallback 候选允许采用的最低置信度。",
    )


class VetTaskDecomposerSettings(BaseSettings):
    """VetTaskDecomposer 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="VET_TASK_DECOMPOSER_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_VET_TASK_DECOMPOSER_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 VetTaskDecomposer。")
    config_version: str = Field(
        default="vet-task-decomposer-config.v1",
        min_length=1,
        max_length=128,
        description="VetTaskDecomposer 配置版本。",
    )
    decomposer_version: str = Field(
        default="vet-task-decomposer.v1",
        min_length=1,
        max_length=128,
        description="拆解器业务版本，写入 trace 摘要。",
    )
    llm_enabled: bool = Field(
        default=True,
        description="是否允许调用 AgentRunner 执行 LLM 结构化拆解。",
    )
    review_repair_enabled: bool = Field(
        default=True,
        description="是否允许对低置信或 schema 异常结果执行有限审查修复。",
    )
    local_fallback_enabled: bool = Field(
        default=True,
        description="LLM 不可用时是否允许调用本地 span fallback 占位能力。",
    )
    decompose_agent_id: str = Field(
        default="vet_task_decomposer",
        min_length=1,
        max_length=128,
        description="主拆解 Agent 规格 ID。",
    )
    decompose_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="主拆解 Agent 规格版本。",
    )
    review_agent_id: str = Field(
        default="vet_task_decomposer_review",
        min_length=1,
        max_length=128,
        description="审查修复 Agent 规格 ID。",
    )
    review_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="审查修复 Agent 规格版本。",
    )
    timeouts: VetTaskDecomposerTimeoutConfig = Field(
        default_factory=VetTaskDecomposerTimeoutConfig,
        description="外部能力调用超时配置。",
    )
    confidence: VetTaskDecomposerConfidenceConfig = Field(
        default_factory=VetTaskDecomposerConfidenceConfig,
        description="输出置信度阈值配置。",
    )
    max_tasks_per_turn: int = Field(
        default=8,
        ge=1,
        le=32,
        description="单轮最多允许输出的子任务数量。",
    )
    max_user_message_chars: int = Field(
        default=12000,
        ge=1,
        le=100000,
        description="允许进入拆解流程的用户原文最大字符数。",
    )

    @model_validator(mode="after")
    def _validate_strategy_relations(self) -> Self:
        """校验启用策略之间不存在无效组合。

        :return: 已通过关系校验的 VetTaskDecomposer 配置。
        :raises ValueError: 当审查修复开启但 LLM 主路径关闭时抛出。
        """

        if self.review_repair_enabled and not self.llm_enabled:
            raise ValueError("review_repair_enabled 开启时必须同时开启 llm_enabled")
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
        """定义 VetTaskDecomposer 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_VET_TASK_DECOMPOSER_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 VetTaskDecomposer YAML 文件的临时 Settings 类型。"""

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


def load_vet_task_decomposer_settings(
    config_path: str | Path | None = None,
) -> VetTaskDecomposerSettings:
    """加载 VetTaskDecomposer 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 VetTaskDecomposer 配置。
    """

    if config_path is None:
        return VetTaskDecomposerSettings()

    class _PathBoundVetTaskDecomposerSettings(VetTaskDecomposerSettings):
        """绑定指定 YAML 文件路径的 VetTaskDecomposer Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundVetTaskDecomposerSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_VET_TASK_DECOMPOSER_CONFIG_PATH",
    "VetTaskDecomposerConfidenceConfig",
    "VetTaskDecomposerSettings",
    "VetTaskDecomposerTimeoutConfig",
    "load_vet_task_decomposer_settings",
)
