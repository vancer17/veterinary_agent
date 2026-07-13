##################################################################################################
# 文件: src/veterinary_agent/education_agent/dto.py
# 作用: 定义 EducationAgent 请求、科普 brief、解释计划、RAG 计划、证据卡、草稿与 trace DTO。
# 边界: 仅承载严格结构化数据，不执行模型调用、RAG 检索、输出安全审查或 checkpoint 写入。
##################################################################################################

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.education_agent.enums import (
    EducationDraftStatus,
    EducationRetrievalPurpose,
    EducationTraceWriteStatus,
    EvidenceSufficiencyStatus,
    ExplanationDimensionCode,
)
from veterinary_agent.vet_context_builder import VetContextBundleDto

JsonMap: TypeAlias = dict[str, object]


class EducationAgentDto(BaseModel):
    """EducationAgent DTO 严格模型基类。"""

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


class EducationGenerationRequestDto(EducationAgentDto):
    """生成科普草稿的应用内请求。"""

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
        description="输入安全评估判定出的生成剖面。",
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


class EducationBriefDto(EducationAgentDto):
    """本轮科普主轴和可用上下文视图。"""

    main_question: str = Field(min_length=1, max_length=2048, description="当前问题。")
    main_axis: str = Field(min_length=1, max_length=256, description="科普主轴。")
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="物种适配范围。",
    )
    continue_recent_topic: bool = Field(
        default=False,
        description="是否自然接续近期话题。",
    )
    allowed_context_refs: list[str] = Field(
        default_factory=list,
        description="允许用于科普表达适配的上下文引用。",
    )
    excluded_context_reasons: list[str] = Field(
        default_factory=list,
        description="被排除上下文及原因摘要。",
    )


class ExplanationDimensionDto(EducationAgentDto):
    """单个科普解释维度规划。"""

    dimension_code: ExplanationDimensionCode = Field(description="解释维度代码。")
    priority: int = Field(ge=1, le=100, description="维度优先级，数值越小越优先。")
    required: bool = Field(default=False, description="该维度是否必须覆盖。")
    evidence_requirement: str = Field(
        min_length=1,
        max_length=512,
        description="该维度需要的证据要求。",
    )
    prohibited_claims: list[str] = Field(
        default_factory=list,
        description="该维度下禁止生成的 claim 摘要。",
    )


class ExplanationPlanDto(EducationAgentDto):
    """动态科普解释维度规划。"""

    main_axis: str = Field(min_length=1, max_length=256, description="科普主轴。")
    dimensions: list[ExplanationDimensionDto] = Field(
        min_length=1,
        description="已选择的解释维度列表。",
    )
    generation_constraints: list[str] = Field(
        default_factory=list,
        description="写作生成约束。",
    )
    safety_boundary_hints: list[str] = Field(
        default_factory=list,
        description="需要自然嵌入正文的安全边界提示。",
    )
    citation_mode: str = Field(
        default="evidence_bound",
        min_length=1,
        max_length=128,
        description="证据引用策略模式。",
    )


class RetrievalFacetDto(EducationAgentDto):
    """一次受控 RAG 检索 facet。"""

    dimension_code: ExplanationDimensionCode = Field(description="解释维度代码。")
    retrieval_purpose: EducationRetrievalPurpose = Field(description="检索用途。")
    queries: list[str] = Field(
        min_length=1,
        description="受控检索 query 列表。",
    )
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


class EducationRetrievalPlanDto(EducationAgentDto):
    """受控科普 RAG 检索计划。"""

    plan_id: str = Field(min_length=1, max_length=128, description="检索计划 ID。")
    facets: list[RetrievalFacetDto] = Field(
        default_factory=list,
        description="检索 facet 列表。",
    )
    dosage_filter_required: bool = Field(
        default=True,
        description="是否要求药物剂量风险过滤。",
    )
    ref_range_generation_forbidden: bool = Field(
        default=True,
        description="是否禁止从 RAG 生成参考区间。",
    )


class EvidenceHintDto(EducationAgentDto):
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
    restricted: bool = Field(default=True, description="是否受限引用。")


class EducationRagResultDto(EducationAgentDto):
    """一次 EducationAgent 受控 RAG 检索结果摘要。"""

    retrieval_purpose: EducationRetrievalPurpose = Field(description="检索用途。")
    dimension_code: ExplanationDimensionCode = Field(description="解释维度代码。")
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


