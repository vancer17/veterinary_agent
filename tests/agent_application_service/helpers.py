##################################################################################################
# 文件: tests/agent_application_service/helpers.py
# 作用: 提供 AgentApplicationService 组件级测试所需的内存替身、命令构建器与异步迭代收集工具。
# 边界: 仅服务测试，不连接真实数据库、不实现真实 GraphRuntime / LogicTraceStore 领域逻辑、不绕过包级公共出口。
##################################################################################################

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Literal, Self, TypeVar

from veterinary_agent import (
    AgentCancelTurnCommandDto,
    AgentCancelTurnResultDto,
    AgentGraphEventDto,
    AgentGraphRuntime,
    AgentGraphRuntimeUnavailableError,
    AgentGraphTurnRequestDto,
    AgentGraphTurnResultDto,
    AgentLogicTraceStore,
    AgentResponseSegmentDto,
    AgentResumeTurnCommandDto,
    AgentTraceDeliveryStatus,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
    AgentTurnDiagnosticsDto,
    AgentTurnExecutionOptionsDto,
    AgentTurnInputItemDto,
    AgentTurnInputTextDto,
    AgentTurnPublishCapabilitiesDto,
    AgentTurnRequestCommandDto,
    AgentTurnRequestContextDto,
    AgentTurnTrustedIdentityDto,
    ApiIngressSettings,
    AppendMessageCommandDto,
    AppendMessageResultDto,
    CheckpointStoreSettings,
    ConversationErrorCode,
    ConversationMessageDto,
    ConversationMessageStatus,
    ConversationOperation,
    ConversationSessionDto,
    ConversationSessionStatus,
    ConversationStoreError,
    ConversationStoreSettings,
    DefaultAgentApplicationService,
    DefaultPetSessionPolicy,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    JsonMap,
    ObservabilitySettings,
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    RuntimeConfigOperation,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    TodoConversationStore,
    create_runtime_config_provider,
)

_AsyncItem = TypeVar("_AsyncItem")
GraphExecuteFailureMode = Literal["unavailable", "timeout", "exception"]


class ListGraphEventStream:
    """按顺序产出固定 GraphRuntime 事件的测试异步迭代器。"""

    def __init__(self, events: Sequence[AgentGraphEventDto]) -> None:
        """初始化测试事件流。

        :param events: 需要按顺序产出的 GraphRuntime 事件。
        :return: None。
        """

        self._events = list(events)
        self._index = 0

    def __aiter__(self) -> Self:
        """返回异步迭代器自身。

        :return: 当前异步迭代器实例。
        """

        return self

    async def __anext__(self) -> AgentGraphEventDto:
        """读取下一条测试 GraphRuntime 事件。

        :return: 下一条 GraphRuntime 事件。
        :raises StopAsyncIteration: 当固定事件已经全部产出时抛出。
        """

        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


class UnavailableGraphEventStream:
    """拉取事件时报告 GraphRuntime 不可用的测试异步迭代器。"""

    def __aiter__(self) -> Self:
        """返回异步迭代器自身。

        :return: 当前异步迭代器实例。
        """

        return self

    async def __anext__(self) -> AgentGraphEventDto:
        """拒绝读取下一条事件。

        :return: 当前测试流不会返回事件。
        :raises AgentGraphRuntimeUnavailableError: 始终抛出 GraphRuntime 不可用异常。
        """

        raise AgentGraphRuntimeUnavailableError("GraphRuntime 测试事件流不可用")


