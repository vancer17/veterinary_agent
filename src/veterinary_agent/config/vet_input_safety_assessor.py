##################################################################################################
# 文件: src/veterinary_agent/config/vet_input_safety_assessor.py
# 作用: 定义 VetInputSafetyAssessor 的输入安全评估、弱依赖超时、置信度阈值与降级策略配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行安全评估、不调用模型或写入 trace。
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

DEFAULT_VET_INPUT_SAFETY_ASSESSOR_CONFIG_PATH = Path(
    "configs/vet_input_safety_assessor.yaml"
)


class _VetInputSafetyAssessorConfigModel(BaseModel):
    """VetInputSafetyAssessor 严格配置模型基类。"""

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


class VetInputSafetyAssessorTimeoutConfig(_VetInputSafetyAssessorConfigModel):
    """VetInputSafetyAssessor 弱依赖调用超时配置。"""

    semantic_router_seconds: float = Field(
        default=0.4,
        gt=0,
        le=10.0,
        description="语义路由调用超时，单位为秒。",
    )
    local_extractor_seconds: float = Field(
        default=0.6,
        gt=0,
        le=10.0,
        description="本地结构化抽取调用超时，单位为秒。",
    )
    llm_arbitration_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60.0,
        description="低置信 LLM 仲裁调用超时，单位为秒。",
    )


class VetInputSafetyAssessorConfidenceConfig(_VetInputSafetyAssessorConfigModel):
    """VetInputSafetyAssessor 置信度阈值配置。"""

    min_semantic_score: float = Field(
        default=0.58,
        ge=0.0,
        le=1.0,
        description="语义路由候选可直接采用的最低分数。",
    )
    min_semantic_margin: float = Field(
        default=0.12,
        ge=0.0,
        le=1.0,
        description="语义路由首位候选与次位候选的最低间隔。",
    )
    min_intent_confidence: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="最终意图低置信判定阈值。",
    )


class VetInputSafetyAssessorSettings(BaseSettings):
    """VetInputSafetyAssessor 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="VET_INPUT_SAFETY_ASSESSOR_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_VET_INPUT_SAFETY_ASSESSOR_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用输入安全评估组件。")
    config_version: str = Field(
        default="vet-input-safety-assessor-config.v1",
        min_length=1,
        max_length=128,
        description="输入安全评估配置版本。",
    )
    assessor_version: str = Field(
        default="vet-input-safety-assessor.v1",
        min_length=1,
        max_length=128,
        description="输入安全评估器业务版本。",
    )
    dictionary_version: str = Field(
        default="vet-input-safety-dictionary.v1",
        min_length=1,
        max_length=128,
        description="SAF 与路由词库版本。",
    )
    trace_schema_version: str = Field(
        default="vet.input-safety.trace.v1",
        min_length=1,
        max_length=128,
        description="输入安全 trace 摘要 schema 版本。",
    )
    semantic_router_enabled: bool = Field(
        default=True,
        description="是否启用语义路由候选能力。",
    )
    local_extractor_enabled: bool = Field(
        default=False,
        description="是否启用本地结构化抽取能力。",
    )
    llm_arbitration_enabled: bool = Field(
        default=False,
        description="是否启用低置信 LLM 仲裁。",
    )
    arbitration_agent_id: str = Field(
        default="vet_input_safety_arbitrator",
        min_length=1,
        max_length=128,
        description="低置信仲裁 Agent ID。",
    )
    arbitration_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="低置信仲裁 Agent 版本。",
    )
    timeouts: VetInputSafetyAssessorTimeoutConfig = Field(
        default_factory=VetInputSafetyAssessorTimeoutConfig,
        description="弱依赖调用超时配置。",
    )
    confidence: VetInputSafetyAssessorConfidenceConfig = Field(
        default_factory=VetInputSafetyAssessorConfidenceConfig,
        description="语义路由与意图置信度阈值。",
    )
    max_tasks_per_turn: int = Field(
        default=8,
        ge=1,
        le=32,
        description="单轮最多允许评估的子任务数量。",
    )
    max_task_text_chars: int = Field(
        default=12000,
        ge=1,
        le=100000,
        description="单个子任务文本允许进入评估流程的最大字符数。",
    )
    deterministic_saf01_override_enabled: bool = Field(
        default=True,
        description="SAF-01 命中时是否启用确定性安全覆盖。",
    )
    deterministic_saf03_realtime_override_enabled: bool = Field(
        default=True,
        description="SAF-03 与实况标记组合时是否启用确定性安全覆盖。",
    )
    cold_start_default_executor: str = Field(
        default="standard_consultation",
        min_length=1,
        max_length=128,
        description="冷启动低置信时的默认执行器。",
    )

    @model_validator(mode="after")
    def _validate_strategy_relations(self) -> Self:
        """校验启用策略之间的关系。

        :return: 已通过关系校验的配置对象。
        :raises ValueError: 当配置组合不符合组件降级策略时抛出。
        """

        if self.cold_start_default_executor not in {
            "standard_consultation",
            "nonmedical_pet_care",
            "education",
        }:
            raise ValueError("cold_start_default_executor 只能使用受控执行器")
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
        """定义 VetInputSafetyAssessor 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = (
            cls.yaml_config_path or DEFAULT_VET_INPUT_SAFETY_ASSESSOR_CONFIG_PATH
        )

        class _YamlBoundSettings(settings_cls):
            """绑定当前 VetInputSafetyAssessor YAML 文件的临时 Settings 类型。"""

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


def load_vet_input_safety_assessor_settings(
    config_path: str | Path | None = None,
) -> VetInputSafetyAssessorSettings:
    """加载 VetInputSafetyAssessor 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 VetInputSafetyAssessor 配置。
    """

    if config_path is None:
        return VetInputSafetyAssessorSettings()

    class _PathBoundVetInputSafetyAssessorSettings(VetInputSafetyAssessorSettings):
        """绑定指定 YAML 文件路径的 VetInputSafetyAssessor Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundVetInputSafetyAssessorSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_VET_INPUT_SAFETY_ASSESSOR_CONFIG_PATH",
    "VetInputSafetyAssessorConfidenceConfig",
    "VetInputSafetyAssessorSettings",
    "VetInputSafetyAssessorTimeoutConfig",
    "load_vet_input_safety_assessor_settings",
)
