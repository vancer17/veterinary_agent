##################################################################################################
# 文件: tests/vet_conversation_graph/helpers.py
# 作用: 提供兽医主业务图仿真全链路测试所需的场景、Fake L2 服务、轻量图执行器与 Runtime 装配工具。
# 边界: 仅用于测试主业务图接线与状态传递；不连接数据库、不调用 LLM/RAG、不实现真实兽医业务判断。
##################################################################################################

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from langgraph.checkpoint.memory import InMemorySaver

from tests.graph_runtime.helpers import CapturingCheckpointStore
from veterinary_agent.agent_application_service import (
    AgentGraphTurnRequestDto,
    AgentTurnExecutionContextDto,
    AgentTurnExecutionOptionsDto,
    AgentTurnInputItemDto,
    AgentTurnInputTextDto,
    AgentTurnPublishCapabilitiesDto,
)
from veterinary_agent.education_agent import (
    EducationAgent,
    EducationContentPlanDto,
    EducationDraftDto,
    EducationDraftStatus,
    EducationGenerationRequestDto,
    EducationTracePatchDto,
    EducationTraceWriteStatus,
    EvidenceBindingDto as EducationPublicEvidenceBindingDto,
    ExplanationDimensionCode,
    GroundingCheckSummaryDto,
    RagUsageSummaryDto as EducationPublicRagUsageSummaryDto,
)
from veterinary_agent.graph_runtime import (
    DefaultGraphRuntime,
    GraphDefinition,
    GraphNodeExecutionContext,
    GraphRuntimeSettings,
    GraphState,
)
from veterinary_agent.guardrail_framework import (
    GuardrailFramework,
    GuardrailRunRequestDto,
    GuardrailRunResultDto,
    GuardrailStage,
    GuardrailStatus,
)
from veterinary_agent.nonmedical_pet_care_agent import (
    AdviceConstraintDto,
    AdviceDimensionCode,
    AdviceDimensionDto,
    AdvicePlanDto,
    NonmedicalAdviceDraftDto,
    NonmedicalAdviceRequestDto,
    NonmedicalDraftStatus,
    NonmedicalPetCareAgent,
    NonmedicalTracePatchDto,
    NonmedicalTraceWriteStatus,
    PersonalizationLevel,
    PersonalizationPlanDto,
    RagUsageSummaryDto as NonmedicalPublicRagUsageSummaryDto,
    SafetySelfCheckSummaryDto,
)
from veterinary_agent.safety_trigger_agent import (
    ConfirmationMode,
    EmergencyBriefDto,
    EmergencyHintCode,
    KeyConfirmationPlanDto,
    SafetySignalSummaryDto,
    SafetyTraceWriteStatus,
    SafetyTriggerAgent,
    SafetyTriggerDraftDto,
    SafetyTriggerDraftStatus,
    SafetyTriggerRequestDto,
    SafetyTriggerSelfCheckSummaryDto,
    SafetyTriggerTracePatchDto,
)
from veterinary_agent.standard_consultation_agent import (
    ConsultationLayer,
    DraftStatus,
    StandardConsultationAgent,
    StandardConsultationDraftDto,
    StandardConsultationRequestDto,
    StandardTracePatchDto,
    StandardTraceWriteStatus,
)
from veterinary_agent.vet_context_builder import (
    CompressionAuditDto,
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextSourceFreshness,
    ContextSourceRefDto,
    ContextSourceStatus,
    ContextSourceType,
    ContextTraceWriteStatus,
    SlotCoverageDto,
    VetAuditTier,
    VetContextBuildRequestDto,
    VetContextBuilder,
    VetContextBundleDto,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockDto,
    VetPromptBlockPriority,
    VetPromptBlockType,
)
from veterinary_agent.vet_conversation_graph import (
    VET_CONVERSATION_GRAPH_ID,
    VET_CONVERSATION_GRAPH_VERSION,
    build_vet_conversation_graph_definition,
    build_vet_conversation_graph_registry,
)
from veterinary_agent.vet_input_safety_assessor import (
    AssessmentMethod,
    AssessmentStatus,
    AssessmentTraceSummaryDto,
    BatchVetInputAssessmentRequestDto,
    BatchVetInputAssessmentResultDto,
    DisambiguationMethod,
    InputSafetySignalDto,
    RouteLabel,
    SafetySignalCode,
    SignalSource,
    SignalStrength,
    VetInputAssessmentRequestDto,
    VetInputAssessmentResultDto,
    VetInputAssessmentTraceWriteStatus,
    VetInputSafetyAssessor,
    VetIntent,
)
from veterinary_agent.vet_response_composer import (
    BranchExecutionStateDto,
    ComposerBranchType,
    ComposerPublishStatus,
    ComposerTracePatchDto,
    ComposerTraceWriteStatus,
    ComposeTurnRequestDto,
    ComposeTurnResultDto,
    ResponseSegmentDto,
    TurnCompositionStateDto,
    VetResponseComposer,
)
from veterinary_agent.vet_task_decomposer import (
    DecompositionMethod,
    DecompositionStatus,
    DecompositionTraceSummaryDto,
    TaskPriorityHint,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskDecomposeRequestDto,
    VetTaskDecomposeResultDto,
    VetTaskDecomposer,
    VetTaskTraceWriteStatus,
    VetTaskType,
    build_text_hash,
)


@dataclass(frozen=True, slots=True)
class SimulatedBusinessScenario:
    """兽医主业务图仿真测试场景。"""

    name: str
    user_message: str
    task_type: VetTaskType
    intent: VetIntent
    executor_key: VetExecutorKey
    generation_profile: VetGenerationProfile | None
    route: RouteLabel
    compression_strategy: ContextCompressionStrategy
    audit_tier: VetAuditTier
    gate_allows_publish: bool = True


