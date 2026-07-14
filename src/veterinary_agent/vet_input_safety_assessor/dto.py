##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/dto.py
# 作用: 定义 VetInputSafetyAssessor 请求、信号、候选、裁决、结果、trace 与端口摘要 DTO。
# 边界: 仅承载严格结构化数据和跨字段契约，不调用弱依赖、不执行业务裁决、不写入 LogicTraceStore。
##################################################################################################

from hashlib import sha256
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetAuditTier,
    VetExecutorKey,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    AssessmentMethod,
    AssessmentStatus,
    DisambiguationMethod,
    RouteLabel,
    SafetySignalCode,
    SignalSource,
    SignalStrength,
    VetInputAssessmentTraceWriteStatus,
    VetIntent,
)
from veterinary_agent.vet_task_decomposer import VetSubTaskDto

JsonMap: TypeAlias = dict[str, object]


class VetInputSafetyAssessorDto(BaseModel):
    """VetInputSafetyAssessor DTO 严格模型基类。"""

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


class LightweightAssessmentContextDto(VetInputSafetyAssessorDto):
    """输入安全评估使用的轻量上下文引用。"""

    recent_session_summary_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="近期会话摘要引用。",
    )
    previous_intent: VetIntent | None = Field(
        default=None,
        description="上一轮稳定意图。",
    )
    previous_generation_profile: VetGenerationProfile | None = Field(
        default=None,
        description="上一轮稳定生成剖面。",
    )
    active_complaint_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="当前活跃主诉类型。",
    )
    recent_signal_refs: list[str] = Field(
        default_factory=list,
        description="近期安全信号引用。",
    )


class InputSafetySignalDto(VetInputSafetyAssessorDto):
    """输入侧安全信号事实。"""

    signal_id: str = Field(min_length=1, max_length=128, description="信号 ID。")
    code: SafetySignalCode = Field(description="受控安全信号码。")
    strength: SignalStrength = Field(description="信号强度。")
    matched_text_hash: str = Field(
        min_length=1,
        max_length=128,
        description="命中文本片段 hash。",
    )
    normalized_concept: str = Field(
        min_length=1,
        max_length=256,
        description="归一化风险概念。",
    )
    source: SignalSource = Field(description="信号来源。")
    confidence: float = Field(ge=0.0, le=1.0, description="信号置信度。")
    dictionary_version: str = Field(
        min_length=1,
        max_length=128,
        description="词库或抽取器版本。",
    )


class SemanticRouteCandidateDto(VetInputSafetyAssessorDto):
    """语义路由候选。"""

    route_label: str = Field(min_length=1, max_length=128, description="候选标签。")
    score: float = Field(ge=0.0, le=1.0, description="候选分数。")
    margin: float = Field(ge=0.0, le=1.0, description="首位与次位间隔。")
    router_version: str = Field(
        min_length=1,
        max_length=128,
        description="语义路由器版本。",
    )


class StructuredSignalExtractionSummaryDto(VetInputSafetyAssessorDto):
    """本地结构化抽取摘要。"""

    extractor_version: str = Field(
        min_length=1,
        max_length=128,
        description="本地抽取器版本。",
    )
    extracted_concept_types: list[str] = Field(
        default_factory=list,
        description="已抽取的受控概念类型。",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="抽取摘要置信度。")
    unavailable: bool = Field(default=False, description="抽取器是否不可用。")


class LlmArbitrationResultDto(VetInputSafetyAssessorDto):
    """低置信 LLM 仲裁结构化结果。"""

    intent: VetIntent = Field(description="仲裁意图。")
    intent_confidence: float = Field(ge=0.0, le=1.0, description="仲裁置信度。")
    route: RouteLabel = Field(description="仲裁路由。")
    generation_profile: VetGenerationProfile | None = Field(
        default=None,
        description="仲裁三生成剖面；非三剖面为空。",
    )
    executor_key: VetExecutorKey = Field(description="仲裁实际执行器。")
    compression_strategy: ContextCompressionStrategy = Field(
        description="仲裁压缩策略。"
    )
    reason_code: str = Field(
        default="llm_arbitration",
        min_length=1,
        max_length=128,
        description="仲裁原因码。",
    )


class ResolvedProfileDecisionDto(VetInputSafetyAssessorDto):
    """最终剖面和执行器裁决。"""

    intent: VetIntent = Field(description="最终意图。")
    intent_confidence: float = Field(ge=0.0, le=1.0, description="最终意图置信度。")
    generation_profile: VetGenerationProfile | None = Field(
        default=None,
        description="三生成剖面；非三剖面为空。",
    )
    route: RouteLabel = Field(description="输入安全路由。")
    executor_key: VetExecutorKey = Field(description="实际业务执行器。")
    compression_strategy: ContextCompressionStrategy = Field(
        description="上下文压缩策略。"
    )
    disambiguation_method: DisambiguationMethod = Field(description="消歧方法。")
    audit_tier_floor: VetAuditTier = Field(description="审计等级下限。")
    method: AssessmentMethod = Field(description="最终采用的评估方法。")
    fallback_used: bool = Field(description="是否使用保守降级裁决。")
    reason_code: str = Field(min_length=1, max_length=128, description="最终原因码。")


