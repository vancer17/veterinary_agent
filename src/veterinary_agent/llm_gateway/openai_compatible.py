##################################################################################################
# 文件: src/veterinary_agent/llm_gateway/openai_compatible.py
# 作用: 实现面向 one-api、LiteLLM、New API 等代理的 OpenAI-compatible HTTP 与 SSE 适配器。
# 边界: 仅负责协议转换、响应解析和供应商错误归一；不执行 profile 降级、业务重试或 prompt 构造。
##################################################################################################

from collections.abc import AsyncIterator, Mapping
import json
import os
from time import perf_counter

import httpx
from httpx_sse import SSEError, aconnect_sse
from pydantic import ValidationError

from veterinary_agent.config import (
    LlmProviderRouteConfig,
    LlmTimeoutPolicyConfig,
)
from veterinary_agent.llm_gateway.dto import (
    JsonMap,
    LlmFunctionCallDto,
    LlmProviderRouteHealthDto,
    LlmResponseFormatDto,
    LlmToolCallDeltaDto,
    LlmToolCallDto,
    LlmUsageSummaryDto,
    ProviderInvocationRequestDto,
    ProviderInvocationResponseDto,
    ProviderStreamEventDto,
)
from veterinary_agent.llm_gateway.enums import (
    LlmFinishReason,
    LlmGatewayErrorCode,
    LlmGatewayOperation,
    LlmResponseFormatType,
    ProviderStreamEventType,
)
from veterinary_agent.llm_gateway.errors import LlmGatewayError
from veterinary_agent.llm_gateway.messages import LangChainLlmMessageAdapter

_SSE_DONE = "[DONE]"


def _join_url(base_url: str, path: str) -> str:
    """拼接模型代理基础地址与接口路径。

    :param base_url: 模型代理基础地址。
    :param path: 以斜杠开头的接口路径。
    :return: 去除重复斜杠后的完整 URL。
    """

    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _normalize_finish_reason(value: object) -> LlmFinishReason:
    """归一化供应商完成原因。

    :param value: 供应商返回的原始完成原因。
    :return: LlmGateway 稳定完成原因。
    """

    if not isinstance(value, str):
        return LlmFinishReason.UNKNOWN
    normalized = value.lower()
    mapping = {
        "stop": LlmFinishReason.STOP,
        "length": LlmFinishReason.LENGTH,
        "max_tokens": LlmFinishReason.LENGTH,
        "tool_calls": LlmFinishReason.TOOL_CALLS,
        "function_call": LlmFinishReason.TOOL_CALLS,
        "content_filter": LlmFinishReason.SAFETY,
        "safety": LlmFinishReason.SAFETY,
        "cancelled": LlmFinishReason.CANCELLED,
    }
    return mapping.get(normalized, LlmFinishReason.UNKNOWN)


def _parse_usage(value: object) -> LlmUsageSummaryDto:
    """解析 OpenAI-compatible usage 对象。

    :param value: 供应商返回的原始 usage 值。
    :return: 缺失字段按零补齐的 token 使用摘要。
    :raises LlmGatewayError: 当 usage 字段类型非法时抛出。
    """

    if value is None:
        return LlmUsageSummaryDto()
    if not isinstance(value, Mapping):
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="模型代理返回的 usage 结构非法",
        )
    prompt_value = value.get("prompt_tokens", value.get("input_tokens", 0))
    completion_value = value.get(
        "completion_tokens",
        value.get("output_tokens", 0),
    )
    total_value = value.get("total_tokens", 0)
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in (prompt_value, completion_value, total_value)
    ):
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="模型代理返回的 usage 数值非法",
        )
    return LlmUsageSummaryDto(
        input_tokens=prompt_value,
        output_tokens=completion_value,
        total_tokens=total_value,
        estimated=False,
    )


