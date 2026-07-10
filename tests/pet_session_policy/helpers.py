##################################################################################################
# 文件: tests/pet_session_policy/helpers.py
# 作用: 提供 PetSessionPolicy 组件测试共享的 DTO 构造器、依赖替身与策略装配函数。
# 边界: 仅依赖各组件包公开出口；不访问真实数据库、不启动 FastAPI、GraphRuntime 或其他业务领域。
##################################################################################################

from datetime import UTC, datetime

from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigErrorCode,
    RuntimeConfigOperation,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    create_runtime_config_provider,
)
from veterinary_agent.conversation_store import (
    ConversationSessionDto,
    ConversationSessionStatus,
    ConversationStore,
    EnsureSessionCommandDto,
    EnsureSessionResultDto,
    TodoConversationStore,
)
from veterinary_agent.observability import (
    JsonMap as ObservabilityJsonMap,
    MetricType,
    ObservabilityErrorDto,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.pet_session_policy import (
    DefaultPetSessionPolicy,
    JsonMap,
    PetSessionRequestContextDto,
    PetSessionTraceRecordDto,
    PetSessionTraceSink,
    PetSessionTraceWriteResultDto,
    PetSessionTraceWriteStatus,
)


def build_session(
    *,
    session_id: str = "session_1",
    user_id: str = "user_1",
    pet_id: str = "pet_1",
    status: ConversationSessionStatus = ConversationSessionStatus.ACTIVE,
) -> ConversationSessionDto:
    """构建测试用 conversation session DTO。

    :param session_id: 测试 session ID。
    :param user_id: 测试用户 ID。
    :param pet_id: 测试宠物 ID。
    :param status: 测试 session 生命周期状态。
    :return: 测试用 conversation session DTO。
    """

    now = datetime.now(UTC)
    return ConversationSessionDto(
        session_id=session_id,
        user_id=user_id,
        pet_id=pet_id,
        status=status,
        created_at=now,
        updated_at=now,
        next_sequence_no=1,
    )


def build_request(
    *,
    request_id: str = "req_1",
    trace_id: str | None = None,
    user_id: str | None = "user_1",
    session_id: str | None = "session_1",
    pet_id: str | None = "pet_1",
    client_pet_snapshot_ref: JsonMap | None = None,
) -> PetSessionRequestContextDto:
    """构建测试用 PetSessionPolicy 请求上下文。

    :param request_id: 测试请求 ID。
    :param trace_id: 可选测试 trace ID；未传入时根据 request_id 生成。
    :param user_id: 可选测试用户 ID。
    :param session_id: 可选测试 session ID。
    :param pet_id: 可选测试宠物 ID。
    :param client_pet_snapshot_ref: 可选客户端宠物快照引用。
    :return: 测试用 PetSessionPolicy 请求上下文。
    """

    return PetSessionRequestContextDto(
        request_id=request_id,
        trace_id=trace_id or f"trace_{request_id}",
        user_id=user_id,
        session_id=session_id,
        pet_id=pet_id,
        client_pet_snapshot_ref=client_pet_snapshot_ref,
    )


class FakeConversationStore(TodoConversationStore):
    """仅覆盖 EnsureSession 的 PetSessionPolicy 测试存储替身。"""

    def __init__(
        self,
        *,
        result: EnsureSessionResultDto | None = None,
        error: Exception | None = None,
    ) -> None:
        """初始化测试 ConversationStore。

        :param result: EnsureSession 成功时返回的预设结果。
        :param error: EnsureSession 需要抛出的预设异常。
        :return: None。
        """

        self.result = result
        self.error = error
        self.ensure_calls: list[EnsureSessionCommandDto] = []

    async def ensure_session(
        self,
        command: EnsureSessionCommandDto,
    ) -> EnsureSessionResultDto:
        """记录并执行预设的 EnsureSession 行为。

        :param command: PetSessionPolicy 传入的 EnsureSession 命令。
        :return: 预设的 EnsureSession 结果。
        :raises Exception: 当测试预设了异常时抛出。
        :raises RuntimeError: 当测试未配置返回结果或异常时抛出。
        """

        self.ensure_calls.append(command)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise RuntimeError("test result is not configured")
        return self.result


class RecordingTraceSink:
    """记录策略判定摘要并返回预设写入结果的测试 trace sink。"""

    def __init__(
        self,
        *,
        result: PetSessionTraceWriteResultDto | None = None,
    ) -> None:
        """初始化记录型 trace sink。

        :param result: 可选 trace 写入结果；未传入时返回 recorded。
        :return: None。
        """

        self.records: list[PetSessionTraceRecordDto] = []
        self.result = result or PetSessionTraceWriteResultDto(
            status=PetSessionTraceWriteStatus.RECORDED,
        )

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """记录策略判定摘要并返回预设结果。

        :param record: 待记录的策略判定摘要。
        :return: 预设的 trace 写入结果。
        """

        self.records.append(record)
        return self.result


class RaisingTraceSink:
    """始终抛出异常的测试 trace sink。"""

    async def write_decision(
        self,
        record: PetSessionTraceRecordDto,
    ) -> PetSessionTraceWriteResultDto:
        """模拟 trace sink 未知异常。

        :param record: 待写入的策略判定摘要。
        :return: 当前测试实现不会返回结果。
        :raises RuntimeError: 始终抛出测试异常。
        """

        del record
        raise RuntimeError("trace failed")


class UnavailableRuntimeConfigProvider(RuntimeConfigProvider):
    """按预设 readiness 返回配置不可用错误的测试 provider。"""

    def __init__(self, *, ready: bool) -> None:
        """初始化不可用 RuntimeConfig provider。

        :param ready: is_ready 方法需要返回的值。
        :return: None。
        """

        self.ready = ready
        self.snapshot_calls = 0

    def is_ready(self) -> bool:
        """返回预设的 RuntimeConfig readiness。

        :return: 预设的 readiness 值。
        """

        return self.ready

    def current_snapshot(self) -> RuntimeConfigSnapshot:
        """模拟读取不存在的 RuntimeConfig 快照。

        :return: 当前测试实现不会返回结果。
        :raises RuntimeConfigError: 始终抛出配置快照不存在错误。
        """

        self.snapshot_calls += 1
        raise RuntimeConfigError(
            code=RuntimeConfigErrorCode.CONFIG_SNAPSHOT_NOT_FOUND,
            operation=RuntimeConfigOperation.GET_CURRENT_CONFIG_SNAPSHOT,
            message="snapshot unavailable",
            retryable=True,
        )


class RecordingObservabilityProvider(ObservabilityProvider):
    """记录 PetSessionPolicy 指标与结构化事件的测试 provider。"""

    def __init__(self) -> None:
        """初始化记录型 Observability provider。

        :return: None。
        """

        self.metrics: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: MetricType,
        labels: dict[str, str] | None = None,
        description: str = "Observability metric.",
    ) -> ObservabilityErrorDto | None:
        """记录测试指标调用。

        :param metric_name: 指标名称。
        :param value: 指标值。
        :param metric_type: 指标类型。
        :param labels: 低基数指标标签。
        :param description: 指标说明。
        :return: 固定返回 None，表示记录成功。
        """

        self.metrics.append(
            {
                "metric_name": metric_name,
                "value": value,
                "metric_type": metric_type,
                "labels": dict(labels or {}),
                "description": description,
            }
        )
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
        """记录测试结构化事件调用。

        :param event_name: 事件名称。
        :param component: 事件所属组件。
        :param level: 结构化日志级别。
        :param safe_fields: 安全事件字段。
        :param error_type: 可选错误类型摘要。
        :return: 固定返回 None，表示记录成功。
        """

        self.events.append(
            {
                "event_name": event_name,
                "component": component,
                "level": level,
                "safe_fields": dict(safe_fields or {}),
                "error_type": error_type,
            }
        )
        return None