class InMemoryConversationStore(TodoConversationStore):
    """AgentApplicationService 组件测试用内存 ConversationStore。"""

    def __init__(
        self,
        *,
        append_failure: ConversationStoreError | None = None,
    ) -> None:
        """初始化测试用内存 ConversationStore。

        :param append_failure: 可选的追加消息失败异常。
        :return: None。
        """

        self.sessions: dict[str, ConversationSessionDto] = {}
        self.messages_by_idempotency_key: dict[str, ConversationMessageDto] = {}
        self.ensure_calls: list[EnsureSessionCommandDto] = []
        self.append_calls: list[AppendMessageCommandDto] = []
        self._append_failure = append_failure

    def seed_session(
        self,
        *,
        session_id: str = "session_1",
        user_id: str = "user_1",
        pet_id: str = "pet_1",
        status: ConversationSessionStatus = ConversationSessionStatus.ACTIVE,
    ) -> ConversationSessionDto:
        """预置测试 session。

        :param session_id: 需要预置的 session ID。
        :param user_id: session 绑定用户 ID。
        :param pet_id: session 绑定宠物 ID。
        :param status: session 生命周期状态。
        :return: 已写入内存状态的 session DTO。
        """

        now = datetime.now(UTC)
        session = ConversationSessionDto(
            session_id=session_id,
            user_id=user_id,
            pet_id=pet_id,
            status=status,
            created_at=now,
            updated_at=now,
            next_sequence_no=1,
        )
        self.sessions[session_id] = session
        return session

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """创建或确认测试 session 锚点。

        :param command: PetSessionPolicy 传入的 EnsureSession 命令。
        :return: 创建或确认后的 session 结果。
        :raises ConversationStoreError: 当请求 user_id 或 pet_id 与既有锚点冲突时抛出。
        """

        self.ensure_calls.append(command)
        existing = self.sessions.get(command.session_id)
        if existing is not None:
            if existing.user_id != command.user_id:
                raise ConversationStoreError(
                    code=ConversationErrorCode.SESSION_USER_CONFLICT,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="session user conflict",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                )
            if existing.pet_id != command.pet_id:
                raise ConversationStoreError(
                    code=ConversationErrorCode.SESSION_PET_CONFLICT,
                    operation=ConversationOperation.ENSURE_SESSION,
                    message="session pet conflict",
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                )
            return EnsureSessionResultDto(session=existing, created_new=False)

        session = self.seed_session(
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
        )
        return EnsureSessionResultDto(session=session, created_new=True)

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """幂等追加测试用户消息。

        :param command: AgentApplicationService 传入的追加消息命令。
        :return: 已写入或幂等命中的测试消息结果。
        :raises ConversationStoreError: 当初始化时配置了追加失败异常时抛出。
        """

        self.append_calls.append(command)
        if self._append_failure is not None:
            raise self._append_failure
        if command.idempotency_key is not None:
            existing_message = self.messages_by_idempotency_key.get(
                command.idempotency_key
            )
            if existing_message is not None:
                return AppendMessageResultDto(
                    message=existing_message,
                    idempotent=True,
                )

        now = datetime.now(UTC)
        message = ConversationMessageDto(
            message_id=f"msg_{len(self.append_calls)}",
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            role=command.role,
            content_type=command.content_type,
            content=command.content,
            sequence_no=len(self.append_calls),
            status=ConversationMessageStatus.FINALIZED,
            idempotency_key=command.idempotency_key,
            metadata=dict(command.metadata),
            created_at=now,
            finalized_at=now,
        )
        if command.idempotency_key is not None:
            self.messages_by_idempotency_key[command.idempotency_key] = message
        return AppendMessageResultDto(message=message, idempotent=False)


