##################################################################################################
# 文件: src/veterinary_agent/config/vet_education.py
# 作用: 定义 EducationAgent 的解释规划、RAG、写作、自检、兜底与超时运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行科普规划、检索或生成。
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

DEFAULT_EDUCATION_AGENT_CONFIG_PATH = Path("configs/vet_education.yaml")


class _EducationConfigModel(BaseModel):
    """EducationAgent 严格配置模型基类。"""

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


class EducationTimeoutConfig(_EducationConfigModel):
    """EducationAgent 外部能力调用超时配置。"""

    total_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120.0,
        description="科普组件端到端软超时，单位为秒。",
    )
    planner_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="解释规划 Agent 调用超时，单位为秒。",
    )
    retrieval_planner_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="RAG 检索计划 Agent 调用超时，单位为秒。",
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
        description="科普写作 Agent 调用超时，单位为秒。",
    )
    grounding_seconds: float = Field(
        default=2.0,
        gt=0,
        le=30.0,
        description="接地性自检 Agent 调用超时，单位为秒。",
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
            self.grounding_seconds,
        ]
        if any(timeout > self.total_seconds for timeout in sub_timeouts):
            raise ValueError("EducationAgent 子调用超时不得大于 total_seconds")
        return self


class EducationRagConfig(_EducationConfigModel):
    """EducationAgent RAG 策略配置。"""

    enabled: bool = Field(default=True, description="是否允许科普组件调用 RAG。")
    required_for_medical: bool = Field(
        default=True,
        description="医学科普是否要求可用 RAG 证据。",
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
        description="单次科普最多允许的检索 facet 数。",
    )
    default_collections: list[str] = Field(
        default_factory=lambda: ["vet_kb_public_mvp"],
        min_length=1,
        description="默认允许检索的知识集合。",
    )
    rerank_enabled: bool = Field(default=True, description="是否启用 rerank。")
    dosage_filter_required: bool = Field(
        default=True,
        description="是否要求药物剂量风险过滤。",
    )
    ref_range_generation_forbidden: bool = Field(
        default=True,
        description="是否禁止从 RAG 生成参考区间。",
    )


class EducationAgentSettings(BaseSettings):
    """EducationAgent 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="EDUCATION_AGENT_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_EDUCATION_AGENT_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(default=True, description="是否启用 EducationAgent。")
    config_version: str = Field(
        default="education-agent-config.v1",
        min_length=1,
        max_length=128,
        description="科普组件配置版本。",
    )
    education_agent_version: str = Field(
        default="education-agent.v1",
        min_length=1,
        max_length=128,
        description="科普组件业务版本。",
    )
    planner_version: str = Field(
        default="education-planner.v1",
        min_length=1,
        max_length=128,
        description="解释维度规划策略版本。",
    )
    retrieval_planner_version: str = Field(
        default="education-retrieval-planner.v1",
        min_length=1,
        max_length=128,
        description="科普检索计划策略版本。",
    )
    writer_version: str = Field(
        default="education-writer.v1",
        min_length=1,
        max_length=128,
        description="科普写作策略版本。",
    )
    grounding_checker_version: str = Field(
        default="education-grounding-checker.v1",
        min_length=1,
        max_length=128,
        description="科普接地性自检策略版本。",
    )
    fallback_template_version: str = Field(
        default="education-fallback-draft.v1",
        min_length=1,
        max_length=128,
        description="科普兜底模板版本。",
    )
    planner_agent_id: str = Field(
        default="education_explanation_planner",
        min_length=1,
        max_length=128,
        description="解释维度规划 Agent 规格 ID。",
    )
    planner_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="解释维度规划 Agent 规格版本。",
    )
    retrieval_planner_agent_id: str = Field(
        default="education_rag_query_planner",
        min_length=1,
        max_length=128,
        description="RAG 检索计划 Agent 规格 ID。",
    )
    retrieval_planner_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="RAG 检索计划 Agent 规格版本。",
    )
    writer_agent_id: str = Field(
        default="education_writer",
        min_length=1,
        max_length=128,
        description="科普写作 Agent 规格 ID。",
    )
    writer_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="科普写作 Agent 规格版本。",
    )
    grounding_checker_agent_id: str = Field(
        default="education_grounding_checker",
        min_length=1,
        max_length=128,
        description="接地性自检 Agent 规格 ID。",
    )
    grounding_checker_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="接地性自检 Agent 规格版本。",
    )
    allowed_dimensions: list[str] = Field(
        default_factory=lambda: [
            "DEFINITION",
            "MECHANISM",
            "COMMON_DIRECTIONS",
            "RED_FLAGS",
            "DIAGNOSTIC_LIMITS",
            "CHECKUP_PRINCIPLES",
            "CARE_PRINCIPLES",
            "MEDICATION_BOUNDARY",
            "PREVENTION_MANAGEMENT",
            "MISCONCEPTION_CLARIFICATION",
            "COMPARISON",
        ],
        min_length=1,
        description="允许解释规划使用的维度代码。",
    )
    max_draft_chars: int = Field(
        default=5000,
        ge=400,
        le=20000,
        description="科普草稿正文最大字符数。",
    )
    timeouts: EducationTimeoutConfig = Field(
        default_factory=EducationTimeoutConfig,
        description="科普组件外部能力调用超时配置。",
    )
    rag: EducationRagConfig = Field(
        default_factory=EducationRagConfig,
        description="科普 RAG 策略配置。",
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
        """定义 EducationAgent 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_EDUCATION_AGENT_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 EducationAgent YAML 文件的临时 Settings 类型。"""

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


def load_education_agent_settings(
    config_path: str | Path | None = None,
) -> EducationAgentSettings:
    """加载 EducationAgent 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 EducationAgent 配置。
    """

    if config_path is None:
        return EducationAgentSettings()

    class _PathBoundEducationAgentSettings(EducationAgentSettings):
        """绑定指定 YAML 文件路径的 EducationAgent Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundEducationAgentSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_EDUCATION_AGENT_CONFIG_PATH",
    "EducationAgentSettings",
    "EducationRagConfig",
    "EducationTimeoutConfig",
    "load_education_agent_settings",
)
