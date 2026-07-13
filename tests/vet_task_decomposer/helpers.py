##################################################################################################
# 文件: tests/vet_task_decomposer/helpers.py
# 作用: 提供 VetTaskDecomposer 组件级测试使用的请求构造器和领域依赖测试替身。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、本地预训练模型、Trace 存储或观测后端。
##################################################################################################

from collections.abc import Mapping

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
    VetTaskDecomposerSettings,
    create_runtime_config_provider,
)
from veterinary_agent.observability import (
    JsonMap as ObservabilityJsonMap,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityErrorDto,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_task_decomposer import (
    AttachmentRefDto,
    DecompositionMethod,
    LocalFallbackResultDto,
    TaskPriorityHint,
    TextSpanDto,
    VetSubTaskDto,
    VetTaskDecomposeRequestDto,
    VetTaskDecomposeTraceRecordDto,
    VetTaskTraceWriteResultDto,
    VetTaskTraceWriteStatus,
    VetTaskType,
    build_text_hash,
)

DEFAULT_USER_MESSAGE = "狗狗今天呕吐两次，还想让我看一下这个化验单。"


def build_provider(
    *,
    settings: VetTaskDecomposerSettings | None = None,
) -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :param settings: 可选 VetTaskDecomposer 组件配置；未传入时使用默认配置。
    :return: 已加载测试配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(
        vet_task_decomposer_settings=settings,
    )


def build_request(
    provider: RuntimeConfigProvider,
    *,
    current_pet_id: str | None = "pet_1",
    user_message: str = DEFAULT_USER_MESSAGE,
    attachments: list[AttachmentRefDto] | None = None,
) -> VetTaskDecomposeRequestDto:
    """构建测试使用的任务拆解请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param current_pet_id: 可选当前宠物 ID；传入 None 用于阻断测试。
    :param user_message: 本轮用户原文。
    :param attachments: 可选附件引用列表；未传入时使用一张化验单图片。
    :return: 可传给 VetTaskDecomposer 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    resolved_attachments = attachments
    if resolved_attachments is None:
        resolved_attachments = [
            AttachmentRefDto(
                attachment_id="att_1",
                mime_type="image/png",
                declared_type="lab_report",
                upload_order=0,
            )
        ]
    return VetTaskDecomposeRequestDto(
        request_id="req_1",
        trace_id="trace_1",
        run_id="run_1",
        session_id="sess_1",
        user_id="user_1",
        current_pet_id=current_pet_id,
        user_message=user_message,
        attachments=resolved_attachments,
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_full_span_task(
    request: VetTaskDecomposeRequestDto,
    *,
    task_type: VetTaskType = VetTaskType.TRIAGE,
    current_pet_id: str | None = None,
    confidence: float = 0.8,
    valid_hash: bool = True,
) -> VetSubTaskDto:
    """构建覆盖完整原文的测试子任务。

    :param request: 当前任务拆解请求。
    :param task_type: 子任务类型。
    :param current_pet_id: 可选覆盖当前宠物 ID；未传入时使用请求中的 current_pet_id。
    :param confidence: 子任务置信度。
    :param valid_hash: 是否生成与原文一致的 source span hash。
    :return: 可用于 fallback 或断言的测试子任务。
    """

    text_hash = (
        build_text_hash(request.user_message)
        if valid_hash
        else build_text_hash(f"invalid:{request.user_message}")
    )
    span = TextSpanDto(
        start_offset=0,
        end_offset=len(request.user_message),
        text_hash=text_hash,
    )
    return VetSubTaskDto(
        task_id=f"task_{task_type.value.lower()}",
        task_type=task_type,
        current_pet_id=current_pet_id or request.current_pet_id or "pet_1",
        source_span=span,
        normalized_query=request.user_message,
        attachment_bindings=[],
        priority_hint=TaskPriorityHint.UNKNOWN,
        coverage_required=True,
        requires_independent_segment=True,
        confidence=confidence,
    )


def build_agent_result(
    *,
    parsed_output: Mapping[str, object],
    status: AgentRunStatus = AgentRunStatus.SUCCEEDED,
) -> AgentRunResultDto:
    """构建测试使用的 AgentRunner 结果。

    :param parsed_output: 模拟 AgentRunner 返回的结构化输出。
    :param status: 模拟 AgentRunner 运行状态。
    :return: AgentRunner 标准运行结果 DTO。
    """

    return AgentRunResultDto(
        status=status,
        agent_id="vet_task_decomposer",
        agent_version="v1",
        parsed_output=dict(parsed_output),
        schema_valid=True,
    )


class FakeAgentRunner:
    """组件级测试使用的 AgentRunner 替身。"""

    def __init__(
        self,
        *,
        results: list[AgentRunResultDto] | None = None,
        ready: bool = True,
    ) -> None:
        """初始化 AgentRunner 替身。

        :param results: 按调用顺序返回的 AgentRunResultDto 列表。
        :param ready: is_ready 返回值。
        :return: None。
        """

        self._results = list(results or [])
        self._ready = ready
        self.requests: list[AgentRunRequestDto] = []

    def is_ready(self) -> bool:
        """判断测试替身是否就绪。

        :return: 初始化时传入的 ready 值。
        """

        return self._ready

    async def run_agent(
        self,
        request: AgentRunRequestDto,
    ) -> AgentRunResultDto:
        """返回预置的 AgentRunner 结果。

        :param request: 本次 AgentRunner 调用请求。
        :return: 按调用顺序弹出的 AgentRunner 结果。
        :raises AssertionError: 当测试未预置足够结果时抛出。
        """

        self.requests.append(request)
        if not self._results:
            raise AssertionError("FakeAgentRunner 缺少预置结果")
        return self._results.pop(0)

    def estimate_agent_prompt(
        self,
        request: AgentRunRequestDto,
    ) -> AgentPromptEstimateDto:
        """返回固定 prompt 估算结果。

        :param request: 本次 AgentRunner 调用请求。
        :return: 固定 token 预算估算结果。
        """

        return AgentPromptEstimateDto(
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            model_profile="test",
            input_tokens=1,
            reserved_output_tokens=1,
            total_budget_tokens=2,
            max_context_tokens=8192,
        )

    def validate_agent_spec(
        self,
        spec: AgentSpecDto,
    ) -> list[AgentValidationErrorDto]:
        """返回空规格校验错误列表。

        :param spec: 待校验的 Agent 规格。
        :return: 空校验错误列表。
        """

        del spec
        return []

    async def close(self) -> None:
        """关闭测试替身。

        :return: None。
        """

        return None


class FakeLocalFallback:
    """组件级测试使用的本地 fallback 替身。"""

    def __init__(
        self,
        *,
        result: LocalFallbackResultDto,
        ready: bool = True,
    ) -> None:
        """初始化本地 fallback 替身。

        :param result: fallback 调用返回的预置结果。
        :param ready: is_ready 返回值。
        :return: None。
        """

        self._result = result
        self._ready = ready
        self.requests: list[VetTaskDecomposeRequestDto] = []

    def is_ready(self) -> bool:
        """判断本地 fallback 替身是否就绪。

        :return: 初始化时传入的 ready 值。
        """

        return self._ready

    async def decompose(
        self,
        request: VetTaskDecomposeRequestDto,
    ) -> LocalFallbackResultDto:
        """返回预置 fallback 结果。

        :param request: 本轮任务拆解请求。
        :return: 初始化时传入的 fallback 结果。
        """

        self.requests.append(request)
        return self._result


class FakeTraceSink:
    """组件级测试使用的任务拆解 trace sink 替身。"""

    def __init__(
        self,
        *,
        status: VetTaskTraceWriteStatus = VetTaskTraceWriteStatus.RECORDED,
        raise_on_write: bool = False,
    ) -> None:
        """初始化 trace sink 替身。

        :param status: 写入成功时返回的 trace 状态。
        :param raise_on_write: 是否在写入时抛出异常。
        :return: None。
        """

        self._status = status
        self._raise_on_write = raise_on_write
        self.records: list[VetTaskDecomposeTraceRecordDto] = []

    async def write_decomposition_summary(
        self,
        record: VetTaskDecomposeTraceRecordDto,
    ) -> VetTaskTraceWriteResultDto:
        """记录 trace 摘要并返回预置状态。

        :param record: 待写入的任务拆解摘要。
        :return: trace 写入结果。
        :raises RuntimeError: 当 raise_on_write 为 True 时抛出。
        """

        if self._raise_on_write:
            raise RuntimeError("trace unavailable")
        self.records.append(record)
        return VetTaskTraceWriteResultDto(
            status=self._status,
            retryable=False,
            detail="recorded",
        )


class FakeObservabilityProvider(ObservabilityProvider):
    """组件级测试使用的 ObservabilityProvider 替身。"""

    def __init__(self) -> None:
        """初始化观测替身。

        :return: None。
        """

        self.metrics: list[tuple[str, float, dict[str, str]]] = []
        self.events: list[tuple[str, ObservabilityJsonMap]] = []

    def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: MetricType,
        labels: dict[str, str] | None = None,
        description: str = "Observability metric.",
    ) -> ObservabilityErrorDto | None:
        """记录一次测试指标事件。

        :param metric_name: 指标名称。
        :param value: 指标观测值。
        :param metric_type: 指标类型。
        :param labels: 低基数 label 集合。
        :param description: 指标 HELP 文本。
        :return: 固定返回 None 表示记录成功。
        """

        del metric_type, description
        self.metrics.append((metric_name, value, labels or {}))
        return None

    def record_event(
        self,
        *,
        event_name: str,
        component: str,
        level: StructuredLogLevel = StructuredLogLevel.INFO,
        safe_fields: ObservabilityJsonMap | None = None,
        error_type: str | None = None,
    ) -> ObservabilityErrorDto | None:
        """记录一次测试结构化事件。

        :param event_name: 事件名称。
        :param component: 产生事件的组件名。
        :param level: 结构化日志级别。
        :param safe_fields: 允许输出到日志的安全字段。
        :param error_type: 可选错误类型摘要。
        :return: 固定返回 None 表示记录成功。
        """

        del component, level, error_type
        self.events.append((event_name, safe_fields or {}))
        return None


def build_fallback_result(
    *,
    tasks: list[VetSubTaskDto],
    confidence: float = 0.8,
) -> LocalFallbackResultDto:
    """构建可用的本地 fallback 结果。

    :param tasks: fallback 候选子任务列表。
    :param confidence: fallback 整体置信度。
    :return: LocalFallbackResultDto 测试对象。
    """

    return LocalFallbackResultDto(
        available=True,
        tasks=tasks,
        confidence=confidence,
    )


def method_metric_seen(
    metrics: list[tuple[str, float, dict[str, str]]],
    *,
    metric_name: str,
    method: DecompositionMethod,
) -> bool:
    """判断测试指标列表中是否存在指定 method 维度的指标。

    :param metrics: FakeObservabilityProvider 记录的指标列表。
    :param metric_name: 需要查找的指标名称。
    :param method: 需要匹配的拆解方法。
    :return: 若存在匹配指标则返回 True。
    """

    return any(
        name == metric_name and labels.get("method") == method.value
        for name, value, labels in metrics
        if value >= 0
    )
