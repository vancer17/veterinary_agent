##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/enums.py
# 作用: 定义 PetSessionPolicy 组件的稳定枚举，覆盖策略判定、业务错误、执行动作与 trace 写入状态。
# 边界: 仅承载 L2 宠物会话策略枚举，不访问存储、不执行会话绑定、不生成对外响应。
##################################################################################################

from enum import StrEnum


class PetSessionDecision(StrEnum):
    """宠物会话策略判定枚举。"""

    ALLOW_NEW_SESSION_BOUND = "ALLOW_NEW_SESSION_BOUND"
    ALLOW_EXISTING_SESSION = "ALLOW_EXISTING_SESSION"
    BLOCK_MISSING_USER_ID = "BLOCK_MISSING_USER_ID"
    BLOCK_MISSING_SESSION_ID = "BLOCK_MISSING_SESSION_ID"
    BLOCK_MISSING_PET_ID = "BLOCK_MISSING_PET_ID"
    BLOCK_SESSION_PET_MISMATCH = "BLOCK_SESSION_PET_MISMATCH"
    BLOCK_SESSION_USER_MISMATCH = "BLOCK_SESSION_USER_MISMATCH"
    BLOCK_SESSION_CLOSED = "BLOCK_SESSION_CLOSED"
    BLOCK_SESSION_ARCHIVED = "BLOCK_SESSION_ARCHIVED"
    BLOCK_STORE_UNAVAILABLE = "BLOCK_STORE_UNAVAILABLE"
    BLOCK_RUNTIME_CONFIG_UNAVAILABLE = "BLOCK_RUNTIME_CONFIG_UNAVAILABLE"
    BLOCK_POLICY_DISABLED = "BLOCK_POLICY_DISABLED"
    BLOCK_INTERNAL_ERROR = "BLOCK_INTERNAL_ERROR"


class PetSessionPolicyAction(StrEnum):
    """宠物会话策略执行动作枚举。"""

    ALLOW_CONTINUE = "allow_continue"
    BLOCK_REQUEST = "block_request"


class PetSessionPolicyErrorCode(StrEnum):
    """宠物会话策略稳定错误码。"""

    REQUIRED_FIELD_MISSING = "PET_SESSION_REQUIRED_FIELD_MISSING"
    PET_MISMATCH = "PET_SESSION_PET_MISMATCH"
    USER_MISMATCH = "PET_SESSION_USER_MISMATCH"
    SESSION_CLOSED = "PET_SESSION_CLOSED"
    SESSION_ARCHIVED = "PET_SESSION_ARCHIVED"
    STORE_UNAVAILABLE = "PET_SESSION_STORE_UNAVAILABLE"
    RUNTIME_CONFIG_UNAVAILABLE = "PET_SESSION_RUNTIME_CONFIG_UNAVAILABLE"
    POLICY_DISABLED = "PET_SESSION_POLICY_DISABLED"
    INTERNAL_ERROR = "PET_SESSION_INTERNAL_ERROR"


class PetSessionTraceWriteStatus(StrEnum):
    """宠物会话策略 trace 写入状态枚举。"""

    RECORDED = "recorded"
    DEGRADED = "degraded"


__all__: tuple[str, ...] = (
    "PetSessionDecision",
    "PetSessionPolicyAction",
    "PetSessionPolicyErrorCode",
    "PetSessionTraceWriteStatus",
)
