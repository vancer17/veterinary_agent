##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/dto.py
# 作用: 定义 VetResponseComposer 的请求、分支状态、可发布段、发布结果和 trace DTO。
# 边界: 仅承载严格结构化数据，不执行排序、发布、存储访问或图节点适配。
##################################################################################################

from datetime import datetime
from typing import Any, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from veterinary_agent.vet_response_composer.enums import (
    ComposerPublishDecision,
    ComposerPublishStatus,
    ComposerTraceWriteStatus,
)

JsonMap: TypeAlias = dict[str, object]


class VetResponseComposerDto(BaseModel):
    """VetResponseComposer DTO 严格模型基类。"""

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


class PublishableSegmentDto(VetResponseComposerDto):
    """上游输出安全链路产出的候选可发布段。"""

    segment_id: str = Field(min_length=1, description="稳定 segment ID。")
    branch_id: str = Field(min_length=1, description="segment 所属业务分支 ID。")
    task_id: str = Field(min_length=1, description="segment 关联业务子任务 ID。")
    segment_type: str = Field(min_length=1, description="业务 segment 类型。")
    final_response: str | None = Field(
        default=None,
        min_length=1,
        description="已通过输出安全链路的最终用户可见正文。",
    )
    final_response_ref: str | None = Field(
        default=None,
        min_length=1,
        description="最终正文引用；正文由上游或 artifact 存储持有。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="可选业务分段标题。",
    )
    guard_status: str = Field(
        min_length=1,
        description="输出安全审查与确定性兜底门后的状态。",
    )
    fallback_triggered: bool = Field(
        default=False,
        description="当前 segment 是否由确定性 fallback 或模板替换产生。",
    )
    fallback_template_version: str | None = Field(
        default=None,
        min_length=1,
        description="fallback 模板版本；未触发 fallback 时为空。",
    )
    audit_tier: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        description="segment 级审计等级摘要。",
    )
    publish_allowed: bool = Field(
        default=False,
        description="上游明确声明该 segment 可进入用户可见发布队列。",
    )
    safety_direction_present: bool | None = Field(
        default=None,
        description="急症 segment 是否已由上游确认包含就医导向。",
    )
    source_stage: str = Field(
        default="final_response",
        min_length=1,
        description="候选文本来源阶段；必须为 final_response 或安全 fallback 阶段。",
    )
    references: list[JsonMap] = Field(
        default_factory=list,
        description="用户可见引用摘要；不得承载完整 RAG 原文。",
    )
    reasoning_display: JsonMap | None = Field(
        default=None,
        description="已允许展示的分段 reasoning display 投影。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="segment 轻量元信息；不得承载完整草稿或审查三联稿。",
    )

    @model_validator(mode="after")
    def _validate_fallback_template_relation(self) -> Self:
        """校验 fallback 标记与模板版本之间的关系。

        :return: 已通过关系校验的可发布段。
        :raises ValueError: 当声明触发 fallback 但缺少模板版本时抛出。
        """

        if self.fallback_triggered and self.fallback_template_version is None:
            raise ValueError("fallback_triggered=true 时必须携带模板版本")
        return self


class BranchExecutionStateDto(VetResponseComposerDto):
    """LangGraph 业务分支执行状态摘要。"""

    branch_id: str = Field(min_length=1, description="业务分支 ID。")
    task_id: str = Field(min_length=1, description="分支关联的业务子任务 ID。")
    branch_type: str = Field(min_length=1, description="业务分支类型。")
    generation_profile: str | None = Field(
        default=None,
        min_length=1,
        description="上游生成剖面摘要。",
    )
    executor_key: str | None = Field(
        default=None,
        min_length=1,
        description="上游实际执行器摘要。",
    )
    status: str = Field(min_length=1, description="业务分支执行状态。")
    publishable_segment: PublishableSegmentDto | None = Field(
        default=None,
        description="上游已通过安全链路的可发布候选段。",
    )
    publishable_segment_ref: str | None = Field(
        default=None,
        min_length=1,
        description="候选段外部引用；MVP 仅作为 trace 摘要保留。",
    )
    failure_reason: str | None = Field(
        default=None,
        min_length=1,
        description="分支失败原因摘要。",
    )
    skip_reason: str | None = Field(
        default=None,
        min_length=1,
        description="分支明确跳过原因摘要。",
    )
    trace_patch_ref: str | None = Field(
        default=None,
        min_length=1,
        description="上游分支 trace patch 引用。",
    )


