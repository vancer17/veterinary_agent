##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/enums.py
# 作用: 定义 VetTraceSchema 组件稳定枚举，包括审计等级、校验状态、错误码与操作名。
# 边界: 仅承载 L2 兽医业务逻辑链 schema 语义枚举，不执行校验、不访问存储、不处理 HTTP。
##################################################################################################

from enum import StrEnum


class VetTraceAuditTier(StrEnum):
    """兽医业务逻辑链审计等级。"""

    A = "A"
    B = "B"
    C = "C"


class VetTraceValidationStatus(StrEnum):
    """VetTraceSchema 校验结果状态。"""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_DEGRADATION = "accepted_with_degradation"
    REJECTED = "rejected"


class VetTraceErrorCode(StrEnum):
    """VetTraceSchema 稳定错误码。"""

    VET_TRACE_SCHEMA_VERSION_NOT_FOUND = "VET_TRACE_SCHEMA_VERSION_NOT_FOUND"
    VET_TRACE_CAPTURE_POLICY_NOT_FOUND = "VET_TRACE_CAPTURE_POLICY_NOT_FOUND"
    VET_TRACE_PATCH_ENVELOPE_INVALID = "VET_TRACE_PATCH_ENVELOPE_INVALID"
    VET_TRACE_PATCH_PAYLOAD_INVALID = "VET_TRACE_PATCH_PAYLOAD_INVALID"
    VET_TRACE_PET_CONFLICT = "VET_TRACE_PET_CONFLICT"
    VET_TRACE_AUDIT_TIER_CONFLICT = "VET_TRACE_AUDIT_TIER_CONFLICT"
    VET_TRACE_REQUIRED_ARTIFACT_MISSING = "VET_TRACE_REQUIRED_ARTIFACT_MISSING"
    VET_TRACE_REASONING_DISPLAY_UNSAFE = "VET_TRACE_REASONING_DISPLAY_UNSAFE"
    VET_TRACE_GUARD_CHAIN_INCOMPLETE = "VET_TRACE_GUARD_CHAIN_INCOMPLETE"
    VET_TRACE_SEGMENT_INCONSISTENT = "VET_TRACE_SEGMENT_INCONSISTENT"
    VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE = "VET_TRACE_SCHEMA_RESOURCE_UNAVAILABLE"
    VET_TRACE_PROJECTION_BUILD_FAILED = "VET_TRACE_PROJECTION_BUILD_FAILED"
    VET_TRACE_INVALID_ARGUMENT = "VET_TRACE_INVALID_ARGUMENT"


class VetTraceOperation(StrEnum):
    """VetTraceSchema 对外操作名。"""

    VALIDATE_TRACE_EVENT = "ValidateTraceEvent"
    VALIDATE_TRACE_PATCH = "ValidateTracePatch"
    RESOLVE_AUDIT_TIER = "ResolveAuditTier"
    APPLY_CAPTURE_POLICY = "ApplyCapturePolicy"
    BUILD_REASONING_DISPLAY_PROJECTION = "BuildReasoningDisplayProjection"
    LOAD_SCHEMA_BUNDLE = "LoadSchemaBundle"


__all__: tuple[str, ...] = (
    "VetTraceAuditTier",
    "VetTraceErrorCode",
    "VetTraceOperation",
    "VetTraceValidationStatus",
)
