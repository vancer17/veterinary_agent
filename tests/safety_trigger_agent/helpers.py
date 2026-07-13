##################################################################################################
# 文件: tests/safety_trigger_agent/helpers.py
# 作用: 提供 SafetyTriggerAgent 组件测试使用的请求构造器、上下文 bundle 和依赖替身。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、ToolRegistry 或 LogicTraceStore。
##################################################################################################

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
from veterinary_agent.safety_trigger_agent import (
    JsonMap,
    SafetyRagPolicySummaryDto,
    SafetyTraceWriteResultDto,
    SafetyTraceWriteStatus,
    SafetyTriggerRequestDto,
    SafetyTriggerTraceRecordDto,
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
    ResolvedContextFactDto,
    SlotCoverageDto,
    VetContextBundleDto,
    VetExecutorKey,
    VetGenerationProfile,
    VetPromptBlockDto,
    VetPromptBlockPriority,
    VetPromptBlockType,
)

DEFAULT_SAFETY_QUERY = "我家猫刚才误吃了布洛芬，现在有点流口水。"


class RecordingSafetyTraceSink:
    """记录急症 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: SafetyTraceWriteStatus = SafetyTraceWriteStatus.RECORDED,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :return: None。
        """

        self.status = status
        self.records: list[SafetyTriggerTraceRecordDto] = []

    async def write_safety_trace(
        self,
        record: SafetyTriggerTraceRecordDto,
    ) -> SafetyTraceWriteResultDto:
        """记录急症 trace 摘要并返回预设状态。

        :param record: 待记录的急症 trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        """

        self.records.append(record)
        return SafetyTraceWriteResultDto(status=self.status)


