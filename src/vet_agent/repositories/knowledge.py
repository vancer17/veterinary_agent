"""
=============================================================================
文件：src/vet_agent/repositories/knowledge.py
作用：定义 RAG 知识命中的跨领域公共值对象。
范围：供回答 RAG 与追问 RAG 复用，不承载数据库查询、文本检索、证据编译或文件回退。
说明：旧版 FileKnowledgeRepository、Postgres 文本相似度检索和 fallback 仓储已移出生产链路；
      具体召回实现应由 answer_rag 或 followup_rag 包内仓储协议隔离。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KnowledgeHit:
    """表示一次 RAG 知识召回命中。

    :param title: 知识片段标题。
    :param summary: 知识片段摘要或正文。
    :param source: 知识来源名称或治理来源标识。
    :param public_citation: 是否允许作为公开引用证据展示。
    :param score: 本轮召回分数。
    :param source_url: 知识来源 URL。
    :param metadata: 知识治理和检索审计元数据。
    :return: 无返回值；该对象只表达召回结果，不执行检索逻辑。
    """

    title: str
    summary: str
    source: str
    public_citation: bool
    score: float = 0.0
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
