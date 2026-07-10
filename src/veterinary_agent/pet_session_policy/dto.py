##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/dto.py
# 作用: 定义 PetSessionPolicy 组件的数据契约，覆盖请求上下文、策略判定、成功上下文与 trace 摘要。
# 边界: 仅描述 L2 宠物会话策略数据结构，不访问 ConversationStore、不执行策略判定或 HTTP 错误映射。
##################################################################################################

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.conversation_store import (
    ConversationErrorCode,
    ConversationSessionStatus,
)
from veterinary_agent.pet_session_policy.enums import (
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionPolicyErrorCode,
    PetSessionTraceWriteStatus,
)

JsonMap: TypeAlias = dict[str, object]


class PetSessionPolicyDto(BaseModel):
    """PetSessionPolicy 组件 DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class PetSessionRequestContextDto(PetSessionPolicyDto):
    """宠物会话策略请求上下文 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次请求 ID，用于错误关联、trace 与可观测性。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    user_id: str | None = Field(
        default=None,
        description="上游可信传入的用户 ID；为空时由策略组件阻断。",
    )
    session_id: str | None = Field(
        default=None,
        description="上游可信传入的会话 ID；为空时由策略组件阻断。",
    )
    pet_id: str | None = Field(
        default=None,
        description="上游可信传入的宠物 ID；为空时由策略组件阻断。",
    )
    client_pet_snapshot_ref: JsonMap | None = Field(
        default=None,
        description="可选客户端宠物快照引用；不参与宠物绑定判定。",
    )


class PetSessionPolicyDecisionDto(PetSessionPolicyDto):
    """宠物会话策略判定结果 DTO。"""

    decision: PetSessionDecision = Field(
        description="稳定策略判定枚举。",
    )
    policy_action: PetSessionPolicyAction = Field(
        description="调用方应执行的策略动作。",
    )
    allow_continue: bool = Field(
        description="当前请求是否允许进入后续业务图。",
    )
    error_code: PetSessionPolicyErrorCode | None = Field(
        default=None,
        description="阻断时使用的稳定业务错误码；允许继续时为空。",
    )
    retryable: bool = Field(
        description="调用方是否可以稍后或在修正请求后重试。",
    )
    reason: str = Field(
        min_length=1,
        description="面向工程排障的策略判定说明。",
    )
    missing_field: str | None = Field(
        default=None,
        min_length=1,
        description="缺少必要字段时对应的字段名称。",
    )
    is_new_session: bool | None = Field(
        default=None,
        description="已访问存储时，本次调用是否创建了新 session。",
    )
    session_status: ConversationSessionStatus | None = Field(
        default=None,
        description="已确认 session 的生命周期状态。",
    )
    current_pet_id: str | None = Field(
        default=None,
        min_length=1,
        description="允许继续时唯一有效的当前宠物 ID。",
    )
    store_error_code: ConversationErrorCode | None = Field(
        default=None,
        description="ConversationStore 调用失败时的稳定错误码摘要。",
    )
    params_version: str | None = Field(
        default=None,
        min_length=1,
        description="当前策略判定使用的业务运行参数版本。",
    )
    config_snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前策略判定使用的 RuntimeConfig 快照 ID。",
    )


