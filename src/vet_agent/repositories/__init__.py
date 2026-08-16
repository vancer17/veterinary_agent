"""
文件：src/vet_agent/repositories/__init__.py
作用：作为 repositories 包入口，提供规则库与 RAG 知识库的数据访问能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .background_tasks import BackgroundTaskRepository, BackgroundTaskRepositoryError, PostgresBackgroundTaskRepository
from .knowledge import KnowledgeHit
from .consultation_state import (
    DEFAULT_TASK_KEY as CONSULTATION_DEFAULT_TASK_KEY,
    ConsultationStateRepository,
    JsonConsultationStateRepository,
    PostgresConsultationStateRepository,
)
from .memory_read import (
    JsonMemoryReadRepository,
    MemoryReadRepositoryError,
    PostgresMemoryReadRepository,
)
from .memory_write import MemoryWriteRepository, PostgresMemoryWriteRepository
from .rules import (
    ConsultationDomainRule,
    ConsultationRuleSet,
    ConsultationSlotRule,
    FallbackRuleRepository,
    FileRuleRepository,
    PostgresRuleRepository,
    RuleRepository,
)
from .scope import (
    PostgresScopeRepository,
    ScopeRepository,
    SessionBinding,
    VerifiedPetProfile,
)
from .turn_execution import (
    PostgresTurnExecutionRepository,
    TurnExecutionRepository,
    TurnExecutionRepositoryError,
    TurnIdempotencyClaim,
    TurnIdempotencyClaimStatus,
)

__all__ = [
    "BackgroundTaskRepository",
    "BackgroundTaskRepositoryError",
    "CONSULTATION_DEFAULT_TASK_KEY",
    "ConsultationDomainRule",
    "ConsultationRuleSet",
    "ConsultationSlotRule",
    "ConsultationStateRepository",
    "FallbackRuleRepository",
    "FileRuleRepository",
    "KnowledgeHit",
    "JsonMemoryReadRepository",
    "JsonConsultationStateRepository",
    "MemoryReadRepositoryError",
    "MemoryWriteRepository",
    "PostgresMemoryReadRepository",
    "PostgresMemoryWriteRepository",
    "PostgresBackgroundTaskRepository",
    "PostgresConsultationStateRepository",
    "PostgresRuleRepository",
    "PostgresScopeRepository",
    "PostgresTurnExecutionRepository",
    "RuleRepository",
    "ScopeRepository",
    "SessionBinding",
    "TurnExecutionRepository",
    "TurnExecutionRepositoryError",
    "TurnIdempotencyClaim",
    "TurnIdempotencyClaimStatus",
    "VerifiedPetProfile",
]
