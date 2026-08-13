"""
文件：src/vet_agent/memory/__init__.py
作用：作为 memory 包入口，集中暴露记忆读取领域的稳定公共能力。
范围：向 Agent 主链路、容器装配和测试替身暴露结构化读取模型、读取服务、上下文编译器与 Mem0 投影客户端工厂。
说明：跨包调用不得直接引用 memory 包内部实现文件，应通过本包顶层导出使用。
"""

from .context_builder import MemoryContextBuilder
from .errors import (
    MemoryProjectionClientError,
    MemoryProjectionScopeError,
    MemoryReadDependencyError,
    MemoryReadError,
)
from .models import (
    AuthoritativeMemoryFact,
    MemoryPromptContext,
    MemoryReadAudit,
    MemoryReadBundle,
    PetMemoryEpisode,
    SemanticRecollection,
    SessionMemoryTurn,
)
from .ports import MemoryProjectionClient, MemoryReadRepository
from .projection import (
    DisabledMemoryProjectionClient,
    Mem0MemoryProjectionClient,
    make_memory_projection_client,
)
from .read_service import MemoryReadService

__all__ = [
    "AuthoritativeMemoryFact",
    "DisabledMemoryProjectionClient",
    "Mem0MemoryProjectionClient",
    "MemoryContextBuilder",
    "MemoryProjectionClient",
    "MemoryProjectionClientError",
    "MemoryProjectionScopeError",
    "MemoryPromptContext",
    "MemoryReadAudit",
    "MemoryReadBundle",
    "MemoryReadDependencyError",
    "MemoryReadError",
    "MemoryReadRepository",
    "MemoryReadService",
    "PetMemoryEpisode",
    "SemanticRecollection",
    "SessionMemoryTurn",
    "make_memory_projection_client",
]
