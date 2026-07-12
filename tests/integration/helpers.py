##################################################################################################
# 文件: tests/integration/helpers.py
# 作用: 提供受控依赖注入应用集成测试所需的 fake 依赖、factory 与响应断言辅助函数。
# 边界: 仅服务集成测试；不连接真实数据库、不实现真实 GraphRuntime / LogicTraceStore、不绕过生产包级公共出口。
##################################################################################################

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Literal, Self, cast

from fastapi import FastAPI

from veterinary_agent.agent_application_service import (
    AgentCancelTurnCommandDto,
    AgentCancelTurnResultDto,
    AgentGraphEventDto,
    AgentGraphRuntimeUnavailableError,
    AgentGraphTurnRequestDto,
    AgentGraphTurnResultDto,
    AgentResponseSegmentDto,
    AgentResumeTurnCommandDto,
    TodoAgentGraphRuntime,
)
from veterinary_agent.config import ApiIngressSettings
from veterinary_agent.conversation_store import (
    AppendMessageCommandDto,
    AppendMessageResultDto,
    ConversationErrorCode,
    ConversationMessageDto,
    ConversationMessageStatus,
    ConversationOperation,
    ConversationSessionDto,
    ConversationSessionStatus,
    ConversationStore,
    ConversationStoreError,
    ConversationStoreSettings,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    TodoConversationStore,
)
from veterinary_agent.checkpoint_store import (
    CheckpointStoreSettings,
    LangGraphCheckpointer,
    LangGraphRunnableConfig,
    build_langgraph_thread_config,
)
from veterinary_agent.app import (
    VeterinaryAgentAppState,
    create_app,
)
from veterinary_agent.logic_trace_store import (
    FinalizeTraceCommandDto,
    LogicTraceStore,
    LogicTraceWriteResultDto,
    LogicTraceWriteStatus,
    StartTraceCommandDto,
    TodoLogicTraceStore,
)

GraphFailureMode = Literal["unavailable", "timeout", "exception"]


class FakeCheckpointProvider:
    """应用集成测试用 checkpoint provider。"""

    def __init__(self) -> None:
        """初始化测试 checkpoint provider。

        :return: None。
        """

        self.started = False
        self.stopped = False
        self.checkpointer = cast(LangGraphCheckpointer, object())

    async def start(self) -> None:
        """启动测试 checkpoint provider。

        :return: None。
        """

        self.started = True
        self.stopped = False

    async def stop(self) -> None:
        """停止测试 checkpoint provider。

        :return: None。
        """

        self.stopped = True
        self.started = False

    def is_ready(self) -> bool:
        """判断测试 checkpoint provider 是否就绪。

        :return: 若 provider 已启动且未停止，则返回 True。
        """

        return self.started and not self.stopped

    def get_checkpointer(self) -> LangGraphCheckpointer:
        """读取测试 LangGraph checkpointer 占位对象。

        :return: 测试 LangGraph checkpointer 占位对象。
        """

        return self.checkpointer

    def build_config(
        self,
        *,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> LangGraphRunnableConfig:
        """构建测试 LangGraph thread 运行配置。

        :param thread_id: LangGraph checkpointer 使用的 thread ID。
        :param checkpoint_id: 可选 checkpoint ID。
        :return: 可传递给 LangGraph 的运行配置。
        """

        return build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )


class FakeCheckpointProviderFactory:
    """向 FastAPI lifespan 注入固定 checkpoint provider 的测试工厂。"""

    def __init__(self, provider: FakeCheckpointProvider) -> None:
        """初始化 checkpoint provider 测试工厂。

        :param provider: 需要注入应用生命周期的 checkpoint provider。
        :return: None。
        """

        self.provider = provider

    def __call__(self) -> FakeCheckpointProvider:
        """返回固定 checkpoint provider。

        :return: 测试用 checkpoint provider。
        """

        return self.provider


