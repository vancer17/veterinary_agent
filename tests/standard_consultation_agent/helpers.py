##################################################################################################
# 文件: tests/standard_consultation_agent/helpers.py
# 作用: 提供 StandardConsultationAgent 组件测试使用的请求构造器、上下文 bundle 和依赖替身。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、RAG、MedicationPolicy 或 Trace 存储。
##################################################################################################

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

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
    create_runtime_config_provider,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext
from veterinary_agent.standard_consultation_agent import (
    RagEvidenceBundleDto,
    RagEvidenceHintDto,
    RetrievalPurpose,
    StandardConsultationRequestDto,
    StandardConsultationTraceRecordDto,
    StandardTraceWriteResultDto,
    StandardTraceWriteStatus,
)
from veterinary_agent.vet_context_builder import (
    CompressionAuditDto,
    ContextBuildStatus,
    ContextCompressionStrategy,
    ContextFactState,
    ContextSourceFreshness,
    ContextSourceRefDto,
    ContextSourceStatus,
    ContextSourceType,
    JsonMap,
    ResolvedContextFactDto,
    SlotCoverageDto,
    VetContextBundleDto,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockDto,
    VetPromptBlockPriority,
    VetPromptBlockType,
)

DEFAULT_QUERY = "狗狗今天呕吐两次，精神稍差。"


class RecordingStandardTraceSink:
    """记录标准问诊 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: StandardTraceWriteStatus = StandardTraceWriteStatus.RECORDED,
        exception: Exception | None = None,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :param exception: 可选待抛出的异常，用于验证 trace 异常旁路。
        :return: None。
        """

        self.status = status
        self.exception = exception
        self.records: list[StandardConsultationTraceRecordDto] = []

    async def write_standard_trace(
        self,
        record: StandardConsultationTraceRecordDto,
    ) -> StandardTraceWriteResultDto:
        """记录标准问诊 trace 摘要并返回预设状态。

        :param record: 待记录的标准问诊 trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        :raises Exception: 当初始化传入 exception 时抛出该异常。
        """

        self.records.append(record)
        if self.exception is not None:
            raise self.exception
        return StandardTraceWriteResultDto(status=self.status)


