##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/dto.py
# 作用: 定义 SafetyTriggerAgent 请求、急症 brief、确认计划、草稿、自检、trace 与端口摘要 DTO。
# 边界: 仅承载严格结构化数据，不执行输入安全判决、模型调用、RAG 检索、输出审查或发布。
##################################################################################################

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.safety_trigger_agent.enums import (
    ConfirmationMode,
    EmergencyHintCode,
    SafetyTraceWriteStatus,
    SafetyTriggerDraftStatus,
)
from veterinary_agent.vet_context_builder import VetContextBundleDto

JsonMap: TypeAlias = dict[str, object]


class SafetyTriggerDto(BaseModel):
    """SafetyTriggerAgent DTO 严格模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls, value: Any) -> Any:
        """清理字符串字段值。

        :param value: 原始 DTO 字段值。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class SafetySignalSummaryDto(SafetyTriggerDto):
    """急症输入安全信号摘要。"""

    signal_id: str = Field(min_length=1, max_length=128, description="信号 ID。")
    signal_code: str = Field(min_length=1, max_length=128, description="信号编码。")
    signal_strength: str = Field(
        min_length=1,
        max_length=64,
        description="信号强度。",
    )
    normalized_concept: str = Field(
        min_length=1,
        max_length=256,
        description="归一化风险概念。",
    )
    evidence_text_hash: str = Field(
        min_length=1,
        max_length=128,
        description="证据文本 hash 或受控引用。",
    )
    dictionary_version: str = Field(
        min_length=1,
        max_length=128,
        description="信号词库或抽取器版本。",
    )


class EmergencyBriefDto(SafetyTriggerDto):
    """急症写作使用的最小上下文视图。"""

    user_text_ref: str = Field(
        min_length=1,
        max_length=256,
        description="当前子任务文本引用。",
    )
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="当前宠物物种作用域。",
    )
    signal_summaries: list[SafetySignalSummaryDto] = Field(
        default_factory=list,
        description="急症或毒物信号摘要。",
    )
    realtime_markers: list[str] = Field(
        default_factory=list,
        description="实况标记摘要。",
    )
    risk_entity_summaries: list[str] = Field(
        default_factory=list,
        description="毒物、药物或风险对象摘要。",
    )
    emergency_hint_codes: list[EmergencyHintCode] = Field(
        default_factory=list,
        description="急症生成弱提示编码。",
    )
    multi_task_first_segment_required: bool = Field(
        default=True,
        description="是否要求多任务时急症段首发。",
    )
    generation_constraints: list[str] = Field(
        default_factory=list,
        description="传给写作 Agent 的生成约束摘要。",
    )


class KeyConfirmationPlanDto(SafetyTriggerDto):
    """急症关键确认或记录计划。"""

    mode: ConfirmationMode = Field(description="确认模式。")
    confirmation_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="确认或记录项文本；不需要时为空。",
    )
    blocks_vet_direction: bool = Field(
        default=False,
        description="该确认是否阻塞就医导向。",
    )
    reason_code: str = Field(
        min_length=1,
        max_length=128,
        description="规划原因码。",
    )

    @model_validator(mode="after")
    def _validate_confirmation_text(self) -> "KeyConfirmationPlanDto":
        """校验确认模式与确认文本关系。

        :return: 已通过关系校验的确认计划。
        :raises ValueError: 当阻塞就医或确认文本缺失时抛出。
        """

        if self.blocks_vet_direction:
            raise ValueError("急症关键确认不得阻塞就医导向")
        if (
            self.mode is not ConfirmationMode.NO_QUESTION
            and self.confirmation_text is None
        ):
            raise ValueError("非 NO_QUESTION 模式必须提供 confirmation_text")
        return self


class SafetyRequirementSetDto(SafetyTriggerDto):
    """急症生成硬性安全要求集合。"""

    require_vet_direction: bool = Field(default=True, description="是否要求就医导向。")
    require_no_rag: bool = Field(default=True, description="是否要求不调用 RAG。")
    require_no_t4: bool = Field(default=True, description="是否要求不得输出 T4。")
    require_disclaimer: bool = Field(default=True, description="是否要求免责说明。")
    max_confirmation_count: int = Field(
        default=1,
        ge=0,
        le=1,
        description="最多允许确认问题数量。",
    )
    forbidden_content_tags: list[str] = Field(
        default_factory=list,
        description="禁止内容标签列表。",
    )


