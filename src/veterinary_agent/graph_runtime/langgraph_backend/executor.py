##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/langgraph_backend/executor.py
# 作用: 封装 LangGraph compiled graph 的新运行、失败恢复、状态读取和多模式事件流。
# 边界: 不解释项目业务结果、不管理运行锁、不发布 segment、不构造应用层事件 DTO。
##################################################################################################

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import RunControl

from veterinary_agent.checkpoint_store import build_langgraph_thread_config
from veterinary_agent.graph_runtime.dto import GraphResumeRef, JsonMap
from veterinary_agent.graph_runtime.langgraph_backend.compiler import CompiledGraph
from veterinary_agent.graph_runtime.langgraph_backend.state import (
    LangGraphRunContext,
    LangGraphRuntimeState,
)

GraphStreamMode: TypeAlias = Literal["tasks", "updates", "checkpoints", "custom"]
GraphDurability: TypeAlias = Literal["sync", "async", "exit"]


@dataclass(frozen=True, slots=True)
class LangGraphStreamEvent:
    """GraphRuntime 后端消费的统一 LangGraph stream 事件。"""

    event_type: str
    namespace: tuple[str, ...]
    data: JsonMap


@dataclass(frozen=True, slots=True)
class LangGraphCheckpointDescriptor:
    """恢复前从原生 LangGraph checkpoint 读取的项目图描述。"""

    thread_id: str
    checkpoint_id: str
    graph_id: str
    graph_version: str
    state_schema_version: str
    values: LangGraphRuntimeState


def _as_json_map(value: object) -> JsonMap:
    """将未知映射值转换为 JSON 映射。

    :param value: LangGraph stream 返回的未知值。
    :return: 字符串键映射；非映射输入返回空映射。
    """

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalize_stream_event(raw_event: object) -> LangGraphStreamEvent:
    """规范化 LangGraph v2 stream 事件。

    :param raw_event: LangGraph ``astream`` 产生的原始事件。
    :return: GraphRuntime 后端统一 stream 事件。
    :raises ValueError: 当 LangGraph 返回不受支持的事件结构时抛出。
    """

    event_map = _as_json_map(raw_event)
    event_type = event_map.get("type")
    namespace = event_map.get("ns")
    data = event_map.get("data")
    if not isinstance(event_type, str):
        raise ValueError("LangGraph stream 事件缺少 type")
    normalized_namespace = (
        tuple(item for item in namespace if isinstance(item, str))
        if isinstance(namespace, tuple | list)
        else ()
    )
    return LangGraphStreamEvent(
        event_type=event_type,
        namespace=normalized_namespace,
        data=_as_json_map(data),
    )


