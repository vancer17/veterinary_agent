"""
文件：src/vet_agent/repositories/__init__.py
作用：作为 repositories 包入口，提供规则库与 RAG 知识库的数据访问能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .knowledge import (
    FallbackKnowledgeRepository,
    FileKnowledgeRepository,
    KnowledgeHit,
    KnowledgeRepository,
    PostgresKnowledgeRepository,
    evidence_from_hits,
)
from .rules import (
    ConsultationDomainRule,
    ConsultationRuleSet,
    ConsultationSlotRule,
    FallbackRuleRepository,
    FileRuleRepository,
    PostgresRuleRepository,
    RuleRepository,
    SafetyRule,
    compile_regex,
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
    "ConsultationDomainRule",
    "ConsultationRuleSet",
    "ConsultationSlotRule",
    "FallbackKnowledgeRepository",
    "FallbackRuleRepository",
    "FileKnowledgeRepository",
    "FileRuleRepository",
    "KnowledgeHit",
    "KnowledgeRepository",
    "PostgresKnowledgeRepository",
    "PostgresRuleRepository",
    "PostgresScopeRepository",
    "PostgresTurnExecutionRepository",
    "RuleRepository",
    "ScopeRepository",
    "SafetyRule",
    "SessionBinding",
    "TurnExecutionRepository",
    "TurnExecutionRepositoryError",
    "TurnIdempotencyClaim",
    "TurnIdempotencyClaimStatus",
    "VerifiedPetProfile",
    "compile_regex",
    "evidence_from_hits",
]
