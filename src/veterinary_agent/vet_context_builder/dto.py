##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/dto.py
# 作用: 定义 VetContextBuilder 请求、来源结果、事实账本、prompt 块、压缩审计与 trace DTO。
# 边界: 仅承载严格结构化数据和跨字段契约，不读取外部来源、不执行裁剪或持久化。
##################################################################################################

from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from veterinary_agent.vet_context_builder.enums import (
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextFactState,
    ContextSourceFreshness,
    ContextSourceStatus,
    ContextSourceType,
    ContextTraceWriteStatus,
    VetAuditTier,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockPriority,
    VetPromptBlockType,
)

JsonMap: TypeAlias = dict[str, JsonValue]


class VetContextBuilderDto(BaseModel):
    """VetContextBuilder DTO 严格模型基类。"""

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


class ContextSourceRefDto(VetContextBuilderDto):
    """进入上下文编译流程的来源引用。"""

    source_type: ContextSourceType = Field(description="来源类型。")
    source_id: str = Field(min_length=1, max_length=256, description="来源对象 ID。")
    pet_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="宠物级来源绑定的宠物 ID；主人级来源可为空。",
    )
    version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="来源版本或更新时间版本。",
    )
    freshness: ContextSourceFreshness = Field(
        default=ContextSourceFreshness.UNKNOWN,
        description="来源新鲜度。",
    )
    status: ContextSourceStatus = Field(
        default=ContextSourceStatus.AVAILABLE,
        description="来源读取或校验状态。",
    )


class ContextFactDto(VetContextBuilderDto):
    """尚未完成优先级合并的上下文事实。"""

    key: str = Field(min_length=1, max_length=128, description="稳定事实键。")
    value: JsonValue = Field(description="JSON 可序列化事实值。")
    source_ref: ContextSourceRefDto = Field(description="事实来源引用。")
    confirmed: bool = Field(default=True, description="事实是否已得到确认。")
    observed_at: datetime | None = Field(
        default=None,
        description="事实观测或确认时间。",
    )


class ContextMessageDto(VetContextBuilderDto):
    """经来源适配器规范化的近期消息。"""

    message_id: str = Field(min_length=1, max_length=256, description="消息 ID。")
    pet_id: str = Field(min_length=1, max_length=128, description="消息宠物 ID。")
    role: Literal["user", "assistant", "system"] = Field(description="消息角色。")
    content: str = Field(max_length=32768, description="消息正文。")
    sequence_no: int = Field(ge=1, description="会话内消息序号。")
    source_ref: ContextSourceRefDto = Field(description="消息来源引用。")

    @model_validator(mode="after")
    def _validate_message_source(self) -> "ContextMessageDto":
        """校验消息与来源引用的类型和宠物边界一致。

        :return: 已通过来源关系校验的近期消息。
        :raises ValueError: 当来源不是 conversation 或宠物 ID 不一致时抛出。
        """

        if self.source_ref.source_type is not ContextSourceType.CONVERSATION:
            raise ValueError("近期消息来源类型必须为 conversation")
        if self.source_ref.pet_id != self.pet_id:
            raise ValueError("近期消息 pet_id 必须与来源引用一致")
        return self


class ContextSummaryDto(VetContextBuilderDto):
    """经确认的资料或记忆摘要。"""

    summary_id: str = Field(min_length=1, max_length=256, description="摘要 ID。")
    summary_type: str = Field(min_length=1, max_length=128, description="摘要类型。")
    content: str = Field(min_length=1, max_length=16384, description="受控摘要正文。")
    confirmed: bool = Field(default=True, description="摘要是否已得到确认。")
    source_ref: ContextSourceRefDto = Field(description="摘要来源引用。")


class SessionContextStateDto(VetContextBuilderDto):
    """供上下文编译使用的 session 短期状态快照。"""

    pet_id: str = Field(min_length=1, max_length=128, description="状态宠物 ID。")
    current_complaint_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="当前主诉类型。",
    )
    slot_progress: JsonMap = Field(default_factory=dict, description="槽位进度摘要。")
    rolling_summary_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="rolling summary 引用。",
    )
    checkpoint_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="提供该状态的 checkpoint ID。",
    )
    checkpoint_version: int = Field(default=0, ge=0, description="checkpoint 版本。")
    source_ref: ContextSourceRefDto = Field(description="session 状态来源引用。")

    @model_validator(mode="after")
    def _validate_session_source(self) -> "SessionContextStateDto":
        """校验 session 状态与 checkpoint 来源引用一致。

        :return: 已通过来源关系校验的 session 状态。
        :raises ValueError: 当来源不是 checkpoint 或宠物 ID 不一致时抛出。
        """

        if self.source_ref.source_type is not ContextSourceType.CHECKPOINT:
            raise ValueError("session 状态来源类型必须为 checkpoint")
        if self.source_ref.pet_id != self.pet_id:
            raise ValueError("session 状态 pet_id 必须与来源引用一致")
        return self


