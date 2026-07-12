##################################################################################################
# 文件: src/veterinary_agent/vet_trace_schema/__init__.py
# 作用: 作为 VetTraceSchema 组件包统一出口，集中暴露 DTO、枚举、错误、registry 与默认服务实现。
# 边界: 外部包应从本文件导入兽医逻辑链 schema 能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.vet_trace_schema.dto import (
    AuditTierDecisionDto,
    CapturePolicyDecisionDto,
    JsonMap,
    ReasoningDisplayProjectionDto,
    TracePatchEnvelopeDto,
    VetTraceCapturePolicyDto,
    VetTracePatchSchemaDto,
    VetTraceSchemaBundleDto,
    VetTraceSchemaDto,
    VetTraceSchemaSettings,
    VetTraceValidationResultDto,
)
from veterinary_agent.vet_trace_schema.enums import (
    VetTraceAuditTier,
    VetTraceErrorCode,
    VetTraceOperation,
    VetTraceValidationStatus,
)
from veterinary_agent.vet_trace_schema.errors import (
    VetTraceSchemaError,
    VetTraceSchemaErrorDto,
    build_vet_trace_schema_error_dto,
    is_vet_trace_error_retryable_by_default,
)
from veterinary_agent.vet_trace_schema.registry import (
    DEFAULT_CAPTURE_POLICY_VERSION,
    DEFAULT_PATCH_SCHEMA_REF,
    DEFAULT_TRACE_SCHEMA_VERSION,
    VetTraceSchemaRegistry,
    create_default_vet_trace_schema_bundle,
)
from veterinary_agent.vet_trace_schema.service import (
    DefaultVetTraceSchema,
    VetTraceSchema,
    create_default_vet_trace_schema,
)

__all__: tuple[str, ...] = (
    "AuditTierDecisionDto",
    "CapturePolicyDecisionDto",
    "DEFAULT_CAPTURE_POLICY_VERSION",
    "DEFAULT_PATCH_SCHEMA_REF",
    "DEFAULT_TRACE_SCHEMA_VERSION",
    "DefaultVetTraceSchema",
    "JsonMap",
    "ReasoningDisplayProjectionDto",
    "TracePatchEnvelopeDto",
    "VetTraceAuditTier",
    "VetTraceCapturePolicyDto",
    "VetTraceErrorCode",
    "VetTraceOperation",
    "VetTracePatchSchemaDto",
    "VetTraceSchema",
    "VetTraceSchemaBundleDto",
    "VetTraceSchemaDto",
    "VetTraceSchemaError",
    "VetTraceSchemaErrorDto",
    "VetTraceSchemaRegistry",
    "VetTraceSchemaSettings",
    "VetTraceValidationResultDto",
    "VetTraceValidationStatus",
    "build_vet_trace_schema_error_dto",
    "create_default_vet_trace_schema",
    "create_default_vet_trace_schema_bundle",
    "is_vet_trace_error_retryable_by_default",
)