@dataclass(slots=True)
class SimulatedBusinessFakes:
    """主业务图仿真测试使用的 Fake 服务集合。"""

    task_decomposer: "FakeTaskDecomposer"
    input_safety_assessor: "FakeInputSafetyAssessor"
    context_builder: "FakeContextBuilder"
    standard_agent: "FakeStandardConsultationAgent"
    education_agent: "FakeEducationAgent"
    safety_agent: "FakeSafetyTriggerAgent"
    nonmedical_agent: "FakeNonmedicalPetCareAgent"
    guardrail_framework: "FakeGuardrailFramework"
    response_composer: "FakeResponseComposer"


@dataclass(frozen=True, slots=True)
class SimulatedBusinessGraphFixture:
    """主业务图仿真测试夹具。"""

    scenario: SimulatedBusinessScenario
    fakes: SimulatedBusinessFakes
    definition: GraphDefinition


@dataclass(frozen=True, slots=True)
class SimulatedRuntimeFixture:
    """真实 GraphRuntime 仿真测试夹具。"""

    scenario: SimulatedBusinessScenario
    fakes: SimulatedBusinessFakes
    runtime: DefaultGraphRuntime
    checkpoint_store: CapturingCheckpointStore


@dataclass(frozen=True, slots=True)
class SimulatedGraphRunResult:
    """轻量图执行器返回的主业务图执行摘要。"""

    state: GraphState
    completed_node_ids: tuple[str, ...]


def build_standard_scenario(
    *, gate_allows_publish: bool = True
) -> SimulatedBusinessScenario:
    """构建标准问诊仿真场景。

    :param gate_allows_publish: 确定性发布门是否允许发布业务草稿。
    :return: 标准问诊仿真场景。
    """

    return SimulatedBusinessScenario(
        name="standard",
        user_message="狗狗今天有点咳嗽，精神还可以，需要怎么观察？",
        task_type=VetTaskType.TRIAGE,
        intent=VetIntent.SYMPTOM_TRIAGE,
        executor_key=VetExecutorKey.STANDARD_CONSULTATION,
        generation_profile=VetGenerationProfile.STANDARD,
        route=RouteLabel.NORMAL,
        compression_strategy=ContextCompressionStrategy.SINGLE_FULL,
        audit_tier=VetAuditTier.B,
        gate_allows_publish=gate_allows_publish,
    )


def build_education_scenario() -> SimulatedBusinessScenario:
    """构建科普仿真场景。

    :return: 科普仿真场景。
    """

    return SimulatedBusinessScenario(
        name="education",
        user_message="想了解猫咪为什么会吐毛球。",
        task_type=VetTaskType.EDUCATION_QA,
        intent=VetIntent.EDUCATION,
        executor_key=VetExecutorKey.EDUCATION,
        generation_profile=VetGenerationProfile.EDUCATION,
        route=RouteLabel.NORMAL,
        compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
        audit_tier=VetAuditTier.C,
    )


def build_safety_scenario() -> SimulatedBusinessScenario:
    """构建急症安全仿真场景。

    :return: 急症安全仿真场景。
    """

    return SimulatedBusinessScenario(
        name="safety",
        user_message="狗狗突然抽搐并且站不稳，现在还在发抖。",
        task_type=VetTaskType.TRIAGE,
        intent=VetIntent.ACUTE_EVENT,
        executor_key=VetExecutorKey.SAFETY_TRIGGER,
        generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
        route=RouteLabel.SAFETY_TRIGGER,
        compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
        audit_tier=VetAuditTier.A,
    )


def build_nonmedical_scenario() -> SimulatedBusinessScenario:
    """构建非医疗养宠仿真场景。

    :return: 非医疗养宠仿真场景。
    """

    return SimulatedBusinessScenario(
        name="nonmedical",
        user_message="幼犬换粮怎么安排更稳妥？",
        task_type=VetTaskType.NUTRITION,
        intent=VetIntent.NONMED_NUTRITION,
        executor_key=VetExecutorKey.NONMEDICAL_PET_CARE,
        generation_profile=None,
        route=RouteLabel.NORMAL,
        compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
        audit_tier=VetAuditTier.C,
    )


def build_simulated_business_graph_fixture(
    scenario: SimulatedBusinessScenario,
) -> SimulatedBusinessGraphFixture:
    """构建使用 Fake L2 服务的主业务图定义夹具。

    :param scenario: 当前仿真业务场景。
    :return: 主业务图仿真测试夹具。
    """

    fakes = build_simulated_business_fakes(scenario)
    definition = build_vet_conversation_graph_definition(
        task_decomposer=cast(VetTaskDecomposer, fakes.task_decomposer),
        input_safety_assessor=cast(
            VetInputSafetyAssessor,
            fakes.input_safety_assessor,
        ),
        context_builder=cast(VetContextBuilder, fakes.context_builder),
        standard_consultation_agent=cast(
            StandardConsultationAgent,
            fakes.standard_agent,
        ),
        education_agent=cast(EducationAgent, fakes.education_agent),
        safety_trigger_agent=cast(SafetyTriggerAgent, fakes.safety_agent),
        nonmedical_pet_care_agent=cast(
            NonmedicalPetCareAgent,
            fakes.nonmedical_agent,
        ),
        guardrail_framework=cast(GuardrailFramework, fakes.guardrail_framework),
        response_composer=cast(VetResponseComposer, fakes.response_composer),
    )
    return SimulatedBusinessGraphFixture(
        scenario=scenario,
        fakes=fakes,
        definition=definition,
    )


