##################################################################################################
# 文件: tests/guardrail_framework/test_trace_and_node.py
# 作用: 验证 GuardrailFramework trace sink 适配、trace 降级暴露和 GraphRuntime 节点适配。
# 边界: 使用内存替身验证契约转换，不连接真实 LogicTraceStore、不实现业务安全 handler。
##################################################################################################

import asyncio
from typing import cast

from veterinary_agent.graph_runtime import GraphNodeExecutionContext
from veterinary_agent.guardrail_framework import (
    GuardrailRunResultDto,
    GuardrailStage,
    GuardrailStatus,
    GuardrailTraceRecordDto,
    GuardrailTraceWriteStatus,
    GuardrailFrameworkGraphNode,
    LogicTraceGuardrailTraceSink,
    build_default_guardrail_policy_registry,
)
from veterinary_agent.logic_trace_store import (
    AppendTraceEventCommandDto,
    LogicTraceStore,
    LogicTraceWriteResultDto,
    LogicTraceWriteStatus,
)

from .helpers import (
    RecordingGuardrailTraceSink,
    StaticGuardrailHandler,
    build_framework_with_handler,
    build_provider,
    build_request,
)


class RecordingLogicTraceStore:
    """记录 LogicTraceStore append_trace_event 调用的测试 store。"""

    def __init__(
        self,
        *,
        status: LogicTraceWriteStatus = LogicTraceWriteStatus.WRITTEN,
    ) -> None:
        """初始化测试 LogicTraceStore。

        :param status: append_trace_event 返回的写入状态。
        :return: None。
        """

        self.status = status
        self.events: list[AppendTraceEventCommandDto] = []

    def is_ready(self) -> bool:
        """判断测试 store 是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def close(self) -> None:
        """关闭测试 store。

        :return: None。
        """

        return None

    async def append_trace_event(
        self,
        command: AppendTraceEventCommandDto,
    ) -> LogicTraceWriteResultDto:
        """记录 trace event 并返回预设状态。

        :param command: 追加 trace event 命令。
        :return: 预设 trace 写入结果。
        """

        self.events.append(command)
        return LogicTraceWriteResultDto(status=self.status)


def _build_graph_context(provider_snapshot_id: str) -> GraphNodeExecutionContext:
    """构建 GuardrailFramework 图节点测试上下文。

    :param provider_snapshot_id: 当前 RuntimeConfig 快照 ID。
    :return: GraphRuntime 节点执行上下文。
    """

    return GraphNodeExecutionContext(
        request_id="request-node",
        trace_id="trace-node",
        run_id="run-node",
        graph_id="graph-test",
        graph_version="graph.v-test",
        node_id="guardrail_gate_node",
        session_id="session-node",
        user_id="user-node",
        current_pet_id="pet-node",
        params_version="params.v-test",
        config_snapshot_id=provider_snapshot_id,
        thread_id="thread-node",
    )


def test_degraded_trace_sink_marks_result_without_changing_gate_allow() -> None:
    """验证 trace 降级会显式暴露但不会改变 gate allow 结论。

    :return: None。
    """

    provider = build_provider()
    trace_sink = RecordingGuardrailTraceSink(status=GuardrailTraceWriteStatus.DEGRADED)
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.DETERMINISTIC_GATE,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)
        ),
        trace_sink=trace_sink,
    )

    result = asyncio.run(
        framework.run_deterministic_gate(
            build_request(stage=GuardrailStage.DETERMINISTIC_GATE, provider=provider)
        )
    )

    assert result.status is GuardrailStatus.ALLOWED
    assert result.publish_allowed is True
    assert result.trace_degraded is True
    assert len(trace_sink.records) == 1


def test_logic_trace_adapter_writes_guardrail_event_summary() -> None:
    """验证 LogicTraceGuardrailTraceSink 会写入标准护栏事件摘要。

    :return: None。
    """

    provider = build_provider()
    request = build_request(
        stage=GuardrailStage.DETERMINISTIC_GATE,
        provider=provider,
    )
    result = GuardrailRunResultDto(
        status=GuardrailStatus.ALLOWED,
        publish_allowed=True,
        final_text_ref="final-ref-test",
    )
    policy = build_default_guardrail_policy_registry(
        provider.current_snapshot().guardrail_framework
    ).resolve_policies(stage=GuardrailStage.DETERMINISTIC_GATE)[0]
    store = RecordingLogicTraceStore()
    sink = LogicTraceGuardrailTraceSink(store=cast(LogicTraceStore, store))

    write_result = asyncio.run(
        sink.write_guardrail_trace(
            GuardrailTraceRecordDto(
                request=request,
                result=result,
                policies=[policy],
                duration_ms=7,
            )
        )
    )

    assert write_result.status is GuardrailTraceWriteStatus.RECORDED
    assert len(store.events) == 1
    event = store.events[0]
    assert event.event_type == "guardrail.deterministic_gate.completed"
    assert event.task_id == "task-test"
    assert event.segment_id == "segment-test"
    assert event.summary["publish_allowed"] is True
    policies = cast(list[dict[str, object]], event.business_payload["policies"])
    assert policies[0]["policy_id"] == policy.policy_id


def test_graph_node_writes_guardrail_result_patch() -> None:
    """验证 GuardrailFrameworkGraphNode 将服务结果写回 graph state patch。

    :return: None。
    """

    provider = build_provider()
    framework = build_framework_with_handler(
        provider=provider,
        stage=GuardrailStage.DETERMINISTIC_GATE,
        handler=StaticGuardrailHandler(
            GuardrailRunResultDto(status=GuardrailStatus.ALLOWED)
        ),
        trace_sink=RecordingGuardrailTraceSink(),
    )
    node = GuardrailFrameworkGraphNode(
        guardrail_framework=framework,
        stage=GuardrailStage.DETERMINISTIC_GATE,
    )

    result = asyncio.run(
        node(
            {
                "guardrail_request": {
                    "context": {
                        "task_id": "task-node",
                        "segment_id": "segment-node",
                        "generation_profile": "standard",
                    },
                    "candidate_text_ref": "draft-ref-node",
                    "task_input": {"task_kind": "node_test"},
                }
            },
            _build_graph_context(provider.current_snapshot().config_snapshot_id),
        )
    )

    assert result.state_patch["guardrail_status"] == GuardrailStatus.ALLOWED.value
    assert result.state_patch["guardrail_publish_allowed"] is True
    assert result.state_patch["guardrail_trace_degraded"] is False
    guardrail_result = result.state_patch["guardrail_result"]
    assert isinstance(guardrail_result, dict)
    assert guardrail_result["publish_allowed"] is True