class SuccessfulGraphRuntime:
    """AgentApplicationService 组件测试用成功 GraphRuntime。"""

    def __init__(
        self,
        *,
        stream_events: Sequence[AgentGraphEventDto] = (),
        resume_events: Sequence[AgentGraphEventDto] = (),
    ) -> None:
        """初始化测试用成功 GraphRuntime。

        :param stream_events: 流式执行时返回的 GraphRuntime 事件。
        :param resume_events: 恢复执行时返回的 GraphRuntime 事件。
        :return: None。
        """

        self.execute_requests: list[AgentGraphTurnRequestDto] = []
        self.stream_requests: list[AgentGraphTurnRequestDto] = []
        self.resume_commands: list[AgentResumeTurnCommandDto] = []
        self.cancel_commands: list[AgentCancelTurnCommandDto] = []
        self._stream_events = list(stream_events)
        self._resume_events = list(resume_events)

    def is_ready(self) -> bool:
        """判断测试 GraphRuntime 是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """返回测试用图执行结果。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 测试用 GraphRuntime 最终结果。
        """

        self.execute_requests.append(request)
        return AgentGraphTurnResultDto(
            output_text="建议先观察精神、食欲和饮水。",
            segments=[
                AgentResponseSegmentDto(
                    segment_id="segment_1",
                    type="advice",
                    title="观察建议",
                    output_text="记录精神、食欲和饮水变化。",
                )
            ],
            metadata={"graph": "fake"},
        )

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """返回测试用流式事件。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 固定事件流。
        """

        self.stream_requests.append(request)
        return ListGraphEventStream(self._stream_events)

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """返回测试用恢复事件。

        :param command: 恢复运行命令。
        :return: 固定恢复事件流。
        """

        self.resume_commands.append(command)
        return ListGraphEventStream(self._resume_events)

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """返回测试用取消结果。

        :param command: 取消运行命令。
        :return: 测试用取消结果。
        """

        self.cancel_commands.append(command)
        return AgentCancelTurnResultDto(
            run_id=command.run_id,
            cancelled=True,
            idempotent=False,
        )


class FailingGraphRuntime(SuccessfulGraphRuntime):
    """AgentApplicationService 组件测试用失败 GraphRuntime。"""

    def __init__(
        self,
        *,
        execute_failure: GraphExecuteFailureMode | None = None,
        stream_unavailable: bool = False,
        resume_unavailable: bool = False,
        cancel_unavailable: bool = False,
    ) -> None:
        """初始化测试用失败 GraphRuntime。

        :param execute_failure: 同步执行失败模式。
        :param stream_unavailable: 流式执行是否报告不可用。
        :param resume_unavailable: 恢复执行是否报告不可用。
        :param cancel_unavailable: 取消执行是否报告不可用。
        :return: None。
        """

        super().__init__()
        self._execute_failure = execute_failure
        self._stream_unavailable = stream_unavailable
        self._resume_unavailable = resume_unavailable
        self._cancel_unavailable = cancel_unavailable

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """按配置模拟同步图执行失败。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 当前失败模式为空时返回成功结果。
        :raises AgentGraphRuntimeUnavailableError: 当失败模式为 unavailable 时抛出。
        :raises TimeoutError: 当失败模式为 timeout 时抛出。
        :raises RuntimeError: 当失败模式为 exception 时抛出。
        """

        self.execute_requests.append(request)
        if self._execute_failure == "unavailable":
            raise AgentGraphRuntimeUnavailableError("GraphRuntime 测试同步不可用")
        if self._execute_failure == "timeout":
            raise TimeoutError("GraphRuntime 测试超时")
        if self._execute_failure == "exception":
            raise RuntimeError("GraphRuntime 测试异常")
        return await super().execute_turn(request)

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """按配置模拟流式图执行失败。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 不可用事件流或空事件流。
        """

        self.stream_requests.append(request)
        if self._stream_unavailable:
            return UnavailableGraphEventStream()
        return ListGraphEventStream(())

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """按配置模拟恢复图执行失败。

        :param command: 恢复运行命令。
        :return: 不可用事件流或空事件流。
        """

        self.resume_commands.append(command)
        if self._resume_unavailable:
            return UnavailableGraphEventStream()
        return ListGraphEventStream(())

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """按配置模拟取消图执行失败。

        :param command: 取消运行命令。
        :return: 当前取消未配置失败时返回取消结果。
        :raises AgentGraphRuntimeUnavailableError: 当配置取消不可用时抛出。
        """

        self.cancel_commands.append(command)
        if self._cancel_unavailable:
            raise AgentGraphRuntimeUnavailableError("GraphRuntime 测试取消不可用")
        return AgentCancelTurnResultDto(
            run_id=command.run_id,
            cancelled=True,
            idempotent=False,
        )


class CapturingTraceStore:
    """AgentApplicationService 组件测试用 TraceStore。"""

    def __init__(
        self,
        *,
        start_status: AgentTraceDeliveryStatus = AgentTraceDeliveryStatus.WRITTEN,
        finalize_status: AgentTraceDeliveryStatus = AgentTraceDeliveryStatus.WRITTEN,
        fail_start: bool = False,
        fail_finalize: bool = False,
    ) -> None:
        """初始化测试用 TraceStore。

        :param start_status: Trace 启动返回状态。
        :param finalize_status: Trace 完成返回状态。
        :param fail_start: 是否在启动 Trace 时抛出异常。
        :param fail_finalize: 是否在完成 Trace 时抛出异常。
        :return: None。
        """

        self.starts: list[AgentTraceStartCommandDto] = []
        self.finalizes: list[AgentTraceFinalizeCommandDto] = []
        self._start_status = start_status
        self._finalize_status = finalize_status
        self._fail_start = fail_start
        self._fail_finalize = fail_finalize

    def is_ready(self) -> bool:
        """判断测试 TraceStore 是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def start_trace(
        self,
        command: AgentTraceStartCommandDto,
    ) -> AgentTraceWriteResultDto:
        """记录 Trace 启动命令并按配置返回结果。

        :param command: Trace 启动命令。
        :return: Trace 写入结果。
        :raises RuntimeError: 当配置启动失败时抛出。
        """

        self.starts.append(command)
        if self._fail_start:
            raise RuntimeError("TraceStore 测试启动失败")
        return AgentTraceWriteResultDto(status=self._start_status)

    async def finalize_trace(
        self,
        command: AgentTraceFinalizeCommandDto,
    ) -> AgentTraceWriteResultDto:
        """记录 Trace 完成命令并按配置返回结果。

        :param command: Trace 完成命令。
        :return: Trace 写入结果。
        :raises RuntimeError: 当配置完成失败时抛出。
        """

        self.finalizes.append(command)
        if self._fail_finalize:
            raise RuntimeError("TraceStore 测试完成失败")
        return AgentTraceWriteResultDto(status=self._finalize_status)


