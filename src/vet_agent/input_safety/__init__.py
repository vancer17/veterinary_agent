"""
文件：src/vet_agent/input_safety/__init__.py
作用：作为基础输入安全候选包入口，集中暴露候选采集、策略裁决与仓储能力。
范围：本包位于 Agent 主链路开始阶段，负责将结构化输入安全候选交给 OPA 裁决，不承载临床医学推理。
说明：跨包调用应通过本文件导出的稳定对象访问，避免直接引用包内实现细节。
"""

from .detectors import GuardrailsInputSafetyDetector, InputSafetyDetector
from .models import (
    InputSafetyCandidate,
    InputSafetyCandidateCategory,
    InputSafetyCandidateSource,
    InputSafetyDecision,
    InputSafetyDecisionAction,
    InputSafetyRequestContext,
)
from .policy import (
    DisabledInputSafetyPolicyClient,
    InputSafetyPolicyClient,
    LocalInputSafetyPolicyClient,
    OpaInputSafetyPolicyClient,
)
from .repository import (
    InputSafetyCandidateDefinition,
    InputSafetyRepository,
    PostgresInputSafetyRepository,
    StaticInputSafetyRepository,
)
from .service import InputSafetyService

__all__ = [
    "GuardrailsInputSafetyDetector",
    "InputSafetyCandidate",
    "InputSafetyCandidateCategory",
    "InputSafetyCandidateDefinition",
    "InputSafetyCandidateSource",
    "InputSafetyDecision",
    "InputSafetyDecisionAction",
    "InputSafetyDetector",
    "InputSafetyPolicyClient",
    "InputSafetyRequestContext",
    "InputSafetyRepository",
    "InputSafetyService",
    "LocalInputSafetyPolicyClient",
    "OpaInputSafetyPolicyClient",
    "PostgresInputSafetyRepository",
    "StaticInputSafetyRepository",
    "DisabledInputSafetyPolicyClient",
]