class IntegrationConversationStore(TodoConversationStore):
    """应用集成测试用内存 ConversationStore。"""

    def __init__(self) -> None:
        """初始化内存 ConversationStore。

        :return: None。
        """

        self.sessions: dict[str, ConversationSessionDto] = {}
        self.messages_by_idempotency_key: dict[str, ConversationMessageDto] = {}
        self.ensure_calls: list[EnsureSessionCommandDto] = []
        self.append_calls: list[AppendMessageCommandDto] = []

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
                raise self._build_conflict(
                    command=command,
                    code=ConversationErrorCode.SESSION_USER_CONFLICT,
                    message="session user conflict",
                )
            if existing.pet_id != command.pet_id:
                raise self._build_conflict(
                    command=command,
                    code=ConversationErrorCode.SESSION_PET_CONFLICT,
                    message="session pet conflict",
                )
            return EnsureSessionResultDto(session=existing, created_new=False)

        now = datetime.now(UTC)
        session = ConversationSessionDto(
            session_id=command.session_id,
            user_id=command.user_id,
            pet_id=command.pet_id,
            status=ConversationSessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            next_sequence_no=1,
        )
        self.sessions[command.session_id] = session
        return EnsureSessionResultDto(session=session, created_new=True)

    async def append_message(
        self,
        command: AppendMessageCommandDto,
    ) -> AppendMessageResultDto:
        """幂等追加测试用户消息。

        :param command: AgentApplicationService 传入的追加消息命令。
        :return: 已写入或幂等命中的消息结果。
        """

        self.append_calls.append(command)
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

    def _build_conflict(
        self,
        *,
        command: EnsureSessionCommandDto,
        code: ConversationErrorCode,
        message: str,
    ) -> ConversationStoreError:
        """构建测试 session 锚点冲突异常。

        :param command: 当前 EnsureSession 命令。
        :param code: ConversationStore 稳定错误码。
        :param message: 测试错误说明。
        :return: ConversationStore 领域异常。
        """

        return ConversationStoreError(
            code=code,
            operation=ConversationOperation.ENSURE_SESSION,
            message=message,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )


class IntegrationConversationStoreFactory:
    """向 FastAPI lifespan 注入固定 ConversationStore 的测试工厂。"""

    def __init__(self, store: ConversationStore) -> None:
        """初始化测试 ConversationStore 工厂。

        :param store: 需要注入应用生命周期的 ConversationStore。
        :return: None。
        """

        self.store = store

    def __call__(
        self,
        settings: ConversationStoreSettings,
    ) -> ConversationStore:
        """返回固定 ConversationStore。

        :param settings: ConversationStore RuntimeConfig；测试工厂不读取具体字段。
        :return: 测试用 ConversationStore。
        """

        del settings
        return self.store


