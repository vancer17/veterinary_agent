##################################################################################################
# 文件: tests/api_ingress/test_request_mapper.py
# 作用: 验证入口外部请求到应用命令的一次性映射行为。
# 边界: 不接入编排、存储或兽医领域依赖；身份解析使用真实入口解析器。
##################################################################################################

from uuid import UUID

from fastapi import Request
from starlette.types import Scope

from veterinary_agent.api_ingress import (
    AgentTurnRequestCommandDto,
    AgentTurnRequestDto,
    ApiRouteKind,
    AttachmentRefDto,
    RequestIdentityContext,
    ResponseMode,
    TurnOptionsDto,
    map_agent_turn_request,
    resolve_request_identity,
)
from veterinary_agent.config import ApiIngressSettings


def _build_request(headers: dict[str, str] | None = None) -> Request:
    """构建用于入口映射测试的最小 HTTP 请求。

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
    """构建可供入口映射处理的外部请求 DTO。

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


def _identity_context(
    request: Request,
    turn_request: AgentTurnRequestDto,
    settings: ApiIngressSettings,
) -> RequestIdentityContext:
    """解析测试请求的入口身份上下文。

    :param request: 当前测试请求。
    :param turn_request: 外部一轮对话请求。
    :param settings: API 接入配置。
    :return: 已解析的入口身份上下文。
    """

    result = resolve_request_identity(
        request=request,
        turn_request=turn_request,
        settings=settings,
    )
    assert result.failure is None
    assert result.identity_context is not None
    return result.identity_context


def _map(
    turn_request: AgentTurnRequestDto,
    *,
    settings: ApiIngressSettings | None = None,
    route_kind: ApiRouteKind = ApiRouteKind.AGENT_TURNS,
    headers: dict[str, str] | None = None,
) -> AgentTurnRequestCommandDto:
    """使用真实身份解析和请求映射构建应用命令。

    :param turn_request: 外部请求 DTO。
    :param settings: 可选 API 接入配置。
    :param route_kind: 当前入口路由类型。
    :param headers: 可选请求头。
    :return: 应用层单轮执行命令。
    """

    resolved_settings = settings or ApiIngressSettings()
    request = _build_request(headers)
    identity = _identity_context(request, turn_request, resolved_settings)
    return map_agent_turn_request(
        turn_request=turn_request,
        settings=resolved_settings,
        route_kind=route_kind,
        identity_context=identity,
    )


def test_mapper_preserves_resolved_header_identity() -> None:
    """验证映射使用入口身份解析阶段得到的请求和链路 ID。

    :return: None。
    """

    command = _map(
        _valid_turn_request(),
        headers={"X-Request-ID": "req_header", "X-Trace-ID": "trace_header"},
    )

    assert command.request_context.request_id == "req_header"
    assert command.request_context.trace_id == "trace_header"


def test_mapper_generates_uuid7_identity_when_headers_are_missing() -> None:
    """验证缺失入口身份时生成的 ID 被写入应用命令。

    :return: None。
    """

    settings = ApiIngressSettings()
    command = _map(_valid_turn_request(), settings=settings)
    request_id = command.request_context.request_id.removeprefix(
        settings.request_identity.request_id_prefix
    )
    trace_id = command.request_context.trace_id.removeprefix(
        settings.request_identity.trace_id_prefix
    )

    assert UUID(request_id).version == 7
    assert UUID(trace_id).version == 7


def test_mapper_prefers_top_level_stream_flag() -> None:
    """验证顶层 stream 字段优先于 turn_options 响应模式。

    :return: None。
    """

    command = _map(
        _valid_turn_request(
            stream=False,
            turn_options=TurnOptionsDto(response_mode=ResponseMode.STREAM),
        )
    )

    assert command.request_context.response_mode == ResponseMode.SYNC.value


def test_mapper_uses_option_response_mode_and_route_kind() -> None:
    """验证未提供 stream 时使用选项模式并保留路由类型。

    :return: None。
    """

    command = _map(
        _valid_turn_request(
            turn_options=TurnOptionsDto(response_mode=ResponseMode.STREAM)
        ),
        route_kind=ApiRouteKind.OPENAI_RESPONSES,
    )

    assert command.request_context.response_mode == ResponseMode.STREAM.value
    assert command.request_context.route_kind == ApiRouteKind.OPENAI_RESPONSES.value


def test_mapper_builds_application_options_and_diagnostics() -> None:
    """验证映射构建执行选项、发布能力和诊断摘要。

    :return: None。
    """

    settings = ApiIngressSettings()
    command = _map(_valid_turn_request(), settings=settings)

    assert command.execution_options.max_event_bytes == settings.sse.max_event_bytes
    assert command.diagnostics.config_version == settings.config_version
    assert command.diagnostics.input_count == 1
    assert command.publish_capabilities.supports_segments is True
    assert command.publish_capabilities.supports_sse_events is False


def test_mapper_preserves_payload_and_uses_idempotency_key() -> None:
    """验证映射保留输入、附件、身份和显式幂等键。

    :return: None。
    """

    attachment = AttachmentRefDto(
        attachment_id="attachment_001",
        mime_type="image/png",
        purpose="symptom_photo",
        storage_ref="s3://bucket/object.png",
    )
    command = _map(
        _valid_turn_request(
            input=None,
            attachments=[attachment],
            metadata=None,
            model="vet-agent-default",
            turn_options=TurnOptionsDto(idempotency_key="idem_001"),
        )
    )

    assert command.idempotency_key == "idem_001"
    assert command.input == []
    assert command.attachments[0].attachment_id == "attachment_001"
    assert command.trusted_identity.pet_id == "pet_001"
    assert command.model_hint == "vet-agent-default"