def _parse_tool_calls(value: object) -> list[LlmToolCallDto]:
    """解析 OpenAI-compatible 完整工具调用列表。

    :param value: 供应商返回的原始工具调用值。
    :return: 归一化完整工具调用列表。
    :raises LlmGatewayError: 当工具调用结构无法校验时抛出。
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="模型代理返回的 tool_calls 结构非法",
        )
    tool_calls: list[LlmToolCallDto] = []
    try:
        for raw_tool_call in value:
            if not isinstance(raw_tool_call, Mapping):
                raise ValueError("tool call must be a mapping")
            raw_function = raw_tool_call.get("function")
            if not isinstance(raw_function, Mapping):
                raise ValueError("tool call function must be a mapping")
            tool_calls.append(
                LlmToolCallDto(
                    id=str(raw_tool_call.get("id", "")),
                    type="function",
                    function=LlmFunctionCallDto(
                        name=str(raw_function.get("name", "")),
                        arguments=str(raw_function.get("arguments", "")),
                    ),
                )
            )
    except (ValidationError, ValueError) as exc:
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="模型代理返回的工具调用无法归一化",
        ) from exc
    return tool_calls


def _parse_tool_call_deltas(value: object) -> list[LlmToolCallDeltaDto]:
    """解析 OpenAI-compatible 流式工具调用增量。

    :param value: 供应商流式 delta 中的原始工具调用值。
    :return: 归一化工具调用增量列表。
    :raises LlmGatewayError: 当工具调用增量结构无法校验时抛出。
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.STREAM_LLM,
            message="模型代理返回的流式 tool_calls 结构非法",
        )
    deltas: list[LlmToolCallDeltaDto] = []
    try:
        for raw_delta in value:
            if not isinstance(raw_delta, Mapping):
                raise ValueError("tool call delta must be a mapping")
            raw_function = raw_delta.get("function")
            function = raw_function if isinstance(raw_function, Mapping) else {}
            raw_index = raw_delta.get("index", 0)
            if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                raise ValueError("tool call delta index invalid")
            raw_id = raw_delta.get("id")
            raw_name = function.get("name")
            raw_arguments = function.get("arguments", "")
            deltas.append(
                LlmToolCallDeltaDto(
                    index=raw_index,
                    id=str(raw_id) if raw_id is not None else None,
                    name=str(raw_name) if raw_name is not None else None,
                    arguments_delta=str(raw_arguments),
                )
            )
    except (ValidationError, ValueError) as exc:
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.STREAM_LLM,
            message="模型代理返回的流式工具调用无法归一化",
        ) from exc
    return deltas


def _render_response_format(response_format: LlmResponseFormatDto) -> JsonMap:
    """转换响应格式为 OpenAI-compatible 请求结构。

    :param response_format: 协议无关响应格式。
    :return: OpenAI-compatible ``response_format`` 对象。
    """

    if response_format.type is LlmResponseFormatType.TEXT:
        return {"type": "text"}
    if response_format.type is LlmResponseFormatType.JSON_OBJECT:
        return {"type": "json_object"}
    if response_format.json_schema is None:
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_INVALID_REQUEST,
            operation=LlmGatewayOperation.INVOKE_LLM,
            message="json_schema 响应格式缺少 schema 定义",
        )
    return {
        "type": "json_schema",
        "json_schema": response_format.json_schema.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        ),
    }


def _render_request_payload(
    *,
    request: ProviderInvocationRequestDto,
    include_stream_usage: bool,
    message_adapter: LangChainLlmMessageAdapter,
) -> JsonMap:
    """转换归一化物理调用请求为 OpenAI-compatible 请求体。

    :param request: ProviderAdapter 物理调用请求。
    :param include_stream_usage: 流式请求是否要求代理返回 usage。
    :param message_adapter: LangChain 消息适配器。
    :return: 可直接作为 JSON 发送的 OpenAI-compatible 请求体。
    """

    payload: JsonMap = {
        **request.generation_params,
        "model": request.model_alias,
        "messages": message_adapter.to_openai_messages(request.messages),
        "stream": request.stream,
    }
    if request.response_format.type is not LlmResponseFormatType.TEXT:
        payload["response_format"] = _render_response_format(request.response_format)
    if request.tool_schemas:
        payload["tools"] = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in request.tool_schemas
        ]
    if request.stream and include_stream_usage:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _parse_error_payload(response: httpx.Response) -> tuple[str | None, str | None]:
    """提取代理错误响应中的短错误类型与短错误码。

    :param response: 模型代理 HTTP 错误响应。
    :return: 错误类型和错误码；无法安全解析时返回空值。
    """

    try:
        payload = response.json()
    except ValueError:
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    raw_error = payload.get("error")
    if not isinstance(raw_error, Mapping):
        return None, None
    raw_type = raw_error.get("type")
    raw_code = raw_error.get("code")
    return (
        str(raw_type)[:128] if raw_type is not None else None,
        str(raw_code)[:128] if raw_code is not None else None,
    )


