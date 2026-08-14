"""
文件：src/vet_agent/agents/__init__.py
作用：作为 agents 包入口，提供多 Agent 协作中的任务拆分、安全、问诊与记忆抽取能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""

from .consultation import (
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
from .memory_extraction import MemoryExtractionAgent, MemoryFactCandidate
from .safety import SafetyAssessment
from .semantic_extractor import (
    ConsultationFactCategory,
    ConsultationFactKey,
    ConsultationFactStatus,
    ConsultationSemanticExtractorAgent,
    ConsultationSemanticStrategy,
    SemanticExtractionResult,
    SemanticFact,
    SemanticIntent,
    SemanticObservation,
)
from .task_router import TaskRouterAgent

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
    "MemoryExtractionAgent",
    "MemoryFactCandidate",
    "SafetyAssessment",
    "ConsultationFactCategory",
    "ConsultationFactKey",
    "ConsultationFactStatus",
    "ConsultationSemanticExtractorAgent",
    "ConsultationSemanticStrategy",
    "SemanticExtractionResult",
    "SemanticFact",
    "SemanticIntent",
    "SemanticObservation",
    "TaskRouterAgent",
]
