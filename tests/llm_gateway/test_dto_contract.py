##################################################################################################
# 文件: tests/llm_gateway/test_dto_contract.py
# 作用: 验证 LlmGateway 消息、工具、响应格式、usage 与调用请求 DTO 的严格字段不变量。
# 边界: 仅通过 veterinary_agent 顶层公共出口使用 DTO；不创建网关、不访问模型代理或其他领域组件。
##################################################################################################

import pytest
from pydantic import ValidationError

from veterinary_agent import (
    LlmFunctionCallDto,
    LlmImageContentPartDto,
    LlmImageUrlDto,
    LlmInvocationRequestDto,
    LlmJsonSchemaDto,
    LlmMessageDto,
    LlmMessageRole,
    LlmResponseFormatDto,
    LlmResponseFormatType,
    LlmToolCallDto,
    LlmUsageSummaryDto,
)


def test_tool_message_requires_tool_call_id() -> None:
    """验证 tool 角色消息必须绑定工具调用 ID。

    :return: None。
    """

    with pytest.raises(ValidationError, match="tool_call_id"):
        LlmMessageDto(
            role=LlmMessageRole.TOOL,
            content='{"temperature": 39.2}',
        )


def test_only_assistant_message_can_carry_tool_calls() -> None:
    """验证只有 assistant 历史消息可以携带工具调用。

    :return: None。
    """

    tool_call = LlmToolCallDto(
        id="call_1",
        function=LlmFunctionCallDto(
            name="lookup_reference_range",
            arguments='{"species":"cat"}',
        ),
    )

    with pytest.raises(ValidationError, match="assistant"):
        LlmMessageDto(
            role=LlmMessageRole.USER,
            content="查询参考区间",
            tool_calls=[tool_call],
        )


def test_json_schema_response_format_requires_schema() -> None:
    """验证 JSON Schema 响应格式必须携带 schema 定义。

    :return: None。
    """

    with pytest.raises(ValidationError, match="必须提供 json_schema"):
        LlmResponseFormatDto(type=LlmResponseFormatType.JSON_SCHEMA)

    response_format = LlmResponseFormatDto(
        type=LlmResponseFormatType.JSON_SCHEMA,
        json_schema=LlmJsonSchemaDto(
            name="triage_result",
            schema={
                "type": "object",
                "properties": {"urgency": {"type": "string"}},
                "required": ["urgency"],
            },
        ),
    )

    dumped = response_format.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    assert dumped["json_schema"]["schema"]["type"] == "object"


def test_usage_summary_normalizes_and_validates_total_tokens() -> None:
    """验证 usage 会补齐总 token，并拒绝小于输入输出之和的总量。

    :return: None。
    """

    usage = LlmUsageSummaryDto(
        input_tokens=7,
        output_tokens=3,
    )

    assert usage.total_tokens == 10
    with pytest.raises(ValidationError, match="total_tokens"):
        LlmUsageSummaryDto(
            input_tokens=7,
            output_tokens=3,
            total_tokens=9,
        )


def test_invocation_request_rejects_reserved_generation_parameters() -> None:
    """验证 generation_params 不得覆盖网关管理的协议字段。

    :return: None。
    """

    with pytest.raises(ValidationError, match="保留字段"):
        LlmInvocationRequestDto(
            trace_id="trace_dto",
            request_id="req_dto",
            caller_component="AgentRunner",
            model_profile_id="profile_primary",
            messages=[
                LlmMessageDto(
                    role=LlmMessageRole.USER,
                    content="猫咪呕吐",
                )
            ],
            generation_params={"model": "forbidden-model"},
        )


def test_invocation_request_rejects_unsafe_identity_value() -> None:
    """验证调用关联字段拒绝控制字符和请求头不安全字符。

    :return: None。
    """

    with pytest.raises(ValidationError, match="调用关联字段"):
        LlmInvocationRequestDto(
            trace_id="trace\nunsafe",
            request_id="req_dto",
            caller_component="AgentRunner",
            model_profile_id="profile_primary",
            messages=[
                LlmMessageDto(
                    role=LlmMessageRole.USER,
                    content="猫咪呕吐",
                )
            ],
        )


def test_multimodal_message_accepts_controlled_image_part() -> None:
    """验证多模态消息可以承载受控图片地址分片。

    :return: None。
    """

    message = LlmMessageDto(
        role=LlmMessageRole.USER,
        content=[
            LlmImageContentPartDto(
                image_url=LlmImageUrlDto(
                    url="data:image/png;base64,AAAA",
                    detail="low",
                )
            )
        ],
    )

    assert isinstance(message.content, list)
    image_part = message.content[0]
    assert isinstance(image_part, LlmImageContentPartDto)
    assert image_part.image_url.detail == "low"