class ResponseSegmentDto(VetResponseComposerDto):
    """Composer 归一化后的用户可见 segment 发布事实。"""

    segment_id: str = Field(min_length=1, description="稳定 segment ID。")
    task_id: str = Field(min_length=1, description="segment 关联任务 ID。")
    segment_type: str = Field(min_length=1, description="用户可见分段类型。")
    order_index: int = Field(ge=0, description="本轮响应中的零基排序索引。")
    content: str = Field(min_length=1, description="用户可见 segment 正文。")
    publish_status: ComposerPublishStatus = Field(description="segment 发布状态。")
    is_first_segment: bool = Field(description="当前 segment 是否为本轮首段。")
    published_at: datetime | None = Field(
        default=None,
        description="segment 成功发布的服务端时间。",
    )
    audit_tier: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        description="segment 级审计等级摘要。",
    )
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="用户可见 segment 标题。",
    )
    trace_refs: list[str] = Field(
        default_factory=list,
        description="关联 trace patch 或 artifact 引用。",
    )
    metadata: JsonMap = Field(
        default_factory=dict,
        description="segment 发布轻量元信息。",
    )


class TurnCompositionStateDto(VetResponseComposerDto):
    """一轮回复合成与发布状态。"""

    request_id: str = Field(min_length=1, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, description="全链路 trace ID。")
    run_id: str = Field(min_length=1, description="图运行 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    thread_id: str | None = Field(
        default=None,
        min_length=1,
        description="CheckpointStore thread ID；未知时为空。",
    )
    assistant_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="ConversationStore 助手消息容器 ID。",
    )
    branches: list[BranchExecutionStateDto] = Field(
        default_factory=list,
        description="本轮已触发业务分支摘要。",
    )
    segments: list[ResponseSegmentDto] = Field(
        default_factory=list,
        description="本轮已发布或幂等命中的用户可见 segment。",
    )
    final_response_text: str = Field(default="", description="整轮聚合正文。")
    turn_publish_status: str = Field(
        default="completed",
        min_length=1,
        description="整轮发布状态。",
    )
    turn_audit_tier: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        description="整轮聚合审计等级。",
    )
    trace_degraded: bool = Field(default=False, description="trace 写入是否降级。")


class PublishDecisionDto(VetResponseComposerDto):
    """Composer 对单个候选段的发布判断。"""

    segment_id: str = Field(min_length=1, description="候选 segment ID。")
    decision: ComposerPublishDecision = Field(description="发布决策。")
    reason_code: str = Field(min_length=1, description="决策原因码。")
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="可发布时的零基排序索引。",
    )
    blocked_by_safety_first_lock: bool = Field(
        default=False,
        description="是否被急症首发锁阻塞。",
    )
    requires_checkpoint_before_publish: bool = Field(
        default=True,
        description="发布前是否要求 checkpoint ready 状态。",
    )