class FakeStandardRagPort:
    """组件级测试使用的 RAG 端口替身。"""

    def __init__(
        self,
        *,
        bundle: RagEvidenceBundleDto | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        """初始化 RAG 端口替身。

        :param bundle: 每次检索返回的证据包；未传入时返回可用标准证据包。
        :param delay_seconds: 返回前等待秒数，用于验证超时降级。
        :return: None。
        """

        self.bundle = bundle or build_rag_bundle()
        self.delay_seconds = delay_seconds
        self.requests: list[
            tuple[StandardConsultationRequestDto, RetrievalPurpose]
        ] = []

    async def retrieve(
        self,
        *,
        request: StandardConsultationRequestDto,
        purpose: RetrievalPurpose,
        query_text: str,
        top_k: int,
        timeout_seconds: float,
    ) -> RagEvidenceBundleDto:
        """记录检索请求并返回预设证据包。

        :param request: 当前标准问诊请求。
        :param purpose: 本次检索用途。
        :param query_text: 本次检索查询文本。
        :param top_k: 最大返回条数。
        :param timeout_seconds: 本次检索超时秒数。
        :return: 初始化时传入的证据包。
        """

        del query_text, top_k, timeout_seconds
        self.requests.append((request, purpose))
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return self.bundle


class AllowingMedicationPolicyPort:
    """组件级测试使用的 MedicationPolicy 允许替身。"""

    def __init__(self) -> None:
        """初始化 MedicationPolicy 允许替身。

        :return: None。
        """

        self.requests: list[tuple[StandardConsultationRequestDto, float]] = []

    async def allows_care_plan(
        self,
        *,
        request: StandardConsultationRequestDto,
        contraindication_completeness: float,
    ) -> bool:
        """记录策略请求并允许进入 L4。

        :param request: 当前标准问诊请求。
        :param contraindication_completeness: 当前禁忌信息完整度。
        :return: 固定返回 True。
        """

        self.requests.append((request, contraindication_completeness))
        return True


class FakeAgentRunner:
    """组件级测试使用的 AgentRunner 替身。"""

    def __init__(
        self,
        *,
        outputs: list[dict[str, object]] | None = None,
        ready: bool = True,
    ) -> None:
        """初始化 AgentRunner 替身。

        :param outputs: 按调用顺序返回的 parsed_output 列表。
        :param ready: is_ready 返回值。
        :return: None。
        """

        self._outputs = list(outputs or [])
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
        :return: 使用预置 parsed_output 构建的 AgentRunner 结果。
        """

        self.requests.append(request)
        parsed_output = self._outputs.pop(0) if self._outputs else {}
        return build_agent_result(
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            parsed_output=parsed_output,
        )

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


def build_provider() -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :return: 已加载默认标准问诊配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider()


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给标准问诊图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_standard_1",
        trace_id="trace_standard_1",
        run_id="run_standard_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="standard_consultation_agent",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_source_ref(
    *,
    source_type: ContextSourceType = ContextSourceType.CURRENT_TASK,
    source_id: str = "task_standard_1",
    pet_id: str = "pet_1",
) -> ContextSourceRefDto:
    """构建测试使用的上下文来源引用。

    :param source_type: 来源类型。
    :param source_id: 来源对象 ID。
    :param pet_id: 来源绑定的宠物 ID。
    :return: 可用且新鲜的来源引用。
    """

    return ContextSourceRefDto(
        source_type=source_type,
        source_id=source_id,
        pet_id=pet_id,
        version="v1",
        freshness=ContextSourceFreshness.FRESH,
        status=ContextSourceStatus.AVAILABLE,
    )


def hash_text(value: str) -> str:
    """计算测试 prompt 块正文 hash。

    :param value: 待计算 hash 的文本。
    :return: 带 sha256 前缀的内容 hash。
    """

    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def build_context_bundle(
    *,
    current_pet_id: str = "pet_1",
    known_slots: JsonMap | None = None,
    missing_slots: list[str] | None = None,
    conflict_fact: bool = False,
) -> VetContextBundleDto:
    """构建标准问诊测试上下文 bundle。

    :param current_pet_id: 当前宠物 ID。
    :param known_slots: 可选已知槽位覆盖；未传入时使用标准半完整问诊槽位。
    :param missing_slots: 可选缺失槽位覆盖；未传入时使用标准缺失问诊槽位。
    :param conflict_fact: 是否将 species 事实标记为冲突。
    :return: 可被 StandardConsultationAgent 消费的上下文 bundle。
    """

    source_ref = build_source_ref(pet_id=current_pet_id)
    block_text = f"当前任务：{DEFAULT_QUERY}"
    prompt_block = VetPromptBlockDto(
        block_id="task_input",
        block_type=VetPromptBlockType.TASK_INPUT,
        priority=VetPromptBlockPriority.P0,
        required=True,
        content_ref_or_text=block_text,
        content_hash=hash_text(block_text),
        token_estimate=32,
        source_refs=[source_ref],
    )
    default_known_slots: JsonMap = {
        "species": "dog",
        "age": "adult",
        "weight_kg": 8.5,
    }
    resolved_known_slots: JsonMap = known_slots or default_known_slots
    resolved_missing_slots = missing_slots
    if resolved_missing_slots is None:
        resolved_missing_slots = [
            "symptom_duration",
            "symptom_frequency",
            "appetite",
            "hydration",
            "energy_level",
        ]
    fact_ledger = [
        ResolvedContextFactDto(
            key=key,
            value=value,
            state=ContextFactState.KNOWN,
            source_refs=[source_ref],
            conflict=(conflict_fact and key == "species"),
        )
        for key, value in resolved_known_slots.items()
    ]
    return VetContextBundleDto(
        task_id="task_standard_1",
        current_pet_id=current_pet_id,
        generation_profile=VetGenerationProfile.STANDARD,
        executor_key=VetExecutorKey.STANDARD_CONSULTATION,
        prompt_blocks=[prompt_block],
        fact_ledger=fact_ledger,
        slot_coverage=SlotCoverageDto(
            task_id="task_standard_1",
            known_slots=resolved_known_slots,
            missing_slots=resolved_missing_slots,
        ),
        source_refs=[source_ref],
        compression_audit=CompressionAuditDto(
            compression_strategy=ContextCompressionStrategy.SINGLE_FULL,
            token_budget=4096,
            estimated_tokens=32,
            trim_applied=False,
            p0_reinjected=False,
            included_block_ids=["task_input"],
        ),
        status=ContextBuildStatus.FULL,
        degraded_reasons=[],
        core_fact_snapshot_version="core.v1",
    )


def build_full_context_bundle() -> VetContextBundleDto:
    """构建信息相对完整的标准问诊上下文 bundle。

    :return: 已知槽位足以进入 L4 门槛的上下文 bundle。
    """

    return build_context_bundle(
        known_slots={
            "species": "dog",
            "age": "adult",
            "weight_kg": 8.5,
            "symptom_duration": "6 hours",
            "symptom_frequency": "twice",
            "appetite": "reduced",
            "hydration": "normal",
            "energy_level": "slightly low",
            "current_medications": "none",
            "allergies": "unknown",
            "pregnancy": "no",
            "chronic_conditions": "none",
        },
        missing_slots=[],
    )


def build_request(
    provider: RuntimeConfigProvider,
    *,
    generation_profile: str = "standard",
    executor_key: str = "standard_consultation",
    current_pet_id: str | None = "pet_1",
    context: VetContextBundleDto | None = None,
    session_state: dict[str, object] | None = None,
    question_budget: dict[str, object] | None = None,
) -> StandardConsultationRequestDto:
    """构建测试使用的标准问诊请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param generation_profile: 本次请求声明的生成剖面。
    :param executor_key: 本次请求声明的执行器。
    :param current_pet_id: 本次请求声明的当前宠物 ID。
    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param session_state: 可选标准问诊短期状态原始字段。
    :param question_budget: 可选问题预算原始字段。
    :return: 可传给 StandardConsultationAgent 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    values: dict[str, object] = {
        "request_id": "req_standard_1",
        "trace_id": "trace_standard_1",
        "run_id": "run_standard_1",
        "session_id": "session_1",
        "user_id": "user_1",
        "current_pet_id": current_pet_id,
        "task_id": "task_standard_1",
        "task_type": "TRIAGE",
        "normalized_query": DEFAULT_QUERY,
        "generation_profile": generation_profile,
        "executor_key": executor_key,
        "assessment_summary": {"intent": "symptom_triage"},
        "context": context or build_context_bundle(),
        "params_version": snapshot.params_version,
        "config_snapshot_id": snapshot.config_snapshot_id,
    }
    if session_state is not None:
        values["session_state"] = session_state
    if question_budget is not None:
        values["question_budget"] = question_budget
    return StandardConsultationRequestDto.model_validate(values)


def build_agent_result(
    *,
    agent_id: str = "standard_test_agent",
    agent_version: str = "v1",
    parsed_output: dict[str, object] | None = None,
    status: AgentRunStatus = AgentRunStatus.SUCCEEDED,
    schema_valid: bool = True,
) -> AgentRunResultDto:
    """构建测试使用的 AgentRunner 结果。

    :param agent_id: 模拟结果中的 Agent ID。
    :param agent_version: 模拟结果中的 Agent 版本。
    :param parsed_output: 模拟 AgentRunner 返回的结构化输出。
    :param status: 模拟 AgentRunner 运行状态。
    :param schema_valid: 模拟结果是否通过 schema 校验。
    :return: AgentRunner 标准运行结果 DTO。
    """

    return AgentRunResultDto(
        status=status,
        agent_id=agent_id,
        agent_version=agent_version,
        parsed_output=dict(parsed_output or {}),
        schema_valid=schema_valid,
    )


def build_rag_bundle(
    *,
    degraded: bool = False,
) -> RagEvidenceBundleDto:
    """构建测试使用的 RAG 证据包。

    :param degraded: 是否构建降级证据包。
    :return: 标准问诊 RAG 证据包。
    """

    if degraded:
        return RagEvidenceBundleDto(
            retrieval_purpose=RetrievalPurpose.STANDARD_PRESEARCH,
            degraded=True,
            degraded_reason="test_degraded",
        )
    return RagEvidenceBundleDto(
        retrieval_purpose=RetrievalPurpose.STANDARD_PRESEARCH,
        query_hashes=["sha256:test"],
        retrieval_ids=["retrieval_1"],
        source_versions=["kb.v1"],
        evidence_hints=[
            RagEvidenceHintDto(
                evidence_id="evidence_1",
                title="Vomiting triage",
                source_ref="kb://vomiting",
                summary="呕吐问诊需要评估频率、精神、饮水和脱水风险。",
            )
        ],
    )


def build_layered_agent_outputs() -> list[dict[str, object]]:
    """构建覆盖 L1 到 L4 路径的子 Agent 输出序列。

    :return: 按 AgentRunner 调用顺序排列的 parsed_output 列表。
    """

    return [
        {"candidate_questions": []},
        {"triage_summary": {"urgency": "routine"}},
        {"direction_hints": [{"direction": "gastrointestinal"}]},
        {"differential_hypotheses": [{"name": "dietary_indiscretion"}]},
        {"care_suggestions": [{"type": "supportive_care"}]},
        {
            "draft_response": "结构化草稿：当前更像轻中度胃肠道问题，继续观察并补充信息。",
            "evidence_bindings": [
                {
                    "claim_id": "claim_1",
                    "evidence_ids": ["evidence_1"],
                    "binding_summary": "依据呕吐分诊证据形成方向提示。",
                }
            ],
        },
    ]


def build_escalation_agent_outputs() -> list[dict[str, object]]:
    """构建触发急症升级路径的子 Agent 输出序列。

    :return: 按 AgentRunner 调用顺序排列的 parsed_output 列表。
    """

    return [
        {"candidate_questions": []},
        {
            "triage_summary": {"urgency": "urgent"},
            "escalation_request": {
                "reason_code": "persistent_collapse",
                "summary": "出现持续虚弱或疑似休克线索。",
            },
        },
        {"draft_response": "需要先进入急症安全处理路径。"},
    ]


def graph_state_for_standard_request(
    *,
    context: VetContextBundleDto | None = None,
    include_request: bool = True,
) -> dict[str, object]:
    """构建标准问诊图节点测试 state。

    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param include_request: 是否包含 standard_consultation_request 字段。
    :return: 可传给 GraphRuntime 节点的 state 映射。
    """

    state: dict[str, object] = {
        "context_bundle": (context or build_context_bundle()).model_dump(mode="json"),
        "original_user_message": DEFAULT_QUERY,
        "standard_session_state": {
            "standard_round_count": 1,
            "layer_state": {"updated_at": datetime.now(UTC).isoformat()},
        },
    }
    if include_request:
        state["standard_consultation_request"] = {
            "task_type": "TRIAGE",
            "normalized_query": DEFAULT_QUERY,
            "assessment_summary": {"intent": "symptom_triage"},
        }
    return state


__all__: tuple[str, ...] = (
    "AllowingMedicationPolicyPort",
    "DEFAULT_QUERY",
    "FakeAgentRunner",
    "FakeStandardRagPort",
    "RecordingStandardTraceSink",
    "build_agent_result",
    "build_context_bundle",
    "build_escalation_agent_outputs",
    "build_full_context_bundle",
    "build_graph_context",
    "build_layered_agent_outputs",
    "build_provider",
    "build_rag_bundle",
    "build_request",
    "build_source_ref",
    "graph_state_for_standard_request",
    "hash_text",
)
