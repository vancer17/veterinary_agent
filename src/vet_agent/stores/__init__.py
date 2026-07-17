"""
文件：src/vet_agent/stores/__init__.py
作用：作为 stores 包入口，提供轻量文件存储封装。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .json_store import JsonDocumentStore

__all__ = ["JsonDocumentStore"]
