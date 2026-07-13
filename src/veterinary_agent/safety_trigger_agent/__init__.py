##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/__init__.py
# 作用: 作为 SafetyTriggerAgent 一级包统一出口，集中暴露 DTO、枚举、错误、端口、服务与节点。
# 边界: 其他包必须从本文件导入急症组件能力，避免跨包直接引用内部实现模块。
##################################################################################################

from veterinary_agent.safety_trigger_agent.dto import (
    EmergencyBriefDto,
    JsonMap,
    KeyConfirmationPlanDto,
    SafetyRagPolicySummaryDto,
    SafetyRequirementSetDto,
    SafetySignalSummaryDto,
    SafetyTraceWriteResultDto,
    SafetyTriggerDraftDto,
    SafetyTriggerDto,
    SafetyTriggerRequestDto,
    SafetyTriggerSelfCheckSummaryDto,
    SafetyTriggerTracePatchDto,
    SafetyTriggerTraceRecordDto,
)
from veterinary_agent.safety_trigger_agent.enums import (
    ConfirmationMode,
    EmergencyHintCode,
    SafetyTraceWriteStatus,
    SafetyTriggerDraftStatus,
    SafetyTriggerErrorCode,
    SafetyTriggerOperation,
)
from veterinary_agent.safety_trigger_agent.errors import (
    SafetyTriggerError,
    SafetyTriggerErrorDto,
    build_safety_trigger_error_dto,
    is_safety_trigger_error_retryable_by_default,
)
from veterinary_agent.safety_trigger_agent.node import SafetyTriggerAgentGraphNode
from veterinary_agent.safety_trigger_agent.ports import (
    SafetyToolPermissionPort,
    TODO_SAFETY_TOOL_PERMISSION_ERROR_CODE,
    TodoSafetyToolPermissionPort,
)
from veterinary_agent.safety_trigger_agent.service import (
    DefaultSafetyTriggerAgent,
    SafetyTriggerAgent,
    create_default_safety_trigger_agent,
)
from veterinary_agent.safety_trigger_agent.trace import (
    LogicTraceSafetyTriggerTraceSink,
    SafetyTriggerTraceSink,
    TODO_SAFETY_TRACE_ERROR_CODE,
    TodoSafetyTriggerTraceSink,
)

__all__: tuple[str, ...] = (
    "ConfirmationMode",
    "DefaultSafetyTriggerAgent",
    "EmergencyBriefDto",
    "EmergencyHintCode",
    "JsonMap",
    "KeyConfirmationPlanDto",
    "LogicTraceSafetyTriggerTraceSink",
    "SafetyRagPolicySummaryDto",
    "SafetyRequirementSetDto",
    "SafetySignalSummaryDto",
    "SafetyToolPermissionPort",
    "SafetyTraceWriteResultDto",
    "SafetyTraceWriteStatus",
    "SafetyTriggerAgent",
    "SafetyTriggerAgentGraphNode",
    "SafetyTriggerDraftDto",
    "SafetyTriggerDraftStatus",
    "SafetyTriggerDto",
    "SafetyTriggerError",
    "SafetyTriggerErrorCode",
    "SafetyTriggerErrorDto",
    "SafetyTriggerOperation",
    "SafetyTriggerRequestDto",
    "SafetyTriggerSelfCheckSummaryDto",
    "SafetyTriggerTracePatchDto",
    "SafetyTriggerTraceRecordDto",
    "SafetyTriggerTraceSink",
    "TODO_SAFETY_TOOL_PERMISSION_ERROR_CODE",
    "TODO_SAFETY_TRACE_ERROR_CODE",
    "TodoSafetyToolPermissionPort",
    "TodoSafetyTriggerTraceSink",
    "build_safety_trigger_error_dto",
    "create_default_safety_trigger_agent",
    "is_safety_trigger_error_retryable_by_default",
)