def build_simulated_runtime_fixture(
    scenario: SimulatedBusinessScenario,
) -> SimulatedRuntimeFixture:
    """构建真实 DefaultGraphRuntime 仿真功能测试夹具。

    :param scenario: 当前仿真业务场景。
    :return: 已注入 InMemorySaver 与 Fake L2 服务的 Runtime 夹具。
    """

    fakes = build_simulated_business_fakes(scenario)
    registry = build_vet_conversation_graph_registry(
        task_decomposer=cast(VetTaskDecomposer, fakes.task_decomposer),
        input_safety_assessor=cast(
            VetInputSafetyAssessor,
            fakes.input_safety_assessor,
        ),
        context_builder=cast(VetContextBuilder, fakes.context_builder),
        standard_consultation_agent=cast(
            StandardConsultationAgent,
            fakes.standard_agent,
        ),
        education_agent=cast(EducationAgent, fakes.education_agent),
        safety_trigger_agent=cast(SafetyTriggerAgent, fakes.safety_agent),
        nonmedical_pet_care_agent=cast(
            NonmedicalPetCareAgent,
            fakes.nonmedical_agent,
        ),
        guardrail_framework=cast(GuardrailFramework, fakes.guardrail_framework),
        response_composer=cast(VetResponseComposer, fakes.response_composer),
    )
    checkpoint_store = CapturingCheckpointStore()
    runtime = DefaultGraphRuntime(
        checkpoint_store=checkpoint_store,
        checkpointer=InMemorySaver(),
        graph_registry=registry,
        settings=GraphRuntimeSettings(
            graph_id=VET_CONVERSATION_GRAPH_ID,
            graph_version=VET_CONVERSATION_GRAPH_VERSION,
            run_deadline_seconds=10.0,
        ),
    )
    return SimulatedRuntimeFixture(
        scenario=scenario,
        fakes=fakes,
        runtime=runtime,
        checkpoint_store=checkpoint_store,
    )


def build_simulated_business_fakes(
    scenario: SimulatedBusinessScenario,
) -> SimulatedBusinessFakes:
    """构建主业务图仿真测试所需的 Fake 服务集合。

    :param scenario: 当前仿真业务场景。
    :return: Fake L2 服务集合。
    """

    return SimulatedBusinessFakes(
        task_decomposer=FakeTaskDecomposer(scenario),
        input_safety_assessor=FakeInputSafetyAssessor(scenario),
        context_builder=FakeContextBuilder(),
        standard_agent=FakeStandardConsultationAgent(),
        education_agent=FakeEducationAgent(),
        safety_agent=FakeSafetyTriggerAgent(),
        nonmedical_agent=FakeNonmedicalPetCareAgent(),
        guardrail_framework=FakeGuardrailFramework(scenario),
        response_composer=FakeResponseComposer(),
    )


def build_simulated_turn_request(
    scenario: SimulatedBusinessScenario,
    *,
    run_id: str = "run_1",
) -> AgentGraphTurnRequestDto:
    """构建主业务图仿真测试使用的 GraphRuntime 请求。

    :param scenario: 当前仿真业务场景。
    :param run_id: 当前图运行 ID。
    :return: 可传给 GraphRuntime 或轻量 driver 的请求 DTO。
    """

    return AgentGraphTurnRequestDto(
        context=AgentTurnExecutionContextDto(
            request_id=f"req_{scenario.name}",
            trace_id=f"trace_{scenario.name}",
            turn_id=f"turn_{scenario.name}",
            run_id=run_id,
            session_id="session_1",
            user_id="user_1",
            current_pet_id="pet_1",
            user_message_id=f"msg_{scenario.name}",
            idempotency_key=f"idem_{scenario.name}_{run_id}",
            params_version="params.v1",
            config_snapshot_id="config_1",
            response_mode="sync",
            route_kind="agent_turns",
        ),
        input=[
            AgentTurnInputItemDto(
                content=[AgentTurnInputTextDto(text=scenario.user_message)]
            )
        ],
        attachments=[],
        metadata={"simulated_scenario": scenario.name},
        execution_options=AgentTurnExecutionOptionsDto(
            orchestrator_target="local",
            connect_timeout_seconds=1,
            request_timeout_seconds=10,
            stream_first_event_timeout_seconds=1,
            stream_total_timeout_seconds=10,
            heartbeat_enabled=True,
            heartbeat_interval_seconds=1,
            stream_idle_timeout_seconds=10,
            max_stream_duration_seconds=30,
            max_event_bytes=8192,
            client_cancel_notify_timeout_seconds=1,
        ),
        publish_capabilities=AgentTurnPublishCapabilitiesDto(
            supports_segments=True,
            supports_reasoning_display=True,
            supports_sse_events=True,
        ),
    )


async def run_definition_to_completion(
    *,
    definition: GraphDefinition,
    request: AgentGraphTurnRequestDto,
) -> SimulatedGraphRunResult:
    """使用轻量单通道 driver 执行主业务图定义。

    :param definition: 需要执行的主业务图定义。
    :param request: 当前图运行请求。
    :return: 轻量执行器收集到的最终状态与节点顺序。
    :raises AssertionError: 当图产生多后继或非法条件后继时抛出。
    """

    state = _initial_graph_state_from_request(request)
    completed_node_ids: list[str] = []
    node_id = definition.entry_node
    while True:
        node = definition.get_node(node_id)
        result = await node.handler(
            state,
            _build_node_context(request=request, node_id=node_id),
        )
        state.update(result.state_patch)
        completed_node_ids.append(node_id)
        next_node_ids = _next_node_ids(
            definition=definition,
            node_id=node_id,
            selected_next_nodes=result.selected_next_nodes,
        )
        if not next_node_ids:
            return SimulatedGraphRunResult(
                state=state,
                completed_node_ids=tuple(completed_node_ids),
            )
        assert len(next_node_ids) == 1, "仿真 driver 仅支持 MVP 单通道后继"
        node_id = next_node_ids[0]


