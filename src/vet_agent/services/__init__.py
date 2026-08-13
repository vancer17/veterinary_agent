"""
文件：src/vet_agent/services/__init__.py
作用：作为 services 包入口，承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .access_control import AccessControlService
from .clinical_knowledge import (
    ClinicalKnowledgeService,
    JsonClinicalKnowledgeStore,
    PostgresClinicalKnowledgeStore,
)
from .context import PetContext, PetContextProvider
from .knowledge import KnowledgeService
from .memory import MemoryService
from .postgres_memory import PostgresMemoryService
from .postgres_trace import PostgresLogicTraceStore
from .rag_governance import (
    JsonRagGovernanceStore,
    PostgresRagGovernanceStore,
    RagGovernanceService,
)
from .reasoning_display import ReasoningDisplayBuilder
from .reports import JsonReportStore, PostgresReportStore, ReportIngestionService
from .semantic_memory import DisabledSemanticMemory, SemanticMemoryWriter, make_semantic_memory
from .scope import (
    AuthenticatedPrincipal,
    DeterministicScopePolicyEvaluator,
    ScopeContext,
    ScopeContextService,
    ScopeDecision,
    ScopeDecisionAction,
    ScopePolicyEvaluator,
)
from .trace import LogicTraceStore
from .turn_execution import (
    TurnExecutionBusyError,
    TurnExecutionConflictError,
    TurnExecutionDependencyError,
    TurnExecutionError,
    TurnExecutionGate,
    TurnExecutionGateProtocol,
    TurnExecutor,
)

__all__ = [
    "AccessControlService",
    "AuthenticatedPrincipal",
    "ClinicalKnowledgeService",
    "DeterministicScopePolicyEvaluator",
    "DisabledSemanticMemory",
    "JsonClinicalKnowledgeStore",
    "JsonRagGovernanceStore",
    "JsonReportStore",
    "KnowledgeService",
    "LogicTraceStore",
    "MemoryService",
    "PetContext",
    "PetContextProvider",
    "PostgresClinicalKnowledgeStore",
    "PostgresLogicTraceStore",
    "PostgresMemoryService",
    "PostgresRagGovernanceStore",
    "PostgresReportStore",
    "RagGovernanceService",
    "ReasoningDisplayBuilder",
    "ReportIngestionService",
    "ScopeContext",
    "ScopeContextService",
    "ScopeDecision",
    "ScopeDecisionAction",
    "ScopePolicyEvaluator",
    "SemanticMemoryWriter",
    "TurnExecutionBusyError",
    "TurnExecutionConflictError",
    "TurnExecutionDependencyError",
    "TurnExecutionError",
    "TurnExecutionGate",
    "TurnExecutionGateProtocol",
    "TurnExecutor",
    "make_semantic_memory",
]
