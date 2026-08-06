"""
文件：src/ingress/app.py
作用：提供外部 API 入口、请求 DTO、错误处理与编排器适配。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from .errors import (
    ApiIngressError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .routes import router
from vet_agent.api import admin_router, memory_router, report_router


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    :return: 返回函数执行结果。
    """
    app = FastAPI(title="Agent API Ingress", version="0.1.0")
    app.include_router(router)
    app.include_router(memory_router)
    app.include_router(report_router)
    app.include_router(admin_router)
    app.add_exception_handler(ApiIngressError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    return app


app = create_app()