class RaisingObservabilityProvider(ObservabilityProvider):
    """始终在记录指标时抛出异常的测试 Observability provider。"""

    def __init__(self) -> None:
        """初始化异常型 Observability provider。

        :return: None。
        """

    def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        metric_type: MetricType,
        labels: dict[str, str] | None = None,
        description: str = "Observability metric.",
    ) -> ObservabilityErrorDto | None:
        """模拟指标记录异常。

        :param metric_name: 指标名称。
        :param value: 指标值。
        :param metric_type: 指标类型。
        :param labels: 低基数指标标签。
        :param description: 指标说明。
        :return: 当前测试实现不会返回结果。
        :raises RuntimeError: 始终抛出测试异常。
        """

        del metric_name, value, metric_type, labels, description
        raise RuntimeError("observability failed")


def build_disabled_runtime_config_provider() -> RuntimeConfigProvider:
    """构建安全锁被关闭的测试 RuntimeConfig provider。

    :return: 持有测试专用禁用策略快照的 RuntimeConfig provider。
    """

    provider = create_runtime_config_provider()
    snapshot = provider.current_snapshot()
    disabled_safety_locks = snapshot.runtime_config.safety_locks.model_copy(
        update={"enforce_pet_session_policy": False},
    )
    disabled_runtime_config = snapshot.runtime_config.model_copy(
        update={"safety_locks": disabled_safety_locks},
    )
    disabled_snapshot = snapshot.model_copy(
        update={"runtime_config": disabled_runtime_config},
    )
    return RuntimeConfigProvider(disabled_snapshot)


def build_policy(
    *,
    store: ConversationStore,
    runtime_config_provider: RuntimeConfigProvider | None = None,
    trace_sink: PetSessionTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultPetSessionPolicy:
    """构建测试用 PetSessionPolicy 默认实现。

    :param store: 测试 ConversationStore。
    :param runtime_config_provider: 可选 RuntimeConfig provider。
    :param trace_sink: 可选策略判定 trace sink。
    :param observability_provider: 可选 Observability provider。
    :return: 测试用 PetSessionPolicy 默认实现。
    """

    return DefaultPetSessionPolicy(
        conversation_store=store,
        runtime_config_provider=(
            runtime_config_provider or create_runtime_config_provider()
        ),
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )
