##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/enums.py
# 作用: 定义 VetTaskDecomposer 的任务类型、附件角色、拆解方法、状态、错误码与操作名枚举。
# 边界: 仅承载稳定枚举值，不执行任务拆解、LLM 调用、fallback 或 trace 写入。
##################################################################################################

from enum import StrEnum


class VetTaskType(StrEnum):
    """兽医子任务类型。"""

    TRIAGE = "TRIAGE"
    NUTRITION = "NUTRITION"
    BEHAVIOR = "BEHAVIOR"
    CARE = "CARE"
    EDUCATION_QA = "EDUCATION_QA"
    REPORT_OCR = "REPORT_OCR"
    RECORD_PARSE = "RECORD_PARSE"
    GENERAL_QA = "GENERAL_QA"
    UNDECOMPOSED = "UNDECOMPOSED"


class AttachmentRole(StrEnum):
    """附件与子任务之间的受控关系。"""

    NONE = "none"
    DIAGNOSTIC_CONTEXT = "diagnostic_context"
    INDEPENDENT_VISUAL_TASK = "independent_visual_task"
    UNSUPPORTED_IMAGE = "unsupported_image"
    UNKNOWN = "unknown"


class TaskPriorityHint(StrEnum):
    """任务拆解阶段输出的初始处理优先级提示。"""

    UNKNOWN = "unknown"
    ROUTINE = "routine"
    ELEVATED = "elevated"
    URGENT = "urgent"


class DecompositionMethod(StrEnum):
    """VetTaskDecomposer 本次采用的拆解方法。"""

    LLM = "llm"
    LLM_REVIEW_REPAIRED = "llm_review_repaired"
    LOCAL_FALLBACK = "local_fallback"
    SINGLE_PASSTHROUGH = "single_passthrough"


class DecompositionStatus(StrEnum):
    """VetTaskDecomposer 结果状态。"""

    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"


class VetTaskTraceWriteStatus(StrEnum):
    """任务拆解摘要 trace 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class VetTaskDecomposerOperation(StrEnum):
    """VetTaskDecomposer 对外和内部稳定操作名。"""

    DECOMPOSE_TASKS = "DecomposeVetTasks"
    VALIDATE_INPUT = "ValidateVetTaskDecomposeInput"
    RUN_LLM_DECOMPOSE = "RunVetTaskDecomposeAgent"
    RUN_LLM_REVIEW = "RunVetTaskDecomposeReview"
    RUN_LOCAL_FALLBACK = "FallbackDecomposeVetTasks"
    NORMALIZE_OUTPUT = "NormalizeVetTaskDecomposition"
    WRITE_TRACE = "WriteVetTaskDecompositionTrace"


class VetTaskDecomposerErrorCode(StrEnum):
    """VetTaskDecomposer 稳定错误码。"""

    TASK_DECOMPOSE_NOT_READY = "TASK_DECOMPOSE_NOT_READY"
    TASK_DECOMPOSE_INVALID_REQUEST = "TASK_DECOMPOSE_INVALID_REQUEST"
    TASK_DECOMPOSE_MISSING_CURRENT_PET_ID = "TASK_DECOMPOSE_MISSING_CURRENT_PET_ID"
    TASK_DECOMPOSE_EMPTY_MESSAGE = "TASK_DECOMPOSE_EMPTY_MESSAGE"
    TASK_DECOMPOSE_LLM_UNAVAILABLE = "TASK_DECOMPOSE_LLM_UNAVAILABLE"
    TASK_DECOMPOSE_OUTPUT_PARSE_FAILED = "TASK_DECOMPOSE_OUTPUT_PARSE_FAILED"
    TASK_DECOMPOSE_SCHEMA_INVALID = "TASK_DECOMPOSE_SCHEMA_INVALID"
    TASK_DECOMPOSE_LOCAL_FALLBACK_UNAVAILABLE = (
        "TASK_DECOMPOSE_LOCAL_FALLBACK_UNAVAILABLE"
    )
    TASK_DECOMPOSE_EMPTY_RESULT = "TASK_DECOMPOSE_EMPTY_RESULT"
    TASK_DECOMPOSE_RUNTIME_CONFIG_UNAVAILABLE = (
        "TASK_DECOMPOSE_RUNTIME_CONFIG_UNAVAILABLE"
    )
    TASK_DECOMPOSE_INTERNAL_ERROR = "TASK_DECOMPOSE_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "AttachmentRole",
    "DecompositionMethod",
    "DecompositionStatus",
    "TaskPriorityHint",
    "VetTaskDecomposerErrorCode",
    "VetTaskDecomposerOperation",
    "VetTaskTraceWriteStatus",
    "VetTaskType",
)