class LangGraphExecutionEngine:
    """GraphRuntime 使用的 LangGraph 执行后端。"""

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver[str],
        durability: GraphDurability,
    ) -> None:
        """初始化 LangGraph 执行后端。

        :param checkpointer: 图状态唯一权威写入使用的 LangGraph checkpointer。
        :param durability: LangGraph checkpoint 持久化模式。
        :return: None。
        """

        self._checkpointer = checkpointer
        self._durability: GraphDurability = durability

    async def inspect_checkpoint(
        self,
        resume_ref: GraphResumeRef,
    ) -> LangGraphCheckpointDescriptor:
        """读取恢复引用对应的原生 LangGraph checkpoint 描述。

        :param resume_ref: LangGraph thread 与可选 checkpoint ID。
        :return: checkpoint 保存的图版本、状态 schema 版本和类型化状态。
        :raises ValueError: 当 checkpoint 不存在或缺少项目恢复元数据时抛出。
        """

        checkpoint_tuple = await self._checkpointer.aget_tuple(
            build_langgraph_thread_config(
                thread_id=resume_ref.thread_id,
                checkpoint_id=resume_ref.checkpoint_id,
            )
        )
        if checkpoint_tuple is None:
            raise ValueError("GraphRuntime 未找到可恢复的 LangGraph checkpoint")
        metadata = _as_json_map(checkpoint_tuple.metadata)
        graph_id = metadata.get("graph_id")
        graph_version = metadata.get("graph_version")
        state_schema_version = metadata.get("state_schema_version")
        configurable = _as_json_map(checkpoint_tuple.config.get("configurable"))
        checkpoint_id = configurable.get("checkpoint_id")
        checkpoint_values = _as_json_map(
            checkpoint_tuple.checkpoint.get("channel_values")
        )
        if not isinstance(graph_id, str) or not graph_id:
            raise ValueError("LangGraph checkpoint 缺少 graph_id")
        if not isinstance(graph_version, str) or not graph_version:
            raise ValueError("LangGraph checkpoint 缺少 graph_version")
        if not isinstance(state_schema_version, str) or not state_schema_version:
            raise ValueError("LangGraph checkpoint 缺少 state_schema_version")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise ValueError("LangGraph checkpoint 缺少 checkpoint_id")
        return LangGraphCheckpointDescriptor(
            thread_id=resume_ref.thread_id,
            checkpoint_id=checkpoint_id,
            graph_id=graph_id,
            graph_version=graph_version,
            state_schema_version=state_schema_version,
            values=cast(LangGraphRuntimeState, checkpoint_values),
        )

    def stream_new_run(
        self,
        *,
        graph: CompiledGraph,
        state: LangGraphRuntimeState,
        context: LangGraphRunContext,
        thread_id: str,
        metadata: JsonMap,
        run_control: RunControl,
    ) -> AsyncIterator[LangGraphStreamEvent]:
        """流式执行一次新的 LangGraph 运行。

        :param graph: 当前版本已编译的 LangGraph。
        :param state: 本次运行初始状态。
        :param context: 本次运行不可变上下文。
        :param thread_id: LangGraph checkpoint thread ID。
        :param metadata: 写入 LangGraph checkpoint metadata 的运行摘要。
        :param run_control: 本次 LangGraph 运行的协作式控制对象。
        :return: 规范化后的 LangGraph stream 事件异步迭代器。
        """

        config = self._build_config(thread_id=thread_id, metadata=metadata)
        return self._stream_graph(
            graph=graph,
            graph_input=state,
            config=config,
            context=context,
            run_control=run_control,
        )

    def stream_resume(
        self,
        *,
        graph: CompiledGraph,
        resume_ref: GraphResumeRef,
        context: LangGraphRunContext,
        metadata: JsonMap,
        run_control: RunControl,
    ) -> AsyncIterator[LangGraphStreamEvent]:
        """从 LangGraph checkpoint 恢复失败或暂停的图运行。

        :param graph: checkpoint 所属图版本的 compiled graph。
        :param resume_ref: LangGraph thread 与可选 checkpoint ID。
        :param context: 恢复请求重新装配的运行期上下文。
        :param metadata: 恢复阶段写入后续 checkpoint 的运行摘要。
        :param run_control: 本次恢复运行的协作式控制对象。
        :return: 规范化后的 LangGraph stream 事件异步迭代器。
        """

        config = self._build_config(
            thread_id=resume_ref.thread_id,
            checkpoint_id=resume_ref.checkpoint_id,
            metadata=metadata,
        )
        return self._stream_graph(
            graph=graph,
            graph_input=None,
            config=config,
            context=context,
            run_control=run_control,
        )

    async def read_state(
        self,
        *,
        graph: CompiledGraph,
        resume_ref: GraphResumeRef,
    ) -> LangGraphRuntimeState:
        """读取指定 LangGraph checkpoint 的类型化状态。

        :param graph: checkpoint 所属图版本的 compiled graph。
        :param resume_ref: LangGraph thread 与可选 checkpoint ID。
        :return: checkpoint 中保存的 GraphRuntime 类型化状态。
        """

        snapshot = await graph.aget_state(
            build_langgraph_thread_config(
                thread_id=resume_ref.thread_id,
                checkpoint_id=resume_ref.checkpoint_id,
            )
        )
        return cast(LangGraphRuntimeState, snapshot.values)

    async def _stream_graph(
        self,
        *,
        graph: CompiledGraph,
        graph_input: LangGraphRuntimeState | None,
        config: RunnableConfig,
        context: LangGraphRunContext,
        run_control: RunControl,
    ) -> AsyncIterator[LangGraphStreamEvent]:
        """调用 LangGraph 并逐条规范化多模式 stream 事件。

        :param graph: 当前版本已编译的 LangGraph。
        :param graph_input: 新运行初始状态；恢复运行时为 None。
        :param config: LangGraph thread 和 metadata 配置。
        :param context: 本次调用重新装配的运行期上下文。
        :param run_control: 本次运行的协作式控制对象。
        :return: 规范化后的 LangGraph stream 事件异步迭代器。
        """

        stream_modes: list[GraphStreamMode] = [
            "tasks",
            "updates",
            "checkpoints",
            "custom",
        ]
        async for raw_event in graph.astream(
            graph_input,
            config,
            context=context,
            stream_mode=stream_modes,
            durability=self._durability,
            control=run_control,
            version="v2",
            subgraphs=True,
        ):
            yield _normalize_stream_event(raw_event)

    def _build_config(
        self,
        *,
        thread_id: str,
        metadata: JsonMap,
        checkpoint_id: str | None = None,
    ) -> RunnableConfig:
        """构建 LangGraph 执行配置。

        :param thread_id: LangGraph checkpoint thread ID。
        :param metadata: 需要写入 checkpoint metadata 的运行摘要。
        :param checkpoint_id: 可选历史 checkpoint ID。
        :return: 带 thread 配置和项目 metadata 的 RunnableConfig。
        """

        config = build_langgraph_thread_config(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
        )
        config["metadata"] = dict(metadata)
        return config


__all__: tuple[str, ...] = (
    "LangGraphCheckpointDescriptor",
    "LangGraphExecutionEngine",
    "LangGraphStreamEvent",
)
