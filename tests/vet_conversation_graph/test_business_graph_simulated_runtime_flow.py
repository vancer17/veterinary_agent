##################################################################################################
# 文件: tests/vet_conversation_graph/test_business_graph_simulated_runtime_flow.py
# 作用: 验证兽医主业务图在真实 DefaultGraphRuntime 下使用 Fake L2 服务完成仿真功能链路、事件与发布。
# 边界: 使用 InMemorySaver 与测试 CheckpointStore；不连接真实数据库、不调用 LLM/RAG 或真实领域服务。
##################################################################################################

import asyncio

from tests.graph_runtime.helpers import collect_events
from tests.vet_conversation_graph.helpers import (
    build_safety_scenario,
    build_simulated_runtime_fixture,
    build_simulated_turn_request,
    build_standard_scenario,
)
from veterinary_agent.agent_application_service import (
    AgentGraphEventDto,
    AgentGraphTurnResultDto,
)
from veterinary_agent.graph_runtime import GraphRuntimeEventType
from veterinary_agent.vet_conversation_graph import (
    EDUCATION_NODE_ID,
    NONMEDICAL_PET_CARE_NODE_ID,
    RESPONSE_COMPOSER_NODE_ID,
    SAFETY_TRIGGER_NODE_ID,
    STANDARD_CONSULTATION_NODE_ID,
)
from veterinary_agent.vet_response_composer import ComposerSegmentType


def _event_types(events: list[AgentGraphEventDto]) -> list[str]:
    """读取事件类型列表。

    :param events: GraphRuntime 事件列表。
    :return: 事件类型字符串列表。
    """

    return [event.event_type for event in events]


def _completed_node_ids(events: list[AgentGraphEventDto]) -> list[str]:
    """读取已完成节点 ID 列表。

    :param events: GraphRuntime 事件列表。
    :return: 按事件顺序排列的已完成节点 ID。
    """

    return [
        str(event.data["node_id"])
        for event in events
        if event.event_type == GraphRuntimeEventType.NODE_COMPLETED.value
        and "node_id" in event.data
    ]


def _run_completed_result(events: list[AgentGraphEventDto]) -> AgentGraphTurnResultDto:
    """读取 run_completed 事件中的最终结果。

    :param events: GraphRuntime 事件列表。
    :return: GraphRuntime 最终结果 DTO。
    :raises StopIteration: 当事件流缺少 run_completed 时抛出。
    """

    completed_event = next(
        event
        for event in events
        if event.event_type == GraphRuntimeEventType.RUN_COMPLETED.value
    )
    raw_result = completed_event.data["result"]
    assert isinstance(raw_result, dict)
    return AgentGraphTurnResultDto.model_validate(raw_result)


def test_simulated_runtime_flow_publishes_standard_segment_with_checkpoint() -> None:
    """验证标准问诊仿真链路可在真实 Runtime 下完成 checkpoint 与 segment 发布。

    :return: None。
    """

    scenario = build_standard_scenario()
    fixture = build_simulated_runtime_fixture(scenario)
    request = build_simulated_turn_request(scenario)

    events = asyncio.run(collect_events(fixture.runtime.stream_turn(request)))
    event_types = _event_types(events)
    completed_node_ids = _completed_node_ids(events)
    result = _run_completed_result(events)

    assert fixture.runtime.is_ready() is True
    assert GraphRuntimeEventType.RUN_STARTED.value in event_types
    assert GraphRuntimeEventType.CHECKPOINT_SAVED.value in event_types
    assert GraphRuntimeEventType.SEGMENT_READY.value in event_types
    assert GraphRuntimeEventType.SEGMENT_COMPLETED.value in event_types
    assert GraphRuntimeEventType.SEGMENT_PUBLISHED.value in event_types
    assert GraphRuntimeEventType.RUN_COMPLETED.value in event_types
    assert STANDARD_CONSULTATION_NODE_ID in completed_node_ids
    assert EDUCATION_NODE_ID not in completed_node_ids
    assert SAFETY_TRIGGER_NODE_ID not in completed_node_ids
    assert NONMEDICAL_PET_CARE_NODE_ID not in completed_node_ids
    assert len(fixture.checkpoint_store.ensure_calls) == 1
    assert len(fixture.checkpoint_store.acquire_calls) == 1
    assert len(fixture.checkpoint_store.release_calls) == 1
    assert len(fixture.checkpoint_store.publish_calls) == 1
    assert fixture.checkpoint_store.publish_calls[0].task_id == "task_primary"
    assert fixture.checkpoint_store.publish_calls[0].metadata["node_id"] == (
        RESPONSE_COMPOSER_NODE_ID
    )
    assert result.segments[0].type == ComposerSegmentType.MEDICAL.value
    assert "标准问诊" in result.output_text


def test_simulated_runtime_flow_keeps_safety_branch_as_only_business_agent() -> None:
    """验证急症安全仿真链路只执行 SafetyTriggerAgent 并发布安全 segment。

    :return: None。
    """

    scenario = build_safety_scenario()
    fixture = build_simulated_runtime_fixture(scenario)
    request = build_simulated_turn_request(scenario, run_id="run_safety")

    events = asyncio.run(collect_events(fixture.runtime.stream_turn(request)))
    completed_node_ids = _completed_node_ids(events)
    result = _run_completed_result(events)

    assert SAFETY_TRIGGER_NODE_ID in completed_node_ids
    assert STANDARD_CONSULTATION_NODE_ID not in completed_node_ids
    assert EDUCATION_NODE_ID not in completed_node_ids
    assert NONMEDICAL_PET_CARE_NODE_ID not in completed_node_ids
    assert len(fixture.fakes.safety_agent.calls) == 1
    assert len(fixture.fakes.standard_agent.calls) == 0
    assert len(fixture.checkpoint_store.publish_calls) == 1
    assert result.segments[0].type == ComposerSegmentType.SAFETY.value
    assert "急症安全提示" in result.output_text


def test_simulated_runtime_flow_publishes_template_safe_segment_when_gate_blocks() -> (
    None
):
    """验证确定性发布门阻断时真实 Runtime 仍能发布模板安全降级 segment。

    :return: None。
    """

    scenario = build_standard_scenario(gate_allows_publish=False)
    fixture = build_simulated_runtime_fixture(scenario)
    request = build_simulated_turn_request(scenario, run_id="run_gate_blocked")

    events = asyncio.run(collect_events(fixture.runtime.stream_turn(request)))
    event_types = _event_types(events)
    result = _run_completed_result(events)
    segment_metadata = result.segments[0].metadata or {}

    assert GraphRuntimeEventType.SEGMENT_PUBLISHED.value in event_types
    assert len(fixture.checkpoint_store.publish_calls) == 1
    assert result.segments[0].type == ComposerSegmentType.MEDICAL.value
    assert segment_metadata["fallback_triggered"] is True
    assert segment_metadata["graph_safe_degraded"] is True
    assert "未完成安全发布门校验" in result.output_text
    assert "标准问诊：" not in result.output_text