class SafetyRagPolicySummaryDto(SafetyTriggerDto):
    """急症链路 RAG 禁用证明摘要。"""

    verified: bool = Field(description="是否完成工具权限禁用确认。")
    rag_invoked: Literal[False] = Field(
        default=False,
        description="急症剖面固定不得调用 RAG。",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="急症剖面固定为空的检索 ID 列表。",
    )
    degraded_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="权限确认降级原因。",
    )

    @model_validator(mode="after")
    def _validate_no_retrieval_ids(self) -> "SafetyRagPolicySummaryDto":
        """校验急症 RAG 摘要不携带检索结果。

        :return: 已通过 RAG 禁用校验的摘要。
        :raises ValueError: 当检索 ID 非空时抛出。
        """

        if self.retrieval_ids:
            raise ValueError("safety_trigger 的 retrieval_ids 必须为空")
        return self


class SafetyTriggerSelfCheckSummaryDto(SafetyTriggerDto):
    """急症草稿自检摘要。"""

    vet_direction_present: bool = Field(description="草稿是否包含就医导向。")
    confirmation_count_valid: bool = Field(description="确认问题数量是否合规。")
    rag_invocation_absent: bool = Field(description="是否未发现 RAG 调用或引用。")
    t4_risk_detected: bool = Field(description="是否检出 T4 风险。")
    differential_overexpanded: bool = Field(description="是否展开完整鉴别长文。")
    saf01_risk_entity_named: bool = Field(
        default=True,
        description="SAF-01 场景是否点名或泛化说明风险物。",
    )
    disclaimer_present: bool = Field(default=True, description="是否包含免责说明。")
    fallback_recommended: bool = Field(description="是否建议使用急症兜底草稿。")
    issue_codes: list[str] = Field(
        default_factory=list,
        description="自检发现的问题编码。",
    )


class SafetyTriggerTracePatchDto(SafetyTriggerDto):
    """急症组件可写入 L2 逻辑链的 trace patch。"""

    safety_trigger_agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="急症组件版本。",
    )
    writer_version: str = Field(
        min_length=1,
        max_length=128,
        description="急症写作策略版本。",
    )
    confirmation_planner_version: str = Field(
        min_length=1,
        max_length=128,
        description="确认规划策略版本。",
    )
    fallback_template_version: str = Field(
        min_length=1,
        max_length=128,
        description="兜底模板版本。",
    )
    requirement_set_version: str = Field(
        min_length=1,
        max_length=128,
        description="最小安全要素版本。",
    )
    signal_codes: list[str] = Field(default_factory=list, description="信号编码列表。")
    emergency_hint_codes: list[EmergencyHintCode] = Field(
        default_factory=list,
        description="急症 hint 编码列表。",
    )
    confirmation_mode: ConfirmationMode = Field(description="确认模式。")
    template_fallback_used: bool = Field(description="是否使用兜底草稿。")
    rag_invoked: Literal[False] = Field(
        default=False,
        description="急症剖面固定未调用 RAG。",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="急症剖面固定为空的检索 ID 列表。",
    )
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="本轮降级标记。",
    )


