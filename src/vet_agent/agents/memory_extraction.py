"""
文件：src/vet_agent/agents/memory_extraction.py
作用：为历史调用路径提供长期记忆候选抽取能力的兼容入口。
说明：新实现已迁移至 vet_agent.memory_extraction 包；本文件仅保留向后兼容的
      顶层导出，不承载业务逻辑、规则回退或写入裁决。
"""

from vet_agent.memory_extraction import (
    MemoryCandidateProposal,
    MemoryExtractionAgent,
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
