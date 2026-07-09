##################################################################################################
# 文件: src/veterinary_agent/checkpoint_store/enums.py
# 作用: 定义 CheckpointStore 组件的稳定字符串枚举，供接口契约、错误映射、日志和后续实现层复用。
# 边界: 仅描述 L0 CheckpointStore 领域枚举，不包含数据库实现、LangGraph 适配或兽医业务语义。
##################################################################################################

from enum import StrEnum


class CheckpointErrorCode(StrEnum):
    """CheckpointStore 稳定错误码。"""

    CHECKPOINT_NOT_FOUND = "CHECKPOINT_NOT_FOUND"
    CHECKPOINT_THREAD_NOT_FOUND = "CHECKPOINT_THREAD_NOT_FOUND"
    CHECKPOINT_LOCKED = "CHECKPOINT_LOCKED"
    CHECKPOINT_VERSION_CONFLICT = "CHECKPOINT_VERSION_CONFLICT"
    CHECKPOINT_PET_CONFLICT = "CHECKPOINT_PET_CONFLICT"
    CHECKPOINT_STATE_TOO_LARGE = "CHECKPOINT_STATE_TOO_LARGE"
    CHECKPOINT_SCHEMA_UNSUPPORTED = "CHECKPOINT_SCHEMA_UNSUPPORTED"
    CHECKPOINT_STATE_CORRUPTED = "CHECKPOINT_STATE_CORRUPTED"
    CHECKPOINT_LOCK_OWNER_MISMATCH = "CHECKPOINT_LOCK_OWNER_MISMATCH"
    CHECKPOINT_STORE_UNAVAILABLE = "CHECKPOINT_STORE_UNAVAILABLE"
    CHECKPOINT_OPERATION_TIMEOUT = "CHECKPOINT_OPERATION_TIMEOUT"
    CHECKPOINT_INVALID_ARGUMENT = "CHECKPOINT_INVALID_ARGUMENT"


class CheckpointOperation(StrEnum):
    """CheckpointStore 对外接口操作名。"""

    ENSURE_THREAD = "EnsureThread"
    ACQUIRE_RUN_LOCK = "AcquireRunLock"
    RELEASE_RUN_LOCK = "ReleaseRunLock"
    LOAD_LATEST_CHECKPOINT = "LoadLatestCheckpoint"
    GET_CHECKPOINT = "GetCheckpoint"
    LIST_CHECKPOINTS = "ListCheckpoints"
    SAVE_CHECKPOINT = "SaveCheckpoint"
    MARK_SEGMENT_PUBLISHED = "MarkSegmentPublished"
    LOAD_SESSION_STATE = "LoadSessionState"
    LANGGRAPH_POSTGRES_SAVER_START = "LangGraphPostgresSaverStart"
    LANGGRAPH_POSTGRES_SAVER_GET = "LangGraphPostgresSaverGet"
    LANGGRAPH_POSTGRES_SAVER_STOP = "LangGraphPostgresSaverStop"
    BUILD_LANGGRAPH_CONFIG = "BuildLangGraphConfig"


class CheckpointThreadStatus(StrEnum):
    """checkpoint thread 生命周期状态。"""

    INITIALIZED = "initialized"
    RUNNING = "running"
    RECOVERABLE = "recoverable"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckpointRecordStatus(StrEnum):
    """单条 checkpoint 快照状态。"""

    RECOVERABLE = "recoverable"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SegmentPublishStatus(StrEnum):
    """segment 发布幂等状态。"""

    READY = "ready"
    PUBLISHED = "published"


__all__: tuple[str, ...] = (
    "CheckpointErrorCode",
    "CheckpointOperation",
    "CheckpointRecordStatus",
    "CheckpointThreadStatus",
    "SegmentPublishStatus",
)
