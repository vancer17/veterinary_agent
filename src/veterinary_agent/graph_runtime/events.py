##################################################################################################
# 文件: src/veterinary_agent/graph_runtime/events.py
# 作用: 提供 GraphRuntime 标准事件构造能力，统一生成协议无关 AgentGraphEventDto。
# 边界: 不执行 SSE/HTTP 映射，不写 LogicTraceStore，不承载业务 trace schema。
##################################################################################################

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256

from veterinary_agent.agent_application_service import AgentGraphEventDto
from veterinary_agent.graph_runtime.dto import GraphRunIdentity, JsonMap
from veterinary_agent.graph_runtime.enums import GraphRuntimeEventType


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


def _build_event_id(
    *,
    identity: GraphRunIdentity,
    sequence_no: int,
    event_type: str,
    node_id: str | None,
) -> str:
    """构建稳定的 GraphRuntime 事件 ID。

    :param identity: 当前图运行身份上下文。
    :param sequence_no: 当前事件在本工厂内的单调递增序号。
    :param event_type: 已解析为字符串的事件类型。
    :param node_id: 可选节点 ID。
    :return: 基于运行身份和事件序号派生的稳定事件 ID。
    """

    source = (
        f"{identity.run_id}:{identity.graph_id}:{identity.graph_version}:"
        f"{sequence_no}:{event_type}:{node_id or '-'}"
    )
    return f"graph_event_{sha256(source.encode('utf-8')).hexdigest()[:32]}"


class GraphEventFactory:
    """GraphRuntime 事件工厂。"""

    def __init__(self, identity: GraphRunIdentity) -> None:
        """初始化 GraphRuntime 事件工厂。

        :param identity: 当前图运行身份上下文。
        :return: None。
        """

        self._identity = identity
        self._sequence_no = 0

    def create(
        self,
        *,
        event_type: GraphRuntimeEventType | str,
        data: Mapping[str, object] | None = None,
        node_id: str | None = None,
    ) -> AgentGraphEventDto:
        """创建标准 GraphRuntime 事件。

        :param event_type: 事件类型。
        :param data: 事件安全数据。
        :param node_id: 可选节点 ID。
        :return: 协议无关 GraphRuntime 事件 DTO。
        """

        payload: JsonMap = {
            "request_id": self._identity.request_id,
            "trace_id": self._identity.trace_id,
            "run_id": self._identity.run_id,
            "graph_id": self._identity.graph_id,
            "graph_version": self._identity.graph_version,
            "state_schema_version": self._identity.state_schema_version,
            "params_version": self._identity.params_version,
            "config_snapshot_id": self._identity.config_snapshot_id,
        }
        if node_id is not None:
            payload["node_id"] = node_id
        if data is not None:
            payload.update(dict(data))
        resolved_type = (
            event_type.value
            if isinstance(event_type, GraphRuntimeEventType)
            else event_type
        )
        self._sequence_no += 1
        return AgentGraphEventDto(
            event_id=_build_event_id(
                identity=self._identity,
                sequence_no=self._sequence_no,
                event_type=resolved_type,
                node_id=node_id,
            ),
            event_type=resolved_type,
            data=payload,
            created_at=_now_utc(),
        )


__all__: tuple[str, ...] = ("GraphEventFactory",)