def _next_node_ids(
    *,
    definition: GraphDefinition,
    node_id: str,
    selected_next_nodes: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """解析轻量 driver 的下一跳节点。

    :param definition: 当前主业务图定义。
    :param node_id: 当前节点 ID。
    :param selected_next_nodes: 当前节点显式选择的条件后继节点。
    :return: 下一跳节点 ID 元组。
    :raises AssertionError: 当条件后继未在图定义中声明时抛出。
    """

    if selected_next_nodes is None:
        return definition.static_next_node_ids(node_id)
    allowed_nodes = set(definition.conditional_next_node_ids(node_id))
    for selected_node_id in selected_next_nodes:
        assert selected_node_id in allowed_nodes
    return selected_next_nodes


def _initial_graph_state_from_request(request: AgentGraphTurnRequestDto) -> GraphState:
    """从 GraphRuntime 请求构建轻量 driver 初始 state。

    :param request: 当前图运行请求。
    :return: 与 GraphRuntime 投影兼容的初始业务状态。
    """

    request_context = request.context
    return {
        "request": {
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
        },
        "input_count": len(request.input),
        "attachment_count": len(request.attachments),
    }


def _build_node_context(
    *,
    request: AgentGraphTurnRequestDto,
    node_id: str,
) -> GraphNodeExecutionContext:
    """构建轻量 driver 调用节点时使用的执行上下文。

    :param request: 当前图运行请求。
    :param node_id: 当前节点 ID。
    :return: GraphRuntime 节点执行上下文。
    """

    context = request.context
    return GraphNodeExecutionContext(
        request_id=context.request_id,
        trace_id=context.trace_id,
        run_id=context.run_id,
        graph_id=VET_CONVERSATION_GRAPH_ID,
        graph_version=VET_CONVERSATION_GRAPH_VERSION,
        node_id=node_id,
        session_id=context.session_id,
        user_id=context.user_id,
        current_pet_id=context.current_pet_id,
        params_version=context.params_version,
        config_snapshot_id=context.config_snapshot_id,
        thread_id="unit_thread_1",
    )


def _hash_block_content(content: str) -> str:
    """构建 VetPromptBlock 要求的内容 hash。

    :param content: 需要计算摘要的块正文。
    :return: 带 sha256 前缀的块正文摘要。
    """

    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


def _signal_codes_for_scenario(
    scenario: SimulatedBusinessScenario,
) -> list[SafetySignalCode]:
    """根据仿真场景构建输入安全信号码。

    :param scenario: 当前仿真业务场景。
    :return: 输入安全信号码列表。
    """

    if scenario.executor_key is VetExecutorKey.SAFETY_TRIGGER:
        return [SafetySignalCode.SAF_03_ACUTE_RED_FLAG]
    if scenario.executor_key is VetExecutorKey.EDUCATION:
        return [SafetySignalCode.EDUCATION_MARKER]
    return []


def _signals_for_scenario(
    scenario: SimulatedBusinessScenario,
) -> list[InputSafetySignalDto]:
    """根据仿真场景构建输入安全信号列表。

    :param scenario: 当前仿真业务场景。
    :return: 输入安全信号 DTO 列表。
    """

    return [
        InputSafetySignalDto(
            signal_id=f"signal_{signal_code.value.lower()}",
            code=signal_code,
            strength=SignalStrength.L3
            if signal_code is SafetySignalCode.SAF_03_ACUTE_RED_FLAG
            else SignalStrength.NOT_APPLICABLE,
            matched_text_hash=build_text_hash(scenario.user_message),
            normalized_concept=signal_code.value.lower(),
            source=SignalSource.DETERMINISTIC,
            confidence=0.99,
            dictionary_version="simulated.input-safety.v1",
        )
        for signal_code in _signal_codes_for_scenario(scenario)
    ]


def _assessment_summary_for_scenario(
    scenario: SimulatedBusinessScenario,
) -> dict[str, object]:
    """构建下游节点消费的输入安全摘要。

    :param scenario: 当前仿真业务场景。
    :return: 输入安全摘要映射。
    """

    return {
        "intent": scenario.intent.value,
        "executor_key": scenario.executor_key.value,
        "generation_profile": (
            scenario.generation_profile.value
            if scenario.generation_profile is not None
            else None
        ),
        "route": scenario.route.value,
        "signals": [
            signal_code.value for signal_code in _signal_codes_for_scenario(scenario)
        ],
        "simulated": True,
    }


def _task_type_for_scenario(scenario: SimulatedBusinessScenario) -> VetTaskType:
    """读取场景声明的任务类型。

    :param scenario: 当前仿真业务场景。
    :return: 任务拆解 DTO 使用的任务类型。
    """

    return scenario.task_type


def _draft_text(prefix: str, request_text: str) -> str:
    """构建仿真业务 Agent 草稿正文。

    :param prefix: 当前业务 Agent 的文案前缀。
    :param request_text: 当前用户请求文本。
    :return: 可进入输出护栏的草稿正文。
    """

    return f"{prefix}：已收到「{request_text}」，这是仿真主业务图草稿。"


class FakeTaskDecomposer:
    """仿真任务拆解服务。"""

    def __init__(self, scenario: SimulatedBusinessScenario) -> None:
        """初始化仿真任务拆解服务。

        :param scenario: 当前仿真业务场景。
        :return: None。
        """

        self._scenario = scenario
        self.calls: list[VetTaskDecomposeRequestDto] = []

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> VetTaskDecomposeResultDto:
        """返回单任务拆解结果。

        :param request: 任务拆解请求。
        :return: 单任务拆解结果 DTO。
        """

        self.calls.append(request)
        normalized_query = request.user_message or self._scenario.user_message
        task = VetSubTaskDto(
            task_id="task_primary",
            task_type=_task_type_for_scenario(self._scenario),
            current_pet_id=request.current_pet_id or "pet_1",
            source_span=TextSpanDto(
                start_offset=0,
                end_offset=max(1, len(normalized_query)),
                text_hash=build_text_hash(normalized_query or "simulated"),
            ),
            normalized_query=normalized_query or self._scenario.user_message,
            priority_hint=TaskPriorityHint.URGENT
            if self._scenario.executor_key is VetExecutorKey.SAFETY_TRIGGER
            else TaskPriorityHint.ROUTINE,
            confidence=0.99,
        )
        return VetTaskDecomposeResultDto(
            tasks=[task],
            status=DecompositionStatus.SUCCEEDED,
            trace_summary=DecompositionTraceSummaryDto(
                decomposer_version="simulated.task-decomposer.v1",
                method=DecompositionMethod.SINGLE_PASSTHROUGH,
                task_count=1,
                task_types=[task.task_type],
                llm_unavailable=False,
                fallback_used=False,
                confidence=0.99,
            ),
            trace_delivery_status=VetTaskTraceWriteStatus.SKIPPED,
        )


class FakeInputSafetyAssessor:
    """仿真输入安全评估服务。"""

    def __init__(self, scenario: SimulatedBusinessScenario) -> None:
        """初始化仿真输入安全评估服务。

        :param scenario: 当前仿真业务场景。
        :return: None。
        """

        self._scenario = scenario
        self.assess_calls: list[VetInputAssessmentRequestDto] = []
        self.batch_calls: list[BatchVetInputAssessmentRequestDto] = []

    async def assess(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> VetInputAssessmentResultDto:
        """返回单任务输入安全评估结果。

        :param request: 单任务输入安全评估请求。
        :return: 单任务输入安全评估结果。
        """

        self.assess_calls.append(request)
        return self._build_result(task=request.task)

    async def batch_assess(
        self,
        request: BatchVetInputAssessmentRequestDto,
    ) -> BatchVetInputAssessmentResultDto:
        """返回批量输入安全评估结果。

        :param request: 批量输入安全评估请求。
        :return: 批量输入安全评估结果。
        """

        self.batch_calls.append(request)
        return BatchVetInputAssessmentResultDto(
            results=[self._build_result(task=task) for task in request.tasks],
            status=AssessmentStatus.SUCCEEDED,
            trace_delivery_status=VetInputAssessmentTraceWriteStatus.SKIPPED,
        )

    def _build_result(self, *, task: VetSubTaskDto) -> VetInputAssessmentResultDto:
        """构建单个任务的输入安全评估结果。

        :param task: 当前待评估子任务。
        :return: 输入安全评估结果 DTO。
        """

        signal_codes = _signal_codes_for_scenario(self._scenario)
        return VetInputAssessmentResultDto(
            task_id=task.task_id,
            current_pet_id=task.current_pet_id,
            status=AssessmentStatus.SUCCEEDED,
            signals=_signals_for_scenario(self._scenario),
            intent=self._scenario.intent,
            intent_confidence=0.98,
            generation_profile=self._scenario.generation_profile,
            route=self._scenario.route,
            executor_key=self._scenario.executor_key,
            compression_strategy=self._scenario.compression_strategy,
            disambiguation_method=DisambiguationMethod.EXPLICIT,
            audit_tier_floor=self._scenario.audit_tier,
            assessment_summary=_assessment_summary_for_scenario(self._scenario),
            trace_summary=AssessmentTraceSummaryDto(
                assessor_version="simulated.input-safety.v1",
                method=AssessmentMethod.DETERMINISTIC,
                llm_unavailable=False,
                semantic_router_unavailable=False,
                local_extractor_unavailable=False,
                fallback_used=False,
                signal_codes=signal_codes,
                final_decision_reason_code=f"simulated_{self._scenario.name}",
            ),
            trace_delivery_status=VetInputAssessmentTraceWriteStatus.SKIPPED,
        )


class FakeContextBuilder:
    """仿真上下文构建服务。"""

    def __init__(self) -> None:
        """初始化仿真上下文构建服务。

        :return: None。
        """

        self.calls: list[VetContextBuildRequestDto] = []

    async def build(
        self,
        request: VetContextBuildRequestDto,
    ) -> VetContextBundleDto:
        """返回最小合法上下文 bundle。

        :param request: 上下文构建请求。
        :return: VetContextBuilder 输出 bundle。
        """

        self.calls.append(request)
        block_text = f"任务: {request.normalized_query}"
        source_ref = ContextSourceRefDto(
            source_type=ContextSourceType.CURRENT_TASK,
            source_id=request.task_id,
            pet_id=request.current_pet_id,
            version="simulated.context.v1",
            freshness=ContextSourceFreshness.FRESH,
            status=ContextSourceStatus.AVAILABLE,
        )
        block = VetPromptBlockDto(
            block_id=f"block_{request.task_id}",
            block_type=VetPromptBlockType.TASK_INPUT,
            priority=VetPromptBlockPriority.P0,
            required=True,
            content_ref_or_text=block_text,
            content_hash=_hash_block_content(block_text),
            token_estimate=16,
            source_refs=[source_ref],
            metadata={"simulated": True},
        )
        return VetContextBundleDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id,
            generation_profile=request.generation_profile,
            executor_key=request.executor_key,
            prompt_blocks=[block],
            fact_ledger=[],
            slot_coverage=SlotCoverageDto(task_id=request.task_id),
            source_refs=[source_ref],
            compression_audit=CompressionAuditDto(
                compression_strategy=request.compression_strategy,
                token_budget=512,
                estimated_tokens=16,
                trim_applied=False,
                p0_reinjected=False,
                included_block_ids=[block.block_id],
            ),
            status=ContextBuildStatus.FULL,
            degraded_reasons=[],
            core_fact_snapshot_version="simulated.core-facts.v1",
            trace_delivery_status=ContextTraceWriteStatus.SKIPPED,
        )


class FakeStandardConsultationAgent:
    """仿真标准问诊 Agent。"""

    def __init__(self) -> None:
        """初始化仿真标准问诊 Agent。

        :return: None。
        """

        self.calls: list[StandardConsultationRequestDto] = []

    async def generate_draft(
        self,
        request: StandardConsultationRequestDto,
    ) -> StandardConsultationDraftDto:
        """返回标准问诊草稿。

        :param request: 标准问诊请求。
        :return: 标准问诊草稿 DTO。
        """

        self.calls.append(request)
        return StandardConsultationDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or "pet_1",
            status=DraftStatus.DRAFT_READY,
            draft_response=_draft_text("标准问诊", request.normalized_query),
            draft_response_ref=f"draft:{request.task_id}:standard",
            reached_layer=ConsultationLayer.L2_DIRECTION,
            trace_patch=StandardTracePatchDto(
                standard_agent_version="simulated.standard.v1",
                orchestrator_version="simulated.standard-orchestrator.v1",
                layer_before=ConsultationLayer.L0_COLLECTION,
                layer_after=ConsultationLayer.L2_DIRECTION,
                activated_agents=["simulated_standard_writer"],
            ),
            trace_delivery_status=StandardTraceWriteStatus.SKIPPED,
        )


