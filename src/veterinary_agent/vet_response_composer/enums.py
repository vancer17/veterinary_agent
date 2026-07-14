##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/enums.py
# 作用: 定义 VetResponseComposer 的稳定字符串枚举，供 DTO、错误映射、日志和测试复用。
# 边界: 仅承载回复合成与发布领域枚举，不执行排序、存储写入或图节点适配。
##################################################################################################

from enum import StrEnum


class ComposerBranchType(StrEnum):
    """业务分支类型。"""

    SAFETY_TRIGGER = "safety_trigger"
    STANDARD_CONSULTATION = "standard_consultation"
    OCR = "ocr"
    MEDICAL_RECORD = "medical_record"
    EDUCATION = "education"
    NONMEDICAL_PET_CARE = "nonmedical_pet_care"
    OTHER = "other"


class ComposerGuardStatus(StrEnum):
    """可发布段经过输出安全链路后的状态。"""

    GATE_PASSED = "gate_passed"
    FALLBACK_REPLACED = "fallback_replaced"
    TEMPLATE_SAFE = "template_safe"


class ComposerPublishDecision(StrEnum):
    """Composer 对候选 segment 的发布决策。"""

    PUBLISH = "publish"
    WAIT = "wait"
    SKIP = "skip"
    FAIL = "fail"


class ComposerPublishStatus(StrEnum):
    """Composer 内部 segment 发布状态。"""

    READY = "ready"
    WAITING = "waiting"
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


class ComposerSegmentType(StrEnum):
    """用户可见 segment 类型。"""

    SAFETY = "safety_trigger"
    MEDICAL = "medical_consultation"
    OCR = "ocr_interpretation"
    MEDICAL_RECORD = "medical_record"
    EDUCATION = "education"
    NONMEDICAL = "nonmedical_pet_care"
    SYSTEM_DEGRADED = "system_degraded"
    OTHER = "other"


class ComposerTraceWriteStatus(StrEnum):
    """Composer trace patch 写入状态。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class VetResponseComposerOperation(StrEnum):
    """VetResponseComposer 对外和内部稳定操作名。"""

    COMPOSE_TURN_RESPONSE = "ComposeTurnResponse"
    REGISTER_PUBLISHABLE_SEGMENT = "RegisterPublishableSegment"
    RESOLVE_NEXT_PUBLISHABLE_SEGMENTS = "ResolveNextPublishableSegments"
    PUBLISH_SEGMENT = "PublishSegment"
    FINALIZE_TURN_COMPOSITION = "FinalizeTurnComposition"
    VALIDATE_SEGMENT_PUBLISH_STATE = "ValidateSegmentPublishState"
    WRITE_TRACE = "WriteComposerTrace"


class VetResponseComposerErrorCode(StrEnum):
    """VetResponseComposer 稳定错误码。"""

    COMPOSER_NOT_READY = "COMPOSER_NOT_READY"
    COMPOSER_BRANCH_STATE_MISSING = "COMPOSER_BRANCH_STATE_MISSING"
    COMPOSER_SEGMENT_ID_MISSING = "COMPOSER_SEGMENT_ID_MISSING"
    COMPOSER_SEGMENT_NOT_GATE_PASSED = "COMPOSER_SEGMENT_NOT_GATE_PASSED"
    COMPOSER_UNSAFE_STAGE_PUBLISH_BLOCKED = "COMPOSER_UNSAFE_STAGE_PUBLISH_BLOCKED"
    COMPOSER_SAFETY_FIRST_LOCK_ACTIVE = "COMPOSER_SAFETY_FIRST_LOCK_ACTIVE"
    COMPOSER_SAFETY_DIRECTION_MISSING = "COMPOSER_SAFETY_DIRECTION_MISSING"
    COMPOSER_SEGMENT_ALREADY_PUBLISHED = "COMPOSER_SEGMENT_ALREADY_PUBLISHED"
    COMPOSER_CONVERSATION_APPEND_FAILED = "COMPOSER_CONVERSATION_APPEND_FAILED"
    COMPOSER_CHECKPOINT_READY_FAILED = "COMPOSER_CHECKPOINT_READY_FAILED"
    COMPOSER_CHECKPOINT_PUBLISHED_FAILED = "COMPOSER_CHECKPOINT_PUBLISHED_FAILED"
    COMPOSER_TRACE_DEGRADED = "COMPOSER_TRACE_DEGRADED"
    COMPOSER_COVERAGE_UNRESOLVED = "COMPOSER_COVERAGE_UNRESOLVED"
    COMPOSER_RUNTIME_CONFIG_UNAVAILABLE = "COMPOSER_RUNTIME_CONFIG_UNAVAILABLE"
    COMPOSER_INTERNAL_ERROR = "COMPOSER_INTERNAL_ERROR"


__all__: tuple[str, ...] = (
    "ComposerBranchType",
    "ComposerGuardStatus",
    "ComposerPublishDecision",
    "ComposerPublishStatus",
    "ComposerSegmentType",
    "ComposerTraceWriteStatus",
    "VetResponseComposerErrorCode",
    "VetResponseComposerOperation",
)
