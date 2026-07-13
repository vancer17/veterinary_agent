##################################################################################################
# 文件: tests/nonmedical_pet_care_agent/helpers.py
# 作用: 提供 NonmedicalPetCareAgent 组件测试使用的请求构造器、上下文 bundle、RAG 和 AgentRunner 替身。
# 边界: 只通过生产包一级出口导入公共契约，不接入真实 LLM、RAG、Trace 存储或输出安全审查。
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
    NonmedicalPetCareAgentSettings,
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext
from veterinary_agent.nonmedical_pet_care_agent import (
    AdviceDimensionCode,
    EvidenceHintDto,
    NonmedicalAdviceRequestDto,
    NonmedicalRagResultDto,
    NonmedicalRetrievalPurpose,
    NonmedicalTraceRecordDto,
    NonmedicalTraceWriteResultDto,
    NonmedicalTraceWriteStatus,
    RetrievalFacetDto,
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

DEFAULT_QUERY = "狗狗总是晚上叫，怎么训练更合适？"


class RecordingNonmedicalTraceSink:
    """记录非医疗 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: NonmedicalTraceWriteStatus = NonmedicalTraceWriteStatus.RECORDED,
        exception: Exception | None = None,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :param exception: 可选待抛出的异常，用于验证 trace 异常旁路。
        :return: None。
        """

        self.status = status
        self.exception = exception
        self.records: list[NonmedicalTraceRecordDto] = []

    async def write_nonmedical_trace(
        self,
        record: NonmedicalTraceRecordDto,
    ) -> NonmedicalTraceWriteResultDto:
        """记录非医疗 trace 摘要并返回预设状态。

        :param record: 待记录的非医疗 trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        :raises Exception: 当初始化传入 exception 时抛出该异常。
        """

        self.records.append(record)
        if self.exception is not None:
            raise self.exception
        return NonmedicalTraceWriteResultDto(status=self.status)


class FakeNonmedicalRagPort:
    """组件级测试使用的非医疗 RAG 端口替身。"""

    def __init__(
        self,
        *,
        result: NonmedicalRagResultDto | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        """初始化非医疗 RAG 端口替身。

        :param result: 每次检索返回的 RAG 结果；未传入时返回可用证据。
        :param delay_seconds: 返回前等待秒数，用于验证超时降级。
        :return: None。
        """

        self.result = result or build_rag_result()
        self.delay_seconds = delay_seconds
        self.requests: list[tuple[NonmedicalAdviceRequestDto, RetrievalFacetDto]] = []

    async def retrieve(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> NonmedicalRagResultDto:
        """记录检索请求并返回预设证据。

        :param request: 当前非医疗建议生成请求。
        :param facet: 本次检索的受控 facet。
        :param timeout_seconds: 本次检索超时秒数。
        :return: 初始化时传入的 RAG 结果。
        """

        del timeout_seconds
        self.requests.append((request, facet))
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return self.result.model_copy(
            update={
                "retrieval_purpose": facet.retrieval_purpose,
                "dimension_code": facet.dimension_code,
                "query_hashes": list(facet.query_hashes),
            }
        )


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


def build_provider(
    *,
    nonmedical_pet_care_settings: NonmedicalPetCareAgentSettings | None = None,
) -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :param nonmedical_pet_care_settings: 可选非医疗组件配置覆盖。
    :return: 已加载默认非医疗配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider(
        nonmedical_pet_care_settings=nonmedical_pet_care_settings
    )


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给非医疗图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_nonmedical_1",
        trace_id="trace_nonmedical_1",
        run_id="run_nonmedical_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="nonmedical_pet_care_agent",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_source_ref(
    *,
    source_type: ContextSourceType = ContextSourceType.CURRENT_TASK,
    source_id: str = "task_nonmedical_1",
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
    task_id: str = "task_nonmedical_1",
    generation_profile: VetGenerationProfile | None = None,
    executor_key: VetExecutorKey = VetExecutorKey.NONMEDICAL_PET_CARE,
    compression_strategy: ContextCompressionStrategy = (
        ContextCompressionStrategy.EDUCATION_LIGHT
    ),
) -> VetContextBundleDto:
    """构建非医疗测试上下文 bundle。

    :param current_pet_id: 当前宠物 ID。
    :param task_id: 子任务 ID。
    :param generation_profile: 上下文生成剖面。
    :param executor_key: 上下文执行器。
    :param compression_strategy: 上下文压缩策略。
    :return: 可被 NonmedicalPetCareAgent 消费的轻量上下文 bundle。
    """

    source_ref = build_source_ref(pet_id=current_pet_id, source_id=task_id)
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
    facts = {
        "species": "dog",
        "age": "adult",
        "weight_kg": 8.5,
        "activity_level": "medium",
    }
    fact_ledger = [
        ResolvedContextFactDto(
            key=key,
            value=value,
            state=ContextFactState.KNOWN,
            source_refs=[source_ref],
        )
        for key, value in facts.items()
    ]
    return VetContextBundleDto(
        task_id=task_id,
        current_pet_id=current_pet_id,
        generation_profile=generation_profile,
        executor_key=executor_key,
        prompt_blocks=[prompt_block],
        fact_ledger=fact_ledger,
        slot_coverage=SlotCoverageDto(
            task_id=task_id,
            known_slots=facts,
            missing_slots=[],
        ),
        source_refs=[source_ref],
        compression_audit=CompressionAuditDto(
            compression_strategy=compression_strategy,
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


def build_request(
    provider: RuntimeConfigProvider,
    *,
    generation_profile: str | None = None,
    executor_key: str = "nonmedical_pet_care",
    current_pet_id: str | None = "pet_1",
    signals: list[dict[str, object]] | None = None,
    context: VetContextBundleDto | None = None,
) -> NonmedicalAdviceRequestDto:
    """构建测试使用的非医疗请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param generation_profile: 本次请求声明的生成剖面。
    :param executor_key: 本次请求声明的执行器。
    :param current_pet_id: 本次请求声明的当前宠物 ID。
    :param signals: 可选输入安全信号列表。
    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :return: 可传给 NonmedicalPetCareAgent 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    values: dict[str, object] = {
        "request_id": "req_nonmedical_1",
        "trace_id": "trace_nonmedical_1",
        "run_id": "run_nonmedical_1",
        "session_id": "session_1",
        "user_id": "user_1",
        "current_pet_id": current_pet_id,
        "task_id": "task_nonmedical_1",
        "task_type": "BEHAVIOR",
        "normalized_query": DEFAULT_QUERY,
        "generation_profile": generation_profile,
        "executor_key": executor_key,
        "assessment_summary": {
            "intent": "NONMEDICAL_PET_CARE",
            "executor_key": executor_key,
            "signals": list(signals or []),
        },
        "context": context or build_context_bundle(),
        "params_version": snapshot.params_version,
        "config_snapshot_id": snapshot.config_snapshot_id,
    }
    return NonmedicalAdviceRequestDto.model_validate(values)


def build_agent_result(
    *,
    agent_id: str = "nonmedical_test_agent",
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


def build_rag_result(
    *,
    degraded: bool = False,
) -> NonmedicalRagResultDto:
    """构建测试使用的非医疗 RAG 结果。

    :param degraded: 是否构建降级结果。
    :return: 非医疗 RAG 检索结果。
    """

    if degraded:
        return NonmedicalRagResultDto(
            retrieval_purpose=NonmedicalRetrievalPurpose.BEHAVIOR_GUIDANCE,
            dimension_code=AdviceDimensionCode.STEPWISE_PLAN,
            degraded=True,
            degraded_reason="test_degraded",
        )
    return NonmedicalRagResultDto(
        retrieval_purpose=NonmedicalRetrievalPurpose.BEHAVIOR_GUIDANCE,
        dimension_code=AdviceDimensionCode.STEPWISE_PLAN,
        query_hashes=["sha256:test"],
        retrieval_ids=["retrieval_nonmedical_1"],
        source_versions=["kb.v1"],
        evidence_hints=[
            EvidenceHintDto(
                evidence_id="evidence_nonmedical_1",
                title="Behavior management",
                source_ref="kb://behavior",
                summary="夜间吠叫管理应结合环境、作息、需求满足和渐进训练，避免惩罚式处理。",
                species_scope="dog",
                source_policy="public_summary",
                public_citable=True,
            )
        ],
    )


def build_success_agent_outputs() -> list[dict[str, object]]:
    """构建覆盖非医疗完整路径的子 Agent 输出序列。

    :return: 按 AgentRunner 调用顺序排列的 parsed_output 列表。
    """

    return [
        {
            "advice_axis": DEFAULT_QUERY,
            "dimensions": [
                {
                    "dimension_code": "STEPWISE_PLAN",
                    "required": True,
                    "evidence_requirement": "需要行为管理原则。",
                },
                {
                    "dimension_code": "RISK_BOUNDARY",
                    "required": True,
                    "evidence_requirement": "需要说明异常边界。",
                },
            ],
            "generation_constraints": ["不得使用惩罚式训练"],
            "safety_boundary_hints": ["异常表现要排除健康问题"],
        },
        {
            "rag_required": True,
            "facets": [
                {
                    "dimension_code": "STEPWISE_PLAN",
                    "queries": ["dog night barking behavior management"],
                    "collections": ["pet_care_kb_public_mvp"],
                }
            ],
        },
        {
            "draft_response": (
                "可以先从作息、环境和需求满足排查，再做渐进训练。"
                "晚上叫时不要用打骂方式处理，先观察是否有疼痛、焦虑或排泄需求。"
            )
        },
        {"passed": True, "risk_flags": []},
    ]


def graph_state_for_nonmedical_request(
    *,
    context: VetContextBundleDto | None = None,
    include_request: bool = True,
    signals: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """构建非医疗图节点测试 state。

    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param include_request: 是否包含 nonmedical_advice_request 字段。
    :param signals: 可选输入安全信号列表。
    :return: 可传给 GraphRuntime 节点的 state 映射。
    """

    state: dict[str, object] = {
        "context_bundle": (context or build_context_bundle()).model_dump(mode="json"),
        "original_user_message": DEFAULT_QUERY,
        "nonmedical_session_state": {
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }
    if include_request:
        state["nonmedical_advice_request"] = {
            "task_type": "BEHAVIOR",
            "normalized_query": DEFAULT_QUERY,
            "assessment_summary": {
                "intent": "NONMEDICAL_PET_CARE",
                "executor_key": "nonmedical_pet_care",
                "signals": list(signals or []),
            },
        }
    return state


__all__: tuple[str, ...] = (
    "DEFAULT_QUERY",
    "FakeAgentRunner",
    "FakeNonmedicalRagPort",
    "RecordingNonmedicalTraceSink",
    "build_agent_result",
    "build_context_bundle",
    "build_graph_context",
    "build_provider",
    "build_rag_result",
    "build_request",
    "build_source_ref",
    "build_success_agent_outputs",
    "graph_state_for_nonmedical_request",
    "hash_text",
)