class FakeEducationAgent:
    """仿真科普 Agent。"""

    def __init__(self) -> None:
        """初始化仿真科普 Agent。

        :return: None。
        """

        self.calls: list[EducationGenerationRequestDto] = []

    async def generate_draft(
        self,
        request: EducationGenerationRequestDto,
    ) -> EducationDraftDto:
        """返回科普草稿。

        :param request: 科普生成请求。
        :return: 科普草稿 DTO。
        """

        self.calls.append(request)
        return EducationDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or "pet_1",
            status=EducationDraftStatus.DRAFT_READY,
            draft_response=_draft_text("科普说明", request.normalized_query),
            draft_response_ref=f"draft:{request.task_id}:education",
            content_plan=EducationContentPlanDto(
                main_axis="毛球形成原因",
                section_titles=["形成原因", "观察边界"],
                selected_dimensions=[ExplanationDimensionCode.DEFINITION],
                safety_boundary_hints=["出现频繁呕吐时需要就医"],
            ),
            evidence_bindings=[
                EducationPublicEvidenceBindingDto(
                    claim_id="claim_education_1",
                    evidence_card_ids=["evidence_card_1"],
                    retrieval_ids=["retrieval_education_1"],
                    binding_summary="仿真证据绑定。",
                )
            ],
            rag_summary=EducationPublicRagUsageSummaryDto(
                rag_invoked=True,
                retrieval_ids=["retrieval_education_1"],
                query_hashes=["hash_education_1"],
            ),
            grounding_check=GroundingCheckSummaryDto(),
            trace_patch=EducationTracePatchDto(
                education_agent_version="simulated.education.v1",
                planner_version="simulated.education-planner.v1",
                writer_version="simulated.education-writer.v1",
                selected_dimensions=[ExplanationDimensionCode.DEFINITION],
                retrieval_ids=["retrieval_education_1"],
            ),
            trace_delivery_status=EducationTraceWriteStatus.SKIPPED,
        )