class ContextSourceLoadRequestDto(VetContextBuilderDto):
    """上下文来源端口统一读取请求。"""

    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    current_pet_id: str = Field(
        min_length=1,
        max_length=128,
        description="PetSessionPolicy 确认的当前宠物 ID。",
    )
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    params_version: str = Field(min_length=1, max_length=128, description="参数版本。")
    recent_message_limit: int = Field(ge=1, le=100, description="近期消息读取上限。")


class ContextSourceReadResultDto(VetContextBuilderDto):
    """单个上下文来源的标准化读取结果。"""

    source_type: ContextSourceType = Field(description="来源类型。")
    status: ContextSourceStatus = Field(description="来源读取状态。")
    source_refs: list[ContextSourceRefDto] = Field(
        default_factory=list,
        description="本次读取涉及的来源引用。",
    )
    facts: list[ContextFactDto] = Field(
        default_factory=list,
        description="来源提供的结构化事实。",
    )
    messages: list[ContextMessageDto] = Field(
        default_factory=list,
        description="来源提供的近期消息。",
    )
    summaries: list[ContextSummaryDto] = Field(
        default_factory=list,
        description="来源提供的受控摘要。",
    )
    session_state: SessionContextStateDto | None = Field(
        default=None,
        description="来源提供的 session 状态。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="来源降级时的稳定错误码。",
    )
    detail: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="不含业务正文的来源降级说明。",
    )

    @model_validator(mode="after")
    def _validate_result_source_types(self) -> "ContextSourceReadResultDto":
        """校验来源结果内全部条目均属于声明来源类型。

        :return: 已通过来源类型关系校验的读取结果。
        :raises ValueError: 当引用、事实、消息、摘要或 session 状态来源类型不一致时抛出。
        """

        nested_refs = [
            *self.source_refs,
            *(fact.source_ref for fact in self.facts),
            *(message.source_ref for message in self.messages),
            *(summary.source_ref for summary in self.summaries),
        ]
        if self.session_state is not None:
            nested_refs.append(self.session_state.source_ref)
        if any(
            source_ref.source_type is not self.source_type for source_ref in nested_refs
        ):
            raise ValueError("来源结果内条目的 source_type 必须与结果声明一致")
        if (
            self.session_state is not None
            and self.source_type is not ContextSourceType.CHECKPOINT
        ):
            raise ValueError("只有 checkpoint 来源可以提供 session_state")
        return self


class ResolvedContextFactDto(VetContextBuilderDto):
    """完成来源优先级和冲突处理后的事实账本条目。"""

    key: str = Field(min_length=1, max_length=128, description="稳定事实键。")
    value: JsonValue = Field(description="最终采用的 JSON 事实值。")
    state: ContextFactState = Field(description="事实当前状态。")
    source_refs: list[ContextSourceRefDto] = Field(
        min_length=1,
        description="支持该事实决策的来源引用。",
    )
    conflict: bool = Field(default=False, description="同优先级来源是否存在冲突。")


