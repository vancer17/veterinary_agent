#########################################################################
# 模块：veterinary_agent.http.settings._settings
# 用途：使用 Pydantic 建立 HTTP L0 基础设施配置模型。
# 层级：L0 HTTP 适配层配置。
# 契约：提供中间件配置对象，不在调用点散落读取环境变量。
# 备注：本模块为私有实现。请从 veterinary_agent.http.settings 或
#        veterinary_agent.http 导入公开符号。
#########################################################################

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from veterinary_agent.http.middleware import (
    RequestLogMiddlewareSettings,
    ServiceKeyMiddlewareSettings,
    TraceIdMiddlewareSettings,
)

type LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_DEFAULT_TRACE_ID_HEADER: Final[str] = "X-Trace-Id"
_DEFAULT_TRACE_ID_STATE_KEY: Final[str] = "trace_id"
_DEFAULT_ACCESS_LOGGER_NAME: Final[str] = "veterinary_agent.http.access"
_DEFAULT_PROTECTED_PATH_PREFIX: Final[str] = "/v1/"
_DEFAULT_SERVICE_KEY_HEADER: Final[str] = "X-Service-Key"


class HttpMiddlewareSettings(BaseModel):
    """HTTP 中间件聚合配置。

    :param service_key: 服务间密钥；空字符串表示关闭服务间密钥校验。
    :type service_key: str
    :param service_key_header: 承载服务间密钥的请求头名称。
    :type service_key_header: str
    :param protected_path_prefix: 默认受保护的路径前缀。
    :type protected_path_prefix: str
    :param ready_requires_service_key: ``/ready`` 是否也需要服务间密钥。
    :type ready_requires_service_key: bool
    :param trace_id_header: 承载 trace_id 的请求头名称。
    :type trace_id_header: str
    :param trace_id_state_key: 写入 ASGI ``scope["state"]`` 的键名。
    :type trace_id_state_key: str
    :param trace_id_response_header_enabled: 是否在响应头回写 trace_id。
    :type trace_id_response_header_enabled: bool
    :param access_log_logger_name: 访问日志器名称。
    :type access_log_logger_name: str
    :param access_log_level: 访问日志级别。
    :type access_log_level: int
    :param access_log_include_client_host: 是否记录客户端主机地址。
    :type access_log_include_client_host: bool
    :param access_log_include_user_agent: 是否记录 ``User-Agent``。
    :type access_log_include_user_agent: bool
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    service_key: str = ""
    service_key_header: str = _DEFAULT_SERVICE_KEY_HEADER
    protected_path_prefix: str = _DEFAULT_PROTECTED_PATH_PREFIX
    ready_requires_service_key: bool = False
    trace_id_header: str = _DEFAULT_TRACE_ID_HEADER
    trace_id_state_key: str = _DEFAULT_TRACE_ID_STATE_KEY
    trace_id_response_header_enabled: bool = True
    access_log_logger_name: str = _DEFAULT_ACCESS_LOGGER_NAME
    access_log_level: int = logging.INFO
    access_log_include_client_host: bool = True
    access_log_include_user_agent: bool = False

    @field_validator(
        "service_key",
        "service_key_header",
        "protected_path_prefix",
        "trace_id_header",
        "trace_id_state_key",
        "access_log_logger_name",
        mode="before",
    )
    @classmethod
    def strip_string(cls, value: Any) -> Any:
        """清理字符串配置两侧空白。

        :param value: 原始配置值。
        :type value: Any
        :return: 清理后的配置值。
        :rtype: Any
        """

        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("access_log_level", mode="before")
    @classmethod
    def parse_log_level(cls, value: Any) -> int:
        """解析访问日志级别。

        :param value: 原始日志级别配置。
        :type value: Any
        :return: Python logging 使用的日志级别整数。
        :rtype: int
        :raises ValueError: 当日志级别不合法时抛出。
        """

        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            raise ValueError("访问日志级别必须是字符串或整数。")

        normalized_value = value.strip()
        if normalized_value.isdigit():
            return int(normalized_value)

        level = logging.getLevelName(normalized_value.upper())
        if isinstance(level, int):
            return level
        raise ValueError("访问日志级别必须是合法 logging 级别。")

    def to_service_key_settings(self) -> ServiceKeyMiddlewareSettings:
        """转换为服务间密钥中间件配置。

        :return: 服务间密钥中间件配置。
        :rtype: ServiceKeyMiddlewareSettings
        """

        return ServiceKeyMiddlewareSettings(
            service_key=self.service_key,
            header_name=self.service_key_header,
            protected_path_prefix=self.protected_path_prefix,
            ready_requires_service_key=self.ready_requires_service_key,
        )

    def to_trace_id_settings(self) -> TraceIdMiddlewareSettings:
        """转换为 trace_id 中间件配置。

        :return: trace_id 中间件配置。
        :rtype: TraceIdMiddlewareSettings
        """

        return TraceIdMiddlewareSettings(
            header_name=self.trace_id_header,
            state_key=self.trace_id_state_key,
            response_header_enabled=self.trace_id_response_header_enabled,
        )

    def to_request_log_settings(self) -> RequestLogMiddlewareSettings:
        """转换为访问日志中间件配置。

        :return: 访问日志中间件配置。
        :rtype: RequestLogMiddlewareSettings
        """

        return RequestLogMiddlewareSettings(
            logger_name=self.access_log_logger_name,
            level=self.access_log_level,
            state_key=self.trace_id_state_key,
            include_client_host=self.access_log_include_client_host,
            include_user_agent=self.access_log_include_user_agent,
        )


class HttpSettings(BaseSettings):
    """HTTP L0 基础设施配置。

    :param service_key: 服务间密钥；来自 ``AGENT_SERVICE_KEY``。
    :type service_key: str
    :param service_key_header: 服务间密钥请求头；来自 ``HTTP_SERVICE_KEY_HEADER``。
    :type service_key_header: str
    :param protected_path_prefix: 受保护路径前缀；来自 ``HTTP_PROTECTED_PATH_PREFIX``。
    :type protected_path_prefix: str
    :param ready_requires_service_key: ``/ready`` 是否也需要密钥。
    :type ready_requires_service_key: bool
    :param trace_id_header: trace_id 请求头；来自 ``HTTP_TRACE_ID_HEADER``。
    :type trace_id_header: str
    :param trace_id_state_key: trace_id state 键名；来自 ``HTTP_TRACE_ID_STATE_KEY``。
    :type trace_id_state_key: str
    :param trace_id_response_header_enabled: 是否回写 trace_id 响应头。
    :type trace_id_response_header_enabled: bool
    :param access_log_logger_name: 访问日志器名称。
    :type access_log_logger_name: str
    :param access_log_level: 访问日志级别。
    :type access_log_level: int
    :param access_log_include_client_host: 是否记录客户端主机地址。
    :type access_log_include_client_host: bool
    :param access_log_include_user_agent: 是否记录 ``User-Agent``。
    :type access_log_include_user_agent: bool
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    service_key: str = Field(default="", alias="AGENT_SERVICE_KEY")
    service_key_header: str = Field(
        default=_DEFAULT_SERVICE_KEY_HEADER,
        alias="HTTP_SERVICE_KEY_HEADER",
    )
    protected_path_prefix: str = Field(
        default=_DEFAULT_PROTECTED_PATH_PREFIX,
        alias="HTTP_PROTECTED_PATH_PREFIX",
    )
    ready_requires_service_key: bool = Field(
        default=False,
        alias="HTTP_READY_REQUIRES_SERVICE_KEY",
    )
    trace_id_header: str = Field(
        default=_DEFAULT_TRACE_ID_HEADER,
        alias="HTTP_TRACE_ID_HEADER",
    )
    trace_id_state_key: str = Field(
        default=_DEFAULT_TRACE_ID_STATE_KEY,
        alias="HTTP_TRACE_ID_STATE_KEY",
    )
    trace_id_response_header_enabled: bool = Field(
        default=True,
        alias="HTTP_TRACE_ID_RESPONSE_HEADER_ENABLED",
    )
    access_log_logger_name: str = Field(
        default=_DEFAULT_ACCESS_LOGGER_NAME,
        alias="HTTP_ACCESS_LOGGER_NAME",
    )
    access_log_level: int = Field(
        default=logging.INFO,
        alias="HTTP_ACCESS_LOG_LEVEL",
    )
    access_log_include_client_host: bool = Field(
        default=True,
        alias="HTTP_ACCESS_LOG_INCLUDE_CLIENT_HOST",
    )
    access_log_include_user_agent: bool = Field(
        default=False,
        alias="HTTP_ACCESS_LOG_INCLUDE_USER_AGENT",
    )

    @field_validator(
        "service_key",
        "service_key_header",
        "protected_path_prefix",
        "trace_id_header",
        "trace_id_state_key",
        "access_log_logger_name",
        mode="before",
    )
    @classmethod
    def _strip_string(cls, value: Any) -> Any:
        """清理字符串配置两侧空白。

        :param value: 原始配置值。
        :type value: Any
        :return: 清理后的配置值。
        :rtype: Any
        """

        return HttpMiddlewareSettings.strip_string(value)

    @field_validator("access_log_level", mode="before")
    @classmethod
    def _parse_log_level(cls, value: Any) -> int:
        """解析访问日志级别。

        :param value: 原始日志级别配置。
        :type value: Any
        :return: Python logging 使用的日志级别整数。
        :rtype: int
        """

        return HttpMiddlewareSettings.parse_log_level(value)

    def to_middleware_settings(self) -> HttpMiddlewareSettings:
        """转换为 HTTP 中间件聚合配置。

        :return: HTTP 中间件聚合配置。
        :rtype: HttpMiddlewareSettings
        """

        return HttpMiddlewareSettings(
            service_key=self.service_key,
            service_key_header=self.service_key_header,
            protected_path_prefix=self.protected_path_prefix,
            ready_requires_service_key=self.ready_requires_service_key,
            trace_id_header=self.trace_id_header,
            trace_id_state_key=self.trace_id_state_key,
            trace_id_response_header_enabled=self.trace_id_response_header_enabled,
            access_log_logger_name=self.access_log_logger_name,
            access_log_level=self.access_log_level,
            access_log_include_client_host=self.access_log_include_client_host,
            access_log_include_user_agent=self.access_log_include_user_agent,
        )


def load_http_settings() -> HttpSettings:
    """从环境变量和 ``.env`` 加载 HTTP 配置。

    :return: HTTP L0 基础设施配置。
    :rtype: HttpSettings
    """

    return HttpSettings()


def load_http_middleware_settings_from_env() -> HttpMiddlewareSettings:
    """从环境变量和 ``.env`` 加载 HTTP 中间件聚合配置。

    :return: HTTP 中间件聚合配置。
    :rtype: HttpMiddlewareSettings
    """

    return load_http_settings().to_middleware_settings()


__all__: Final[tuple[str, ...]] = (
    "HttpMiddlewareSettings",
    "HttpSettings",
    "LogLevelName",
    "load_http_middleware_settings_from_env",
    "load_http_settings",
)
