"""
核心执行上下文最小契约。

该模块为模型调用、审计事件和后续 pipeline 提供统一执行定位信息。当前实现
只包含 ``core.llm`` 必需字段，不定义持久化身份或工作流身份。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """一次核心执行的脱敏定位上下文。

    :ivar execution_id: 当前执行链的稳定标识。
    :ivar correlation_id: 可选跨服务关联标识。
    :ivar step: 当前执行步骤名，用于审计与追踪。
    """

    execution_id: str
    correlation_id: str | None
    step: str


__all__ = ["ExecutionContext"]