class SlotCoverageDto(VetContextBuilderDto):
    """当前子任务的槽位覆盖结果。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    known_slots: JsonMap = Field(default_factory=dict, description="已知槽位和值。")
    missing_slots: list[str] = Field(default_factory=list, description="缺失槽位。")
    stale_slots: JsonMap = Field(default_factory=dict, description="过期槽位和值。")
    pending_confirmation_slots: JsonMap = Field(
        default_factory=dict,
        description="待用户确认槽位和值。",
    )


class VetPromptBlockDto(VetContextBuilderDto):
    """VetContextBuilder 输出的受控 prompt 块。"""

    block_id: str = Field(min_length=1, max_length=256, description="稳定块 ID。")
    block_type: VetPromptBlockType = Field(description="受控块类型。")
    priority: VetPromptBlockPriority = Field(description="块保留优先级。")
    required: bool = Field(description="当前策略下该块是否必须保留。")
    content_ref_or_text: str = Field(min_length=1, description="受控块正文或引用文本。")
    content_hash: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$",
        description="块正文 SHA-256 hash。",
    )
    token_estimate: int = Field(ge=0, description="块 token 估算。")
    source_refs: list[ContextSourceRefDto] = Field(
        default_factory=list,
        description="块关联来源引用。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通块元信息。")


class CompressionAuditDto(VetContextBuilderDto):
    """上下文裁剪、块丢弃和 P0 再注入摘要。"""

    compression_strategy: ContextCompressionStrategy = Field(description="压缩策略。")
    token_budget: int = Field(ge=1, description="应用安全余量后的 token 预算。")
    estimated_tokens: int = Field(ge=0, description="最终块 token 估算总量。")
    trim_applied: bool = Field(description="是否发生块裁剪。")
    p0_reinjected: bool = Field(description="裁剪后是否重新注入 P0 块。")
    included_block_ids: list[str] = Field(description="最终保留的块 ID。")
    dropped_block_ids: list[str] = Field(
        default_factory=list, description="丢弃块 ID。"
    )
    dropped_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="块 ID 到丢弃原因的映射。",
    )
    truncated_block_ids: list[str] = Field(
        default_factory=list,
        description="在块编译阶段已执行有界裁剪的块 ID。",
    )
    fallback_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="进入最小降级路径时的稳定路径名。",
    )

    @model_validator(mode="after")
    def _validate_audit_relations(self) -> "CompressionAuditDto":
        """校验压缩审计中的预算与块集合关系。

        :return: 已通过关系校验的压缩审计。
        :raises ValueError: 当估算超预算、块集合重叠或丢弃原因不完整时抛出。
        """

        included = set(self.included_block_ids)
        dropped = set(self.dropped_block_ids)
        if self.estimated_tokens > self.token_budget:
            raise ValueError("estimated_tokens 不得大于 token_budget")
        if included.intersection(dropped):
            raise ValueError("included_block_ids 与 dropped_block_ids 不得重叠")
        if dropped.difference(self.dropped_reasons):
            raise ValueError("每个 dropped_block_id 必须具有 dropped_reasons")
        return self


class VetContextBuildRequestDto(VetContextBuilderDto):
    """单个兽医子任务的上下文构建请求。"""

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
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    task_type: str = Field(min_length=1, max_length=128, description="受控任务类型。")
    normalized_query: str = Field(
        min_length=1,
        max_length=32768,
        description="子任务规范化文本。",
    )
    generation_profile: VetGenerationProfile | None = Field(
        default=None,
        description="三生成剖面；纯非医疗任务可为空。",
    )
    route: str = Field(min_length=1, max_length=128, description="输入安全路由。")
    executor_key: VetExecutorKey = Field(description="实际业务执行器。")
    compression_strategy: ContextCompressionStrategy = Field(description="压缩策略。")
    audit_tier: VetAuditTier = Field(default=VetAuditTier.C, description="审计等级。")
    assessment_summary: JsonMap = Field(
        default_factory=dict,
        description="输入安全评估的受控摘要。",
    )
    observed_facts: list[ContextFactDto] = Field(
        default_factory=list,
        description="前置结构化抽取器提供的本轮事实。",
    )
    session_state_snapshot: SessionContextStateDto | None = Field(
        default=None,
        description="LangGraph 当前 business_state 投影的 session 状态。",
    )
    params_version: str = Field(
        min_length=1, max_length=128, description="业务参数版本。"
    )
    config_snapshot_id: str = Field(
        min_length=1,
        max_length=256,
        description="RuntimeConfig 快照 ID。",
    )

    @model_validator(mode="after")
    def _validate_execution_profile(self) -> "VetContextBuildRequestDto":
        """校验执行器、生成剖面、路由和压缩策略组合。

        :return: 已通过执行关系校验的构建请求。
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
            if self.route != "safety_trigger":
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


