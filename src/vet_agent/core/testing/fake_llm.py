"""
Fake LLM 网关测试替身。

该替身保持与生产网关一致的 prompt / role / schema 绑定检查，并可返回已
构造的 typed 输出、原始 JSON 文本或异常，用于隔离真实模型服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from vet_agent.core.execution import ExecutionContext
from vet_agent.core.llm.contracts import (
    LLMResponse,
    LLMResponseMetadata,
    LLMUsage,
    ModelRole,
)
from vet_agent.core.prompts import RenderedPrompt, validate_prompt_binding
from vet_agent.core.schema import StrictJSONGateway, StrictModel

T = TypeVar("T", bound=StrictModel)


@dataclass(frozen=True, slots=True)
class FakeLLMResult:
    """一次 Fake LLM 调用的脚本结果。

    :ivar output: 已构造的 typed 输出。
    :ivar content: 待严格解析的原始 JSON 文本。
    :ivar failure: 需要抛出的异常。
    :ivar model: 测试模型名。
    :ivar response_id: 测试响应标识。
    :ivar prompt_tokens: 测试输入 token 数。
    :ivar completion_tokens: 测试输出 token 数。
    """

    output: StrictModel | None = None
    content: str | None = None
    failure: BaseException | None = None
    model: str = "fake-model"
    response_id: str | None = None
    prompt_tokens: int = 10
    completion_tokens: int = 5

    def __post_init__(self) -> None:
        """校验 Fake 结果脚本的互斥性。

        :return: 无返回值；脚本非法时显式失败。
        :raises ValueError: 当同时提供多种结果或未提供任何结果。
        """
        provided_kinds = sum(
            value is not None for value in (self.output, self.content, self.failure)
        )
        if provided_kinds != 1:
            raise ValueError(
                "FakeLLMResult 必须且仅提供 output、content 或 failure 之一"
            )

    @staticmethod
    def typed(output: StrictModel) -> FakeLLMResult:
        """构造 typed 输出脚本。

        :param output: 已符合目标 schema 的输出对象。
        :return: Fake LLM 结果。
        """
        return FakeLLMResult(output=output)

    @staticmethod
    def raw_content(content: str) -> FakeLLMResult:
        """构造原始模型文本脚本。

        :param content: 将交给 Strict JSON Gateway 的文本。
        :return: Fake LLM 结果。
        """
        return FakeLLMResult(content=content)

    @staticmethod
    def with_failure(failure: BaseException) -> FakeLLMResult:
        """构造异常脚本。

        :param failure: 调用时需要抛出的异常。
        :return: Fake LLM 结果。
        """
        return FakeLLMResult(failure=failure)


@dataclass(frozen=True, slots=True)
class RecordedLLMCall:
    """Fake LLM 网关收到的调用快照。

    :ivar role: 稳定模型角色。
    :ivar prompt_id: prompt 标识。
    :ivar prompt_version: prompt 版本。
    :ivar output_schema_name: 输出 schema 类名。
    :ivar execution_id: 执行链标识。
    """

    role: ModelRole
    prompt_id: str
    prompt_version: str
    output_schema_name: str
    execution_id: str


@dataclass(slots=True)
class FakeLLMGateway:
    """不访问外部服务的严格 JSON 模型网关替身。

    :ivar results: 待消费脚本结果队列。
    :ivar calls: 已收到调用快照。
    :ivar strict_json_gateway: 用于 raw content 的严格解析器。
    """

    results: list[FakeLLMResult] = field(default_factory=list)
    calls: list[RecordedLLMCall] = field(default_factory=list)
    strict_json_gateway: StrictJSONGateway = field(default_factory=StrictJSONGateway)

    def enqueue(self, result: FakeLLMResult) -> None:
        """加入一个脚本结果。

        :param result: Fake LLM 调用结果。
        :return: 无返回值。
        """
        self.results.append(result)

    async def complete_json(
        self,
        *,
        role: ModelRole,
        prompt: RenderedPrompt,
        output_schema: type[T],
        context: ExecutionContext,
    ) -> LLMResponse[T]:
        """按脚本返回 typed 模型输出。

        :param role: 稳定模型角色。
        :param prompt: 已渲染版本化 prompt。
        :param output_schema: 目标严格 schema。
        :param context: 当前执行上下文。
        :return: Fake typed 输出与 metadata。
        :raises BaseException: 当脚本结果为异常时按原类型抛出。
        :raises vet_agent.core.errors.LLMProtocolError: 当 typed 输出不符合目标 schema。
        """
        from vet_agent.core import errors as core_errors

        validate_prompt_binding(
            model_role=role.value,
            output_schema=output_schema,
            prompt=prompt,
        )
        self.calls.append(
            RecordedLLMCall(
                role=role,
                prompt_id=prompt.record.prompt_id,
                prompt_version=prompt.record.prompt_version,
                output_schema_name=output_schema.__name__,
                execution_id=context.execution_id,
            )
        )
        if not self.results:
            raise core_errors.LLMProtocolError(
                error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
                message="Fake LLM 结果队列耗尽",
            )
        result = self.results.pop(0)
        if result.failure is not None:
            raise result.failure
        if result.output is not None:
            if not isinstance(result.output, output_schema):
                raise core_errors.LLMProtocolError(
                    error_code=core_errors.CoreErrorCode.LLM_PROTOCOL_INVALID,
                    message="Fake typed 输出不符合目标 schema",
                    context={"expected_schema": output_schema.__name__},
                )
            output: T = result.output
        else:
            output = self.strict_json_gateway.parse(
                content=result.content or "",
                output_schema=output_schema,
            )
        usage = LLMUsage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        )
        return LLMResponse(
            output=output,
            metadata=LLMResponseMetadata(
                request_id="fake_llm_request",
                model=result.model,
                model_role=role.value,
                prompt_id=prompt.record.prompt_id,
                prompt_version=prompt.record.prompt_version,
                prompt_sha256=prompt.record.prompt_sha256,
                response_id=result.response_id,
                latency_ms=0,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                finish_reason="stop",
                attempt_count=1,
            ),
        )


__all__ = ["FakeLLMGateway", "FakeLLMResult", "RecordedLLMCall"]