class AllowingSafetyToolPermissionPort:
    """组件级测试使用的工具权限允许替身。"""

    def __init__(self) -> None:
        """初始化工具权限允许替身。

        :return: None。
        """

        self.requests: list[tuple[SafetyTriggerRequestDto, list[str]]] = []

    def is_ready(self) -> bool:
        """判断测试替身是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def verify_no_rag_tools(
        self,
        *,
        request: SafetyTriggerRequestDto,
        agent_ids: list[str],
    ) -> SafetyRagPolicySummaryDto:
        """记录权限证明请求并返回通过摘要。

        :param request: 当前急症请求。
        :param agent_ids: 需要验证的内部 Agent ID 列表。
        :return: 表示无 RAG 工具权限的摘要。
        """

        self.requests.append((request, agent_ids))
        return SafetyRagPolicySummaryDto(verified=True)


class DenyingSafetyToolPermissionPort:
    """组件级测试使用的工具权限未验证替身。"""

    def __init__(
        self, *, degraded_reason: str = "test_rag_permission_unverified"
    ) -> None:
        """初始化工具权限未验证替身。

        :param degraded_reason: 返回给组件的权限降级原因。
        :return: None。
        """

        self.degraded_reason = degraded_reason
        self.requests: list[tuple[SafetyTriggerRequestDto, list[str]]] = []

    def is_ready(self) -> bool:
        """判断测试替身是否就绪。

        :return: 固定返回 True。
        """

        return True

    async def verify_no_rag_tools(
        self,
        *,
        request: SafetyTriggerRequestDto,
        agent_ids: list[str],
    ) -> SafetyRagPolicySummaryDto:
        """记录权限证明请求并返回未验证摘要。

        :param request: 当前急症请求。
        :param agent_ids: 需要验证的内部 Agent ID 列表。
        :return: 表示 RAG 禁用证明未完成的摘要。
        """

        self.requests.append((request, agent_ids))
        return SafetyRagPolicySummaryDto(
            verified=False,
            degraded_reason=self.degraded_reason,
        )


class FakeSafetyAgentRunner:
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

    :return: 已加载默认急症配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider()


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给急症图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_safety_1",
        trace_id="trace_safety_1",
        run_id="run_safety_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="safety_trigger_agent",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_source_ref(
    *,
    source_type: ContextSourceType = ContextSourceType.CURRENT_TASK,
    source_id: str = "task_safety_1",
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


def build_prompt_block(
    *,
    block_id: str,
    block_type: VetPromptBlockType,
    text: str,
    source_ref: ContextSourceRefDto,
) -> VetPromptBlockDto:
    """构建测试使用的 prompt block。

    :param block_id: prompt block ID。
    :param block_type: prompt block 类型。
    :param text: prompt block 文本。
    :param source_ref: prompt block 关联来源引用。
    :return: 可放入 VetContextBundleDto 的 prompt block。
    """

    return VetPromptBlockDto(
        block_id=block_id,
        block_type=block_type,
        priority=VetPromptBlockPriority.P0,
        required=True,
        content_ref_or_text=text,
        content_hash=hash_text(text),
        token_estimate=32,
        source_refs=[source_ref],
    )


def build_context_bundle(
    *,
    current_pet_id: str = "pet_1",
) -> VetContextBundleDto:
    """构建急症测试上下文 bundle。

    :param current_pet_id: 当前宠物 ID。
    :return: 可被 SafetyTriggerAgent 消费的 safety_minimal 上下文 bundle。
    """

    source_ref = build_source_ref(pet_id=current_pet_id)
    task_block = build_prompt_block(
        block_id="task_safety_1:task_input",
        block_type=VetPromptBlockType.TASK_INPUT,
        text=f"当前任务：{DEFAULT_SAFETY_QUERY}",
        source_ref=source_ref,
    )
    safety_block = build_prompt_block(
        block_id="task_safety_1:safety_assessment",
        block_type=VetPromptBlockType.SAFETY_ASSESSMENT,
        text="SAF-01 命中：布洛芬。",
        source_ref=source_ref,
    )
    fact_ledger = [
        ResolvedContextFactDto(
            key="species",
            value="cat",
            state=ContextFactState.KNOWN,
            source_refs=[source_ref],
        )
    ]
    return VetContextBundleDto(
        task_id="task_safety_1",
        current_pet_id=current_pet_id,
        generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
        executor_key=VetExecutorKey.SAFETY_TRIGGER,
        prompt_blocks=[task_block, safety_block],
        fact_ledger=fact_ledger,
        slot_coverage=SlotCoverageDto(
            task_id="task_safety_1",
            known_slots={"species": "cat"},
            missing_slots=[],
        ),
        source_refs=[source_ref],
        compression_audit=CompressionAuditDto(
            compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
            token_budget=4096,
            estimated_tokens=64,
            trim_applied=False,
            p0_reinjected=False,
            included_block_ids=[task_block.block_id, safety_block.block_id],
        ),
        status=ContextBuildStatus.MINIMAL,
        degraded_reasons=[],
        core_fact_snapshot_version="core.v1",
    )


def build_context_with_compression_strategy(
    strategy: ContextCompressionStrategy,
) -> VetContextBundleDto:
    """构建指定压缩策略的急症测试上下文 bundle。

    :param strategy: 需要写入压缩审计的策略。
    :return: 已替换压缩策略的上下文 bundle。
    """

    context = build_context_bundle()
    compression_audit = context.compression_audit.model_copy(
        update={"compression_strategy": strategy}
    )
    return context.model_copy(update={"compression_audit": compression_audit})


def build_context_with_generation_profile(
    generation_profile: VetGenerationProfile,
) -> VetContextBundleDto:
    """构建指定生成剖面的急症测试上下文 bundle。

    :param generation_profile: 需要写入上下文 bundle 的生成剖面。
    :return: 已替换生成剖面的上下文 bundle。
    """

    return build_context_bundle().model_copy(
        update={"generation_profile": generation_profile}
    )


def build_assessment_summary(
    *,
    include_rag: bool = False,
) -> JsonMap:
    """构建急症输入安全评估摘要。

    :param include_rag: 是否加入违规 RAG 摘要。
    :return: 可传给 SafetyTriggerAgent 的 assessment_summary。
    """

    summary: JsonMap = {
        "intent": "ACUTE_EVENT",
        "risk_level": "P0",
        "signals": [
            {
                "signal_id": "sig_saf01_1",
                "signal_code": "SAF_01_TOXIC_SUBSTANCE",
                "signal_strength": "L3",
                "normalized_concept": "布洛芬",
                "dictionary_version": "saf.v1",
                "risk_entity": "布洛芬",
            }
        ],
        "risk_entities": ["布洛芬"],
        "realtime_markers": ["刚才"],
    }
    if include_rag:
        summary["rag_invoked"] = True
        summary["retrieval_ids"] = ["retrieval_bad"]
    return summary


def build_request(
    provider: RuntimeConfigProvider,
    *,
    generation_profile: str = "safety_trigger",
    executor_key: str = "safety_trigger",
    current_pet_id: str | None = "pet_1",
    context: VetContextBundleDto | None = None,
    assessment_summary: JsonMap | None = None,
) -> SafetyTriggerRequestDto:
    """构建测试使用的急症请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param generation_profile: 本次请求声明的生成剖面。
    :param executor_key: 本次请求声明的执行器。
    :param current_pet_id: 本次请求声明的当前宠物 ID。
    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param assessment_summary: 可选安全评估摘要；未传入时使用默认 SAF-01 摘要。
    :return: 可传给 SafetyTriggerAgent 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    return SafetyTriggerRequestDto(
        request_id="req_safety_1",
        trace_id="trace_safety_1",
        run_id="run_safety_1",
        session_id="session_1",
        user_id="user_1",
        current_pet_id=current_pet_id,
        task_id="task_safety_1",
        task_type="ACUTE_EVENT",
        normalized_query=DEFAULT_SAFETY_QUERY,
        generation_profile=generation_profile,
        executor_key=executor_key,
        assessment_summary=(
            build_assessment_summary()
            if assessment_summary is None
            else assessment_summary
        ),
        context=context or build_context_bundle(),
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_agent_result(
    *,
    agent_id: str = "safety_test_agent",
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


def build_writer_outputs(*, safe: bool = True) -> list[dict[str, object]]:
    """构建 planner 与 writer 的 AgentRunner 输出序列。

    :param safe: 是否构建可通过自检的 writer 正文。
    :return: 按 AgentRunner 调用顺序排列的 parsed_output 列表。
    """

    writer_text = (
        "建议立即联系附近宠物医院或急诊兽医，并尽快带猫线下就医。"
        "布洛芬对猫可能有中毒风险，请带上包装和摄入时间。"
        "不要自行催吐、灌水或喂人药。线上提示不能替代线下兽医检查。"
    )
    if not safe:
        writer_text = "可以先在家观察，可能是胃肠不适。"
    return [
        {"mode": "NO_QUESTION", "reason_code": "agent_no_question"},
        {
            "draft_response": writer_text,
            "urgency_statement": "布洛芬对猫可能有中毒风险。",
            "vet_direction": "建议立即联系附近宠物医院或急诊兽医。",
            "safe_actions": ["带上包装和摄入时间"],
            "forbidden_actions": ["不要自行催吐", "不要喂人药"],
            "info_to_prepare": ["布洛芬包装", "摄入时间"],
        },
    ]


def graph_state_for_safety_request(
    *,
    context: VetContextBundleDto | None = None,
    include_request: bool = True,
) -> dict[str, object]:
    """构建急症图节点测试 state。

    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param include_request: 是否包含 safety_trigger_request 字段。
    :return: 可传给 GraphRuntime 节点的 state 映射。
    """

    state: dict[str, object] = {
        "context_bundle": (context or build_context_bundle()).model_dump(mode="json"),
        "original_user_message": DEFAULT_SAFETY_QUERY,
    }
    if include_request:
        state["safety_trigger_request"] = {
            "task_type": "ACUTE_EVENT",
            "normalized_query": DEFAULT_SAFETY_QUERY,
            "assessment_summary": build_assessment_summary(),
        }
    return state


__all__: tuple[str, ...] = (
    "AllowingSafetyToolPermissionPort",
    "DEFAULT_SAFETY_QUERY",
    "DenyingSafetyToolPermissionPort",
    "FakeSafetyAgentRunner",
    "RecordingSafetyTraceSink",
    "build_agent_result",
    "build_assessment_summary",
    "build_context_bundle",
    "build_context_with_compression_strategy",
    "build_context_with_generation_profile",
    "build_graph_context",
    "build_provider",
    "build_request",
    "build_source_ref",
    "build_writer_outputs",
    "graph_state_for_safety_request",
    "hash_text",
)
