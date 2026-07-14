##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/runtime.py
# 作用: 实现基于 LangGraph 执行内核的 GraphRuntime facade，承接项目契约、运行锁、事件流和结果映射。
# 边界: 不自研图调度、不手工写 LangGraph checkpoint、不实现 L2 兽医业务节点或 HTTP/SSE 协议细节。
##################################################################################################

import asyncio
from collections.abc import AsyncIterator, Mapping
from threading import RLock
from time import perf_counter
from typing import Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import RunControl

from veterinary_agent.agent_application_service import (
    AgentCancelTurnCommandDto,
    AgentCancelTurnResultDto,
    AgentGraphEventDto,
    AgentGraphRuntimeUnavailableError,
    AgentGraphTurnRequestDto,
    AgentGraphTurnResultDto,
    AgentResponseSegmentDto,
    AgentResumeTurnCommandDto,
    AgentVetResultDto,
)
from veterinary_agent.checkpoint_store import (
    CheckpointStore,
    CheckpointStoreError,
)
from veterinary_agent.graph_runtime.control_plane import GraphRunControlPlane
from veterinary_agent.graph_runtime.definition import GraphDefinition
from veterinary_agent.graph_runtime.dto import (
    GraphResumeRef,
    GraphRunControlContext,
    GraphRunIdentity,
    GraphRuntimeSettings,
    JsonMap,
    parse_graph_checkpoint_ref,
)
from veterinary_agent.graph_runtime.enums import (
    GraphRuntimeErrorCode,
    GraphRuntimeEventType,
)
from veterinary_agent.graph_runtime.errors import (
    GraphRuntimeCancelledError,
    GraphRuntimeError,
)
from veterinary_agent.graph_runtime.event_adapter import GraphEventAdapter
from veterinary_agent.graph_runtime.events import GraphEventFactory
from veterinary_agent.graph_runtime.langgraph_backend import (
    CompiledGraph,
    LangGraphCompiler,
    LangGraphExecutionEngine,
    LangGraphRunContext,
    LangGraphRuntimeState,
    LangGraphStreamEvent,
)
from veterinary_agent.graph_runtime.registry import GraphRegistry
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)

_COMPONENT_NAME = "GraphRuntime"


