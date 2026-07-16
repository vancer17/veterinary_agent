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
from vet_agent.api.admin_routes import router as admin_router
from vet_agent.api.memory_routes import router as memory_router
from vet_agent.api.report_routes import router as report_router


def create_app() -> FastAPI:
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