class ComposerTracePatchDto(VetResponseComposerDto):
    """回复合成与发布阶段写入业务逻辑链的 trace patch。"""

    triggered_branch_ids: list[str] = Field(
        default_factory=list,
        description="本轮触发的业务分支 ID 列表。",
    )
    published_segment_ids: list[str] = Field(
        default_factory=list,
        description="本轮已发布或幂等命中的 segment ID 列表。",
    )
    first_segment_type: str | None = Field(
        default=None,
        min_length=1,
        description="本轮首个用户可见 segment 类型。",
    )
    safety_first_lock_applied: bool = Field(
        default=False,
        description="本轮是否应用急症首发锁。",
    )
    delayed_segment_ids: list[str] = Field(
        default_factory=list,
        description="因急症首发或优先级延迟发布的 segment ID。",
    )
    fallback_segment_ids: list[str] = Field(
        default_factory=list,
        description="使用 fallback 或模板替换的 segment ID。",
    )
    failed_branch_ids: list[str] = Field(
        default_factory=list,
        description="本轮失败分支 ID 列表。",
    )
    skipped_branch_ids: list[str] = Field(
        default_factory=list,
        description="本轮明确跳过分支 ID 列表。",
    )
    turn_audit_tier: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        description="整轮聚合审计等级。",
    )
    composer_version: str = Field(
        min_length=1,
        description="生成该 trace patch 的 Composer 版本。",
    )
    trace_degraded: bool = Field(default=False, description="trace 写入是否降级。")


class ComposeTurnRequestDto(VetResponseComposerDto):
    """回复合成与发布请求。"""

    request_id: str = Field(min_length=1, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, description="全链路 trace ID。")
    run_id: str = Field(min_length=1, description="图运行 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    user_message_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前助手消息回复的用户消息 ID。",
    )
    thread_id: str | None = Field(
        default=None,
        min_length=1,
        description="CheckpointStore thread ID。",
    )
    params_version: str = Field(min_length=1, description="业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="配置快照 ID。")
    graph_state: JsonMap = Field(
        default_factory=dict,
        description="LangGraph business_state 投影。",
    )


class ComposeTurnResultDto(VetResponseComposerDto):
    """回复合成与发布结果。"""

    output_text: str = Field(default="", description="整轮用户可见聚合正文。")
    segments: list[ResponseSegmentDto] = Field(
        default_factory=list,
        description="本轮已发布 segment 列表。",
    )
    turn_state: TurnCompositionStateDto = Field(description="本轮发布状态。")
    trace_patch: ComposerTracePatchDto = Field(description="Composer trace patch。")
    trace_delivery_status: ComposerTraceWriteStatus = Field(
        description="Composer trace 写入状态。",
    )
    metadata: JsonMap = Field(default_factory=dict, description="普通结果元信息。")


class ComposerTraceRecordDto(VetResponseComposerDto):
    """可提交给 LogicTraceStore 的 Composer trace 摘要。"""

    request_id: str = Field(min_length=1, description="入口请求 ID。")
    trace_id: str = Field(min_length=1, description="全链路 trace ID。")
    run_id: str = Field(min_length=1, description="图运行 ID。")
    session_id: str = Field(min_length=1, description="会话 ID。")
    user_id: str = Field(min_length=1, description="用户 ID。")
    current_pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    params_version: str = Field(min_length=1, description="业务参数版本。")
    config_snapshot_id: str = Field(min_length=1, description="配置快照 ID。")
    trace_schema_version: str = Field(min_length=1, description="trace schema 引用。")
    capture_policy_version: str = Field(
        min_length=1,
        description="capture policy 版本。",
    )
    trace_patch: ComposerTracePatchDto = Field(description="Composer trace patch。")


class ComposerTraceWriteResultDto(VetResponseComposerDto):
    """Composer trace patch 写入结果。"""

    status: ComposerTraceWriteStatus = Field(description="trace 写入状态。")
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="trace 降级错误码。",
    )
    retryable: bool = Field(default=False, description="trace 写入是否可补偿重试。")
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="trace 写入降级说明。",
    )


__all__: tuple[str, ...] = (
    "BranchExecutionStateDto",
    "ComposerTracePatchDto",
    "ComposerTraceRecordDto",
    "ComposerTraceWriteResultDto",
    "ComposeTurnRequestDto",
    "ComposeTurnResultDto",
    "JsonMap",
    "PublishDecisionDto",
    "PublishableSegmentDto",
    "ResponseSegmentDto",
    "TurnCompositionStateDto",
    "VetResponseComposerDto",
)