class FakeSafetyTriggerAgent:
    """仿真急症安全 Agent。"""

    def __init__(self) -> None:
        """初始化仿真急症安全 Agent。

        :return: None。
        """

        self.calls: list[SafetyTriggerRequestDto] = []

    async def generate_draft(
        self,
        request: SafetyTriggerRequestDto,
    ) -> SafetyTriggerDraftDto:
        """返回急症安全草稿。

        :param request: 急症安全请求。
        :return: 急症安全草稿 DTO。
        """

        self.calls.append(request)
        return SafetyTriggerDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or "pet_1",
            status=SafetyTriggerDraftStatus.DRAFT_READY,
            draft_response=_draft_text("急症安全提示", request.normalized_query),
            draft_response_ref=f"draft:{request.task_id}:safety",
            emergency_brief=EmergencyBriefDto(
                user_text_ref=f"user-text:{request.task_id}",
                species_scope="dog",
                signal_summaries=[
                    SafetySignalSummaryDto(
                        signal_id="signal_safety_1",
                        signal_code=SafetySignalCode.SAF_03_ACUTE_RED_FLAG.value,
                        signal_strength=SignalStrength.L3.value,
                        normalized_concept="acute_red_flag",
                        evidence_text_hash=build_text_hash(request.normalized_query),
                        dictionary_version="simulated.safety.v1",
                    )
                ],
                emergency_hint_codes=[EmergencyHintCode.UNKNOWN_RED_FLAG_HINT],
            ),
            confirmation_plan=KeyConfirmationPlanDto(
                mode=ConfirmationMode.NO_QUESTION,
                reason_code="simulated_no_question",
            ),
            urgency_statement="这类表现需要按急症处理。",
            vet_direction="请尽快联系线下兽医或急诊。",
            safe_actions=["保持环境安静并记录发作时长。"],
            forbidden_actions=["不要自行喂药。"],
            info_to_prepare=["发作开始时间", "视频记录"],
            self_check=SafetyTriggerSelfCheckSummaryDto(
                vet_direction_present=True,
                confirmation_count_valid=True,
                rag_invocation_absent=True,
                t4_risk_detected=False,
                differential_overexpanded=False,
                fallback_recommended=False,
            ),
            trace_patch=SafetyTriggerTracePatchDto(
                safety_trigger_agent_version="simulated.safety.v1",
                writer_version="simulated.safety-writer.v1",
                confirmation_planner_version="simulated.confirmation.v1",
                fallback_template_version="simulated.safety-template.v1",
                requirement_set_version="simulated.requirements.v1",
                signal_codes=[SafetySignalCode.SAF_03_ACUTE_RED_FLAG.value],
                emergency_hint_codes=[EmergencyHintCode.UNKNOWN_RED_FLAG_HINT],
                confirmation_mode=ConfirmationMode.NO_QUESTION,
                template_fallback_used=False,
            ),
            trace_delivery_status=SafetyTraceWriteStatus.SKIPPED,
        )


