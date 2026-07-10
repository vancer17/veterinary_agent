##################################################################################################
# 文件: src/veterinary_agent/agent_application_service/service.py
# 作用: 实现 AgentApplicationService 胶水层，编排配置快照、Trace、宠物会话策略、用户消息持久化与 GraphRuntime。
# 边界: 不实现 HTTP/SSE 协议、图节点调度、checkpoint、模型调用、Trace 持久化或兽医业务判断。
##################################################################################################

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Protocol

from veterinary_agent.agent_application_service.dto import (
    AgentCancelTurnCommandDto,
    AgentCancelTurnResultDto,
    AgentGraphTurnResultDto,
    AgentGraphTurnRequestDto,
    AgentResumeTurnCommandDto,
    AgentTraceFinalizeCommandDto,
    AgentTraceStartCommandDto,
    AgentTraceWriteResultDto,
    AgentTurnEventDto,
    AgentTurnExecutionContextDto,
    AgentTurnInputTextDto,
    AgentTurnRequestCommandDto,
    AgentTurnResultDto,
)
from veterinary_agent.agent_application_service.enums import (
    AgentApplicationErrorCode,
    AgentApplicationOperation,
    AgentApplicationPhase,
    AgentTraceDeliveryStatus,
    AgentTraceFinalStatus,
    AgentTurnStatus,
)
from veterinary_agent.agent_application_service.errors import (
    AgentApplicationServiceError,
)
from veterinary_agent.agent_application_service.ports import (
    AgentGraphRuntime,
    AgentGraphRuntimeUnavailableError,
    AgentLogicTraceStore,
)
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
)
from veterinary_agent.conversation_store import (
    AppendMessageCommandDto,
    ConversationMessageRole,
    ConversationStore,
    ConversationStoreError,
    MessageAttachmentRefInputDto,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    SpanStatus,
    StructuredLogLevel,
)
from veterinary_agent.pet_session_policy import (
    PetSessionContextDto,
    PetSessionPolicy,
    PetSessionPolicyError,
    PetSessionPolicyErrorCode,
    PetSessionRequestContextDto,
)

_COMPONENT_NAME = "agent_application_service"


