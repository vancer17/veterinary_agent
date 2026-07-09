##################################################################################################
# 文件: tests/api_ingress/test_normalizer.py
# 作用: 验证 API 接入组件 Ingress Normalizer 的纯归一化行为。
# 边界: 仅测试外部请求 DTO 到内部请求 DTO 的转换，不接入 Builder、编排层、存储或兽医业务组件。
##################################################################################################

from uuid import UUID

from fastapi import Request
from starlette.types import Scope

from veterinary_agent import (
    AgentTurnRequestDto,
    ApiIngressSettings,
    ApiRouteKind,
    AttachmentRefDto,
    RequestIdentityContext,
    ResponseMode,
    TurnOptionsDto,
    normalize_agent_turn_request,
    resolve_request_identity,
)


def _build_request(headers: dict[str, str] | None = None) -> Request:
    """构建用于 normalizer 测试的最小 HTTP 请求对象。

    :param headers: 可选 HTTP 请求头字典。
    :return: FastAPI 请求对象。
    """

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/agent/turns",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    return Request(scope)


def _valid_turn_request(**updates: object) -> AgentTurnRequestDto:
    """构建可供 normalizer 处理的外部请求 DTO。

    :param updates: 需要覆盖到基础请求体的字段。
    :return: 外部一轮 Agent 对话请求 DTO。
    """

    payload: dict[str, object] = {
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "需要观察哪些症状？"}],
            }
        ],
        "vet_context": {
            "user_id": "user_001",
            "session_id": "session_001",
            "pet_id": "pet_001",
            "pet_info": {"species": "dog"},
        },
    }
    payload.update(updates)
    return AgentTurnRequestDto.model_validate(payload)


def _uuid_without_prefix(value: str, prefix: str) -> UUID:
    """将带前缀的业务 ID 转换为 UUID 对象。

    :param value: 带业务前缀的 ID。
    :param prefix: 业务 ID 前缀。
    :return: 去除前缀后的 UUID 对象。
    """

    assert value.startswith(prefix)
    return UUID(value.removeprefix(prefix))


def _resolve_identity_context(
    request: Request,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> RequestIdentityContext:
    """解析测试请求的入口身份上下文。

    :param request: 当前 HTTP 请求对象。
    :param turn_request: 外部一轮 Agent 对话请求 DTO。
    :param settings: API 接入组件配置。
    :return: 已解析的入口请求身份上下文。
    """

    identity_resolution = resolve_request_identity(
        request=request,
        turn_request=turn_request,
        settings=settings,
    )
    assert identity_resolution.failure is None
    identity_context = identity_resolution.identity_context
    assert identity_context is not None
    return identity_context


def test_normalizer_uses_resolved_header_identity_values() -> None:
    """验证 normalizer 使用已解析的请求头 request_id 与 trace_id。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    request = _build_request(
        {
            "X-Request-ID": "req_header",
            "X-Trace-ID": "trace_header",
        }
    )
    turn_request = _valid_turn_request()
    identity_context = _resolve_identity_context(request, turn_request, settings)

    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=ApiRouteKind.AGENT_TURNS,
        identity_context=identity_context,
    )

    assert normalized_request.request_context.request_id == "req_header"
    assert normalized_request.request_context.trace_id == "trace_header"


def test_normalizer_generates_uuid7_identity_values_when_missing() -> None:
    """验证 normalizer 使用身份解析阶段生成的 UUIDv7。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    request = _build_request()
    turn_request = _valid_turn_request()
    identity_context = _resolve_identity_context(request, turn_request, settings)

    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=ApiRouteKind.AGENT_TURNS,
        identity_context=identity_context,
    )

    request_uuid = _uuid_without_prefix(
        normalized_request.request_context.request_id,
        settings.request_identity.request_id_prefix,
    )
    trace_uuid = _uuid_without_prefix(
        normalized_request.request_context.trace_id,
        settings.request_identity.trace_id_prefix,
    )

    assert request_uuid.version == 7
    assert trace_uuid.version == 7


def test_normalizer_resolves_response_mode_from_stream_first() -> None:
    """验证显式 stream 字段优先于 turn_options.response_mode。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    request = _build_request()
    turn_request = _valid_turn_request(
        stream=False,
        turn_options=TurnOptionsDto(response_mode=ResponseMode.STREAM),
    )
    identity_context = _resolve_identity_context(request, turn_request, settings)

    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=ApiRouteKind.AGENT_TURNS,
        identity_context=identity_context,
    )

    assert normalized_request.request_context.response_mode is ResponseMode.SYNC


def test_normalizer_uses_turn_option_response_mode_when_stream_missing() -> None:
    """验证 stream 缺失时可使用 turn_options.response_mode 提示。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    request = _build_request()
    turn_request = _valid_turn_request(
        turn_options=TurnOptionsDto(response_mode=ResponseMode.STREAM),
    )
    identity_context = _resolve_identity_context(request, turn_request, settings)

    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=ApiRouteKind.OPENAI_RESPONSES,
        identity_context=identity_context,
    )

    assert normalized_request.request_context.response_mode is ResponseMode.STREAM
    assert (
        normalized_request.request_context.route_kind is ApiRouteKind.OPENAI_RESPONSES
    )


def test_normalizer_standardizes_optional_collections_and_identity() -> None:
    """验证 normalizer 标准化可选集合并映射可信身份上下文。

    :return: 无返回值。
    """

    attachment = AttachmentRefDto(
        attachment_id="attachment_001",
        mime_type="image/png",
        purpose="symptom_photo",
        storage_ref="s3://bucket/object.png",
    )
    turn_request = _valid_turn_request(
        input=None,
        attachments=[attachment],
        metadata=None,
    )

    settings = ApiIngressSettings()
    request = _build_request()
    identity_context = _resolve_identity_context(request, turn_request, settings)

    normalized_request = normalize_agent_turn_request(
        request=request,
        turn_request=turn_request,
        settings=settings,
        route_kind=ApiRouteKind.AGENT_TURNS,
        identity_context=identity_context,
    )

    assert normalized_request.input == []
    assert normalized_request.attachments == [attachment]
    assert normalized_request.metadata == {}
    assert normalized_request.trusted_identity.user_id == "user_001"
    assert normalized_request.trusted_identity.session_id == "session_001"
    assert normalized_request.trusted_identity.pet_id == "pet_001"
    assert normalized_request.trusted_identity.pet_info == {"species": "dog"}
