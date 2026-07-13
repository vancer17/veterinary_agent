##################################################################################################
# 文件: src/veterinary_agent/config/vet_standard_consultation.py
# 作用: 定义 StandardConsultationAgent 的子 Agent、RAG、层级推进、问题预算与超时运行配置。
# 边界: 仅负责配置结构、默认值、关系校验与 YAML 加载；不执行问诊生成、不调用模型或 RAG。
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

DEFAULT_STANDARD_CONSULTATION_CONFIG_PATH = Path(
    "configs/vet_standard_consultation.yaml"
)


class _StandardConsultationConfigModel(BaseModel):
    """StandardConsultationAgent 严格配置模型基类。"""

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


class StandardConsultationTimeoutConfig(_StandardConsultationConfigModel):
    """StandardConsultationAgent 外部能力调用超时配置。"""

    total_seconds: float = Field(
        default=12.0,
        gt=0,
        le=120.0,
        description="标准问诊组件端到端软超时，单位为秒。",
    )
    sub_agent_seconds: float = Field(
        default=4.0,
        gt=0,
        le=60.0,
        description="单个内部子 Agent 调用超时，单位为秒。",
    )
    rag_seconds: float = Field(
        default=1.5,
        gt=0,
        le=30.0,
        description="单次阶段式 RAG 检索超时，单位为秒。",
    )

    @model_validator(mode="after")
    def _validate_timeout_relations(self) -> Self:
        """校验子调用超时不会超过组件总超时。

        :return: 已通过关系校验的超时配置。
        :raises ValueError: 当子 Agent 或 RAG 超时大于组件总超时时抛出。
        """

        if self.sub_agent_seconds > self.total_seconds:
            raise ValueError("sub_agent_seconds 不得大于 total_seconds")
        if self.rag_seconds > self.total_seconds:
            raise ValueError("rag_seconds 不得大于 total_seconds")
        return self


class StandardConsultationQuestionBudgetConfig(_StandardConsultationConfigModel):
    """StandardConsultationAgent 每轮追问预算配置。"""

    default_max_questions: int = Field(
        default=3,
        ge=1,
        le=3,
        description="请求未显式传入预算时每轮最多选择的问题数。",
    )
    absolute_max_questions: int = Field(
        default=3,
        ge=1,
        le=3,
        description="组件硬性允许的每轮问题数上限。",
    )

    @model_validator(mode="after")
    def _validate_question_budget(self) -> Self:
        """校验默认问题预算不超过硬上限。

        :return: 已通过关系校验的问题预算配置。
        :raises ValueError: 当默认预算超过硬上限时抛出。
        """

        if self.default_max_questions > self.absolute_max_questions:
            raise ValueError("default_max_questions 不得大于 absolute_max_questions")
        return self


class StandardConsultationReadinessConfig(_StandardConsultationConfigModel):
    """StandardConsultationAgent 层级推进 readiness 阈值配置。"""

    direction_known_slot_ratio: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="允许进入 L2 方向提示的已知槽位比例阈值。",
    )
    differential_known_slot_ratio: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="允许进入 L3 鉴别方向的已知槽位比例阈值。",
    )
    care_known_slot_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="允许进入 L4 护理建议的已知槽位比例阈值。",
    )
    contraindication_completeness_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="允许进入 L4 个案护理前的禁忌信息完整度阈值。",
    )

    @model_validator(mode="after")
    def _validate_readiness_order(self) -> Self:
        """校验层级推进阈值按 L2 到 L4 递增。

        :return: 已通过关系校验的 readiness 配置。
        :raises ValueError: 当层级阈值顺序非法时抛出。
        """

        ordered = [
            self.direction_known_slot_ratio,
            self.differential_known_slot_ratio,
            self.care_known_slot_ratio,
        ]
        if ordered != sorted(ordered):
            raise ValueError("readiness 槽位比例阈值必须按 L2/L3/L4 递增")
        return self


class StandardConsultationRagConfig(_StandardConsultationConfigModel):
    """StandardConsultationAgent 阶段式 RAG 策略配置。"""

    enabled: bool = Field(default=True, description="是否允许标准问诊请求 RAG。")
    presearch_enabled: bool = Field(
        default=True,
        description="是否在问诊开始阶段执行前置检索。",
    )
    required_for_l3: bool = Field(
        default=True,
        description="L3 鉴别方向是否要求可用 RAG 证据。",
    )
    required_for_l4: bool = Field(
        default=True,
        description="L4 护理建议是否要求可用 RAG 证据。",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="单次 RAG 检索最大返回条数。",
    )


