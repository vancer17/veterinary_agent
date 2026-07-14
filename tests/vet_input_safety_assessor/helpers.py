##################################################################################################
# 文件: tests/vet_input_safety_assessor/helpers.py
# 作用: 提供 VetInputSafetyAssessor 组件测试使用的请求构造器和 trace sink 替身。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、Trace 存储、本地模型或后续业务图。
##################################################################################################

from veterinary_agent.agent_runner import (
    AgentPromptEstimateDto,
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunStatus,
    AgentSpecDto,
    AgentValidationErrorDto,
)
from veterinary_agent.config import (
    RuntimeConfigProvider,
    VetInputSafetyAssessorSettings,
    create_runtime_config_provider,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext
from veterinary_agent.vet_input_safety_assessor import (
    BatchVetInputAssessmentRequestDto,
    InputSafetySignalDto,
    SemanticRouteCandidateDto,
    StructuredSignalExtractionSummaryDto,
    VetInputAssessmentRequestDto,
    VetInputAssessmentTraceRecordDto,
    VetInputAssessmentTraceWriteStatus,
    VetInputSafetyTraceWriteResultDto,
)
from veterinary_agent.vet_task_decomposer import (
    TaskPriorityHint,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskType,
    build_text_hash,
)


class UnreadyLexicalSignalMatcher:
    """模拟 SAF 词库不可用的信号匹配器。"""

    def is_ready(self) -> bool:
        """判断测试匹配器是否就绪。

        :return: 固定返回 False，用于验证词库不可用阻断。
        """

        return False

    def match(
        self, request: VetInputAssessmentRequestDto
    ) -> list[InputSafetySignalDto]:
        """返回空信号列表。

        :param request: 当前输入安全评估请求。
        :return: 空信号列表。
        """

        del request
        return []


class FakeSemanticRouteClassifier:
    """组件级测试使用的语义路由端口替身。"""

    def __init__(
        self,
        *,
        candidates: list[SemanticRouteCandidateDto] | None = None,
        ready: bool = True,
        raises: Exception | None = None,
    ) -> None:
        """初始化语义路由替身。

        :param candidates: 每次分类返回的候选列表。
        :param ready: is_ready 返回值。
        :param raises: 可选待抛出的异常，用于验证降级路径。
        :return: None。
        """

        self.candidates = candidates or []
        self.ready = ready
        self.raises = raises
        self.requests: list[VetInputAssessmentRequestDto] = []

    def is_ready(self) -> bool:
        """判断测试语义路由器是否就绪。

        :return: 初始化时传入的 ready 标记。
        """

        return self.ready

    async def classify(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> list[SemanticRouteCandidateDto]:
        """记录分类请求并返回预设候选。

        :param request: 当前输入安全评估请求。
        :return: 初始化时传入的候选列表。
        :raises Exception: 当初始化传入 raises 时抛出该异常。
        """

        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return list(self.candidates)


class FakeStructuredSignalExtractor:
    """组件级测试使用的本地结构化抽取端口替身。"""

    def __init__(
        self,
        *,
        summary: StructuredSignalExtractionSummaryDto | None = None,
        ready: bool = True,
        raises: Exception | None = None,
    ) -> None:
        """初始化结构化抽取替身。

        :param summary: 每次抽取返回的结构化摘要。
        :param ready: is_ready 返回值。
        :param raises: 可选待抛出的异常，用于验证降级路径。
        :return: None。
        """

        self.summary = summary or StructuredSignalExtractionSummaryDto(
            extractor_version="fake-extractor.v1",
            extracted_concept_types=["symptom"],
            confidence=0.7,
            unavailable=False,
        )
        self.ready = ready
        self.raises = raises
        self.requests: list[VetInputAssessmentRequestDto] = []

    def is_ready(self) -> bool:
        """判断测试结构化抽取器是否就绪。

        :return: 初始化时传入的 ready 标记。
        """

        return self.ready

    async def extract(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> StructuredSignalExtractionSummaryDto:
        """记录抽取请求并返回预设摘要。

        :param request: 当前输入安全评估请求。
        :return: 初始化时传入的结构化抽取摘要。
        :raises Exception: 当初始化传入 raises 时抛出该异常。
        """

        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return self.summary


class FakeAgentRunner:
    """组件级测试使用的 AgentRunner 替身。"""

    def __init__(
        self,
        *,
        result: AgentRunResultDto | None = None,
        ready: bool = True,
        raises: Exception | None = None,
    ) -> None:
        """初始化 AgentRunner 替身。

        :param result: 每次运行返回的 AgentRunResultDto。
        :param ready: is_ready 返回值。
        :param raises: 可选待抛出的异常，用于验证 LLM 不可用路径。
        :return: None。
        """

        self.result = result or build_agent_result(parsed_output={})
        self.ready = ready
        self.raises = raises
        self.requests: list[AgentRunRequestDto] = []

    def is_ready(self) -> bool:
        """判断 AgentRunner 替身是否就绪。

        :return: 初始化时传入的 ready 标记。
        """

        return self.ready

    async def run_agent(self, request: AgentRunRequestDto) -> AgentRunResultDto:
        """记录 AgentRunner 请求并返回预设结果。

        :param request: 当前 AgentRunner 运行请求。
        :return: 初始化时传入的 AgentRunResultDto。
        :raises Exception: 当初始化传入 raises 时抛出该异常。
        """

        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return self.result

    def estimate_agent_prompt(
        self,
        request: AgentRunRequestDto,
    ) -> AgentPromptEstimateDto:
        """返回测试用 prompt 预算估算。

        :param request: 当前 AgentRunner 运行请求。
        :return: 固定的 prompt 预算估算结果。
        """

        return AgentPromptEstimateDto(
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            model_profile="fake-model",
            input_tokens=1,
            reserved_output_tokens=1,
            total_budget_tokens=2,
            max_context_tokens=4096,
        )

    def validate_agent_spec(
        self,
        spec: AgentSpecDto,
    ) -> list[AgentValidationErrorDto]:
        """返回测试用 Agent 规格校验结果。

        :param spec: 待校验的 Agent 规格。
        :return: 空错误列表。
        """

        del spec
        return []

    async def close(self) -> None:
        """关闭测试 AgentRunner 替身。

        :return: None。
        """


class RecordingInputSafetyTraceSink:
    """记录输入安全 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: VetInputAssessmentTraceWriteStatus = (
            VetInputAssessmentTraceWriteStatus.RECORDED
        ),
        exception: Exception | None = None,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :param exception: 可选待抛出的异常，用于验证 trace 异常旁路。
        :return: None。
        """

        self.status = status
        self.exception = exception
        self.records: list[VetInputAssessmentTraceRecordDto] = []

    async def write_assessment_summary(
        self,
        record: VetInputAssessmentTraceRecordDto,
    ) -> VetInputSafetyTraceWriteResultDto:
        """记录输入安全摘要并返回预设状态。

        :param record: 待记录的输入安全 trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        :raises Exception: 当初始化传入 exception 时抛出该异常。
        """

        self.records.append(record)
        if self.exception is not None:
            raise self.exception
        return VetInputSafetyTraceWriteResultDto(status=self.status)


def build_provider(
    *,
    settings: VetInputSafetyAssessorSettings | None = None,
) -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :param settings: 可选 VetInputSafetyAssessor 组件配置；未传入时使用默认配置。
    :return: 已加载测试配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(
        vet_input_safety_assessor_settings=settings,
    )


def build_agent_result(
    *,
    parsed_output: dict[str, object],
    status: AgentRunStatus = AgentRunStatus.SUCCEEDED,
) -> AgentRunResultDto:
    """构建测试使用的 AgentRunner 结果。

    :param parsed_output: 模拟 AgentRunner 返回的结构化输出。
    :param status: 模拟 AgentRunner 运行状态。
    :return: AgentRunner 标准运行结果 DTO。
    """

    return AgentRunResultDto(
        status=status,
        agent_id="vet_input_safety_arbitrator",
        agent_version="v1",
        parsed_output=parsed_output,
        schema_valid=True,
    )


def build_semantic_candidate(
    *,
    label: str,
    score: float = 0.8,
    margin: float = 0.2,
) -> SemanticRouteCandidateDto:
    """构建测试使用的语义路由候选。

    :param label: 候选标签。
    :param score: 候选分数。
    :param margin: 首位候选间隔。
    :return: 语义路由候选 DTO。
    """

    return SemanticRouteCandidateDto(
        route_label=label,
        score=score,
        margin=margin,
        router_version="fake-router.v1",
    )


def build_task(
    *,
    query: str,
    task_type: VetTaskType = VetTaskType.TRIAGE,
    task_id: str | None = None,
    current_pet_id: str = "pet_1",
    confidence: float = 0.82,
) -> VetSubTaskDto:
    """构建测试使用的子任务。

    :param query: 子任务规范化文本。
    :param task_type: 子任务类型。
    :param task_id: 可选子任务 ID；未传入时按任务类型生成。
    :param current_pet_id: 当前宠物 ID。
    :param confidence: 子任务置信度。
    :return: 可用于输入安全评估的子任务 DTO。
    """

    return VetSubTaskDto(
        task_id=task_id or f"task_{task_type.value.lower()}",
        task_type=task_type,
        current_pet_id=current_pet_id,
        source_span=TextSpanDto(
            start_offset=0,
            end_offset=max(1, len(query)),
            text_hash=build_text_hash(query),
        ),
        normalized_query=query,
        attachment_bindings=[],
        priority_hint=TaskPriorityHint.UNKNOWN,
        coverage_required=True,
        requires_independent_segment=True,
        confidence=confidence,
    )


def build_batch_request(
    provider: RuntimeConfigProvider,
    *,
    tasks: list[VetSubTaskDto] | None = None,
    original_user_message: str = "狗狗今天不舒服。",
) -> BatchVetInputAssessmentRequestDto:
    """构建测试使用的批量输入安全评估请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param tasks: 可选子任务列表；未传入时构建一个普通分诊任务。
    :param original_user_message: 本轮用户原文。
    :return: 可传给 VetInputSafetyAssessor 的严格批量请求 DTO。
    """

    snapshot = provider.current_snapshot()
    resolved_tasks = tasks or [
        build_task(query=original_user_message, task_type=VetTaskType.TRIAGE)
    ]
    return BatchVetInputAssessmentRequestDto(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        session_id="sess_1",
        user_id="user_1",
        current_pet_id="pet_1",
        tasks=resolved_tasks,
        original_user_message=original_user_message,
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给 GraphNode 的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="vet_input_safety_assessor",
        session_id="sess_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )
