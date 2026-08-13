"""
文件：src/vet_agent/memory/errors.py
作用：定义记忆读取数据链的异常类型。
范围：用于区分 PostgreSQL 权威记忆读取失败、Mem0 语义投影失败、范围校验失败与上下文编译失败。
说明：本文件仅提供异常契约，不执行数据库、网络或模型调用；跨包调用应通过 vet_agent.memory 顶层导出。
"""

from __future__ import annotations

from typing import Any


class MemoryReadError(RuntimeError):
    """表示记忆读取数据链的基础异常。

    :return: 无返回值。
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """初始化记忆读取异常。

        :param message: 错误描述。
        :param details: 结构化错误详情。
        :return: 无返回值。
        """
        super().__init__(message)
        self.details = details or {}


class MemoryReadDependencyError(MemoryReadError):
    """表示记忆读取依赖不可用且不能静默回退。

    :return: 无返回值。
    """


class MemoryProjectionClientError(MemoryReadError):
    """表示 Mem0 语义投影客户端调用失败或返回契约非法。

    :return: 无返回值。
    """


class MemoryProjectionScopeError(MemoryProjectionClientError):
    """表示 Mem0 语义投影结果不满足当前用户与宠物范围约束。

    :return: 无返回值。
    """
