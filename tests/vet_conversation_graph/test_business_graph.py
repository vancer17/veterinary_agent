##################################################################################################
# 文件: tests/vet_conversation_graph/test_business_graph.py
# 作用: 验证兽医主业务图真实接线定义与图内状态适配节点的核心契约。
# 边界: 使用协议类型占位和纯状态测试，不调用真实 L2 服务、不连接数据库、不执行 LangGraph。
##################################################################################################

import asyncio
from typing import cast

from veterinary_agent.education_agent import EducationAgent
from veterinary_agent.graph_runtime import (
    GraphDefinition,
    GraphNodeExecutionContext,
    GraphState,
)
from veterinary_agent.guardrail_framework import GuardrailFramework, GuardrailStage
from veterinary_agent.nonmedical_pet_care_agent import NonmedicalPetCareAgent
from veterinary_agent.safety_trigger_agent import SafetyTriggerAgent
from veterinary_agent.standard_consultation_agent import StandardConsultationAgent
from veterinary_agent.vet_context_builder import VetContextBuilder
from veterinary_agent.vet_conversation_graph import (
    DETERMINISTIC_GATE_NODE_ID,
    EDUCATION_NODE_ID,
    EXECUTOR_ROUTER_NODE_ID,
    NONMEDICAL_PET_CARE_NODE_ID,
    SAFETY_TRIGGER_NODE_ID,
    STANDARD_CONSULTATION_NODE_ID,
    TASK_DECOMPOSER_NODE_ID,
    BranchStateBuilderGraphNode,
    ExecutorRouterGraphNode,
    GuardrailRequestBuilderGraphNode,
    TaskLaneSelectorGraphNode,
    build_vet_conversation_graph_definition,
)
from veterinary_agent.vet_input_safety_assessor import VetInputSafetyAssessor
from veterinary_agent.vet_response_composer import (
    ComposerGuardStatus,
    VetResponseComposer,
)
from veterinary_agent.vet_task_decomposer import VetTaskDecomposer


def _build_context() -> GraphNodeExecutionContext:
    """构建状态适配节点测试使用的执行上下文。

    :return: GraphRuntime 节点执行上下文。
    """

    return GraphNodeExecutionContext(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        graph_id="vet_conversation_graph",
        graph_version="v2-langgraph",
        node_id="node_under_test",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version="params.v1",
        config_snapshot_id="config_1",
        thread_id="thread_1",
    )


def _build_definition() -> GraphDefinition:
    """构建使用协议占位依赖的真实主业务图定义。

    :return: 真实主业务图定义。
    """

    return build_vet_conversation_graph_definition(
        task_decomposer=cast(VetTaskDecomposer, object()),
        input_safety_assessor=cast(VetInputSafetyAssessor, object()),
        context_builder=cast(VetContextBuilder, object()),
        standard_consultation_agent=cast(StandardConsultationAgent, object()),
        education_agent=cast(EducationAgent, object()),
        safety_trigger_agent=cast(SafetyTriggerAgent, object()),
        nonmedical_pet_care_agent=cast(NonmedicalPetCareAgent, object()),
        guardrail_framework=cast(GuardrailFramework, object()),
        response_composer=cast(VetResponseComposer, object()),
    )


def test_business_graph_definition_declares_expected_route_edges() -> None:
    """验证真实主业务图声明执行器条件路由与发布收口边。

    :return: None。
    """

    definition = _build_definition()
    conditional_nodes = set(
        definition.conditional_next_node_ids(EXECUTOR_ROUTER_NODE_ID)
    )

    assert definition.entry_node == TASK_DECOMPOSER_NODE_ID
    assert conditional_nodes == {
        STANDARD_CONSULTATION_NODE_ID,
        EDUCATION_NODE_ID,
        SAFETY_TRIGGER_NODE_ID,
        NONMEDICAL_PET_CARE_NODE_ID,
    }
    assert DETERMINISTIC_GATE_NODE_ID in definition.nodes


def test_task_lane_selector_prefers_safety_request() -> None:
    """验证任务主通道选择优先急症安全链路。

    :return: None。
    """

    node = TaskLaneSelectorGraphNode()
    state: GraphState = {
        "context_build_requests": [
            {
                "task_id": "task_normal",
                "task_type": "GENERAL_QA",
                "executor_key": "standard_consultation",
                "generation_profile": "standard",
                "route": "normal",
                "assessment_summary": {},
            },
            {
                "task_id": "task_safety",
                "task_type": "ACUTE_EVENT",
                "executor_key": "safety_trigger",
                "generation_profile": "safety_trigger",
                "route": "safety_trigger",
                "assessment_summary": {"signals": ["SAF_03_ACUTE_RED_FLAG"]},
            },
        ],
    }

    result = asyncio.run(node(state, _build_context()))

    assert result.state_patch["task_id"] == "task_safety"
    assert result.state_patch["branch_type"] == "safety_trigger"
    assert result.state_patch["medical_content_expected"] is True


def test_executor_router_selects_nonmedical_node() -> None:
    """验证执行器路由会将非医疗执行器导向非医疗 Agent。

    :return: None。
    """

    node = ExecutorRouterGraphNode()
    state: GraphState = {"executor_key": "nonmedical_pet_care"}

    result = asyncio.run(node(state, _build_context()))

    assert result.selected_next_nodes == (NONMEDICAL_PET_CARE_NODE_ID,)
    assert result.state_patch["selected_executor_node_id"] == (
        NONMEDICAL_PET_CARE_NODE_ID
    )


def test_guardrail_request_builder_uses_review_result_for_gate() -> None:
    """验证确定性发布门请求会沿用输出安全审查后的文本引用。

    :return: None。
    """

    node = GuardrailRequestBuilderGraphNode(
        stage=GuardrailStage.DETERMINISTIC_GATE,
        previous_result_state_key="post_generation_review_result",
    )
    state: GraphState = {
        "task_id": "task_1",
        "executor_key": "standard_consultation",
        "generation_profile": "standard",
        "draft_response": "请观察精神食欲并及时就医。",
        "draft_response_ref": "draft-ref",
        "post_generation_review_result": {
            "reviewed_text_ref": "reviewed-ref",
            "status": "allowed",
        },
    }

    result = asyncio.run(node(state, _build_context()))
    guardrail_request = cast(dict[str, object], result.state_patch["guardrail_request"])

    assert guardrail_request["stage"] == GuardrailStage.DETERMINISTIC_GATE.value
    assert guardrail_request["candidate_text_ref"] == "reviewed-ref"


def test_branch_state_builder_emits_safe_degraded_segment_when_gate_blocks() -> None:
    """验证发布门未放行时分支状态会产出模板安全降级 segment。

    :return: None。
    """

    node = BranchStateBuilderGraphNode()
    state: GraphState = {
        "task_id": "task_1",
        "branch_id": "branch_task_1",
        "branch_type": "standard_consultation",
        "executor_key": "standard_consultation",
        "generation_profile": "standard",
        "draft_response": "未审查草稿不得直接发布。",
        "deterministic_gate_result": {
            "status": "blocked",
            "publish_allowed": False,
        },
        "post_generation_review_result": {"status": "failed"},
    }

    result = asyncio.run(node(state, _build_context()))
    branches = cast(
        list[dict[str, object]], result.state_patch["branch_execution_states"]
    )
    segment = cast(dict[str, object], branches[0]["publishable_segment"])

    assert segment["publish_allowed"] is True
    assert segment["guard_status"] == ComposerGuardStatus.TEMPLATE_SAFE.value
    assert segment["fallback_triggered"] is True
