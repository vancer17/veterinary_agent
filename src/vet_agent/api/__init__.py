"""
文件：src/vet_agent/api/__init__.py
作用：作为 api 包入口，提供面向业务侧的 HTTP API 路由。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .admin_routes import router as admin_router
from .memory_routes import router as memory_router
from .report_routes import router as report_router

__all__ = ["admin_router", "memory_router", "report_router"]
