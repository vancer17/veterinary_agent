##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/messages.py
# 作用: 提供 LlmGateway DTO 与 LangChain 消息模型、OpenAI-compatible 消息结构之间的内部适配。
# 边界: 不构造业务 prompt、不裁剪上下文、不执行工具调用，不向公共契约泄漏 LangChain 类型。
##################################################################################################

from collections.abc import Sequence
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    convert_to_openai_messages,
)

from veterinary_agent.llm_gateway.dto import (
    JsonMap,
    LlmContentPartDto,
    LlmImageContentPartDto,
    LlmMessageDto,
    LlmTextContentPartDto,
)
from veterinary_agent.llm_gateway.enums import LlmMessageRole


def _content_part_to_langchain(part: LlmContentPartDto) -> str | dict[str, Any]:
    """将单个 LlmGateway 内容分片转换为 LangChain 可接受的内容块。

    :param part: LlmGateway 协议无关内容分片。
    :return: LangChain 消息内容块。
    """

    if isinstance(part, LlmTextContentPartDto):
        return {"type": "text", "text": part.text}
    if isinstance(part, LlmImageContentPartDto):
        return {
            "type": "image_url",
            "image_url": part.image_url.model_dump(mode="json", exclude_none=True),
        }
    return cast(dict[str, Any], part)


def _message_content_to_langchain(
    content: str | list[LlmContentPartDto] | None,
) -> str | list[str | dict[str, Any]]:
    """将 LlmGateway 消息正文转换为 LangChain 消息正文。

    :param content: LlmGateway 消息正文。
    :return: LangChain 消息正文；空值转换为空字符串。
    """

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return [_content_part_to_langchain(part) for part in content]


class LangChainLlmMessageAdapter:
    """LlmGateway 内部 LangChain 消息适配器。"""

    def to_langchain_messages(
        self,
        messages: Sequence[LlmMessageDto],
    ) -> list[BaseMessage]:
        """将 LlmGateway 消息列表转换为 LangChain 标准消息列表。

        :param messages: LlmGateway 协议无关消息列表。
        :return: LangChain 标准消息列表。
        """

        return [self.to_langchain_message(message) for message in messages]

    def to_langchain_message(self, message: LlmMessageDto) -> BaseMessage:
        """将单条 LlmGateway 消息转换为 LangChain 标准消息。

        :param message: LlmGateway 协议无关消息。
        :return: LangChain 标准消息。
        """

        content = _message_content_to_langchain(message.content)
        if message.role is LlmMessageRole.SYSTEM:
            return SystemMessage(content=content, name=message.name)
        if message.role is LlmMessageRole.USER:
            return HumanMessage(content=content, name=message.name)
        if message.role is LlmMessageRole.ASSISTANT:
            return AIMessage(
                content=content,
                name=message.name,
                additional_kwargs=self._assistant_additional_kwargs(message),
            )
        if message.role is LlmMessageRole.TOOL:
            return ToolMessage(
                content=content,
                name=message.name,
                tool_call_id=cast(str, message.tool_call_id),
            )
        return HumanMessage(content=content, name=message.name)

    def to_openai_messages(
        self,
        messages: Sequence[LlmMessageDto],
    ) -> list[JsonMap]:
        """将 LlmGateway 消息转换为 OpenAI-compatible 请求消息结构。

        :param messages: LlmGateway 协议无关消息列表。
        :return: OpenAI-compatible ``messages`` 列表。
        """

        rendered = convert_to_openai_messages(self.to_langchain_messages(messages))
        if isinstance(rendered, dict):
            return [cast(JsonMap, rendered)]
        return [cast(JsonMap, dict(message)) for message in rendered]

    def _assistant_additional_kwargs(self, message: LlmMessageDto) -> dict[str, object]:
        """构造 LangChain AIMessage 所需的附加字段。

        :param message: LlmGateway assistant 消息。
        :return: 供 LangChain 渲染 OpenAI 工具调用的附加字段。
        """

        if not message.tool_calls:
            return {}
        return {
            "tool_calls": [
                tool_call.model_dump(mode="json", by_alias=True)
                for tool_call in message.tool_calls
            ]
        }


__all__: tuple[str, ...] = ("LangChainLlmMessageAdapter",)
