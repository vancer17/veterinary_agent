##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/__init__.py
# 作用: 作为 GuardrailFramework 一级包统一出口，集中暴露 DTO、枚举、错误、端口、注册表、服务、节点与 trace。
# 边界: 其他包必须从本文件导入护栏框架能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.guardrail_framework.dto import (
    FallbackTemplateDto,
    GuardActionDto,
    GuardrailFailurePolicyDto,
    GuardrailFindingDto,
    GuardrailFrameworkDto,
    GuardrailPolicyDto,
    GuardrailRetryPolicyDto,
    GuardrailRunContextDto,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailTimeoutPolicyDto,
    GuardrailTracePolicyDto,
    GuardrailTraceRecordDto,
    GuardrailTraceWriteResultDto,
    JsonMap,
)
from veterinary_agent.guardrail_framework.enums import (
    GuardActionType,
    GuardrailFailureStrategy,
    GuardrailFindingSeverity,
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceWriteStatus,
)
from veterinary_agent.guardrail_framework.errors import (
    GuardrailFrameworkError,
    GuardrailFrameworkErrorDto,
    build_guardrail_framework_error_dto,
    is_guardrail_framework_error_retryable_by_default,
)
from veterinary_agent.guardrail_framework.node import GuardrailFrameworkGraphNode
from veterinary_agent.guardrail_framework.ports import (
    FallbackTemplateProvider,
    GuardrailHandler,
    GuardrailHandlerRegistry,
    GuardrailPolicyRegistry,
    GuardrailTraceSink,
    TODO_FALLBACK_TEMPLATE_ERROR_CODE,
    TODO_GUARDRAIL_HANDLER_ERROR_CODE,
    TODO_GUARDRAIL_TRACE_ERROR_CODE,
    TodoFallbackTemplateProvider,
    TodoGuardrailHandler,
    TodoGuardrailTraceSink,
)
from veterinary_agent.guardrail_framework.registry import (
    InMemoryGuardrailHandlerRegistry,
    InMemoryGuardrailPolicyRegistry,
)
from veterinary_agent.guardrail_framework.service import (
    DefaultGuardrailFramework,
    GuardrailFramework,
    build_default_guardrail_handler_registry,
    build_default_guardrail_policy_registry,
    create_default_guardrail_framework,
)
from veterinary_agent.guardrail_framework.trace import LogicTraceGuardrailTraceSink

__all__: tuple[str, ...] = (
    "DefaultGuardrailFramework",
    "FallbackTemplateDto",
    "FallbackTemplateProvider",
    "GuardActionDto",
    "GuardActionType",
    "GuardrailFailurePolicyDto",
    "GuardrailFailureStrategy",
    "GuardrailFindingDto",
    "GuardrailFindingSeverity",
    "GuardrailFramework",
    "GuardrailFrameworkDto",
    "GuardrailFrameworkError",
    "GuardrailFrameworkErrorCode",
    "GuardrailFrameworkErrorDto",
    "GuardrailFrameworkGraphNode",
    "GuardrailFrameworkOperation",
    "GuardrailHandler",
    "GuardrailHandlerRegistry",
    "GuardrailPolicyDto",
    "GuardrailPolicyRegistry",
    "GuardrailRetryPolicyDto",
    "GuardrailRunContextDto",
    "GuardrailRunRequestDto",
    "GuardrailRunResultDto",
    "GuardrailStage",
    "GuardrailStatus",
    "GuardrailTimeoutPolicyDto",
    "GuardrailTracePolicyDto",
    "GuardrailTraceRecordDto",
    "GuardrailTraceSink",
    "GuardrailTraceWriteResultDto",
    "GuardrailTraceWriteStatus",
    "InMemoryGuardrailHandlerRegistry",
    "InMemoryGuardrailPolicyRegistry",
    "JsonMap",
    "LogicTraceGuardrailTraceSink",
    "TODO_FALLBACK_TEMPLATE_ERROR_CODE",
    "TODO_GUARDRAIL_HANDLER_ERROR_CODE",
    "TODO_GUARDRAIL_TRACE_ERROR_CODE",
    "TodoFallbackTemplateProvider",
    "TodoGuardrailHandler",
    "TodoGuardrailTraceSink",
    "build_default_guardrail_handler_registry",
    "build_default_guardrail_policy_registry",
    "build_guardrail_framework_error_dto",
    "create_default_guardrail_framework",
    "is_guardrail_framework_error_retryable_by_default",
)