class VetContextBundleDto(VetContextBuilderDto):
    """VetContextBuilder 唯一标准输出。"""

    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    current_pet_id: str = Field(
        min_length=1, max_length=128, description="当前宠物 ID。"
    )
    generation_profile: VetGenerationProfile | None = Field(description="生成剖面。")
    executor_key: VetExecutorKey = Field(description="实际业务执行器。")
    prompt_blocks: list[VetPromptBlockDto] = Field(
        min_length=1,
        description="供生成 Agent 消费的上下文块。",
    )
    fact_ledger: list[ResolvedContextFactDto] = Field(
        default_factory=list,
        description="当前子任务事实账本。",
    )
    slot_coverage: SlotCoverageDto = Field(description="当前子任务槽位覆盖。")
    source_refs: list[ContextSourceRefDto] = Field(description="全部来源引用摘要。")
    compression_audit: CompressionAuditDto = Field(description="压缩审计摘要。")
    status: ContextBuildStatus = Field(description="上下文构建状态。")
    degraded_reasons: list[str] = Field(
        default_factory=list,
        description="稳定降级原因列表。",
    )
    core_fact_snapshot_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="命中的核心事实快照版本。",
    )
    adapter_invoked: Literal[True] = Field(
        default=True,
        description="证明后续生成链已经过领域上下文适配层。",
    )
    trace_delivery_status: ContextTraceWriteStatus = Field(
        default=ContextTraceWriteStatus.SKIPPED,
        description="上下文摘要留痕状态。",
    )

    @model_validator(mode="after")
    def _validate_bundle_relations(self) -> "VetContextBundleDto":
        """校验 bundle 任务、块列表与压缩审计之间的关系。

        :return: 已通过关系校验的上下文 bundle。
        :raises ValueError: 当槽位任务不一致、块 ID 重复或审计未覆盖最终块时抛出。
        """

        block_ids = [block.block_id for block in self.prompt_blocks]
        if self.slot_coverage.task_id != self.task_id:
            raise ValueError("slot_coverage.task_id 必须与 bundle.task_id 一致")
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("prompt_blocks 不得包含重复 block_id")
        if block_ids != self.compression_audit.included_block_ids:
            raise ValueError("compression_audit 必须按顺序覆盖最终 prompt_blocks")
        return self


class ContextTraceRecordDto(VetContextBuilderDto):
    """可写入 LogicTraceStore 的上下文构建脱敏摘要。"""

    schema_version: Literal["vet.context-builder.trace.v1"] = Field(
        default="vet.context-builder.trace.v1",
        description="上下文构建 trace schema 版本。",
    )
    request_id: str = Field(min_length=1, max_length=128, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, max_length=128, description="全链路追踪 ID。")
    run_id: str = Field(min_length=1, max_length=128, description="图运行 ID。")
    session_id: str = Field(min_length=1, max_length=128, description="会话 ID。")
    user_id: str = Field(min_length=1, max_length=128, description="用户 ID。")
    pet_id: str = Field(min_length=1, max_length=128, description="当前宠物 ID。")
    task_id: str = Field(min_length=1, max_length=128, description="子任务 ID。")
    audit_tier: VetAuditTier = Field(description="审计等级。")
    generation_profile: VetGenerationProfile | None = Field(description="生成剖面。")
    executor_key: VetExecutorKey = Field(description="实际业务执行器。")
    status: ContextBuildStatus = Field(description="构建状态。")
    compression_audit: CompressionAuditDto = Field(description="压缩审计摘要。")
    source_types: list[ContextSourceType] = Field(description="参与构建的来源类型。")
    block_hashes: dict[str, str] = Field(description="块 ID 到正文 hash 的映射。")
    degraded_reasons: list[str] = Field(description="构建降级原因。")
    params_version: str = Field(
        min_length=1, max_length=128, description="业务参数版本。"
    )
    config_snapshot_id: str = Field(
        min_length=1, max_length=256, description="配置快照 ID。"
    )


class ContextTraceWriteResultDto(VetContextBuilderDto):
    """上下文构建摘要留痕结果。"""

    status: ContextTraceWriteStatus = Field(description="trace 写入状态。")
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
    "CompressionAuditDto",
    "ContextFactDto",
    "ContextMessageDto",
    "ContextSourceLoadRequestDto",
    "ContextSourceReadResultDto",
    "ContextSourceRefDto",
    "ContextSummaryDto",
    "ContextTraceRecordDto",
    "ContextTraceWriteResultDto",
    "JsonMap",
    "ResolvedContextFactDto",
    "SessionContextStateDto",
    "SlotCoverageDto",
    "VetContextBuildRequestDto",
    "VetContextBuilderDto",
    "VetContextBundleDto",
    "VetPromptBlockDto",
)
