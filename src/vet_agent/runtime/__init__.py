"""
文件：src/vet_agent/runtime/__init__.py
作用：作为 runtime 包入口，封装模型调用、向量生成与外部运行时能力。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .embeddings import QwenEmbeddingClient
from .qwen import QwenClient

__all__ = ["QwenClient", "QwenEmbeddingClient"]
