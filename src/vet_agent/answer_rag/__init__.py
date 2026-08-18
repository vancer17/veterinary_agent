"""
=============================================================================
文件：src/vet_agent/answer_rag/__init__.py
作用：作为回答相关 RAG 迁移包入口，集中暴露服务、协议、模型与生产适配器。
范围：供 container、orchestrator 与测试代码通过包级导出访问稳定契约；
      调用方不直接引用包内实现文件，避免跨包穿透实现细节。
说明：本包替代旧版 KnowledgeService.retrieve、文本相似度、seed 文件和默认知识回退路径。
=============================================================================
"""

from .errors import AnswerRagContractError, AnswerRagDependencyError, AnswerRagError
from .models import AnswerRagRequest, AnswerRagResult, AnswerRagRetrievalResult, AnswerRagStrategy
from .ports import AnswerRagKnowledgeRepository, AnswerRagRetriever, AnswerRagServiceProtocol
from .query_builder import AnswerRagQueryBuilder
from .retriever import LlamaIndexAnswerKnowledgeRetriever, PostgresAnswerRagKnowledgeRepository
from .service import DEFAULT_ANSWER_RAG_CHUNK_TYPES, AnswerRagService

__all__ = [
    "DEFAULT_ANSWER_RAG_CHUNK_TYPES",
    "AnswerRagContractError",
    "AnswerRagDependencyError",
    "AnswerRagError",
    "AnswerRagKnowledgeRepository",
    "AnswerRagQueryBuilder",
    "AnswerRagRequest",
    "AnswerRagResult",
    "AnswerRagRetrievalResult",
    "AnswerRagRetriever",
    "AnswerRagService",
    "AnswerRagServiceProtocol",
    "AnswerRagStrategy",
    "LlamaIndexAnswerKnowledgeRetriever",
    "PostgresAnswerRagKnowledgeRepository",
]
