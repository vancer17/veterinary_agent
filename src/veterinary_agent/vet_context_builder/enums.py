##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/enums.py
# 作用: 定义 VetContextBuilder 的来源、剖面、块类型、构建状态、错误码与操作名枚举。
# 边界: 仅承载稳定枚举值，不执行上下文读取、事实合并、裁剪或留痕。
##################################################################################################

from enum import IntEnum, StrEnum


class VetGenerationProfile(StrEnum):
    """兽医生成剖面。"""

    STANDARD = "standard"
    EDUCATION = "education"
    SAFETY_TRIGGER = "safety_trigger"


class VetExecutorKey(StrEnum):
    """兽医子任务实际执行器标识。"""

    STANDARD_CONSULTATION = "standard_consultation"
    EDUCATION = "education"
    SAFETY_TRIGGER = "safety_trigger"
    NONMEDICAL_PET_CARE = "nonmedical_pet_care"
    LAB_REPORT_INTERPRETATION = "lab_report_interpretation"
    OUT_OF_SCOPE_HANDLER = "out_of_scope_handler"


class ContextCompressionStrategy(StrEnum):
    """VetContextBuilder 支持的上下文压缩策略。"""

    SINGLE_FULL = "single_full"
    SAFETY_MINIMAL = "safety_minimal"
    EDUCATION_LIGHT = "education_light"


class ContextSourceType(StrEnum):
    """标准上下文来源类型。"""

    CURRENT_TASK = "current_task"
    CORE_FACT_SNAPSHOT = "core_fact_snapshot"
    PET_PROFILE = "pet_profile"
    CHECKPOINT = "checkpoint"
    CONVERSATION = "conversation"
    CONFIRMED_LAB = "confirmed_lab"
    OWNER_PREFERENCE = "owner_preference"


class ContextSourceStatus(StrEnum):
    """上下文来源读取状态。"""

    AVAILABLE = "available"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    PET_MISMATCH = "pet_mismatch"


class ContextSourceFreshness(StrEnum):
    """上下文来源新鲜度。"""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ContextFactState(StrEnum):
    """事实进入槽位覆盖后的状态。"""

    KNOWN = "known"
    STALE = "stale"
    PENDING_CONFIRMATION = "pending_confirmation"


class VetPromptBlockType(StrEnum):
    """受控兽医 prompt 块类型。"""

    TASK_INPUT = "task_input"
    PET_PROFILE_P0 = "pet_profile_p0"
    CORE_FACT_SNAPSHOT = "core_fact_snapshot"
    SESSION_STATE = "session_state"
    ROLLING_SUMMARY = "rolling_summary"
    RECENT_MESSAGES = "recent_messages"
    SLOT_COVERAGE = "slot_coverage"
    CONFIRMED_LAB_SUMMARY = "confirmed_lab_summary"
    OWNER_PREFERENCE = "owner_preference"
    SAFETY_ASSESSMENT = "safety_assessment"


class VetPromptBlockPriority(IntEnum):
    """prompt 块保留优先级，数值越小优先级越高。"""

    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3


class ContextBuildStatus(StrEnum):
    """VetContextBuilder 构建结果状态。"""

    FULL = "full"
    DEGRADED = "degraded"
    MINIMAL = "minimal"


class ContextTraceWriteStatus(StrEnum):
    """上下文构建摘要留痕状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class VetAuditTier(StrEnum):
    """兽医业务逻辑链审计等级。"""

    A = "A"
    B = "B"
    C = "C"


class VetContextBuilderOperation(StrEnum):
    """VetContextBuilder 对外和内部稳定操作名。"""

    BUILD_CONTEXT = "BuildVetContext"
    LOAD_SOURCE = "LoadContextSource"
    COMPILE_BLOCKS = "CompileContextBlocks"
    TRIM_BLOCKS = "EstimateAndTrimContextBlocks"
    VALIDATE_BUNDLE = "ValidateVetContextBundle"


class VetContextBuilderErrorCode(StrEnum):
    """VetContextBuilder 稳定错误码。"""

    CONTEXT_NOT_READY = "CONTEXT_NOT_READY"
    CONTEXT_INVALID_REQUEST = "CONTEXT_INVALID_REQUEST"
    CONTEXT_MISSING_CURRENT_PET_ID = "CONTEXT_MISSING_CURRENT_PET_ID"
    CONTEXT_ROUTE_DECISION_MISSING = "CONTEXT_ROUTE_DECISION_MISSING"
    CONTEXT_COMPRESSION_STRATEGY_UNSUPPORTED = (
        "CONTEXT_COMPRESSION_STRATEGY_UNSUPPORTED"
    )
    CONTEXT_REQUIRED_BLOCKS_EXCEED_BUDGET = "CONTEXT_REQUIRED_BLOCKS_EXCEED_BUDGET"
    CONTEXT_EMPTY_BUNDLE = "CONTEXT_EMPTY_BUNDLE"
    CONTEXT_RUNTIME_CONFIG_UNAVAILABLE = "CONTEXT_RUNTIME_CONFIG_UNAVAILABLE"
    CONTEXT_INTERNAL_ERROR = "CONTEXT_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "ContextBuildStatus",
    "ContextCompressionStrategy",
    "ContextFactState",
    "ContextSourceFreshness",
    "ContextSourceStatus",
    "ContextSourceType",
    "ContextTraceWriteStatus",
    "VetAuditTier",
    "VetContextBuilderErrorCode",
    "VetContextBuilderOperation",
    "VetExecutorKey",
    "VetGenerationProfile",
    "VetPromptBlockPriority",
    "VetPromptBlockType",
)
