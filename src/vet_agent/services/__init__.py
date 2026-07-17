"""
文件：src/vet_agent/services/__init__.py
作用：作为 services 包入口，承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .access_control import (
    AccessControlService,
    JsonAccessControlStore,
    PostgresAccessControlStore,
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
from .semantic_memory import DisabledSemanticMemory, make_semantic_memory
from .trace import LogicTraceStore

__all__ = [
    "AccessControlService",
    "DisabledSemanticMemory",
    "JsonAccessControlStore",
    "JsonRagGovernanceStore",
    "JsonReportStore",
    "KnowledgeService",
    "LogicTraceStore",
    "MemoryService",
    "PetContext",
    "PetContextProvider",
    "PostgresAccessControlStore",
    "PostgresLogicTraceStore",
    "PostgresMemoryService",
    "PostgresRagGovernanceStore",
    "PostgresReportStore",
    "RagGovernanceService",
    "ReasoningDisplayBuilder",
    "ReportIngestionService",
    "make_semantic_memory",
]