class AgentApplicationService(Protocol):
    """单轮 Agent 应用用例服务契约。"""

    def is_ready(self) -> bool:
        """判断应用服务是否具备接受正式流量的条件。

        :return: 若强依赖均已就绪，则返回 True。
        """

        ...

    async def execute_turn(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> AgentTurnResultDto:
        """同步执行一轮 Agent 请求。

        :param command: 已由入口层归一化的单轮执行命令。
        :return: 完整单轮 Agent 执行结果。
        :raises AgentApplicationServiceError: 当策略、消息持久化或图运行失败时抛出。
        """

        ...

    def stream_turn(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> AsyncIterator[AgentTurnEventDto]:
        """流式执行一轮 Agent 请求。

        :param command: 已由入口层归一化的单轮执行命令。
        :return: 协议无关应用事件异步迭代器。
        :raises AgentApplicationServiceError: 当准备流程或图运行失败时抛出。
        """

        ...

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentTurnEventDto]:
        """恢复一轮未完成 Agent 运行。

        :param command: 恢复运行命令。
        :return: 协议无关恢复事件异步迭代器。
        :raises AgentApplicationServiceError: 当 GraphRuntime 无法恢复运行时抛出。
        """

        ...

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """取消正在执行的 Agent 运行。

        :param command: 取消运行命令。
        :return: 取消处理结果。
        :raises AgentApplicationServiceError: 当 GraphRuntime 无法处理取消时抛出。
        """

        ...


class DefaultAgentApplicationService:
    """AgentApplicationService 默认胶水层实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        pet_session_policy: PetSessionPolicy,
        conversation_store: ConversationStore,
        graph_runtime: AgentGraphRuntime,
        logic_trace_store: AgentLogicTraceStore,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 AgentApplicationService 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param pet_session_policy: 宠物会话策略服务。
        :param conversation_store: 对话事实存储服务。
        :param graph_runtime: GraphRuntime 应用内端口。
        :param logic_trace_store: LogicTraceStore 应用内端口。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._pet_session_policy = pet_session_policy
        self._conversation_store = conversation_store
        self._graph_runtime = graph_runtime
        self._logic_trace_store = logic_trace_store
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断应用服务是否具备接受正式流量的条件。

        :return: 若 RuntimeConfig、PetSessionPolicy 与 GraphRuntime 均就绪，则返回 True。
        """

        return (
            self._runtime_config_provider.is_ready()
            and self._pet_session_policy.is_ready()
            and self._graph_runtime.is_ready()
        )

    async def execute_turn(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> AgentTurnResultDto:
        """同步执行一轮 Agent 请求。

        :param command: 已由入口层归一化的单轮执行命令。
        :return: 完整单轮 Agent 执行结果。
        :raises AgentApplicationServiceError: 当策略、消息持久化或图运行失败时抛出。
        """

        operation = AgentApplicationOperation.EXECUTE_TURN
        started_monotonic = perf_counter()
        turn_id, run_id = self._build_execution_ids(command)
        trace_result = AgentTraceWriteResultDto(
            status=AgentTraceDeliveryStatus.DEGRADED,
            error_code="TRACE_NOT_STARTED",
            retryable=True,
            detail="Trace 尚未启动",
        )
        user_message_id: str | None = None
        span_handle = (
            self._observability_provider.start_span(
                span_name="agent.turn.execute",
                component=_COMPONENT_NAME,
                safe_attributes={
                    "response_mode": command.request_context.response_mode,
                    "route_kind": command.request_context.route_kind,
                },
            )
            if self._observability_provider is not None
            else None
        )
        try:
            snapshot = self._load_snapshot_or_raise(
                command=command,
                operation=operation,
                turn_id=turn_id,
                run_id=run_id,
            )
            trace_result = await self._start_trace_or_raise(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                params_version=snapshot.params_version,
                config_snapshot_id=snapshot.config_snapshot_id,
                operation=operation,
            )
            pet_context = await self._ensure_pet_context_or_raise(
                command=command,
                operation=operation,
                turn_id=turn_id,
                run_id=run_id,
                trace_result=trace_result,
            )
            user_message_id = await self._persist_user_message_or_raise(
                command=command,
                pet_context=pet_context,
                operation=operation,
                turn_id=turn_id,
                run_id=run_id,
                trace_result=trace_result,
            )
            execution_context = AgentTurnExecutionContextDto(
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                session_id=pet_context.session_id,
                user_id=pet_context.user_id,
                current_pet_id=pet_context.current_pet_id,
                user_message_id=user_message_id,
                idempotency_key=command.idempotency_key,
                params_version=snapshot.params_version,
                config_snapshot_id=snapshot.config_snapshot_id,
                response_mode=command.request_context.response_mode,
                route_kind=command.request_context.route_kind,
            )
            graph_result = await self._execute_graph_or_raise(
                command=command,
                context=execution_context,
                operation=operation,
                trace_result=trace_result,
            )
            final_trace_result = await self._finalize_trace_safely(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                final_status=AgentTraceFinalStatus.COMPLETED,
                user_message_id=user_message_id,
                error_code=None,
                summary={
                    "segment_count": len(graph_result.segments),
                    "has_reasoning_display": graph_result.reasoning_display is not None,
                },
                fallback_status=trace_result.status,
            )
            result = AgentTurnResultDto(
                turn_id=turn_id,
                run_id=run_id,
                created_at=datetime.now(UTC),
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                user_message_id=user_message_id,
                status=AgentTurnStatus.COMPLETED,
                output_text=graph_result.output_text,
                segments=list(graph_result.segments),
                reasoning_display=graph_result.reasoning_display,
                vet_result=graph_result.vet_result,
                trace_delivery_status=final_trace_result,
                metadata={
                    **graph_result.metadata,
                    "params_version": snapshot.params_version,
                    "config_snapshot_id": snapshot.config_snapshot_id,
                },
            )
            self._record_completion(
                result=result,
                duration_seconds=perf_counter() - started_monotonic,
            )
            if self._observability_provider is not None and span_handle is not None:
                self._observability_provider.finish_span(
                    handle=span_handle,
                    status=SpanStatus.SUCCEEDED,
                )
                span_handle = None
            return result
        except AgentApplicationServiceError as exc:
            await self._finalize_trace_after_error(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                user_message_id=user_message_id,
                error=exc,
                fallback_status=trace_result.status,
            )
            self._record_failure(
                error=exc,
                duration_seconds=perf_counter() - started_monotonic,
            )
            if self._observability_provider is not None and span_handle is not None:
                self._observability_provider.finish_span(
                    handle=span_handle,
                    status=SpanStatus.FAILED,
                    error_type=exc.code.value,
                )
                span_handle = None
            raise
        except Exception as exc:
            error = AgentApplicationServiceError(
                code=AgentApplicationErrorCode.INTERNAL_ERROR,
                operation=operation,
                phase=AgentApplicationPhase.GRAPH_EXECUTING,
                message="AgentApplicationService 发生未映射异常",
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency_error_code=type(exc).__name__,
                trace_delivery_status=trace_result.status,
            )
            await self._finalize_trace_after_error(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                user_message_id=user_message_id,
                error=error,
                fallback_status=trace_result.status,
            )
            self._record_failure(
                error=error,
                duration_seconds=perf_counter() - started_monotonic,
            )
            if self._observability_provider is not None and span_handle is not None:
                self._observability_provider.finish_span(
                    handle=span_handle,
                    status=SpanStatus.FAILED,
                    error_type=error.code.value,
                )
                span_handle = None
            raise error from exc

    async def stream_turn(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> AsyncIterator[AgentTurnEventDto]:
        """流式执行一轮 Agent 请求。

        :param command: 已由入口层归一化的单轮执行命令。
        :return: 协议无关应用事件异步迭代器。
        :raises AgentApplicationServiceError: 当准备流程或图运行失败时抛出。
        """

        operation = AgentApplicationOperation.STREAM_TURN
        turn_id, run_id = self._build_execution_ids(command)
        snapshot = self._load_snapshot_or_raise(
            command=command,
            operation=operation,
            turn_id=turn_id,
            run_id=run_id,
        )
        trace_result = await self._start_trace_or_raise(
            command=command,
            turn_id=turn_id,
            run_id=run_id,
            params_version=snapshot.params_version,
            config_snapshot_id=snapshot.config_snapshot_id,
            operation=operation,
        )
        pet_context = await self._ensure_pet_context_or_raise(
            command=command,
            operation=operation,
            turn_id=turn_id,
            run_id=run_id,
            trace_result=trace_result,
        )
        user_message_id = await self._persist_user_message_or_raise(
            command=command,
            pet_context=pet_context,
            operation=operation,
            turn_id=turn_id,
            run_id=run_id,
            trace_result=trace_result,
        )
        execution_context = AgentTurnExecutionContextDto(
            request_id=command.request_context.request_id,
            trace_id=command.request_context.trace_id,
            turn_id=turn_id,
            run_id=run_id,
            session_id=pet_context.session_id,
            user_id=pet_context.user_id,
            current_pet_id=pet_context.current_pet_id,
            user_message_id=user_message_id,
            idempotency_key=command.idempotency_key,
            params_version=snapshot.params_version,
            config_snapshot_id=snapshot.config_snapshot_id,
            response_mode=command.request_context.response_mode,
            route_kind=command.request_context.route_kind,
        )
        graph_request = self._build_graph_request(
            command=command,
            context=execution_context,
        )
        sequence_no = 0
        try:
            async for event in self._graph_runtime.stream_turn(graph_request):
                sequence_no += 1
                yield AgentTurnEventDto(
                    event_id=event.event_id,
                    sequence_no=sequence_no,
                    event_type=event.event_type,
                    request_id=execution_context.request_id,
                    trace_id=execution_context.trace_id,
                    turn_id=execution_context.turn_id,
                    run_id=execution_context.run_id,
                    data=dict(event.data),
                    created_at=event.created_at,
                )
            await self._finalize_trace_safely(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                final_status=AgentTraceFinalStatus.COMPLETED,
                user_message_id=user_message_id,
                error_code=None,
                summary={"stream_event_count": sequence_no},
                fallback_status=trace_result.status,
            )
        except AgentGraphRuntimeUnavailableError as exc:
            error = self._build_graph_error(
                command=command,
                operation=operation,
                turn_id=turn_id,
                run_id=run_id,
                trace_status=trace_result.status,
                error=exc,
            )
            await self._finalize_trace_after_error(
                command=command,
                turn_id=turn_id,
                run_id=run_id,
                user_message_id=user_message_id,
                error=error,
                fallback_status=trace_result.status,
            )
            raise error from exc

    async def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentTurnEventDto]:
        """恢复一轮未完成 Agent 运行。

        :param command: 恢复运行命令。
        :return: 协议无关恢复事件异步迭代器。
        :raises AgentApplicationServiceError: 当 GraphRuntime 无法恢复运行时抛出。
        """

        sequence_no = 0
        try:
            async for event in self._graph_runtime.resume_turn(command):
                sequence_no += 1
                yield AgentTurnEventDto(
                    event_id=event.event_id,
                    sequence_no=sequence_no,
                    event_type=event.event_type,
                    request_id=command.request_id,
                    trace_id=command.trace_id,
                    turn_id=f"turn_resume_{command.run_id}",
                    run_id=command.run_id,
                    data=dict(event.data),
                    created_at=event.created_at,
                )
        except AgentGraphRuntimeUnavailableError as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
                operation=AgentApplicationOperation.RESUME_TURN,
                phase=AgentApplicationPhase.GRAPH_EXECUTING,
                message=str(exc),
                request_id=command.request_id,
                trace_id=command.trace_id,
                run_id=command.run_id,
                dependency="GraphRuntime",
                dependency_error_code=exc.code,
            ) from exc

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """取消正在执行的 Agent 运行。

        :param command: 取消运行命令。
        :return: 取消处理结果。
        :raises AgentApplicationServiceError: 当 GraphRuntime 无法处理取消时抛出。
        """

        try:
            result = await self._graph_runtime.cancel_turn(command)
        except AgentGraphRuntimeUnavailableError as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
                operation=AgentApplicationOperation.CANCEL_TURN,
                phase=AgentApplicationPhase.CANCELLED,
                message=str(exc),
                request_id=command.request_id,
                trace_id=command.trace_id,
                run_id=command.run_id,
                dependency="GraphRuntime",
                dependency_error_code=exc.code,
            ) from exc
        return result

    def _build_execution_ids(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> tuple[str, str]:
        """根据身份锚点与幂等键构建稳定 turn_id 和 run_id。

        :param command: 单轮 Agent 执行命令。
        :return: 稳定 turn_id 与 run_id。
        """

        identity = command.trusted_identity
        raw_turn_key = "\x1f".join(
            (
                identity.user_id,
                identity.session_id,
                identity.pet_id,
                command.idempotency_key,
            )
        )
        turn_digest = sha256(raw_turn_key.encode("utf-8")).hexdigest()
        run_digest = sha256(f"run\x1f{turn_digest}".encode("utf-8")).hexdigest()
        return f"turn_{turn_digest}", f"run_{run_digest}"

    def _load_snapshot_or_raise(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        operation: AgentApplicationOperation,
        turn_id: str,
        run_id: str,
    ) -> RuntimeConfigSnapshot:
        """读取并绑定当前 RuntimeConfig 快照。

        :param command: 单轮 Agent 执行命令。
        :param operation: 当前应用操作。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :return: 当前不可变 RuntimeConfig 快照。
        :raises AgentApplicationServiceError: 当 RuntimeConfig 不可用时抛出。
        """

        try:
            return self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE,
                operation=operation,
                phase=AgentApplicationPhase.PREPARING,
                message="RuntimeConfig 当前快照不可用",
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency="RuntimeConfig",
                dependency_error_code=exc.code.value,
                retryable=exc.retryable,
            ) from exc

    async def _start_trace_or_raise(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        turn_id: str,
        run_id: str,
        params_version: str,
        config_snapshot_id: str,
        operation: AgentApplicationOperation,
    ) -> AgentTraceWriteResultDto:
        """在业务策略与图运行前启动逻辑链。

        :param command: 单轮 Agent 执行命令。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param params_version: 本轮业务参数版本。
        :param config_snapshot_id: 本轮配置快照 ID。
        :param operation: 当前应用操作。
        :return: Trace 启动写入结果。
        :raises AgentApplicationServiceError: 当 Trace 启动发生未降级处理的异常时抛出。
        """

        try:
            return await self._logic_trace_store.start_trace(
                AgentTraceStartCommandDto(
                    request_id=command.request_context.request_id,
                    trace_id=command.request_context.trace_id,
                    turn_id=turn_id,
                    run_id=run_id,
                    session_id=command.trusted_identity.session_id,
                    user_id=command.trusted_identity.user_id,
                    pet_id=command.trusted_identity.pet_id,
                    params_version=params_version,
                    config_snapshot_id=config_snapshot_id,
                    idempotency_key=command.idempotency_key,
                )
            )
        except Exception as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.TRACE_START_FAILED,
                operation=operation,
                phase=AgentApplicationPhase.TRACE_STARTING,
                message="LogicTraceStore 启动逻辑链失败",
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency="LogicTraceStore",
                dependency_error_code=type(exc).__name__,
                trace_delivery_status=AgentTraceDeliveryStatus.FAILED,
            ) from exc

    async def _ensure_pet_context_or_raise(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        operation: AgentApplicationOperation,
        turn_id: str,
        run_id: str,
        trace_result: AgentTraceWriteResultDto,
    ) -> PetSessionContextDto:
        """执行宠物会话策略并返回标准当前宠物上下文。

        :param command: 单轮 Agent 执行命令。
        :param operation: 当前应用操作。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param trace_result: 当前 Trace 启动结果。
        :return: 允许进入业务图的标准宠物会话上下文。
        :raises AgentApplicationServiceError: 当 PetSessionPolicy 阻断请求时抛出。
        """

        identity = command.trusted_identity
        try:
            return await self._pet_session_policy.ensure_context(
                PetSessionRequestContextDto(
                    request_id=command.request_context.request_id,
                    trace_id=command.request_context.trace_id,
                    user_id=identity.user_id,
                    session_id=identity.session_id,
                    pet_id=identity.pet_id,
                    client_pet_snapshot_ref=(
                        dict(identity.pet_info)
                        if identity.pet_info is not None
                        else None
                    ),
                )
            )
        except PetSessionPolicyError as exc:
            error_dto = exc.to_dto()
            code = self._map_pet_session_error_code(exc.code)
            raise AgentApplicationServiceError(
                code=code,
                operation=operation,
                phase=AgentApplicationPhase.PET_SESSION_POLICY,
                message=error_dto.message,
                request_id=error_dto.request_id,
                trace_id=error_dto.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency="PetSessionPolicy",
                dependency_error_code=error_dto.code.value,
                trace_delivery_status=trace_result.status,
                retryable=error_dto.retryable,
                details={
                    "decision": error_dto.decision.decision.value,
                    "missing_field": error_dto.decision.missing_field,
                    "policy_trace_delivery_status": (
                        error_dto.trace_delivery_status.value
                    ),
                },
            ) from exc

    def _map_pet_session_error_code(
        self,
        code: PetSessionPolicyErrorCode,
    ) -> AgentApplicationErrorCode:
        """将 PetSessionPolicy 错误码映射为应用层错误码。

        :param code: PetSessionPolicy 稳定错误码。
        :return: 对应的 AgentApplicationService 稳定错误码。
        """

        mapping: dict[PetSessionPolicyErrorCode, AgentApplicationErrorCode] = {
            PetSessionPolicyErrorCode.REQUIRED_FIELD_MISSING: (
                AgentApplicationErrorCode.REQUIRED_CONTEXT_MISSING
            ),
            PetSessionPolicyErrorCode.PET_MISMATCH: (
                AgentApplicationErrorCode.PET_SESSION_CONFLICT
            ),
            PetSessionPolicyErrorCode.USER_MISMATCH: (
                AgentApplicationErrorCode.SESSION_IDENTITY_CONFLICT
            ),
            PetSessionPolicyErrorCode.SESSION_CLOSED: (
                AgentApplicationErrorCode.SESSION_CLOSED
            ),
            PetSessionPolicyErrorCode.SESSION_ARCHIVED: (
                AgentApplicationErrorCode.SESSION_ARCHIVED
            ),
            PetSessionPolicyErrorCode.STORE_UNAVAILABLE: (
                AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE
            ),
            PetSessionPolicyErrorCode.RUNTIME_CONFIG_UNAVAILABLE: (
                AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE
            ),
            PetSessionPolicyErrorCode.POLICY_DISABLED: (
                AgentApplicationErrorCode.DEPENDENCY_UNAVAILABLE
            ),
            PetSessionPolicyErrorCode.INTERNAL_ERROR: (
                AgentApplicationErrorCode.INTERNAL_ERROR
            ),
        }
        return mapping[code]

    async def _persist_user_message_or_raise(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        pet_context: PetSessionContextDto,
        operation: AgentApplicationOperation,
        turn_id: str,
        run_id: str,
        trace_result: AgentTraceWriteResultDto,
    ) -> str:
        """在业务图执行前幂等保存用户消息。

        :param command: 单轮 Agent 执行命令。
        :param pet_context: 策略确认后的宠物会话上下文。
        :param operation: 当前应用操作。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param trace_result: 当前 Trace 启动结果。
        :return: 已保存或幂等命中的用户消息 ID。
        :raises AgentApplicationServiceError: 当 ConversationStore 写入失败时抛出。
        """

        try:
            result = await self._conversation_store.append_message(
                AppendMessageCommandDto(
                    request_id=command.request_context.request_id,
                    trace_id=command.request_context.trace_id,
                    session_id=pet_context.session_id,
                    user_id=pet_context.user_id,
                    pet_id=pet_context.current_pet_id,
                    role=ConversationMessageRole.USER,
                    content=self._build_user_message_content(command),
                    idempotency_key=f"{command.idempotency_key}:user-message",
                    metadata={
                        "turn_id": turn_id,
                        "run_id": run_id,
                        "route_kind": command.request_context.route_kind,
                        "response_mode": command.request_context.response_mode,
                    },
                    attachments=[
                        MessageAttachmentRefInputDto(
                            attachment_id=attachment.attachment_id,
                            attachment_type=attachment.purpose,
                            metadata={
                                "mime_type": attachment.mime_type,
                                **(attachment.metadata or {}),
                            },
                        )
                        for attachment in command.attachments
                    ],
                )
            )
        except ConversationStoreError as exc:
            error_dto = exc.to_dto()
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.USER_MESSAGE_PERSIST_FAILED,
                operation=operation,
                phase=AgentApplicationPhase.USER_MESSAGE_PERSISTING,
                message="ConversationStore 保存用户消息失败",
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency="ConversationStore",
                dependency_error_code=error_dto.code.value,
                trace_delivery_status=trace_result.status,
                retryable=error_dto.retryable,
            ) from exc
        except Exception as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.USER_MESSAGE_PERSIST_FAILED,
                operation=operation,
                phase=AgentApplicationPhase.USER_MESSAGE_PERSISTING,
                message="ConversationStore 保存用户消息时发生未映射异常",
                request_id=command.request_context.request_id,
                trace_id=command.request_context.trace_id,
                turn_id=turn_id,
                run_id=run_id,
                dependency="ConversationStore",
                dependency_error_code=type(exc).__name__,
                trace_delivery_status=trace_result.status,
            ) from exc
        return result.message.message_id

    def _build_user_message_content(
        self,
        command: AgentTurnRequestCommandDto,
    ) -> str:
        """将归一化输入中的文本内容组合为用户消息正文。

        :param command: 单轮 Agent 执行命令。
        :return: 按输入顺序拼接的用户文本；仅附件输入时返回空字符串。
        """

        text_parts = [
            content.text
            for item in command.input
            for content in item.content
            if isinstance(content, AgentTurnInputTextDto)
        ]
        return "\n".join(text_parts)

    async def _execute_graph_or_raise(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        context: AgentTurnExecutionContextDto,
        operation: AgentApplicationOperation,
        trace_result: AgentTraceWriteResultDto,
    ) -> AgentGraphTurnResultDto:
        """调用 GraphRuntime 同步执行本轮业务图。

        :param command: 单轮 Agent 执行命令。
        :param context: 已绑定消息与配置版本的执行上下文。
        :param operation: 当前应用操作。
        :param trace_result: 当前 Trace 启动结果。
        :return: GraphRuntime 最终结果。
        :raises AgentApplicationServiceError: 当图运行不可用、超时或失败时抛出。
        """

        graph_request = self._build_graph_request(command=command, context=context)
        try:
            return await self._graph_runtime.execute_turn(graph_request)
        except AgentGraphRuntimeUnavailableError as exc:
            raise self._build_graph_error(
                command=command,
                operation=operation,
                turn_id=context.turn_id,
                run_id=context.run_id,
                trace_status=trace_result.status,
                error=exc,
            ) from exc
        except TimeoutError as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.GRAPH_EXECUTION_TIMEOUT,
                operation=operation,
                phase=AgentApplicationPhase.GRAPH_EXECUTING,
                message="GraphRuntime 执行超时",
                request_id=context.request_id,
                trace_id=context.trace_id,
                turn_id=context.turn_id,
                run_id=context.run_id,
                dependency="GraphRuntime",
                dependency_error_code=type(exc).__name__,
                trace_delivery_status=trace_result.status,
            ) from exc
        except AgentApplicationServiceError:
            raise
        except Exception as exc:
            raise AgentApplicationServiceError(
                code=AgentApplicationErrorCode.GRAPH_EXECUTION_FAILED,
                operation=operation,
                phase=AgentApplicationPhase.GRAPH_EXECUTING,
                message="GraphRuntime 执行失败",
                request_id=context.request_id,
                trace_id=context.trace_id,
                turn_id=context.turn_id,
                run_id=context.run_id,
                dependency="GraphRuntime",
                dependency_error_code=type(exc).__name__,
                trace_delivery_status=trace_result.status,
            ) from exc

    def _build_graph_request(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        context: AgentTurnExecutionContextDto,
    ) -> AgentGraphTurnRequestDto:
        """构建 GraphRuntime 端口请求。

        :param command: 单轮 Agent 执行命令。
        :param context: 已绑定消息与配置版本的执行上下文。
        :return: GraphRuntime 可消费的结构化运行请求。
        """

        return AgentGraphTurnRequestDto(
            context=context,
            input=list(command.input),
            attachments=list(command.attachments),
            metadata=dict(command.metadata),
            model_hint=command.model_hint,
            execution_options=command.execution_options,
            publish_capabilities=command.publish_capabilities,
        )

    def _build_graph_error(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        operation: AgentApplicationOperation,
        turn_id: str,
        run_id: str,
        trace_status: AgentTraceDeliveryStatus,
        error: AgentGraphRuntimeUnavailableError,
    ) -> AgentApplicationServiceError:
        """构建 GraphRuntime 不可用对应的应用层错误。

        :param command: 单轮 Agent 执行命令。
        :param operation: 当前应用操作。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param trace_status: 当前逻辑链交付状态。
        :param error: GraphRuntime 不可用异常。
        :return: 可向上游抛出的应用层错误。
        """

        return AgentApplicationServiceError(
            code=AgentApplicationErrorCode.GRAPH_RUNTIME_UNAVAILABLE,
            operation=operation,
            phase=AgentApplicationPhase.GRAPH_EXECUTING,
            message=str(error),
            request_id=command.request_context.request_id,
            trace_id=command.request_context.trace_id,
            turn_id=turn_id,
            run_id=run_id,
            dependency="GraphRuntime",
            dependency_error_code=error.code,
            trace_delivery_status=trace_status,
        )

    async def _finalize_trace_safely(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        turn_id: str,
        run_id: str,
        final_status: AgentTraceFinalStatus,
        user_message_id: str | None,
        error_code: str | None,
        summary: dict[str, object],
        fallback_status: AgentTraceDeliveryStatus,
    ) -> AgentTraceDeliveryStatus:
        """尽力完成逻辑链且不覆盖主业务结果。

        :param command: 单轮 Agent 执行命令。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param final_status: 逻辑链最终状态。
        :param user_message_id: 可选用户消息 ID。
        :param error_code: 可选应用层错误码。
        :param summary: 最终安全摘要。
        :param fallback_status: Trace 完成失败时使用的既有状态。
        :return: Trace 完成后的交付状态，异常时返回降级状态。
        """

        try:
            result = await self._logic_trace_store.finalize_trace(
                AgentTraceFinalizeCommandDto(
                    request_id=command.request_context.request_id,
                    trace_id=command.request_context.trace_id,
                    turn_id=turn_id,
                    run_id=run_id,
                    final_status=final_status,
                    user_message_id=user_message_id,
                    error_code=error_code,
                    summary=summary,
                )
            )
            return result.status
        except Exception as exc:
            if self._observability_provider is not None:
                self._observability_provider.record_error(
                    component=_COMPONENT_NAME,
                    error_type=type(exc).__name__,
                    error_message="LogicTraceStore 完成逻辑链失败",
                    safe_fields={
                        "final_status": final_status.value,
                        "fallback_status": fallback_status.value,
                    },
                )
            return AgentTraceDeliveryStatus.DEGRADED

    async def _finalize_trace_after_error(
        self,
        *,
        command: AgentTurnRequestCommandDto,
        turn_id: str,
        run_id: str,
        user_message_id: str | None,
        error: AgentApplicationServiceError,
        fallback_status: AgentTraceDeliveryStatus,
    ) -> None:
        """在应用执行失败后尽力完成逻辑链。

        :param command: 单轮 Agent 执行命令。
        :param turn_id: 稳定 turn ID。
        :param run_id: 稳定图运行 ID。
        :param user_message_id: 可选用户消息 ID。
        :param error: 当前应用层错误。
        :param fallback_status: Trace 完成失败时使用的既有状态。
        :return: None。
        """

        await self._finalize_trace_safely(
            command=command,
            turn_id=turn_id,
            run_id=run_id,
            final_status=AgentTraceFinalStatus.FAILED,
            user_message_id=user_message_id,
            error_code=error.code.value,
            summary={
                "phase": error.to_dto().phase.value,
                "dependency": error.to_dto().dependency,
            },
            fallback_status=fallback_status,
        )

    def _record_completion(
        self,
        *,
        result: AgentTurnResultDto,
        duration_seconds: float,
    ) -> None:
        """记录应用服务成功指标与结构化事件。

        :param result: 完整单轮 Agent 执行结果。
        :param duration_seconds: 本轮应用编排耗时，单位为秒。
        :return: None。
        """

        if self._observability_provider is None:
            return
        self._observability_provider.record_metric(
            metric_name="agent_application_turn_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"status": result.status.value},
            description="AgentApplicationService 单轮执行总数。",
        )
        self._observability_provider.record_metric(
            metric_name="agent_application_turn_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels={"status": result.status.value},
            description="AgentApplicationService 单轮执行耗时，单位为秒。",
        )
        self._observability_provider.record_event(
            event_name="agent_application.turn.completed",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO,
            safe_fields={
                "segment_count": len(result.segments),
                "trace_delivery_status": result.trace_delivery_status.value,
            },
        )

    def _record_failure(
        self,
        *,
        error: AgentApplicationServiceError,
        duration_seconds: float,
    ) -> None:
        """记录应用服务失败指标与结构化事件。

        :param error: 当前应用层错误。
        :param duration_seconds: 本轮应用编排耗时，单位为秒。
        :return: None。
        """

        if self._observability_provider is None:
            return
        error_dto = error.to_dto()
        self._observability_provider.record_metric(
            metric_name="agent_application_turn_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"status": AgentTurnStatus.FAILED.value},
            description="AgentApplicationService 单轮执行总数。",
        )
        self._observability_provider.record_metric(
            metric_name="agent_application_turn_duration_seconds",
            value=duration_seconds,
            metric_type=MetricType.HISTOGRAM,
            labels={"status": AgentTurnStatus.FAILED.value},
            description="AgentApplicationService 单轮执行耗时，单位为秒。",
        )
        self._observability_provider.record_event(
            event_name="agent_application.turn.failed",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.ERROR,
            error_type=error_dto.code.value,
            safe_fields={
                "phase": error_dto.phase.value,
                "dependency": error_dto.dependency,
                "retryable": error_dto.retryable,
            },
        )


__all__: tuple[str, ...] = (
    "AgentApplicationService",
    "DefaultAgentApplicationService",
)
