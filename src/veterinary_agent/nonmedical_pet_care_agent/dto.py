##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/dto.py
# 作用: 定义 NonmedicalPetCareAgent 请求、brief、建议计划、RAG 计划、约束、草稿与 trace DTO。
# 边界: 仅承载严格结构化数据，不执行模型调用、RAG 检索、输出安全审查或 checkpoint 写入。
##################################################################################################

from typing import Any, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.nonmedical_pet_care_agent.enums import (
    AdviceDimensionCode,
    CareDomain,
    NonmedicalDraftStatus,
    NonmedicalRetrievalPurpose,
    NonmedicalTraceWriteStatus,
    PersonalizationLevel,
)
from veterinary_agent.vet_context_builder import VetContextBundleDto

JsonMap: TypeAlias = dict[str, object]


class NonmedicalPetCareAgentDto(BaseModel):
    """NonmedicalPetCareAgent DTO 严格模型基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_value(cls: type[Self], value: Any) -> Any:
        """清理字符串字段值。

        :param value: 原始 DTO 字段值。
        :return: 字符串去除首尾空白后的值，或原始非字符串值。
        """

        if isinstance(value, str):
            return value.strip()
        return value


class InputSafetySignalDto(NonmedicalPetCareAgentDto):
    """输入安全评估中被非医疗建议消费的信号摘要。"""

    signal_id: str = Field(min_length=1, max_length=128, description="信号 ID。")
    code: str = Field(min_length=1, max_length=128, description="SAF 或跨域信号码。")
    strength: str = Field(
        default="NOT_APPLICABLE",
        min_length=1,
        max_length=64,
        description="信号强度。",
    )
    normalized_concept: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="归一化信号概念。",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度。")


class NonmedicalAdviceRequestDto(NonmedicalPetCareAgentDto):
    """生成非医疗养宠建议草稿的应用内请求。"""

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
    generation_profile: str | None = Field(
        default=None,
        max_length=128,
        description="三生成剖面；纯非医疗任务应为空。",
    )
    executor_key: str = Field(
        min_length=1,
        max_length=128,
        description="输入安全评估判定出的实际执行器。",
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


class PetCareBriefDto(NonmedicalPetCareAgentDto):
    """本轮非医疗养宠建议主轴和可用上下文视图。"""

    main_request: str = Field(min_length=1, max_length=2048, description="当前诉求。")
    advice_axis: str = Field(min_length=1, max_length=256, description="建议主轴。")
    care_domain: CareDomain = Field(description="非医疗护理领域。")
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="物种适配范围。",
    )
    consumed_signals: list[InputSafetySignalDto] = Field(
        default_factory=list,
        description="本组件已消费的输入安全信号。",
    )
    available_pet_context_refs: list[str] = Field(
        default_factory=list,
        description="允许用于个性化的上下文引用。",
    )
    missing_personalization_fields: list[str] = Field(
        default_factory=list,
        description="缺失且不得编造的个性化字段。",
    )


class PersonalizationFactorDto(NonmedicalPetCareAgentDto):
    """非医疗建议可使用的个性化因子。"""

    factor_code: str = Field(min_length=1, max_length=128, description="因子代码。")
    value_summary: str = Field(
        min_length=1,
        max_length=512,
        description="不含敏感正文的因子值摘要。",
    )
    source_ref: str = Field(min_length=1, max_length=256, description="来源引用。")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度。")


class AdviceDimensionDto(NonmedicalPetCareAgentDto):
    """单个非医疗建议维度规划。"""

    dimension_code: AdviceDimensionCode = Field(description="建议维度代码。")
    priority: int = Field(ge=1, le=100, description="维度优先级，数值越小越优先。")
    required: bool = Field(default=False, description="该维度是否必须覆盖。")
    evidence_requirement: str = Field(
        min_length=1,
        max_length=512,
        description="该维度需要的证据或规则要求。",
    )
    prohibited_advice: list[str] = Field(
        default_factory=list,
        description="该维度下禁止生成的建议摘要。",
    )


class AdvicePlanDto(NonmedicalPetCareAgentDto):
    """动态非医疗建议维度规划。"""

    advice_axis: str = Field(min_length=1, max_length=256, description="建议主轴。")
    dimensions: list[AdviceDimensionDto] = Field(
        min_length=1,
        description="已选择的建议维度列表。",
    )
    personalization_factors: list[PersonalizationFactorDto] = Field(
        default_factory=list,
        description="规划阶段可见的个性化因子摘要。",
    )
    generation_constraints: list[str] = Field(
        default_factory=list,
        description="写作生成约束。",
    )
    safety_boundary_hints: list[str] = Field(
        default_factory=list,
        description="需要自然嵌入正文的安全边界提示。",
    )


class RetrievalFacetDto(NonmedicalPetCareAgentDto):
    """一次受控非医疗养宠 RAG 检索 facet。"""

    dimension_code: AdviceDimensionCode = Field(description="建议维度代码。")
    retrieval_purpose: NonmedicalRetrievalPurpose = Field(description="检索用途。")
    queries: list[str] = Field(min_length=1, description="受控检索 query 列表。")
    query_hashes: list[str] = Field(
        default_factory=list,
        description="检索 query 哈希摘要。",
    )
    collections: list[str] = Field(
        min_length=1,
        description="允许检索的知识集合。",
    )
    metadata_filters: JsonMap = Field(
        default_factory=dict,
        description="RAG metadata filter 摘要。",
    )
    top_k: int = Field(ge=1, le=20, description="最大返回条数。")
    rerank_enabled: bool = Field(default=True, description="是否启用 rerank。")
    source_policy_required: bool = Field(
        default=True,
        description="是否要求来源策略可判定。",
    )

    @model_validator(mode="after")
    def _normalize_query_hashes(self) -> "RetrievalFacetDto":
        """补齐 query_hashes 与 queries 的基础数量关系。

        :return: 已完成基础归一的检索 facet。
        :raises ValueError: 当 query_hashes 数量与 queries 数量不一致时抛出。
        """

        if self.query_hashes and len(self.query_hashes) != len(self.queries):
            raise ValueError("query_hashes 数量必须与 queries 数量一致")
        return self


class KnowledgeRetrievalPlanDto(NonmedicalPetCareAgentDto):
    """受控非医疗养宠知识检索或规则接地计划。"""

    plan_id: str = Field(min_length=1, max_length=128, description="检索计划 ID。")
    facets: list[RetrievalFacetDto] = Field(
        default_factory=list,
        description="检索 facet 列表。",
    )
    rag_required: bool = Field(default=False, description="本轮是否必须调用 RAG。")
    conservative_rules_allowed: bool = Field(
        default=True,
        description="是否允许受控规则库保守生成。",
    )


class EvidenceHintDto(NonmedicalPetCareAgentDto):
    """RAG 返回的单条可组织证据摘要。"""

    evidence_id: str = Field(min_length=1, max_length=128, description="证据 ID。")
    title: str = Field(min_length=1, max_length=256, description="证据标题。")
    source_ref: str = Field(min_length=1, max_length=256, description="来源引用。")
    summary: str = Field(min_length=1, max_length=1024, description="证据摘要。")
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="证据适用物种范围。",
    )
    source_policy: str = Field(
        default="restricted",
        min_length=1,
        max_length=128,
        description="归一后的来源策略。",
    )
    public_citable: bool = Field(default=False, description="是否可公开引用。")


class NonmedicalRagResultDto(NonmedicalPetCareAgentDto):
    """一次 NonmedicalPetCareAgent 受控 RAG 检索结果摘要。"""

    retrieval_purpose: NonmedicalRetrievalPurpose = Field(description="检索用途。")
    dimension_code: AdviceDimensionCode = Field(description="建议维度代码。")
    query_hashes: list[str] = Field(default_factory=list, description="query 哈希。")
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    source_versions: list[str] = Field(default_factory=list, description="来源版本。")
    cache_hit: bool = Field(default=False, description="是否命中缓存。")
    degraded: bool = Field(default=False, description="检索是否降级。")
    evidence_hints: list[EvidenceHintDto] = Field(
        default_factory=list,
        description="证据摘要列表。",
    )
    degraded_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="检索降级原因。",
    )


class EvidenceCardDto(NonmedicalPetCareAgentDto):
    """写作 Agent 可消费的最小养宠证据卡。"""

    evidence_card_id: str = Field(
        min_length=1,
        max_length=128,
        description="证据卡 ID。",
    )
    dimension_code: AdviceDimensionCode = Field(description="建议维度代码。")
    supported_principle_summary: str = Field(
        min_length=1,
        max_length=1024,
        description="证据支持的养宠原则摘要。",
    )
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="证据卡适用物种范围。",
    )
    retrieval_ids: list[str] = Field(description="证据卡绑定的检索或规则引用 ID。")
    source_policy: str = Field(
        default="restricted",
        min_length=1,
        max_length=128,
        description="来源策略。",
    )
    public_citable: bool = Field(default=False, description="是否可公开引用。")

    @model_validator(mode="after")
    def _validate_reference_binding(self) -> "EvidenceCardDto":
        """校验证据卡必须绑定稳定引用。

        :return: 已通过引用关系校验的证据卡。
        :raises ValueError: 当 retrieval_ids 为空时抛出。
        """

        if not self.retrieval_ids:
            raise ValueError("EvidenceCard 必须绑定至少一个 retrieval_id 或 rule_id")
        return self


class AdviceConstraintDto(NonmedicalPetCareAgentDto):
    """证据或规则对非医疗建议的约束。"""

    constraint_id: str = Field(min_length=1, max_length=128, description="约束 ID。")
    constraint_type: str = Field(min_length=1, max_length=128, description="约束类型。")
    constraint_summary: str = Field(
        min_length=1,
        max_length=1024,
        description="约束摘要。",
    )
    evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="支持该约束的证据卡 ID。",
    )
    hard_boundary: bool = Field(default=False, description="是否硬边界。")


class PersonalizationPlanDto(NonmedicalPetCareAgentDto):
    """当前非医疗建议可个性化到什么程度。"""

    personalization_level: PersonalizationLevel = Field(description="个性化程度。")
    applied_factors: list[PersonalizationFactorDto] = Field(
        default_factory=list,
        description="本轮实际采用的个性化因子。",
    )
    unavailable_factors: list[str] = Field(
        default_factory=list,
        description="缺失且不得编造的个性化因子。",
    )
    assumption_guards: list[str] = Field(
        default_factory=list,
        description="避免模型自行假设的约束。",
    )


class RagUsageSummaryDto(NonmedicalPetCareAgentDto):
    """NonmedicalPetCareAgent 本轮 RAG 使用摘要。"""

    rag_invoked: bool = Field(default=False, description="本轮是否调用 RAG。")
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    query_hashes: list[str] = Field(default_factory=list, description="query 哈希。")
    degraded: bool = Field(default=False, description="RAG 是否降级。")
    degraded_reasons: list[str] = Field(
        default_factory=list,
        description="RAG 降级原因。",
    )
    cache_hit_count: int = Field(default=0, ge=0, description="缓存命中次数。")


class SafetySelfCheckSummaryDto(NonmedicalPetCareAgentDto):
    """非医疗建议草稿安全性与实用性自检摘要。"""

    passed: bool = Field(default=True, description="自检是否通过。")
    risk_flags: list[str] = Field(default_factory=list, description="风险标记。")
    extreme_diet_detected: bool = Field(default=False, description="是否检出极端饮食。")
    punitive_training_detected: bool = Field(
        default=False,
        description="是否检出惩罚式训练。",
    )
    medical_signal_ignored: bool = Field(
        default=False,
        description="是否疑似忽略医学信号。",
    )
    medication_boundary_detected: bool = Field(
        default=False,
        description="是否检出处方级用药越界。",
    )
    overpromise_detected: bool = Field(default=False, description="是否检出过度承诺。")
    personalization_hallucination_detected: bool = Field(
        default=False,
        description="是否检出个性化字段幻觉。",
    )


class NonmedicalTracePatchDto(NonmedicalPetCareAgentDto):
    """NonmedicalPetCareAgent 可写入 L2 逻辑链的 trace patch。"""

    nonmedical_agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="非医疗 Agent 业务版本。",
    )
    planner_version: str = Field(
        min_length=1,
        max_length=128,
        description="建议规划策略版本。",
    )
    writer_version: str = Field(
        min_length=1,
        max_length=128,
        description="建议写作策略版本。",
    )
    selected_dimensions: list[AdviceDimensionCode] = Field(
        default_factory=list,
        description="本轮选中的建议维度。",
    )
    consumed_signal_ids: list[str] = Field(
        default_factory=list,
        description="已消费的输入安全信号 ID。",
    )
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="本轮降级标记。",
    )


class NonmedicalAdviceDraftDto(NonmedicalPetCareAgentDto):
    """NonmedicalPetCareAgent 唯一对外业务结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前宠物 ID。",
    )
    status: NonmedicalDraftStatus = Field(description="草稿状态。")
    draft_response: str = Field(
        min_length=1,
        max_length=32768,
        description="待输出安全审查的非医疗建议草稿正文。",
    )
    draft_response_ref: str = Field(
        min_length=1,
        max_length=256,
        description="草稿引用 ID。",
    )
    advice_plan: AdvicePlanDto = Field(description="建议维度计划。")
    advice_constraints: list[AdviceConstraintDto] = Field(
        default_factory=list,
        description="证据或规则约束摘要。",
    )
    personalization_plan: PersonalizationPlanDto = Field(description="个性化计划。")
    rag_summary: RagUsageSummaryDto = Field(description="RAG 使用摘要。")
    self_check: SafetySelfCheckSummaryDto = Field(description="安全实用性自检摘要。")
    trace_patch: NonmedicalTracePatchDto = Field(description="非医疗 trace patch。")
    trace_delivery_status: NonmedicalTraceWriteStatus = Field(
        default=NonmedicalTraceWriteStatus.SKIPPED,
        description="trace patch 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_ready_draft(self) -> "NonmedicalAdviceDraftDto":
        """校验可用草稿必须保留建议约束并通过轻量自检。

        :return: 已通过关系校验的非医疗草稿。
        :raises ValueError: 当可用草稿缺少建议约束或自检未通过时抛出。
        """

        if self.status is NonmedicalDraftStatus.DRAFT_READY:
            if not self.advice_constraints:
                raise ValueError("DRAFT_READY 非医疗草稿必须包含 advice_constraints")
            if not self.self_check.passed:
                raise ValueError("DRAFT_READY 非医疗草稿必须通过 self_check")
        return self


class NonmedicalTraceRecordDto(NonmedicalPetCareAgentDto):
    """可提交给 LogicTraceStore 的非医疗脱敏 trace 摘要。"""

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
    status: NonmedicalDraftStatus = Field(description="草稿状态。")
    trace_patch: NonmedicalTracePatchDto = Field(description="非医疗 trace patch。")
    constraint_count: int = Field(ge=0, description="建议约束数量。")
    rag_invoked: bool = Field(description="本轮是否调用 RAG。")
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


class NonmedicalTraceWriteResultDto(NonmedicalPetCareAgentDto):
    """NonmedicalPetCareAgent trace patch 写入结果。"""

    status: NonmedicalTraceWriteStatus = Field(description="trace 写入状态。")
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
    "AdviceConstraintDto",
    "AdviceDimensionDto",
    "AdvicePlanDto",
    "EvidenceCardDto",
    "EvidenceHintDto",
    "InputSafetySignalDto",
    "JsonMap",
    "KnowledgeRetrievalPlanDto",
    "NonmedicalAdviceDraftDto",
    "NonmedicalAdviceRequestDto",
    "NonmedicalPetCareAgentDto",
    "NonmedicalRagResultDto",
    "NonmedicalTracePatchDto",
    "NonmedicalTraceRecordDto",
    "NonmedicalTraceWriteResultDto",
    "PersonalizationFactorDto",
    "PersonalizationPlanDto",
    "PetCareBriefDto",
    "RagUsageSummaryDto",
    "RetrievalFacetDto",
    "SafetySelfCheckSummaryDto",
)
