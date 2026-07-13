##################################################################################################
# 文件: tests/vet_context_builder/helpers.py
# 作用: 提供 VetContextBuilder 测试请求、来源结果、假端口和 trace sink 夹具。
# 边界: 仅构造进程内测试对象，不访问数据库、网络、LangGraph checkpointer 或真实逻辑链存储。
##################################################################################################

import asyncio
from datetime import UTC, datetime
from pydantic import JsonValue

from veterinary_agent.config import (
    RuntimeConfigProvider,
    VetContextBuilderSettings,
    create_runtime_config_provider,
)
from veterinary_agent.vet_context_builder import (
    CompressionAuditDto,
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextFactDto,
    ContextFactState,
    ContextMessageDto,
    ContextSourceFreshness,
    ContextSourceLoadRequestDto,
    ContextSourcePort,
    ContextSourceReadResultDto,
    ContextSourceRefDto,
    ContextSourceStatus,
    ContextSourceType,
    ContextSummaryDto,
    ContextTraceWriteResultDto,
    ContextTraceRecordDto,
    ContextTraceWriteStatus,
    ResolvedContextFactDto,
    SessionContextStateDto,
    SlotCoverageDto,
    VetAuditTier,
    VetContextBuildRequestDto,
    VetContextBundleDto,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockDto,
    VetPromptBlockPriority,
    VetPromptBlockType,
)


class FakeContextSourcePort:
    """返回预设来源结果的测试端口。"""

    def __init__(
        self,
        *,
        result: ContextSourceReadResultDto,
        delay_seconds: float = 0.0,
    ) -> None:
        """初始化测试来源端口。

        :param result: 每次读取固定返回的来源结果。
        :param delay_seconds: 返回结果前等待的秒数，用于验证来源超时。
        :return: None。
        """

        self._result = result
        self._delay_seconds = delay_seconds
        self.requests: list[ContextSourceLoadRequestDto] = []

    @property
    def source_type(self) -> ContextSourceType:
        """读取测试端口负责的来源类型。

        :return: 预设结果的来源类型。
        """

        return self._result.source_type

    async def load(
        self,
        request: ContextSourceLoadRequestDto,
    ) -> ContextSourceReadResultDto:
        """记录请求并返回预设来源结果。

        :param request: 当前来源读取请求。
        :return: 初始化时传入的来源结果。
        """

        self.requests.append(request)
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        return self._result


class RecordingContextTraceSink:
    """记录上下文 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: ContextTraceWriteStatus = ContextTraceWriteStatus.RECORDED,
        exception: Exception | None = None,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :param exception: 可选待抛出的异常，用于验证 trace 异常旁路。
        :return: None。
        """

        self.status = status
        self.exception = exception
        self.records: list[object] = []

    async def write_context_summary(
        self,
        record: ContextTraceRecordDto,
    ) -> ContextTraceWriteResultDto:
        """记录上下文摘要并返回预设状态。

        :param record: 待记录的上下文构建摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        """

        self.records.append(record)
        if self.exception is not None:
            raise self.exception
        return ContextTraceWriteResultDto(status=self.status)


