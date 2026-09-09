"""
可脚本化的模型传输测试替身。

该替身实现 ``core.llm`` 传输端口，用于测试网关的重试、预算、审计和严格
解析行为；它不发起真实网络请求。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vet_agent.core import errors as core_errors
from vet_agent.core.llm.contracts import (
    ChatCompletionRequest,
    LLMUsage,
    NormalizedChatCompletion,
)


@dataclass(slots=True)
class ScriptedChatCompletionTransport:
    """按队列返回脚本结果的真实传输端口测试替身。

    :ivar scripted_results: 待消费的归一响应或异常队列。
    :ivar requests: 已收到的物理请求快照。
    """

    scripted_results: list[NormalizedChatCompletion | BaseException] = field(
        default_factory=list
    )
    requests: list[ChatCompletionRequest] = field(default_factory=list)

    def enqueue_completion(
        self,
        *,
        content: str,
        model: str = "fake-model",
        response_id: str | None = None,
        prompt_tokens: int = 10,
        completion_tokens: int = 5,
    ) -> None:
        """加入一个成功完成响应。

        :param content: 模型原始文本输出。
        :param model: 测试模型名。
        :param response_id: 测试响应标识。
        :param prompt_tokens: 测试输入 token 数。
        :param completion_tokens: 测试输出 token 数。
        :return: 无返回值。
        """
        self.scripted_results.append(
            NormalizedChatCompletion(
                content=content,
                response_id=response_id,
                model=model,
                finish_reason="stop",
                usage=LLMUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
            )
        )

    def enqueue_failure(self, failure: BaseException) -> None:
        """加入一个物理请求异常。

        :param failure: 下一次调用应抛出的异常对象。
        :return: 无返回值。
        """
        self.scripted_results.append(failure)

    async def complete(
        self, request: ChatCompletionRequest
    ) -> NormalizedChatCompletion:
        """消费一个脚本结果并记录请求。

        :param request: 网关构造的物理请求。
        :return: 队列中的归一完成响应。
        :raises BaseException: 当队首脚本结果是异常时按原类型抛出。
        :raises vet_agent.core.errors.LLMProtocolError: 当测试脚本队列耗尽。
        """
        self.requests.append(request)
        if not self.scripted_results:
            raise core_errors.LLMProtocolError(
                error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
                message="测试传输脚本队列耗尽",
                context={"request_id": request.request_id},
            )
        result = self.scripted_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


__all__ = ["ScriptedChatCompletionTransport"]
