##################################################################################################
# 文件: tests/api_ingress/test_error_response.py
# 作用: 验证 API 接入组件错误响应策略会消费 error_response.* 配置并统一裁剪、脱敏响应。
# 边界: 仅测试 ApiIngress 内部错误响应构造器；不接入编排层、可观测性或业务安全审查。
##################################################################################################

from veterinary_agent.api_ingress import (
    DEPENDENCY_ERROR_SOURCE,
    INTERNAL_ERROR_SOURCE,
    ErrorDetailDto,
    IngressErrorCode,
    build_api_ingress_error_response,
)
from veterinary_agent.config import ApiIngressSettings


def _settings_with_error_response(
    **error_response_updates: object,
) -> ApiIngressSettings:
    """构建带有 error_response 配置覆盖项的 API 接入组件配置。

    :param error_response_updates: error_response 配置字段覆盖值。
    :return: 已合并 error_response 覆盖项的 API 接入组件配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "error_response": base_settings.error_response.model_copy(
                update=error_response_updates
            ),
        }
    )


def _sample_details() -> list[ErrorDetailDto]:
    """构建测试用错误明细列表。

    :return: 测试用错误明细列表。
    """

    return [
        ErrorDetailDto(field="field_a", reason="reason_a"),
        ErrorDetailDto(field="field_b", reason="reason_b"),
        ErrorDetailDto(field="field_c", reason="reason_c"),
    ]


def test_error_response_can_hide_all_details() -> None:
    """验证 include_details=false 时错误响应不暴露 details。

    :return: 无返回值。
    """

    settings = _settings_with_error_response(include_details=False)

    response = build_api_ingress_error_response(
        settings=settings,
        code=IngressErrorCode.INVALID_REQUEST,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="invalid request",
        details=_sample_details(),
    )

    assert response.details is None


def test_error_response_truncates_details_by_max_details() -> None:
    """验证 max_details 会裁剪对外错误明细数量。

    :return: 无返回值。
    """

    settings = _settings_with_error_response(max_details=2)

    response = build_api_ingress_error_response(
        settings=settings,
        code=IngressErrorCode.INVALID_REQUEST,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="invalid request",
        details=_sample_details(),
    )

    assert response.details is not None
    assert [detail.reason for detail in response.details] == ["reason_a", "reason_b"]


def test_error_response_uses_diagnostic_message_only_when_enabled() -> None:
    """验证 detailed_message_enabled 控制诊断消息是否对外展示。

    :return: 无返回值。
    """

    default_settings = _settings_with_error_response(detailed_message_enabled=False)
    detailed_settings = _settings_with_error_response(detailed_message_enabled=True)

    default_response = build_api_ingress_error_response(
        settings=default_settings,
        code=IngressErrorCode.INTERNAL_ERROR,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="request failed",
        diagnostic_message="internal server error",
        source=INTERNAL_ERROR_SOURCE,
    )
    detailed_response = build_api_ingress_error_response(
        settings=detailed_settings,
        code=IngressErrorCode.INTERNAL_ERROR,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="request failed",
        diagnostic_message="internal server error",
        source=INTERNAL_ERROR_SOURCE,
    )

    assert default_response.message == "request failed"
    assert detailed_response.message == "internal server error"


def test_error_response_hides_dependency_details_by_default() -> None:
    """验证默认隐藏内部依赖错误明细。

    :return: 无返回值。
    """

    response = build_api_ingress_error_response(
        settings=ApiIngressSettings(),
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="service unavailable",
        details=[ErrorDetailDto(field="orchestrator", reason="todo_placeholder")],
        source=DEPENDENCY_ERROR_SOURCE,
    )

    assert response.details is not None
    assert response.details[0].field is None
    assert response.details[0].reason == "internal_dependency_details_hidden"


def test_error_response_can_reveal_dependency_details_by_settings() -> None:
    """验证关闭内部依赖明细隐藏后可返回原始依赖错误明细。

    :return: 无返回值。
    """

    settings = _settings_with_error_response(hide_internal_dependency_details=False)

    response = build_api_ingress_error_response(
        settings=settings,
        code=IngressErrorCode.SERVICE_UNAVAILABLE,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        public_message="service unavailable",
        details=[ErrorDetailDto(field="orchestrator", reason="todo_placeholder")],
        source=DEPENDENCY_ERROR_SOURCE,
    )

    assert response.details is not None
    assert response.details[0].field == "orchestrator"
    assert response.details[0].reason == "todo_placeholder"


def test_error_response_uses_default_message_when_public_message_is_missing() -> None:
    """验证 public_message 缺失时使用 default_message 兜底。

    :return: 无返回值。
    """

    settings = _settings_with_error_response(default_message="fallback request failed")

    response = build_api_ingress_error_response(
        settings=settings,
        code=IngressErrorCode.INTERNAL_ERROR,
        request_id="req_error_policy",
        trace_id="trace_error_policy",
        details=[ErrorDetailDto(reason="RuntimeError")],
        source=INTERNAL_ERROR_SOURCE,
    )

    assert response.message == "fallback request failed"
