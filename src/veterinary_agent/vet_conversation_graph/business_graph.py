##################################################################################################
# 文件: src/veterinary_agent/vet_conversation_graph/business_graph.py
# 作用: 构建兽医主业务图的真实接线定义，将 L2 业务组件节点按任务拆解、评估、生成、护栏和合成顺序编排。
# 边界: 只声明图结构与节点适配，不创建基础设施、不实现跨领域业务逻辑、不读取配置或执行图运行。
##################################################################################################

from veterinary_agent.education_agent import EducationAgent, EducationAgentGraphNode
from veterinary_agent.graph_runtime import (
    GraphDefinition,
    GraphEdgeSpec,
    GraphNodeSpec,
    GraphRegistry,
)
from veterinary_agent.guardrail_framework import (
    GuardrailFramework,
    GuardrailFrameworkGraphNode,
    GuardrailStage,
)
from veterinary_agent.nonmedical_pet_care_agent import (
    NonmedicalPetCareAgent,
    NonmedicalPetCareAgentGraphNode,
)
from veterinary_agent.safety_trigger_agent import (
    SafetyTriggerAgent,
    SafetyTriggerAgentGraphNode,
)
from veterinary_agent.standard_consultation_agent import (
    StandardConsultationAgent,
    StandardConsultationAgentGraphNode,
)
from veterinary_agent.vet_context_builder import (
    VetContextBuilder,
    VetContextBuilderGraphNode,
)
from veterinary_agent.vet_input_safety_assessor import (
    VetInputSafetyAssessor,
    VetInputSafetyAssessorGraphNode,
)
from veterinary_agent.vet_response_composer import (
    VetResponseComposer,
    VetResponseComposerGraphNode,
)
from veterinary_agent.vet_task_decomposer import (
    VetTaskDecomposer,
    VetTaskDecomposerGraphNode,
)

from veterinary_agent.vet_conversation_graph.state_adapters import (
    BranchStateBuilderGraphNode,
    ExecutorRouterGraphNode,
    GuardrailRequestBuilderGraphNode,
    TaskLaneSelectorGraphNode,
)
from veterinary_agent.vet_conversation_graph.todo_graph import (
    VET_CONVERSATION_GRAPH_ID,
    VET_CONVERSATION_GRAPH_VERSION,
    VET_CONVERSATION_STATE_SCHEMA_VERSION,
)

TASK_DECOMPOSER_NODE_ID = "vet_task_decomposer"
INPUT_SAFETY_NODE_ID = "vet_input_safety_assessor"
TASK_LANE_SELECTOR_NODE_ID = "task_lane_selector"
CONTEXT_BUILDER_NODE_ID = "vet_context_builder"
EXECUTOR_ROUTER_NODE_ID = "executor_router"
STANDARD_CONSULTATION_NODE_ID = "standard_consultation_agent"
EDUCATION_NODE_ID = "education_agent"
SAFETY_TRIGGER_NODE_ID = "safety_trigger_agent"
NONMEDICAL_PET_CARE_NODE_ID = "nonmedical_pet_care_agent"
POST_GENERATION_GUARD_REQUEST_NODE_ID = "post_generation_guard_request_builder"
POST_GENERATION_REVIEW_NODE_ID = "post_generation_review_guardrail"
DETERMINISTIC_GATE_REQUEST_NODE_ID = "deterministic_gate_request_builder"
DETERMINISTIC_GATE_NODE_ID = "deterministic_gate_guardrail"
BRANCH_STATE_BUILDER_NODE_ID = "branch_state_builder"
RESPONSE_COMPOSER_NODE_ID = "vet_response_composer"


