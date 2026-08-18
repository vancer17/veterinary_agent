"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/__init__.py
作用：作为 RAG 无命中治理包入口，集中暴露领域模型、协议、服务与生产仓储。
范围：供 container、orchestrator 与测试代码通过包级导出访问稳定契约；
      调用方不直接引用包内实现文件。
说明：本包只负责知识缺口治理留痕，不参与回答 RAG 召回和回复生成。
=============================================================================
"""

from .errors import RagMissGovernanceError
from .models import (
    RagMissRecord,
    RagMissRecordDraft,
    RagMissRecordRequest,
    RagMissRecordView,
    RagMissScope,
    RagMissStatus,
)
from .ports import RagMissGovernanceProtocol, RagMissRecorderProtocol, RagMissRepositoryProtocol
from .postgres_repository import PostgresRagMissRepository
from .service import DisabledRagMissRecorder, RagMissGovernanceService

__all__ = [
    "DisabledRagMissRecorder",
    "PostgresRagMissRepository",
    "RagMissGovernanceError",
    "RagMissGovernanceService",
    "RagMissRecord",
    "RagMissRecordDraft",
    "RagMissRecordRequest",
    "RagMissRecordView",
    "RagMissGovernanceProtocol",
    "RagMissRecorderProtocol",
    "RagMissRepositoryProtocol",
    "RagMissScope",
    "RagMissStatus",
]