class ListGraphEventStream:
    """按顺序产出固定 GraphRuntime 事件的测试异步迭代器。"""

    def __init__(self, events: Sequence[AgentGraphEventDto]) -> None:
        """初始化测试 GraphRuntime 事件流。

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
        """读取下一条 GraphRuntime 事件。

        :return: 下一条 GraphRuntime 事件。
        :raises StopAsyncIteration: 当事件已经全部产出时抛出。
        """

        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


class FakeGraphRuntime:
    """应用集成测试用 GraphRuntime。"""

    def __init__(
        self,
        *,
        failure_mode: GraphFailureMode | None = None,
        ready: bool = True,
    ) -> None:
        """初始化测试 GraphRuntime。

        :param failure_mode: 可选同步执行失败模式。
        :param ready: GraphRuntime 是否报告就绪。
        :return: None。
        """

        self.execute_requests: list[AgentGraphTurnRequestDto] = []
        self.cancel_commands: list[AgentCancelTurnCommandDto] = []
        self.resume_commands: list[AgentResumeTurnCommandDto] = []
        self._failure_mode = failure_mode
        self._ready = ready

    def is_ready(self) -> bool:
        """判断测试 GraphRuntime 是否就绪。

        :return: 初始化时配置的就绪状态。
        """

        return self._ready

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """执行测试图运行并返回固定结果或模拟失败。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 测试用 GraphRuntime 最终结果。
        :raises AgentGraphRuntimeUnavailableError: 当失败模式为 unavailable 时抛出。
        :raises TimeoutError: 当失败模式为 timeout 时抛出。
        :raises RuntimeError: 当失败模式为 exception 时抛出。
        """

        self.execute_requests.append(request)
        if self._failure_mode == "unavailable":
            raise AgentGraphRuntimeUnavailableError("GraphRuntime 集成测试不可用")
        if self._failure_mode == "timeout":
            raise TimeoutError("GraphRuntime 集成测试超时")
        if self._failure_mode == "exception":
            raise RuntimeError("GraphRuntime 集成测试异常")
        return AgentGraphTurnResultDto(
            output_text="建议先观察精神、食欲和饮水。",
            segments=[
                AgentResponseSegmentDto(
                    segment_id="segment_integration_1",
                    type="advice",
                    title="观察建议",
                    output_text="记录精神、食欲和饮水变化。",
                )
            ],
            metadata={"graph_runtime": "fake"},
        )

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """返回空 GraphRuntime 流式事件。

        :param request: AgentApplicationService 构建的图运行请求。
        :return: 空事件流。
        """

        self.execute_requests.append(request)
        return ListGraphEventStream(())

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """返回空恢复事件流。

        :param command: 恢复运行命令。
        :return: 空事件流。
        """

        self.resume_commands.append(command)
        return ListGraphEventStream(())

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """返回测试取消结果。

        :param command: 取消运行命令。
        :return: 测试取消结果。
        """

        self.cancel_commands.append(command)
        return AgentCancelTurnResultDto(
            run_id=command.run_id,
            cancelled=True,
            idempotent=False,
        )


class FakeGraphRuntimeFactory:
    """向 FastAPI lifespan 注入固定 GraphRuntime 的测试工厂。"""

    def __init__(self, runtime: FakeGraphRuntime | TodoAgentGraphRuntime) -> None:
        """初始化 GraphRuntime 测试工厂。

        :param runtime: 需要注入应用生命周期的 GraphRuntime。
        :return: None。
        """

        self.runtime = runtime

    def __call__(self) -> FakeGraphRuntime | TodoAgentGraphRuntime:
        """返回固定 GraphRuntime。

        :return: 测试用 GraphRuntime。
        """

        return self.runtime


class FakeLogicTraceStore(TodoLogicTraceStore):
    """应用集成测试用 LogicTraceStore。"""

    def __init__(
        self,
        *,
        start_status: LogicTraceWriteStatus = LogicTraceWriteStatus.WRITTEN,
        finalize_status: LogicTraceWriteStatus = LogicTraceWriteStatus.WRITTEN,
    ) -> None:
        """初始化测试 LogicTraceStore。

        :param start_status: Trace 启动返回状态。
        :param finalize_status: Trace 完成返回状态。
        :return: None。
        """

        self.starts: list[StartTraceCommandDto] = []
        self.finalizes: list[FinalizeTraceCommandDto] = []
        self._start_status = start_status
        self._finalize_status = finalize_status
        self.closed = False

    def is_ready(self) -> bool:
        """判断测试 LogicTraceStore 是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def start_trace(
        self,
        command: StartTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录 Trace 启动命令。

        :param command: Trace 启动命令。
        :return: Trace 写入结果。
        """

        self.starts.append(command)
        return LogicTraceWriteResultDto(status=self._start_status)

    async def finalize_trace(
        self,
        command: FinalizeTraceCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录 Trace 完成命令。

        :param command: Trace 完成命令。
        :return: Trace 写入结果。
        """

        self.finalizes.append(command)
        return LogicTraceWriteResultDto(status=self._finalize_status)

    async def close(self) -> None:
        """记录通用 LogicTraceStore 已进入关闭阶段。

        :return: None。
        """

        self.closed = True


class FakeLogicTraceStoreFactory:
    """向 FastAPI lifespan 注入固定 LogicTraceStore 的测试工厂。"""

    def __init__(self, store: LogicTraceStore) -> None:
        """初始化 LogicTraceStore 测试工厂。

        :param store: 需要注入应用生命周期的 LogicTraceStore。
        :return: None。
        """

        self.store = store

    def __call__(self) -> LogicTraceStore:
        """返回固定 LogicTraceStore。

        :return: 测试用 LogicTraceStore。
        """

        return self.store


class IntegrationHarness:
    """受控依赖注入应用集成测试装配结果。"""

    def __init__(
        self,
        *,
        app: FastAPI,
        conversation_store: IntegrationConversationStore,
        graph_runtime: FakeGraphRuntime | TodoAgentGraphRuntime,
        trace_store: FakeLogicTraceStore,
        checkpoint_provider: FakeCheckpointProvider,
    ) -> None:
        """初始化应用集成测试装配结果。

        :param app: 已完成测试依赖注入的 FastAPI 应用。
        :param conversation_store: 注入的 ConversationStore 测试替身。
        :param graph_runtime: 注入的 GraphRuntime 测试替身。
        :param trace_store: 注入的 LogicTraceStore 测试替身。
        :param checkpoint_provider: 注入的 checkpoint provider 测试替身。
        :return: None。
        """

        self.app = app
        self.conversation_store = conversation_store
        self.graph_runtime = graph_runtime
        self.trace_store = trace_store
        self.checkpoint_provider = checkpoint_provider


def build_valid_payload(
    *,
    request_id: str = "req_integration_001",
    user_id: str = "user_001",
    session_id: str = "session_001",
    pet_id: str = "pet_001",
    response_mode: str | None = None,
) -> dict[str, object]:
    """构建可通过 ApiIngress 校验的合法请求体。

    :param request_id: 请求体 request_id。
    :param user_id: 上游可信用户 ID。
    :param session_id: 上游可信 session ID。
    :param pet_id: 上游可信宠物 ID。
    :param response_mode: 可选响应模式；传入 stream 时写入外部 API 的 stream=true。
    :return: 合法一轮 Agent 请求体。
    """

    payload: dict[str, object] = {
        "request_id": request_id,
        "trace_id": f"trace_{request_id}",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "小狗今天精神一般，需要先观察哪些症状？",
                    }
                ],
            }
        ],
        "vet_context": {
            "user_id": user_id,
            "session_id": session_id,
            "pet_id": pet_id,
        },
    }
    if response_mode is not None:
        payload["stream"] = response_mode == "stream"
    return payload


