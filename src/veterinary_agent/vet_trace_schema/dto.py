##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/dto.py
# 作用: 定义 VetTraceSchema 组件 DTO，覆盖 trace patch envelope、schema bundle、策略裁决和投影契约。
# 边界: 仅描述 L2 兽医业务逻辑链 schema 数据结构，不执行持久化、不调用 LLM、不访问图运行时。
##################################################################################################

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from veterinary_agent.vet_trace_schema.enums import (
    VetTraceAuditTier,
    VetTraceValidationStatus,
)

JsonMap: TypeAlias = dict[str, object]


class VetTraceSchemaDto(BaseModel):
    """VetTraceSchema 组件 DTO 基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
        validate_assignment=True,
    )


class VetTraceSchemaSettings(VetTraceSchemaDto):
    """VetTraceSchema 应用内服务设置。"""

    default_trace_schema_version: str = Field(
        default="vet-trace-schema.v1",
        min_length=1,
        description="默认业务逻辑链 schema 版本。",
    )
    default_capture_policy_version: str = Field(
        default="vet-trace-capture-policy.v1",
        min_length=1,
        description="默认 capture policy 版本。",
    )
    strict_mode: bool = Field(
        default=True,
        description="是否以严格模式拒绝阻断级 schema、tier 或策略错误。",
    )
    max_artifact_refs: int = Field(
        default=32,
        ge=0,
        description="单个业务 patch 允许携带的 artifact 引用数量上限。",
    )
    enable_reasoning_display_projection: bool = Field(
        default=True,
        description="是否允许构建用户可见推理摘要候选投影。",
    )


class TracePatchEnvelopeDto(VetTraceSchemaDto):
    """L2 业务组件提交给 VetTraceSchema 的标准 trace patch envelope。"""

    trace_id: str = Field(min_length=1, description="本次逻辑链 ID。")
    request_id: str | None = Field(
        default=None,
        min_length=1,
        description="本次请求 ID。",
    )
    run_id: str | None = Field(default=None, min_length=1, description="图运行 ID。")
    session_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前会话 ID。",
    )
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description="当前用户 ID。",
    )
    pet_id: str = Field(min_length=1, description="当前宠物 ID。")
    params_version: str = Field(min_length=1, description="业务运行参数版本。")
    trace_schema_version: str = Field(
        min_length=1,
        description="业务逻辑链 schema 版本。",
    )
    capture_policy_version: str = Field(
        min_length=1,
        description="capture policy 版本。",
    )
    source_component: str = Field(min_length=1, description="提交 patch 的组件名。")
    patch_type: str = Field(min_length=1, description="业务 patch 类型。")
    schema_version: str = Field(min_length=1, description="payload schema 引用。")
    task_id: str | None = Field(default=None, min_length=1, description="业务任务 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="业务 segment ID。",
    )
    payload: JsonMap = Field(default_factory=dict, description="业务 patch 负载。")
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="当前 patch 关联的 artifact 引用。",
    )
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="上游组件已声明的降级标记。",
    )


class AuditTierDecisionDto(VetTraceSchemaDto):
    """审计等级解析或校验结果。"""

    scope: str = Field(min_length=1, description="审计等级裁决作用域。")
    scope_id: str | None = Field(
        default=None,
        min_length=1,
        description="作用域对象 ID。",
    )
    resolved_tier: VetTraceAuditTier = Field(description="最终解析得到的审计等级。")
    declared_tier: VetTraceAuditTier | None = Field(
        default=None,
        description="上游声明的审计等级。",
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="审计等级解析原因码。",
    )
    upgraded: bool = Field(default=False, description="是否相对上游声明发生升级。")
    conflicts: list[str] = Field(
        default_factory=list,
        description="审计等级冲突摘要。",
    )


class CapturePolicyDecisionDto(VetTraceSchemaDto):
    """A/B/C capture policy 裁决结果。"""

    audit_tier: VetTraceAuditTier = Field(description="当前策略适用的审计等级。")
    policy_version: str = Field(min_length=1, description="capture policy 版本。")
    required_patch_types: list[str] = Field(
        default_factory=list,
        description="当前审计等级要求出现的 patch 类型。",
    )
    required_artifact_types: list[str] = Field(
        default_factory=list,
        description="当前审计等级要求具备的 artifact 类型。",
    )
    redact_fields: list[str] = Field(
        default_factory=list,
        description="当前审计等级要求裁剪或脱敏的字段名。",
    )
    projection_views: list[str] = Field(
        default_factory=list,
        description="当前审计等级允许构建的投影视图。",
    )


class VetTracePatchSchemaDto(VetTraceSchemaDto):
    """版本化 trace patch payload JSON Schema 资源。"""

    schema_ref: str = Field(min_length=1, description="payload schema 稳定引用。")
    patch_type: str = Field(min_length=1, description="该 schema 适配的 patch 类型。")
    schema_version: str = Field(min_length=1, description="schema 版本。")
    json_schema: JsonMap = Field(description="JSON Schema 文档。")


class VetTraceCapturePolicyDto(VetTraceSchemaDto):
    """版本化 capture policy 资源。"""

    policy_version: str = Field(min_length=1, description="capture policy 版本。")
    audit_tier: VetTraceAuditTier = Field(description="该策略适用的审计等级。")
    required_patch_types: list[str] = Field(
        default_factory=list,
        description="该等级要求出现的 patch 类型。",
    )
    required_artifact_types: list[str] = Field(
        default_factory=list,
        description="该等级要求具备的 artifact 类型。",
    )
    redact_fields: list[str] = Field(
        default_factory=list,
        description="该等级要求裁剪或脱敏的字段名。",
    )
    projection_views: list[str] = Field(
        default_factory=list,
        description="该等级允许构建的投影视图。",
    )


class VetTraceSchemaBundleDto(VetTraceSchemaDto):
    """VetTraceSchema 版本化资源包。"""

    trace_schema_version: str = Field(
        min_length=1,
        description="业务逻辑链 schema 版本。",
    )
    capture_policy_version: str = Field(
        min_length=1,
        description="默认 capture policy 版本。",
    )
    default_schema_ref: str = Field(
        min_length=1,
        description="默认 payload schema 引用。",
    )
    patch_schemas: list[VetTracePatchSchemaDto] = Field(
        default_factory=list,
        description="当前资源包包含的 patch schema 列表。",
    )
    capture_policies: list[VetTraceCapturePolicyDto] = Field(
        default_factory=list,
        description="当前资源包包含的 capture policy 列表。",
    )


class ReasoningDisplayProjectionDto(VetTraceSchemaDto):
    """用户可见推理摘要候选投影。"""

    projection_id: str = Field(min_length=1, description="投影 ID。")
    trace_id: str = Field(min_length=1, description="关联逻辑链 ID。")
    segment_id: str | None = Field(
        default=None,
        min_length=1,
        description="关联 segment ID。",
    )
    display_safety_level: str = Field(
        default="guard_required",
        min_length=1,
        description="展示安全等级。",
    )
    understood_question_ref: str | None = Field(
        default=None,
        min_length=1,
        description="用户问题理解摘要引用。",
    )
    reasoning_summary_ref: str | None = Field(
        default=None,
        min_length=1,
        description="可展示推理摘要引用。",
    )
    considered_domains: list[str] = Field(
        default_factory=list,
        description="摘要中涉及的业务域。",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="仍缺失的关键信息。",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="可展示证据引用。",
    )
    requires_output_guard: bool = Field(
        default=True,
        description="发布前是否仍需输出安全审查。",
    )


class VetTraceValidationResultDto(VetTraceSchemaDto):
    """VetTraceSchema 完整业务校验结果。"""

    valid: bool = Field(description="校验是否通过。")
    validation_status: VetTraceValidationStatus = Field(description="校验状态。")
    schema_ref: str | None = Field(
        default=None,
        min_length=1,
        description="生效 payload schema 引用。",
    )
    errors: list[str] = Field(default_factory=list, description="阻断级错误列表。")
    warnings: list[str] = Field(default_factory=list, description="非阻断告警列表。")
    degraded_flags: list[str] = Field(
        default_factory=list,
        description="降级标记列表。",
    )
    normalized_business_payload: JsonMap = Field(
        default_factory=dict,
        description="标准化后可写入 LogicTraceStore 的业务负载。",
    )
    tier_decision: AuditTierDecisionDto | None = Field(
        default=None,
        description="审计等级裁决结果。",
    )
    capture_policy: CapturePolicyDecisionDto | None = Field(
        default=None,
        description="capture policy 裁决结果。",
    )


__all__: tuple[str, ...] = (
    "AuditTierDecisionDto",
    "CapturePolicyDecisionDto",
    "JsonMap",
    "ReasoningDisplayProjectionDto",
    "TracePatchEnvelopeDto",
    "VetTraceCapturePolicyDto",
    "VetTracePatchSchemaDto",
    "VetTraceSchemaBundleDto",
    "VetTraceSchemaDto",
    "VetTraceSchemaSettings",
    "VetTraceValidationResultDto",
)