class EvidenceCardDto(EducationAgentDto):
    """写作 Agent 可消费的最小证据卡。"""

    evidence_card_id: str = Field(
        min_length=1,
        max_length=128,
        description="证据卡 ID。",
    )
    dimension_code: ExplanationDimensionCode = Field(description="解释维度代码。")
    supported_claim_summary: str = Field(
        min_length=1,
        max_length=1024,
        description="证据支持的 claim 摘要。",
    )
    species_scope: str = Field(
        default="unknown",
        min_length=1,
        max_length=128,
        description="证据卡适用物种范围。",
    )
    retrieval_ids: list[str] = Field(description="证据卡绑定的检索 ID。")
    source_policy: str = Field(
        default="restricted",
        min_length=1,
        max_length=128,
        description="来源策略。",
    )
    public_citable: bool = Field(default=False, description="是否可公开引用。")
    restricted: bool = Field(default=True, description="是否受限引用。")

    @model_validator(mode="after")
    def _validate_reference_binding(self) -> "EvidenceCardDto":
        """校验证据卡必须绑定稳定检索引用。

        :return: 已通过引用关系校验的证据卡。
        :raises ValueError: 当 retrieval_ids 为空时抛出。
        """

        if not self.retrieval_ids:
            raise ValueError("EvidenceCard 必须绑定至少一个 retrieval_id")
        return self


class EvidenceSufficiencyResultDto(EducationAgentDto):
    """科普证据充分性判定结果。"""

    status: EvidenceSufficiencyStatus = Field(description="证据充分性状态。")
    missing_dimensions: list[ExplanationDimensionCode] = Field(
        default_factory=list,
        description="缺少证据支持的维度。",
    )
    degraded_reasons: list[str] = Field(
        default_factory=list,
        description="证据降级原因。",
    )
    allow_full_answer: bool = Field(default=False, description="是否允许完整回答。")


class EducationContentPlanDto(EducationAgentDto):
    """科普正文内容编排计划摘要。"""

    main_axis: str = Field(min_length=1, max_length=256, description="科普主轴。")
    section_titles: list[str] = Field(
        default_factory=list,
        description="正文段落标题或主题摘要。",
    )
    selected_dimensions: list[ExplanationDimensionCode] = Field(
        default_factory=list,
        description="正文覆盖的解释维度。",
    )
    continue_recent_topic: bool = Field(
        default=False,
        description="是否自然接续近期话题。",
    )
    safety_boundary_hints: list[str] = Field(
        default_factory=list,
        description="正文嵌入的安全边界摘要。",
    )
    citation_mode: str = Field(
        default="evidence_bound",
        min_length=1,
        max_length=128,
        description="证据引用策略模式。",
    )


class EvidenceBindingDto(EducationAgentDto):
    """科普草稿声明与证据卡之间的绑定摘要。"""

    claim_id: str = Field(min_length=1, max_length=128, description="声明 ID。")
    evidence_card_ids: list[str] = Field(
        default_factory=list,
        description="支持该声明的证据卡 ID。",
    )
    retrieval_ids: list[str] = Field(
        default_factory=list,
        description="支持该声明的检索 ID。",
    )
    binding_summary: str = Field(
        min_length=1,
        max_length=512,
        description="证据绑定摘要。",
    )


class RagUsageSummaryDto(EducationAgentDto):
    """EducationAgent 本轮 RAG 使用摘要。"""

    rag_invoked: bool = Field(default=False, description="本轮是否调用 RAG。")
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    query_hashes: list[str] = Field(default_factory=list, description="query 哈希。")
    degraded: bool = Field(default=False, description="RAG 是否降级。")
    degraded_reasons: list[str] = Field(
        default_factory=list,
        description="RAG 降级原因。",
    )
    cache_hit_count: int = Field(default=0, ge=0, description="缓存命中次数。")


