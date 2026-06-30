#
# 模块：veterinary_agent.http.middleware
# 用途：HTTP API 中间件的公开包入口。
# 层级：L0 HTTP 适配层。
# 契约：使用方应从本包导入，避免直接引用私有实现模块。
#

from __future__ import annotations

from ._request_log import (
    RequestLogMiddleware,
    RequestLogMiddlewareSettings,
    RequestLogRecord,
)
from ._service_key import (
    AsgiApp,
    HeaderPair,
    Message,
    PathPredicate,
    Receive,
    Scope,
    Send,
    ServiceKeyMiddleware,
    ServiceKeyMiddlewareSettings,
)
from ._trace_id import (
    TraceIdFactory,
    TraceIdMiddleware,
    TraceIdMiddlewareSettings,
    get_current_trace_id,
)

__all__: tuple[str, ...] = (
    "AsgiApp",
    "HeaderPair",
    "Message",
    "PathPredicate",
    "Receive",
    "RequestLogMiddleware",
    "RequestLogMiddlewareSettings",
    "RequestLogRecord",
    "Scope",
    "Send",
    "ServiceKeyMiddleware",
    "ServiceKeyMiddlewareSettings",
    "TraceIdFactory",
    "TraceIdMiddleware",
    "TraceIdMiddlewareSettings",
    "get_current_trace_id",
)