class AssessmentTraceSummaryDto(VetInputSafetyAssessorDto):
    """输入安全评估 trace 摘要。"""

    assessor_version: str = Field(
        min_length=1,
        max_length=128,
        description="评估器业务版本。",
    )
    method: AssessmentMethod = Field(description="本次采用的评估方法。")
    llm_unavailable: bool = Field(description="低置信 LLM 仲裁是否不可用。")
    semantic_router_unavailable: bool = Field(description="语义路由是否不可用。")
    local_extractor_unavailable: bool = Field(description="本地抽取器是否不可用。")
    fallback_used: bool = Field(description="是否使用保守默认策略。")
    signal_codes: list[SafetySignalCode] = Field(
        default_factory=list,
        description="本次检出的信号码列表。",
    )
    final_decision_reason_code: str = Field(
        min_length=1,
        max_length=128,
        description="最终判决原因码。",
    )


class VetInputAssessmentRequestDto(VetInputSafetyAssessorDto):
    """单个子任务输入安全评估请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    task: VetSubTaskDto = Field(description="待评估子任务。")
    light_context: LightweightAssessmentContextDto = Field(
        default_factory=LightweightAssessmentContextDto,
        description="轻量消歧上下文。",
    )
    original_user_message_hash: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="本轮用户原文 hash。",
    )
    params_version: str = Field(min_length=1, max_length=128, description="参数版本。")
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="RuntimeConfig 快照 ID。",
    )


class BatchVetInputAssessmentRequestDto(VetInputSafetyAssessorDto):
    """当前轮子任务输入安全批量评估请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    tasks: list[VetSubTaskDto] = Field(
        min_length=1,
        description="当前轮待评估子任务列表。",
    )
    light_context: LightweightAssessmentContextDto = Field(
        default_factory=LightweightAssessmentContextDto,
        description="轻量消歧上下文。",
    )
    original_user_message: str = Field(
        default="",
        max_length=100000,
        description="本轮用户原文；仅用于 hash 与兜底信号回查。",
    )
    params_version: str = Field(min_length=1, max_length=128, description="参数版本。")
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="RuntimeConfig 快照 ID。",
    )

    @model_validator(mode="after")
    def _validate_tasks_current_pet(self) -> "BatchVetInputAssessmentRequestDto":
        """校验全部子任务宠物归属与当前宠物一致。

        :return: 已通过宠物归属校验的批量请求。
        :raises ValueError: 当任一子任务宠物 ID 与请求当前宠物不一致时抛出。
        """

        if any(task.current_pet_id != self.current_pet_id for task in self.tasks):
            raise ValueError("全部 VetSubTask.current_pet_id 必须等于 current_pet_id")
        return self