class GroundingCheckSummaryDto(EducationAgentDto):
    """科普草稿接地性自检摘要。"""

    passed: bool = Field(default=True, description="接地性自检是否通过。")
    risk_flags: list[str] = Field(default_factory=list, description="风险标记。")
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="疑似缺少证据支持的 claim。",
    )
    forbidden_format_detected: bool = Field(
        default=False,
        description="是否检出问诊四层等禁用格式。",
    )
    t4_risk_detected: bool = Field(default=False, description="是否检出 T4 风险。")
    reference_range_risk_detected: bool = Field(
        default=False,
        description="是否检出参考区间幻觉风险。",
    )
    restricted_source_risk_detected: bool = Field(
        default=False,
        description="是否检出不可公开引用来源风险。",
    )


class EducationTracePatchDto(EducationAgentDto):
    """EducationAgent 可写入 L2 逻辑链的 trace patch。"""

    education_agent_version: str = Field(
        min_length=1,
        max_length=128,
        description="EducationAgent 业务版本。",
    )
    planner_version: str = Field(
        min_length=1,
        max_length=128,
        description="解释规划策略版本。",
    )
    writer_version: str = Field(
        min_length=1,
        max_length=128,
        description="科普写作策略版本。",
    )
    selected_dimensions: list[ExplanationDimensionCode] = Field(
        default_factory=list,
        description="本轮选中的解释维度。",
    )
    retrieval_ids: list[str] = Field(default_factory=list, description="检索 ID。")
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="本轮降级标记。",
    )


class EducationDraftDto(EducationAgentDto):
    """EducationAgent 唯一对外业务结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="当前宠物 ID。",
    )
    status: EducationDraftStatus = Field(description="草稿状态。")
    draft_response: str = Field(
        min_length=1,
        max_length=32768,
        description="待输出安全审查的科普草稿正文。",
    )
    draft_response_ref: str = Field(
        min_length=1,
        max_length=256,
        description="草稿引用 ID。",
    )
    content_plan: EducationContentPlanDto = Field(description="内容编排计划。")
    evidence_bindings: list[EvidenceBindingDto] = Field(
        default_factory=list,
        description="证据绑定摘要。",
    )
    rag_summary: RagUsageSummaryDto = Field(description="RAG 使用摘要。")
    grounding_check: GroundingCheckSummaryDto = Field(description="接地性自检摘要。")
    trace_patch: EducationTracePatchDto = Field(description="科普 trace patch。")
    trace_delivery_status: EducationTraceWriteStatus = Field(
        default=EducationTraceWriteStatus.SKIPPED,
        description="trace patch 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_ready_draft_bindings(self) -> "EducationDraftDto":
        """校验可用草稿必须保留证据绑定与 RAG 留痕。

        :return: 已通过关系校验的科普草稿。
        :raises ValueError: 当可用草稿缺少证据绑定或 RAG 留痕时抛出。
        """

        if self.status is EducationDraftStatus.DRAFT_READY:
            if not self.evidence_bindings:
                raise ValueError("DRAFT_READY 科普草稿必须包含 evidence_bindings")
            if not self.rag_summary.rag_invoked:
                raise ValueError("DRAFT_READY 科普草稿必须记录 rag_invoked=true")
        return self


class EducationTraceRecordDto(EducationAgentDto):
    """可提交给 LogicTraceStore 的科普脱敏 trace 摘要。"""

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
    status: EducationDraftStatus = Field(description="草稿状态。")
    trace_patch: EducationTracePatchDto = Field(description="科普 trace patch。")
    evidence_binding_count: int = Field(ge=0, description="证据绑定数量。")
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


class EducationTraceWriteResultDto(EducationAgentDto):
    """EducationAgent trace patch 写入结果。"""

    status: EducationTraceWriteStatus = Field(description="trace 写入状态。")
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
    "EducationAgentDto",
    "EducationBriefDto",
    "EducationContentPlanDto",
    "EducationDraftDto",
    "EducationGenerationRequestDto",
    "EducationRagResultDto",
    "EducationRetrievalPlanDto",
    "EducationTracePatchDto",
    "EducationTraceRecordDto",
    "EducationTraceWriteResultDto",
    "EvidenceBindingDto",
    "EvidenceCardDto",
    "EvidenceHintDto",
    "EvidenceSufficiencyResultDto",
    "ExplanationDimensionDto",
    "ExplanationPlanDto",
    "GroundingCheckSummaryDto",
    "JsonMap",
    "RagUsageSummaryDto",
    "RetrievalFacetDto",
)
