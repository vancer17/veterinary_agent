##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/token_estimator.py
# 作用: 提供不依赖供应商 tokenizer 的保守 token 估算与上下文预算检查。
# 边界: 不裁剪或压缩业务上下文；估算超限时由 LlmGateway 返回稳定错误，由上游显式重构输入。
##################################################################################################

from math import ceil
import json

from veterinary_agent.config import (
    LlmModelProfileConfig,
    LlmProviderRouteConfig,
    LlmTokenEstimationConfig,
)
from veterinary_agent.llm_gateway.dto import (
    LlmImageContentPartDto,
    LlmInvocationRequestDto,
    LlmTokenEstimateDto,
)
from veterinary_agent.llm_gateway.enums import (
    LlmGatewayErrorCode,
    LlmGatewayOperation,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError

_IMAGE_ESTIMATE_TOKENS = 512


class ConservativeTokenEstimator:
    """基于字符数和协议固定开销的保守 token 估算器。"""

    def __init__(self, *, settings: LlmTokenEstimationConfig) -> None:
        """初始化保守 token 估算器。

        :param settings: 字符换算与协议开销配置。
        :return: None。
        """

        self._settings = settings

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
        :return: 本地估算的 token 预算。
        :raises LlmGatewayError: 当输出上限参数非法时抛出。
        """

        input_tokens = sum(
            self._estimate_message(message=request_message)
            for request_message in request.messages
        )
        input_tokens += sum(
            self._estimate_json(tool.model_dump(mode="json", by_alias=True))
            + self._settings.tool_overhead_tokens
            for tool in request.tool_schemas
        )
        if request.response_format.type.value != "text":
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

    def _estimate_message(self, *, message: object) -> int:
        """估算一条模型消息的 token 数。

        :param message: LlmMessageDto 消息对象。
        :return: 消息文本、多模态内容与协议开销的估算 token 数。
        """

        from veterinary_agent.llm_gateway.dto import LlmMessageDto

        if not isinstance(message, LlmMessageDto):
            raise TypeError("message 必须是 LlmMessageDto")
        estimated = self._settings.message_overhead_tokens
        if isinstance(message.content, str):
            estimated += self._estimate_text(message.content)
        elif isinstance(message.content, list):
            for content_part in message.content:
                if isinstance(content_part, LlmImageContentPartDto):
                    estimated += _IMAGE_ESTIMATE_TOKENS
                else:
                    estimated += self._estimate_text(content_part.text)
        if message.name is not None:
            estimated += self._estimate_text(message.name)
        if message.tool_call_id is not None:
            estimated += self._estimate_text(message.tool_call_id)
        if message.tool_calls:
            estimated += self._estimate_json(
                [
                    tool_call.model_dump(mode="json", by_alias=True)
                    for tool_call in message.tool_calls
                ]
            )
        return estimated

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

    def _estimate_text(self, value: str) -> int:
        """按字符换算比例估算文本 token 数。

        :param value: 待估算文本。
        :return: 向上取整后的估算 token 数。
        """

        if not value:
            return 0
        return ceil(len(value) / self._settings.chars_per_token)

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
        return self._estimate_text(serialized)


__all__: tuple[str, ...] = ("ConservativeTokenEstimator",)
