##################################################################################################
# 文件: src/veterinary_agent/agent_runner/messages.py
# 作用: 基于 LangChain 消息模型实现 AgentRunner 消息编排、格式修复消息构造与 LlmGateway DTO 适配。
# 边界: 不调用模型、不读取历史会话、不执行工具；仅负责协议内消息结构转换和局部运行消息拼装。
##################################################################################################

import json
from collections.abc import Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.prompts import ChatPromptTemplate

from veterinary_agent.agent_runner.dto import AgentValidationErrorDto
from veterinary_agent.llm_gateway import LlmMessageDto, LlmMessageRole

_FORMAT_REPAIR_MAX_CHARS = 4000


def _content_to_text(content: object) -> str:
    """将 LangChain 消息内容转换为 LlmGateway 可承载的文本。

    :param content: LangChain 消息中的原始内容。
    :return: 文本内容；结构化内容会被稳定序列化为 JSON 文本。
    """

    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)


def _truncate_for_repair(
    value: str, *, max_chars: int = _FORMAT_REPAIR_MAX_CHARS
) -> str:
    """裁剪写入格式修复 prompt 的模型原始输出。

    :param value: 模型原始输出文本。
    :param max_chars: 最大保留字符数。
    :return: 未超限时返回原文；超限时返回带裁剪标记的文本。
    """

    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def _build_format_repair_instruction(
    *,
    raw_output: str | None,
    validation_errors: Sequence[AgentValidationErrorDto],
) -> str:
    """构建结构化输出格式修复指令。

    :param raw_output: 上一次模型输出文本。
    :param validation_errors: 上一次解析或 schema 校验错误列表。
    :return: 可追加到消息末尾的修复指令文本。
    """

    error_payload = [
        error.model_dump(mode="json") for error in validation_errors[:20]
    ] or [{"path": "$", "message": "输出无法解析为目标 JSON", "error_type": "parse"}]
    return (
        "请只修复上一条输出的格式，使其满足目标 JSON / JSON Schema。"
        "不要改变业务判断、不要新增事实、不要解释修复过程，只返回最终 JSON。\n\n"
        "上一条输出：\n"
        f"{_truncate_for_repair(raw_output or '')}\n\n"
        "校验错误：\n"
        f"{json.dumps(error_payload, ensure_ascii=False, sort_keys=True)}"
    )


class LangChainMessageComposer:
    """基于 LangChain 的 AgentRunner 消息编排器。"""

    def compose_base_messages(self, *, system_prompt: str) -> list[BaseMessage]:
        """构建一次 Agent 调用的基础 LangChain 消息。

        :param system_prompt: 已由模板层渲染完成的系统 prompt 文本。
        :return: LangChain 标准消息列表。
        """

        prompt = ChatPromptTemplate.from_messages([("system", "{system_prompt}")])
        return list(prompt.format_messages(system_prompt=system_prompt))

    def compose_base_llm_messages(self, *, system_prompt: str) -> list[LlmMessageDto]:
        """构建一次 Agent 调用的基础 LlmGateway 消息。

        :param system_prompt: 已由模板层渲染完成的系统 prompt 文本。
        :return: LlmGateway 标准消息列表。
        """

        return self.langchain_messages_to_llm(
            self.compose_base_messages(system_prompt=system_prompt)
        )

    def compose_repair_llm_messages(
        self,
        *,
        base_messages: Sequence[LlmMessageDto],
        raw_output: str | None,
        validation_errors: Sequence[AgentValidationErrorDto],
    ) -> list[LlmMessageDto]:
        """构建结构化输出修复重试消息。

        :param base_messages: 首次模型调用使用的基础消息。
        :param raw_output: 上一次模型输出文本。
        :param validation_errors: 上一次解析或 schema 校验错误列表。
        :return: 添加格式修复指令后的 LlmGateway 消息列表。
        """

        langchain_messages = self.llm_messages_to_langchain(base_messages)
        repair_instruction = _build_format_repair_instruction(
            raw_output=raw_output,
            validation_errors=validation_errors,
        )
        repaired_messages = [
            *langchain_messages,
            HumanMessage(content=repair_instruction),
        ]
        return self.langchain_messages_to_llm(repaired_messages)

    def llm_messages_to_langchain(
        self,
        messages: Sequence[LlmMessageDto],
    ) -> list[BaseMessage]:
        """将 LlmGateway 消息转换为 LangChain 标准消息。

        :param messages: LlmGateway 消息列表。
        :return: LangChain 标准消息列表。
        """

        converted: list[BaseMessage] = []
        for message in messages:
            content = _content_to_text(message.content)
            if message.role is LlmMessageRole.SYSTEM:
                converted.append(SystemMessage(content=content))
            elif message.role is LlmMessageRole.DEVELOPER:
                converted.append(
                    ChatMessage(role=LlmMessageRole.DEVELOPER.value, content=content)
                )
            elif message.role is LlmMessageRole.USER:
                converted.append(HumanMessage(content=content))
            elif message.role is LlmMessageRole.ASSISTANT:
                converted.append(AIMessage(content=content))
            elif message.role is LlmMessageRole.TOOL:
                converted.append(
                    ToolMessage(
                        content=content,
                        tool_call_id=message.tool_call_id or "unknown_tool_call",
                    )
                )
        return converted

    def langchain_messages_to_llm(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[LlmMessageDto]:
        """将 LangChain 标准消息转换为 LlmGateway 消息。

        :param messages: LangChain 标准消息列表。
        :return: LlmGateway 消息列表。
        """

        converted: list[LlmMessageDto] = []
        for message in messages:
            role = self._resolve_llm_role(message)
            tool_call_id = (
                message.tool_call_id if isinstance(message, ToolMessage) else None
            )
            converted.append(
                LlmMessageDto(
                    role=role,
                    content=_content_to_text(message.content),
                    tool_call_id=tool_call_id,
                )
            )
        return converted

    def _resolve_llm_role(self, message: BaseMessage) -> LlmMessageRole:
        """解析 LangChain 消息对应的 LlmGateway 角色。

        :param message: LangChain 标准消息。
        :return: LlmGateway 消息角色。
        """

        if isinstance(message, SystemMessage):
            return LlmMessageRole.SYSTEM
        if isinstance(message, HumanMessage):
            return LlmMessageRole.USER
        if isinstance(message, AIMessage):
            return LlmMessageRole.ASSISTANT
        if isinstance(message, ToolMessage):
            return LlmMessageRole.TOOL
        if (
            isinstance(message, ChatMessage)
            and message.role == LlmMessageRole.DEVELOPER.value
        ):
            return LlmMessageRole.DEVELOPER
        return LlmMessageRole.USER


__all__: tuple[str, ...] = ("LangChainMessageComposer",)
