##################################################################################################
# 文件: src/veterinary_agent/agent_runner/runner.py
# 作用: 实现 AgentRunner 默认应用内服务，串联 AgentSpec、prompt 渲染、工具绑定、LlmGateway 与结构化输出校验。
# 边界: 不实现 HTTP 接入、业务图调度、真实 ToolRegistry、长期记忆、RAG、最终输出安全放行或用户可见发布。
##################################################################################################

import asyncio
from time import perf_counter

from veterinary_agent.agent_runner.dto import (
    AgentPromptEstimateDto,
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunSummaryDto,
    AgentSpecDto,
    AgentToolCallSummaryDto,
    AgentUsageSummaryDto,
    AgentValidationErrorDto,
    JsonMap,
)
from veterinary_agent.agent_runner.enums import (
    AgentResponseFormat,
    AgentRunnerErrorCode,
    AgentRunnerOperation,
    AgentRunnerTraceWriteStatus,
    AgentRunStatus,
)
from veterinary_agent.agent_runner.errors import AgentRunnerError
from veterinary_agent.agent_runner.messages import LangChainMessageComposer
from veterinary_agent.agent_runner.parser import (
    DefaultStructuredOutputParser,
    StructuredOutputParseResult,
)
from veterinary_agent.agent_runner.ports import (
    AgentRunnerTraceSink,
    AgentSpecRegistry,
    AgentToolRegistry,
    TodoAgentRunnerTraceSink,
    TodoAgentToolRegistry,
)
from veterinary_agent.agent_runner.prompt import DefaultPromptRenderer
from veterinary_agent.agent_runner.registry import InMemoryAgentSpecRegistry
from veterinary_agent.llm_gateway import (
    LlmGateway,
    LlmGatewayError,
    LlmGatewayErrorCode,
    LlmInvocationRequestDto,
    LlmInvocationResultDto,
    LlmJsonSchemaDto,
    LlmMessageDto,
    LlmResponseFormatDto,
    LlmResponseFormatType,
    LlmToolSchemaDto,
    LlmUsageSummaryDto,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)

_COMPONENT_NAME = "AgentRunner"
_UNKNOWN_MODEL_PROFILE = "unknown"


