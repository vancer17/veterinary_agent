##################################################################################################
# 文件: src/veterinary_agent/api_ingress/response_mapper.py
# 作用: 将 AgentApplicationService 同步结果映射为 ApiIngress 对外响应 DTO。
# 边界: 仅执行协议层字段映射，不修改业务分段、不生成用户正文、不执行错误处理或流式连接管理。
##################################################################################################

from veterinary_agent.agent_application_service import AgentTurnResultDto
from veterinary_agent.api_ingress.dto import (
    AgentTurnResponseDto,
    OutputItemDto,
    OutputTextContentDto,
    SegmentDto,
)


def map_agent_turn_result(
    result: AgentTurnResultDto,
) -> AgentTurnResponseDto:
    """将应用服务同步结果映射为对外 Agent turn 响应。

    :param result: AgentApplicationService 返回的同步结果。
    :return: ApiIngress 可直接序列化的对外响应 DTO。
    """

    output = (
        [
            OutputItemDto(
                content=[OutputTextContentDto(text=result.output_text)],
            )
        ]
        if result.output_text
        else []
    )
    segments = [
        SegmentDto.model_validate(segment.model_dump(mode="json"))
        for segment in result.segments
    ]
    return AgentTurnResponseDto.model_validate(
        {
            "id": result.turn_id,
            "created_at": result.created_at,
            "request_id": result.request_id,
            "trace_id": result.trace_id,
            "status": result.status.value,
            "output": [item.model_dump(mode="json") for item in output],
            "segments": [segment.model_dump(mode="json") for segment in segments],
            "reasoning_display": (
                result.reasoning_display.model_dump(mode="json")
                if result.reasoning_display is not None
                else None
            ),
            "vet_result": (
                result.vet_result.model_dump(mode="json")
                if result.vet_result is not None
                else None
            ),
            "metadata": {
                **result.metadata,
                "run_id": result.run_id,
                "user_message_id": result.user_message_id,
                "trace_delivery_status": result.trace_delivery_status.value,
            },
        }
    )


__all__: tuple[str, ...] = ("map_agent_turn_result",)
