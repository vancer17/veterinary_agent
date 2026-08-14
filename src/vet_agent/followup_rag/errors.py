"""
=============================================================================
文件：src/vet_agent/followup_rag/errors.py
作用：定义追问相关 RAG 迁移链路的领域异常。
范围：用于区分追问 RAG 的依赖故障、结构化契约故障与通用领域错误；
      不承载检索、模型调用、问诊状态更新或入口层 HTTP 映射。
说明：追问 RAG 坚持 Fail Fast。依赖不可用、知识无合格命中、模型结构化
      输出非法或追问计划不满足业务契约时，均通过本文件异常显式暴露。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class FollowupRagError(Exception):
    """表示追问相关 RAG 链路的基础领域异常。

    :param message: 错误描述。
    :param details: 结构化排障信息。
    :return: 无返回值；异常实例用于上游入口层转换为标准错误响应。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化追问 RAG 领域异常。

        :param message: 错误描述。
        :param details: 结构化排障信息。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class FollowupRagDependencyError(FollowupRagError):
    """表示追问相关 RAG 的外部依赖或基础设施故障。

    :return: 无返回值；该异常用于表达 PostgreSQL、pgvector、embedding、
        LlamaIndex 适配或 LiteLLM 结构化模型调用不可用。
    """


class FollowupRagContractError(FollowupRagError):
    """表示追问相关 RAG 的结构化契约不满足要求。

    :return: 无返回值；该异常用于表达追问计划为空、slot 非法、证据引用非法
        或模型输出与 Pydantic/业务契约不一致。
    """
