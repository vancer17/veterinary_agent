"""
=============================================================================
文件：src/vet_agent/followup_rag/__init__.py
作用：作为追问相关 RAG 迁移包入口，集中暴露服务、协议、模型与生产适配器。
范围：供 container、orchestrator 与测试代码通过包级导出访问稳定契约；
      调用方不直接引用包内实现文件，避免跨包穿透实现细节。
说明：本包替代旧版默认追问模板、关键词规划器和手写 JSON 解析 Agent。
=============================================================================
"""

from .errors import FollowupRagContractError, FollowupRagDependencyError, FollowupRagError
from .models import (
    FollowupRagPlan,
    FollowupRagQuestion,
    FollowupRagRequest,
    FollowupRagRetrievalResult,
    FollowupRagStrategy,
)
from .planner import FollowupQuestionItem, FollowupQuestionPlannerOutput, LiteLlmFollowupQuestionPlanner
from .ports import (
    FollowupQuestionPlanner,
    FollowupRagKnowledgeRepository,
    FollowupRagRetriever,
    FollowupRagServiceProtocol,
)
from .query_builder import FollowupRagQueryBuilder
from .retriever import LlamaIndexFollowupKnowledgeRetriever, PostgresFollowupRagKnowledgeRepository
from .service import DEFAULT_FOLLOWUP_RAG_CHUNK_TYPES, FollowupRagService

__all__ = [
    "DEFAULT_FOLLOWUP_RAG_CHUNK_TYPES",
    "FollowupQuestionItem",
    "FollowupQuestionPlanner",
    "FollowupQuestionPlannerOutput",
    "FollowupRagContractError",
    "FollowupRagDependencyError",
    "FollowupRagError",
    "FollowupRagKnowledgeRepository",
    "FollowupRagPlan",
    "FollowupRagQuestion",
    "FollowupRagRequest",
    "FollowupRagRetrievalResult",
    "FollowupRagRetriever",
    "FollowupRagService",
    "FollowupRagServiceProtocol",
    "FollowupRagStrategy",
    "FollowupRagQueryBuilder",
    "LiteLlmFollowupQuestionPlanner",
    "LlamaIndexFollowupKnowledgeRetriever",
    "PostgresFollowupRagKnowledgeRepository",
]
