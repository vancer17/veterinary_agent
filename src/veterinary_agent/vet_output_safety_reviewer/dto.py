##################################################################################################
# 文件: src/veterinary_agent/vet_output_safety_reviewer/dto.py
# 作用: 定义 VetOutputSafetyReviewer 请求、上下文、发现项、改写计划、结果与 trace DTO。
# 边界: 仅承载严格结构化数据，不执行模型调用、用药策略判定、输出改写或 trace 持久化。
##################################################################################################

from typing import Any, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.vet_output_safety_reviewer.enums import (
    MedicationPolicyDecisionStatus,
    OutputFindingSeverity,
    OutputFindingType,
    OutputReviewTraceWriteStatus,
    ReviewActionType,
    ReviewDomain,
    ReviewStatus,
)

JsonMap: TypeAlias = dict[str, object]


class VetOutputSafetyReviewerDto(BaseModel):
    """VetOutputSafetyReviewer DTO 严格模型基类。"""

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
        :return: 若字段值为字符串，则返回去除首尾空白后的值；否则返回原值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class MedicationSpanCandidateDto(VetOutputSafetyReviewerDto):
    """输出审查消费的用药相关 span 候选。"""

    span_id: str = Field(min_length=1, max_length=128, description="span ID。")
    span_type: str = Field(min_length=1, max_length=128, description="span 类型。")
    text_hash: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="span 文本 hash；不得要求携带原文。",
    )
    normalized_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="归一化标签。",
    )
    extraction_source: str = Field(
        default="upstream",
        min_length=1,
        max_length=128,
        description="span 来源。",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度。")
    context_role: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="上下文角色。",
    )


class MedicationPolicyFindingDto(VetOutputSafetyReviewerDto):
    """输出审查侧归一化后的用药策略发现项。"""

    finding_id: str = Field(min_length=1, max_length=128, description="发现项 ID。")
    finding_type: str = Field(min_length=1, max_length=128, description="发现类型。")
    severity: OutputFindingSeverity = Field(description="严重程度。")
    tier: str | None = Field(
        default=None, min_length=1, max_length=64, description="层级。"
    )
    reason_code: str = Field(min_length=1, max_length=128, description="原因码。")
    span_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="关联 span 引用。",
    )
    rule_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="命中规则版本。",
    )


class MedicationPolicyAnalysisRequestDto(VetOutputSafetyReviewerDto):
    """输出审查调用用药策略端口时使用的请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="trace ID。")
    candidate_text_ref: str = Field(
        min_length=1,
        max_length=512,
        description="待审查文本引用。",
    )
    candidate_text: str = Field(
        min_length=1,
        max_length=32768,
        description="待审查文本正文；仅应用内端口消费，不进入 trace。",
    )
    generation_profile: str = Field(
        min_length=1,
        max_length=128,
        description="生成剖面。",
    )
    executor_key: str = Field(min_length=1, max_length=128, description="执行器。")
    pet_species: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="宠物物种摘要。",
    )
    span_candidates: list[MedicationSpanCandidateDto] = Field(
        default_factory=list,
        description="上游提供的用药 span 候选。",
    )
    text_source: str = Field(
        default="draft_response",
        min_length=1,
        max_length=128,
        description="文本来源。",
    )
    params_version: str = Field(min_length=1, max_length=128, description="参数版本。")


class MedicationPolicyDecisionDto(VetOutputSafetyReviewerDto):
    """输出审查消费的用药策略判定结果。"""

    status: MedicationPolicyDecisionStatus = Field(description="策略判定状态。")
    action: str = Field(min_length=1, max_length=128, description="动作建议。")
    policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="用药策略版本。",
    )
    findings: list[MedicationPolicyFindingDto] = Field(
        default_factory=list,
        description="用药策略发现项。",
    )
    rewrite_hints: list[str] = Field(default_factory=list, description="改写提示。")
    fallback_required: bool = Field(default=False, description="是否建议 fallback。")
    degraded_flags: list[str] = Field(default_factory=list, description="降级标记。")
    trace_patch: JsonMap = Field(default_factory=dict, description="策略 trace 摘要。")


class ReviewInputContextDto(VetOutputSafetyReviewerDto):
    """输出安全审查所需的最小上下文视图。"""

    assessment_summary: JsonMap = Field(
        default_factory=dict,
        description="输入安全评估受控摘要。",
    )
    signal_codes: list[str] = Field(default_factory=list, description="安全信号码。")
    rag_summary: JsonMap = Field(default_factory=dict, description="RAG 使用摘要。")
    evidence_bindings: list[JsonMap] = Field(
        default_factory=list,
        description="claim 与证据绑定摘要。",
    )
    lab_analytes: list[JsonMap] = Field(
        default_factory=list,
        description="化验项或 OCR 结构化摘要。",
    )
    medication_spans: list[MedicationSpanCandidateDto] = Field(
        default_factory=list,
        description="用药相关 span 候选。",
    )
    content_plan_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="内容编排计划引用。",
    )
    context_summary_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="上下文摘要引用。",
    )
    medical_content_expected: bool = Field(
        default=False,
        description="调用方是否声明该段包含医学内容。",
    )
    ocr_confirmed: bool | None = Field(
        default=None,
        description="OCR 或病历结构化内容是否已确认。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="轻量上下文元信息。")


class OutputSafetyReviewRequestDto(VetOutputSafetyReviewerDto):
    """输出安全审查请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="trace ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, max_length=128, description="宠物 ID。")
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    segment_id: str = Field(min_length=1, max_length=128, description="segment ID。")
    generation_profile: str = Field(
        min_length=1,
        max_length=128,
        description="生成剖面。",
    )
    executor_key: str = Field(min_length=1, max_length=128, description="执行器。")
    draft_response_ref: str = Field(
        min_length=1,
        max_length=512,
        description="草稿引用。",
    )
    draft_response_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=32768,
        description="应用内传递的草稿正文；不得写入 trace。",
    )
    input_context: ReviewInputContextDto = Field(
        default_factory=ReviewInputContextDto,
        description="审查上下文。",
    )
    params_version: str = Field(min_length=1, max_length=128, description="参数版本。")
    config_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="配置快照 ID。",
    )