class FakeNonmedicalPetCareAgent:
    """仿真非医疗养宠 Agent。"""

    def __init__(self) -> None:
        """初始化仿真非医疗养宠 Agent。

        :return: None。
        """

        self.calls: list[NonmedicalAdviceRequestDto] = []

    async def generate_draft(
        self,
        request: NonmedicalAdviceRequestDto,
    ) -> NonmedicalAdviceDraftDto:
        """返回非医疗养宠建议草稿。

        :param request: 非医疗养宠请求。
        :return: 非医疗养宠建议草稿 DTO。
        """

        self.calls.append(request)
        return NonmedicalAdviceDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or "pet_1",
            status=NonmedicalDraftStatus.DRAFT_READY,
            draft_response=_draft_text("日常养宠建议", request.normalized_query),
            draft_response_ref=f"draft:{request.task_id}:nonmedical",
            advice_plan=AdvicePlanDto(
                advice_axis="换粮节奏",
                dimensions=[
                    AdviceDimensionDto(
                        dimension_code=AdviceDimensionCode.STEPWISE_PLAN,
                        priority=1,
                        required=True,
                        evidence_requirement="仿真规则证据。",
                    )
                ],
                generation_constraints=["不得输出医疗诊断。"],
            ),
            advice_constraints=[
                AdviceConstraintDto(
                    constraint_id="constraint_nonmedical_1",
                    constraint_type="gradual_change",
                    constraint_summary="换粮应循序渐进。",
                    evidence_card_ids=["rule_nonmedical_1"],
                )
            ],
            personalization_plan=PersonalizationPlanDto(
                personalization_level=PersonalizationLevel.MINIMAL,
                unavailable_factors=["age_months"],
            ),
            rag_summary=NonmedicalPublicRagUsageSummaryDto(rag_invoked=False),
            self_check=SafetySelfCheckSummaryDto(passed=True),
            trace_patch=NonmedicalTracePatchDto(
                nonmedical_agent_version="simulated.nonmedical.v1",
                planner_version="simulated.nonmedical-planner.v1",
                writer_version="simulated.nonmedical-writer.v1",
                selected_dimensions=[AdviceDimensionCode.STEPWISE_PLAN],
            ),
            trace_delivery_status=NonmedicalTraceWriteStatus.SKIPPED,
        )


