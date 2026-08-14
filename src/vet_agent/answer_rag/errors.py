"""
=============================================================================
文件：src/vet_agent/answer_rag/errors.py
作用：定义回答相关 RAG 迁移链路的统一异常类型。
范围：区分业务契约失败与外部依赖失败，供编排器、测试和运维审计明确失败原因。
说明：回答 RAG 不提供文本、seed、默认知识或硬编码回复回退；异常会沿主链路显式暴露。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class AnswerRagError(RuntimeError):
    """表示回答相关 RAG 链路的基础异常。

    :return: 无返回值；该异常作为回答 RAG 所有显式失败的公共父类。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化回答 RAG 基础异常。

        :param message: 面向调用方和日志的错误说明。
        :param details: 可序列化的错误细节，用于审计和测试断言。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class AnswerRagContractError(AnswerRagError):
    """表示回答 RAG 输入、配置或结果不满足业务契约。

    :return: 无返回值；该异常通常说明上游状态或服务配置存在明确错误。
    """


class AnswerRagDependencyError(AnswerRagError):
    """表示回答 RAG 所需数据库、embedding 或检索依赖不可用。

    :return: 无返回值；该异常用于遵循 Fail Fast，不触发低质量回退路径。
    """