class PetSessionTraceRecordDto(PetSessionPolicyDto):
    """宠物会话策略逻辑链摘要 DTO。"""

    schema_version: Literal["pet-session-policy.trace.v1"] = Field(
        default="pet-session-policy.trace.v1",
        description="宠物会话策略 trace 摘要结构版本。",
    )
    request_id: str = Field(
        min_length=1,
        description="本次请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    user_id: str | None = Field(
        default=None,
        description="上游可信传入的用户 ID。",
    )
    session_id: str | None = Field(
        default=None,
        description="上游可信传入的 session ID。",
    )
    requested_pet_id: str | None = Field(
        default=None,
        description="本轮请求显式携带的宠物 ID。",
    )
    current_pet_id: str | None = Field(
        default=None,
        description="策略确认后允许后续组件使用的宠物 ID。",
    )
    decision: PetSessionDecision = Field(
        description="宠物会话策略判定。",
    )
    policy_action: PetSessionPolicyAction = Field(
        description="宠物会话策略动作。",
    )
    allow_continue: bool = Field(
        description="当前请求是否允许继续。",
    )
    error_code: PetSessionPolicyErrorCode | None = Field(
        default=None,
        description="阻断时使用的稳定业务错误码。",
    )
    retryable: bool = Field(
        description="当前策略结果是否允许调用方重试。",
    )
    missing_field: str | None = Field(
        default=None,
        description="缺少必要字段时对应的字段名称。",
    )
    is_new_session: bool | None = Field(
        default=None,
        description="本次请求是否创建了新 session。",
    )
    session_status: ConversationSessionStatus | None = Field(
        default=None,
        description="已确认 session 的生命周期状态。",
    )
    store_error_code: ConversationErrorCode | None = Field(
        default=None,
        description="底层 ConversationStore 错误码摘要。",
    )
    params_version: str | None = Field(
        default=None,
        description="当前策略判定使用的业务运行参数版本。",
    )
    config_snapshot_id: str | None = Field(
        default=None,
        description="当前策略判定使用的 RuntimeConfig 快照 ID。",
    )


class PetSessionTraceWriteResultDto(PetSessionPolicyDto):
    """宠物会话策略 trace 写入结果 DTO。"""

    status: PetSessionTraceWriteStatus = Field(
        description="trace 摘要写入状态。",
    )
    error_code: str | None = Field(
        default=None,
        min_length=1,
        description="trace 写入降级时的稳定错误码。",
    )
    retryable: bool = Field(
        default=False,
        description="trace 写入失败是否允许稍后补偿重试。",
    )
    detail: str | None = Field(
        default=None,
        min_length=1,
        description="面向工程排障的 trace 写入结果说明。",
    )


class PetSessionContextDto(PetSessionPolicyDto):
    """允许进入后续业务图的标准宠物会话上下文 DTO。"""

    request_id: str = Field(
        min_length=1,
        description="本次请求 ID。",
    )
    trace_id: str = Field(
        min_length=1,
        description="本次全链路追踪 ID。",
    )
    user_id: str = Field(
        min_length=1,
        description="策略确认后的用户 ID。",
    )
    session_id: str = Field(
        min_length=1,
        description="策略确认后的 session ID。",
    )
    current_pet_id: str = Field(
        min_length=1,
        description="后续业务节点唯一允许使用的当前宠物 ID。",
    )
    is_new_session: bool = Field(
        description="本次策略执行是否创建了新 session。",
    )
    decision: PetSessionDecision = Field(
        description="允许继续对应的策略判定。",
    )
    allow_continue: Literal[True] = Field(
        default=True,
        description="成功上下文固定允许进入后续业务图。",
    )
    session_status: Literal[ConversationSessionStatus.ACTIVE] = Field(
        default=ConversationSessionStatus.ACTIVE,
        description="成功上下文中的 session 状态固定为 active。",
    )
    params_version: str = Field(
        min_length=1,
        description="当前策略判定使用的业务运行参数版本。",
    )
    config_snapshot_id: str = Field(
        min_length=1,
        description="当前策略判定使用的 RuntimeConfig 快照 ID。",
    )
    trace_delivery_status: PetSessionTraceWriteStatus = Field(
        description="策略判定摘要的逻辑链写入状态。",
    )


__all__: tuple[str, ...] = (
    "JsonMap",
    "PetSessionContextDto",
    "PetSessionPolicyDecisionDto",
    "PetSessionPolicyDto",
    "PetSessionRequestContextDto",
    "PetSessionTraceRecordDto",
    "PetSessionTraceWriteResultDto",
)
