##################################################################################################
# 文件: src/veterinary_agent/vet_response_composer/node.py
# 作用: 提供 VetResponseComposer 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 Composer DTO 转换，不执行发布业务逻辑、不管理 HTTP/SSE 连接。
##################################################################################################

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from veterinary_agent.agent_application_service import (
    AgentGraphEventDto,
    AgentGraphTurnResultDto,
    AgentReasoningDisplayDto,
    AgentResponseSegmentDto,
    AgentVetResultDto,
)
from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.vet_response_composer.dto import (
    ComposeTurnRequestDto,
    ComposeTurnResultDto,
    JsonMap,
    ResponseSegmentDto,
)
from veterinary_agent.vet_response_composer.service import VetResponseComposer

_COMPONENT_NODE_ID = "vet_response_composer"


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _read_optional_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _now_utc() -> datetime:
    """读取当前 UTC 时间。

    :return: 当前 UTC 时间。
    """

    return datetime.now(UTC)


class VetResponseComposerGraphNode:
    """将 VetResponseComposer 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        composer: VetResponseComposer,
        node_id: str = _COMPONENT_NODE_ID,
    ) -> None:
        """初始化 Composer 图节点。

        :param composer: VetResponseComposer 公共服务契约。
        :param node_id: 当前图节点 ID，用于事件与 trace 摘要。
        :return: None。
        :raises ValueError: 当节点 ID 为空时抛出。
        """

        if not node_id.strip():
            raise ValueError("node_id 不得为空")
        self._composer = composer
        self._node_id = node_id.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 并执行回复合成发布。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 写回最终 result、segments、turn state 和 Composer 事件的节点结果。
        """

        request = self._build_request_from_state(state=state, context=context)
        composition = await self._composer.compose_turn_response(request)
        graph_result = self._build_graph_result(composition=composition)
        agent_segments = graph_result.segments
        segment_payloads = [
            segment.model_dump(mode="json") for segment in agent_segments
        ]
        return GraphNodeResult(
            state_patch={
                "result": graph_result.model_dump(mode="json"),
                "segments": segment_payloads,
                "segments_to_publish": segment_payloads,
                "turn_composition_state": composition.turn_state.model_dump(
                    mode="json"
                ),
                "composer_trace_patch": composition.trace_patch.model_dump(mode="json"),
                "composer_trace_delivery_status": (
                    composition.trace_delivery_status.value
                ),
            },
            events=self._build_stream_events(
                context=context,
                composition=composition,
                agent_segments=agent_segments,
            ),
        )

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> ComposeTurnRequestDto:
        """从 graph state 和节点上下文构建 Composer 请求。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 已绑定可信运行身份的 Composer 请求。
        """

        request_state = _as_mapping(state.get("request")) or {}
        return ComposeTurnRequestDto(
            request_id=context.request_id,
            trace_id=context.trace_id,
            run_id=context.run_id,
            session_id=context.session_id,
            user_id=context.user_id,
            current_pet_id=context.current_pet_id,
            user_message_id=_read_optional_string(request_state.get("user_message_id")),
            thread_id=context.thread_id,
            params_version=context.params_version,
            config_snapshot_id=context.config_snapshot_id,
            graph_state=dict(state),
        )

    def _build_graph_result(
        self,
        *,
        composition: ComposeTurnResultDto,
    ) -> AgentGraphTurnResultDto:
        """将 Composer 结果转换为 GraphRuntime 最终结果。

        :param composition: Composer 回复合成结果。
        :return: GraphRuntime 可读取的应用层最终结果。
        """

        agent_segments = [
            self._to_agent_segment(segment) for segment in composition.segments
        ]
        return AgentGraphTurnResultDto(
            output_text=composition.output_text,
            segments=agent_segments,
            vet_result=AgentVetResultDto(
                route="vet_response_composer",
                audit_tier=composition.turn_state.turn_audit_tier,
                metadata={
                    "trace_degraded": composition.trace_patch.trace_degraded,
                    "published_segment_count": len(agent_segments),
                },
            ),
            metadata=dict(composition.metadata),
        )

    def _to_agent_segment(
        self,
        segment: ResponseSegmentDto,
    ) -> AgentResponseSegmentDto:
        """将 Composer 内部 segment 转换为应用层用户可见 segment。

        :param segment: Composer 归一化发布事实。
        :return: 应用层用户可见业务分段 DTO。
        """

        reasoning_display = self._read_reasoning_display(segment.metadata)
        return AgentResponseSegmentDto(
            segment_id=segment.segment_id,
            type=segment.segment_type,
            title=segment.title,
            status="completed",
            output_text=segment.content,
            references=[],
            reasoning_display=reasoning_display,
            metadata={
                **segment.metadata,
                "task_id": segment.task_id,
                "order_index": segment.order_index,
                "audit_tier": segment.audit_tier,
            },
        )

    def _read_reasoning_display(
        self,
        metadata: JsonMap,
    ) -> AgentReasoningDisplayDto | None:
        """从 segment metadata 中读取可展示 reasoning display。

        :param metadata: segment 轻量元信息。
        :return: 可展示 reasoning display DTO；缺失或非法时返回 None。
        """

        raw_reasoning = _as_mapping(metadata.get("reasoning_display"))
        if raw_reasoning is None:
            return None
        try:
            return AgentReasoningDisplayDto.model_validate(raw_reasoning)
        except ValueError:
            return None

    def _build_stream_events(
        self,
        *,
        context: GraphNodeExecutionContext,
        composition: ComposeTurnResultDto,
        agent_segments: Sequence[AgentResponseSegmentDto],
    ) -> tuple[AgentGraphEventDto, ...]:
        """构建段级流式发布事件。

        :param context: 当前图节点执行上下文。
        :param composition: Composer 回复合成结果。
        :param agent_segments: 应用层用户可见 segment 列表。
        :return: 可由 GraphRuntime 透传的协议无关事件元组。
        """

        stream_delta_chars = self._read_stream_delta_chars(composition.metadata)
        events: list[AgentGraphEventDto] = [
            self._build_event(
                context=context,
                sequence_no=1,
                event_type="turn.started",
                data={
                    "id": context.run_id,
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                },
            )
        ]
        sequence_no = 2
        for segment_index, segment in enumerate(agent_segments):
            events.append(
                self._build_event(
                    context=context,
                    sequence_no=sequence_no,
                    event_type="segment.started",
                    data={
                        "segment_id": segment.segment_id,
                        "index": segment_index,
                        "type": segment.type,
                        "title": segment.title,
                    },
                )
            )
            sequence_no += 1
            for delta in self._split_delta_text(
                text=segment.output_text or "",
                max_chars=stream_delta_chars,
            ):
                events.append(
                    self._build_event(
                        context=context,
                        sequence_no=sequence_no,
                        event_type="segment.delta",
                        data={
                            "segment_id": segment.segment_id,
                            "delta": {
                                "type": "output_text_delta",
                                "text": delta,
                            },
                        },
                    )
                )
                sequence_no += 1
            events.append(
                self._build_event(
                    context=context,
                    sequence_no=sequence_no,
                    event_type="segment.completed",
                    data={
                        "segment_id": segment.segment_id,
                        "status": "completed",
                    },
                )
            )
            sequence_no += 1
        events.append(
            self._build_event(
                context=context,
                sequence_no=sequence_no,
                event_type="turn.completed",
                data={"id": context.run_id, "status": "completed"},
            )
        )
        return tuple(events)

    def _read_stream_delta_chars(self, metadata: JsonMap) -> int:
        """从 Composer metadata 中读取流式 delta 字符数。

        :param metadata: Composer 结果元信息。
        :return: 可用于切分 segment.delta 的最大字符数。
        """

        raw_value = metadata.get("stream_delta_chars")
        if isinstance(raw_value, int) and raw_value > 0:
            return raw_value
        return 512

    def _split_delta_text(
        self,
        *,
        text: str,
        max_chars: int,
    ) -> tuple[str, ...]:
        """按固定字符数切分 segment delta 文本。

        :param text: 待切分的用户可见文本。
        :param max_chars: 单个 delta 的最大字符数。
        :return: 切分后的文本片段元组。
        """

        if not text:
            return ()
        return tuple(
            text[index : index + max_chars] for index in range(0, len(text), max_chars)
        )

    def _build_event(
        self,
        *,
        context: GraphNodeExecutionContext,
        sequence_no: int,
        event_type: str,
        data: Mapping[str, object],
    ) -> AgentGraphEventDto:
        """构建 Composer 节点输出事件。

        :param context: 当前图节点执行上下文。
        :param sequence_no: 当前 Composer 事件序号。
        :param event_type: 对外事件类型。
        :param data: 事件安全数据。
        :return: 协议无关 GraphRuntime 事件 DTO。
        """

        return AgentGraphEventDto(
            event_id=(f"composer_event_{context.run_id}_{sequence_no}_{event_type}"),
            event_type=event_type,
            data={
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "run_id": context.run_id,
                "node_id": self._node_id,
                **dict(data),
            },
            created_at=_now_utc(),
        )


__all__: tuple[str, ...] = ("VetResponseComposerGraphNode",)