def _elapsed_ms(started_at: float) -> int:
    """计算从指定单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


def _usage_from_llm(usage: LlmUsageSummaryDto | None) -> AgentUsageSummaryDto:
    """将 LlmGateway usage 转换为 AgentRunner usage。

    :param usage: LlmGateway token 使用摘要。
    :return: AgentRunner token 使用摘要。
    """

    if usage is None:
        return AgentUsageSummaryDto()
    return AgentUsageSummaryDto(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated=usage.estimated,
    )


def _schema_name(spec: AgentSpecDto) -> str:
    """构建 LlmGateway JSON Schema 名称。

    :param spec: 已解析的 Agent 规格。
    :return: 不超过 LlmGateway 限制的 schema 名称。
    """

    if spec.output_schema_ref:
        return spec.output_schema_ref[:128]
    return f"{spec.agent_id}_{spec.agent_version}"[:128]


def _resolve_response_format(spec: AgentSpecDto) -> LlmResponseFormatDto:
    """根据 Agent 规格构造 LlmGateway 响应格式。

    :param spec: 已解析的 Agent 规格。
    :return: 可传给 LlmGateway 的响应格式。
    :raises AgentRunnerError: 当 JSON Schema 响应格式缺少 schema 时抛出。
    """

    resolved_format = spec.response_format
    if resolved_format is AgentResponseFormat.AUTO:
        resolved_format = (
            AgentResponseFormat.JSON_SCHEMA
            if spec.output_schema is not None
            else AgentResponseFormat.TEXT
        )
    if resolved_format is AgentResponseFormat.TEXT:
        return LlmResponseFormatDto(type=LlmResponseFormatType.TEXT)
    if resolved_format is AgentResponseFormat.JSON_OBJECT:
        return LlmResponseFormatDto(type=LlmResponseFormatType.JSON_OBJECT)
    if spec.output_schema is None:
        raise AgentRunnerError(
            code=AgentRunnerErrorCode.OUTPUT_SCHEMA_VALIDATION_FAILED,
            operation=AgentRunnerOperation.VALIDATE_OUTPUT_SCHEMA,
            message="JSON Schema 响应格式缺少 output_schema",
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile_id=spec.model_profile,
        )
    return LlmResponseFormatDto(
        type=LlmResponseFormatType.JSON_SCHEMA,
        json_schema=LlmJsonSchemaDto(
            name=_schema_name(spec),
            description=spec.output_schema_description,
            schema=spec.output_schema,
            strict=True,
        ),
    )


def _build_llm_request(
    *,
    request: AgentRunRequestDto,
    spec: AgentSpecDto,
    messages: list[LlmMessageDto],
    tool_schemas: list[LlmToolSchemaDto],
    repair_attempt: int,
) -> LlmInvocationRequestDto:
    """构造 LlmGateway 调用请求。

    :param request: AgentRunner 单次运行请求。
    :param spec: 已解析的 Agent 规格。
    :param messages: 已渲染模型消息。
    :param tool_schemas: 已授权工具 schema。
    :param repair_attempt: 当前格式修复尝试序号。
    :return: 可传给 LlmGateway 的调用请求。
    """

    metadata: JsonMap = {
        "run_id": request.run_id,
        "agent_id": spec.agent_id,
        "agent_version": spec.agent_version,
        "agent_type": spec.agent_type.value,
        "repair_attempt": repair_attempt,
    }
    return LlmInvocationRequestDto(
        trace_id=request.trace_id,
        request_id=request.request_id,
        caller_component=_COMPONENT_NAME,
        model_profile_id=spec.model_profile,
        messages=messages,
        response_format=_resolve_response_format(spec),
        tool_schemas=tool_schemas,
        stream=False,
        generation_params=spec.generation_params,
        metadata=metadata,
    )


def _map_llm_gateway_error(
    *,
    error: LlmGatewayError,
    request: AgentRunRequestDto,
    spec: AgentSpecDto,
) -> AgentRunnerError:
    """将 LlmGateway 错误映射为 AgentRunner 错误。

    :param error: LlmGateway 领域异常。
    :param request: AgentRunner 单次运行请求。
    :param spec: 已解析的 Agent 规格。
    :return: AgentRunner 领域异常。
    """

    code_map: dict[LlmGatewayErrorCode, AgentRunnerErrorCode] = {
        LlmGatewayErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED: (
            AgentRunnerErrorCode.TOKEN_BUDGET_EXCEEDED
        ),
        LlmGatewayErrorCode.LLM_TIMEOUT: AgentRunnerErrorCode.MODEL_TIMEOUT,
        LlmGatewayErrorCode.LLM_FIRST_TOKEN_TIMEOUT: AgentRunnerErrorCode.MODEL_TIMEOUT,
        LlmGatewayErrorCode.LLM_RETRY_EXHAUSTED: (
            AgentRunnerErrorCode.AGENT_RETRY_EXHAUSTED
        ),
        LlmGatewayErrorCode.LLM_CANCELLED: AgentRunnerErrorCode.AGENT_CANCELLED,
    }
    mapped_code = code_map.get(
        error.code,
        AgentRunnerErrorCode.MODEL_PROVIDER_ERROR,
    )
    return AgentRunnerError(
        code=mapped_code,
        operation=AgentRunnerOperation.RUN_AGENT,
        message=f"LlmGateway 调用失败: {error.error.message}",
        retryable=error.retryable,
        run_id=request.run_id,
        request_id=request.request_id,
        trace_id=request.trace_id,
        agent_id=spec.agent_id,
        agent_version=spec.agent_version,
        model_profile_id=spec.model_profile,
        conflict_with={"llm_error": error.to_dto().model_dump(mode="json")},
    )


class DefaultAgentRunner:
    """AgentRunner 默认应用内实现。"""

    def __init__(
        self,
        *,
        llm_gateway: LlmGateway,
        spec_registry: AgentSpecRegistry | None = None,
        tool_registry: AgentToolRegistry | None = None,
        trace_sink: AgentRunnerTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
        prompt_renderer: DefaultPromptRenderer | None = None,
        output_parser: DefaultStructuredOutputParser | None = None,
        message_composer: LangChainMessageComposer | None = None,
    ) -> None:
        """初始化 AgentRunner 默认实现。

        :param llm_gateway: 已装配的 LlmGateway 服务端口。
        :param spec_registry: 可选 Agent 规格注册表；未传入时使用空内存注册表。
        :param tool_registry: 可选工具绑定治理端口；未传入时使用 TODO 空壳。
        :param trace_sink: 可选运行摘要写入端口；未传入时使用 TODO 降级空壳。
        :param observability_provider: 可选项目 Observability provider。
        :param prompt_renderer: 可选 prompt 渲染器；未传入时使用默认实现。
        :param output_parser: 可选结构化输出解析器；未传入时使用默认实现。
        :param message_composer: 可选 LangChain 消息编排器；未传入时创建默认实现。
        :return: None。
        """

        resolved_message_composer = message_composer or LangChainMessageComposer()
        self._llm_gateway = llm_gateway
        self._spec_registry = spec_registry or InMemoryAgentSpecRegistry()
        self._tool_registry = tool_registry or TodoAgentToolRegistry()
        self._trace_sink = trace_sink or TodoAgentRunnerTraceSink()
        self._observability = observability_provider
        self._message_composer = resolved_message_composer
        self._prompt_renderer = prompt_renderer or DefaultPromptRenderer(
            message_composer=resolved_message_composer,
        )
        self._output_parser = output_parser or DefaultStructuredOutputParser()
        self._closed = False

    def is_ready(self) -> bool:
        """判断 AgentRunner 是否具备执行条件。

        :return: 若组件未关闭、规格注册表就绪且 LlmGateway 就绪，则返回 True。
        """

        return (
            not self._closed
            and self._spec_registry.is_ready()
            and self._llm_gateway.is_ready()
        )

    async def run_agent(
        self,
        request: AgentRunRequestDto,
    ) -> AgentRunResultDto:
        """执行一次受控 Agent 调用。

        :param request: AgentRunner 单次运行请求。
        :return: 标准化 Agent 运行结果；领域失败以 status=failed 返回。
        """

        started_at = perf_counter()
        spec: AgentSpecDto | None = None
        try:
            self._ensure_ready(request=request)
            spec = self._spec_registry.resolve_spec(
                agent_id=request.agent_id,
                agent_version=request.agent_version,
            )
            self._record_started(request=request, spec=spec)
            async with asyncio.timeout(spec.timeout_policy.total_timeout_seconds):
                result = await self._run_agent_with_spec(
                    request=request,
                    spec=spec,
                    started_at=started_at,
                )
        except asyncio.CancelledError:
            self._record_cancelled(request=request, spec=spec)
            raise
        except TimeoutError:
            error = AgentRunnerError(
                code=AgentRunnerErrorCode.MODEL_TIMEOUT,
                operation=AgentRunnerOperation.RUN_AGENT,
                message="AgentRunner 单次运行总超时",
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=request.agent_id,
                agent_version=request.agent_version,
                model_profile_id=spec.model_profile if spec is not None else None,
            )
            result = await self._build_failed_result(
                request=request,
                spec=spec,
                error=error,
                started_at=started_at,
            )
            self._record_completed(result=result)
            return result
        except AgentRunnerError as exc:
            error = exc.with_context(
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=request.agent_id,
                agent_version=request.agent_version,
                model_profile_id=spec.model_profile if spec is not None else None,
            )
            result = await self._build_failed_result(
                request=request,
                spec=spec,
                error=error,
                started_at=started_at,
            )
            self._record_completed(result=result)
            return result
        self._record_completed(result=result)
        return result

    def estimate_agent_prompt(
        self,
        request: AgentRunRequestDto,
    ) -> AgentPromptEstimateDto:
        """估算一次 Agent prompt 的 token 预算。

        :param request: AgentRunner 单次运行请求。
        :return: prompt token 预算估算结果。
        :raises AgentRunnerError: 当 AgentRunner、规格、prompt 或模型 profile 不可用时抛出。
        """

        self._ensure_ready(request=request)
        spec = self._spec_registry.resolve_spec(
            agent_id=request.agent_id,
            agent_version=request.agent_version,
        )
        messages = self._prompt_renderer.render_prompt(request=request, spec=spec)
        llm_request = _build_llm_request(
            request=request,
            spec=spec,
            messages=messages,
            tool_schemas=[],
            repair_attempt=0,
        )
        try:
            estimate = self._llm_gateway.estimate_tokens(llm_request)
        except LlmGatewayError as exc:
            raise _map_llm_gateway_error(error=exc, request=request, spec=spec) from exc
        return AgentPromptEstimateDto(
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile=spec.model_profile,
            input_tokens=estimate.input_tokens,
            reserved_output_tokens=estimate.reserved_output_tokens,
            total_budget_tokens=estimate.total_budget_tokens,
            max_context_tokens=estimate.max_context_tokens,
            estimated=estimate.estimated,
        )

    def validate_agent_spec(
        self,
        spec: AgentSpecDto,
    ) -> list[AgentValidationErrorDto]:
        """校验 Agent 规格。

        :param spec: 待校验的 Agent 规格。
        :return: 结构化校验错误列表；空列表表示通过。
        """

        return self._spec_registry.validate_spec(spec)

    async def close(self) -> None:
        """关闭 AgentRunner 持有的本地资源。

        :return: None。
        """

        self._closed = True

    def _ensure_ready(self, *, request: AgentRunRequestDto) -> None:
        """确保 AgentRunner 核心依赖可用。

        :param request: AgentRunner 单次运行请求。
        :return: None。
        :raises AgentRunnerError: 当组件关闭、规格注册表不可用或 LlmGateway 不可用时抛出。
        """

        if self._closed:
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY,
                operation=AgentRunnerOperation.RUN_AGENT,
                message="AgentRunner 已关闭",
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=request.agent_id,
                agent_version=request.agent_version,
                conflict_with={"reason": "runner_closed"},
            )
        if not self._spec_registry.is_ready():
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY,
                operation=AgentRunnerOperation.RUN_AGENT,
                message="AgentSpecRegistry 尚未就绪",
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=request.agent_id,
                agent_version=request.agent_version,
                conflict_with={"reason": "spec_registry_not_ready"},
            )
        if not self._llm_gateway.is_ready():
            raise AgentRunnerError(
                code=AgentRunnerErrorCode.AGENT_RUNNER_NOT_READY,
                operation=AgentRunnerOperation.RUN_AGENT,
                message="LlmGateway 尚未就绪",
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=request.agent_id,
                agent_version=request.agent_version,
                conflict_with={"reason": "llm_gateway_not_ready"},
            )

    async def _run_agent_with_spec(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
        started_at: float,
    ) -> AgentRunResultDto:
        """基于已解析规格执行 Agent 调用。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :param started_at: 本次 AgentRunner 运行开始单调时钟。
        :return: 成功的 Agent 运行结果。
        :raises AgentRunnerError: 当 prompt、模型、工具、解析或 schema 校验失败时抛出。
        """

        base_messages = self._prompt_renderer.render_prompt(request=request, spec=spec)
        tool_binding = await self._tool_registry.build_agent_tool_bindings(
            request=request,
            spec=spec,
        )
        repair_attempt = 0
        llm_retry_count = 0
        messages = base_messages
        last_llm_result: LlmInvocationResultDto | None = None
        last_validation_errors: list[AgentValidationErrorDto] = []
        while True:
            llm_request = _build_llm_request(
                request=request,
                spec=spec,
                messages=messages,
                tool_schemas=tool_binding.tool_schemas,
                repair_attempt=repair_attempt,
            )
            llm_result = await self._invoke_llm(
                request=llm_request,
                run_request=request,
                spec=spec,
            )
            last_llm_result = llm_result
            llm_retry_count += llm_result.retry_count
            if llm_result.tool_calls:
                raise AgentRunnerError(
                    code=AgentRunnerErrorCode.TOOL_EXECUTION_FAILED,
                    operation=AgentRunnerOperation.RUN_AGENT,
                    message="当前 AgentRunner 首版尚未执行模型返回的工具调用",
                    run_id=request.run_id,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    agent_id=spec.agent_id,
                    agent_version=spec.agent_version,
                    model_profile_id=spec.model_profile,
                    conflict_with={"tool_call_count": len(llm_result.tool_calls)},
                )
            try:
                parsed = self._output_parser.parse_and_validate(
                    content=llm_result.content,
                    spec=spec,
                )
            except AgentRunnerError as exc:
                if repair_attempt < spec.retry_policy.max_format_repair_attempts:
                    repair_attempt += 1
                    messages = self._message_composer.compose_repair_llm_messages(
                        base_messages=base_messages,
                        raw_output=llm_result.content,
                        validation_errors=[],
                    )
                    continue
                if repair_attempt > 0:
                    raise self._build_retry_exhausted_error(
                        request=request,
                        spec=spec,
                        last_error=exc,
                        validation_errors=last_validation_errors,
                    ) from exc
                raise exc
            if parsed.schema_valid:
                return await self._build_success_result(
                    request=request,
                    spec=spec,
                    parsed=parsed,
                    llm_result=llm_result,
                    tool_call_summaries=tool_binding.tool_call_summaries,
                    latency_ms=_elapsed_ms(started_at),
                    retry_count=repair_attempt + llm_retry_count,
                )
            last_validation_errors = parsed.validation_errors
            if repair_attempt < spec.retry_policy.max_format_repair_attempts:
                repair_attempt += 1
                messages = self._message_composer.compose_repair_llm_messages(
                    base_messages=base_messages,
                    raw_output=llm_result.content,
                    validation_errors=parsed.validation_errors,
                )
                continue
            error = AgentRunnerError(
                code=(
                    AgentRunnerErrorCode.AGENT_RETRY_EXHAUSTED
                    if repair_attempt > 0
                    else AgentRunnerErrorCode.OUTPUT_SCHEMA_VALIDATION_FAILED
                ),
                operation=AgentRunnerOperation.VALIDATE_OUTPUT_SCHEMA,
                message="模型输出未通过 schema 校验",
                run_id=request.run_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
                agent_id=spec.agent_id,
                agent_version=spec.agent_version,
                model_profile_id=spec.model_profile,
                conflict_with={
                    "validation_errors": [
                        item.model_dump(mode="json")
                        for item in parsed.validation_errors
                    ],
                    "model_call_id": (
                        last_llm_result.call_id if last_llm_result is not None else None
                    ),
                },
            )
            raise error

    async def _invoke_llm(
        self,
        *,
        request: LlmInvocationRequestDto,
        run_request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> LlmInvocationResultDto:
        """调用 LlmGateway 并映射模型错误。

        :param request: LlmGateway 调用请求。
        :param run_request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: LlmGateway 成功调用结果。
        :raises AgentRunnerError: 当模型调用失败时抛出。
        """

        try:
            return await self._llm_gateway.invoke(request)
        except LlmGatewayError as exc:
            raise _map_llm_gateway_error(
                error=exc,
                request=run_request,
                spec=spec,
            ) from exc

    def _build_retry_exhausted_error(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
        last_error: AgentRunnerError,
        validation_errors: list[AgentValidationErrorDto],
    ) -> AgentRunnerError:
        """构建格式修复重试耗尽错误。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :param last_error: 最后一次解析或校验错误。
        :param validation_errors: 最后一次 schema 校验错误列表。
        :return: 格式修复重试耗尽错误。
        """

        return AgentRunnerError(
            code=AgentRunnerErrorCode.AGENT_RETRY_EXHAUSTED,
            operation=AgentRunnerOperation.RUN_AGENT,
            message="AgentRunner 结构化输出格式修复重试耗尽",
            run_id=request.run_id,
            request_id=request.request_id,
            trace_id=request.trace_id,
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile_id=spec.model_profile,
            conflict_with={
                "last_error": last_error.to_dto().model_dump(mode="json"),
                "validation_errors": [
                    item.model_dump(mode="json") for item in validation_errors
                ],
            },
        )

    async def _build_success_result(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
        parsed: StructuredOutputParseResult,
        llm_result: LlmInvocationResultDto,
        tool_call_summaries: list[AgentToolCallSummaryDto],
        latency_ms: int,
        retry_count: int,
    ) -> AgentRunResultDto:
        """构建成功运行结果并写入运行摘要。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :param parsed: 结构化输出解析结果。
        :param llm_result: LlmGateway 成功调用结果。
        :param tool_call_summaries: 工具调用摘要列表。
        :param latency_ms: AgentRunner 运行耗时。
        :param retry_count: AgentRunner 与 LlmGateway 累计重试次数。
        :return: 成功 Agent 运行结果。
        """

        usage = _usage_from_llm(llm_result.usage)
        result = AgentRunResultDto(
            status=AgentRunStatus.SUCCEEDED,
            agent_id=spec.agent_id,
            agent_version=spec.agent_version,
            model_profile=spec.model_profile,
            model_id=llm_result.actual_model,
            parsed_output=parsed.parsed_output,
            schema_valid=True,
            validation_errors=[],
            tool_call_summaries=tool_call_summaries,
            usage=usage,
            latency_ms=latency_ms,
            retry_count=retry_count,
            model_call_id=llm_result.call_id,
            metadata={
                "actual_profile_id": llm_result.actual_profile_id,
                "provider_route_id": llm_result.provider_route_id,
                "fallback_used": llm_result.fallback_used,
                "fallback_chain": llm_result.fallback_chain,
                "llm_trace_write_status": llm_result.trace_write_status.value,
            },
        )
        result.trace_delivery_status = await self._write_run_summary(
            request=request,
            spec=spec,
            result=result,
        )
        return result

    async def _build_failed_result(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto | None,
        error: AgentRunnerError,
        started_at: float,
    ) -> AgentRunResultDto:
        """构建失败运行结果并尝试写入运行摘要。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格；规格解析前失败时为空。
        :param error: AgentRunner 领域异常。
        :param started_at: 本次 AgentRunner 运行开始单调时钟。
        :return: 失败 Agent 运行结果。
        """

        result = AgentRunResultDto(
            status=AgentRunStatus.FAILED,
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            model_profile=spec.model_profile if spec is not None else None,
            schema_valid=False,
            usage=AgentUsageSummaryDto(),
            latency_ms=_elapsed_ms(started_at),
            retry_count=0,
            error=error.to_dto(),
        )
        result.trace_delivery_status = await self._write_run_summary(
            request=request,
            spec=spec,
            result=result,
        )
        return result

    async def _write_run_summary(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto | None,
        result: AgentRunResultDto,
    ) -> AgentRunnerTraceWriteStatus:
        """写入 AgentRunner 脱敏运行摘要。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格；规格解析前失败时为空。
        :param result: 已构建的 AgentRunner 运行结果。
        :return: 运行摘要写入状态。
        """

        if spec is not None and not spec.trace_policy.emit_run_summary:
            return AgentRunnerTraceWriteStatus.SKIPPED
        summary = AgentRunSummaryDto(
            run_id=request.run_id,
            trace_id=request.trace_id,
            request_id=request.request_id,
            agent_id=result.agent_id,
            agent_version=result.agent_version,
            model_profile=result.model_profile or _UNKNOWN_MODEL_PROFILE,
            actual_model=result.model_id,
            status=result.status,
            schema_valid=result.schema_valid,
            usage=result.usage,
            latency_ms=result.latency_ms,
            retry_count=result.retry_count,
            error_code=result.error.code if result.error is not None else None,
            metadata={
                "trace_sink_ready": self._trace_sink.is_ready(),
            },
        )
        try:
            write_result = await self._trace_sink.write_run_summary(summary)
        except Exception:
            return AgentRunnerTraceWriteStatus.DEGRADED
        return write_result.status

    def _record_started(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto,
    ) -> None:
        """记录 AgentRunner 运行开始事件。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格。
        :return: None。
        """

        if self._observability is None:
            return
        self._observability.record_event(
            event_name="agent_runner.run.started",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO,
            safe_fields={
                "run_id": request.run_id,
                "agent_id": spec.agent_id,
                "agent_version": spec.agent_version,
                "agent_type": spec.agent_type.value,
                "model_profile": spec.model_profile,
            },
        )

    def _record_completed(self, *, result: AgentRunResultDto) -> None:
        """记录 AgentRunner 运行结束指标与事件。

        :param result: AgentRunner 运行结果。
        :return: None。
        """

        if self._observability is None:
            return
        labels = {
            "component": _COMPONENT_NAME,
            "agent_name": result.agent_id,
            "status": result.status.value,
        }
        self._observability.record_metric(
            metric_name="agent_runner_runs_total",
            value=1.0,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="AgentRunner 运行总数。",
        )
        self._observability.record_metric(
            metric_name="agent_runner_duration_seconds",
            value=result.latency_ms / 1000,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="AgentRunner 单次运行耗时。",
        )
        self._observability.record_event(
            event_name="agent_runner.run.completed",
            component=_COMPONENT_NAME,
            level=(
                StructuredLogLevel.INFO
                if result.status is AgentRunStatus.SUCCEEDED
                else StructuredLogLevel.ERROR
            ),
            safe_fields={
                "agent_id": result.agent_id,
                "agent_version": result.agent_version,
                "status": result.status.value,
                "latency_ms": result.latency_ms,
                "retry_count": result.retry_count,
                "schema_valid": result.schema_valid,
                "trace_delivery_status": result.trace_delivery_status.value,
            },
            error_type=result.error.code.value if result.error is not None else None,
        )

    def _record_cancelled(
        self,
        *,
        request: AgentRunRequestDto,
        spec: AgentSpecDto | None,
    ) -> None:
        """记录 AgentRunner 运行取消事件。

        :param request: AgentRunner 单次运行请求。
        :param spec: 已解析的 Agent 规格；取消发生在规格解析前时为空。
        :return: None。
        """

        if self._observability is None:
            return
        self._observability.record_event(
            event_name="agent_runner.run.cancelled",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.WARNING,
            safe_fields={
                "run_id": request.run_id,
                "agent_id": request.agent_id,
                "agent_version": request.agent_version,
                "model_profile": spec.model_profile if spec is not None else None,
            },
            error_type=AgentRunnerErrorCode.AGENT_CANCELLED.value,
        )


def create_default_agent_runner(
    *,
    llm_gateway: LlmGateway,
    spec_registry: AgentSpecRegistry | None = None,
    tool_registry: AgentToolRegistry | None = None,
    trace_sink: AgentRunnerTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultAgentRunner:
    """创建默认 AgentRunner。

    :param llm_gateway: 已装配的 LlmGateway 服务端口。
    :param spec_registry: 可选 Agent 规格注册表；未传入时使用空内存注册表。
    :param tool_registry: 可选工具绑定治理端口；未传入时使用 TODO 空壳。
    :param trace_sink: 可选运行摘要写入端口；未传入时使用 TODO 降级空壳。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 AgentRunner 实例。
    """

    return DefaultAgentRunner(
        llm_gateway=llm_gateway,
        spec_registry=spec_registry,
        tool_registry=tool_registry,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultAgentRunner",
    "create_default_agent_runner",
)
