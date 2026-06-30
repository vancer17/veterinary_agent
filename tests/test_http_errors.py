#########################################################################
# 模块：tests.test_http_errors
# 用途：验证 HTTP API 错误响应工厂的公开契约。
# 层级：测试层；基于 pytest 的错误响应单元测试。
# 契约：仅通过 veterinary_agent.http 公开入口导入被测对象。
#########################################################################

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from veterinary_agent.http import (
    ErrorCode,
    ErrorResponseFactory,
    PetNotAuthorizedError,
    ServiceKeyError,
    build_error_response,
)

_HTTP_422_UNPROCESSABLE_ENTITY: Final[int] = 422
_HTTP_403_FORBIDDEN: Final[int] = 403
_HTTP_500_INTERNAL_SERVER_ERROR: Final[int] = 500


def test_error_response_factory_builds_contract_shape() -> None:
    """校验错误响应工厂输出公开 API 契约形状。

    :return: 无返回值。
    :rtype: None
    """

    response = build_error_response(ErrorCode.UNSUPPORTED_FIELD, param="temperature")

    assert response.status_code == _HTTP_422_UNPROCESSABLE_ENTITY
    assert response.body == {
        "error": {
            "message": "请求字段 'temperature' 不受支持。",
            "type": "invalid_request_error",
            "code": "unsupported_field",
            "param": "temperature",
        }
    }
    assert isinstance(response.headers, MappingProxyType)


def test_error_response_factory_maps_known_exceptions() -> None:
    """校验已知业务异常会映射到稳定错误码。

    :return: 无返回值。
    :rtype: None
    """

    factory = ErrorResponseFactory()

    service_key_response = factory.from_exception(ServiceKeyError())
    pet_auth_response = factory.from_exception(PetNotAuthorizedError())

    assert service_key_response.status_code == _HTTP_403_FORBIDDEN
    assert service_key_response.body["error"]["code"] == "invalid_service_key"
    assert service_key_response.body["error"]["type"] == "authentication_error"
    assert pet_auth_response.status_code == _HTTP_403_FORBIDDEN
    assert pet_auth_response.body["error"]["code"] == "pet_not_authorized"
    assert pet_auth_response.body["error"]["type"] == "permission_error"


def test_error_response_factory_hides_unknown_exceptions() -> None:
    """校验未知异常统一映射为内部错误。

    :return: 无返回值。
    :rtype: None
    """

    response = ErrorResponseFactory().from_exception(RuntimeError("hidden"))

    assert response.status_code == _HTTP_500_INTERNAL_SERVER_ERROR
    assert response.body["error"]["code"] == "internal_error"
    assert response.body["error"]["message"] == "服务器内部错误。"
