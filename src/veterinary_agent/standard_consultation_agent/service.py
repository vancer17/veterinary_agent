##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/service.py
# 作用: 实现 StandardConsultationAgent 应用内服务，编排 readiness、RAG、受控子 Agent 与草稿合成。
# 边界: 不执行输入安全判决、不直接发布用户回复、不写 checkpoint、不实现 RAG 或 MedicationPolicy 领域能力。
##################################################################################################

import asyncio
from collections.abc import Mapping
from enum import Enum
from time import perf_counter
from typing import Protocol, TypeVar

from veterinary_agent.agent_runner import (
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunStatus,
    AgentRunner,
)
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    StandardConsultationAgentSettings,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.standard_consultation_agent.dto import (
    CandidateQuestionDto,
    EscalationRequestDto,
    EvidenceBindingDto,
    JsonMap,
    RagEvidenceBundleDto,
    ReadinessProfileDto,
    SlotProgressPatchDto,
    StandardConsultationDraftDto,
    StandardConsultationRequestDto,
    StandardConsultationTraceRecordDto,
    StandardTracePatchDto,
    StandardTraceWriteResultDto,
)
from veterinary_agent.standard_consultation_agent.enums import (
    ConsultationLayer,
    DraftStatus,
    QuestionPurpose,
    RetrievalPurpose,
    RiskImpact,
    StandardConsultationErrorCode,
    StandardConsultationOperation,
    StandardTraceWriteStatus,
)
from veterinary_agent.standard_consultation_agent.errors import (
    StandardConsultationError,
)
from veterinary_agent.standard_consultation_agent.ports import (
    StandardMedicationPolicyPort,
    StandardRagPort,
    TodoStandardMedicationPolicyPort,
    TodoStandardRagPort,
)
from veterinary_agent.standard_consultation_agent.trace import (
    StandardConsultationTraceSink,
    TodoStandardConsultationTraceSink,
)
from veterinary_agent.vet_context_builder import (
    VetExecutorKey,
    VetGenerationProfile,
    to_agent_prompt_blocks,
)

_COMPONENT_NAME = "standard_consultation_agent"
_STANDARD_PROFILE = VetGenerationProfile.STANDARD.value
_STANDARD_EXECUTOR = VetExecutorKey.STANDARD_CONSULTATION.value
EnumT = TypeVar("EnumT", bound=Enum)
_SLOT_QUESTION_TEXT: dict[str, str] = {
    "species": "这次咨询的是猫、狗，还是其他动物？",
    "age": "它现在大约多大年龄？",
    "weight_kg": "它目前体重大约是多少公斤？",
    "symptom_duration": "这个情况已经持续多久了？",
    "symptom_frequency": "症状大概多久出现一次，今天一共发生了几次？",
    "appetite": "它今天食欲和平时相比有没有明显变化？",
    "hydration": "它喝水、排尿或口腔湿润程度有没有异常？",
    "energy_level": "它精神状态、活动量和平时相比怎么样？",
}
_HIGH_RISK_SLOTS: frozenset[str] = frozenset(
    {"symptom_duration", "symptom_frequency", "hydration", "energy_level"}
)
_CONTRAINDICATION_SLOTS: frozenset[str] = frozenset(
    {
        "species",
        "age",
        "weight_kg",
        "current_medications",
        "allergies",
        "pregnancy",
        "chronic_conditions",
    }
)


class StandardConsultationAgent(Protocol):
    """StandardConsultationAgent 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断标准问诊服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且组件已启用则返回 True。
        """

        ...

    def evaluate_readiness(
        self,
        request: StandardConsultationRequestDto,
    ) -> ReadinessProfileDto:
        """计算标准问诊信息完备度。

        :param request: 当前标准问诊请求。
        :return: 中控调度使用的 readiness profile。
        """

        ...

    async def generate_draft(
        self,
        request: StandardConsultationRequestDto,
    ) -> StandardConsultationDraftDto:
        """生成标准问诊结构化草稿。

        :param request: 当前标准问诊请求。
        :return: 待输出安全审查的标准问诊草稿。
        """

        ...


