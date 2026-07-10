##################################################################################################
# 文件: tests/api_ingress/test_builder.py
# 作用: 验证 API 接入组件 AgentTurnRequest Builder 的纯字段映射行为。
# 边界: 仅测试内部归一化请求到编排请求命令的转换，不接入真实编排层、存储或兽医业务组件。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent import (
    AgentTurnInternalRequestDto,
    AgentTurnRequestCommandDto,
    ApiIngressSettings,
    ApiRouteKind,
    AttachmentRefDto,
    InputItemDto,
    RequestContextDto,
    ResponseMode,
    TrustedIdentityDto,
    TurnOptionsDto,
    build_agent_turn_request,
)


def _normalized_request(
    *,
    response_mode: ResponseMode = ResponseMode.SYNC,
    turn_options: TurnOptionsDto | None = None,
) -> AgentTurnInternalRequestDto:
    """构建可供 Builder 消费的内部归一化请求 DTO。

    :param response_mode: 归一化后的入口响应模式。
    :param turn_options: 可选本轮入口选项。
    :return: 内部归一化请求 DTO。
    """

    input_item = InputItemDto.model_validate(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "需要观察哪些症状？"}],
        }
    )
    attachment = AttachmentRefDto(
        attachment_id="attachment_001",
        mime_type="image/png",
        purpose="symptom_photo",
        storage_ref="s3://bucket/object.png",
    )
    return AgentTurnInternalRequestDto(
        request_context=RequestContextDto(
            request_id="req_builder_001",
            trace_id="trace_builder_001",
            response_mode=response_mode,
            received_at=datetime.now(UTC),
            route_kind=ApiRouteKind.AGENT_TURNS,
        ),
        trusted_identity=TrustedIdentityDto(
            user_id="user_001",
            session_id="session_001",
            pet_id="pet_001",
            pet_info={"species": "dog"},
        ),
        input=[input_item],
        attachments=[attachment],
        metadata={"client": "pytest"},
        model="vet-agent-default",
        turn_options=turn_options,
    )


def test_builder_uses_explicit_idempotency_key() -> None:
    """验证 Builder 优先使用 turn_options.idempotency_key。

    :return: 无返回值。
    """

    built_request = build_agent_turn_request(
        normalized_request=_normalized_request(
            turn_options=TurnOptionsDto(idempotency_key="idem_builder_001")
        ),
        settings=ApiIngressSettings(),
    )

    assert built_request.idempotency_key == "idem_builder_001"


def test_builder_falls_back_to_request_id_as_idempotency_key() -> None:
    """验证 Builder 在缺少显式幂等键时回退使用 request_id。

    :return: 无返回值。
    """

    built_request = build_agent_turn_request(
        normalized_request=_normalized_request(),
        settings=ApiIngressSettings(),
    )

    assert built_request.idempotency_key == "req_builder_001"


def test_builder_maps_execution_options_and_diagnostics() -> None:
    """验证 Builder 映射编排执行选项和入口诊断摘要。

    :return: 无返回值。
    """

    settings = ApiIngressSettings()
    built_request = build_agent_turn_request(
        normalized_request=_normalized_request(),
        settings=settings,
    )

    assert isinstance(built_request, AgentTurnRequestCommandDto)
    assert built_request.execution_options.orchestrator_target == (
        settings.orchestrator.target
    )
    assert (
        built_request.execution_options.max_event_bytes == settings.sse.max_event_bytes
    )
    assert built_request.diagnostics.config_version == settings.config_version
    assert built_request.diagnostics.input_count == 1
    assert built_request.diagnostics.attachment_count == 1


def test_builder_marks_sse_publish_capability_for_stream_mode() -> None:
    """验证 Builder 在流式响应模式下标记 SSE 事件发布能力。

    :return: 无返回值。
    """

    built_request = build_agent_turn_request(
        normalized_request=_normalized_request(response_mode=ResponseMode.STREAM),
        settings=ApiIngressSettings(),
    )

    assert built_request.publish_capabilities.supports_segments is True
    assert built_request.publish_capabilities.supports_reasoning_display is True
    assert built_request.publish_capabilities.supports_sse_events is True


def test_builder_preserves_payload_and_identity_context() -> None:
    """验证 Builder 保留归一化后的输入、附件、元信息和身份上下文。

    :return: 无返回值。
    """

    built_request = build_agent_turn_request(
        normalized_request=_normalized_request(),
        settings=ApiIngressSettings(),
    )

    assert built_request.trusted_identity.pet_id == "pet_001"
    assert built_request.trusted_identity.pet_info == {"species": "dog"}
    assert built_request.input[0].role == "user"
    assert built_request.attachments[0].attachment_id == "attachment_001"
    assert built_request.metadata == {"client": "pytest"}
    assert built_request.model_hint == "vet-agent-default"
