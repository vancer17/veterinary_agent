"""
=============================================================================
文件：src/vet_agent/response_generation/service.py
作用：编排回复生成上下文编译、Qwen 模型调用与结果封装。
范围：仅在问诊充分性已判定为 answer 后执行；本服务不决定是否回答，
      不读取或写入问诊状态，不提供硬编码回复回退，也不执行输出安全清洗。
说明：本服务是回复生成链路中“上下文编译 + 模型调用”的生产实现；
      任一依赖不可用或上游契约不满足时均 Fail Fast。
=============================================================================
"""

from __future__ import annotations

from vet_agent.runtime import QwenClient

from .context_builder import ResponseGenerationContextBuilder
from .errors import ResponseGenerationDependencyError
from .models import ResponseGenerationRequest, ResponseGenerationResult, ResponseGenerationStrategy
from .ports import ResponseGenerationServiceProtocol


class ResponseGenerationService(ResponseGenerationServiceProtocol):
    """执行回复生成上下文编译与模型调用的生产服务。

    :return: 无返回值；该服务将上游结构化事实编译为最终回答文本。
    """

    def __init__(
        self,
        *,
        qwen_client: QwenClient,
        context_builder: ResponseGenerationContextBuilder,
    ) -> None:
        """初始化回复生成服务。

        :param qwen_client: 通义千问兼容客户端。
        :param context_builder: 回复生成上下文编译器。
        :return: 无返回值。
        """
        self.qwen_client = qwen_client
        self.context_builder = context_builder

    async def generate(self, request: ResponseGenerationRequest, *, model: str) -> ResponseGenerationResult:
        """在结构化上下文编译完成后生成最终回复。

        :param request: 回复生成结构化请求。
        :param model: 模型名称。
        :return: 返回回复生成结果。
        :raises ResponseGenerationDependencyError: 模型客户端不可用或调用失败时抛出。
        """
        if not self.is_ready():
            raise ResponseGenerationDependencyError(
                "response generation client is not ready",
                details={"litellm_configured": self.qwen_client.available},
            )
        context = self.context_builder.build(request)
        try:
            raw_text = await self.qwen_client.chat(list(context.messages), model=model)
        except Exception as exc:  # pragma: no cover - 由上游模型客户端封装失败原因
            raise ResponseGenerationDependencyError(
                "response generation model call failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        return ResponseGenerationResult(
            text=raw_text,
            context=context,
            evidence=(
                *request.pet_context.evidence,
                *request.memory_context.evidence,
            ),
            strategy=ResponseGenerationStrategy.QWEN_RESPONSE_GENERATION,
        )

    def is_ready(self) -> bool:
        """检查回复生成服务是否就绪。

        :return: 模型客户端可用时返回 True。
        """
        return self.qwen_client.available