class UnavailableRuntimeConfigProvider(RuntimeConfigProvider):
    """测试用不可用 RuntimeConfig provider。"""

    def __init__(self) -> None:
        """初始化不可用 RuntimeConfig provider。

        :return: None。
        """

    def is_ready(self) -> bool:
        """判断 RuntimeConfig provider 是否可用。

        :return: 固定返回 False。
        """

        return False

    def current_snapshot(self) -> RuntimeConfigSnapshot:
        """拒绝读取 RuntimeConfig 快照。

        :return: 当前实现不会返回快照。
        :raises RuntimeConfigError: 始终抛出配置快照缺失错误。
        """

        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
            operation=RuntimeConfigOperation.GET_CURRENT_CONFIG_SNAPSHOT,
            message="RuntimeConfig 测试快照不可用",
            retryable=True,
        )


async def collect_async_iterator(
    iterator: AsyncIterator[_AsyncItem],
) -> list[_AsyncItem]:
    """收集异步迭代器中的全部元素。

    :param iterator: 需要收集的异步迭代器。
    :return: 按产出顺序收集的元素列表。
    """

    items: list[_AsyncItem] = []
    async for item in iterator:
        items.append(item)
    return items


def build_runtime_config_provider() -> RuntimeConfigProvider:
    """构建测试用 RuntimeConfig provider。

    :return: 已持有默认配置快照的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(
        api_ingress_settings=ApiIngressSettings(),
        checkpoint_store_settings=CheckpointStoreSettings(),
        conversation_store_settings=ConversationStoreSettings(),
        observability_settings=ObservabilitySettings(),
    )


def build_command(
    *,
    request_id: str = "req_agent_service",
    user_id: str = "user_1",
    session_id: str = "session_1",
    pet_id: str = "pet_1",
    idempotency_key: str = "idem_agent_service",
    response_mode: str = "sync",
    text_parts: Sequence[str] = ("小狗今天精神一般，需要观察什么？",),
) -> AgentTurnRequestCommandDto:
    """构建测试用应用服务命令。

    :param request_id: 当前测试请求 ID。
    :param user_id: 当前测试用户 ID。
    :param session_id: 当前测试 session ID。
    :param pet_id: 当前测试宠物 ID。
    :param idempotency_key: 当前测试整轮幂等键。
    :param response_mode: 当前测试响应模式。
    :param text_parts: 当前测试输入文本片段。
    :return: 可传入 AgentApplicationService 的测试命令。
    """

    return AgentTurnRequestCommandDto(
        request_context=AgentTurnRequestContextDto(
            request_id=request_id,
            trace_id=f"trace_{request_id}",
            response_mode=response_mode,
            received_at=datetime.now(UTC),
            route_kind="agent_turns",
        ),
        trusted_identity=AgentTurnTrustedIdentityDto(
            user_id=user_id,
            session_id=session_id,
            pet_id=pet_id,
        ),
        input=[
            AgentTurnInputItemDto(
                content=[
                    AgentTurnInputTextDto(text=text_part) for text_part in text_parts
                ]
            )
        ],
        idempotency_key=idempotency_key,
        execution_options=AgentTurnExecutionOptionsDto(
            orchestrator_target="test",
            connect_timeout_seconds=1,
            request_timeout_seconds=5,
            stream_first_event_timeout_seconds=1,
            stream_total_timeout_seconds=5,
            heartbeat_enabled=True,
            heartbeat_interval_seconds=1,
            stream_idle_timeout_seconds=10,
            max_stream_duration_seconds=30,
            max_event_bytes=1024,
            client_cancel_notify_timeout_seconds=1,
        ),
        publish_capabilities=AgentTurnPublishCapabilitiesDto(
            supports_sse_events=response_mode == "stream",
        ),
        diagnostics=AgentTurnDiagnosticsDto(
            service_name="test",
            environment="test",
            config_version="test",
            input_count=1,
            attachment_count=0,
        ),
    )


def build_service(
    *,
    store: InMemoryConversationStore,
    graph_runtime: AgentGraphRuntime,
    trace_store: AgentLogicTraceStore,
    runtime_config_provider: RuntimeConfigProvider | None = None,
) -> DefaultAgentApplicationService:
    """构建测试用 AgentApplicationService。

    :param store: 测试用 ConversationStore。
    :param graph_runtime: 测试用 GraphRuntime。
    :param trace_store: 测试用 TraceStore。
    :param runtime_config_provider: 可选 RuntimeConfig provider。
    :return: 已装配测试依赖的默认 AgentApplicationService。
    """

    resolved_runtime_config_provider = (
        runtime_config_provider
        if runtime_config_provider is not None
        else build_runtime_config_provider()
    )
    pet_session_policy = DefaultPetSessionPolicy(
        conversation_store=store,
        runtime_config_provider=resolved_runtime_config_provider,
    )
    return DefaultAgentApplicationService(
        runtime_config_provider=resolved_runtime_config_provider,
        pet_session_policy=pet_session_policy,
        conversation_store=store,
        graph_runtime=graph_runtime,
        logic_trace_store=trace_store,
    )


def build_graph_event(
    *,
    event_id: str,
    event_type: str,
    data: JsonMap | None = None,
) -> AgentGraphEventDto:
    """构建测试用 GraphRuntime 事件。

    :param event_id: 测试事件 ID。
    :param event_type: 测试事件类型。
    :param data: 可选测试事件数据。
    :return: GraphRuntime 事件 DTO。
    """

    return AgentGraphEventDto(
        event_id=event_id,
        event_type=event_type,
        data=data or {},
        created_at=datetime.now(UTC),
    )


def build_resume_command(
    *,
    run_id: str = "run_resume",
) -> AgentResumeTurnCommandDto:
    """构建测试用恢复命令。

    :param run_id: 需要恢复的图运行 ID。
    :return: 恢复运行命令 DTO。
    """

    return AgentResumeTurnCommandDto(
        request_id="req_resume",
        trace_id="trace_resume",
        run_id=run_id,
    )


def build_cancel_command(
    *,
    run_id: str = "run_cancel",
) -> AgentCancelTurnCommandDto:
    """构建测试用取消命令。

    :param run_id: 需要取消的图运行 ID。
    :return: 取消运行命令 DTO。
    """

    return AgentCancelTurnCommandDto(
        request_id="req_cancel",
        trace_id="trace_cancel",
        run_id=run_id,
        reason="pytest",
    )


def build_append_failure(
    *,
    request_id: str = "req_append_failed",
) -> ConversationStoreError:
    """构建测试用用户消息追加失败异常。

    :param request_id: 当前测试请求 ID。
    :return: ConversationStore 追加失败异常。
    """

    return ConversationStoreError(
        code=ConversationErrorCode.STORE_UNAVAILABLE,
        operation=ConversationOperation.APPEND_MESSAGE,
        message="ConversationStore 测试追加失败",
        request_id=request_id,
        trace_id=f"trace_{request_id}",
        retryable=True,
    )


__all__: tuple[str, ...] = (
    "CapturingTraceStore",
    "FailingGraphRuntime",
    "GraphExecuteFailureMode",
    "InMemoryConversationStore",
    "ListGraphEventStream",
    "SuccessfulGraphRuntime",
    "UnavailableGraphEventStream",
    "UnavailableRuntimeConfigProvider",
    "build_append_failure",
    "build_cancel_command",
    "build_command",
    "build_graph_event",
    "build_resume_command",
    "build_runtime_config_provider",
    "build_service",
    "collect_async_iterator",
)
