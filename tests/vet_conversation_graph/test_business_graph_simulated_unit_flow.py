##################################################################################################
# 文件: tests/vet_conversation_graph/test_business_graph_simulated_unit_flow.py
# 作用: 验证兽医主业务图在 Fake L2 服务下的仿真全链路单元接线、条件路由和安全降级状态传递。
# 边界: 使用轻量图定义 driver；不启动 LangGraph、不连接数据库、不调用真实 L2 服务或外部模型。
##################################################################################################

import asyncio

import pytest

from tests.vet_conversation_graph.helpers import (
    SimulatedBusinessFakes,
    SimulatedBusinessScenario,
    build_education_scenario,
    build_nonmedical_scenario,
    build_safety_scenario,
    build_simulated_business_graph_fixture,
    build_simulated_turn_request,
    build_standard_scenario,
    run_definition_to_completion,
)
from veterinary_agent.guardrail_framework import GuardrailStage
from veterinary_agent.vet_conversation_graph import (
    BRANCH_STATE_BUILDER_NODE_ID,
    CONTEXT_BUILDER_NODE_ID,
    DETERMINISTIC_GATE_NODE_ID,
    DETERMINISTIC_GATE_REQUEST_NODE_ID,
    EDUCATION_NODE_ID,
    EXECUTOR_ROUTER_NODE_ID,
    INPUT_SAFETY_NODE_ID,
    NONMEDICAL_PET_CARE_NODE_ID,
    POST_GENERATION_GUARD_REQUEST_NODE_ID,
    POST_GENERATION_REVIEW_NODE_ID,
    RESPONSE_COMPOSER_NODE_ID,
    SAFETY_TRIGGER_NODE_ID,
    STANDARD_CONSULTATION_NODE_ID,
    TASK_DECOMPOSER_NODE_ID,
    TASK_LANE_SELECTOR_NODE_ID,
)
from veterinary_agent.vet_response_composer import ComposerGuardStatus


def _business_agent_call_counts(fakes: SimulatedBusinessFakes) -> dict[str, int]:
    """读取四类业务 Agent 的调用次数。

    :param fakes: 主业务图仿真 Fake 服务集合。
    :return: 节点 ID 到调用次数的映射。
    """

    return {
        STANDARD_CONSULTATION_NODE_ID: len(fakes.standard_agent.calls),
        EDUCATION_NODE_ID: len(fakes.education_agent.calls),
        SAFETY_TRIGGER_NODE_ID: len(fakes.safety_agent.calls),
        NONMEDICAL_PET_CARE_NODE_ID: len(fakes.nonmedical_agent.calls),
    }


@pytest.mark.parametrize(
    ("scenario", "expected_business_node_id"),
    [
        (build_standard_scenario(), STANDARD_CONSULTATION_NODE_ID),
        (build_education_scenario(), EDUCATION_NODE_ID),
        (build_safety_scenario(), SAFETY_TRIGGER_NODE_ID),
        (build_nonmedical_scenario(), NONMEDICAL_PET_CARE_NODE_ID),
    ],
)
def test_simulated_unit_flow_routes_to_expected_business_agent(
    scenario: SimulatedBusinessScenario,
    expected_business_node_id: str,
) -> None:
    """验证主业务图仿真单元链路会路由到预期业务 Agent 并完成发布收口。

    :param scenario: 当前参数化仿真场景。
    :param expected_business_node_id: 预期被执行的业务 Agent 节点 ID。
    :return: None。
    """

    fixture = build_simulated_business_graph_fixture(scenario)
    request = build_simulated_turn_request(scenario)

    result = asyncio.run(
        run_definition_to_completion(
            definition=fixture.definition,
            request=request,
        )
    )

    assert result.completed_node_ids == (
        TASK_DECOMPOSER_NODE_ID,
        INPUT_SAFETY_NODE_ID,
        TASK_LANE_SELECTOR_NODE_ID,
        CONTEXT_BUILDER_NODE_ID,
        EXECUTOR_ROUTER_NODE_ID,
        expected_business_node_id,
        POST_GENERATION_GUARD_REQUEST_NODE_ID,
        POST_GENERATION_REVIEW_NODE_ID,
        DETERMINISTIC_GATE_REQUEST_NODE_ID,
        DETERMINISTIC_GATE_NODE_ID,
        BRANCH_STATE_BUILDER_NODE_ID,
        RESPONSE_COMPOSER_NODE_ID,
    )
    assert result.state["selected_executor_node_id"] == expected_business_node_id
    assert _business_agent_call_counts(fixture.fakes)[expected_business_node_id] == 1
    assert sum(_business_agent_call_counts(fixture.fakes).values()) == 1
    assert fixture.fakes.input_safety_assessor.batch_calls
    assert fixture.fakes.context_builder.calls
    assert [call.stage for call in fixture.fakes.guardrail_framework.calls] == [
        GuardrailStage.POST_GENERATION_REVIEW,
        GuardrailStage.DETERMINISTIC_GATE,
    ]
    assert "branch_execution_states" in result.state
    graph_result = result.state["result"]
    assert isinstance(graph_result, dict)
    assert graph_result["segments"]
    assert scenario.name in fixture.fakes.response_composer.calls[0].request_id


def test_simulated_unit_flow_builds_template_safe_segment_when_gate_blocks() -> None:
    """验证确定性发布门阻断时主图会生成模板安全降级分支并交给 Composer。

    :return: None。
    """

    scenario = build_standard_scenario(gate_allows_publish=False)
    fixture = build_simulated_business_graph_fixture(scenario)
    request = build_simulated_turn_request(scenario)

    result = asyncio.run(
        run_definition_to_completion(
            definition=fixture.definition,
            request=request,
        )
    )

    publishable_segment = result.state["publishable_segment"]
    graph_result = result.state["result"]
    assert isinstance(publishable_segment, dict)
    assert isinstance(graph_result, dict)
    assert (
        publishable_segment["guard_status"] == ComposerGuardStatus.TEMPLATE_SAFE.value
    )
    assert publishable_segment["fallback_triggered"] is True
    assert publishable_segment["publish_allowed"] is True
    assert "未完成安全发布门校验" in graph_result["output_text"]
    assert "标准问诊：" not in graph_result["output_text"]
