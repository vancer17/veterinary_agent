##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/dto.py
# 作用: 定义 StandardConsultationAgent 请求、readiness、RAG 摘要、草稿、trace 与写入结果 DTO。
# 边界: 仅承载严格结构化数据，不执行模型调用、RAG 检索、输出安全审查或 checkpoint 写入。
##################################################################################################

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.standard_consultation_agent.enums import (
    ConsultationLayer,
    DraftStatus,
    QuestionPurpose,
    RetrievalPurpose,
    RiskImpact,
    StandardTraceWriteStatus,
)
from veterinary_agent.vet_context_builder import VetContextBundleDto

JsonMap: TypeAlias = dict[str, object]


class StandardConsultationDto(BaseModel):
    """StandardConsultationAgent DTO 严格模型基类。"""

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


class QuestionBudgetDto(StandardConsultationDto):
    """本轮标准问诊追问预算。"""

    max_questions: int = Field(default=3, ge=1, le=3, description="最多问题数。")
    allow_followup_when_safety_unclear: bool = Field(
        default=True,
        description="安全信息不清时是否允许优先追问。",
    )


class StandardSessionStateDto(StandardConsultationDto):
    """标准问诊跨轮短期状态。"""

    current_layer: ConsultationLayer = Field(
        default=ConsultationLayer.L0_COLLECTION,
        description="进入本轮前的标准问诊层级。",
    )
    current_complaint_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="当前主诉类型。",
    )
    asked_question_index: dict[str, list[str]] = Field(
        default_factory=dict,
        description="按事实键记录的已问问题文本。",
    )
    layer_state: JsonMap = Field(default_factory=dict, description="层级状态摘要。")
    standard_round_count: int = Field(
        default=0,
        ge=0,
        description="standard 剖面已运行轮次数。",
    )
    last_stop_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="上一轮停止原因。",
    )


class ReadinessProfileDto(StandardConsultationDto):
    """标准问诊中控使用的信息完备度视图。"""

    symptom_entity_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="症状实体置信度。",
    )
    high_risk_field_completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="高风险字段完整度。",
    )
    rag_evidence_readiness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="RAG 证据可用度。",
    )
    differential_convergence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="鉴别方向收敛度。",
    )
    contraindication_completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="护理或用药禁忌信息完整度。",
    )
    answer_consistency: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="当前事实回答一致性。",
    )
    hard_gates: list[str] = Field(
        default_factory=list,
        description="未通过的硬门槛原因码。",
    )


class CandidateQuestionDto(StandardConsultationDto):
    """问诊采集子 Agent 和中控共同使用的候选追问问题。"""

    question_id: str = Field(min_length=1, max_length=128, description="问题 ID。")
    question_text: str = Field(min_length=1, max_length=512, description="问题正文。")
    target_fact_key: str = Field(
        min_length=1,
        max_length=128,
        description="问题希望补齐的事实键。",
    )
    purpose: QuestionPurpose = Field(description="问题目的。")
    target_layer: ConsultationLayer = Field(description="问题服务的目标层级。")
    risk_impact: RiskImpact = Field(
        default=RiskImpact.MEDIUM,
        description="该问题对风险判断的影响等级。",
    )
    information_gain: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="预估信息增益。",
    )
    already_known: bool = Field(default=False, description="目标事实是否已知。")
    already_asked: bool = Field(default=False, description="本问题是否已经问过。")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="支持该问题的证据引用 ID。",
    )


class RagEvidenceHintDto(StandardConsultationDto):
    """RAG 返回的标准化医学启发摘要。"""

    evidence_id: str = Field(min_length=1, max_length=128, description="证据 ID。")
    title: str = Field(min_length=1, max_length=256, description="证据标题。")
    source_ref: str = Field(min_length=1, max_length=256, description="来源引用。")
    summary: str = Field(min_length=1, max_length=1024, description="证据摘要。")


class RagEvidenceBundleDto(StandardConsultationDto):
    """标准问诊一次阶段式 RAG 检索的脱敏证据包。"""

    retrieval_purpose: RetrievalPurpose = Field(description="检索用途。")
    query_hashes: list[str] = Field(default_factory=list, description="查询 hash。")
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    source_versions: list[str] = Field(default_factory=list, description="来源版本。")
    cache_hit: bool = Field(default=False, description="是否命中缓存。")
    degraded: bool = Field(default=False, description="检索是否降级。")
    evidence_hints: list[RagEvidenceHintDto] = Field(
        default_factory=list,
        description="标准化证据启发摘要。",
    )
    degraded_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="检索降级原因。",
    )


class EscalationRequestDto(StandardConsultationDto):
    """standard 内部发现急症切换时输出的升级请求。"""

    reason_code: str = Field(min_length=1, max_length=128, description="升级原因码。")
    summary: str = Field(min_length=1, max_length=512, description="升级摘要。")
    target_profile: str = Field(
        default="safety_trigger",
        min_length=1,
        max_length=128,
        description="建议切换到的生成剖面。",
    )


class EvidenceBindingDto(StandardConsultationDto):
    """草稿声明与证据引用之间的绑定摘要。"""

    claim_id: str = Field(min_length=1, max_length=128, description="声明 ID。")
    evidence_ids: list[str] = Field(description="支持该声明的证据 ID。")
    binding_summary: str = Field(
        min_length=1,
        max_length=512,
        description="证据绑定摘要。",
    )


