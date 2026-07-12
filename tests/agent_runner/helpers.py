##################################################################################################
# 文件: tests/agent_runner/helpers.py
# 作用: 提供 AgentRunner 组件测试使用的请求、规格、fake trace sink 与 runner 构造器。
# 边界: 仅服务测试；不连接真实模型代理、不访问网络、不实现业务图或真实工具系统。
##################################################################################################

from dataclasses import dataclass, field

from veterinary_agent.agent_runner import (
    AgentResponseFormat,
    AgentRetryPolicyDto,
    AgentRunSummaryDto,
    AgentRunRequestDto,
    AgentRunner,
    AgentRunnerTraceSink,
    AgentRunnerTraceWriteResultDto,
    AgentRunnerTraceWriteStatus,
    AgentSpecDto,
    AgentTimeoutPolicyDto,
    AgentToolPolicyDto,
    AgentTracePolicyDto,
    AgentType,
    InMemoryAgentSpecRegistry,
    PromptBlockDto,
    create_default_agent_runner,
)
from veterinary_agent.llm_gateway import (
    LlmFinishReason,
    LlmFunctionCallDto,
    LlmGatewayError,
    LlmGatewaySettings,
    LlmToolCallDto,
    LlmUsageSummaryDto,
    ProviderInvocationResponseDto,
    create_default_llm_gateway,
)

from tests.llm_gateway import (
    FakeProviderAdapter,
    build_success_response,
    build_test_settings,
)