def response_body(response_json: object) -> dict[str, object]:
    """将响应 JSON 约束为字典。

    :param response_json: HTTP 响应解析后的 JSON 对象。
    :return: 字典形式的响应体。
    """

    assert isinstance(response_json, dict)
    return cast(dict[str, object], response_json)


def detail_reasons(body: dict[str, object]) -> set[str]:
    """提取统一错误响应中的明细原因集合。

    :param body: 统一错误响应体。
    :return: details 数组中的 reason 字段集合。
    """

    details = body.get("details")
    assert isinstance(details, list)
    reasons: set[str] = set()
    for detail in details:
        assert isinstance(detail, dict)
        reason = detail.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
    return reasons


def app_state(app: FastAPI) -> VeterinaryAgentAppState:
    """读取 FastAPI app.state 中的 VeterinaryAgentAppState。

    :param app: 已启动 lifespan 的 FastAPI 应用。
    :return: 应用框架级状态。
    """

    state = getattr(app.state, "veterinary_agent_state")
    assert isinstance(state, VeterinaryAgentAppState)
    return state


def build_harness(
    *,
    settings: ApiIngressSettings | None = None,
    conversation_store: IntegrationConversationStore | None = None,
    graph_runtime: FakeGraphRuntime | TodoAgentGraphRuntime | None = None,
    trace_store: FakeLogicTraceStore | None = None,
) -> IntegrationHarness:
    """构建使用受控依赖注入的 FastAPI 应用测试装配。

    :param settings: 可选 API 接入配置。
    :param conversation_store: 可选 ConversationStore 测试替身。
    :param graph_runtime: 可选 GraphRuntime 测试替身。
    :param trace_store: 可选 LogicTraceStore 测试替身。
    :return: 集成测试装配结果。
    """

    resolved_conversation_store = (
        conversation_store
        if conversation_store is not None
        else IntegrationConversationStore()
    )
    resolved_graph_runtime = (
        graph_runtime if graph_runtime is not None else FakeGraphRuntime()
    )
    resolved_trace_store = (
        trace_store if trace_store is not None else FakeLogicTraceStore()
    )
    checkpoint_provider = FakeCheckpointProvider()
    app = create_app(
        settings=settings,
        checkpoint_store_settings=CheckpointStoreSettings(),
        conversation_store_settings=ConversationStoreSettings(),
        checkpoint_provider_factory=FakeCheckpointProviderFactory(checkpoint_provider),
        conversation_store_factory=IntegrationConversationStoreFactory(
            resolved_conversation_store
        ),
        graph_runtime_factory=FakeGraphRuntimeFactory(resolved_graph_runtime),
        logic_trace_store_factory=FakeLogicTraceStoreFactory(resolved_trace_store),
    )
    return IntegrationHarness(
        app=app,
        conversation_store=resolved_conversation_store,
        graph_runtime=resolved_graph_runtime,
        trace_store=resolved_trace_store,
        checkpoint_provider=checkpoint_provider,
    )


def settings_without_orchestrator_readiness() -> ApiIngressSettings:
    """构建关闭编排依赖 readiness 检查的 API 接入配置。

    :return: 已关闭 orchestrator readiness 检查的 API 接入配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "readiness": base_settings.readiness.model_copy(
                update={"check_orchestrator": False}
            )
        }
    )


def settings_with_rate_limit_enabled() -> ApiIngressSettings:
    """构建每分钟仅允许一次请求的 API 接入配置。

    :return: 已启用限流的 API 接入配置。
    """

    base_settings = ApiIngressSettings()
    return base_settings.model_copy(
        update={
            "rate_limit": base_settings.rate_limit.model_copy(
                update={
                    "enabled": True,
                    "max_requests_per_minute": 1,
                    "per_client_source_enabled": False,
                    "per_path_enabled": True,
                }
            ),
            "readiness": base_settings.readiness.model_copy(
                update={"check_orchestrator": False}
            ),
        }
    )


__all__: tuple[str, ...] = (
    "FakeCheckpointProvider",
    "FakeCheckpointProviderFactory",
    "FakeGraphRuntime",
    "FakeGraphRuntimeFactory",
    "FakeLogicTraceStore",
    "FakeLogicTraceStoreFactory",
    "GraphFailureMode",
    "IntegrationConversationStore",
    "IntegrationConversationStoreFactory",
    "IntegrationHarness",
    "ListGraphEventStream",
    "app_state",
    "build_harness",
    "build_valid_payload",
    "detail_reasons",
    "response_body",
    "settings_with_rate_limit_enabled",
    "settings_without_orchestrator_readiness",
)
