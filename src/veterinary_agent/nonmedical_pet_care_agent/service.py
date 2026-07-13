##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/service.py
# 作用: 实现 NonmedicalPetCareAgent 应用内服务，编排 brief、建议规划、受控 RAG、规则约束、写作、自检与留痕。
# 边界: 不执行输入侧剖面判决、不直接读取记忆或知识库、不发布用户可见回复、不替代输出安全审查。
##################################################################################################

import asyncio
from time import perf_counter

from pydantic import ValidationError

from veterinary_agent.agent_runner import (
    AgentRunRequestDto,
    AgentRunResultDto,
    AgentRunStatus,
    AgentRunner,
)
from veterinary_agent.config import (
    NonmedicalPetCareAgentSettings,
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
)
from veterinary_agent.nonmedical_pet_care_agent.dto import (
    AdviceConstraintDto,
    AdviceDimensionDto,
    AdvicePlanDto,
    EvidenceCardDto,
    EvidenceHintDto,
    JsonMap,
    KnowledgeRetrievalPlanDto,
    NonmedicalAdviceDraftDto,
    NonmedicalAdviceRequestDto,
    NonmedicalRagResultDto,
    NonmedicalTracePatchDto,
    NonmedicalTraceRecordDto,
    NonmedicalTraceWriteResultDto,
    PersonalizationFactorDto,
    PersonalizationPlanDto,
    PetCareBriefDto,
    RagUsageSummaryDto,
    RetrievalFacetDto,
    SafetySelfCheckSummaryDto,
)
from veterinary_agent.nonmedical_pet_care_agent.briefing import (
    build_brief as _build_brief,
    has_body_boundary_signal as _has_body_boundary_signal,
    personalization_factors_from_context as _personalization_factors_from_context,
    requires_safety_escalation as _requires_safety_escalation,
)
from veterinary_agent.nonmedical_pet_care_agent.drafting import (
    build_conservative_response as _build_conservative_response,
    build_escalation_advice_plan as _build_escalation_advice_plan,
    build_escalation_draft as _build_escalation_draft,
    deterministic_self_check as _deterministic_self_check,
    status_for_draft as _status_for_draft,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import (
    AdviceDimensionCode,
    CareDomain,
    NonmedicalAgentErrorCode,
    NonmedicalAgentOperation,
    NonmedicalRetrievalPurpose,
    NonmedicalTraceWriteStatus,
    PersonalizationLevel,
)
from veterinary_agent.nonmedical_pet_care_agent.errors import NonmedicalAgentError
from veterinary_agent.nonmedical_pet_care_agent.ports import (
    NonmedicalPetCareRagPort,
    TodoNonmedicalPetCareRagPort,
)
from veterinary_agent.nonmedical_pet_care_agent.rules import (
    COMPONENT_NAME as _COMPONENT_NAME,
    GENERAL_SPECIES_SCOPES as _GENERAL_SPECIES_SCOPES,
    as_list as _as_list,
    as_mapping as _as_mapping,
    dimension_from_value as _dimension_from_value,
    elapsed_ms as _elapsed_ms,
    purpose_for_dimension as _purpose_for_dimension,
    read_bool as _read_bool,
    read_string as _read_string,
    strings_from_unknown_list as _strings_from_unknown_list,
    text_hash as _text_hash,
    unique_strings as _unique_strings,
)
from veterinary_agent.nonmedical_pet_care_agent.trace import (
    NonmedicalPetCareTraceSink,
    TodoNonmedicalPetCareTraceSink,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetExecutorKey,
    to_agent_prompt_blocks,
)

_NONMEDICAL_EXECUTOR = VetExecutorKey.NONMEDICAL_PET_CARE.value
_NONMEDICAL_COMPRESSION = ContextCompressionStrategy.EDUCATION_LIGHT


class DefaultNonmedicalPetCareAgent:
    """NonmedicalPetCareAgent 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        rag_port: NonmedicalPetCareRagPort | None = None,
        trace_sink: NonmedicalPetCareTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 NonmedicalPetCareAgent 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 端口；缺失时进入保守草稿降级。
        :param rag_port: 可选 RagPlatform 端口；缺失时使用 TODO 降级空壳。
        :param trace_sink: 可选非医疗 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._rag_port = rag_port or TodoNonmedicalPetCareRagPort()
        self._trace_sink = trace_sink or TodoNonmedicalPetCareTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断非医疗养宠建议服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 NonmedicalPetCareAgent 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.nonmedical_pet_care.enabled

    async def generate_draft(
        self,
        request: NonmedicalAdviceRequestDto,
    ) -> NonmedicalAdviceDraftDto:
        """生成非医疗养宠建议结构化草稿。

        :param request: 当前非医疗建议生成请求。
        :return: 待输出安全审查的非医疗建议草稿。
        :raises NonmedicalAgentError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_at = perf_counter()
        draft: NonmedicalAdviceDraftDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.nonmedical_pet_care
            self._validate_request_or_raise(request=request, settings=settings)
            brief = _build_brief(request=request)
            personalization_factors = _personalization_factors_from_context(
                request=request
            )
            degraded_flags: list[str] = []
            if _requires_safety_escalation(brief=brief):
                plan = _build_escalation_advice_plan(
                    brief=brief,
                    personalization_factors=personalization_factors,
                    generation_constraints=self._default_generation_constraints(),
                    safety_boundary_hints=self._default_safety_boundary_hints(
                        brief=brief
                    ),
                )
                personalization_plan = self._build_personalization_plan(
                    brief=brief,
                    personalization_factors=personalization_factors,
                )
                trace_patch = self._build_trace_patch(
                    brief=brief,
                    plan=plan,
                    rag_summary=RagUsageSummaryDto(),
                    degraded_flags=["safety_escalation_required"],
                    settings=settings,
                )
                draft = _build_escalation_draft(
                    request=request,
                    brief=brief,
                    plan=plan,
                    personalization_plan=personalization_plan,
                    trace_patch=trace_patch,
                    settings=settings,
                )
                trace_result = await self._write_trace_safely(
                    request=request,
                    draft=draft,
                )
                return draft.model_copy(
                    update={"trace_delivery_status": trace_result.status}
                )

            plan, plan_flags = await self._plan_advice(
                request=request,
                brief=brief,
                personalization_factors=personalization_factors,
                settings=settings,
            )
            degraded_flags.extend(plan_flags)
            retrieval_plan, retrieval_plan_flags = await self._build_knowledge_plan(
                request=request,
                brief=brief,
                plan=plan,
                settings=settings,
            )
            degraded_flags.extend(retrieval_plan_flags)
            rag_results = await self._retrieve_evidence(
                request=request,
                retrieval_plan=retrieval_plan,
                settings=settings,
            )
            evidence_cards, evidence_flags = self._organize_evidence(
                brief=brief,
                rag_results=rag_results,
            )
            degraded_flags.extend(evidence_flags)
            rule_cards, rule_flags = self._rule_evidence_cards(
                brief=brief,
                plan=plan,
                settings=settings,
            )
            evidence_cards.extend(rule_cards)
            degraded_flags.extend(rule_flags)
            advice_constraints = self._build_advice_constraints(
                plan=plan,
                evidence_cards=evidence_cards,
                settings=settings,
            )
            personalization_plan = self._build_personalization_plan(
                brief=brief,
                personalization_factors=personalization_factors,
            )
            rag_summary = self._build_rag_summary(rag_results=rag_results)
            draft = await self._write_or_degrade_draft(
                request=request,
                brief=brief,
                plan=plan,
                advice_constraints=advice_constraints,
                personalization_plan=personalization_plan,
                rag_summary=rag_summary,
                degraded_flags=degraded_flags,
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
        except NonmedicalAgentError:
            raise
        except RuntimeConfigError as exc:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_RUNTIME_CONFIG_UNAVAILABLE,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="NonmedicalPetCareAgent 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except ValidationError as exc:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_OUTPUT_SCHEMA_INVALID,
                operation=NonmedicalAgentOperation.VALIDATE_DRAFT,
                message="NonmedicalPetCareAgent 结构化输出不符合 DTO 契约",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except Exception as exc:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_INTERNAL_ERROR,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="NonmedicalPetCareAgent 发生未映射内部错误",
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
        request: NonmedicalAdviceRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前非医疗请求使用的配置快照。

        :param request: 当前非医疗建议生成请求。
        :return: 与请求版本一致且启用 NonmedicalPetCareAgent 的配置快照。
        :raises NonmedicalAgentError: 当配置不可用、未启用或版本不一致时抛出。
        """

        if not self._runtime_config_provider.is_ready():
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_NOT_READY,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="RuntimeConfig provider 未就绪",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.nonmedical_pet_care.enabled:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_NOT_READY,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="NonmedicalPetCareAgent 已被配置关闭",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if (
            request.params_version != snapshot.params_version
            or request.config_snapshot_id != snapshot.config_snapshot_id
        ):
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="请求参数版本或配置快照与当前 RuntimeConfig 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "request_params_version": request.params_version,
                    "snapshot_params_version": snapshot.params_version,
                    "request_config_snapshot_id": request.config_snapshot_id,
                    "snapshot_config_snapshot_id": snapshot.config_snapshot_id,
                },
            )
        return snapshot

    def _validate_request_or_raise(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> None:
        """校验非医疗输入执行器、宠物作用域和上下文契约。

        :param request: 当前非医疗建议生成请求。
        :param settings: 当前非医疗配置；用于证明配置已解析。
        :return: None。
        :raises NonmedicalAgentError: 当前置契约不满足时抛出稳定错误。
        """

        del settings
        if request.current_pet_id is None:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_MISSING_CURRENT_PET_ID,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗请求缺少 current_pet_id",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.generation_profile not in {None, ""}:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="纯非医疗链路 generation_profile 必须为空",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"generation_profile": request.generation_profile},
            )
        if request.executor_key != _NONMEDICAL_EXECUTOR:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗组件仅接受 nonmedical_pet_care 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"executor_key": request.executor_key},
            )
        if not request.assessment_summary:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_ASSESSMENT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗请求缺少输入安全评估摘要",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.context.generation_profile is not None:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗上下文 generation_profile 必须为空",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "context_generation_profile": request.context.generation_profile.value
                },
            )
        if request.context.executor_key is not VetExecutorKey.NONMEDICAL_PET_CARE:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_EXECUTOR_MISMATCH,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗上下文不是 nonmedical_pet_care 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "context_executor_key": request.context.executor_key.value
                },
            )
        if request.context.compression_audit.compression_strategy is not (
            _NONMEDICAL_COMPRESSION
        ):
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗上下文未使用 education_light 轻量压缩策略",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "compression_strategy": (
                        request.context.compression_audit.compression_strategy.value
                    )
                },
            )
        if request.current_pet_id != request.context.current_pet_id:
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_PET_CONTEXT_INVALID,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗请求宠物 ID 与上下文宠物 ID 不一致",
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
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="非医疗请求 task_id 与上下文 task_id 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )

    async def _plan_advice(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        personalization_factors: list[PersonalizationFactorDto],
        settings: NonmedicalPetCareAgentSettings,
    ) -> tuple[AdvicePlanDto, list[str]]:
        """执行建议维度规划，失败时使用保守确定性计划。

        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param personalization_factors: 可用个性化因子。
        :param settings: 当前非医疗配置。
        :return: 建议计划与降级标记列表。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.planner_agent_id,
            agent_version=settings.planner_agent_version,
            stage="advice_dimension_planner",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "personalization_factors": [
                    factor.model_dump(mode="json") for factor in personalization_factors
                ],
                "allowed_dimensions": list(settings.allowed_dimensions),
            },
            timeout_seconds=settings.timeouts.planner_seconds,
        )
        if result is None:
            return (
                self._deterministic_advice_plan(
                    brief=brief,
                    personalization_factors=personalization_factors,
                    settings=settings,
                ),
                ["advice_planner_unavailable"],
            )
        plan = self._advice_plan_from_output(
            output=result.parsed_output,
            brief=brief,
            personalization_factors=personalization_factors,
            settings=settings,
        )
        if plan is None:
            return (
                self._deterministic_advice_plan(
                    brief=brief,
                    personalization_factors=personalization_factors,
                    settings=settings,
                ),
                ["advice_planner_invalid"],
            )
        return plan, []

    def _advice_plan_from_output(
        self,
        *,
        output: JsonMap,
        brief: PetCareBriefDto,
        personalization_factors: list[PersonalizationFactorDto],
        settings: NonmedicalPetCareAgentSettings,
    ) -> AdvicePlanDto | None:
        """从 AgentRunner 输出解析建议计划。

        :param output: AgentRunner 结构化输出。
        :param brief: 本轮非医疗 brief。
        :param personalization_factors: 可用个性化因子。
        :param settings: 当前非医疗配置。
        :return: 可用建议计划；解析失败时返回 None。
        """

        dimensions = self._dimensions_from_output(output=output, settings=settings)
        if not dimensions:
            return None
        try:
            return AdvicePlanDto(
                advice_axis=_read_string(output.get("advice_axis"))
                or brief.advice_axis,
                dimensions=dimensions,
                personalization_factors=personalization_factors,
                generation_constraints=_strings_from_unknown_list(
                    output.get("generation_constraints")
                )
                or self._default_generation_constraints(),
                safety_boundary_hints=_strings_from_unknown_list(
                    output.get("safety_boundary_hints")
                )
                or self._default_safety_boundary_hints(brief=brief),
            )
        except ValidationError:
            return None

    def _dimensions_from_output(
        self,
        *,
        output: JsonMap,
        settings: NonmedicalPetCareAgentSettings,
    ) -> list[AdviceDimensionDto]:
        """从结构化输出中解析建议维度列表。

        :param output: AgentRunner 结构化输出。
        :param settings: 当前非医疗配置。
        :return: 已校验且去重的建议维度列表。
        """

        allowed = self._allowed_dimensions(settings=settings)
        dimensions: list[AdviceDimensionDto] = []
        seen: set[AdviceDimensionCode] = set()
        for index, item in enumerate(_as_list(output.get("dimensions")), start=1):
            item_map = _as_mapping(item)
            if item_map is None:
                continue
            dimension_code = _dimension_from_value(
                item_map.get("dimension_code") or item_map.get("code")
            )
            if dimension_code is None or dimension_code not in allowed:
                continue
            if dimension_code in seen:
                continue
            seen.add(dimension_code)
            dimensions.append(
                AdviceDimensionDto(
                    dimension_code=dimension_code,
                    priority=index,
                    required=_read_bool(item_map.get("required"), default=index <= 2),
                    evidence_requirement=(
                        _read_string(item_map.get("evidence_requirement"))
                        or "需要公开养宠知识或受控规则支持。"
                    ),
                    prohibited_advice=_strings_from_unknown_list(
                        item_map.get("prohibited_advice")
                    ),
                )
            )
        return dimensions

    def _deterministic_advice_plan(
        self,
        *,
        brief: PetCareBriefDto,
        personalization_factors: list[PersonalizationFactorDto],
        settings: NonmedicalPetCareAgentSettings,
    ) -> AdvicePlanDto:
        """构建不依赖模型输出的保守建议计划。

        :param brief: 本轮非医疗 brief。
        :param personalization_factors: 可用个性化因子。
        :param settings: 当前非医疗配置。
        :return: 确定性建议计划。
        """

        allowed = self._allowed_dimensions(settings=settings)
        dimension_codes = [
            code
            for code in self._default_dimension_codes(brief=brief)
            if code in allowed
        ]
        if not dimension_codes:
            dimension_codes = [AdviceDimensionCode.STEPWISE_PLAN]
        dimensions = [
            AdviceDimensionDto(
                dimension_code=dimension_code,
                priority=index,
                required=index <= 2,
                evidence_requirement="需要受控规则或 RAG 证据支持该建议维度。",
                prohibited_advice=self._prohibited_advice_for_dimension(dimension_code),
            )
            for index, dimension_code in enumerate(dimension_codes, start=1)
        ]
        return AdvicePlanDto(
            advice_axis=brief.advice_axis,
            dimensions=dimensions,
            personalization_factors=personalization_factors,
            generation_constraints=self._default_generation_constraints(),
            safety_boundary_hints=self._default_safety_boundary_hints(brief=brief),
        )

    def _allowed_dimensions(
        self,
        *,
        settings: NonmedicalPetCareAgentSettings,
    ) -> set[AdviceDimensionCode]:
        """读取配置允许的建议维度集合。

        :param settings: 当前非医疗配置。
        :return: 可用于本轮规划的建议维度集合。
        """

        return {
            dimension
            for dimension in (
                _dimension_from_value(value) for value in settings.allowed_dimensions
            )
            if dimension is not None
        }

    def _default_dimension_codes(
        self,
        *,
        brief: PetCareBriefDto,
    ) -> list[AdviceDimensionCode]:
        """根据当前领域选择保守默认建议维度。

        :param brief: 本轮非医疗 brief。
        :return: 默认建议维度列表。
        """

        dimensions = [
            AdviceDimensionCode.APPLICABILITY_CHECK,
            AdviceDimensionCode.STEPWISE_PLAN,
            AdviceDimensionCode.GRADUAL_PACE,
            AdviceDimensionCode.OBSERVATION_METRICS,
            AdviceDimensionCode.RISK_BOUNDARY,
        ]
        if brief.care_domain in {CareDomain.NUTRITION, CareDomain.WEIGHT_MANAGEMENT}:
            dimensions.append(AdviceDimensionCode.ALTERNATIVE_OPTIONS)
        if brief.care_domain is CareDomain.BEHAVIOR:
            dimensions.append(AdviceDimensionCode.MISCONCEPTION_WARNING)
        if _has_body_boundary_signal(brief=brief):
            dimensions.append(AdviceDimensionCode.PROFESSIONAL_ESCALATION)
        return dimensions

    def _default_generation_constraints(self) -> list[str]:
        """构建非医疗建议写作默认生成约束。

        :return: 非医疗建议写作默认生成约束列表。
        """

        return [
            "不得输出诊断结论、鉴别诊断或标准问诊四层结构。",
            "不得输出药物剂量、频次、片数、疗程或处方级用药方案。",
            "不得建议极端饮食、长期禁食、惩罚式训练或忽略医学信号。",
            "缺失体重、主粮、活动量或生活环境时只能给通用原则或自然追问。",
            "安全边界应自然嵌入正文，不使用可发布前替代护栏的措辞。",
        ]

    def _default_safety_boundary_hints(
        self,
        *,
        brief: PetCareBriefDto,
    ) -> list[str]:
        """构建非医疗建议默认安全边界提示。

        :param brief: 本轮非医疗 brief。
        :return: 默认安全边界提示列表。
        """

        hints = [
            "非医疗养宠建议不能替代线下兽医检查。",
            "若出现精神沉郁、持续恶化、呼吸异常、抽搐或疑似中毒，应及时就医。",
        ]
        if _has_body_boundary_signal(brief=brief):
            hints.insert(0, "当前输入包含安全信号，建议必须保守并提示观察或就医边界。")
        return hints

    def _prohibited_advice_for_dimension(
        self,
        dimension_code: AdviceDimensionCode,
    ) -> list[str]:
        """构建指定建议维度的禁止建议摘要。

        :param dimension_code: 建议维度代码。
        :return: 禁止建议摘要列表。
        """

        if dimension_code is AdviceDimensionCode.RISK_BOUNDARY:
            return ["把医学异常解释成普通养宠问题", "延误就医"]
        if dimension_code is AdviceDimensionCode.GRADUAL_PACE:
            return ["快速增肥或快速减重", "突然大幅改变饮食或运动"]
        if dimension_code is AdviceDimensionCode.MISCONCEPTION_WARNING:
            return ["惩罚式训练", "以疼痛或恐吓纠正行为"]
        return ["保证性结果", "编造缺失个性化字段"]

    async def _build_knowledge_plan(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> tuple[KnowledgeRetrievalPlanDto, list[str]]:
        """生成或回退受控养宠知识检索计划。

        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param settings: 当前非医疗配置。
        :return: 知识检索计划与降级标记列表。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.retrieval_planner_agent_id,
            agent_version=settings.retrieval_planner_agent_version,
            stage="knowledge_retrieval_planner",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "advice_plan": plan.model_dump(mode="json"),
                "rag_policy": settings.rag.model_dump(mode="json"),
                "rule_policy": settings.rules.model_dump(mode="json"),
            },
            timeout_seconds=settings.timeouts.retrieval_planner_seconds,
        )
        if result is None:
            return (
                self._deterministic_knowledge_plan(
                    request=request,
                    brief=brief,
                    plan=plan,
                    settings=settings,
                ),
                ["knowledge_planner_unavailable"],
            )
        retrieval_plan = self._knowledge_plan_from_output(
            output=result.parsed_output,
            request=request,
            brief=brief,
            plan=plan,
            settings=settings,
        )
        if retrieval_plan is None:
            return (
                self._deterministic_knowledge_plan(
                    request=request,
                    brief=brief,
                    plan=plan,
                    settings=settings,
                ),
                ["knowledge_planner_invalid"],
            )
        return retrieval_plan, []

    def _knowledge_plan_from_output(
        self,
        *,
        output: JsonMap,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> KnowledgeRetrievalPlanDto | None:
        """从 AgentRunner 输出解析知识检索计划。

        :param output: AgentRunner 结构化输出。
        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param settings: 当前非医疗配置。
        :return: 可用检索计划；解析失败时返回 None。
        """

        allowed_dimensions = {dimension.dimension_code for dimension in plan.dimensions}
        facets: list[RetrievalFacetDto] = []
        for item in _as_list(output.get("facets")):
            item_map = _as_mapping(item)
            if item_map is None:
                continue
            dimension_code = _dimension_from_value(item_map.get("dimension_code"))
            if dimension_code is None or dimension_code not in allowed_dimensions:
                continue
            queries = _strings_from_unknown_list(item_map.get("queries"))
            if not queries:
                queries = [
                    self._query_for_dimension(
                        brief=brief, dimension_code=dimension_code
                    )
                ]
            query_hashes = _strings_from_unknown_list(item_map.get("query_hashes"))
            if not query_hashes:
                query_hashes = [_text_hash(query) for query in queries]
            purpose = self._purpose_from_output(
                value=item_map.get("retrieval_purpose"),
                dimension_code=dimension_code,
            )
            collections = _strings_from_unknown_list(
                item_map.get("collections")
            ) or list(settings.rag.default_collections)
            facets.append(
                RetrievalFacetDto(
                    dimension_code=dimension_code,
                    retrieval_purpose=purpose,
                    queries=queries,
                    query_hashes=query_hashes,
                    collections=collections,
                    metadata_filters=dict(
                        _as_mapping(item_map.get("metadata_filters")) or {}
                    ),
                    top_k=settings.rag.top_k,
                    rerank_enabled=settings.rag.rerank_enabled,
                    source_policy_required=True,
                )
            )
            if len(facets) >= settings.rag.max_facets:
                break
        rag_required = _read_bool(
            output.get("rag_required"),
            default=self._rag_required(brief=brief, settings=settings),
        )
        conservative_rules_allowed = _read_bool(
            output.get("conservative_rules_allowed"),
            default=settings.rules.enabled,
        )
        if rag_required and not facets:
            return None
        return KnowledgeRetrievalPlanDto(
            plan_id=self._plan_id(request=request),
            facets=facets,
            rag_required=rag_required,
            conservative_rules_allowed=conservative_rules_allowed,
        )

    def _purpose_from_output(
        self,
        *,
        value: object,
        dimension_code: AdviceDimensionCode,
    ) -> NonmedicalRetrievalPurpose:
        """从结构化输出中解析检索用途。

        :param value: 检索用途原始值。
        :param dimension_code: 当前建议维度代码。
        :return: 合法检索用途。
        """

        purpose_text = _read_string(value)
        if purpose_text is not None:
            try:
                return NonmedicalRetrievalPurpose(purpose_text)
            except ValueError:
                pass
        return _purpose_for_dimension(dimension_code)

    def _deterministic_knowledge_plan(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> KnowledgeRetrievalPlanDto:
        """构建不依赖模型输出的知识检索计划。

        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param settings: 当前非医疗配置。
        :return: 确定性知识检索计划。
        """

        facets = [
            self._facet_for_dimension(
                brief=brief,
                dimension=dimension,
                settings=settings,
            )
            for dimension in plan.dimensions[: settings.rag.max_facets]
        ]
        return KnowledgeRetrievalPlanDto(
            plan_id=self._plan_id(request=request),
            facets=facets,
            rag_required=self._rag_required(brief=brief, settings=settings),
            conservative_rules_allowed=settings.rules.enabled,
        )

    def _rag_required(
        self,
        *,
        brief: PetCareBriefDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> bool:
        """判断当前任务是否需要尝试 RAG。

        :param brief: 本轮非医疗 brief。
        :param settings: 当前非医疗配置。
        :return: 若应调用 RAG 则返回 True。
        """

        if not settings.rag.enabled:
            return False
        if _has_body_boundary_signal(brief=brief) and settings.rag.required_for_signal:
            return True
        return brief.care_domain in {
            CareDomain.NUTRITION,
            CareDomain.BEHAVIOR,
            CareDomain.WEIGHT_MANAGEMENT,
            CareDomain.GENERAL_PET_CARE,
        }

    def _plan_id(self, *, request: NonmedicalAdviceRequestDto) -> str:
        """构建本轮知识计划 ID。

        :param request: 当前非医疗建议生成请求。
        :return: 稳定且短小的检索计划 ID。
        """

        digest = _text_hash(f"{request.trace_id}:{request.task_id}")[-12:]
        return f"nonmedical_plan:{digest}"

    def _facet_for_dimension(
        self,
        *,
        brief: PetCareBriefDto,
        dimension: AdviceDimensionDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> RetrievalFacetDto:
        """为单个建议维度构建检索 facet。

        :param brief: 本轮非医疗 brief。
        :param dimension: 单个建议维度。
        :param settings: 当前非医疗配置。
        :return: 受控 RAG 检索 facet。
        """

        query = self._query_for_dimension(
            brief=brief,
            dimension_code=dimension.dimension_code,
        )
        return RetrievalFacetDto(
            dimension_code=dimension.dimension_code,
            retrieval_purpose=_purpose_for_dimension(dimension.dimension_code),
            queries=[query],
            query_hashes=[_text_hash(query)],
            collections=list(settings.rag.default_collections),
            metadata_filters={
                "species_scope": brief.species_scope,
                "care_domain": brief.care_domain.value,
                "executor_key": _NONMEDICAL_EXECUTOR,
            },
            top_k=settings.rag.top_k,
            rerank_enabled=settings.rag.rerank_enabled,
            source_policy_required=True,
        )

    def _query_for_dimension(
        self,
        *,
        brief: PetCareBriefDto,
        dimension_code: AdviceDimensionCode,
    ) -> str:
        """按建议维度构建默认检索 query。

        :param brief: 本轮非医疗 brief。
        :param dimension_code: 建议维度代码。
        :return: 可交给 RAG 的查询文本。
        """

        return (
            f"{brief.species_scope} {brief.care_domain.value} "
            f"{brief.advice_axis} {dimension_code.value}"
        )

    async def _retrieve_evidence(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        retrieval_plan: KnowledgeRetrievalPlanDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> list[NonmedicalRagResultDto]:
        """按知识计划执行受控 RAG 检索。

        :param request: 当前非医疗建议生成请求。
        :param retrieval_plan: 受控知识检索计划。
        :param settings: 当前非医疗配置。
        :return: 本轮已获得或降级的 RAG 结果列表。
        """

        if not retrieval_plan.rag_required:
            return []
        if not settings.rag.enabled:
            return [
                NonmedicalRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason="NONMEDICAL_RAG_DISABLED",
                )
                for facet in retrieval_plan.facets
            ]
        results: list[NonmedicalRagResultDto] = []
        for facet in retrieval_plan.facets:
            try:
                result = await asyncio.wait_for(
                    self._rag_port.retrieve(
                        request=request,
                        facet=facet,
                        timeout_seconds=settings.timeouts.rag_seconds,
                    ),
                    timeout=settings.timeouts.rag_seconds,
                )
            except TimeoutError:
                result = NonmedicalRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason="NONMEDICAL_RAG_TIMEOUT",
                )
            except Exception as exc:
                result = NonmedicalRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason=f"NONMEDICAL_RAG_ERROR:{type(exc).__name__}",
                )
            results.append(result)
        return results

    def _organize_evidence(
        self,
        *,
        brief: PetCareBriefDto,
        rag_results: list[NonmedicalRagResultDto],
    ) -> tuple[list[EvidenceCardDto], list[str]]:
        """将 RAG 结果组织为写作可消费的证据卡。

        :param brief: 本轮非医疗 brief。
        :param rag_results: 本轮 RAG 检索结果。
        :return: 证据卡列表与降级标记列表。
        """

        cards: list[EvidenceCardDto] = []
        degraded_flags: list[str] = []
        for result in rag_results:
            if result.degraded:
                degraded_flags.append("rag_degraded")
                continue
            if not result.evidence_hints:
                degraded_flags.append("rag_empty_result")
                continue
            if not result.retrieval_ids:
                degraded_flags.append("evidence_reference_missing")
                continue
            for hint in result.evidence_hints:
                card = self._evidence_card_from_hint(
                    brief=brief,
                    result=result,
                    hint=hint,
                    index=len(cards) + 1,
                )
                if card is None:
                    degraded_flags.append("evidence_filtered")
                    continue
                cards.append(card)
        return cards, _unique_strings(degraded_flags)

    def _evidence_card_from_hint(
        self,
        *,
        brief: PetCareBriefDto,
        result: NonmedicalRagResultDto,
        hint: EvidenceHintDto,
        index: int,
    ) -> EvidenceCardDto | None:
        """将单条证据摘要转换为证据卡。

        :param brief: 本轮非医疗 brief。
        :param result: 该证据所属的 RAG 检索结果。
        :param hint: RAG 返回的证据摘要。
        :param index: 本轮证据卡序号。
        :return: 可用证据卡；因物种不兼容时返回 None。
        """

        if not self._species_compatible(brief=brief, hint=hint):
            return None
        return EvidenceCardDto(
            evidence_card_id=f"ecard_{index}",
            dimension_code=result.dimension_code,
            supported_principle_summary=hint.summary,
            species_scope=hint.species_scope,
            retrieval_ids=list(result.retrieval_ids),
            source_policy=hint.source_policy,
            public_citable=hint.public_citable,
        )

    def _species_compatible(
        self,
        *,
        brief: PetCareBriefDto,
        hint: EvidenceHintDto,
    ) -> bool:
        """判断证据物种范围是否适配当前 brief。

        :param brief: 本轮非医疗 brief。
        :param hint: RAG 返回的证据摘要。
        :return: 若证据可用于当前物种范围则返回 True。
        """

        brief_species = brief.species_scope.lower()
        hint_species = hint.species_scope.lower()
        if brief_species in _GENERAL_SPECIES_SCOPES:
            return True
        if hint_species in _GENERAL_SPECIES_SCOPES:
            return True
        return brief_species == hint_species

    def _rule_evidence_cards(
        self,
        *,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        settings: NonmedicalPetCareAgentSettings,
    ) -> tuple[list[EvidenceCardDto], list[str]]:
        """根据受控规则库生成兜底证据卡。

        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param settings: 当前非医疗配置。
        :return: 规则证据卡列表与降级标记。
        """

        if not settings.rules.enabled:
            return [], ["rule_library_disabled"]
        cards = [
            EvidenceCardDto(
                evidence_card_id=f"rule_card_{index}",
                dimension_code=dimension.dimension_code,
                supported_principle_summary=self._rule_summary_for_dimension(
                    brief=brief,
                    dimension_code=dimension.dimension_code,
                ),
                species_scope=brief.species_scope,
                retrieval_ids=[
                    (
                        f"rule:{settings.rules.rule_library_version}:"
                        f"{dimension.dimension_code.value.lower()}"
                    )
                ],
                source_policy="controlled_rule",
                public_citable=False,
            )
            for index, dimension in enumerate(plan.dimensions, start=1)
        ]
        return cards, ["rule_fallback_used"]

    def _rule_summary_for_dimension(
        self,
        *,
        brief: PetCareBriefDto,
        dimension_code: AdviceDimensionCode,
    ) -> str:
        """构建指定建议维度的受控规则摘要。

        :param brief: 本轮非医疗 brief。
        :param dimension_code: 建议维度代码。
        :return: 可作为证据约束的规则摘要。
        """

        if dimension_code is AdviceDimensionCode.RISK_BOUNDARY:
            return "出现持续恶化、精神异常、呼吸异常、抽搐、中毒疑虑或明显疼痛时，应优先线下兽医评估。"
        if dimension_code is AdviceDimensionCode.GRADUAL_PACE:
            return "饮食、运动、环境和行为训练应采用渐进调整，避免突然大幅改变。"
        if dimension_code is AdviceDimensionCode.MISCONCEPTION_WARNING:
            return "非医疗行为建议不得使用恐吓、疼痛或惩罚式训练作为核心手段。"
        if brief.care_domain is CareDomain.NUTRITION:
            return "营养建议应围绕均衡、适口性、逐步换粮和观察排便体况展开，不替代疾病饮食处方。"
        return "非医疗养宠建议应以可执行、可观察、可回退的日常管理原则为主。"

    def _build_advice_constraints(
        self,
        *,
        plan: AdvicePlanDto,
        evidence_cards: list[EvidenceCardDto],
        settings: NonmedicalPetCareAgentSettings,
    ) -> list[AdviceConstraintDto]:
        """将证据卡和规则版本整理为建议约束。

        :param plan: 建议维度计划。
        :param evidence_cards: 已组织的证据卡。
        :param settings: 当前非医疗配置。
        :return: 写作 Agent 必须遵守的建议约束列表。
        """

        constraints: list[AdviceConstraintDto] = []
        for index, card in enumerate(evidence_cards, start=1):
            hard_boundary = card.dimension_code in {
                AdviceDimensionCode.RISK_BOUNDARY,
                AdviceDimensionCode.PROFESSIONAL_ESCALATION,
            }
            constraints.append(
                AdviceConstraintDto(
                    constraint_id=f"constraint_{index}",
                    constraint_type=card.dimension_code.value,
                    constraint_summary=card.supported_principle_summary,
                    evidence_card_ids=[card.evidence_card_id],
                    hard_boundary=hard_boundary,
                )
            )
        constraints.append(
            AdviceConstraintDto(
                constraint_id=f"rule_boundary:{settings.rules.rule_library_version}",
                constraint_type="generation_boundary",
                constraint_summary="不得输出诊断、处方级用药、极端饮食、惩罚式训练或保证性结论。",
                evidence_card_ids=[],
                hard_boundary=True,
            )
        )
        for dimension in plan.dimensions:
            if dimension.prohibited_advice:
                constraints.append(
                    AdviceConstraintDto(
                        constraint_id=f"prohibited:{dimension.dimension_code.value}",
                        constraint_type="prohibited_advice",
                        constraint_summary="；".join(dimension.prohibited_advice),
                        evidence_card_ids=[],
                        hard_boundary=True,
                    )
                )
        return constraints

    def _build_personalization_plan(
        self,
        *,
        brief: PetCareBriefDto,
        personalization_factors: list[PersonalizationFactorDto],
    ) -> PersonalizationPlanDto:
        """构建非医疗建议个性化计划。

        :param brief: 本轮非医疗 brief。
        :param personalization_factors: 可用个性化因子。
        :return: 当前建议可个性化程度与缺失字段摘要。
        """

        if (
            len(personalization_factors) >= 5
            and not brief.missing_personalization_fields
        ):
            level = PersonalizationLevel.FULL
        elif len(personalization_factors) >= 2:
            level = PersonalizationLevel.PARTIAL
        elif personalization_factors:
            level = PersonalizationLevel.MINIMAL
        else:
            level = PersonalizationLevel.UNAVAILABLE
        return PersonalizationPlanDto(
            personalization_level=level,
            applied_factors=personalization_factors,
            unavailable_factors=list(brief.missing_personalization_fields),
            assumption_guards=[
                "不得编造体重、主粮、活动量、生活环境或既往病史。",
                "缺失字段只能以通用原则或自然补充问题处理。",
            ],
        )

    def _build_rag_summary(
        self,
        *,
        rag_results: list[NonmedicalRagResultDto],
    ) -> RagUsageSummaryDto:
        """构建本轮 RAG 使用摘要。

        :param rag_results: 本轮 RAG 检索结果。
        :return: 可进入草稿与 trace 的 RAG 摘要。
        """

        retrieval_ids = _unique_strings(
            retrieval_id
            for result in rag_results
            for retrieval_id in result.retrieval_ids
        )
        query_hashes = _unique_strings(
            query_hash for result in rag_results for query_hash in result.query_hashes
        )
        degraded_reasons = _unique_strings(
            result.degraded_reason or "rag_degraded"
            for result in rag_results
            if result.degraded or result.degraded_reason is not None
        )
        return RagUsageSummaryDto(
            rag_invoked=bool(rag_results),
            retrieval_ids=retrieval_ids,
            query_hashes=query_hashes,
            degraded=any(result.degraded for result in rag_results),
            degraded_reasons=degraded_reasons,
            cache_hit_count=sum(1 for result in rag_results if result.cache_hit),
        )

    async def _write_or_degrade_draft(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        advice_constraints: list[AdviceConstraintDto],
        personalization_plan: PersonalizationPlanDto,
        rag_summary: RagUsageSummaryDto,
        degraded_flags: list[str],
        settings: NonmedicalPetCareAgentSettings,
    ) -> NonmedicalAdviceDraftDto:
        """执行写作和自检，失败时返回保守草稿。

        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param advice_constraints: 证据或规则约束。
        :param personalization_plan: 个性化计划。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param degraded_flags: 当前累计降级标记。
        :param settings: 当前非医疗配置。
        :return: 待输出安全审查的非医疗建议草稿。
        """

        writer_result = await self._run_sub_agent(
            request=request,
            agent_id=settings.writer_agent_id,
            agent_version=settings.writer_agent_version,
            stage="nonmedical_advice_writer",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "advice_plan": plan.model_dump(mode="json"),
                "advice_constraints": [
                    constraint.model_dump(mode="json")
                    for constraint in advice_constraints
                ],
                "personalization_plan": personalization_plan.model_dump(mode="json"),
                "rag_summary": rag_summary.model_dump(mode="json"),
                "generation_constraints": list(plan.generation_constraints),
            },
            timeout_seconds=settings.timeouts.writer_seconds,
        )
        if writer_result is None:
            draft_response = _build_conservative_response(
                brief=brief,
                personalization_plan=personalization_plan,
            )
            degraded_flags = [*degraded_flags, "nonmedical_writer_unavailable"]
        else:
            draft_response = _read_string(
                writer_result.parsed_output.get("draft_response")
            )
            if draft_response is None:
                draft_response = _build_conservative_response(
                    brief=brief,
                    personalization_plan=personalization_plan,
                )
                degraded_flags = [*degraded_flags, "nonmedical_writer_invalid"]

        self_check = await self._run_self_check(
            request=request,
            brief=brief,
            plan=plan,
            advice_constraints=advice_constraints,
            draft_response=draft_response,
            settings=settings,
        )
        status = _status_for_draft(
            has_body_boundary_signal=_has_body_boundary_signal(brief=brief),
            brief=brief,
            rag_summary=rag_summary,
            personalization_plan=personalization_plan,
            self_check=self_check,
        )
        trace_patch = self._build_trace_patch(
            brief=brief,
            plan=plan,
            rag_summary=rag_summary,
            degraded_flags=degraded_flags,
            settings=settings,
        )
        return NonmedicalAdviceDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=status,
            draft_response=draft_response[: settings.max_draft_chars],
            draft_response_ref=self._draft_response_ref(request=request),
            advice_plan=plan,
            advice_constraints=advice_constraints,
            personalization_plan=personalization_plan,
            rag_summary=rag_summary,
            self_check=self_check,
            trace_patch=trace_patch,
        )

    async def _run_self_check(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        advice_constraints: list[AdviceConstraintDto],
        draft_response: str,
        settings: NonmedicalPetCareAgentSettings,
    ) -> SafetySelfCheckSummaryDto:
        """执行安全实用性自检，失败时使用确定性轻量扫描。

        :param request: 当前非医疗建议生成请求。
        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param advice_constraints: 证据或规则约束。
        :param draft_response: 待检查的非医疗草稿正文。
        :param settings: 当前非医疗配置。
        :return: 安全实用性自检摘要。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.self_checker_agent_id,
            agent_version=settings.self_checker_agent_version,
            stage="safety_practicality_checker",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "advice_plan": plan.model_dump(mode="json"),
                "advice_constraints": [
                    constraint.model_dump(mode="json")
                    for constraint in advice_constraints
                ],
                "draft_response": draft_response,
            },
            timeout_seconds=settings.timeouts.self_check_seconds,
        )
        if result is None:
            return _deterministic_self_check(draft_response=draft_response)
        summary = self._self_check_from_output(output=result.parsed_output)
        if summary is None:
            return _deterministic_self_check(draft_response=draft_response)
        return summary

    def _self_check_from_output(
        self,
        *,
        output: JsonMap,
    ) -> SafetySelfCheckSummaryDto | None:
        """从自检 Agent 输出解析检查摘要。

        :param output: 自检 Agent 结构化输出。
        :return: 可用自检摘要；解析失败时返回 None。
        """

        try:
            return SafetySelfCheckSummaryDto(
                passed=_read_bool(output.get("passed"), default=True),
                risk_flags=_strings_from_unknown_list(output.get("risk_flags")),
                extreme_diet_detected=_read_bool(
                    output.get("extreme_diet_detected"),
                    default=False,
                ),
                punitive_training_detected=_read_bool(
                    output.get("punitive_training_detected"),
                    default=False,
                ),
                medical_signal_ignored=_read_bool(
                    output.get("medical_signal_ignored"),
                    default=False,
                ),
                medication_boundary_detected=_read_bool(
                    output.get("medication_boundary_detected"),
                    default=False,
                ),
                overpromise_detected=_read_bool(
                    output.get("overpromise_detected"),
                    default=False,
                ),
                personalization_hallucination_detected=_read_bool(
                    output.get("personalization_hallucination_detected"),
                    default=False,
                ),
            )
        except ValidationError:
            return None

    def _build_trace_patch(
        self,
        *,
        brief: PetCareBriefDto,
        plan: AdvicePlanDto,
        rag_summary: RagUsageSummaryDto,
        degraded_flags: list[str],
        settings: NonmedicalPetCareAgentSettings,
    ) -> NonmedicalTracePatchDto:
        """构建非医疗 trace patch。

        :param brief: 本轮非医疗 brief。
        :param plan: 建议维度计划。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param degraded_flags: 当前累计降级标记。
        :param settings: 当前非医疗配置。
        :return: 可写入逻辑链的非医疗 trace patch。
        """

        return NonmedicalTracePatchDto(
            nonmedical_agent_version=settings.nonmedical_agent_version,
            planner_version=settings.planner_version,
            writer_version=settings.writer_version,
            selected_dimensions=[
                dimension.dimension_code for dimension in plan.dimensions
            ],
            consumed_signal_ids=[signal.signal_id for signal in brief.consumed_signals],
            retrieval_ids=list(rag_summary.retrieval_ids),
            degraded_flags=_unique_strings(degraded_flags),
        )

    async def _run_sub_agent(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        agent_id: str,
        agent_version: str,
        stage: str,
        task_input: JsonMap,
        timeout_seconds: float,
    ) -> AgentRunResultDto | None:
        """通过 AgentRunner 执行一个受控内部子 Agent。

        :param request: 当前非医疗建议生成请求。
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
                            "generation_profile": "nonmedical",
                            "executor_key": request.executor_key,
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

    def _draft_response_ref(self, *, request: NonmedicalAdviceRequestDto) -> str:
        """构建非医疗草稿引用 ID。

        :param request: 当前非医疗建议生成请求。
        :return: 可供后续图节点和 trace 使用的草稿引用。
        """

        return f"draft:{request.trace_id}:{request.task_id}"

    async def _write_trace_safely(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        draft: NonmedicalAdviceDraftDto,
    ) -> NonmedicalTraceWriteResultDto:
        """以降级安全方式写入非医疗 trace 摘要。

        :param request: 当前非医疗建议生成请求。
        :param draft: 已生成的非医疗草稿。
        :return: trace 写入结果；异常时返回 degraded。
        """

        try:
            return await self._trace_sink.write_nonmedical_trace(
                NonmedicalTraceRecordDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    current_pet_id=request.current_pet_id
                    or request.context.current_pet_id,
                    task_id=request.task_id,
                    status=draft.status,
                    trace_patch=draft.trace_patch,
                    constraint_count=len(draft.advice_constraints),
                    rag_invoked=draft.rag_summary.rag_invoked,
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception as exc:
            return NonmedicalTraceWriteResultDto(
                status=NonmedicalTraceWriteStatus.DEGRADED,
                error_code=f"NONMEDICAL_TRACE_ERROR:{type(exc).__name__}",
                retryable=True,
                detail="NonmedicalPetCareAgent trace 写入异常，已降级返回草稿",
            )

    def _record_observability(
        self,
        *,
        request: NonmedicalAdviceRequestDto,
        draft: NonmedicalAdviceDraftDto | None,
        duration_ms: int,
    ) -> None:
        """记录 NonmedicalPetCareAgent 端到端观测事件。

        :param request: 当前非医疗建议生成请求。
        :param draft: 已生成草稿；失败时为 None。
        :param duration_ms: 端到端耗时毫秒数。
        :return: None。
        """

        if self._observability_provider is None:
            return
        status = draft.status.value if draft is not None else "failed"
        labels = {
            "component": _COMPONENT_NAME,
            "generation_profile": "nonmedical",
            "status": status,
        }
        self._observability_provider.record_metric(
            metric_name="nonmedical_agent_latency_ms",
            value=float(duration_ms),
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="NonmedicalPetCareAgent end-to-end latency in milliseconds.",
        )
        self._observability_provider.record_event(
            event_name="nonmedical_agent_generate_draft",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO,
            safe_fields={
                "status": status,
                "executor_key": request.executor_key,
                "duration_ms": duration_ms,
            },
        )


__all__: tuple[str, ...] = ("DefaultNonmedicalPetCareAgent",)