def build_vet_conversation_graph_definition(
    *,
    task_decomposer: VetTaskDecomposer,
    input_safety_assessor: VetInputSafetyAssessor,
    context_builder: VetContextBuilder,
    standard_consultation_agent: StandardConsultationAgent,
    education_agent: EducationAgent,
    safety_trigger_agent: SafetyTriggerAgent,
    nonmedical_pet_care_agent: NonmedicalPetCareAgent,
    guardrail_framework: GuardrailFramework,
    response_composer: VetResponseComposer,
) -> GraphDefinition:
    """构建兽医主业务图真实接线定义。

    :param task_decomposer: 任务拆解服务。
    :param input_safety_assessor: 输入安全评估服务。
    :param context_builder: 上下文构建服务。
    :param standard_consultation_agent: 标准问诊业务 Agent。
    :param education_agent: 科普业务 Agent。
    :param safety_trigger_agent: 急症安全业务 Agent。
    :param nonmedical_pet_care_agent: 非医疗养宠业务 Agent。
    :param guardrail_framework: 护栏框架服务。
    :param response_composer: 回复合成发布服务。
    :return: 可注册到 GraphRuntime 的版本化主业务图定义。
    """

    return GraphDefinition(
        graph_id=VET_CONVERSATION_GRAPH_ID,
        graph_version=VET_CONVERSATION_GRAPH_VERSION,
        state_schema_version=VET_CONVERSATION_STATE_SCHEMA_VERSION,
        entry_node=TASK_DECOMPOSER_NODE_ID,
        nodes={
            TASK_DECOMPOSER_NODE_ID: GraphNodeSpec(
                node_id=TASK_DECOMPOSER_NODE_ID,
                handler=VetTaskDecomposerGraphNode(decomposer=task_decomposer),
            ),
            INPUT_SAFETY_NODE_ID: GraphNodeSpec(
                node_id=INPUT_SAFETY_NODE_ID,
                handler=VetInputSafetyAssessorGraphNode(assessor=input_safety_assessor),
            ),
            TASK_LANE_SELECTOR_NODE_ID: GraphNodeSpec(
                node_id=TASK_LANE_SELECTOR_NODE_ID,
                handler=TaskLaneSelectorGraphNode(),
            ),
            CONTEXT_BUILDER_NODE_ID: GraphNodeSpec(
                node_id=CONTEXT_BUILDER_NODE_ID,
                handler=VetContextBuilderGraphNode(builder=context_builder),
            ),
            EXECUTOR_ROUTER_NODE_ID: GraphNodeSpec(
                node_id=EXECUTOR_ROUTER_NODE_ID,
                handler=ExecutorRouterGraphNode(
                    standard_node_id=STANDARD_CONSULTATION_NODE_ID,
                    education_node_id=EDUCATION_NODE_ID,
                    safety_node_id=SAFETY_TRIGGER_NODE_ID,
                    nonmedical_node_id=NONMEDICAL_PET_CARE_NODE_ID,
                ),
            ),
            STANDARD_CONSULTATION_NODE_ID: GraphNodeSpec(
                node_id=STANDARD_CONSULTATION_NODE_ID,
                handler=StandardConsultationAgentGraphNode(
                    agent=standard_consultation_agent
                ),
            ),
            EDUCATION_NODE_ID: GraphNodeSpec(
                node_id=EDUCATION_NODE_ID,
                handler=EducationAgentGraphNode(agent=education_agent),
            ),
            SAFETY_TRIGGER_NODE_ID: GraphNodeSpec(
                node_id=SAFETY_TRIGGER_NODE_ID,
                handler=SafetyTriggerAgentGraphNode(agent=safety_trigger_agent),
            ),
            NONMEDICAL_PET_CARE_NODE_ID: GraphNodeSpec(
                node_id=NONMEDICAL_PET_CARE_NODE_ID,
                handler=NonmedicalPetCareAgentGraphNode(
                    agent=nonmedical_pet_care_agent
                ),
            ),
            POST_GENERATION_GUARD_REQUEST_NODE_ID: GraphNodeSpec(
                node_id=POST_GENERATION_GUARD_REQUEST_NODE_ID,
                handler=GuardrailRequestBuilderGraphNode(
                    stage=GuardrailStage.POST_GENERATION_REVIEW
                ),
            ),
            POST_GENERATION_REVIEW_NODE_ID: GraphNodeSpec(
                node_id=POST_GENERATION_REVIEW_NODE_ID,
                handler=GuardrailFrameworkGraphNode(
                    guardrail_framework=guardrail_framework,
                    stage=GuardrailStage.POST_GENERATION_REVIEW,
                    output_state_key="post_generation_review_result",
                ),
            ),
            DETERMINISTIC_GATE_REQUEST_NODE_ID: GraphNodeSpec(
                node_id=DETERMINISTIC_GATE_REQUEST_NODE_ID,
                handler=GuardrailRequestBuilderGraphNode(
                    stage=GuardrailStage.DETERMINISTIC_GATE,
                    previous_result_state_key="post_generation_review_result",
                ),
            ),
            DETERMINISTIC_GATE_NODE_ID: GraphNodeSpec(
                node_id=DETERMINISTIC_GATE_NODE_ID,
                handler=GuardrailFrameworkGraphNode(
                    guardrail_framework=guardrail_framework,
                    stage=GuardrailStage.DETERMINISTIC_GATE,
                    output_state_key="deterministic_gate_result",
                ),
            ),
            BRANCH_STATE_BUILDER_NODE_ID: GraphNodeSpec(
                node_id=BRANCH_STATE_BUILDER_NODE_ID,
                handler=BranchStateBuilderGraphNode(),
            ),
            RESPONSE_COMPOSER_NODE_ID: GraphNodeSpec(
                node_id=RESPONSE_COMPOSER_NODE_ID,
                handler=VetResponseComposerGraphNode(
                    composer=response_composer,
                    node_id=RESPONSE_COMPOSER_NODE_ID,
                ),
            ),
        },
        edges=(
            GraphEdgeSpec(
                from_node=TASK_DECOMPOSER_NODE_ID,
                to_node=INPUT_SAFETY_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=INPUT_SAFETY_NODE_ID,
                to_node=TASK_LANE_SELECTOR_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=TASK_LANE_SELECTOR_NODE_ID,
                to_node=CONTEXT_BUILDER_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=CONTEXT_BUILDER_NODE_ID,
                to_node=EXECUTOR_ROUTER_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=EXECUTOR_ROUTER_NODE_ID,
                to_node=STANDARD_CONSULTATION_NODE_ID,
                kind="conditional",
            ),
            GraphEdgeSpec(
                from_node=EXECUTOR_ROUTER_NODE_ID,
                to_node=EDUCATION_NODE_ID,
                kind="conditional",
            ),
            GraphEdgeSpec(
                from_node=EXECUTOR_ROUTER_NODE_ID,
                to_node=SAFETY_TRIGGER_NODE_ID,
                kind="conditional",
            ),
            GraphEdgeSpec(
                from_node=EXECUTOR_ROUTER_NODE_ID,
                to_node=NONMEDICAL_PET_CARE_NODE_ID,
                kind="conditional",
            ),
            GraphEdgeSpec(
                from_node=STANDARD_CONSULTATION_NODE_ID,
                to_node=POST_GENERATION_GUARD_REQUEST_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=EDUCATION_NODE_ID,
                to_node=POST_GENERATION_GUARD_REQUEST_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=SAFETY_TRIGGER_NODE_ID,
                to_node=POST_GENERATION_GUARD_REQUEST_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=NONMEDICAL_PET_CARE_NODE_ID,
                to_node=POST_GENERATION_GUARD_REQUEST_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=POST_GENERATION_GUARD_REQUEST_NODE_ID,
                to_node=POST_GENERATION_REVIEW_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=POST_GENERATION_REVIEW_NODE_ID,
                to_node=DETERMINISTIC_GATE_REQUEST_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=DETERMINISTIC_GATE_REQUEST_NODE_ID,
                to_node=DETERMINISTIC_GATE_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=DETERMINISTIC_GATE_NODE_ID,
                to_node=BRANCH_STATE_BUILDER_NODE_ID,
            ),
            GraphEdgeSpec(
                from_node=BRANCH_STATE_BUILDER_NODE_ID,
                to_node=RESPONSE_COMPOSER_NODE_ID,
            ),
        ),
    )


