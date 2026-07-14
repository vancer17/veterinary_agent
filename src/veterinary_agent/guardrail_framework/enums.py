##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/enums.py
# 作用: 定义 GuardrailFramework 的稳定字符串枚举，供 DTO、错误映射、策略注册、日志和测试复用。
# 边界: 仅承载护栏框架通用枚举，不实现兽医业务安全规则、handler 调度或 trace 写入。
##################################################################################################

from enum import StrEnum


class GuardrailStage(StrEnum):
    """护栏执行阶段。"""

    PRE_GENERATION = "pre_generation"
    POST_GENERATION_REVIEW = "post_generation_review"
    DETERMINISTIC_GATE = "deterministic_gate"


class GuardrailStatus(StrEnum):
    """单次护栏阶段执行状态。"""

    ALLOWED = "allowed"
    REWRITTEN = "rewritten"
    BLOCKED = "blocked"
    FALLBACK = "fallback"
    DEGRADED = "degraded"
    FAILED = "failed"


class GuardActionType(StrEnum):
    """护栏动作类型。"""

    ALLOW = "allow"
    REWRITE = "rewrite"
    BLOCK = "block"
    FALLBACK = "fallback"
    DEGRADE = "degrade"


class GuardrailFindingSeverity(StrEnum):
    """护栏发现项严重程度。"""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailFailureStrategy(StrEnum):
    """护栏 handler 失败后的框架级处理策略。"""

    FAIL_CLOSED_BLOCK = "fail_closed_block"
    FAIL_OPEN_DEGRADED = "fail_open_degraded"
    FALLBACK = "fallback"


class GuardrailTraceWriteStatus(StrEnum):
    """GuardrailFramework trace 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class GuardrailFrameworkOperation(StrEnum):
    """GuardrailFramework 对外和内部稳定操作名。"""

    RUN_PRE_GENERATION_GUARD = "RunPreGenerationGuard"
    RUN_POST_GENERATION_REVIEW = "RunPostGenerationReview"
    RUN_DETERMINISTIC_GATE = "RunDeterministicGate"
    RUN_GUARDRAIL_STAGE = "RunGuardrailStage"
    REGISTER_POLICY = "RegisterGuardrailPolicy"
    VALIDATE_POLICY = "ValidateGuardrailPolicy"
    RESOLVE_POLICY = "ResolveGuardrailPolicy"
    EXECUTE_HANDLER = "ExecuteGuardrailHandler"
    RENDER_FALLBACK_TEMPLATE = "RenderFallbackTemplate"
    WRITE_TRACE = "WriteGuardrailTrace"


class GuardrailFrameworkErrorCode(StrEnum):
    """GuardrailFramework 稳定错误码。"""

    GUARDRAIL_NOT_READY = "GUARDRAIL_NOT_READY"
    GUARDRAIL_STAGE_DISABLED = "GUARDRAIL_STAGE_DISABLED"
    GUARDRAIL_STAGE_MISMATCH = "GUARDRAIL_STAGE_MISMATCH"
    GUARDRAIL_POLICY_NOT_FOUND = "GUARDRAIL_POLICY_NOT_FOUND"
    GUARDRAIL_POLICY_VERSION_UNAVAILABLE = "GUARDRAIL_POLICY_VERSION_UNAVAILABLE"
    GUARDRAIL_POLICY_SCHEMA_INVALID = "GUARDRAIL_POLICY_SCHEMA_INVALID"
    GUARDRAIL_HANDLER_NOT_REGISTERED = "GUARDRAIL_HANDLER_NOT_REGISTERED"
    GUARDRAIL_HANDLER_NOT_IMPLEMENTED = "GUARDRAIL_HANDLER_NOT_IMPLEMENTED"
    GUARDRAIL_HANDLER_TIMEOUT = "GUARDRAIL_HANDLER_TIMEOUT"
    GUARDRAIL_HANDLER_RETRY_EXHAUSTED = "GUARDRAIL_HANDLER_RETRY_EXHAUSTED"
    GUARDRAIL_OUTPUT_PARSE_FAILED = "GUARDRAIL_OUTPUT_PARSE_FAILED"
    GUARDRAIL_OUTPUT_SCHEMA_INVALID = "GUARDRAIL_OUTPUT_SCHEMA_INVALID"
    GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE = "GUARDRAIL_FALLBACK_TEMPLATE_UNAVAILABLE"
    GUARDRAIL_GATE_INCONCLUSIVE = "GUARDRAIL_GATE_INCONCLUSIVE"
    GUARDRAIL_TRACE_DEGRADED = "GUARDRAIL_TRACE_DEGRADED"
    GUARDRAIL_RUNTIME_CONFIG_UNAVAILABLE = "GUARDRAIL_RUNTIME_CONFIG_UNAVAILABLE"
    GUARDRAIL_INTERNAL_ERROR = "GUARDRAIL_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "GuardActionType",
    "GuardrailFailureStrategy",
    "GuardrailFindingSeverity",
    "GuardrailFrameworkErrorCode",
    "GuardrailFrameworkOperation",
    "GuardrailStage",
    "GuardrailStatus",
    "GuardrailTraceWriteStatus",
)
