"""
文件：src/vet_agent/observability/__init__.py
作用：作为可观察性包入口，集中暴露 Agent 审计路径等稳定契约。
范围：面向主链路、测试和后续审计日志扩展提供统一命名入口。
说明：跨包调用应通过本文件导出的对象访问，避免直接依赖包内实现细节。
"""

from .agent_path import AgentPathNode, build_agent_path

__all__ = [
    "AgentPathNode",
    "build_agent_path",
]
