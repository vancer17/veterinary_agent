"""
文件：src/vet_agent/memory_extraction/__init__.py
作用：作为长期记忆候选抽取包入口，集中暴露结构化契约与抽取服务。
说明：上游编排层、测试替身与兼容入口应仅通过本包顶层导出的对象访问能力，
      不得直接引用内部实现文件。
"""

from .models import (
    MemoryCandidateProposal,
    MemoryExtractionAssertionStatus,
    MemoryExtractionDurability,
    MemoryExtractionEntryKind,
    MemoryExtractionEvidenceKind,
    MemoryExtractionFactType,
    MemoryExtractionOutput,
    MemoryExtractionRequest,
    MemoryExtractionResult,
    MemoryExtractionSourceEntry,
    MemoryExtractionStrategy,
    MemoryExtractionSubjectScope,
    MemoryExtractionTemporalScope,
    MemoryFactCandidate,
)
from .service import MemoryExtractionAgent

__all__ = [
    "MemoryCandidateProposal",
    "MemoryExtractionAgent",
    "MemoryExtractionAssertionStatus",
    "MemoryExtractionDurability",
    "MemoryExtractionEntryKind",
    "MemoryExtractionEvidenceKind",
    "MemoryExtractionFactType",
    "MemoryExtractionOutput",
    "MemoryExtractionRequest",
    "MemoryExtractionResult",
    "MemoryExtractionSourceEntry",
    "MemoryExtractionStrategy",
    "MemoryExtractionSubjectScope",
    "MemoryExtractionTemporalScope",
    "MemoryFactCandidate",
]
