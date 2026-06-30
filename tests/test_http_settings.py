#########################################################################
# 模块：tests.test_http_settings
# 用途：验证 HTTP L0 基础设施 Pydantic 配置模型。
# 层级：测试层；基于 pytest 的配置单元测试。
# 契约：仅通过 veterinary_agent.http 公开入口导入被测对象。
#########################################################################

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import pytest

from veterinary_agent.http import (
    HttpSettings,
    load_http_middleware_settings_from_env,
    load_http_settings,
)

_HTTP_ENV_KEYS: Final[tuple[str, ...]] = (
    "AGENT_SERVICE_KEY",
    "HTTP_SERVICE_KEY_HEADER",
    "HTTP_PROTECTED_PATH_PREFIX",
    "HTTP_READY_REQUIRES_SERVICE_KEY",
    "HTTP_TRACE_ID_HEADER",
    "HTTP_TRACE_ID_STATE_KEY",
    "HTTP_TRACE_ID_RESPONSE_HEADER_ENABLED",
    "HTTP_ACCESS_LOGGER_NAME",
    "HTTP_ACCESS_LOG_LEVEL",
    "HTTP_ACCESS_LOG_INCLUDE_CLIENT_HOST",
    "HTTP_ACCESS_LOG_INCLUDE_USER_AGENT",
)


def _prepare_clean_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """清理 HTTP 配置环境变量并切换到无 ``.env`` 的临时目录。

    :param monkeypatch: pytest monkeypatch 夹具。
    :type monkeypatch: pytest.MonkeyPatch
    :param tmp_path: pytest 临时目录夹具。
    :type tmp_path: Path
    :return: 无返回值。
    :rtype: None
    """

    monkeypatch.chdir(tmp_path)
    for key in _HTTP_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_http_settings_defaults_build_middleware_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """校验默认配置可转换为三类中间件配置。

    :param monkeypatch: pytest monkeypatch 夹具。
    :type monkeypatch: pytest.MonkeyPatch
    :param tmp_path: pytest 临时目录夹具。
    :type tmp_path: Path
    :return: 无返回值。
    :rtype: None
    """

    _prepare_clean_environment(monkeypatch, tmp_path)

    settings = HttpSettings()
    middleware_settings = settings.to_middleware_settings()
    service_key_settings = middleware_settings.to_service_key_settings()
    trace_id_settings = middleware_settings.to_trace_id_settings()
    request_log_settings = middleware_settings.to_request_log_settings()

    assert middleware_settings.service_key == ""
    assert service_key_settings.header_name == "X-Service-Key"
    assert service_key_settings.protected_path_prefix == "/v1/"
    assert service_key_settings.ready_requires_service_key is False
    assert trace_id_settings.header_name == "X-Trace-Id"
    assert trace_id_settings.state_key == "trace_id"
    assert trace_id_settings.response_header_enabled is True
    assert request_log_settings.logger_name == "veterinary_agent.http.access"
    assert request_log_settings.level == logging.INFO
    assert request_log_settings.include_client_host is True
    assert request_log_settings.include_user_agent is False


def test_http_settings_loads_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """校验环境变量覆盖、字符串清理、布尔解析和日志级别解析。

    :param monkeypatch: pytest monkeypatch 夹具。
    :type monkeypatch: pytest.MonkeyPatch
    :param tmp_path: pytest 临时目录夹具。
    :type tmp_path: Path
    :return: 无返回值。
    :rtype: None
    """

    _prepare_clean_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_SERVICE_KEY", " expected-secret ")
    monkeypatch.setenv("HTTP_SERVICE_KEY_HEADER", " X-Api-Key ")
    monkeypatch.setenv("HTTP_PROTECTED_PATH_PREFIX", " /internal/ ")
    monkeypatch.setenv("HTTP_READY_REQUIRES_SERVICE_KEY", "true")
    monkeypatch.setenv("HTTP_TRACE_ID_HEADER", " X-Request-Id ")
    monkeypatch.setenv("HTTP_TRACE_ID_STATE_KEY", " request_id ")
    monkeypatch.setenv("HTTP_TRACE_ID_RESPONSE_HEADER_ENABLED", "false")
    monkeypatch.setenv("HTTP_ACCESS_LOGGER_NAME", " tests.access ")
    monkeypatch.setenv("HTTP_ACCESS_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("HTTP_ACCESS_LOG_INCLUDE_CLIENT_HOST", "false")
    monkeypatch.setenv("HTTP_ACCESS_LOG_INCLUDE_USER_AGENT", "true")

    settings = load_http_settings()
    middleware_settings = load_http_middleware_settings_from_env()
    service_key_settings = middleware_settings.to_service_key_settings()
    trace_id_settings = middleware_settings.to_trace_id_settings()
    request_log_settings = middleware_settings.to_request_log_settings()

    assert settings.service_key == "expected-secret"
    assert middleware_settings.service_key == "expected-secret"
    assert service_key_settings.header_name == "X-Api-Key"
    assert service_key_settings.protected_path_prefix == "/internal/"
    assert service_key_settings.ready_requires_service_key is True
    assert trace_id_settings.header_name == "X-Request-Id"
    assert trace_id_settings.state_key == "request_id"
    assert trace_id_settings.response_header_enabled is False
    assert request_log_settings.logger_name == "tests.access"
    assert request_log_settings.level == logging.WARNING
    assert request_log_settings.include_client_host is False
    assert request_log_settings.include_user_agent is True
