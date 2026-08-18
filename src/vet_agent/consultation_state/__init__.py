"""
=============================================================================
文件：src/vet_agent/consultation_state/__init__.py
作用：作为问诊状态与回答充分性迁移包入口，集中暴露状态模型、策略契约、
      证据构建与服务编排能力。
范围：供 agents、orchestrator、container 与测试代码通过包级导出访问稳定契约，
      不直接依赖内部实现文件。
说明：本包用于替代旧版 ConsultationStateAgent 内聚的硬编码状态机与规则判断，
      主链路裁决应经由 OPA 策略客户端完成。
=============================================================================
"""

from .errors import (
    ConsultationStateContractError,
    ConsultationStateDependencyError,
    ConsultationStateError,
    ConsultationStatePolicyRejectedError,
)
from .models import (
    AnswerabilityDecision,
    ConsultationDecision,
    ConsultationState,
    ConsultationStatePolicyAction,
    ConsultationStatePolicyContext,
    ConsultationStatePolicyInput,
    ConsultationStatePolicyIntent,
    ConsultationStatePolicyLimits,
    ConsultationStatePolicyState,
)
from .policy import (
    ConsultationAnswerabilityPolicyClient,
    LocalConsultationAnswerabilityPolicyClient,
    OpaConsultationAnswerabilityPolicyClient,
)
from .service import ConsultationStateAgent, ConsultationStateService

AnswerabilityEvaluator = ConsultationStateService

__all__ = [
    "AnswerabilityDecision",
    "AnswerabilityEvaluator",
    "ConsultationAnswerabilityPolicyClient",
    "ConsultationDecision",
    "ConsultationState",
    "ConsultationStateAgent",
    "ConsultationStateContractError",
    "ConsultationStateDependencyError",
    "ConsultationStateError",
    "ConsultationStatePolicyAction",
    "ConsultationStatePolicyContext",
    "ConsultationStatePolicyInput",
    "ConsultationStatePolicyIntent",
    "ConsultationStatePolicyLimits",
    "ConsultationStatePolicyRejectedError",
    "ConsultationStatePolicyState",
    "ConsultationStateService",
    "LocalConsultationAnswerabilityPolicyClient",
    "OpaConsultationAnswerabilityPolicyClient",
]