class SlotProgressPatchDto(StandardConsultationDto):
    """本轮建议写回 checkpoint 的槽位进度补丁。"""

    known_updates: JsonMap = Field(default_factory=dict, description="新增已知槽位。")
    requested_slots: list[str] = Field(
        default_factory=list,
        description="本轮请求用户补齐的槽位。",
    )
    stale_slots: list[str] = Field(
        default_factory=list,
        description="本轮发现仍需更新的过期槽位。",
    )


class StandardTracePatchDto(StandardConsultationDto):
    """标准问诊可写入 L2 逻辑链的 trace patch。"""

    standard_agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="标准问诊 Agent 版本。",
    )
    orchestrator_version: str = Field(
        min_length=1,
        max_length=128,
        description="标准问诊中控版本。",
    )
    sub_agent_versions: list[str] = Field(
        default_factory=list,
        description="本轮使用的子 Agent 版本摘要。",
    )
    layer_before: ConsultationLayer = Field(description="进入本轮前的层级。")
    layer_after: ConsultationLayer = Field(description="本轮达到的层级。")
    activated_agents: list[str] = Field(
        default_factory=list,
        description="本轮激活的内部子 Agent。",
    )
    selected_question_ids: list[str] = Field(
        default_factory=list,
        description="最终选择的问题 ID。",
    )
    retrieval_ids: list[str] = Field(default_factory=list, description="RAG 检索 ID。")
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="本轮降级标记。",
    )


class StandardConsultationRequestDto(StandardConsultationDto):
    """生成标准问诊草稿的应用内请求。"""

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
    session_state: StandardSessionStateDto = Field(
        default_factory=StandardSessionStateDto,
        description="标准问诊短期状态。",
    )
    question_budget: QuestionBudgetDto = Field(
        default_factory=QuestionBudgetDto,
        description="本轮追问预算。",
    )
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


class StandardConsultationDraftDto(StandardConsultationDto):
    """StandardConsultationAgent 唯一对外业务结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前宠物 ID。",
    )
    status: DraftStatus = Field(description="草稿状态。")
    draft_response: str = Field(
        min_length=1, max_length=32768, description="草稿正文。"
    )
    draft_response_ref: str = Field(
        min_length=1,
        max_length=256,
        description="草稿引用 ID。",
    )
    reached_layer: ConsultationLayer = Field(description="本轮达到的标准问诊层级。")
    triage_summary: JsonMap = Field(default_factory=dict, description="分诊摘要。")
    direction_hints: list[JsonMap] = Field(
        default_factory=list,
        description="方向提示摘要。",
    )
    differential_hypotheses: list[JsonMap] = Field(
        default_factory=list,
        description="鉴别方向摘要。",
    )
    care_suggestions: list[JsonMap] = Field(
        default_factory=list,
        description="护理或处置建议摘要。",
    )
    selected_questions: list[CandidateQuestionDto] = Field(
        default_factory=list,
        description="本轮最终选择的问题。",
    )
    slot_progress_patch: SlotProgressPatchDto = Field(
        default_factory=SlotProgressPatchDto,
        description="建议写回 checkpoint 的槽位补丁。",
    )
    stop_reason_candidate: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="候选停止原因。",
    )
    escalation_request: EscalationRequestDto | None = Field(
        default=None,
        description="急症升级请求。",
    )
    evidence_bindings: list[EvidenceBindingDto] = Field(
        default_factory=list,
        description="证据绑定摘要。",
    )
    rag_summary: list[RagEvidenceBundleDto] = Field(
        default_factory=list,
        description="RAG 使用摘要。",
    )
    trace_patch: StandardTracePatchDto = Field(description="标准问诊 trace patch。")
    trace_delivery_status: StandardTraceWriteStatus = Field(
        default=StandardTraceWriteStatus.SKIPPED,
        description="trace patch 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_question_budget_relation(self) -> "StandardConsultationDraftDto":
        """校验草稿问题数量不超过产品硬上限。

        :return: 已通过关系校验的标准问诊草稿。
        :raises ValueError: 当草稿选择问题超过硬上限时抛出。
        """

        if len(self.selected_questions) > 3:
            raise ValueError("标准问诊每轮最多选择 3 个问题")
        if (
            self.status is DraftStatus.NEEDS_SAFETY_ESCALATION
            and self.escalation_request is None
        ):
            raise ValueError("安全升级草稿必须携带 escalation_request")
        return self


class StandardConsultationTraceRecordDto(StandardConsultationDto):
    """可提交给 LogicTraceStore 的标准问诊脱敏 trace 摘要。"""

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
    status: DraftStatus = Field(description="草稿状态。")
    trace_patch: StandardTracePatchDto = Field(description="标准问诊 trace patch。")
    selected_question_count: int = Field(ge=0, le=3, description="选中问题数量。")
    evidence_binding_count: int = Field(ge=0, description="证据绑定数量。")
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


class StandardTraceWriteResultDto(StandardConsultationDto):
    """标准问诊 trace patch 写入结果。"""

    status: StandardTraceWriteStatus = Field(description="trace 写入状态。")
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
    "CandidateQuestionDto",
    "EscalationRequestDto",
    "EvidenceBindingDto",
    "JsonMap",
    "QuestionBudgetDto",
    "RagEvidenceBundleDto",
    "RagEvidenceHintDto",
    "ReadinessProfileDto",
    "SlotProgressPatchDto",
    "StandardConsultationDraftDto",
    "StandardConsultationDto",
    "StandardConsultationRequestDto",
    "StandardConsultationTraceRecordDto",
    "StandardSessionStateDto",
    "StandardTracePatchDto",
    "StandardTraceWriteResultDto",
)