def _error_from_http_response(
    *,
    response: httpx.Response,
    operation: LlmGatewayOperation,
    provider_route_id: str,
) -> LlmGatewayError:
    """将模型代理 HTTP 错误响应归一化为 LlmGateway 错误。

    :param response: 模型代理 HTTP 错误响应。
    :param operation: 当前 LlmGateway 操作名。
    :param provider_route_id: 当前供应商路由 ID。
    :return: 不包含响应正文的 LlmGateway 领域异常。
    """

    status_code = response.status_code
    provider_error_type, provider_error_code = _parse_error_payload(response)
    conflict_with: JsonMap = {"status_code": status_code}
    if provider_error_type is not None:
        conflict_with["provider_error_type"] = provider_error_type
    if provider_error_code is not None:
        conflict_with["provider_error_code"] = provider_error_code
    if status_code == 429:
        code = LlmGatewayErrorCode.LLM_RATE_LIMITED
        message = "模型供应商或代理返回限流"
    elif status_code in {401, 403}:
        code = LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE
        message = "模型代理鉴权失败或当前路由无权限"
    elif status_code in {400, 404, 405, 409, 413, 415, 422}:
        if provider_error_code in {
            "context_length_exceeded",
            "context_window_exceeded",
        }:
            code = LlmGatewayErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED
            message = "模型供应商拒绝超出上下文长度的请求"
        else:
            code = LlmGatewayErrorCode.LLM_INVALID_REQUEST
            message = "模型代理拒绝当前请求参数"
    elif status_code in {408, 504}:
        code = LlmGatewayErrorCode.LLM_TIMEOUT
        message = "模型代理或供应商调用超时"
    elif status_code in {502, 503}:
        code = LlmGatewayErrorCode.LLM_PROXY_UNAVAILABLE
        message = "模型代理暂时不可用"
    elif status_code >= 500:
        code = LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE
        message = "模型供应商暂时不可用"
    else:
        code = LlmGatewayErrorCode.LLM_PROVIDER_UNAVAILABLE
        message = "模型代理调用失败"
    return LlmGatewayError(
        code=code,
        operation=operation,
        message=message,
        provider_route_id=provider_route_id,
        conflict_with=conflict_with,
    )


def _error_from_sse_error(
    *,
    exc: SSEError,
    provider_route_id: str,
) -> LlmGatewayError:
    """将 SSE 协议解析错误转换为 LlmGateway 错误。

    :param exc: httpx-sse 抛出的协议解析异常。
    :param provider_route_id: 当前供应商路由 ID。
    :return: 不暴露响应正文的 LlmGateway 领域异常。
    """

    return LlmGatewayError(
        code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
        operation=LlmGatewayOperation.STREAM_LLM,
        message="模型代理流式响应不是合法 SSE 事件流",
        provider_route_id=provider_route_id,
        conflict_with={"sse_error": exc.__class__.__name__},
    )


