# 文件: src/veterinary_agent/config/__init__.py
# 作用: 作为配置包的统一出口，向其他包暴露配置模型与加载函数。
# 边界: 外部包应从本文件导入配置能力，避免跨包直接引用实现模块。

from veterinary_agent.config.api_ingress import (
    ApiIngressSettings,
    AttachmentLimitConfig,
    ErrorResponseConfig,
    OpenAICompatibilityConfig,
    OrchestratorClientConfig,
    RateLimitConfig,
    ReadinessConfig,
    RequestIdentityConfig,
    RequestLimitConfig,
    ResponseModeConfig,
    SseConfig,
    load_api_ingress_settings,
)

__all__: tuple[str, ...] = (
    "ApiIngressSettings",
    "AttachmentLimitConfig",
    "ErrorResponseConfig",
    "OpenAICompatibilityConfig",
    "OrchestratorClientConfig",
    "RateLimitConfig",
    "ReadinessConfig",
    "RequestIdentityConfig",
    "RequestLimitConfig",
    "ResponseModeConfig",
    "SseConfig",
    "load_api_ingress_settings",
)
