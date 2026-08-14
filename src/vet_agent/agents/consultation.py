"""
=============================================================================
文件：src/vet_agent/agents/consultation.py
作用：为历史调用点提供问诊状态与回答充分性能力的兼容导出。
范围：本文件不承载任何本地规则状态机或硬编码回答判断逻辑，仅将稳定契约
      转发到 vet_agent.consultation_state 包。
说明：新链路的实际实现位于 vet_agent.consultation_state.service 与
      vet_agent.consultation_state.policy；本文件仅用于平滑迁移和外部兼容。
=============================================================================
"""

from __future__ import annotations

from vet_agent.consultation_state import (
    AnswerabilityDecision,
    AnswerabilityEvaluator,
    ConsultationDecision,
    ConsultationState,
    ConsultationStateAgent,
    ConsultationStatePolicyAction,
    ConsultationStatePolicyContext,
    ConsultationStatePolicyInput,
    ConsultationStatePolicyIntent,
    ConsultationStatePolicyLimits,
    ConsultationStatePolicyState,
    ConsultationStateService,
)

__all__ = [
    "AnswerabilityDecision",
    "AnswerabilityEvaluator",
    "ConsultationDecision",
    "ConsultationState",
    "ConsultationStateAgent",
    "ConsultationStatePolicyAction",
    "ConsultationStatePolicyContext",
    "ConsultationStatePolicyInput",
    "ConsultationStatePolicyIntent",
    "ConsultationStatePolicyLimits",
    "ConsultationStatePolicyState",
    "ConsultationStateService",
]