def build_runtime_provider(
    settings: VetContextBuilderSettings | None = None,
) -> RuntimeConfigProvider:
    """构建包含指定 VetContextBuilder 配置的 RuntimeConfig provider。

    :param settings: 可选 VetContextBuilder 配置。
    :return: 可供默认 Builder 使用的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(vet_context_builder_settings=settings)


def build_request(
    *,
    provider: RuntimeConfigProvider,
    **updates: object,
) -> VetContextBuildRequestDto:
    """构建标准问诊上下文请求并应用字段覆盖。

    :param provider: 用于填充参数版本与配置快照 ID 的 RuntimeConfig provider。
    :param updates: 待覆盖的请求字段。
    :return: 已完成严格校验的上下文构建请求。
    """

    snapshot = provider.current_snapshot()
    values: dict[str, object] = {
        "request_id": "req_context_1",
        "trace_id": "trace_context_1",
        "run_id": "run_context_1",
        "session_id": "session_context_1",
        "user_id": "user_context_1",
        "current_pet_id": "pet_context_1",
        "task_id": "task_context_1",
        "task_type": "TRIAGE",
        "normalized_query": "狗今天呕吐三次，精神稍差。",
        "generation_profile": VetGenerationProfile.STANDARD,
        "route": "normal",
        "executor_key": VetExecutorKey.STANDARD_CONSULTATION,
        "compression_strategy": ContextCompressionStrategy.SINGLE_FULL,
        "audit_tier": VetAuditTier.A,
        "assessment_summary": {
            "signals": [],
            "intent": "symptom_triage",
            "intent_confidence": 0.98,
        },
        "observed_facts": [],
        "session_state_snapshot": None,
        "params_version": snapshot.params_version,
        "config_snapshot_id": snapshot.config_snapshot_id,
    }
    values.update(updates)
    return VetContextBuildRequestDto.model_validate(values)


def build_source_ref(
    *,
    source_type: ContextSourceType,
    source_id: str,
    pet_id: str | None,
    version: str | None = "v1",
    freshness: ContextSourceFreshness = ContextSourceFreshness.FRESH,
) -> ContextSourceRefDto:
    """构建测试来源引用。

    :param source_type: 来源类型。
    :param source_id: 来源对象 ID。
    :param pet_id: 来源绑定宠物 ID。
    :param version: 可选来源版本。
    :param freshness: 来源新鲜度。
    :return: 状态为 available 的来源引用。
    """

    return ContextSourceRefDto(
        source_type=source_type,
        source_id=source_id,
        pet_id=pet_id,
        version=version,
        freshness=freshness,
        status=ContextSourceStatus.AVAILABLE,
    )


def build_fact_source_result(
    *,
    source_type: ContextSourceType,
    pet_id: str | None,
    facts: dict[str, JsonValue],
    source_id: str | None = None,
    version: str = "v1",
    freshness: ContextSourceFreshness = ContextSourceFreshness.FRESH,
) -> ContextSourceReadResultDto:
    """构建包含结构化事实的可用来源结果。

    :param source_type: 来源类型。
    :param pet_id: 来源绑定宠物 ID。
    :param facts: 事实键值映射。
    :param source_id: 可选来源对象 ID。
    :param version: 来源版本。
    :param freshness: 来源新鲜度。
    :return: 标准可用事实来源结果。
    """

    source_ref = build_source_ref(
        source_type=source_type,
        source_id=source_id or f"{source_type.value}:1",
        pet_id=pet_id,
        version=version,
        freshness=freshness,
    )
    return ContextSourceReadResultDto(
        source_type=source_type,
        status=ContextSourceStatus.AVAILABLE,
        source_refs=[source_ref],
        facts=[
            ContextFactDto(
                key=key,
                value=value,
                source_ref=source_ref,
                confirmed=True,
                observed_at=datetime.now(UTC),
            )
            for key, value in facts.items()
        ],
    )


def build_empty_source_result(
    *,
    source_type: ContextSourceType,
    pet_id: str | None,
) -> ContextSourceReadResultDto:
    """构建无数据但来源可正常访问的结果。

    :param source_type: 来源类型。
    :param pet_id: 来源绑定宠物 ID。
    :return: 状态为 empty 的来源结果。
    """

    source_ref = build_source_ref(
        source_type=source_type,
        source_id=f"{source_type.value}:empty",
        pet_id=pet_id,
    )
    return ContextSourceReadResultDto(
        source_type=source_type,
        status=ContextSourceStatus.EMPTY,
        source_refs=[source_ref],
    )


def build_conversation_source_result(
    *,
    pet_id: str,
    message_count: int = 2,
    content_size: int = 20,
) -> ContextSourceReadResultDto:
    """构建近期对话来源结果。

    :param pet_id: 消息绑定宠物 ID。
    :param message_count: 生成的消息数量。
    :param content_size: 每条消息正文字符数。
    :return: 包含交替用户与助手消息的来源结果。
    """

    messages: list[ContextMessageDto] = []
    source_refs: list[ContextSourceRefDto] = []
    for index in range(1, message_count + 1):
        source_ref = build_source_ref(
            source_type=ContextSourceType.CONVERSATION,
            source_id=f"message_{index}",
            pet_id=pet_id,
            version=str(index),
        )
        source_refs.append(source_ref)
        messages.append(
            ContextMessageDto(
                message_id=f"message_{index}",
                pet_id=pet_id,
                role="user" if index % 2 else "assistant",
                content=("问" if index % 2 else "答") * content_size,
                sequence_no=index,
                source_ref=source_ref,
            )
        )
    return ContextSourceReadResultDto(
        source_type=ContextSourceType.CONVERSATION,
        status=ContextSourceStatus.AVAILABLE,
        source_refs=source_refs,
        messages=messages,
    )


def build_lab_source_result(*, pet_id: str) -> ContextSourceReadResultDto:
    """构建已确认化验摘要来源结果。

    :param pet_id: 化验摘要绑定宠物 ID。
    :return: 包含单条已确认摘要的来源结果。
    """

    source_ref = build_source_ref(
        source_type=ContextSourceType.CONFIRMED_LAB,
        source_id="lab_summary_1",
        pet_id=pet_id,
    )
    return ContextSourceReadResultDto(
        source_type=ContextSourceType.CONFIRMED_LAB,
        status=ContextSourceStatus.AVAILABLE,
        source_refs=[source_ref],
        summaries=[
            ContextSummaryDto(
                summary_id="lab_summary_1",
                summary_type="cbc",
                content="白细胞轻度升高，结果已经用户确认。",
                confirmed=True,
                source_ref=source_ref,
            )
        ],
    )


def build_session_state(*, pet_id: str) -> SessionContextStateDto:
    """构建当前 graph state 可直接传入的 session 状态快照。

    :param pet_id: session 状态绑定宠物 ID。
    :return: 包含已回答槽位和 rolling summary 引用的状态快照。
    """

    source_ref = build_source_ref(
        source_type=ContextSourceType.CHECKPOINT,
        source_id="checkpoint_1",
        pet_id=pet_id,
        version="3",
    )
    return SessionContextStateDto(
        pet_id=pet_id,
        current_complaint_type="gastrointestinal",
        slot_progress={
            "symptom_duration": {"value": "今天", "status": "answered"},
        },
        rolling_summary_ref="summary_ref_1",
        checkpoint_id="checkpoint_1",
        checkpoint_version=3,
        source_ref=source_ref,
    )


def build_minimal_bundle() -> VetContextBundleDto:
    """构建供图节点映射测试使用的最小合法 bundle。

    :return: 包含 task_input 块和空槽位覆盖的最小 bundle。
    """

    source_ref = build_source_ref(
        source_type=ContextSourceType.CURRENT_TASK,
        source_id="task_context_1",
        pet_id="pet_context_1",
    )
    block = VetPromptBlockDto(
        block_id="task_context_1:task_input",
        block_type=VetPromptBlockType.TASK_INPUT,
        priority=VetPromptBlockPriority.P0,
        required=True,
        content_ref_or_text='{"normalized_query":"test"}',
        content_hash=(
            "sha256:0000000000000000000000000000000000000000000000000000000000000000"
        ),
        token_estimate=8,
        source_refs=[source_ref],
    )
    return VetContextBundleDto(
        task_id="task_context_1",
        current_pet_id="pet_context_1",
        generation_profile=VetGenerationProfile.EDUCATION,
        executor_key=VetExecutorKey.EDUCATION,
        prompt_blocks=[block],
        fact_ledger=[
            ResolvedContextFactDto(
                key="species",
                value="dog",
                state=ContextFactState.KNOWN,
                source_refs=[source_ref],
            )
        ],
        slot_coverage=SlotCoverageDto(task_id="task_context_1"),
        source_refs=[source_ref],
        compression_audit=CompressionAuditDto(
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
            token_budget=100,
            estimated_tokens=8,
            trim_applied=False,
            p0_reinjected=False,
            included_block_ids=[block.block_id],
        ),
        status=ContextBuildStatus.FULL,
    )


def as_source_ports(
    *results: ContextSourceReadResultDto,
) -> tuple[ContextSourcePort, ...]:
    """将来源结果转换为 Builder 可注入的测试端口元组。

    :param results: 需要包装的预设来源结果。
    :return: 与输入结果顺序一致的测试来源端口元组。
    """

    return tuple(FakeContextSourcePort(result=result) for result in results)


__all__: tuple[str, ...] = (
    "FakeContextSourcePort",
    "RecordingContextTraceSink",
    "as_source_ports",
    "build_conversation_source_result",
    "build_empty_source_result",
    "build_fact_source_result",
    "build_lab_source_result",
    "build_minimal_bundle",
    "build_request",
    "build_runtime_provider",
    "build_session_state",
    "build_source_ref",
)
