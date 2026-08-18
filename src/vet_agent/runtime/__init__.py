"""
文件：src/vet_agent/runtime/__init__.py
作用：作为 runtime 包入口，封装模型调用、向量生成与外部运行时能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .embeddings import EmbeddingClient, QwenEmbeddingClient
from .qwen import QwenClient

__all__ = [
    "EmbeddingClient",
    "OfflineStartupCheckResult",
    "QwenClient",
    "QwenEmbeddingClient",
    "run_offline_startup_checks",
]


def __getattr__(name: str) -> object:
    """按名称延迟解析 runtime 包公共对象。

    :param name: 待解析的公共对象名称。
    :return: 返回对应公共对象。
    :raises AttributeError: 名称未在 runtime 包公共能力中声明时抛出。
    """
    if name in {"OfflineStartupCheckResult", "run_offline_startup_checks"}:
        from .offline_startup import OfflineStartupCheckResult, run_offline_startup_checks

        values = {
            "OfflineStartupCheckResult": OfflineStartupCheckResult,
            "run_offline_startup_checks": run_offline_startup_checks,
        }
        return values[name]
    raise AttributeError(f"module 'vet_agent.runtime' has no attribute {name!r}")
