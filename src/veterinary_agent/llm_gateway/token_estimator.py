##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/token_estimator.py
# 作用: 基于 LangChain 消息计数能力提供 LlmGateway token 估算与上下文预算检查。
# 边界: 不裁剪或压缩业务上下文；估算超限时由 LlmGateway 返回稳定错误，由上游显式重构输入。
##################################################################################################

import json
from typing import Any, cast

from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool

from veterinary_agent.config import (
    LlmModelProfileConfig,
    LlmProviderRouteConfig,
    LlmTokenEstimationConfig,
)
from veterinary_agent.llm_gateway.dto import (
    LlmInvocationRequestDto,
    LlmTokenEstimateDto,
)
from veterinary_agent.llm_gateway.enums import (
    LlmGatewayErrorCode,
    LlmGatewayOperation,
    LlmResponseFormatType,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError
from veterinary_agent.llm_gateway.messages import LangChainLlmMessageAdapter


_IMAGE_ESTIMATE_TOKENS = 512


class LangChainTokenEstimator:
    """基于 LangChain 消息计数能力的 LlmGateway token 估算器。"""

    def __init__(
        self,
        *,
        settings: LlmTokenEstimationConfig,
        message_adapter: LangChainLlmMessageAdapter | None = None,
    ) -> None:
        """初始化 LangChain token 估算器。

        :param settings: 字符换算与协议开销配置。
        :param message_adapter: 可选 LangChain 消息适配器；未传入时创建默认实例。
        :return: None。
        """

        self._settings = settings
        self._message_adapter = message_adapter or LangChainLlmMessageAdapter()

    def estimate(
        self,
        *,
        request: LlmInvocationRequestDto,
        profile: LlmModelProfileConfig,
        route: LlmProviderRouteConfig,
    ) -> LlmTokenEstimateDto:
        """估算请求输入、输出预留与总上下文预算。

        :param request: 协议无关模型调用请求。
        :param profile: 当前候选模型 profile。
        :param route: 当前候选供应商路由。
        :return: 基于 LangChain 近似计数与本地协议开销补偿得到的 token 预算。
        :raises LlmGatewayError: 当输出上限参数非法时抛出。
        """

        langchain_messages = self._message_adapter.to_langchain_messages(
            request.messages
        )
        tools: list[dict[str, Any]] = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in request.tool_schemas
        ]
        input_tokens = count_tokens_approximately(
            langchain_messages,
            chars_per_token=self._settings.chars_per_token,
            extra_tokens_per_message=self._settings.message_overhead_tokens,
            tokens_per_image=_IMAGE_ESTIMATE_TOKENS,
            tools=cast(list[BaseTool | dict[str, Any]], tools),
        )
        input_tokens += self._estimate_tool_overhead(tools=tools)
        if request.response_format.type is not LlmResponseFormatType.TEXT:
            input_tokens += self._estimate_json(
                request.response_format.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
            )
            input_tokens += self._settings.response_format_overhead_tokens
        reserved_output_tokens = self._resolve_output_reserve(
            request=request,
            profile=profile,
        )
        return LlmTokenEstimateDto(
            model_profile_id=profile.model_profile_id,
            provider_route_id=route.provider_route_id,
            input_tokens=input_tokens,
            reserved_output_tokens=reserved_output_tokens,
            total_budget_tokens=input_tokens + reserved_output_tokens,
            max_context_tokens=route.capability.max_context_tokens,
            estimated=True,
        )

    def ensure_within_context(
        self,
        *,
        request: LlmInvocationRequestDto,
        profile: LlmModelProfileConfig,
        route: LlmProviderRouteConfig,
    ) -> LlmTokenEstimateDto:
        """估算并检查请求是否位于模型上下文限制内。

        :param request: 协议无关模型调用请求。
        :param profile: 当前候选模型 profile。
        :param route: 当前候选供应商路由。
        :return: 未超限的 token 预算估算。
        :raises LlmGatewayError: 当总预算超过模型上下文限制时抛出。
        """

        estimate = self.estimate(
            request=request,
            profile=profile,
            route=route,
        )
        if estimate.total_budget_tokens <= estimate.max_context_tokens:
            return estimate
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED,
            operation=(
                LlmGatewayOperation.STREAM_LLM
                if request.stream
                else LlmGatewayOperation.INVOKE_LLM
            ),
            message="模型调用上下文预算超过目标路由限制",
            call_id=request.call_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            model_profile_id=profile.model_profile_id,
            provider_route_id=route.provider_route_id,
            conflict_with={
                "estimated_input_size": estimate.input_tokens,
                "reserved_output_size": estimate.reserved_output_tokens,
                "estimated_total_size": estimate.total_budget_tokens,
                "max_context_size": estimate.max_context_tokens,
            },
        )

    def _resolve_output_reserve(
        self,
        *,
        request: LlmInvocationRequestDto,
        profile: LlmModelProfileConfig,
    ) -> int:
        """解析调用方输出上限或使用 profile 默认预留。

        :param request: 协议无关模型调用请求。
        :param profile: 当前候选模型 profile。
        :return: 正整数输出 token 预留。
        :raises LlmGatewayError: 当输出上限不是正整数时抛出。
        """

        value = request.generation_params.get(
            "max_completion_tokens",
            request.generation_params.get("max_tokens"),
        )
        if value is None:
            return profile.reserved_output_tokens
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_INVALID_REQUEST,
                operation=(
                    LlmGatewayOperation.STREAM_LLM
                    if request.stream
                    else LlmGatewayOperation.INVOKE_LLM
                ),
                message="模型输出上限必须为正整数",
                call_id=request.call_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                model_profile_id=profile.model_profile_id,
            )
        return value

    def _estimate_tool_overhead(self, *, tools: list[dict[str, Any]]) -> int:
        """估算工具 schema 的额外协议开销。

        :param tools: OpenAI-compatible 工具 schema 列表。
        :return: 工具 schema 附加协议开销。
        """

        return len(tools) * self._settings.tool_overhead_tokens

    def _estimate_json(self, value: object) -> int:
        """序列化结构化值并估算 token 数。

        :param value: JSON 可序列化结构化值。
        :return: 稳定 JSON 文本对应的估算 token 数。
        """

        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return count_tokens_approximately(
            [serialized],
            chars_per_token=self._settings.chars_per_token,
            extra_tokens_per_message=0,
        )


class ConservativeTokenEstimator(LangChainTokenEstimator):
    """兼容旧导出名称的 LangChain token 估算器。

    当前实现已经迁移到 LangChain 近似计数能力；保留本类名用于兼容既有导入。
    """


__all__: tuple[str, ...] = (
    "ConservativeTokenEstimator",
    "LangChainTokenEstimator",
)