class OpenAICompatibleAdapter:
    """OpenAI-compatible 模型代理适配器。"""

    def __init__(
        self,
        *,
        route: LlmProviderRouteConfig,
        timeout_policy: LlmTimeoutPolicyConfig,
        client: httpx.AsyncClient | None = None,
        message_adapter: LangChainLlmMessageAdapter | None = None,
    ) -> None:
        """初始化 OpenAI-compatible 适配器。

        :param route: 当前供应商路由配置。
        :param timeout_policy: 物理请求超时策略。
        :param client: 可选外部 AsyncClient；测试或共享连接池场景可注入。
        :param message_adapter: 可选 LangChain 消息适配器；未传入时创建默认实例。
        :return: None。
        """

        self._route = route
        self._timeout_policy = timeout_policy
        self._api_key = (
            os.getenv(route.api_key_env) if route.api_key_env is not None else None
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._message_adapter = message_adapter or LangChainLlmMessageAdapter()

    def is_ready(self) -> bool:
        """判断适配器本地配置是否完整。

        :return: 若客户端未关闭且鉴权要求满足，则返回 True。
        """

        return not self._client.is_closed and (
            not self._route.auth_required or bool(self._api_key)
        )

    def _headers(self, *, request: ProviderInvocationRequestDto) -> dict[str, str]:
        """构建模型代理请求头。

        :param request: 当前物理调用请求。
        :return: 包含关联 ID 和可选代理鉴权信息的请求头。
        """

        headers = {
            "Accept": "text/event-stream" if request.stream else "application/json",
            "Content-Type": "application/json",
            "X-Call-ID": request.call_id,
            "X-Request-ID": request.request_id,
            "X-Trace-ID": request.trace_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _timeout(self) -> httpx.Timeout:
        """构建 httpx 超时对象。

        :return: 映射连接和读取上限的 httpx Timeout。
        """

        return httpx.Timeout(
            timeout=self._timeout_policy.total_timeout_seconds,
            connect=self._timeout_policy.connect_timeout_seconds,
            read=self._timeout_policy.read_timeout_seconds,
            write=self._timeout_policy.connect_timeout_seconds,
            pool=self._timeout_policy.connect_timeout_seconds,
        )

    def _build_network_error(
        self,
        *,
        operation: LlmGatewayOperation,
        exc: httpx.HTTPError,
    ) -> LlmGatewayError:
        """将 httpx 网络异常转换为 LlmGateway 错误。

        :param operation: 当前 LlmGateway 操作名。
        :param exc: 捕获的 httpx 网络异常。
        :return: 不暴露真实 URL 的 LlmGateway 领域异常。
        """

        if isinstance(exc, httpx.TimeoutException):
            code = LlmGatewayErrorCode.LLM_TIMEOUT
            message = "模型代理网络调用超时"
        else:
            code = LlmGatewayErrorCode.LLM_PROXY_UNAVAILABLE
            message = "模型代理网络连接不可用"
        return LlmGatewayError(
            code=code,
            operation=operation,
            message=message,
            provider_route_id=self._route.provider_route_id,
            conflict_with={"network_error": exc.__class__.__name__},
        )

    async def invoke(
        self,
        request: ProviderInvocationRequestDto,
    ) -> ProviderInvocationResponseDto:
        """执行一次非流式 OpenAI-compatible 模型调用。

        :param request: 已解析模型别名的物理调用请求。
        :return: 协议无关模型响应。
        :raises LlmGatewayError: 当适配器未就绪、网络失败或响应非法时抛出。
        """

        if not self.is_ready():
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理适配器未就绪",
                provider_route_id=self._route.provider_route_id,
            )
        try:
            response = await self._client.post(
                _join_url(self._route.base_url, self._route.request_path),
                headers=self._headers(request=request),
                json=_render_request_payload(
                    request=request,
                    include_stream_usage=self._route.include_stream_usage,
                    message_adapter=self._message_adapter,
                ),
                timeout=self._timeout(),
            )
        except httpx.HTTPError as exc:
            raise self._build_network_error(
                operation=LlmGatewayOperation.INVOKE_LLM,
                exc=exc,
            ) from exc
        if response.is_error:
            raise _error_from_http_response(
                response=response,
                operation=LlmGatewayOperation.INVOKE_LLM,
                provider_route_id=self._route.provider_route_id,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理返回的 JSON 无法解析",
                provider_route_id=self._route.provider_route_id,
            ) from exc
        return self._parse_invoke_payload(payload)

    def _parse_invoke_payload(
        self,
        payload: object,
    ) -> ProviderInvocationResponseDto:
        """解析非流式 OpenAI-compatible 响应体。

        :param payload: 模型代理返回的原始 JSON 值。
        :return: 协议无关模型响应。
        :raises LlmGatewayError: 当响应缺少模型、选择项或消息结构时抛出。
        """

        if not isinstance(payload, Mapping):
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理响应根结构非法",
                provider_route_id=self._route.provider_route_id,
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理响应缺少 choices",
                provider_route_id=self._route.provider_route_id,
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理响应 choice 结构非法",
                provider_route_id=self._route.provider_route_id,
            )
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理响应缺少 message",
                provider_route_id=self._route.provider_route_id,
            )
        raw_model = payload.get("model", self._route.model_alias)
        content_value = message.get("content")
        if content_value is not None and not isinstance(content_value, str):
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.INVOKE_LLM,
                message="模型代理响应 content 类型非法",
                provider_route_id=self._route.provider_route_id,
            )
        return ProviderInvocationResponseDto(
            actual_model=str(raw_model),
            content=content_value,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            finish_reason=_normalize_finish_reason(choice.get("finish_reason")),
            usage=_parse_usage(payload.get("usage")),
        )

    async def stream(
        self,
        request: ProviderInvocationRequestDto,
    ) -> AsyncIterator[ProviderStreamEventDto]:
        """执行一次流式 OpenAI-compatible 模型调用。

        :param request: 已解析模型别名的流式物理调用请求。
        :return: 协议无关模型事件异步迭代器。
        :raises LlmGatewayError: 当适配器未就绪、网络失败或流式响应非法时抛出。
        """

        if not self.is_ready():
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_PROFILE_UNAVAILABLE,
                operation=LlmGatewayOperation.STREAM_LLM,
                message="模型代理适配器未就绪",
                provider_route_id=self._route.provider_route_id,
            )
        try:
            async with aconnect_sse(
                self._client,
                "POST",
                _join_url(self._route.base_url, self._route.request_path),
                headers=self._headers(request=request),
                json=_render_request_payload(
                    request=request,
                    include_stream_usage=self._route.include_stream_usage,
                    message_adapter=self._message_adapter,
                ),
                timeout=self._timeout(),
            ) as event_source:
                response = event_source.response
                if response.is_error:
                    await response.aread()
                    raise _error_from_http_response(
                        response=response,
                        operation=LlmGatewayOperation.STREAM_LLM,
                        provider_route_id=self._route.provider_route_id,
                    )
                async for event in event_source.aiter_sse():
                    if event.data == _SSE_DONE:
                        break
                    yield self._parse_stream_payload(data=event.data)
        except LlmGatewayError:
            raise
        except SSEError as exc:
            raise _error_from_sse_error(
                exc=exc,
                provider_route_id=self._route.provider_route_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._build_network_error(
                operation=LlmGatewayOperation.STREAM_LLM,
                exc=exc,
            ) from exc

    def _parse_stream_payload(self, *, data: str) -> ProviderStreamEventDto:
        """解析单个 OpenAI-compatible SSE data 事件。

        :param data: 单个完整 SSE 事件的 data 文本。
        :return: 协议无关模型流式事件。
        :raises LlmGatewayError: 当 SSE JSON 或 chunk 结构非法时抛出。
        """

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.STREAM_LLM,
                message="模型代理流式事件 JSON 无法解析",
                provider_route_id=self._route.provider_route_id,
            ) from exc
        if not isinstance(payload, Mapping):
            raise LlmGatewayError(
                code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                operation=LlmGatewayOperation.STREAM_LLM,
                message="模型代理流式事件根结构非法",
                provider_route_id=self._route.provider_route_id,
            )
        actual_model = str(payload.get("model", self._route.model_alias))
        usage = (
            _parse_usage(payload.get("usage"))
            if payload.get("usage") is not None
            else None
        )
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                    operation=LlmGatewayOperation.STREAM_LLM,
                    message="模型代理流式 choice 结构非法",
                    provider_route_id=self._route.provider_route_id,
                )
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                delta = {}
            content_value = delta.get("content", "")
            if content_value is None:
                content_value = ""
            if not isinstance(content_value, str):
                raise LlmGatewayError(
                    code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
                    operation=LlmGatewayOperation.STREAM_LLM,
                    message="模型代理流式 content 类型非法",
                    provider_route_id=self._route.provider_route_id,
                )
            tool_call_deltas = _parse_tool_call_deltas(delta.get("tool_calls"))
            finish_reason = (
                _normalize_finish_reason(choice.get("finish_reason"))
                if choice.get("finish_reason") is not None
                else None
            )
            if content_value:
                event_type = ProviderStreamEventType.DELTA
            elif tool_call_deltas:
                event_type = ProviderStreamEventType.TOOL_CALL_DELTA
            elif finish_reason is not None:
                event_type = ProviderStreamEventType.COMPLETED
            elif usage is not None:
                event_type = ProviderStreamEventType.USAGE
            else:
                event_type = ProviderStreamEventType.DELTA
            return ProviderStreamEventDto(
                event_type=event_type,
                actual_model=actual_model,
                delta=content_value,
                tool_call_deltas=tool_call_deltas,
                finish_reason=finish_reason,
                usage=usage,
            )
        if usage is not None:
            return ProviderStreamEventDto(
                event_type=ProviderStreamEventType.USAGE,
                actual_model=actual_model,
                usage=usage,
            )
        raise LlmGatewayError(
            code=LlmGatewayErrorCode.LLM_MALFORMED_RESPONSE,
            operation=LlmGatewayOperation.STREAM_LLM,
            message="模型代理流式事件缺少 choices 和 usage",
            provider_route_id=self._route.provider_route_id,
        )

    async def healthcheck(self) -> LlmProviderRouteHealthDto:
        """检查当前模型代理路由健康状态。

        :return: 不包含响应正文的路由健康检查结果。
        """

        if not self.is_ready():
            return LlmProviderRouteHealthDto(
                provider_route_id=self._route.provider_route_id,
                healthy=False,
                reason="adapter_not_ready",
            )
        if self._route.health_path is None:
            return LlmProviderRouteHealthDto(
                provider_route_id=self._route.provider_route_id,
                healthy=True,
                reason="local_readiness_only",
            )
        started_at = perf_counter()
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._client.get(
                _join_url(self._route.base_url, self._route.health_path),
                headers=headers,
                timeout=httpx.Timeout(self._timeout_policy.connect_timeout_seconds),
            )
        except httpx.HTTPError as exc:
            return LlmProviderRouteHealthDto(
                provider_route_id=self._route.provider_route_id,
                healthy=False,
                latency_ms=round((perf_counter() - started_at) * 1000),
                reason=exc.__class__.__name__,
            )
        return LlmProviderRouteHealthDto(
            provider_route_id=self._route.provider_route_id,
            healthy=response.is_success,
            latency_ms=round((perf_counter() - started_at) * 1000),
            status_code=response.status_code,
            reason=None if response.is_success else "health_endpoint_error",
        )

    async def close(self) -> None:
        """关闭适配器拥有的 httpx 客户端。

        :return: None。
        """

        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()


class OpenAICompatibleAdapterFactory:
    """按路由和 profile 超时策略创建 OpenAI-compatible 适配器。"""

    def __init__(
        self,
        *,
        client_by_route: Mapping[str, httpx.AsyncClient] | None = None,
    ) -> None:
        """初始化 OpenAI-compatible 适配器工厂。

        :param client_by_route: 可选按路由注入的 httpx 测试或共享客户端。
        :return: None。
        """

        self._client_by_route = dict(client_by_route or {})

    def create(
        self,
        *,
        route: LlmProviderRouteConfig,
        timeout_policy: LlmTimeoutPolicyConfig,
    ) -> OpenAICompatibleAdapter:
        """创建一个 OpenAI-compatible 适配器。

        :param route: 供应商路由配置。
        :param timeout_policy: profile 物理请求超时策略。
        :return: 已绑定路由和超时策略的适配器。
        """

        return OpenAICompatibleAdapter(
            route=route,
            timeout_policy=timeout_policy,
            client=self._client_by_route.get(route.provider_route_id),
        )


__all__: tuple[str, ...] = (
    "OpenAICompatibleAdapter",
    "OpenAICompatibleAdapterFactory",
)