class StandardConsultationAgentSettings(BaseSettings):
    """StandardConsultationAgent 应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="STANDARD_CONSULTATION_",
        extra="forbid",
        validate_assignment=True,
        yaml_file=DEFAULT_STANDARD_CONSULTATION_CONFIG_PATH,
        yaml_file_encoding="utf-8",
    )

    yaml_config_path: ClassVar[Path | None] = None

    enabled: bool = Field(
        default=True, description="是否启用 StandardConsultationAgent。"
    )
    config_version: str = Field(
        default="standard-consultation-config.v1",
        min_length=1,
        max_length=128,
        description="标准问诊配置版本。",
    )
    standard_agent_version: str = Field(
        default="standard-consultation-agent.v1",
        min_length=1,
        max_length=128,
        description="标准问诊 Agent 业务版本。",
    )
    orchestrator_version: str = Field(
        default="standard-consultation-orchestrator.v1",
        min_length=1,
        max_length=128,
        description="标准问诊中控版本。",
    )
    question_collector_agent_id: str = Field(
        default="standard_question_collector",
        min_length=1,
        max_length=128,
        description="问诊采集子 Agent 规格 ID。",
    )
    question_collector_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="问诊采集子 Agent 规格版本。",
    )
    triage_agent_id: str = Field(
        default="standard_triage_urgency",
        min_length=1,
        max_length=128,
        description="分诊紧急度子 Agent 规格 ID。",
    )
    triage_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="分诊紧急度子 Agent 规格版本。",
    )
    direction_agent_id: str = Field(
        default="standard_direction_hint",
        min_length=1,
        max_length=128,
        description="方向提示子 Agent 规格 ID。",
    )
    direction_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="方向提示子 Agent 规格版本。",
    )
    differential_agent_id: str = Field(
        default="standard_differential_diagnosis",
        min_length=1,
        max_length=128,
        description="鉴别方向子 Agent 规格 ID。",
    )
    differential_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="鉴别方向子 Agent 规格版本。",
    )
    care_agent_id: str = Field(
        default="standard_care_plan",
        min_length=1,
        max_length=128,
        description="护理处置子 Agent 规格 ID。",
    )
    care_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="护理处置子 Agent 规格版本。",
    )
    synthesizer_agent_id: str = Field(
        default="standard_draft_synthesizer",
        min_length=1,
        max_length=128,
        description="标准问诊草稿合成 Agent 规格 ID。",
    )
    synthesizer_agent_version: str = Field(
        default="v1",
        min_length=1,
        max_length=128,
        description="标准问诊草稿合成 Agent 规格版本。",
    )
    timeouts: StandardConsultationTimeoutConfig = Field(
        default_factory=StandardConsultationTimeoutConfig,
        description="标准问诊外部能力调用超时配置。",
    )
    question_budget: StandardConsultationQuestionBudgetConfig = Field(
        default_factory=StandardConsultationQuestionBudgetConfig,
        description="每轮追问预算配置。",
    )
    readiness: StandardConsultationReadinessConfig = Field(
        default_factory=StandardConsultationReadinessConfig,
        description="层级推进 readiness 阈值配置。",
    )
    rag: StandardConsultationRagConfig = Field(
        default_factory=StandardConsultationRagConfig,
        description="阶段式 RAG 策略配置。",
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
        """定义 StandardConsultationAgent 配置源优先级。

        :param settings_cls: 当前 Settings 类型。
        :param init_settings: 初始化参数配置源。
        :param env_settings: 环境变量配置源。
        :param dotenv_settings: dotenv 文件配置源。
        :param file_secret_settings: 文件密钥配置源。
        :return: 按覆盖优先级排列的配置源元组。
        """

        yaml_path = cls.yaml_config_path or DEFAULT_STANDARD_CONSULTATION_CONFIG_PATH

        class _YamlBoundSettings(settings_cls):
            """绑定当前 StandardConsultationAgent YAML 文件的临时 Settings 类型。"""

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


def load_standard_consultation_agent_settings(
    config_path: str | Path | None = None,
) -> StandardConsultationAgentSettings:
    """加载 StandardConsultationAgent 配置。

    :param config_path: 可选 YAML 配置文件路径；未传入时使用默认路径。
    :return: 已完成 Pydantic 校验的 StandardConsultationAgent 配置。
    """

    if config_path is None:
        return StandardConsultationAgentSettings()

    class _PathBoundStandardConsultationAgentSettings(
        StandardConsultationAgentSettings
    ):
        """绑定指定 YAML 文件路径的 StandardConsultationAgent Settings 类型。"""

        yaml_config_path: ClassVar[Path | None] = Path(config_path)

    return _PathBoundStandardConsultationAgentSettings()


__all__: tuple[str, ...] = (
    "DEFAULT_STANDARD_CONSULTATION_CONFIG_PATH",
    "StandardConsultationAgentSettings",
    "StandardConsultationQuestionBudgetConfig",
    "StandardConsultationRagConfig",
    "StandardConsultationReadinessConfig",
    "StandardConsultationTimeoutConfig",
    "load_standard_consultation_agent_settings",
)