class OutputSafetyFindingDto(VetOutputSafetyReviewerDto):
    """输出安全审查发现项。"""

    finding_id: str = Field(min_length=1, max_length=128, description="发现项 ID。")
    finding_type: OutputFindingType = Field(description="发现项类型。")
    severity: OutputFindingSeverity = Field(description="严重程度。")
    reason_code: str = Field(min_length=1, max_length=128, description="原因码。")
    evidence_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="证据、草稿或摘要引用。",
    )
    source_review_domain: ReviewDomain = Field(description="来源风险域。")
    p0_candidate: bool = Field(default=False, description="是否为 P0 候选。")
    metadata: JsonMap = Field(default_factory=dict, description="轻量元信息。")


class ReviewDomainResultDto(VetOutputSafetyReviewerDto):
    """单个风险域的结构化审查摘要。"""

    domain: ReviewDomain = Field(description="风险域。")
    status: str = Field(min_length=1, max_length=128, description="审查状态。")
    findings: list[OutputSafetyFindingDto] = Field(
        default_factory=list,
        description="风险域发现项。",
    )
    rewrite_hints: list[str] = Field(default_factory=list, description="改写提示。")
    degraded: bool = Field(default=False, description="当前风险域是否降级。")


class RewritePlanDto(VetOutputSafetyReviewerDto):
    """输出安全审查改写计划。"""

    plan_id: str = Field(min_length=1, max_length=128, description="改写计划 ID。")
    action_types: list[ReviewActionType] = Field(
        default_factory=list,
        description="计划动作类型。",
    )
    target_finding_ids: list[str] = Field(
        default_factory=list,
        description="动作覆盖的发现项 ID。",
    )
    fallback_recommended: bool = Field(default=False, description="是否建议 fallback。")
    required_constraints: list[str] = Field(
        default_factory=list,
        description="改写必须满足的约束。",
    )


class OutputGuardActionDto(VetOutputSafetyReviewerDto):
    """输出安全审查护栏动作。"""

    action_id: str = Field(min_length=1, max_length=160, description="动作 ID。")
    action_type: ReviewActionType = Field(description="动作类型。")
    reason_code: str = Field(min_length=1, max_length=128, description="原因码。")
    before_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="动作前引用。",
    )
    after_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="动作后引用。",
    )
    source_finding_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="来源发现项 ID。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="轻量动作元信息。")


