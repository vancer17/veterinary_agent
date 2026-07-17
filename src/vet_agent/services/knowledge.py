"""
文件：src/vet_agent/services/knowledge.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from vet_agent import Evidence
from vet_agent.repositories import KnowledgeHit, KnowledgeRepository, evidence_from_hits


class KnowledgeService:
    """Grounding facade backed by PostgreSQL/pgvector or seed-file fallback."""

    def __init__(self, repository: KnowledgeRepository) -> None:
        """初始化当前对象。

        :param repository: 参数 repository。
        :return: 无返回值。
        """
        self.repository = repository

    async def retrieve(self, query: str) -> tuple[list[KnowledgeHit], list[Evidence]]:
        """检索与查询相关的知识片段。

        :param query: 检索查询。
        :return: 返回函数执行结果。
        """
        hits = self.repository.retrieve(query)
        return hits, evidence_from_hits(hits)

    def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return self.repository.is_ready()