def _as_json_map(value: object) -> JsonMap:
    """将未知映射值转换为字符串键映射。

    :param value: 需要转换的未知值。
    :return: 字符串键映射；非映射输入返回空映射。
    """

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _read_string(value: object) -> str | None:
    """从未知值读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 非空字符串；其他输入返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value
    return None


class DefaultGraphRuntime:
    """基于 LangGraph 的 GraphRuntime 默认实现。"""

    def __init__(
        self,
        *,
        checkpoint_store: CheckpointStore | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        graph_registry: GraphRegistry | None = None,
        settings: GraphRuntimeSettings | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 GraphRuntime 默认实现。

        :param checkpoint_store: 项目控制面强依赖，用于 thread、运行锁和 segment 幂等。
        :param checkpointer: LangGraph 图状态唯一权威 checkpointer。
        :param graph_registry: 可选图定义注册表；缺失时使用默认兽医 TODO 图注册表。
        :param settings: 可选 GraphRuntime 运行设置。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._checkpoint_store = checkpoint_store
        self._checkpointer = checkpointer
        self._settings = settings if settings is not None else GraphRuntimeSettings()
        self._graph_registry = (
            graph_registry if graph_registry is not None else GraphRegistry()
        )
        self._observability_provider = observability_provider
        self._engine: LangGraphExecutionEngine | None = None
        if self._checkpointer is not None:
            compiler = LangGraphCompiler(
                checkpointer=self._checkpointer,
                settings=self._settings,
            )
            self._graph_registry.compile_all(compiler)
            self._engine = LangGraphExecutionEngine(
                checkpointer=self._checkpointer,
                durability=cast(
                    Literal["sync", "async", "exit"],
                    self._settings.durability,
                ),
            )
        self._active_runs: dict[str, RunControl] = {}
        self._active_runs_lock = RLock()

    def is_ready(self) -> bool:
        """判断 GraphRuntime 是否具备执行条件。

        :return: 当项目控制面、LangGraph checkpointer 和默认图版本均已就绪时返回 True。
        """

        return (
            self._checkpoint_store is not None
            and self._checkpointer is not None
            and self._engine is not None
            and self._graph_registry.has_graph(
                graph_id=self._settings.graph_id,
                graph_version=self._settings.graph_version,
                require_compiled=True,
            )
        )

    async def execute_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AgentGraphTurnResultDto:
        """同步执行一轮业务图。

        :param request: 已绑定应用执行上下文的图运行请求。
        :return: GraphRuntime 最终结果。
        :raises AgentGraphRuntimeUnavailableError: 当运行时尚未就绪时抛出。
        :raises GraphRuntimeError: 当图执行失败时抛出。
        """

        final_result: AgentGraphTurnResultDto | None = None
        async for event in self._stream_turn_iterator(request):
            if event.event_type == GraphRuntimeEventType.RUN_COMPLETED.value:
                raw_result = event.data.get("result")
                if isinstance(raw_result, dict):
                    final_result = AgentGraphTurnResultDto.model_validate(raw_result)
        if final_result is not None:
            return final_result
        raise GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_STATE_INVALID,
            message="GraphRuntime 事件流缺少 run_completed result",
            request_id=request.context.request_id,
            trace_id=request.context.trace_id,
            run_id=request.context.run_id,
            graph_id=self._settings.graph_id,
            graph_version=self._settings.graph_version,
            retryable=False,
        )

    def stream_turn(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """流式执行一轮业务图。

        :param request: 已绑定应用执行上下文的图运行请求。
        :return: GraphRuntime 协议无关事件异步迭代器。
        """

        return self._stream_turn_iterator(request)

    def resume_turn(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """恢复一轮未完成业务图。

        :param command: 恢复运行命令。
        :return: GraphRuntime 恢复事件异步迭代器。
        """

        return self._resume_turn_iterator(command)

    async def cancel_turn(
        self,
        command: AgentCancelTurnCommandDto,
    ) -> AgentCancelTurnResultDto:
        """请求取消正在执行的业务图。

        :param command: 取消运行命令。
        :return: GraphRuntime 取消请求处理结果。
        :raises AgentGraphRuntimeUnavailableError: 当运行时尚未就绪时抛出。
        """

        self._ensure_ready()
        with self._active_runs_lock:
            control = self._active_runs.get(command.run_id)
        if control is None:
            return AgentCancelTurnResultDto(
                run_id=command.run_id,
                cancelled=False,
                idempotent=True,
            )
        control.request_drain(command.reason)
        return AgentCancelTurnResultDto(
            run_id=command.run_id,
            cancelled=True,
            idempotent=False,
        )

    async def _stream_turn_iterator(
        self,
        request: AgentGraphTurnRequestDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """执行新图运行并逐条产出项目标准事件。

        :param request: GraphRuntime 单轮执行请求。
        :return: GraphRuntime 事件异步迭代器。
        :raises AgentGraphRuntimeUnavailableError: 当运行时未就绪时抛出。
        :raises GraphRuntimeError: 当图运行失败时抛出。
        """

        self._ensure_ready()
        definition = self._resolve_graph_definition()
        graph = self._resolve_compiled_graph(definition=definition)
        control_plane = self._build_control_plane()
        identity = self._build_run_identity(request=request, definition=definition)
        event_factory = GraphEventFactory(identity)
        event_adapter = GraphEventAdapter(
            definition=definition,
            event_factory=event_factory,
            emit_node_events=self._settings.emit_node_events,
        )
        started_monotonic = perf_counter()
        run_control = self._register_active_run(request.context.run_id)
        control_context: GraphRunControlContext | None = None
        try:
            control_context = await control_plane.prepare_new_run(request)
            await control_plane.acquire_run_lock(control_context)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_STARTED,
                data={
                    "thread_id": control_context.thread_id,
                    "entry_node": definition.entry_node,
                    "execution_engine": "langgraph",
                },
            )
            async with asyncio.timeout(self._settings.run_deadline_seconds):
                async for event in self._stream_langgraph_events(
                    graph=graph,
                    definition=definition,
                    identity=identity,
                    event_adapter=event_adapter,
                    control_plane=control_plane,
                    control_context=control_context,
                    run_control=run_control,
                    source_events=self._require_engine().stream_new_run(
                        graph=graph,
                        state=self._build_initial_state(
                            request=request,
                            identity=identity,
                        ),
                        context=self._build_langgraph_context(
                            request=request,
                            identity=identity,
                            thread_id=control_context.thread_id,
                        ),
                        thread_id=control_context.thread_id,
                        metadata=self._build_langgraph_metadata(identity=identity),
                        run_control=run_control,
                    ),
                ):
                    yield event
            final_state = await self._require_engine().read_state(
                graph=graph,
                resume_ref=GraphResumeRef(thread_id=control_context.thread_id),
            )
            result = self._build_result_from_state(
                state=final_state,
                identity=identity,
            )
            duration_ms = int((perf_counter() - started_monotonic) * 1000)
            self._record_run_metric(status="completed", duration_ms=duration_ms)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_COMPLETED,
                data={
                    "duration_ms": duration_ms,
                    "output_text": result.output_text,
                    "segment_count": len(result.segments),
                    "result": result.model_dump(mode="json"),
                },
            )
        except TimeoutError as exc:
            graph_error = self._build_timeout_error(
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                run_id=request.context.run_id,
                definition=definition,
                started_monotonic=started_monotonic,
            )
            self._record_graph_error(graph_error)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_FAILED,
                data={"error": graph_error.to_safe_fields()},
            )
            self._record_run_metric(status="failed", duration_ms=None)
            raise graph_error from exc
        except GraphRuntimeCancelledError as exc:
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_CANCELLED,
                data={"error": exc.to_safe_fields()},
            )
            self._record_run_metric(status="cancelled", duration_ms=None)
            raise
        except Exception as exc:
            graph_error = self._coerce_graph_error(
                error=exc,
                request_id=request.context.request_id,
                trace_id=request.context.trace_id,
                run_id=request.context.run_id,
                definition=definition,
            )
            self._record_graph_error(graph_error)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_FAILED,
                data={"error": graph_error.to_safe_fields()},
            )
            self._record_run_metric(status="failed", duration_ms=None)
            raise graph_error from exc
        finally:
            if control_context is not None:
                await self._release_run_lock_safely(
                    control_plane=control_plane,
                    control_context=control_context,
                )
            self._unregister_active_run(request.context.run_id)

    async def _resume_turn_iterator(
        self,
        command: AgentResumeTurnCommandDto,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """基于 LangGraph checkpoint 恢复失败或暂停的图运行。

        :param command: 恢复运行命令。
        :return: GraphRuntime 恢复事件异步迭代器。
        :raises AgentGraphRuntimeUnavailableError: 当运行时未就绪或恢复引用非法时抛出。
        """

        self._ensure_ready()
        if command.checkpoint_ref is None:
            raise AgentGraphRuntimeUnavailableError(
                "GraphRuntime 恢复运行需要 checkpoint_ref"
            )
        try:
            resume_ref = parse_graph_checkpoint_ref(command.checkpoint_ref)
            descriptor = await self._require_engine().inspect_checkpoint(resume_ref)
        except ValueError as exc:
            raise AgentGraphRuntimeUnavailableError(str(exc)) from exc
        definition = self._graph_registry.get_definition(
            graph_id=descriptor.graph_id,
            graph_version=descriptor.graph_version,
        )
        graph = self._resolve_compiled_graph(definition=definition)
        identity = self._build_resume_identity(command=command, state=descriptor.values)
        event_factory = GraphEventFactory(identity)
        event_adapter = GraphEventAdapter(
            definition=definition,
            event_factory=event_factory,
            emit_node_events=self._settings.emit_node_events,
        )
        control_plane = self._build_control_plane()
        control_context = self._build_resume_control_context(
            command=command,
            resume_ref=resume_ref,
            state=descriptor.values,
        )
        run_control = self._register_active_run(command.run_id)
        started_monotonic = perf_counter()
        try:
            await control_plane.acquire_run_lock(control_context)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RESUME_STARTED,
                data={"checkpoint_ref": command.checkpoint_ref},
            )
            yield event_factory.create(
                event_type=GraphRuntimeEventType.CHECKPOINT_LOADED,
                data={
                    "thread_id": descriptor.thread_id,
                    "checkpoint_id": descriptor.checkpoint_id,
                    "graph_id": descriptor.graph_id,
                    "graph_version": descriptor.graph_version,
                    "state_schema_version": descriptor.state_schema_version,
                },
            )
            async with asyncio.timeout(self._settings.run_deadline_seconds):
                async for event in self._stream_langgraph_events(
                    graph=graph,
                    definition=definition,
                    identity=identity,
                    event_adapter=event_adapter,
                    control_plane=control_plane,
                    control_context=control_context,
                    run_control=run_control,
                    source_events=self._require_engine().stream_resume(
                        graph=graph,
                        resume_ref=resume_ref,
                        context=self._build_resume_langgraph_context(
                            identity=identity,
                            state=descriptor.values,
                            thread_id=resume_ref.thread_id,
                        ),
                        metadata=self._build_langgraph_metadata(identity=identity),
                        run_control=run_control,
                    ),
                ):
                    yield event
            final_state = await self._require_engine().read_state(
                graph=graph,
                resume_ref=GraphResumeRef(thread_id=resume_ref.thread_id),
            )
            result = self._build_result_from_state(
                state=final_state,
                identity=identity,
            )
            duration_ms = int((perf_counter() - started_monotonic) * 1000)
            self._record_run_metric(status="completed", duration_ms=duration_ms)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_COMPLETED,
                data={
                    "duration_ms": duration_ms,
                    "output_text": result.output_text,
                    "segment_count": len(result.segments),
                    "result": result.model_dump(mode="json"),
                },
            )
        except TimeoutError as exc:
            graph_error = self._build_timeout_error(
                request_id=command.request_id,
                trace_id=command.trace_id,
                run_id=command.run_id,
                definition=definition,
                started_monotonic=started_monotonic,
            )
            self._record_graph_error(graph_error)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_FAILED,
                data={"error": graph_error.to_safe_fields()},
            )
            raise graph_error from exc
        except Exception as exc:
            graph_error = self._coerce_graph_error(
                error=exc,
                request_id=command.request_id,
                trace_id=command.trace_id,
                run_id=command.run_id,
                definition=definition,
            )
            self._record_graph_error(graph_error)
            yield event_factory.create(
                event_type=GraphRuntimeEventType.RUN_FAILED,
                data={"error": graph_error.to_safe_fields()},
            )
            raise graph_error from exc
        finally:
            await self._release_run_lock_safely(
                control_plane=control_plane,
                control_context=control_context,
            )
            self._unregister_active_run(command.run_id)

    async def _stream_langgraph_events(
        self,
        *,
        graph: CompiledGraph,
        definition: GraphDefinition,
        identity: GraphRunIdentity,
        event_adapter: GraphEventAdapter,
        control_plane: GraphRunControlPlane,
        control_context: GraphRunControlContext,
        run_control: RunControl,
        source_events: AsyncIterator[LangGraphStreamEvent],
    ) -> AsyncIterator[AgentGraphEventDto]:
        """适配 LangGraph 原生事件并处理 segment 发布幂等。

        :param graph: 当前已编译的 LangGraph。
        :param definition: 当前版本化图定义。
        :param identity: 当前图运行身份。
        :param event_adapter: LangGraph 到项目事件的适配器。
        :param control_plane: 项目控制面协调器。
        :param control_context: 当前运行控制面上下文。
        :param run_control: LangGraph 运行控制对象。
        :param source_events: LangGraph 后端原生事件流。
        :return: 项目标准事件异步迭代器。
        :raises GraphRuntimeCancelledError: 当调用方请求取消且 LangGraph 已完成 drain 时抛出。
        """

        del graph
        published_in_stream: set[str] = set()
        async for backend_event in source_events:
            for event in event_adapter.adapt(backend_event):
                yield event
            async for segment_event in self._publish_segments_from_event(
                backend_event=backend_event,
                event_factory=event_adapter.event_factory,
                control_plane=control_plane,
                control_context=control_context,
                published_in_stream=published_in_stream,
            ):
                yield segment_event
            if run_control.drain_requested:
                raise GraphRuntimeCancelledError(
                    request_id=identity.request_id,
                    trace_id=identity.trace_id,
                    run_id=identity.run_id,
                    graph_id=definition.graph_id,
                    graph_version=definition.graph_version,
                )

    async def _publish_segments_from_event(
        self,
        *,
        backend_event: LangGraphStreamEvent,
        event_factory: GraphEventFactory,
        control_plane: GraphRunControlPlane,
        control_context: GraphRunControlContext,
        published_in_stream: set[str],
    ) -> AsyncIterator[AgentGraphEventDto]:
        """从 LangGraph updates 事件中发布节点产出的待发布 segment。

        :param backend_event: LangGraph 后端事件。
        :param event_factory: 当前运行事件工厂。
        :param control_plane: 项目控制面协调器。
        :param control_context: 当前运行控制面上下文。
        :param published_in_stream: 当前 stream 已处理过的 segment ID 集合。
        :return: segment ready/completed/published 项目事件异步迭代器。
        """

        if backend_event.event_type != "updates":
            return
        for node_id, raw_update in backend_event.data.items():
            update = _as_json_map(raw_update)
            business_state = _as_json_map(update.get("business_state"))
            raw_segments = business_state.get("segments_to_publish")
            if not isinstance(raw_segments, list | tuple):
                continue
            for raw_segment in raw_segments:
                try:
                    segment = AgentResponseSegmentDto.model_validate(raw_segment)
                except ValueError:
                    continue
                if segment.segment_id in published_in_stream:
                    continue
                published_in_stream.add(segment.segment_id)
                async for event in self._publish_one_segment(
                    event_factory=event_factory,
                    control_plane=control_plane,
                    control_context=control_context,
                    segment=segment,
                    node_id=node_id,
                ):
                    yield event

    async def _publish_one_segment(
        self,
        *,
        event_factory: GraphEventFactory,
        control_plane: GraphRunControlPlane,
        control_context: GraphRunControlContext,
        segment: AgentResponseSegmentDto,
        node_id: str,
    ) -> AsyncIterator[AgentGraphEventDto]:
        """发布单个 segment 并记录项目控制面幂等状态。

        :param event_factory: 当前运行事件工厂。
        :param control_plane: 项目控制面协调器。
        :param control_context: 当前运行控制面上下文。
        :param segment: 需要发布的用户可见 segment。
        :param node_id: 产出该 segment 的节点 ID。
        :return: segment ready、completed 与 published 事件。
        """

        segment_data = segment.model_dump(mode="json")
        yield event_factory.create(
            event_type=GraphRuntimeEventType.SEGMENT_READY,
            node_id=node_id,
            data={"segment_id": segment.segment_id, "segment": segment_data},
        )
        yield event_factory.create(
            event_type=GraphRuntimeEventType.SEGMENT_COMPLETED,
            node_id=node_id,
            data={"segment_id": segment.segment_id, "segment": segment_data},
        )
        published = await control_plane.mark_segment_published(
            context=control_context,
            segment_id=segment.segment_id,
            task_id=self._read_segment_task_id(segment),
            metadata={
                "node_id": node_id,
                "segment_type": segment.type,
                "status": segment.status,
            },
        )
        yield event_factory.create(
            event_type=GraphRuntimeEventType.SEGMENT_PUBLISHED,
            node_id=node_id,
            data={
                "segment_id": segment.segment_id,
                "publish_state": published.model_dump(mode="json"),
            },
        )

    def _ensure_ready(self) -> None:
        """确认 GraphRuntime 当前可接受运行请求。

        :return: None。
        :raises AgentGraphRuntimeUnavailableError: 当运行时未就绪时抛出。
        """

        if self.is_ready():
            return
        raise AgentGraphRuntimeUnavailableError(
            "GraphRuntime 尚未完成 LangGraph 依赖装配"
        )

    def _require_engine(self) -> LangGraphExecutionEngine:
        """读取已就绪的 LangGraph 执行后端。

        :return: LangGraph 执行后端。
        :raises AgentGraphRuntimeUnavailableError: 当执行后端尚未装配时抛出。
        """

        if self._engine is not None:
            return self._engine
        raise AgentGraphRuntimeUnavailableError("GraphRuntime 缺少 LangGraph 执行后端")

    def _resolve_graph_definition(self) -> GraphDefinition:
        """解析当前默认图定义。

        :return: 当前默认图定义。
        :raises GraphRuntimeError: 当默认图定义不存在时抛出。
        """

        return self._graph_registry.get_definition(
            graph_id=self._settings.graph_id,
            graph_version=self._settings.graph_version,
        )

    def _resolve_compiled_graph(self, *, definition: GraphDefinition) -> CompiledGraph:
        """读取当前图定义对应的 compiled graph。

        :param definition: 当前版本化图定义。
        :return: 已编译的 LangGraph。
        """

        return self._graph_registry.get_compiled(
            graph_id=definition.graph_id,
            graph_version=definition.graph_version,
        )

    def _build_control_plane(self) -> GraphRunControlPlane:
        """构建项目控制面协调器。

        :return: 项目控制面协调器。
        :raises AgentGraphRuntimeUnavailableError: 当 CheckpointStore 未装配时抛出。
        """

        if self._checkpoint_store is None:
            raise AgentGraphRuntimeUnavailableError("GraphRuntime 缺少 CheckpointStore")
        return GraphRunControlPlane(
            checkpoint_store=self._checkpoint_store,
            settings=self._settings,
        )

    def _build_run_identity(
        self,
        *,
        request: AgentGraphTurnRequestDto,
        definition: GraphDefinition,
    ) -> GraphRunIdentity:
        """构建新运行身份上下文。

        :param request: GraphRuntime 单轮执行请求。
        :param definition: 当前图定义。
        :return: 图运行身份上下文。
        """

        context = request.context
        return GraphRunIdentity(
            request_id=context.request_id,
            trace_id=context.trace_id,
            run_id=context.run_id,
            graph_id=definition.graph_id,
            graph_version=definition.graph_version,
            state_schema_version=definition.state_schema_version,
            params_version=context.params_version,
            config_snapshot_id=context.config_snapshot_id,
        )

    def _build_resume_identity(
        self,
        *,
        command: AgentResumeTurnCommandDto,
        state: LangGraphRuntimeState,
    ) -> GraphRunIdentity:
        """基于 checkpoint 状态和恢复命令构建恢复事件身份。

        :param command: 恢复运行命令。
        :param state: checkpoint 中读取的 LangGraph 状态。
        :return: 恢复阶段使用的图运行身份。
        """

        checkpoint_identity = _as_json_map(state.get("identity"))
        return GraphRunIdentity(
            request_id=command.request_id,
            trace_id=command.trace_id,
            run_id=command.run_id,
            graph_id=str(checkpoint_identity.get("graph_id", self._settings.graph_id)),
            graph_version=str(
                checkpoint_identity.get("graph_version", self._settings.graph_version)
            ),
            state_schema_version=str(
                checkpoint_identity.get("state_schema_version", "unknown")
            ),
            params_version=str(checkpoint_identity.get("params_version", "unknown")),
            config_snapshot_id=str(
                checkpoint_identity.get("config_snapshot_id", "unknown")
            ),
        )

    def _build_initial_state(
        self,
        *,
        request: AgentGraphTurnRequestDto,
        identity: GraphRunIdentity,
    ) -> LangGraphRuntimeState:
        """构建 LangGraph 新运行初始状态。

        :param request: GraphRuntime 单轮执行请求。
        :param identity: 当前运行身份。
        :return: 可传递给 LangGraph 的类型化初始状态。
        """

        request_context = request.context
        request_payload: JsonMap = {
            "request_id": request_context.request_id,
            "trace_id": request_context.trace_id,
            "turn_id": request_context.turn_id,
            "run_id": request_context.run_id,
            "session_id": request_context.session_id,
            "user_id": request_context.user_id,
            "current_pet_id": request_context.current_pet_id,
            "user_message_id": request_context.user_message_id,
            "idempotency_key": request_context.idempotency_key,
            "params_version": request_context.params_version,
            "config_snapshot_id": request_context.config_snapshot_id,
            "response_mode": request_context.response_mode,
            "route_kind": request_context.route_kind,
            "input": [item.model_dump(mode="json") for item in request.input],
            "attachments": [
                attachment.model_dump(mode="json") for attachment in request.attachments
            ],
            "metadata": dict(request.metadata),
            "model_hint": request.model_hint,
        }
        return {
            "request": request_payload,
            "identity": {
                "request_id": identity.request_id,
                "trace_id": identity.trace_id,
                "run_id": identity.run_id,
                "graph_id": identity.graph_id,
                "graph_version": identity.graph_version,
                "state_schema_version": identity.state_schema_version,
                "params_version": identity.params_version,
                "config_snapshot_id": identity.config_snapshot_id,
            },
            "business_state": {
                "input_count": len(request.input),
                "attachment_count": len(request.attachments),
            },
            "node_outputs": {},
            "completed_nodes": (),
            "node_events": (),
            "selected_routes": {},
        }

    def _build_langgraph_context(
        self,
        *,
        request: AgentGraphTurnRequestDto,
        identity: GraphRunIdentity,
        thread_id: str,
    ) -> LangGraphRunContext:
        """构建新运行传入 LangGraph Runtime 的上下文。

        :param request: GraphRuntime 单轮执行请求。
        :param identity: 当前运行身份。
        :param thread_id: 当前运行绑定的 checkpoint thread ID。
        :return: LangGraph 运行期上下文。
        """

        return LangGraphRunContext(
            identity=identity,
            session_id=request.context.session_id,
            user_id=request.context.user_id,
            current_pet_id=request.context.current_pet_id,
            thread_id=thread_id,
            request=self._build_initial_state(
                request=request,
                identity=identity,
            ).get("request", {}),
        )

    def _build_resume_langgraph_context(
        self,
        *,
        identity: GraphRunIdentity,
        state: LangGraphRuntimeState,
        thread_id: str,
    ) -> LangGraphRunContext:
        """构建恢复运行传入 LangGraph Runtime 的上下文。

        :param identity: 恢复阶段运行身份。
        :param state: checkpoint 中读取的 LangGraph 状态。
        :param thread_id: 当前恢复运行绑定的 checkpoint thread ID。
        :return: LangGraph 运行期上下文。
        """

        request = _as_json_map(state.get("request"))
        return LangGraphRunContext(
            identity=identity,
            session_id=self._require_state_string(request, "session_id"),
            user_id=self._require_state_string(request, "user_id"),
            current_pet_id=self._require_state_string(request, "current_pet_id"),
            request=request,
            thread_id=thread_id,
        )

    def _build_resume_control_context(
        self,
        *,
        command: AgentResumeTurnCommandDto,
        resume_ref: GraphResumeRef,
        state: LangGraphRuntimeState,
    ) -> GraphRunControlContext:
        """构建恢复运行使用的项目控制面上下文。

        :param command: 恢复运行命令。
        :param resume_ref: 已解析的恢复引用。
        :param state: checkpoint 中读取的 LangGraph 状态。
        :return: 可用于运行锁释放和 segment 幂等的控制面上下文。
        """

        request = _as_json_map(state.get("request"))
        return GraphRunControlContext(
            request_id=command.request_id,
            trace_id=command.trace_id,
            run_id=command.run_id,
            session_id=self._require_state_string(request, "session_id"),
            user_id=self._require_state_string(request, "user_id"),
            pet_id=self._require_state_string(request, "current_pet_id"),
            thread_id=resume_ref.thread_id,
        )

    def _build_langgraph_metadata(self, *, identity: GraphRunIdentity) -> JsonMap:
        """构建写入 LangGraph checkpoint metadata 的项目摘要。

        :param identity: 当前图运行身份。
        :return: 可写入 LangGraph checkpoint metadata 的项目摘要。
        """

        return {
            "graph_id": identity.graph_id,
            "graph_version": identity.graph_version,
            "state_schema_version": identity.state_schema_version,
            "run_id": identity.run_id,
            "request_id": identity.request_id,
            "trace_id": identity.trace_id,
            "params_version": identity.params_version,
            "config_snapshot_id": identity.config_snapshot_id,
        }

    def _build_result_from_state(
        self,
        *,
        state: LangGraphRuntimeState,
        identity: GraphRunIdentity,
    ) -> AgentGraphTurnResultDto:
        """从 LangGraph 最终状态构建 GraphRuntime 最终结果。

        :param state: LangGraph checkpoint 中的最终状态。
        :param identity: 当前图运行身份上下文。
        :return: GraphRuntime 最终结果。
        :raises GraphRuntimeError: 当最终状态缺少合法业务结果时抛出。
        """

        business_state = _as_json_map(state.get("business_state"))
        raw_result = business_state.get("result")
        if isinstance(raw_result, dict):
            return AgentGraphTurnResultDto.model_validate(raw_result)
        segments = self._read_segments(business_state.get("segments"))
        output_text = "\n\n".join(
            segment.output_text or "" for segment in segments if segment.output_text
        )
        if segments or output_text:
            return AgentGraphTurnResultDto(
                output_text=output_text,
                segments=segments,
                vet_result=AgentVetResultDto(
                    route="graph_runtime_state_fallback",
                    metadata={
                        "graph_id": identity.graph_id,
                        "graph_version": identity.graph_version,
                    },
                ),
                metadata={"graph_runtime_result_source": "state_segments"},
            )
        raise GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_STATE_INVALID,
            message="GraphRuntime 最终状态缺少合法 result",
            request_id=identity.request_id,
            trace_id=identity.trace_id,
            run_id=identity.run_id,
            graph_id=identity.graph_id,
            graph_version=identity.graph_version,
            retryable=False,
        )

    def _read_segments(self, value: object) -> list[AgentResponseSegmentDto]:
        """从未知值中读取用户可见 segment 列表。

        :param value: 需要读取的未知值。
        :return: segment DTO 列表。
        """

        if not isinstance(value, list | tuple):
            return []
        segments: list[AgentResponseSegmentDto] = []
        for item in value:
            try:
                segments.append(AgentResponseSegmentDto.model_validate(item))
            except ValueError:
                continue
        return segments

    def _read_segment_task_id(self, segment: AgentResponseSegmentDto) -> str | None:
        """从 segment metadata 中读取 task_id。

        :param segment: 用户可见 segment。
        :return: task_id；segment 未携带时返回 None。
        """

        metadata = segment.metadata
        if metadata is None:
            return None
        return _read_string(metadata.get("task_id"))

    def _require_state_string(self, mapping: JsonMap, key: str) -> str:
        """从状态映射中读取必填字符串字段。

        :param mapping: checkpoint 中读取的状态映射。
        :param key: 需要读取的字段名。
        :return: 非空字符串字段值。
        :raises AgentGraphRuntimeUnavailableError: 当字段缺失或类型非法时抛出。
        """

        value = _read_string(mapping.get(key))
        if value is not None:
            return value
        raise AgentGraphRuntimeUnavailableError(f"GraphRuntime 恢复状态缺少 {key}")

    def _register_active_run(self, run_id: str) -> RunControl:
        """注册当前进程内活跃运行。

        :param run_id: 图运行 ID。
        :return: 当前运行的 LangGraph 控制对象。
        :raises AgentGraphRuntimeUnavailableError: 当同一 run_id 已在执行时抛出。
        """

        with self._active_runs_lock:
            if run_id in self._active_runs:
                raise AgentGraphRuntimeUnavailableError("同一 run_id 已在执行中")
            run_control = RunControl()
            self._active_runs[run_id] = run_control
            return run_control

    def _unregister_active_run(self, run_id: str) -> None:
        """注销当前进程内活跃运行。

        :param run_id: 图运行 ID。
        :return: None。
        """

        with self._active_runs_lock:
            self._active_runs.pop(run_id, None)

    def _build_timeout_error(
        self,
        *,
        request_id: str,
        trace_id: str,
        run_id: str,
        definition: GraphDefinition,
        started_monotonic: float,
    ) -> GraphRuntimeError:
        """构建整轮运行超时错误。

        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param run_id: 图运行 ID。
        :param definition: 当前图定义。
        :param started_monotonic: 当前图运行开始时的单调时钟值。
        :return: GraphRuntime 运行超时异常。
        """

        elapsed_seconds = perf_counter() - started_monotonic
        return GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_RUN_TIMEOUT,
            message="GraphRuntime 图运行超过总 deadline",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            graph_id=definition.graph_id,
            graph_version=definition.graph_version,
            retryable=True,
            details={
                "elapsed_seconds": elapsed_seconds,
                "run_deadline_seconds": self._settings.run_deadline_seconds,
            },
        )

    async def _release_run_lock_safely(
        self,
        *,
        control_plane: GraphRunControlPlane,
        control_context: GraphRunControlContext,
    ) -> None:
        """尽力释放项目运行锁，避免覆盖主流程异常。

        :param control_plane: 项目控制面协调器。
        :param control_context: 当前运行控制面上下文。
        :return: None。
        """

        try:
            await control_plane.release_run_lock(control_context)
        except Exception as exc:
            self._record_graph_error(
                GraphRuntimeError(
                    code=GraphRuntimeErrorCode.GRAPH_RUN_LOCK_FAILED,
                    message="GraphRuntime 释放运行锁失败",
                    request_id=control_context.request_id,
                    trace_id=control_context.trace_id,
                    run_id=control_context.run_id,
                    retryable=True,
                    details={"error_type": type(exc).__name__},
                )
            )

    def _coerce_graph_error(
        self,
        *,
        error: Exception,
        request_id: str,
        trace_id: str,
        run_id: str,
        definition: GraphDefinition,
    ) -> GraphRuntimeError:
        """将未知异常转换为 GraphRuntime 领域异常。

        :param error: 捕获到的异常。
        :param request_id: 入口请求 ID。
        :param trace_id: 全链路追踪 ID。
        :param run_id: 图运行 ID。
        :param definition: 当前图定义。
        :return: GraphRuntime 领域异常。
        """

        if isinstance(error, GraphRuntimeError):
            return error
        if isinstance(error, CheckpointStoreError):
            return GraphRuntimeError(
                code=GraphRuntimeErrorCode.GRAPH_CHECKPOINT_UNAVAILABLE,
                message="GraphRuntime 项目控制面依赖失败",
                request_id=request_id,
                trace_id=trace_id,
                run_id=run_id,
                graph_id=definition.graph_id,
                graph_version=definition.graph_version,
                retryable=error.retryable,
                details={"dependency_error_code": error.code.value},
            )
        return GraphRuntimeError(
            code=GraphRuntimeErrorCode.GRAPH_NODE_FAILED,
            message="GraphRuntime LangGraph 执行失败",
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            graph_id=definition.graph_id,
            graph_version=definition.graph_version,
            retryable=False,
            details={"error_type": type(error).__name__},
        )

    def _record_run_metric(
        self,
        *,
        status: str,
        duration_ms: int | None,
    ) -> None:
        """记录图运行指标。

        :param status: 图运行状态摘要。
        :param duration_ms: 可选图运行耗时，单位毫秒。
        :return: None。
        """

        if self._observability_provider is None:
            return
        self._observability_provider.record_metric(
            metric_name="graph_run_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"component": _COMPONENT_NAME, "status": status},
            description="GraphRuntime 图运行总数。",
        )
        if duration_ms is not None:
            self._observability_provider.record_metric(
                metric_name="graph_run_duration_ms",
                value=float(duration_ms),
                metric_type=MetricType.HISTOGRAM,
                labels={"component": _COMPONENT_NAME, "status": status},
                description="GraphRuntime 图运行耗时，单位毫秒。",
            )

    def _record_graph_error(self, error: GraphRuntimeError) -> None:
        """记录 GraphRuntime 错误摘要。

        :param error: GraphRuntime 领域异常。
        :return: None。
        """

        if self._observability_provider is None:
            return
        self._observability_provider.record_event(
            event_name="graph_runtime.error",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.ERROR,
            safe_fields=error.to_safe_fields(),
            error_type=error.code.value,
        )


def create_default_graph_runtime(
    *,
    checkpoint_store: CheckpointStore,
    checkpointer: BaseCheckpointSaver[str],
    graph_registry: GraphRegistry | None = None,
    settings: GraphRuntimeSettings | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultGraphRuntime:
    """创建基于 LangGraph 的 GraphRuntime 默认实现。

    :param checkpoint_store: 项目控制面强依赖。
    :param checkpointer: LangGraph 图状态唯一权威 checkpointer。
    :param graph_registry: 可选图定义注册表；未传入时注册默认兽医 TODO 图。
    :param settings: 可选 GraphRuntime 运行设置。
    :param observability_provider: 可选 Observability provider。
    :return: GraphRuntime 默认实现。
    """

    return DefaultGraphRuntime(
        checkpoint_store=checkpoint_store,
        checkpointer=checkpointer,
        graph_registry=graph_registry,
        settings=settings,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultGraphRuntime",
    "create_default_graph_runtime",
)