def _elapsed_ms(started_at: float) -> int:
    """计算从单调时钟起点到当前的毫秒数。

    :param started_at: ``perf_counter`` 记录的起点。
    :return: 四舍五入后的非负毫秒数。
    """

    return max(0, round((perf_counter() - started_at) * 1000))


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _as_list(value: object) -> list[object]:
    """将未知值安全读取为列表。

    :param value: 需要读取的未知值。
    :return: 若输入为列表或元组则返回普通列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_float(value: object, *, default: float) -> float:
    """从未知值中读取 0 到 1 之间的浮点数。

    :param value: 需要读取的未知值。
    :param default: 无法读取数值时使用的默认值。
    :return: 归一化到 0 到 1 区间内的浮点数。
    """

    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return default


def _enum_or_default(
    *,
    enum_type: type[EnumT],
    value: object,
    default: EnumT,
) -> EnumT:
    """将未知枚举值转换为受控枚举。

    :param enum_type: 目标枚举类型。
    :param value: 原始枚举值。
    :param default: 转换失败时使用的默认枚举。
    :return: 解析后的枚举或默认值。
    """

    try:
        if isinstance(value, str):
            return enum_type(value)
    except ValueError:
        return default
    return default


def _known_ratio(*, known_count: int, total_count: int) -> float:
    """计算槽位已知比例。

    :param known_count: 已知槽位数量。
    :param total_count: 总槽位数量。
    :return: 已知比例；没有槽位要求时返回 1。
    """

    if total_count <= 0:
        return 1.0
    return max(0.0, min(1.0, known_count / total_count))


class DefaultStandardConsultationAgent:
    """StandardConsultationAgent 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        rag_port: StandardRagPort | None = None,
        medication_policy_port: StandardMedicationPolicyPort | None = None,
        trace_sink: StandardConsultationTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 StandardConsultationAgent 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 端口；缺失时进入保守草稿降级。
        :param rag_port: 可选 RagPlatform 端口；缺失时使用 TODO 降级空壳。
        :param medication_policy_port: 可选 MedicationPolicy 端口；缺失时保守禁止 L4。
        :param trace_sink: 可选标准问诊 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._rag_port = rag_port or TodoStandardRagPort()
        self._medication_policy_port = (
            medication_policy_port or TodoStandardMedicationPolicyPort()
        )
        self._trace_sink = trace_sink or TodoStandardConsultationTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断标准问诊服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 StandardConsultationAgent 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.standard_consultation.enabled

    def evaluate_readiness(
        self,
        request: StandardConsultationRequestDto,
    ) -> ReadinessProfileDto:
        """计算标准问诊信息完备度。

        :param request: 当前标准问诊请求。
        :return: 中控调度使用的 readiness profile。
        """

        slot_coverage = request.context.slot_coverage
        known_slots = set(slot_coverage.known_slots)
        missing_slots = set(slot_coverage.missing_slots)
        stale_slots = set(slot_coverage.stale_slots)
        pending_slots = set(slot_coverage.pending_confirmation_slots)
        total_slots = len(known_slots | missing_slots | stale_slots | pending_slots)
        known_slot_ratio = _known_ratio(
            known_count=len(known_slots),
            total_count=total_slots,
        )
        high_risk_total = len(_HIGH_RISK_SLOTS)
        high_risk_known = len(known_slots.intersection(_HIGH_RISK_SLOTS))
        contraindication_total = len(_CONTRAINDICATION_SLOTS)
        contraindication_known = len(known_slots.intersection(_CONTRAINDICATION_SLOTS))
        conflicts = sum(1 for fact in request.context.fact_ledger if fact.conflict)
        answer_consistency = (
            1.0
            if not request.context.fact_ledger
            else 1.0 - (conflicts / len(request.context.fact_ledger))
        )
        hard_gates: list[str] = []
        if "species" in missing_slots:
            hard_gates.append("missing_species")
        if stale_slots:
            hard_gates.append("has_stale_slots")
        return ReadinessProfileDto(
            symptom_entity_confidence=known_slot_ratio,
            high_risk_field_completeness=_known_ratio(
                known_count=high_risk_known,
                total_count=high_risk_total,
            ),
            rag_evidence_readiness=0.0,
            differential_convergence=known_slot_ratio,
            contraindication_completeness=_known_ratio(
                known_count=contraindication_known,
                total_count=contraindication_total,
            ),
            answer_consistency=max(0.0, min(1.0, answer_consistency)),
            hard_gates=hard_gates,
        )

    async def generate_draft(
        self,
        request: StandardConsultationRequestDto,
    ) -> StandardConsultationDraftDto:
        """生成标准问诊结构化草稿。

        :param request: 当前标准问诊请求。
        :return: 待输出安全审查的标准问诊草稿。
        :raises StandardConsultationError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_at = perf_counter()
        draft: StandardConsultationDraftDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.standard_consultation
            self._validate_request_or_raise(request=request, settings=settings)
            readiness = self.evaluate_readiness(request)
            rag_bundles = await self._retrieve_initial_evidence(
                request=request,
                settings=settings,
            )
            readiness = readiness.model_copy(
                update={
                    "rag_evidence_readiness": self._rag_readiness(bundles=rag_bundles)
                }
            )
            agent_state = await self._run_controlled_sub_agents(
                request=request,
                readiness=readiness,
                rag_bundles=rag_bundles,
                settings=settings,
            )
            draft = self._build_draft(
                request=request,
                readiness=readiness,
                rag_bundles=rag_bundles,
                agent_state=agent_state,
                settings=settings,
            )
            trace_result = await self._write_trace_safely(
                request=request,
                draft=draft,
            )
            draft = draft.model_copy(
                update={"trace_delivery_status": trace_result.status}
            )
            return draft
        except StandardConsultationError:
            raise
        except RuntimeConfigError as exc:
            raise StandardConsultationError(
                code=(
                    StandardConsultationErrorCode.STANDARD_RUNTIME_CONFIG_UNAVAILABLE
                ),
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="StandardConsultationAgent 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except Exception as exc:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_INTERNAL_ERROR,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="StandardConsultationAgent 发生未映射内部错误",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"exception_type": type(exc).__name__},
            ) from exc
        finally:
            self._record_observability(
                request=request,
                draft=draft,
                duration_ms=_elapsed_ms(started_at),
            )

    def _load_config_snapshot(
        self,
        *,
        request: StandardConsultationRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前标准问诊请求使用的配置快照。

        :param request: 当前标准问诊请求。
        :return: 与请求版本一致且启用 StandardConsultationAgent 的配置快照。
        :raises StandardConsultationError: 当配置不可用、未启用或版本不一致时抛出。
        """

        if not self._runtime_config_provider.is_ready():
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_NOT_READY,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="RuntimeConfig provider 未就绪",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.standard_consultation.enabled:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_NOT_READY,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="StandardConsultationAgent 已被配置关闭",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.params_version != snapshot.params_version:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_CONTEXT_MISSING,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="请求参数版本与当前 RuntimeConfig 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "request_params_version": request.params_version,
                    "snapshot_params_version": snapshot.params_version,
                },
            )
        return snapshot

    def _validate_request_or_raise(
        self,
        *,
        request: StandardConsultationRequestDto,
        settings: StandardConsultationAgentSettings,
    ) -> None:
        """校验标准问诊输入剖面、宠物作用域和上下文契约。

        :param request: 当前标准问诊请求。
        :param settings: 当前标准问诊配置；用于证明配置已解析。
        :return: None。
        :raises StandardConsultationError: 当前置契约不满足时抛出稳定错误。
        """

        del settings
        if request.current_pet_id is None:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_MISSING_CURRENT_PET_ID,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊请求缺少 current_pet_id",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.generation_profile != _STANDARD_PROFILE:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_PROFILE_MISMATCH,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊仅接受 generation_profile=standard",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"generation_profile": request.generation_profile},
            )
        context_profile = (
            request.context.generation_profile.value
            if request.context.generation_profile is not None
            else None
        )
        if context_profile != _STANDARD_PROFILE:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_PROFILE_MISMATCH,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊上下文不是 standard 剖面",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"context_generation_profile": context_profile},
            )
        if request.current_pet_id != request.context.current_pet_id:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_PET_CONTEXT_INVALID,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊请求宠物 ID 与上下文宠物 ID 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "request_pet_id": request.current_pet_id,
                    "context_pet_id": request.context.current_pet_id,
                },
            )
        if request.task_id != request.context.task_id:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_CONTEXT_MISSING,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊请求 task_id 与上下文 task_id 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.executor_key != _STANDARD_EXECUTOR:
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_PROFILE_MISMATCH,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="标准问诊仅接受 standard_consultation 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"executor_key": request.executor_key},
            )

    async def _retrieve_initial_evidence(
        self,
        *,
        request: StandardConsultationRequestDto,
        settings: StandardConsultationAgentSettings,
    ) -> list[RagEvidenceBundleDto]:
        """按配置执行标准问诊前置 RAG 检索。

        :param request: 当前标准问诊请求。
        :param settings: 当前标准问诊配置。
        :return: 本轮已获得或降级的 RAG 证据包列表。
        """

        if not settings.rag.enabled or not settings.rag.presearch_enabled:
            return []
        try:
            bundle = await asyncio.wait_for(
                self._rag_port.retrieve(
                    request=request,
                    purpose=RetrievalPurpose.STANDARD_PRESEARCH,
                    query_text=request.normalized_query,
                    top_k=settings.rag.top_k,
                    timeout_seconds=settings.timeouts.rag_seconds,
                ),
                timeout=settings.timeouts.rag_seconds,
            )
            return [bundle]
        except TimeoutError:
            return [
                RagEvidenceBundleDto(
                    retrieval_purpose=RetrievalPurpose.STANDARD_PRESEARCH,
                    degraded=True,
                    degraded_reason="STANDARD_RAG_TIMEOUT",
                )
            ]
        except Exception as exc:
            return [
                RagEvidenceBundleDto(
                    retrieval_purpose=RetrievalPurpose.STANDARD_PRESEARCH,
                    degraded=True,
                    degraded_reason=f"STANDARD_RAG_ERROR:{type(exc).__name__}",
                )
            ]

    async def _run_controlled_sub_agents(
        self,
        *,
        request: StandardConsultationRequestDto,
        readiness: ReadinessProfileDto,
        rag_bundles: list[RagEvidenceBundleDto],
        settings: StandardConsultationAgentSettings,
    ) -> JsonMap:
        """执行受控 MAS 子 Agent 调度计划。

        :param request: 当前标准问诊请求。
        :param readiness: 当前 readiness profile。
        :param rag_bundles: 当前可用或降级的 RAG 证据包。
        :param settings: 当前标准问诊配置。
        :return: 包含子 Agent 输出、激活列表和降级标记的中间状态。
        """

        activated_agents: list[str] = []
        degraded_flags: list[str] = []
        collector_output = await self._run_sub_agent(
            request=request,
            agent_id=settings.question_collector_agent_id,
            agent_version=settings.question_collector_agent_version,
            stage="question_collector",
            task_input=self._base_task_input(
                request=request,
                readiness=readiness,
                rag_bundles=rag_bundles,
            ),
            timeout_seconds=settings.timeouts.sub_agent_seconds,
        )
        if collector_output is None:
            degraded_flags.append("question_collector_unavailable")
        else:
            activated_agents.append(settings.question_collector_agent_id)
        candidate_questions = self._candidate_questions_from_output(
            output=collector_output,
            request=request,
        )
        if not candidate_questions:
            candidate_questions = self._deterministic_candidate_questions(request)
        selected_questions = self._select_questions(
            request=request,
            questions=candidate_questions,
            settings=settings,
        )
        triage_output = await self._run_sub_agent(
            request=request,
            agent_id=settings.triage_agent_id,
            agent_version=settings.triage_agent_version,
            stage="triage_urgency",
            task_input={
                **self._base_task_input(
                    request=request,
                    readiness=readiness,
                    rag_bundles=rag_bundles,
                ),
                "selected_question_ids": [
                    question.question_id for question in selected_questions
                ],
            },
            timeout_seconds=settings.timeouts.sub_agent_seconds,
        )
        if triage_output is None:
            degraded_flags.append("triage_unavailable")
        else:
            activated_agents.append(settings.triage_agent_id)
        triage_summary = self._mapping_from_output(triage_output, "triage_summary")
        escalation_request = self._escalation_from_output(triage_output)
        layer_after = ConsultationLayer.L1_TRIAGE
        direction_hints: list[JsonMap] = []
        differential_hypotheses: list[JsonMap] = []
        care_suggestions: list[JsonMap] = []
        if escalation_request is None and self._direction_ready(
            readiness=readiness,
            settings=settings,
        ):
            direction_output = await self._run_sub_agent(
                request=request,
                agent_id=settings.direction_agent_id,
                agent_version=settings.direction_agent_version,
                stage="direction_hint",
                task_input=self._base_task_input(
                    request=request,
                    readiness=readiness,
                    rag_bundles=rag_bundles,
                ),
                timeout_seconds=settings.timeouts.sub_agent_seconds,
            )
            if direction_output is None:
                degraded_flags.append("direction_unavailable")
            else:
                activated_agents.append(settings.direction_agent_id)
                direction_hints = self._list_of_maps_from_output(
                    direction_output,
                    "direction_hints",
                )
                layer_after = ConsultationLayer.L2_DIRECTION
        if escalation_request is None and self._differential_ready(
            readiness=readiness,
            rag_bundles=rag_bundles,
            settings=settings,
        ):
            differential_output = await self._run_sub_agent(
                request=request,
                agent_id=settings.differential_agent_id,
                agent_version=settings.differential_agent_version,
                stage="differential_diagnosis",
                task_input=self._base_task_input(
                    request=request,
                    readiness=readiness,
                    rag_bundles=rag_bundles,
                ),
                timeout_seconds=settings.timeouts.sub_agent_seconds,
            )
            if differential_output is None:
                degraded_flags.append("differential_unavailable")
            else:
                activated_agents.append(settings.differential_agent_id)
                differential_hypotheses = self._list_of_maps_from_output(
                    differential_output,
                    "differential_hypotheses",
                )
                layer_after = ConsultationLayer.L3_DIFFERENTIAL
        if escalation_request is None and await self._care_ready(
            request=request,
            readiness=readiness,
            rag_bundles=rag_bundles,
            settings=settings,
        ):
            care_output = await self._run_sub_agent(
                request=request,
                agent_id=settings.care_agent_id,
                agent_version=settings.care_agent_version,
                stage="care_plan",
                task_input=self._base_task_input(
                    request=request,
                    readiness=readiness,
                    rag_bundles=rag_bundles,
                ),
                timeout_seconds=settings.timeouts.sub_agent_seconds,
            )
            if care_output is None:
                degraded_flags.append("care_plan_unavailable")
            else:
                activated_agents.append(settings.care_agent_id)
                care_suggestions = self._list_of_maps_from_output(
                    care_output,
                    "care_suggestions",
                )
                layer_after = ConsultationLayer.L4_CARE_PLAN
        synthesizer_output = await self._run_sub_agent(
            request=request,
            agent_id=settings.synthesizer_agent_id,
            agent_version=settings.synthesizer_agent_version,
            stage="standard_draft_synthesizer",
            task_input={
                **self._base_task_input(
                    request=request,
                    readiness=readiness,
                    rag_bundles=rag_bundles,
                ),
                "selected_question_ids": [
                    question.question_id for question in selected_questions
                ],
                "layer_after": layer_after.value,
            },
            timeout_seconds=settings.timeouts.sub_agent_seconds,
        )
        if synthesizer_output is None:
            degraded_flags.append("synthesizer_unavailable")
        else:
            activated_agents.append(settings.synthesizer_agent_id)
        return {
            "selected_questions": selected_questions,
            "triage_summary": triage_summary,
            "direction_hints": direction_hints,
            "differential_hypotheses": differential_hypotheses,
            "care_suggestions": care_suggestions,
            "escalation_request": escalation_request,
            "layer_after": layer_after,
            "activated_agents": activated_agents,
            "degraded_flags": degraded_flags,
            "synthesizer_output": synthesizer_output,
        }

    async def _run_sub_agent(
        self,
        *,
        request: StandardConsultationRequestDto,
        agent_id: str,
        agent_version: str,
        stage: str,
        task_input: JsonMap,
        timeout_seconds: float,
    ) -> AgentRunResultDto | None:
        """通过 AgentRunner 执行一个受控内部子 Agent。

        :param request: 当前标准问诊请求。
        :param agent_id: 子 Agent 规格 ID。
        :param agent_version: 子 Agent 规格版本。
        :param stage: 当前中控阶段名。
        :param task_input: 传给 AgentRunner 的结构化任务输入。
        :param timeout_seconds: 本次调用超时秒数。
        :return: 成功运行结果；AgentRunner 缺失、失败或超时时返回 None。
        """

        if self._agent_runner is None or not self._agent_runner.is_ready():
            return None
        try:
            result = await asyncio.wait_for(
                self._agent_runner.run_agent(
                    AgentRunRequestDto(
                        run_id=f"{request.run_id}:{stage}",
                        trace_id=request.trace_id,
                        request_id=request.request_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        agent_id=agent_id,
                        agent_version=agent_version,
                        task_input=task_input,
                        prompt_blocks=to_agent_prompt_blocks(request.context),
                        runtime_options={
                            "generation_profile": request.generation_profile,
                            "stage": stage,
                        },
                    )
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return None
        if result.status is not AgentRunStatus.SUCCEEDED or not result.schema_valid:
            return None
        return result

    def _base_task_input(
        self,
        *,
        request: StandardConsultationRequestDto,
        readiness: ReadinessProfileDto,
        rag_bundles: list[RagEvidenceBundleDto],
    ) -> JsonMap:
        """构建传给内部子 Agent 的基础任务输入。

        :param request: 当前标准问诊请求。
        :param readiness: 当前 readiness profile。
        :param rag_bundles: 当前 RAG 证据包。
        :return: 不含完整原始上下文正文的结构化任务输入。
        """

        return {
            "task_id": request.task_id,
            "task_type": request.task_type,
            "current_pet_id": request.current_pet_id,
            "generation_profile": request.generation_profile,
            "executor_key": request.executor_key,
            "normalized_query": request.normalized_query,
            "slot_coverage": {
                "known_slots": dict(request.context.slot_coverage.known_slots),
                "missing_slots": list(request.context.slot_coverage.missing_slots),
                "stale_slots": dict(request.context.slot_coverage.stale_slots),
                "pending_confirmation_slots": dict(
                    request.context.slot_coverage.pending_confirmation_slots
                ),
            },
            "readiness": self._readiness_to_map(readiness),
            "rag_summary": [self._rag_to_map(bundle) for bundle in rag_bundles],
        }

    def _readiness_to_map(self, readiness: ReadinessProfileDto) -> JsonMap:
        """将 readiness profile 转换为普通映射。

        :param readiness: 当前 readiness profile。
        :return: 可放入 AgentRunner task_input 的映射。
        """

        return {
            "symptom_entity_confidence": readiness.symptom_entity_confidence,
            "high_risk_field_completeness": readiness.high_risk_field_completeness,
            "rag_evidence_readiness": readiness.rag_evidence_readiness,
            "differential_convergence": readiness.differential_convergence,
            "contraindication_completeness": readiness.contraindication_completeness,
            "answer_consistency": readiness.answer_consistency,
            "hard_gates": list(readiness.hard_gates),
        }

    def _rag_to_map(self, bundle: RagEvidenceBundleDto) -> JsonMap:
        """将 RAG 证据包转换为普通映射。

        :param bundle: 当前 RAG 证据包。
        :return: 可放入 AgentRunner task_input 的映射。
        """

        return {
            "retrieval_purpose": bundle.retrieval_purpose.value,
            "retrieval_ids": list(bundle.retrieval_ids),
            "source_versions": list(bundle.source_versions),
            "cache_hit": bundle.cache_hit,
            "degraded": bundle.degraded,
            "degraded_reason": bundle.degraded_reason,
            "evidence_ids": [hint.evidence_id for hint in bundle.evidence_hints],
        }

    def _candidate_questions_from_output(
        self,
        *,
        output: AgentRunResultDto | None,
        request: StandardConsultationRequestDto,
    ) -> list[CandidateQuestionDto]:
        """从问诊采集子 Agent 输出中解析候选问题。

        :param output: AgentRunner 结构化结果；为空时返回空列表。
        :param request: 当前标准问诊请求。
        :return: 解析和归一化后的候选问题列表。
        """

        if output is None:
            return []
        questions: list[CandidateQuestionDto] = []
        raw_questions = _as_list(output.parsed_output.get("candidate_questions"))
        if not raw_questions:
            raw_questions = _as_list(output.parsed_output.get("questions"))
        for index, raw_question in enumerate(raw_questions, start=1):
            item = _as_mapping(raw_question)
            if item is None:
                continue
            target_fact_key = _read_string(item.get("target_fact_key"))
            question_text = _read_string(item.get("question_text"))
            if target_fact_key is None or question_text is None:
                continue
            questions.append(
                CandidateQuestionDto(
                    question_id=(
                        _read_string(item.get("question_id"))
                        or f"{request.task_id}:agent_question:{index}"
                    ),
                    question_text=question_text,
                    target_fact_key=target_fact_key,
                    purpose=_enum_or_default(
                        enum_type=QuestionPurpose,
                        value=item.get("purpose"),
                        default=QuestionPurpose.CHIEF_COMPLAINT_CHARACTERIZATION,
                    ),
                    target_layer=_enum_or_default(
                        enum_type=ConsultationLayer,
                        value=item.get("target_layer"),
                        default=ConsultationLayer.L0_COLLECTION,
                    ),
                    risk_impact=_enum_or_default(
                        enum_type=RiskImpact,
                        value=item.get("risk_impact"),
                        default=RiskImpact.MEDIUM,
                    ),
                    information_gain=_read_float(
                        item.get("information_gain"),
                        default=0.5,
                    ),
                    evidence_ids=[
                        value
                        for value in (
                            _read_string(item)
                            for item in _as_list(item.get("evidence_ids"))
                        )
                        if value is not None
                    ],
                )
            )
        return questions

    def _deterministic_candidate_questions(
        self,
        request: StandardConsultationRequestDto,
    ) -> list[CandidateQuestionDto]:
        """根据缺失槽位构建确定性候选追问。

        :param request: 当前标准问诊请求。
        :return: 按缺失槽位生成的保守候选问题列表。
        """

        questions: list[CandidateQuestionDto] = []
        for index, slot in enumerate(
            request.context.slot_coverage.missing_slots, start=1
        ):
            question_text = _SLOT_QUESTION_TEXT.get(
                slot, f"关于 {slot} 这一点能再补充一下吗？"
            )
            questions.append(
                CandidateQuestionDto(
                    question_id=f"{request.task_id}:slot:{slot}",
                    question_text=question_text,
                    target_fact_key=slot,
                    purpose=(
                        QuestionPurpose.ACUTE_RULE_OUT
                        if slot in _HIGH_RISK_SLOTS
                        else QuestionPurpose.CHIEF_COMPLAINT_CHARACTERIZATION
                    ),
                    target_layer=ConsultationLayer.L0_COLLECTION,
                    risk_impact=RiskImpact.HIGH
                    if slot in _HIGH_RISK_SLOTS
                    else RiskImpact.MEDIUM,
                    information_gain=max(0.1, 1.0 - (index * 0.05)),
                )
            )
        return questions

    def _select_questions(
        self,
        *,
        request: StandardConsultationRequestDto,
        questions: list[CandidateQuestionDto],
        settings: StandardConsultationAgentSettings,
    ) -> list[CandidateQuestionDto]:
        """按预算、去重、已知事实和已问索引选择最终追问。

        :param request: 当前标准问诊请求。
        :param questions: 候选问题列表。
        :param settings: 当前标准问诊配置。
        :return: 最终选择的问题列表。
        """

        known_slots = set(request.context.slot_coverage.known_slots)
        asked_texts = {
            text
            for values in request.session_state.asked_question_index.values()
            for text in values
        }
        max_questions = min(
            request.question_budget.max_questions,
            settings.question_budget.absolute_max_questions,
        )
        risk_order = {RiskImpact.HIGH: 0, RiskImpact.MEDIUM: 1, RiskImpact.LOW: 2}
        selected: list[CandidateQuestionDto] = []
        seen_fact_keys: set[str] = set()
        for question in sorted(
            questions,
            key=lambda item: (
                risk_order[item.risk_impact],
                -item.information_gain,
                item.question_id,
            ),
        ):
            already_known = question.target_fact_key in known_slots
            already_asked = question.question_text in asked_texts
            if already_known or already_asked:
                continue
            if question.target_fact_key in seen_fact_keys:
                continue
            selected.append(
                question.model_copy(
                    update={
                        "already_known": already_known,
                        "already_asked": already_asked,
                    }
                )
            )
            seen_fact_keys.add(question.target_fact_key)
            if len(selected) >= max_questions:
                break
        return selected

    def _mapping_from_output(
        self,
        output: AgentRunResultDto | None,
        key: str,
    ) -> JsonMap:
        """从 AgentRunner 输出中读取映射字段。

        :param output: AgentRunner 结构化结果。
        :param key: 需要读取的字段名。
        :return: 字段存在且为映射时返回普通字典，否则返回空字典。
        """

        if output is None:
            return {}
        value = _as_mapping(output.parsed_output.get(key))
        return dict(value) if value is not None else {}

    def _list_of_maps_from_output(
        self,
        output: AgentRunResultDto,
        key: str,
    ) -> list[JsonMap]:
        """从 AgentRunner 输出中读取映射列表字段。

        :param output: AgentRunner 结构化结果。
        :param key: 需要读取的字段名。
        :return: 字段存在且元素为映射时返回普通字典列表。
        """

        values: list[JsonMap] = []
        for item in _as_list(output.parsed_output.get(key)):
            mapping = _as_mapping(item)
            if mapping is not None:
                values.append(dict(mapping))
        return values

    def _escalation_from_output(
        self,
        output: AgentRunResultDto | None,
    ) -> EscalationRequestDto | None:
        """从分诊子 Agent 输出中读取急症升级请求。

        :param output: AgentRunner 结构化结果。
        :return: 存在有效升级请求时返回 DTO，否则返回 None。
        """

        if output is None:
            return None
        raw_escalation = _as_mapping(output.parsed_output.get("escalation_request"))
        if raw_escalation is None:
            return None
        reason_code = _read_string(raw_escalation.get("reason_code"))
        summary = _read_string(raw_escalation.get("summary"))
        if reason_code is None or summary is None:
            return None
        return EscalationRequestDto(reason_code=reason_code, summary=summary)

    def _direction_ready(
        self,
        *,
        readiness: ReadinessProfileDto,
        settings: StandardConsultationAgentSettings,
    ) -> bool:
        """判断本轮是否允许进入 L2 方向提示。

        :param readiness: 当前 readiness profile。
        :param settings: 当前标准问诊配置。
        :return: 满足 L2 阈值时返回 True。
        """

        return (
            readiness.symptom_entity_confidence
            >= settings.readiness.direction_known_slot_ratio
        )

    def _differential_ready(
        self,
        *,
        readiness: ReadinessProfileDto,
        rag_bundles: list[RagEvidenceBundleDto],
        settings: StandardConsultationAgentSettings,
    ) -> bool:
        """判断本轮是否允许进入 L3 鉴别方向。

        :param readiness: 当前 readiness profile。
        :param rag_bundles: 当前 RAG 证据包。
        :param settings: 当前标准问诊配置。
        :return: 满足信息和 RAG 阈值时返回 True。
        """

        known_ready = (
            readiness.symptom_entity_confidence
            >= settings.readiness.differential_known_slot_ratio
        )
        if not known_ready:
            return False
        if not settings.rag.required_for_l3:
            return True
        return self._rag_ready(bundles=rag_bundles)

    async def _care_ready(
        self,
        *,
        request: StandardConsultationRequestDto,
        readiness: ReadinessProfileDto,
        rag_bundles: list[RagEvidenceBundleDto],
        settings: StandardConsultationAgentSettings,
    ) -> bool:
        """判断本轮是否允许进入 L4 护理或处置建议。

        :param request: 当前标准问诊请求。
        :param readiness: 当前 readiness profile。
        :param rag_bundles: 当前 RAG 证据包。
        :param settings: 当前标准问诊配置。
        :return: 满足信息、RAG 和 MedicationPolicy 阈值时返回 True。
        """

        if (
            readiness.symptom_entity_confidence
            < settings.readiness.care_known_slot_ratio
        ):
            return False
        if settings.rag.required_for_l4 and not self._rag_ready(bundles=rag_bundles):
            return False
        if (
            readiness.contraindication_completeness
            < settings.readiness.contraindication_completeness_ratio
        ):
            return False
        return await self._medication_policy_port.allows_care_plan(
            request=request,
            contraindication_completeness=readiness.contraindication_completeness,
        )

    def _rag_ready(self, *, bundles: list[RagEvidenceBundleDto]) -> bool:
        """判断当前是否存在可用 RAG 证据。

        :param bundles: 当前 RAG 证据包。
        :return: 至少存在一个非降级且含证据的 bundle 时返回 True。
        """

        return any(
            (not bundle.degraded) and bundle.evidence_hints for bundle in bundles
        )

    def _rag_readiness(self, *, bundles: list[RagEvidenceBundleDto]) -> float:
        """计算 RAG 证据 readiness 分数。

        :param bundles: 当前 RAG 证据包。
        :return: 可用证据存在时返回 1，否则返回 0。
        """

        return 1.0 if self._rag_ready(bundles=bundles) else 0.0

    def _build_draft(
        self,
        *,
        request: StandardConsultationRequestDto,
        readiness: ReadinessProfileDto,
        rag_bundles: list[RagEvidenceBundleDto],
        agent_state: JsonMap,
        settings: StandardConsultationAgentSettings,
    ) -> StandardConsultationDraftDto:
        """合成标准问诊结构化草稿。

        :param request: 当前标准问诊请求。
        :param readiness: 当前 readiness profile。
        :param rag_bundles: 当前 RAG 证据包。
        :param agent_state: 受控子 Agent 中间状态。
        :param settings: 当前标准问诊配置。
        :return: 已通过 DTO 校验的标准问诊草稿。
        """

        selected_questions = self._questions_from_state(agent_state)
        escalation_request = self._escalation_from_state(agent_state)
        layer_after = self._layer_from_state(agent_state)
        degraded_flags = self._degraded_flags_from_state(agent_state)
        if any(bundle.degraded for bundle in rag_bundles):
            degraded_flags.append("rag_degraded")
        status = self._resolve_draft_status(
            escalation_request=escalation_request,
            selected_questions=selected_questions,
            rag_bundles=rag_bundles,
        )
        draft_response = self._draft_response_from_state(
            request=request,
            agent_state=agent_state,
            selected_questions=selected_questions,
            status=status,
        )
        trace_patch = StandardTracePatchDto(
            standard_agent_version=settings.standard_agent_version,
            orchestrator_version=settings.orchestrator_version,
            sub_agent_versions=self._sub_agent_versions(settings=settings),
            layer_before=request.session_state.current_layer,
            layer_after=layer_after,
            activated_agents=self._string_list_from_state(
                agent_state, "activated_agents"
            ),
            selected_question_ids=[
                question.question_id for question in selected_questions
            ],
            retrieval_ids=[
                retrieval_id
                for bundle in rag_bundles
                for retrieval_id in bundle.retrieval_ids
            ],
            degraded_flags=list(dict.fromkeys(degraded_flags)),
        )
        return StandardConsultationDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=status,
            draft_response=draft_response,
            draft_response_ref=f"draft:{request.trace_id}:{request.task_id}",
            reached_layer=layer_after,
            triage_summary=self._state_mapping(agent_state, "triage_summary"),
            direction_hints=self._state_list(agent_state, "direction_hints"),
            differential_hypotheses=self._state_list(
                agent_state,
                "differential_hypotheses",
            ),
            care_suggestions=self._state_list(agent_state, "care_suggestions"),
            selected_questions=selected_questions,
            slot_progress_patch=SlotProgressPatchDto(
                requested_slots=[
                    question.target_fact_key for question in selected_questions
                ]
            ),
            stop_reason_candidate=self._stop_reason(
                status=status,
                readiness=readiness,
                selected_questions=selected_questions,
            ),
            escalation_request=escalation_request,
            evidence_bindings=self._evidence_bindings_from_state(agent_state),
            rag_summary=rag_bundles,
            trace_patch=trace_patch,
            trace_delivery_status=StandardTraceWriteStatus.SKIPPED,
        )

    def _questions_from_state(self, agent_state: JsonMap) -> list[CandidateQuestionDto]:
        """从中间状态中读取选中问题列表。

        :param agent_state: 受控子 Agent 中间状态。
        :return: 选中问题 DTO 列表。
        """

        values = agent_state.get("selected_questions")
        return [
            item for item in _as_list(values) if isinstance(item, CandidateQuestionDto)
        ]

    def _escalation_from_state(
        self,
        agent_state: JsonMap,
    ) -> EscalationRequestDto | None:
        """从中间状态中读取升级请求。

        :param agent_state: 受控子 Agent 中间状态。
        :return: 升级请求 DTO；不存在时返回 None。
        """

        value = agent_state.get("escalation_request")
        return value if isinstance(value, EscalationRequestDto) else None

    def _layer_from_state(self, agent_state: JsonMap) -> ConsultationLayer:
        """从中间状态中读取本轮达到的层级。

        :param agent_state: 受控子 Agent 中间状态。
        :return: 本轮达到的标准问诊层级。
        """

        value = agent_state.get("layer_after")
        return (
            value
            if isinstance(value, ConsultationLayer)
            else ConsultationLayer.L1_TRIAGE
        )

    def _degraded_flags_from_state(self, agent_state: JsonMap) -> list[str]:
        """从中间状态中读取降级标记。

        :param agent_state: 受控子 Agent 中间状态。
        :return: 去除空值后的降级标记列表。
        """

        return [
            value
            for value in (
                _read_string(item)
                for item in _as_list(agent_state.get("degraded_flags"))
            )
            if value is not None
        ]

    def _resolve_draft_status(
        self,
        *,
        escalation_request: EscalationRequestDto | None,
        selected_questions: list[CandidateQuestionDto],
        rag_bundles: list[RagEvidenceBundleDto],
    ) -> DraftStatus:
        """解析标准问诊草稿状态。

        :param escalation_request: 可选急症升级请求。
        :param selected_questions: 本轮选中的追问问题。
        :param rag_bundles: 当前 RAG 证据包。
        :return: 草稿状态枚举。
        """

        if escalation_request is not None:
            return DraftStatus.NEEDS_SAFETY_ESCALATION
        if any(bundle.degraded for bundle in rag_bundles):
            return DraftStatus.RAG_DEGRADED_CONSERVATIVE
        if selected_questions:
            return DraftStatus.NEEDS_MORE_INFO
        return DraftStatus.DRAFT_READY

    def _draft_response_from_state(
        self,
        *,
        request: StandardConsultationRequestDto,
        agent_state: JsonMap,
        selected_questions: list[CandidateQuestionDto],
        status: DraftStatus,
    ) -> str:
        """从合成子 Agent 输出或确定性 fallback 构建草稿正文。

        :param request: 当前标准问诊请求。
        :param agent_state: 受控子 Agent 中间状态。
        :param selected_questions: 本轮选中的追问问题。
        :param status: 草稿状态。
        :return: 待输出安全审查的草稿正文。
        """

        synthesizer = agent_state.get("synthesizer_output")
        if isinstance(synthesizer, AgentRunResultDto):
            text = _read_string(synthesizer.parsed_output.get("draft_response"))
            if text is not None:
                return text
        if status is DraftStatus.NEEDS_SAFETY_ESCALATION:
            return "根据目前信息，需要先切换到急症安全处理路径。"
        if selected_questions:
            question_text = "；".join(
                question.question_text for question in selected_questions
            )
            return f"我需要先补齐几个关键信息，才能继续判断 {request.normalized_query}：{question_text}"
        return "已形成标准问诊草稿，后续仍需经过输出安全审查后才能发布。"

    def _stop_reason(
        self,
        *,
        status: DraftStatus,
        readiness: ReadinessProfileDto,
        selected_questions: list[CandidateQuestionDto],
    ) -> str:
        """生成候选停止原因。

        :param status: 当前草稿状态。
        :param readiness: 当前 readiness profile。
        :param selected_questions: 本轮选中的追问问题。
        :return: 稳定候选停止原因。
        """

        if status is DraftStatus.NEEDS_SAFETY_ESCALATION:
            return "acute_escalation_requested"
        if status is DraftStatus.RAG_DEGRADED_CONSERVATIVE:
            return "rag_degraded_conservative"
        if selected_questions:
            return "needs_more_information"
        if readiness.hard_gates:
            return "hard_gate_pending"
        return "draft_ready"

    def _sub_agent_versions(
        self,
        *,
        settings: StandardConsultationAgentSettings,
    ) -> list[str]:
        """构建本轮配置声明的子 Agent 版本摘要。

        :param settings: 当前标准问诊配置。
        :return: 子 Agent ID 与版本摘要列表。
        """

        return [
            f"{settings.question_collector_agent_id}:{settings.question_collector_agent_version}",
            f"{settings.triage_agent_id}:{settings.triage_agent_version}",
            f"{settings.direction_agent_id}:{settings.direction_agent_version}",
            f"{settings.differential_agent_id}:{settings.differential_agent_version}",
            f"{settings.care_agent_id}:{settings.care_agent_version}",
            f"{settings.synthesizer_agent_id}:{settings.synthesizer_agent_version}",
        ]

    def _string_list_from_state(self, agent_state: JsonMap, key: str) -> list[str]:
        """从中间状态读取字符串列表。

        :param agent_state: 受控子 Agent 中间状态。
        :param key: 需要读取的字段名。
        :return: 去除空值后的字符串列表。
        """

        return [
            value
            for value in (_read_string(item) for item in _as_list(agent_state.get(key)))
            if value is not None
        ]

    def _state_mapping(self, agent_state: JsonMap, key: str) -> JsonMap:
        """从中间状态读取映射字段。

        :param agent_state: 受控子 Agent 中间状态。
        :param key: 需要读取的字段名。
        :return: 字段为映射时返回普通字典，否则返回空字典。
        """

        mapping = _as_mapping(agent_state.get(key))
        return dict(mapping) if mapping is not None else {}

    def _state_list(self, agent_state: JsonMap, key: str) -> list[JsonMap]:
        """从中间状态读取映射列表字段。

        :param agent_state: 受控子 Agent 中间状态。
        :param key: 需要读取的字段名。
        :return: 字段为映射列表时返回普通字典列表。
        """

        values: list[JsonMap] = []
        for item in _as_list(agent_state.get(key)):
            mapping = _as_mapping(item)
            if mapping is not None:
                values.append(dict(mapping))
        return values

    def _evidence_bindings_from_state(
        self,
        agent_state: JsonMap,
    ) -> list[EvidenceBindingDto]:
        """从合成子 Agent 输出中读取证据绑定摘要。

        :param agent_state: 受控子 Agent 中间状态。
        :return: 证据绑定 DTO 列表。
        """

        synthesizer = agent_state.get("synthesizer_output")
        if not isinstance(synthesizer, AgentRunResultDto):
            return []
        bindings: list[EvidenceBindingDto] = []
        for index, raw_binding in enumerate(
            _as_list(synthesizer.parsed_output.get("evidence_bindings")),
            start=1,
        ):
            mapping = _as_mapping(raw_binding)
            if mapping is None:
                continue
            summary = _read_string(mapping.get("binding_summary")) or "证据绑定摘要"
            claim_id = _read_string(mapping.get("claim_id")) or f"claim_{index}"
            evidence_ids = [
                value
                for value in (
                    _read_string(item) for item in _as_list(mapping.get("evidence_ids"))
                )
                if value is not None
            ]
            bindings.append(
                EvidenceBindingDto(
                    claim_id=claim_id,
                    evidence_ids=evidence_ids,
                    binding_summary=summary,
                )
            )
        return bindings

    async def _write_trace_safely(
        self,
        *,
        request: StandardConsultationRequestDto,
        draft: StandardConsultationDraftDto,
    ) -> StandardTraceWriteResultDto:
        """写入标准问诊 trace patch，并将异常旁路为降级状态。

        :param request: 当前标准问诊请求。
        :param draft: 当前标准问诊草稿。
        :return: trace 写入结果。
        """

        try:
            return await self._trace_sink.write_standard_trace(
                StandardConsultationTraceRecordDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    current_pet_id=draft.current_pet_id,
                    task_id=request.task_id,
                    status=draft.status,
                    trace_patch=draft.trace_patch,
                    selected_question_count=len(draft.selected_questions),
                    evidence_binding_count=len(draft.evidence_bindings),
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception as exc:
            return StandardTraceWriteResultDto(
                status=StandardTraceWriteStatus.DEGRADED,
                error_code="STANDARD_TRACE_WRITE_FAILED",
                retryable=True,
                detail=type(exc).__name__,
            )

    def _record_observability(
        self,
        *,
        request: StandardConsultationRequestDto,
        draft: StandardConsultationDraftDto | None,
        duration_ms: int,
    ) -> None:
        """记录标准问诊指标与结构化事件。

        :param request: 当前标准问诊请求。
        :param draft: 当前标准问诊草稿；失败时为空。
        :param duration_ms: 本次生成耗时，单位为毫秒。
        :return: None。
        """

        provider = self._observability_provider
        if provider is None:
            return
        status = draft.status.value if draft is not None else "failed"
        layer = draft.reached_layer.value if draft is not None else "none"
        labels = {
            "status": status,
            "generation_profile": request.generation_profile,
            "layer": layer,
        }
        provider.record_metric(
            metric_name="standard_consultation_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels=labels,
            description="StandardConsultationAgent 生成请求总数。",
        )
        provider.record_metric(
            metric_name="standard_consultation_latency_ms",
            value=duration_ms,
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="StandardConsultationAgent 端到端耗时，单位为毫秒。",
        )
        provider.record_event(
            event_name="standard_consultation.finished",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO
            if draft is not None
            else StructuredLogLevel.ERROR,
            safe_fields={
                "status": status,
                "layer": layer,
                "selected_question_count": (
                    len(draft.selected_questions) if draft is not None else 0
                ),
                "duration_ms": duration_ms,
            },
        )


def create_default_standard_consultation_agent(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    agent_runner: AgentRunner | None = None,
    rag_port: StandardRagPort | None = None,
    medication_policy_port: StandardMedicationPolicyPort | None = None,
    trace_sink: StandardConsultationTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> StandardConsultationAgent:
    """创建默认 StandardConsultationAgent 服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param agent_runner: 可选 AgentRunner 端口。
    :param rag_port: 可选 RAG 端口。
    :param medication_policy_port: 可选 MedicationPolicy 端口。
    :param trace_sink: 可选 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认 StandardConsultationAgent 服务实例。
    """

    return DefaultStandardConsultationAgent(
        runtime_config_provider=runtime_config_provider,
        agent_runner=agent_runner,
        rag_port=rag_port,
        medication_policy_port=medication_policy_port,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultStandardConsultationAgent",
    "StandardConsultationAgent",
    "create_default_standard_consultation_agent",
)
