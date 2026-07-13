##################################################################################################
# 文件: tests/education_agent/helpers.py
# 作用: 提供 EducationAgent 组件测试使用的请求构造器、上下文 bundle、RAG 和 AgentRunner 替身。
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
    RuntimeConfigProvider,
    create_runtime_config_provider,
)
from veterinary_agent.education_agent import (
    EducationGenerationRequestDto,
    EducationRagResultDto,
    EducationRetrievalPurpose,
    EducationTraceRecordDto,
    EducationTraceWriteResultDto,
    EducationTraceWriteStatus,
    EvidenceHintDto,
    ExplanationDimensionCode,
    RetrievalFacetDto,
)
from veterinary_agent.graph_runtime import GraphNodeExecutionContext
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

DEFAULT_QUERY = "狗狗抽搐一般有哪些可能原因？"


class RecordingEducationTraceSink:
    """记录科普 trace 摘要的测试 sink。"""

    def __init__(
        self,
        *,
        status: EducationTraceWriteStatus = EducationTraceWriteStatus.RECORDED,
        exception: Exception | None = None,
    ) -> None:
        """初始化测试 trace sink。

        :param status: 每次写入返回的 trace 状态。
        :param exception: 可选待抛出的异常，用于验证 trace 异常旁路。
        :return: None。
        """

        self.status = status
        self.exception = exception
        self.records: list[EducationTraceRecordDto] = []

    async def write_education_trace(
        self,
        record: EducationTraceRecordDto,
    ) -> EducationTraceWriteResultDto:
        """记录科普 trace 摘要并返回预设状态。

        :param record: 待记录的科普 trace 摘要。
        :return: 使用预设状态构建的 trace 写入结果。
        :raises Exception: 当初始化传入 exception 时抛出该异常。
        """

        self.records.append(record)
        if self.exception is not None:
            raise self.exception
        return EducationTraceWriteResultDto(status=self.status)


