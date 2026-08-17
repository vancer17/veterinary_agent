"""
=============================================================================
文件：src/vet_agent/rag_miss_governance/errors.py
作用：定义 RAG 无命中治理链路的统一异常类型。
范围：仅表达治理记录写入、参数规范化和仓储访问失败；不参与回答生成、
      知识召回、问诊状态裁决或任何运行时回退。
说明：本模块用于将“无命中可治理”与“无命中可回答”严格分离。
=============================================================================
"""

from __future__ import annotations

from typing import Any


class RagMissGovernanceError(RuntimeError):
    """表示 RAG 无命中治理链路失败。

    :return: 无返回值；该异常只用于治理记录链路，不应被解释为可回答状态。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化 RAG 无命中治理异常。

        :param message: 面向调用方和排障日志的错误说明。
        :param details: 可序列化错误细节。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}
