##################################################################################################
# 文件: src/veterinary_agent/config/vet_nonmedical_pet_care.py
# 作用: 定义 NonmedicalPetCareAgent 的建议规划、RAG、写作、自检、兜底与超时运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行建议规划、检索或生成。
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

DEFAULT_NONMEDICAL_PET_CARE_CONFIG_PATH = Path("configs/vet_nonmedical_pet_care.yaml")


class _NonmedicalConfigModel(BaseModel):
    """NonmedicalPetCareAgent 严格配置模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls: type[Self], value: Any) -> Any:
        """清理字符串配置值。

        :param value: 原始配置字段值。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class NonmedicalPetCareTimeoutConfig(_NonmedicalConfigModel):
    """NonmedicalPetCareAgent 外部能力调用超时配置。"""

    total_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120.0,
        description="非医疗组件端到端软超时，单位为秒。",
    )
    planner_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="建议维度规划 Agent 调用超时，单位为秒。",
    )
    retrieval_planner_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="知识检索计划 Agent 调用超时，单位为秒。",
    )
    rag_seconds: float = Field(
        default=1.5,
        gt=0,
        le=30.0,
        description="单次 RAG 检索超时，单位为秒。",
    )
    writer_seconds: float = Field(
        default=4.0,
        gt=0,
        le=60.0,
        description="非医疗建议写作 Agent 调用超时，单位为秒。",
    )
    self_check_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="安全实用性自检 Agent 调用超时，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验子调用超时不会超过组件总超时。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当任一子调用超时大于组件总超时时抛出。
        """

        sub_timeouts = [
            self.planner_seconds,
            self.retrieval_planner_seconds,
            self.rag_seconds,
            self.writer_seconds,
            self.self_check_seconds,
        ]
        if any(timeout > self.total_seconds for timeout in sub_timeouts):
            raise ValueError("NonmedicalPetCareAgent 子调用超时不得大于 total_seconds")
        return self


class NonmedicalPetCareRagConfig(_NonmedicalConfigModel):
    """NonmedicalPetCareAgent RAG 策略配置。"""

    enabled: bool = Field(default=True, description="是否允许非医疗组件调用 RAG。")
    required_for_signal: bool = Field(
        default=True,
        description="含 L1/L2 信号时是否要求证据或规则边界。",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="单个检索 facet 最大返回条数。",
    )
    max_facets: int = Field(
        default=5,
        ge=1,
        le=12,
        description="单次非医疗建议最多允许的检索 facet 数。",
    )
    default_collections: list[str] = Field(
        default_factory=lambda: ["pet_care_kb_public_mvp"],
        min_length=1,
        description="默认允许检索的知识集合。",
    )
    rerank_enabled: bool = Field(default=True, description="是否启用 rerank。")


class NonmedicalPetCareRuleConfig(_NonmedicalConfigModel):
    """NonmedicalPetCareAgent 受控规则库配置。"""

    enabled: bool = Field(default=True, description="是否启用受控规则库兜底。")
    rule_library_version: str = Field(
        default="nonmedical-rule-library.v1",
        min_length=1,
        max_length=128,
        description="规则库版本。",
    )
    allow_low_risk_rule_fallback: bool = Field(
        default=True,
        description="低风险任务是否允许仅使用规则保守生成。",
    )


class NonmedicalPetCareAgentSettings(BaseSettings):
    """NonmedicalPetCareAgent 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="NONMEDICAL_PET_CARE_AGENT_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_NONMEDICAL_PET_CARE_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 NonmedicalPetCareAgent。")
    config_version: str = Field(
        default="nonmedical-pet-care-agent-config.v1",
        min_length=1,
        max_length=128,
        description="非医疗组件配置版本。",
    )
    nonmedical_agent_version: str = Field(
        default="nonmedical-pet-care-agent.v1",
        min_length=1,
        max_length=128,
        description="非医疗组件业务版本。",
    )
    planner_version: str = Field(
        default="nonmedical-advice-planner.v1",
        min_length=1,
        max_length=128,
        description="建议维度规划策略版本。",
    )
    retrieval_planner_version: str = Field(
        default="nonmedical-retrieval-planner.v1",
        min_length=1,
        max_length=128,
        description="知识检索计划策略版本。",
    )
    writer_version: str = Field(
        default="nonmedical-advice-writer.v1",
        min_length=1,
        max_length=128,
        description="非医疗建议写作策略版本。",
    )
    self_checker_version: str = Field(
        default="nonmedical-self-checker.v1",
        min_length=1,
        max_length=128,
        description="安全实用性自检策略版本。",
    )
    fallback_template_version: str = Field(
        default="nonmedical-fallback-draft.v1",
        min_length=1,
        max_length=128,
        description="非医疗兜底模板版本。",
    )
    planner_agent_id: str = Field(
        default="nonmedical_advice_dimension_planner",
        min_length=1,
        max_length=128,
        description="建议维度规划 Agent 规格 ID。",
    )
    planner_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="建议维度规划 Agent 规格版本。",
    )
    retrieval_planner_agent_id: str = Field(
        default="nonmedical_knowledge_retrieval_planner",
        min_length=1,
        max_length=128,
        description="知识检索计划 Agent 规格 ID。",
    )
    retrieval_planner_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="知识检索计划 Agent 规格版本。",
    )
    writer_agent_id: str = Field(
        default="nonmedical_advice_writer",
        min_length=1,
        max_length=128,
        description="非医疗建议写作 Agent 规格 ID。",
    )
    writer_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="非医疗建议写作 Agent 规格版本。",
    )
    self_checker_agent_id: str = Field(
        default="nonmedical_safety_practicality_checker",
        min_length=1,
        max_length=128,
        description="安全实用性自检 Agent 规格 ID。",
    )
    self_checker_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="安全实用性自检 Agent 规格版本。",
    )
    allowed_dimensions: list[str] = Field(
        default_factory=lambda: [
            "GOAL_CLARIFICATION",
            "APPLICABILITY_CHECK",
            "STEPWISE_PLAN",
            "GRADUAL_PACE",
            "OBSERVATION_METRICS",
            "RISK_BOUNDARY",
            "ALTERNATIVE_OPTIONS",
            "MISCONCEPTION_WARNING",
            "PROFESSIONAL_ESCALATION",
        ],
        min_length=1,
        description="允许建议规划使用的维度代码。",
    )
    max_draft_chars: int = Field(
        default=5000,
        ge=400,
        le=20000,
        description="非医疗草稿正文最大字符数。",
    )
    timeouts: NonmedicalPetCareTimeoutConfig = Field(
        default_factory=NonmedicalPetCareTimeoutConfig,
        description="非医疗组件外部能力调用超时配置。",
    )
    rag: NonmedicalPetCareRagConfig = Field(
        default_factory=NonmedicalPetCareRagConfig,
        description="非医疗 RAG 策略配置。",
    )
    rules: NonmedicalPetCareRuleConfig = Field(
        default_factory=NonmedicalPetCareRuleConfig,
        description="非医疗受控规则库策略配置。",
    )

    @classmethod
    def settings_customise_sources(
        cls: type[Self],
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """定义 NonmedicalPetCareAgent 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_NONMEDICAL_PET_CARE_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 NonmedicalPetCareAgent YAML 文件的临时 Settings 类型。"""

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


def load_nonmedical_pet_care_agent_settings(
    config_path: str | Path | None = None,
) -> NonmedicalPetCareAgentSettings:
    """加载 NonmedicalPetCareAgent 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 NonmedicalPetCareAgent 配置。
    """

    if config_path is None:
        return NonmedicalPetCareAgentSettings()

    class _PathBoundNonmedicalPetCareAgentSettings(NonmedicalPetCareAgentSettings):
        """绑定指定 YAML 文件路径的 NonmedicalPetCareAgent Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundNonmedicalPetCareAgentSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_NONMEDICAL_PET_CARE_CONFIG_PATH",
    "NonmedicalPetCareAgentSettings",
    "NonmedicalPetCareRagConfig",
    "NonmedicalPetCareRuleConfig",
    "NonmedicalPetCareTimeoutConfig",
    "load_nonmedical_pet_care_agent_settings",
)