class FakeEducationRagPort:
    """组件级测试使用的科普 RAG 端口替身。"""

    def __init__(
        self,
        *,
        result: EducationRagResultDto | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        """初始化科普 RAG 端口替身。

        :param result: 每次检索返回的 RAG 结果；未传入时返回可用证据。
        :param delay_seconds: 返回前等待秒数，用于验证超时降级。
        :return: None。
        """

        self.result = result or build_rag_result()
        self.delay_seconds = delay_seconds
        self.requests: list[
            tuple[EducationGenerationRequestDto, RetrievalFacetDto]
        ] = []

    async def retrieve(
        self,
        *,
        request: EducationGenerationRequestDto,
        facet: RetrievalFacetDto,
        timeout_seconds: float,
    ) -> EducationRagResultDto:
        """记录检索请求并返回预设证据。

        :param request: 当前科普生成请求。
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


def build_provider() -> RuntimeConfigProvider:
    """构建测试使用的 RuntimeConfig provider。

    :return: 已加载默认科普配置的 RuntimeConfig provider。
    """

    return create_runtime_config_provider()


def build_graph_context(provider: RuntimeConfigProvider) -> GraphNodeExecutionContext:
    """构建测试使用的 GraphNodeExecutionContext。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :return: 可传给科普图节点的执行上下文。
    """

    snapshot = provider.current_snapshot()
    return GraphNodeExecutionContext(
        request_id="req_education_1",
        trace_id="trace_education_1",
        run_id="run_education_1",
        graph_id="vet_conversation_graph",
        graph_version="test",
        node_id="education_agent",
        session_id="session_1",
        user_id="user_1",
        current_pet_id="pet_1",
        params_version=snapshot.params_version,
        config_snapshot_id=snapshot.config_snapshot_id,
    )


def build_source_ref(
    *,
    source_type: ContextSourceType = ContextSourceType.CURRENT_TASK,
    source_id: str = "task_education_1",
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
    task_id: str = "task_education_1",
) -> VetContextBundleDto:
    """构建科普测试上下文 bundle。

    :param current_pet_id: 当前宠物 ID。
    :param task_id: 子任务 ID。
    :return: 可被 EducationAgent 消费的 education_light 上下文 bundle。
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
        generation_profile=VetGenerationProfile.EDUCATION,
        executor_key=VetExecutorKey.EDUCATION,
        prompt_blocks=[prompt_block],
        fact_ledger=fact_ledger,
        slot_coverage=SlotCoverageDto(
            task_id=task_id,
            known_slots=facts,
            missing_slots=[],
        ),
        source_refs=[source_ref],
        compression_audit=CompressionAuditDto(
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
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
    generation_profile: str = "education",
    executor_key: str = "education",
    current_pet_id: str | None = "pet_1",
    context: VetContextBundleDto | None = None,
) -> EducationGenerationRequestDto:
    """构建测试使用的科普请求。

    :param provider: 已加载测试配置的 RuntimeConfig provider。
    :param generation_profile: 本次请求声明的生成剖面。
    :param executor_key: 本次请求声明的执行器。
    :param current_pet_id: 本次请求声明的当前宠物 ID。
    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :return: 可传给 EducationAgent 的严格请求 DTO。
    """

    snapshot = provider.current_snapshot()
    values: dict[str, object] = {
        "request_id": "req_education_1",
        "trace_id": "trace_education_1",
        "run_id": "run_education_1",
        "session_id": "session_1",
        "user_id": "user_1",
        "current_pet_id": current_pet_id,
        "task_id": "task_education_1",
        "task_type": "EDUCATION",
        "normalized_query": DEFAULT_QUERY,
        "generation_profile": generation_profile,
        "executor_key": executor_key,
        "assessment_summary": {
            "intent": "EDUCATION",
            "continue_recent_topic": False,
        },
        "context": context or build_context_bundle(),
        "params_version": snapshot.params_version,
        "config_snapshot_id": snapshot.config_snapshot_id,
    }
    return EducationGenerationRequestDto.model_validate(values)


def build_agent_result(
    *,
    agent_id: str = "education_test_agent",
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
) -> EducationRagResultDto:
    """构建测试使用的科普 RAG 结果。

    :param degraded: 是否构建降级结果。
    :return: 科普 RAG 检索结果。
    """

    if degraded:
        return EducationRagResultDto(
            retrieval_purpose=EducationRetrievalPurpose.EDUCATION_EXPLANATION,
            dimension_code=ExplanationDimensionCode.DEFINITION,
            degraded=True,
            degraded_reason="test_degraded",
        )
    return EducationRagResultDto(
        retrieval_purpose=EducationRetrievalPurpose.EDUCATION_EXPLANATION,
        dimension_code=ExplanationDimensionCode.DEFINITION,
        query_hashes=["sha256:test"],
        retrieval_ids=["retrieval_education_1"],
        source_versions=["kb.v1"],
        evidence_hints=[
            EvidenceHintDto(
                evidence_id="evidence_education_1",
                title="Seizure overview",
                source_ref="kb://seizure",
                summary="抽搐可与神经系统问题、代谢异常、中毒或高热等方向相关。",
                species_scope="dog",
                source_policy="public_summary",
                public_citable=True,
                restricted=False,
            )
        ],
    )


def build_success_agent_outputs() -> list[dict[str, object]]:
    """构建覆盖科普完整路径的子 Agent 输出序列。

    :return: 按 AgentRunner 调用顺序排列的 parsed_output 列表。
    """

    return [
        {
            "main_axis": DEFAULT_QUERY,
            "dimensions": [
                {
                    "dimension_code": "DEFINITION",
                    "required": True,
                    "evidence_requirement": "需要说明抽搐概念。",
                }
            ],
            "generation_constraints": ["不得确诊"],
            "safety_boundary_hints": ["出现持续抽搐要就医"],
        },
        {
            "facets": [
                {
                    "dimension_code": "DEFINITION",
                    "queries": ["dog seizure overview"],
                    "collections": ["vet_kb_public_mvp"],
                }
            ]
        },
        {
            "draft_response": (
                "抽搐是肌肉不受控制地快速收缩或意识状态异常的一类表现。"
                "常见方向包括神经系统问题、代谢异常、中毒或高热等，"
                "如果正在持续抽搐或反复发作，应尽快联系线下兽医。"
            ),
            "section_titles": ["概念", "常见方向", "边界"],
            "evidence_bindings": [
                {
                    "claim_id": "claim_1",
                    "evidence_card_ids": ["ecard_1"],
                    "retrieval_ids": ["retrieval_education_1"],
                    "binding_summary": "依据抽搐科普证据说明常见方向。",
                }
            ],
        },
        {"passed": True, "risk_flags": []},
    ]


def graph_state_for_education_request(
    *,
    context: VetContextBundleDto | None = None,
    include_request: bool = True,
) -> dict[str, object]:
    """构建科普图节点测试 state。

    :param context: 可选上下文 bundle；未传入时使用默认 bundle。
    :param include_request: 是否包含 education_generation_request 字段。
    :return: 可传给 GraphRuntime 节点的 state 映射。
    """

    state: dict[str, object] = {
        "context_bundle": (context or build_context_bundle()).model_dump(mode="json"),
        "original_user_message": DEFAULT_QUERY,
        "education_session_state": {
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }
    if include_request:
        state["education_generation_request"] = {
            "task_type": "EDUCATION",
            "normalized_query": DEFAULT_QUERY,
            "assessment_summary": {"intent": "EDUCATION"},
        }
    return state


__all__: tuple[str, ...] = (
    "DEFAULT_QUERY",
    "FakeAgentRunner",
    "FakeEducationRagPort",
    "RecordingEducationTraceSink",
    "build_agent_result",
    "build_context_bundle",
    "build_graph_context",
    "build_provider",
    "build_rag_result",
    "build_request",
    "build_source_ref",
    "build_success_agent_outputs",
    "graph_state_for_education_request",
    "hash_text",
)
