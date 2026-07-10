##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/event_adapter.py
# 作用: 将 LangGraph tasks、updates、checkpoints 与 custom stream 转换为项目标准事件。
# 边界: 不执行图、不读写 checkpoint、不发布业务 segment；只做安全字段提取和事件语义映射。
##################################################################################################

from collections.abc import Mapping
from dataclasses import dataclass, field

from veterinary_agent.agent_application_service import AgentGraphEventDto
from veterinary_agent.graph_runtime.definition import GraphDefinition
from veterinary_agent.graph_runtime.dto import JsonMap
from veterinary_agent.graph_runtime.enums import GraphRuntimeEventType
from veterinary_agent.graph_runtime.events import GraphEventFactory
from veterinary_agent.graph_runtime.langgraph_backend import LangGraphStreamEvent


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


def _read_checkpoint_id(data: JsonMap) -> str | None:
    """从 LangGraph checkpoint 事件中读取 checkpoint ID。

    :param data: LangGraph checkpoint 事件数据。
    :return: checkpoint ID；事件结构不完整时返回 None。
    """

    config = _as_json_map(data.get("config"))
    configurable = _as_json_map(config.get("configurable"))
    return _read_string(configurable.get("checkpoint_id"))


@dataclass(slots=True)
class GraphEventAdapter:
    """LangGraph stream 到 GraphRuntime 标准事件的有状态适配器。"""

    definition: GraphDefinition
    event_factory: GraphEventFactory
    emit_node_events: bool
    started_tasks: set[str] = field(default_factory=set)
    emitted_node_events: int = 0

    def adapt(
        self,
        event: LangGraphStreamEvent,
    ) -> tuple[AgentGraphEventDto, ...]:
        """转换单个 LangGraph stream 事件。

        :param event: 已规范化的 LangGraph stream 事件。
        :return: 零个或多个项目标准事件。
        """

        if event.event_type == "tasks":
            return self._adapt_task_event(event)
        if event.event_type == "updates":
            return self._adapt_update_event(event)
        if event.event_type == "checkpoints":
            return self._adapt_checkpoint_event(event)
        if event.event_type == "custom":
            return self._adapt_custom_event(event)
        return ()

    def _adapt_task_event(
        self,
        event: LangGraphStreamEvent,
    ) -> tuple[AgentGraphEventDto, ...]:
        """转换 LangGraph task 开始或完成事件。

        :param event: LangGraph task stream 事件。
        :return: 节点开始、失败或重试事件元组。
        """

        task_id = _read_string(event.data.get("id"))
        node_id = _read_string(event.data.get("name"))
        if task_id is None or node_id is None or node_id.startswith("__"):
            return ()
        if "input" in event.data:
            already_started = task_id in self.started_tasks
            self.started_tasks.add(task_id)
            if not self.emit_node_events:
                return ()
            event_type = (
                GraphRuntimeEventType.NODE_RETRYING
                if already_started
                else GraphRuntimeEventType.NODE_STARTED
            )
            self.emitted_node_events += 1
            return (
                self.event_factory.create(
                    event_type=event_type,
                    node_id=node_id,
                    data={
                        "task_id": task_id,
                        "namespace": list(event.namespace),
                    },
                ),
            )
        raw_error = event.data.get("error")
        if raw_error is None:
            return ()
        self.emitted_node_events += 1
        return (
            self.event_factory.create(
                event_type=GraphRuntimeEventType.NODE_FAILED,
                node_id=node_id,
                data={
                    "task_id": task_id,
                    "error_type": type(raw_error).__name__,
                    "namespace": list(event.namespace),
                },
            ),
        )

    def _adapt_update_event(
        self,
        event: LangGraphStreamEvent,
    ) -> tuple[AgentGraphEventDto, ...]:
        """转换 LangGraph 节点状态更新事件。

        :param event: LangGraph updates stream 事件。
        :return: 节点完成事件元组。
        """

        if not self.emit_node_events:
            return ()
        events: list[AgentGraphEventDto] = []
        for node_id, update_value in event.data.items():
            if node_id.startswith("__"):
                continue
            update = _as_json_map(update_value)
            business_state = _as_json_map(update.get("business_state"))
            selected_routes = _as_json_map(update.get("selected_routes"))
            selected = selected_routes.get(node_id)
            self.emitted_node_events += 1
            events.append(
                self.event_factory.create(
                    event_type=GraphRuntimeEventType.NODE_COMPLETED,
                    node_id=node_id,
                    data={
                        "patch_keys": sorted(business_state),
                        "selected_next_nodes": selected
                        if isinstance(selected, list)
                        else None,
                        "namespace": list(event.namespace),
                    },
                )
            )
        return tuple(events)

    def _adapt_checkpoint_event(
        self,
        event: LangGraphStreamEvent,
    ) -> tuple[AgentGraphEventDto, ...]:
        """转换 LangGraph checkpoint 创建事件。

        :param event: LangGraph checkpoints stream 事件。
        :return: checkpoint 保存事件元组。
        """

        checkpoint_id = _read_checkpoint_id(event.data)
        metadata = _as_json_map(event.data.get("metadata"))
        return (
            self.event_factory.create(
                event_type=GraphRuntimeEventType.CHECKPOINT_SAVED,
                data={
                    "checkpoint_id": checkpoint_id,
                    "step": metadata.get("step"),
                    "next_nodes": event.data.get("next"),
                    "namespace": list(event.namespace),
                },
            ),
        )

    def _adapt_custom_event(
        self,
        event: LangGraphStreamEvent,
    ) -> tuple[AgentGraphEventDto, ...]:
        """转换节点通过 LangGraph custom stream 发送的项目事件。

        :param event: LangGraph custom stream 事件。
        :return: 允许的 GraphRuntime 自定义事件元组。
        """

        event_type = _read_string(event.data.get("event_type"))
        if event_type is None:
            return ()
        node_id = _read_string(event.data.get("node_id"))
        raw_data = _as_json_map(event.data.get("data"))
        raw_data["namespace"] = list(event.namespace)
        return (
            self.event_factory.create(
                event_type=event_type,
                node_id=node_id,
                data=raw_data,
            ),
        )


__all__: tuple[str, ...] = ("GraphEventAdapter",)