class FakeGuardrailFramework:
    """仿真 GuardrailFramework 服务。"""

    def __init__(self, scenario: SimulatedBusinessScenario) -> None:
        """初始化仿真护栏框架服务。

        :param scenario: 当前仿真业务场景。
        :return: None。
        """

        self._scenario = scenario
        self.calls: list[GuardrailRunRequestDto] = []

    async def run_pre_generation_guard(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """返回前置护栏允许结果。

        :param request: 前置护栏请求。
        :return: 护栏允许结果。
        """

        return await self.run_guardrail_stage(request)

    async def run_post_generation_review(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """返回后置输出审查结果。

        :param request: 后置输出审查请求。
        :return: 护栏审查结果。
        """

        return await self.run_guardrail_stage(request)

    async def run_deterministic_gate(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """返回确定性发布门结果。

        :param request: 确定性发布门请求。
        :return: 护栏发布门结果。
        """

        return await self.run_guardrail_stage(request)

    async def run_guardrail_stage(
        self,
        request: GuardrailRunRequestDto,
    ) -> GuardrailRunResultDto:
        """根据阶段返回可预测护栏结果。

        :param request: 护栏运行请求。
        :return: 当前阶段仿真结果。
        """

        self.calls.append(request)
        if request.stage is GuardrailStage.POST_GENERATION_REVIEW:
            return GuardrailRunResultDto(
                status=GuardrailStatus.ALLOWED,
                reviewed_text_ref=f"reviewed:{request.context.task_id}",
                publish_allowed=False,
                metadata={"simulated_stage": request.stage.value},
            )
        if request.stage is GuardrailStage.DETERMINISTIC_GATE:
            if self._scenario.gate_allows_publish:
                return GuardrailRunResultDto(
                    status=GuardrailStatus.ALLOWED,
                    final_text_ref=f"final:{request.context.task_id}",
                    publish_allowed=True,
                    metadata={"simulated_stage": request.stage.value},
                )
            return GuardrailRunResultDto(
                status=GuardrailStatus.BLOCKED,
                publish_allowed=False,
                metadata={"simulated_stage": request.stage.value},
            )
        return GuardrailRunResultDto(
            status=GuardrailStatus.ALLOWED,
            publish_allowed=False,
            metadata={"simulated_stage": request.stage.value},
        )


class FakeResponseComposer:
    """仿真回复合成服务。"""

    def __init__(self) -> None:
        """初始化仿真回复合成服务。

        :return: None。
        """

        self.calls: list[ComposeTurnRequestDto] = []

    async def compose_turn_response(
        self,
        request: ComposeTurnRequestDto,
    ) -> ComposeTurnResultDto:
        """根据主图分支状态返回最终回复。

        :param request: 回复合成请求。
        :return: 仿真回复合成结果。
        """

        self.calls.append(request)
        branches = self._read_branches(request)
        segments = [
            self._segment_from_branch(branch=branch, order_index=index)
            for index, branch in enumerate(branches)
            if branch.publishable_segment is not None
        ]
        output_text = "\n\n".join(segment.content for segment in segments)
        fallback_segment_ids = [
            segment.segment_id
            for segment in segments
            if bool(segment.metadata.get("fallback_triggered"))
        ]
        turn_state = TurnCompositionStateDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            current_pet_id=request.current_pet_id,
            thread_id=request.thread_id,
            branches=branches,
            segments=segments,
            final_response_text=output_text,
            turn_audit_tier=segments[0].audit_tier if segments else None,
        )
        trace_patch = ComposerTracePatchDto(
            triggered_branch_ids=[branch.branch_id for branch in branches],
            published_segment_ids=[segment.segment_id for segment in segments],
            first_segment_type=segments[0].segment_type if segments else None,
            safety_first_lock_applied=any(
                branch.branch_type == ComposerBranchType.SAFETY_TRIGGER.value
                for branch in branches
            ),
            fallback_segment_ids=fallback_segment_ids,
            turn_audit_tier=segments[0].audit_tier if segments else None,
            composer_version="simulated.composer.v1",
            trace_degraded=False,
        )
        return ComposeTurnResultDto(
            output_text=output_text,
            segments=segments,
            turn_state=turn_state,
            trace_patch=trace_patch,
            trace_delivery_status=ComposerTraceWriteStatus.SKIPPED,
            metadata={
                "simulated_composer": True,
                "stream_delta_chars": 32,
            },
        )

    def _read_branches(
        self,
        request: ComposeTurnRequestDto,
    ) -> list[BranchExecutionStateDto]:
        """从 graph state 中读取 Composer 分支状态。

        :param request: 回复合成请求。
        :return: 已完成 DTO 校验的分支状态列表。
        """

        raw_branches = request.graph_state.get("branch_execution_states")
        assert isinstance(raw_branches, list), "主图必须在 Composer 前写入分支状态"
        return [BranchExecutionStateDto.model_validate(item) for item in raw_branches]

    def _segment_from_branch(
        self,
        *,
        branch: BranchExecutionStateDto,
        order_index: int,
    ) -> ResponseSegmentDto:
        """将单个分支状态转换为仿真发布 segment。

        :param branch: 当前 Composer 分支状态。
        :param order_index: 当前 segment 在本轮中的排序。
        :return: 仿真发布 segment。
        """

        publishable_segment = branch.publishable_segment
        assert publishable_segment is not None
        return ResponseSegmentDto(
            segment_id=publishable_segment.segment_id,
            task_id=publishable_segment.task_id,
            segment_type=publishable_segment.segment_type,
            order_index=order_index,
            content=publishable_segment.final_response or "仿真回复为空。",
            publish_status=ComposerPublishStatus.PUBLISHED,
            is_first_segment=order_index == 0,
            audit_tier=publishable_segment.audit_tier,
            title=publishable_segment.title,
            trace_refs=[ref for ref in [branch.trace_patch_ref] if ref is not None],
            metadata={
                **publishable_segment.metadata,
                "guard_status": publishable_segment.guard_status,
                "fallback_triggered": publishable_segment.fallback_triggered,
                "branch_type": branch.branch_type,
            },
        )


__all__: tuple[str, ...] = (
    "FakeContextBuilder",
    "FakeEducationAgent",
    "FakeGuardrailFramework",
    "FakeInputSafetyAssessor",
    "FakeNonmedicalPetCareAgent",
    "FakeResponseComposer",
    "FakeSafetyTriggerAgent",
    "FakeStandardConsultationAgent",
    "FakeTaskDecomposer",
    "SimulatedBusinessFakes",
    "SimulatedBusinessGraphFixture",
    "SimulatedBusinessScenario",
    "SimulatedGraphRunResult",
    "SimulatedRuntimeFixture",
    "build_education_scenario",
    "build_nonmedical_scenario",
    "build_safety_scenario",
    "build_simulated_business_fakes",
    "build_simulated_business_graph_fixture",
    "build_simulated_runtime_fixture",
    "build_simulated_turn_request",
    "build_standard_scenario",
    "run_definition_to_completion",
)
