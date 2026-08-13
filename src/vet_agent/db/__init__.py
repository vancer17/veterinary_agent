"""
文件：src/vet_agent/db/__init__.py
作用：作为 db 包入口，提供数据库模型、连接与会话管理能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .models import (
    Base,
    ClinicalSafetyAssetModel,
    ClinicalSafetyChunkModel,
    ClinicalConditionCardModel,
    ConsultationDomainModel,
    ConsultationSlotModel,
    ConsultationStateModel,
    ConversationTurnModel,
    IdempotencyRecordModel,
    InputSafetyCandidateDefinitionModel,
    KnowledgeIngestionBatchModel,
    KnowledgeChunkModel,
    LogicTraceModel,
    PetMemoryEpisodeModel,
    PetMemoryFactModel,
    PetProfileModel,
    PetReportItemModel,
    PetReportModel,
    PetSessionBindingModel,
    RagAuditEventModel,
)
from .session import make_engine, make_session_factory, session_scope, sqlalchemy_url

__all__ = [
    "Base",
    "ClinicalConditionCardModel",
    "ClinicalSafetyAssetModel",
    "ClinicalSafetyChunkModel",
    "ConsultationDomainModel",
    "ConsultationSlotModel",
    "ConsultationStateModel",
    "ConversationTurnModel",
    "IdempotencyRecordModel",
    "InputSafetyCandidateDefinitionModel",
    "KnowledgeChunkModel",
    "KnowledgeIngestionBatchModel",
    "LogicTraceModel",
    "PetMemoryEpisodeModel",
    "PetMemoryFactModel",
    "PetProfileModel",
    "PetReportItemModel",
    "PetReportModel",
    "PetSessionBindingModel",
    "RagAuditEventModel",
    "make_engine",
    "make_session_factory",
    "session_scope",
    "sqlalchemy_url",
]