class VetInputAssessmentResultDto(VetInputSafetyAssessorDto):
    """单个子任务输入安全评估结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1, max_length=128, description="当前宠物 ID。"
    )
    status: AssessmentStatus = Field(description="评估状态。")
    signals: list[InputSafetySignalDto] = Field(
        default_factory=list,
        description="输入侧安全信号列表。",
    )
    intent: VetIntent = Field(description="最终意图。")
    intent_confidence: float = Field(ge=0.0, le=1.0, description="意图置信度。")
    generation_profile: VetGenerationProfile | None = Field(
        default=None,
        description="三生成剖面；非三剖面为空。",
    )
    route: RouteLabel = Field(description="输入安全路由。")
    executor_key: VetExecutorKey = Field(description="实际业务执行器。")
    compression_strategy: ContextCompressionStrategy = Field(
        description="上下文压缩策略。"
    )
    disambiguation_method: DisambiguationMethod = Field(description="消歧方法。")
    audit_tier_floor: VetAuditTier = Field(description="审计等级下限。")
    assessment_summary: JsonMap = Field(
        default_factory=dict,
        description="供下游消费的输入安全受控摘要。",
    )
    trace_summary: AssessmentTraceSummaryDto = Field(description="trace 摘要。")
    trace_delivery_status: VetInputAssessmentTraceWriteStatus = Field(
        default=VetInputAssessmentTraceWriteStatus.SKIPPED,
        description="评估摘要 trace 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_execution_profile(self) -> "VetInputAssessmentResultDto":
        """校验执行器、剖面、路由和压缩策略组合。

        :return: 已通过组合关系校验的评估结果。
        :raises ValueError: 当执行器、剖面、路由或压缩策略组合非法时抛出。
        """

        standard_executors = {
            VetExecutorKey.STANDARD_CONSULTATION,
            VetExecutorKey.LAB_REPORT_INTERPRETATION,
        }
        if self.executor_key in standard_executors:
            if self.generation_profile is not VetGenerationProfile.STANDARD:
                raise ValueError("standard 执行器必须使用 standard 生成剖面")
            if self.compression_strategy is not ContextCompressionStrategy.SINGLE_FULL:
                raise ValueError("standard 执行器必须使用 single_full 压缩策略")
        elif self.executor_key is VetExecutorKey.EDUCATION:
            if self.generation_profile is not VetGenerationProfile.EDUCATION:
                raise ValueError("education 执行器必须使用 education 生成剖面")
            if (
                self.compression_strategy
                is not ContextCompressionStrategy.EDUCATION_LIGHT
            ):
                raise ValueError("education 执行器必须使用 education_light 压缩策略")
        elif self.executor_key is VetExecutorKey.SAFETY_TRIGGER:
            if self.generation_profile is not VetGenerationProfile.SAFETY_TRIGGER:
                raise ValueError("safety_trigger 执行器必须使用同名生成剖面")
            if (
                self.compression_strategy
                is not ContextCompressionStrategy.SAFETY_MINIMAL
            ):
                raise ValueError(
                    "safety_trigger 执行器必须使用 safety_minimal 压缩策略"
                )
            if self.route is not RouteLabel.SAFETY_TRIGGER:
                raise ValueError("safety_trigger 生成剖面必须使用安全路由")
        else:
            if self.generation_profile is not None:
                raise ValueError("非三剖面执行器的 generation_profile 必须为空")
            if (
                self.compression_strategy
                is not ContextCompressionStrategy.EDUCATION_LIGHT
            ):
                raise ValueError("轻量非医疗执行器必须使用 education_light 压缩策略")
        return self


class BatchVetInputAssessmentResultDto(VetInputSafetyAssessorDto):
    """当前轮输入安全批量评估结果。"""

    results: list[VetInputAssessmentResultDto] = Field(
        min_length=1,
        description="按子任务输出的独立评估结果。",
    )
    status: AssessmentStatus = Field(description="批量评估状态。")
    trace_delivery_status: VetInputAssessmentTraceWriteStatus = Field(
        default=VetInputAssessmentTraceWriteStatus.SKIPPED,
        description="批量评估摘要 trace 写入状态。",
    )

    @model_validator(mode="after")
    def _validate_results_have_unique_task_ids(
        self,
    ) -> "BatchVetInputAssessmentResultDto":
        """校验批量结果中的子任务 ID 唯一。

        :return: 已通过唯一性校验的批量结果。
        :raises ValueError: 当批量结果包含重复 task_id 时抛出。
        """

        task_ids = [result.task_id for result in self.results]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("批量评估结果不得包含重复 task_id")
        return self


class VetInputAssessmentTraceRecordDto(VetInputSafetyAssessorDto):
    """可写入 LogicTraceStore 的输入安全评估脱敏摘要记录。"""

    schema_version: Literal["vet.input-safety.trace.v1"] = Field(
        default="vet.input-safety.trace.v1",
        description="输入安全 trace 摘要结构版本。",
    )
    request_id: str = Field(min_length=1, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, description="图运行 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    original_user_message_hash: str | None = Field(
        default=None,
        min_length=1,
        description="本轮用户原文 hash。",
    )
    result_summaries: list[JsonMap] = Field(
        min_length=1,
        description="按子任务整理的脱敏评估摘要。",
    )
    params_version: str = Field(min_length=1, description="业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="配置快照 ID。")


class VetInputSafetyTraceWriteResultDto(VetInputSafetyAssessorDto):
    """输入安全评估 trace 写入结果。"""

    status: VetInputAssessmentTraceWriteStatus = Field(description="trace 写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="trace 写入降级时的稳定错误码。",
    )
    retryable: bool = Field(default=False, description="是否允许稍后补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="不含业务正文的 trace 写入状态说明。",
    )


def build_input_text_hash(text: str) -> str:
    """构建输入文本片段的稳定 hash。

    :param text: 需要计算摘要的文本。
    :return: sha256 十六进制摘要。
    """

    return sha256(text.encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "AssessmentTraceSummaryDto",
    "BatchVetInputAssessmentRequestDto",
    "BatchVetInputAssessmentResultDto",
    "InputSafetySignalDto",
    "JsonMap",
    "LightweightAssessmentContextDto",
    "LlmArbitrationResultDto",
    "ResolvedProfileDecisionDto",
    "SemanticRouteCandidateDto",
    "StructuredSignalExtractionSummaryDto",
    "VetInputAssessmentRequestDto",
    "VetInputAssessmentResultDto",
    "VetInputAssessmentTraceRecordDto",
    "VetInputSafetyAssessorDto",
    "VetInputSafetyTraceWriteResultDto",
    "build_input_text_hash",
)
