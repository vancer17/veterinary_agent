##################################################################################################
# 文件: src/veterinary_agent/pet_session_policy/__init__.py
# 作用: 作为 PetSessionPolicy 组件包统一出口，集中暴露稳定契约、默认实现、错误与 TODO trace 适配器。
# 边界: 外部包应从本文件导入宠物会话策略能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.pet_session_policy.dto import (
    JsonMap,
    PetSessionContextDto,
    PetSessionPolicyDecisionDto,
    PetSessionPolicyDto,
    PetSessionRequestContextDto,
    PetSessionTraceRecordDto,
    PetSessionTraceWriteResultDto,
)
from veterinary_agent.pet_session_policy.enums import (
    PetSessionDecision,
    PetSessionPolicyAction,
    PetSessionPolicyErrorCode,
    PetSessionTraceWriteStatus,
)
from veterinary_agent.pet_session_policy.errors import (
    PetSessionPolicyError,
    PetSessionPolicyErrorDto,
    build_pet_session_policy_error_dto,
    is_pet_session_policy_error_retryable_by_default,
)
from veterinary_agent.pet_session_policy.service import (
    DefaultPetSessionPolicy,
    PetSessionPolicy,
)
from veterinary_agent.pet_session_policy.trace import (
    TODO_TRACE_ERROR_CODE,
    LogicTracePetSessionTraceSink,
    PetSessionTraceSink,
    TodoPetSessionTraceSink,
)

__all__: tuple[str, ...] = (
    "TODO_TRACE_ERROR_CODE",
    "DefaultPetSessionPolicy",
    "JsonMap",
    "LogicTracePetSessionTraceSink",
    "PetSessionContextDto",
    "PetSessionDecision",
    "PetSessionPolicy",
    "PetSessionPolicyAction",
    "PetSessionPolicyDecisionDto",
    "PetSessionPolicyDto",
    "PetSessionPolicyError",
    "PetSessionPolicyErrorCode",
    "PetSessionPolicyErrorDto",
    "PetSessionRequestContextDto",
    "PetSessionTraceRecordDto",
    "PetSessionTraceSink",
    "PetSessionTraceWriteResultDto",
    "PetSessionTraceWriteStatus",
    "TodoPetSessionTraceSink",
    "build_pet_session_policy_error_dto",
    "is_pet_session_policy_error_retryable_by_default",
)