def build_vet_conversation_graph_registry(
    *,
    task_decomposer: VetTaskDecomposer,
    input_safety_assessor: VetInputSafetyAssessor,
    context_builder: VetContextBuilder,
    standard_consultation_agent: StandardConsultationAgent,
    education_agent: EducationAgent,
    safety_trigger_agent: SafetyTriggerAgent,
    nonmedical_pet_care_agent: NonmedicalPetCareAgent,
    guardrail_framework: GuardrailFramework,
    response_composer: VetResponseComposer,
) -> GraphRegistry:
    """构建已注册真实兽医主业务图的 GraphRegistry。

    :param task_decomposer: 任务拆解服务。
    :param input_safety_assessor: 输入安全评估服务。
    :param context_builder: 上下文构建服务。
    :param standard_consultation_agent: 标准问诊业务 Agent。
    :param education_agent: 科普业务 Agent。
    :param safety_trigger_agent: 急症安全业务 Agent。
    :param nonmedical_pet_care_agent: 非医疗养宠业务 Agent。
    :param guardrail_framework: 护栏框架服务。
    :param response_composer: 回复合成发布服务。
    :return: 已注册真实主业务图定义的注册表。
    """

    registry = GraphRegistry()
    registry.register(
        build_vet_conversation_graph_definition(
            task_decomposer=task_decomposer,
            input_safety_assessor=input_safety_assessor,
            context_builder=context_builder,
            standard_consultation_agent=standard_consultation_agent,
            education_agent=education_agent,
            safety_trigger_agent=safety_trigger_agent,
            nonmedical_pet_care_agent=nonmedical_pet_care_agent,
            guardrail_framework=guardrail_framework,
            response_composer=response_composer,
        )
    )
    return registry


__all__: tuple[str, ...] = (
    "BRANCH_STATE_BUILDER_NODE_ID",
    "CONTEXT_BUILDER_NODE_ID",
    "DETERMINISTIC_GATE_NODE_ID",
    "DETERMINISTIC_GATE_REQUEST_NODE_ID",
    "EDUCATION_NODE_ID",
    "EXECUTOR_ROUTER_NODE_ID",
    "INPUT_SAFETY_NODE_ID",
    "NONMEDICAL_PET_CARE_NODE_ID",
    "POST_GENERATION_GUARD_REQUEST_NODE_ID",
    "POST_GENERATION_REVIEW_NODE_ID",
    "RESPONSE_COMPOSER_NODE_ID",
    "SAFETY_TRIGGER_NODE_ID",
    "STANDARD_CONSULTATION_NODE_ID",
    "TASK_DECOMPOSER_NODE_ID",
    "TASK_LANE_SELECTOR_NODE_ID",
    "build_vet_conversation_graph_definition",
    "build_vet_conversation_graph_registry",
)