@dataclass(slots=True)
class RecordingAgentRunnerTraceSink:
    """测试用 AgentRunner 运行摘要记录器。"""

    status: AgentRunnerTraceWriteStatus = AgentRunnerTraceWriteStatus.DELIVERED
    summaries: list[AgentRunSummaryDto] = field(default_factory=list)

    def is_ready(self) -> bool:
        """判断测试运行摘要记录器是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """记录一次 AgentRunner 运行摘要。

        :param summary: AgentRunner 提交的脱敏运行摘要。
        :return: 预设写入状态。
        """

        self.summaries.append(summary)
        return AgentRunnerTraceWriteResultDto(status=self.status)


@dataclass(slots=True)
class RaisingAgentRunnerTraceSink:
    """测试用异常 AgentRunner 运行摘要记录器。"""

    exception: Exception

    def is_ready(self) -> bool:
        """判断异常运行摘要记录器是否就绪。

        :return: 固定返回 True，使测试进入写入异常分支。
        """

        return True

    async def write_run_summary(
        self,
        summary: AgentRunSummaryDto,
    ) -> AgentRunnerTraceWriteResultDto:
        """抛出预设异常以验证 AgentRunner 留痕降级。

        :param summary: AgentRunner 提交的脱敏运行摘要。
        :return: 本方法不会正常返回。
        :raises Exception: 固定抛出初始化时提供的异常。
        """

        del summary
        raise self.exception


@dataclass(frozen=True, slots=True)
class AgentRunnerFixture:
    """AgentRunner 组件测试夹具。"""

    runner: AgentRunner
    spec: AgentSpecDto
    trace_sink: AgentRunnerTraceSink
    adapter: FakeProviderAdapter


def build_agent_runner_spec(
    *,
    allowed_tools: list[str] | None = None,
    max_format_repair_attempts: int = 1,
) -> AgentSpecDto:
    """构建测试用 AgentRunner 规格。

    :param allowed_tools: 可选授权工具名列表。
    :param max_format_repair_attempts: 格式修复重试次数。
    :return: 可直接注册到 AgentSpecRegistry 的 AgentSpecDto。
    """

    return AgentSpecDto(
        agent_id="standard_consultation_agent",
        agent_version="v1",
        agent_type=AgentType.STANDARD,
        model_profile="profile_primary",
        prompt_template_ref="inline.standard.v1",
        prompt_template=(
            "你是兽医 Agent。\n"
            "上下文块：\n"
            "{prompt_blocks}\n\n"
            "任务输入：\n"
            "{task_input}\n\n"
            "运行选项：\n"
            "{runtime_options}\n"
        ),
        output_schema_ref="standard.result.v1",
        output_schema={
            "type": "object",
            "properties": {
                "result": {"type": "string"},
            },
            "required": ["result"],
            "additionalProperties": False,
        },
        response_format=AgentResponseFormat.JSON_SCHEMA,
        tool_policy=AgentToolPolicyDto(
            allowed_tools=allowed_tools or [],
        ),
        timeout_policy=AgentTimeoutPolicyDto(total_timeout_seconds=5.0),
        retry_policy=AgentRetryPolicyDto(
            max_format_repair_attempts=max_format_repair_attempts,
        ),
        trace_policy=AgentTracePolicyDto(
            emit_run_summary=True,
            persist_prompt=False,
            persist_raw_output=False,
        ),
        generation_params={"temperature": 0.0},
    )


def build_agent_runner_request(
    *,
    run_id: str = "run_agent_runner",
    content: str = "猫咪呕吐",
) -> AgentRunRequestDto:
    """构建测试用 AgentRunner 运行请求。

    :param run_id: Agent 运行 ID。
    :param content: 任务输入文本。
    :return: 可直接传入 AgentRunner 的运行请求。
    """

    return AgentRunRequestDto(
        run_id=run_id,
        trace_id="trace_agent_runner",
        request_id="req_agent_runner",
        session_id="session_001",
        user_id="user_001",
        agent_id="standard_consultation_agent",
        agent_version="v1",
        task_input={"chief_complaint": content},
        prompt_blocks=[
            PromptBlockDto(
                block_id="context_001",
                block_type="vet_context",
                content_ref_or_text="宠物为猫，体重 4kg，最近出现呕吐。",
                metadata={"source": "VetContextBuilder"},
            )
        ],
        runtime_options={"temperature": 0.0},
    )


def build_tool_call_response() -> ProviderInvocationResponseDto:
    """构建携带模型工具调用的 ProviderAdapter 响应。

    :return: 可触发 AgentRunner 工具执行边界的 ProviderInvocationResponseDto。
    """

    return ProviderInvocationResponseDto(
        actual_model="test-primary",
        content=None,
        finish_reason=LlmFinishReason.TOOL_CALLS,
        tool_calls=[
            LlmToolCallDto(
                id="tool_call_001",
                function=LlmFunctionCallDto(
                    name="fetch_lab_result",
                    arguments="{}",
                ),
            )
        ],
        usage=LlmUsageSummaryDto(
            input_tokens=10,
            output_tokens=1,
            total_tokens=11,
        ),
    )


def build_agent_runner_fixture(
    *,
    spec: AgentSpecDto | None = None,
    settings: LlmGatewaySettings | None = None,
    trace_sink: AgentRunnerTraceSink | None = None,
    outcomes: list[ProviderInvocationResponseDto | LlmGatewayError] | None = None,
    adapter_ready: bool = True,
) -> AgentRunnerFixture:
    """构建可观察 fake adapter 的 AgentRunner 组件测试夹具。

    :param spec: 可选预置 Agent 规格。
    :param settings: 可选 LlmGateway 测试配置；未传入时使用默认测试配置。
    :param trace_sink: 可选运行摘要记录器。
    :param outcomes: 可选 ProviderAdapter invoke_outcomes 序列。
    :param adapter_ready: fake ProviderAdapter 是否就绪。
    :return: 包含 runner、spec、trace sink 与 fake adapter 的测试夹具。
    """

    resolved_spec = spec or build_agent_runner_spec()
    resolved_trace_sink = trace_sink or RecordingAgentRunnerTraceSink()
    gateway_settings = settings or build_test_settings()
    adapter = FakeProviderAdapter(
        ready=adapter_ready,
        invoke_outcomes=outcomes
        or [
            build_success_response(
                content='{"result": "ok"}',
            )
        ],
    )
    gateway = create_default_llm_gateway(
        settings=gateway_settings,
        adapters_by_profile={"profile_primary": adapter},
    )
    registry = InMemoryAgentSpecRegistry([resolved_spec])
    runner = create_default_agent_runner(
        llm_gateway=gateway,
        spec_registry=registry,
        trace_sink=resolved_trace_sink,
    )
    return AgentRunnerFixture(
        runner=runner,
        spec=resolved_spec,
        trace_sink=resolved_trace_sink,
        adapter=adapter,
    )


def build_default_agent_runner(
    *,
    spec: AgentSpecDto | None = None,
    settings: LlmGatewaySettings | None = None,
    trace_sink: RecordingAgentRunnerTraceSink | None = None,
    outcomes: list[ProviderInvocationResponseDto | LlmGatewayError] | None = None,
) -> tuple[AgentRunner, AgentSpecDto, RecordingAgentRunnerTraceSink]:
    """构建测试用默认 AgentRunner。

    :param spec: 可选预置 Agent 规格。
    :param settings: 可选 LlmGateway 测试配置；未传入时使用默认测试配置。
    :param trace_sink: 可选运行摘要记录器。
    :param outcomes: 可选 ProviderAdapter invoke_outcomes 序列。
    :return: ``(runner, spec, trace_sink)`` 三元组。
    """

    resolved_trace_sink = trace_sink or RecordingAgentRunnerTraceSink()
    fixture = build_agent_runner_fixture(
        spec=spec,
        settings=settings,
        trace_sink=resolved_trace_sink,
        outcomes=outcomes,
    )
    return fixture.runner, fixture.spec, resolved_trace_sink
