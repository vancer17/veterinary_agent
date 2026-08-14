"""
文件：src/vet_agent/output_safety/__init__.py
作用：作为输出安全包入口，集中暴露候选采集、策略裁决、仓储与服务能力。
范围：本包位于回复生成之后、记忆抽取与持久化之前，替代旧输出清洗与安全复核实现。
说明：跨包调用应通过本文件导出的稳定对象访问，避免直接依赖包内实现细节。
"""

from .detectors import GuardrailsOutputSafetyDetector, OutputSafetyDetector
from .models import (
    OutputSafetyCandidate,
    OutputSafetyCandidateCategory,
    OutputSafetyCandidateSource,
    OutputSafetyDecision,
    OutputSafetyDecisionAction,
    OutputSafetyMode,
    OutputSafetyReviewContext,
    OutputSafetySegment,
)
from .policy import (
    DisabledOutputSafetyPolicyClient,
    LocalOutputSafetyPolicyClient,
    OpaOutputSafetyPolicyClient,
    OutputSafetyPolicyClient,
)
from .repository import (
    OutputSafetyCandidateDefinition,
    OutputSafetyRepository,
    PostgresOutputSafetyRepository,
    StaticOutputSafetyRepository,
)
from .service import OutputSafetyService

__all__ = [
    "DisabledOutputSafetyPolicyClient",
    "GuardrailsOutputSafetyDetector",
    "LocalOutputSafetyPolicyClient",
    "OpaOutputSafetyPolicyClient",
    "OutputSafetyCandidate",
    "OutputSafetyCandidateCategory",
    "OutputSafetyCandidateDefinition",
    "OutputSafetyCandidateSource",
    "OutputSafetyDecision",
    "OutputSafetyDecisionAction",
    "OutputSafetyDetector",
    "OutputSafetyMode",
    "OutputSafetyPolicyClient",
    "OutputSafetyRepository",
    "OutputSafetyReviewContext",
    "OutputSafetySegment",
    "OutputSafetyService",
    "PostgresOutputSafetyRepository",
    "StaticOutputSafetyRepository",
]
