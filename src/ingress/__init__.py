"""
文件：src/ingress/__init__.py
作用：作为 ingress 包入口，提供外部 API 入口、请求 DTO、错误处理与编排器适配。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""



from .errors import (
    ApiIngressError,
    ConflictError,
    ErrorCode,
    ErrorResponse,
    ForbiddenError,
    InvalidRequestError,
    MissingRequiredContextError,
    OrchestratorTimeoutError,
    OrchestratorUnavailableError,
    PayloadTooLargeError,
    UnauthorizedError,
)
from .orchestrator import Orchestrator, get_orchestrator, set_orchestrator

__all__ = [
    "ApiIngressError",
    "ConflictError",
    "ErrorCode",
    "ErrorResponse",
    "ForbiddenError",
    "InvalidRequestError",
    "MissingRequiredContextError",
    "Orchestrator",
    "OrchestratorTimeoutError",
    "OrchestratorUnavailableError",
    "PayloadTooLargeError",
    "UnauthorizedError",
    "create_app",
    "get_orchestrator",
    "set_orchestrator",
]


def create_app():
    """创建并配置 FastAPI 应用实例。

    :return: 返回函数执行结果。
    """
    from .app import create_app as _create_app

    return _create_app()