class OutputReviewTracePatchDto(VetOutputSafetyReviewerDto):
    """输出安全审查 trace patch。"""

    reviewer_version: str = Field(
        min_length=1,
        max_length=128,
        description="输出安全审查器版本。",
    )
    writer_version: str = Field(
        min_length=1,
        max_length=128,
        description="改写器版本。",
    )
    medication_policy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="用药策略版本。",
    )
    finding_types: list[OutputFindingType] = Field(
        default_factory=list,
        description="发现项类型集合。",
    )
    action_types: list[ReviewActionType] = Field(
        default_factory=list,
        description="动作类型集合。",
    )
    degraded_flags: list[str] = Field(default_factory=list, description="降级标记。")
    review_domains: list[ReviewDomain] = Field(
        default_factory=list,
        description="本次执行的风险域。",
    )


class OutputSafetyReviewResultDto(VetOutputSafetyReviewerDto):
    """输出安全审查结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    segment_id: str = Field(min_length=1, max_length=128, description="segment ID。")
    reviewed_draft_ref: str = Field(
        min_length=1,
        max_length=512,
        description="审查后草稿引用。",
    )
    reviewed_draft_text: str = Field(
        min_length=1,
        max_length=32768,
        description="审查后草稿正文；仅供应用内继续进入 gate。",
    )
    status: ReviewStatus = Field(description="审查状态。")
    findings: list[OutputSafetyFindingDto] = Field(
        default_factory=list,
        description="审查发现项。",
    )
    guard_actions: list[OutputGuardActionDto] = Field(
        default_factory=list,
        description="护栏动作。",
    )
    medication_decision: MedicationPolicyDecisionDto | None = Field(
        default=None,
        description="用药策略判定结果。",
    )
    rewrite_plan: RewritePlanDto = Field(description="改写计划。")
    fallback_recommended: bool = Field(default=False, description="是否建议 fallback。")
    review_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="审查置信度。",
    )
    degraded_flags: list[str] = Field(default_factory=list, description="降级标记。")
    trace_patch: OutputReviewTracePatchDto = Field(description="输出审查 trace patch。")
    trace_delivery_status: OutputReviewTraceWriteStatus = Field(
        default=OutputReviewTraceWriteStatus.SKIPPED,
        description="trace 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_p0_actions(self) -> Self:
        """校验 P0 候选发现项必须具备明确动作覆盖。

        :return: 已通过关系校验的审查结果。
        :raises ValueError: 当 P0 候选发现项缺少动作覆盖时抛出。
        """

        targeted_finding_ids = {
            action.source_finding_id
            for action in self.guard_actions
            if action.source_finding_id is not None
        }
        missing_action_ids = [
            finding.finding_id
            for finding in self.findings
            if finding.p0_candidate and finding.finding_id not in targeted_finding_ids
        ]
        if missing_action_ids:
            raise ValueError("P0 候选发现项必须携带明确护栏动作")
        if (
            self.status is ReviewStatus.FALLBACK_RECOMMENDED
            and not self.fallback_recommended
        ):
            raise ValueError(
                "status=FALLBACK_RECOMMENDED 时必须声明 fallback_recommended"
            )
        return self


class OutputReviewTraceRecordDto(VetOutputSafetyReviewerDto):
    """可提交给 LogicTraceStore 的输出审查脱敏 trace 记录。"""

    request: OutputSafetyReviewRequestDto = Field(description="审查请求。")
    result: OutputSafetyReviewResultDto = Field(description="审查结果。")
    duration_ms: int = Field(ge=0, description="审查耗时，单位为毫秒。")


class OutputReviewTraceWriteResultDto(VetOutputSafetyReviewerDto):
    """输出审查 trace 写入结果。"""

    status: OutputReviewTraceWriteStatus = Field(description="trace 写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="trace 降级错误码。",
    )
    retryable: bool = Field(default=False, description="是否可补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="trace 写入降级说明。",
    )


__all__: tuple[str, ...] = (
    "JsonMap",
    "MedicationPolicyAnalysisRequestDto",
    "MedicationPolicyDecisionDto",
    "MedicationPolicyFindingDto",
    "MedicationSpanCandidateDto",
    "OutputGuardActionDto",
    "OutputReviewTracePatchDto",
    "OutputReviewTraceRecordDto",
    "OutputReviewTraceWriteResultDto",
    "OutputSafetyFindingDto",
    "OutputSafetyReviewRequestDto",
    "OutputSafetyReviewResultDto",
    "ReviewDomainResultDto",
    "ReviewInputContextDto",
    "RewritePlanDto",
    "VetOutputSafetyReviewerDto",
)