class SafetyTriggerRequestDto(SafetyTriggerDto):
    """生成急症简版草稿的应用内请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    task_type: str = Field(min_length=1, max_length=128, description="受控任务类型。")
    normalized_query: str = Field(
        min_length=1,
        max_length=32768,
        description="子任务规范化文本。",
    )
    generation_profile: str = Field(
        min_length=1,
        max_length=128,
        description="输入安全判定出的生成剖面。",
    )
    executor_key: str = Field(
        min_length=1,
        max_length=128,
        description="输入安全判定出的实际执行器。",
    )
    assessment_summary: JsonMap = Field(
        default_factory=dict,
        description="输入安全评估受控摘要。",
    )
    context: VetContextBundleDto = Field(description="VetContextBuilder 输出 bundle。")
    params_version: str = Field(
        min_length=1,
        max_length=128,
        description="业务参数版本。",
    )
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="RuntimeConfig 快照 ID。",
    )


class SafetyTriggerDraftDto(SafetyTriggerDto):
    """SafetyTriggerAgent 唯一对外业务结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前宠物 ID。",
    )
    status: SafetyTriggerDraftStatus = Field(description="草稿状态。")
    draft_response: str = Field(
        min_length=1,
        max_length=32768,
        description="待输出安全审查的急症草稿正文。",
    )
    draft_response_ref: str = Field(
        min_length=1,
        max_length=256,
        description="草稿引用 ID。",
    )
    emergency_brief: EmergencyBriefDto = Field(description="急症最小 brief。")
    confirmation_plan: KeyConfirmationPlanDto = Field(description="关键确认计划。")
    urgency_statement: str = Field(
        min_length=1,
        max_length=1024,
        description="紧急度表述。",
    )
    vet_direction: str = Field(
        min_length=1,
        max_length=1024,
        description="就医导向表述。",
    )
    safe_actions: list[str] = Field(
        default_factory=list,
        description="低风险临时动作。",
    )
    forbidden_actions: list[str] = Field(
        default_factory=list,
        description="危险动作提醒。",
    )
    info_to_prepare: list[str] = Field(
        default_factory=list,
        description="给兽医准备的信息。",
    )
    rag_invoked: Literal[False] = Field(
        default=False,
        description="急症剖面固定未调用 RAG。",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="急症剖面固定为空的检索 ID 列表。",
    )
    self_check: SafetyTriggerSelfCheckSummaryDto = Field(description="急症自检摘要。")
    trace_patch: SafetyTriggerTracePatchDto = Field(description="急症 trace patch。")
    trace_delivery_status: SafetyTraceWriteStatus = Field(
        default=SafetyTraceWriteStatus.SKIPPED,
        description="trace patch 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_rag_absent(self) -> "SafetyTriggerDraftDto":
        """校验急症草稿不携带 RAG 结果。

        :return: 已通过 RAG 禁用关系校验的急症草稿。
        :raises ValueError: 当检索 ID 非空时抛出。
        """

        if self.retrieval_ids:
            raise ValueError("safety_trigger 草稿 retrieval_ids 必须为空")
        if self.trace_patch.retrieval_ids:
            raise ValueError("safety_trigger trace patch retrieval_ids 必须为空")
        return self


class SafetyTriggerTraceRecordDto(SafetyTriggerDto):
    """可提交给 LogicTraceStore 的急症脱敏 trace 摘要。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前宠物 ID。",
    )
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    status: SafetyTriggerDraftStatus = Field(description="草稿状态。")
    trace_patch: SafetyTriggerTracePatchDto = Field(description="急症 trace patch。")
    self_check: SafetyTriggerSelfCheckSummaryDto = Field(description="自检摘要。")
    params_version: str = Field(
        min_length=1,
        max_length=128,
        description="业务参数版本。",
    )
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="RuntimeConfig 快照 ID。",
    )


class SafetyTraceWriteResultDto(SafetyTriggerDto):
    """急症 trace patch 写入结果。"""

    status: SafetyTraceWriteStatus = Field(description="trace 写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="trace 降级错误码。",
    )
    retryable: bool = Field(default=False, description="trace 写入是否可补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="trace 写入降级说明。",
    )


__all__: tuple[str, ...] = (
    "EmergencyBriefDto",
    "JsonMap",
    "KeyConfirmationPlanDto",
    "SafetyRagPolicySummaryDto",
    "SafetyRequirementSetDto",
    "SafetySignalSummaryDto",
    "SafetyTraceWriteResultDto",
    "SafetyTriggerDraftDto",
    "SafetyTriggerDto",
    "SafetyTriggerRequestDto",
    "SafetyTriggerSelfCheckSummaryDto",
    "SafetyTriggerTracePatchDto",
    "SafetyTriggerTraceRecordDto",
)
