##################################################################################################
# 文件: src/veterinary_agent/education_agent/service.py
# 作用: 实现 EducationAgent 应用内服务，编排科普 brief、解释规划、受控 RAG、证据组织、写作、自检与留痕。
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
    EducationAgentSettings,
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
)
from veterinary_agent.education_agent.dto import (
    EducationBriefDto,
    EducationContentPlanDto,
    EducationDraftDto,
    EducationGenerationRequestDto,
    EducationRagResultDto,
    EducationRetrievalPlanDto,
    EducationTracePatchDto,
    EducationTraceRecordDto,
    EducationTraceWriteResultDto,
    EvidenceBindingDto,
    EvidenceCardDto,
    EvidenceHintDto,
    EvidenceSufficiencyResultDto,
    ExplanationDimensionDto,
    ExplanationPlanDto,
    GroundingCheckSummaryDto,
    JsonMap,
    RagUsageSummaryDto,
    RetrievalFacetDto,
)
from veterinary_agent.education_agent.enums import (
    EducationAgentErrorCode,
    EducationAgentOperation,
    EducationDraftStatus,
    EducationRetrievalPurpose,
    EducationTraceWriteStatus,
    EvidenceSufficiencyStatus,
    ExplanationDimensionCode,
)
from veterinary_agent.education_agent.errors import EducationAgentError
from veterinary_agent.education_agent.ports import (
    EducationRagPort,
    TodoEducationRagPort,
)
from veterinary_agent.education_agent.rules import (
    COMPONENT_NAME as _COMPONENT_NAME,
    FORBIDDEN_FORMAT_TERMS as _FORBIDDEN_FORMAT_TERMS,
    GENERAL_SPECIES_SCOPES as _GENERAL_SPECIES_SCOPES,
    LAB_TERMS as _LAB_TERMS,
    MEDICATION_TERMS as _MEDICATION_TERMS,
    REFERENCE_RANGE_RISK_TERMS as _REFERENCE_RANGE_RISK_TERMS,
    T4_RISK_TERMS as _T4_RISK_TERMS,
    as_list as _as_list,
    as_mapping as _as_mapping,
    contains_any as _contains_any,
    dimension_from_value as _dimension_from_value,
    elapsed_ms as _elapsed_ms,
    purpose_for_dimension as _purpose_for_dimension,
    read_bool as _read_bool,
    read_string as _read_string,
    strings_from_unknown_list as _strings_from_unknown_list,
    text_hash as _text_hash,
    unique_strings as _unique_strings,
)
from veterinary_agent.education_agent.trace import (
    EducationTraceSink,
    TodoEducationTraceSink,
)
from veterinary_agent.observability import (
    MetricType,
    ObservabilityProvider,
    StructuredLogLevel,
)
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetExecutorKey,
    VetGenerationProfile,
    to_agent_prompt_blocks,
)

_EDUCATION_PROFILE = VetGenerationProfile.EDUCATION.value
_EDUCATION_EXECUTOR = VetExecutorKey.EDUCATION.value
_EDUCATION_COMPRESSION = ContextCompressionStrategy.EDUCATION_LIGHT


class DefaultEducationAgent:
    """EducationAgent 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        agent_runner: AgentRunner | None = None,
        rag_port: EducationRagPort | None = None,
        trace_sink: EducationTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 EducationAgent 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param agent_runner: 可选 AgentRunner 端口；缺失时进入保守草稿降级。
        :param rag_port: 可选 RagPlatform 端口；缺失时使用 TODO 降级空壳。
        :param trace_sink: 可选科普 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._agent_runner = agent_runner
        self._rag_port = rag_port or TodoEducationRagPort()
        self._trace_sink = trace_sink or TodoEducationTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断科普服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取且 EducationAgent 已启用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
        except RuntimeConfigError:
            return False
        return snapshot.education_agent.enabled

    async def generate_draft(
        self,
        request: EducationGenerationRequestDto,
    ) -> EducationDraftDto:
        """生成科普结构化草稿。

        :param request: 当前科普生成请求。
        :return: 待输出安全审查的科普草稿。
        :raises EducationAgentError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_at = perf_counter()
        draft: EducationDraftDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.education_agent
            self._validate_request_or_raise(request=request, settings=settings)
            brief = self._build_brief(request=request)
            degraded_flags: list[str] = []
            plan, plan_flags = await self._plan_explanation(
                request=request,
                brief=brief,
                settings=settings,
            )
            degraded_flags.extend(plan_flags)
            retrieval_plan, retrieval_plan_flags = await self._build_retrieval_plan(
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
                retrieval_plan=retrieval_plan,
            )
            degraded_flags.extend(evidence_flags)
            sufficiency = self._evaluate_sufficiency(
                plan=plan,
                evidence_cards=evidence_cards,
                rag_results=rag_results,
                settings=settings,
            )
            degraded_flags.extend(sufficiency.degraded_reasons)
            rag_summary = self._build_rag_summary(rag_results=rag_results)
            draft = await self._write_or_degrade_draft(
                request=request,
                brief=brief,
                plan=plan,
                evidence_cards=evidence_cards,
                sufficiency=sufficiency,
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
        except EducationAgentError:
            raise
        except RuntimeConfigError as exc:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_RUNTIME_CONFIG_UNAVAILABLE,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="EducationAgent 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except ValidationError as exc:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_OUTPUT_SCHEMA_INVALID,
                operation=EducationAgentOperation.VALIDATE_DRAFT,
                message="EducationAgent 结构化输出不符合 DTO 契约",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except Exception as exc:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_INTERNAL_ERROR,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="EducationAgent 发生未映射内部错误",
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
        request: EducationGenerationRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前科普请求使用的配置快照。

        :param request: 当前科普生成请求。
        :return: 与请求版本一致且启用 EducationAgent 的配置快照。
        :raises EducationAgentError: 当配置不可用、未启用或版本不一致时抛出。
        """

        if not self._runtime_config_provider.is_ready():
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_NOT_READY,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="RuntimeConfig provider 未就绪",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        snapshot = self._runtime_config_provider.current_snapshot()
        if not snapshot.education_agent.enabled:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_NOT_READY,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="EducationAgent 已被配置关闭",
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.params_version != snapshot.params_version:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_CONTEXT_MISSING,
                operation=EducationAgentOperation.GENERATE_DRAFT,
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
        request: EducationGenerationRequestDto,
        settings: EducationAgentSettings,
    ) -> None:
        """校验科普输入剖面、宠物作用域和上下文契约。

        :param request: 当前科普生成请求。
        :param settings: 当前科普配置；用于证明配置已解析。
        :return: None。
        :raises EducationAgentError: 当前置契约不满足时抛出稳定错误。
        """

        del settings
        if request.current_pet_id is None:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_MISSING_CURRENT_PET_ID,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普请求缺少 current_pet_id",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.generation_profile != _EDUCATION_PROFILE:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普组件仅接受 generation_profile=education",
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
        if context_profile != _EDUCATION_PROFILE:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普上下文不是 education 剖面",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"context_generation_profile": context_profile},
            )
        if request.context.executor_key is not VetExecutorKey.EDUCATION:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普上下文不是 education 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={
                    "context_executor_key": request.context.executor_key.value
                },
            )
        if request.context.compression_audit.compression_strategy is not (
            _EDUCATION_COMPRESSION
        ):
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_CONTEXT_MISSING,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普上下文未使用 education_light 压缩策略",
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
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_PET_CONTEXT_INVALID,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普请求宠物 ID 与上下文宠物 ID 不一致",
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
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_CONTEXT_MISSING,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普请求 task_id 与上下文 task_id 不一致",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
            )
        if request.executor_key != _EDUCATION_EXECUTOR:
            raise EducationAgentError(
                code=EducationAgentErrorCode.EDUCATION_PROFILE_MISMATCH,
                operation=EducationAgentOperation.GENERATE_DRAFT,
                message="科普组件仅接受 education 执行器",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                conflict_with={"executor_key": request.executor_key},
            )

    def _build_brief(
        self, *, request: EducationGenerationRequestDto
    ) -> EducationBriefDto:
        """基于轻量上下文构建科普 brief。

        :param request: 当前科普生成请求。
        :return: 本轮科普主轴和可用上下文视图。
        """

        species_scope = self._species_scope_from_context(request=request)
        allowed_context_refs = [
            block.block_id
            for block in request.context.prompt_blocks
            if block.required
            or block.block_type.value in {"task_input", "owner_preference"}
        ]
        excluded_context_reasons = [
            f"{block_id}:{reason}"
            for block_id, reason in (
                request.context.compression_audit.dropped_reasons.items()
            )
        ]
        return EducationBriefDto(
            main_question=request.normalized_query,
            main_axis=self._main_axis_from_query(request.normalized_query),
            species_scope=species_scope,
            continue_recent_topic=self._should_continue_recent_topic(request=request),
            allowed_context_refs=allowed_context_refs,
            excluded_context_reasons=excluded_context_reasons,
        )

    def _species_scope_from_context(
        self,
        *,
        request: EducationGenerationRequestDto,
    ) -> str:
        """从上下文事实账本提取物种范围。

        :param request: 当前科普生成请求。
        :return: 可用于科普表达适配的物种范围。
        """

        for fact in request.context.fact_ledger:
            if fact.key == "species":
                value = _read_string(fact.value)
                if value is not None:
                    return value
        value = _read_string(request.context.slot_coverage.known_slots.get("species"))
        return value or "unknown"

    def _main_axis_from_query(self, query: str) -> str:
        """从当前问题提取科普主轴摘要。

        :param query: 当前规范化问题。
        :return: 不超过 120 字的科普主轴。
        """

        normalized = " ".join(query.split())
        if len(normalized) <= 120:
            return normalized
        return f"{normalized[:117]}..."

    def _should_continue_recent_topic(
        self,
        *,
        request: EducationGenerationRequestDto,
    ) -> bool:
        """判断本轮科普是否适合自然接续近期话题。

        :param request: 当前科普生成请求。
        :return: 若安全评估或上下文提示允许接续近期话题则返回 True。
        """

        explicit = request.assessment_summary.get("continue_recent_topic")
        if isinstance(explicit, bool):
            return explicit
        return any(
            block.block_type.value == "recent_messages"
            and block.block_id in request.context.compression_audit.included_block_ids
            for block in request.context.prompt_blocks
        )

    async def _plan_explanation(
        self,
        *,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        settings: EducationAgentSettings,
    ) -> tuple[ExplanationPlanDto, list[str]]:
        """执行解释维度规划，失败时使用保守确定性计划。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param settings: 当前科普配置。
        :return: 解释计划与降级标记列表。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.planner_agent_id,
            agent_version=settings.planner_agent_version,
            stage="explanation_planner",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "allowed_dimensions": list(settings.allowed_dimensions),
            },
            timeout_seconds=settings.timeouts.planner_seconds,
        )
        if result is None:
            return (
                self._deterministic_explanation_plan(
                    brief=brief,
                    settings=settings,
                ),
                ["explanation_planner_unavailable"],
            )
        plan = self._explanation_plan_from_output(
            output=result.parsed_output,
            brief=brief,
            settings=settings,
        )
        if plan is None:
            return (
                self._deterministic_explanation_plan(
                    brief=brief,
                    settings=settings,
                ),
                ["explanation_planner_invalid"],
            )
        return plan, []

    def _explanation_plan_from_output(
        self,
        *,
        output: JsonMap,
        brief: EducationBriefDto,
        settings: EducationAgentSettings,
    ) -> ExplanationPlanDto | None:
        """从 AgentRunner 输出解析解释计划。

        :param output: AgentRunner 结构化输出。
        :param brief: 本轮科普 brief。
        :param settings: 当前科普配置。
        :return: 可用解释计划；解析失败时返回 None。
        """

        dimensions = self._dimensions_from_output(
            output=output,
            settings=settings,
        )
        if not dimensions:
            return None
        try:
            return ExplanationPlanDto(
                main_axis=_read_string(output.get("main_axis")) or brief.main_axis,
                dimensions=dimensions,
                generation_constraints=_strings_from_unknown_list(
                    output.get("generation_constraints")
                )
                or self._default_generation_constraints(),
                safety_boundary_hints=_strings_from_unknown_list(
                    output.get("safety_boundary_hints")
                )
                or self._default_safety_boundary_hints(),
                citation_mode=_read_string(output.get("citation_mode"))
                or "evidence_bound",
            )
        except ValidationError:
            return None

    def _dimensions_from_output(
        self,
        *,
        output: JsonMap,
        settings: EducationAgentSettings,
    ) -> list[ExplanationDimensionDto]:
        """从结构化输出中解析解释维度列表。

        :param output: AgentRunner 结构化输出。
        :param settings: 当前科普配置。
        :return: 已校验且去重的解释维度列表。
        """

        allowed = self._allowed_dimensions(settings=settings)
        dimensions: list[ExplanationDimensionDto] = []
        seen: set[ExplanationDimensionCode] = set()
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
                ExplanationDimensionDto(
                    dimension_code=dimension_code,
                    priority=index,
                    required=_read_bool(item_map.get("required"), default=index <= 2),
                    evidence_requirement=(
                        _read_string(item_map.get("evidence_requirement"))
                        or "需要公开兽医知识库证据支持。"
                    ),
                    prohibited_claims=_strings_from_unknown_list(
                        item_map.get("prohibited_claims")
                    ),
                )
            )
        return dimensions

    def _deterministic_explanation_plan(
        self,
        *,
        brief: EducationBriefDto,
        settings: EducationAgentSettings,
    ) -> ExplanationPlanDto:
        """构建保守确定性解释计划。

        :param brief: 本轮科普 brief。
        :param settings: 当前科普配置。
        :return: 不依赖模型输出的解释计划。
        """

        allowed = self._allowed_dimensions(settings=settings)
        dimension_codes = [
            code
            for code in self._default_dimension_codes(brief=brief)
            if code in allowed
        ]
        if not dimension_codes:
            dimension_codes = [ExplanationDimensionCode.DEFINITION]
        dimensions = [
            ExplanationDimensionDto(
                dimension_code=dimension_code,
                priority=index,
                required=index <= 2,
                evidence_requirement="需要 RAG 证据卡支持该维度的通识描述。",
                prohibited_claims=self._prohibited_claims_for_dimension(dimension_code),
            )
            for index, dimension_code in enumerate(dimension_codes, start=1)
        ]
        return ExplanationPlanDto(
            main_axis=brief.main_axis,
            dimensions=dimensions,
            generation_constraints=self._default_generation_constraints(),
            safety_boundary_hints=self._default_safety_boundary_hints(),
            citation_mode="evidence_bound",
        )

    def _allowed_dimensions(
        self,
        *,
        settings: EducationAgentSettings,
    ) -> set[ExplanationDimensionCode]:
        """读取配置允许的解释维度集合。

        :param settings: 当前科普配置。
        :return: 可用于本轮规划的解释维度集合。
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
        brief: EducationBriefDto,
    ) -> list[ExplanationDimensionCode]:
        """根据当前问题选择保守默认解释维度。

        :param brief: 本轮科普 brief。
        :return: 默认解释维度列表。
        """

        dimension_codes = [
            ExplanationDimensionCode.DEFINITION,
            ExplanationDimensionCode.COMMON_DIRECTIONS,
            ExplanationDimensionCode.DIAGNOSTIC_LIMITS,
            ExplanationDimensionCode.RED_FLAGS,
        ]
        if _contains_any(brief.main_question, _MEDICATION_TERMS):
            dimension_codes.append(ExplanationDimensionCode.MEDICATION_BOUNDARY)
        if _contains_any(brief.main_question, _LAB_TERMS):
            dimension_codes.append(ExplanationDimensionCode.CHECKUP_PRINCIPLES)
        return dimension_codes

    def _default_generation_constraints(self) -> list[str]:
        """构建科普写作默认生成约束。

        :return: 科普写作默认生成约束列表。
        """

        return [
            "不得套用 standard 四层问诊或鉴别诊断格式。",
            "不得针对当前宠物下诊断结论。",
            "不得输出 T4 精确剂量或处方级用药方案。",
            "不得从 RAG 生成检验参考区间或异常标记。",
            "安全边界应自然嵌入正文，不使用统一后置模板句。",
        ]

    def _default_safety_boundary_hints(self) -> list[str]:
        """构建科普默认安全边界提示。

        :return: 科普默认安全边界提示列表。
        """

        return [
            "科普内容不能替代线下兽医检查。",
            "若出现精神沉郁、持续恶化、呼吸异常、抽搐或疑似中毒，应及时就医。",
        ]

    def _prohibited_claims_for_dimension(
        self,
        dimension_code: ExplanationDimensionCode,
    ) -> list[str]:
        """构建指定解释维度的禁止 claim 摘要。

        :param dimension_code: 解释维度代码。
        :return: 禁止 claim 摘要列表。
        """

        if dimension_code is ExplanationDimensionCode.MEDICATION_BOUNDARY:
            return ["针对当前宠物给出精确剂量", "替代兽医处方"]
        if dimension_code is ExplanationDimensionCode.CHECKUP_PRINCIPLES:
            return ["生成参考区间", "根据单个指标确诊"]
        return ["针对当前宠物确诊", "保证性结论"]

    async def _build_retrieval_plan(
        self,
        *,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        settings: EducationAgentSettings,
    ) -> tuple[EducationRetrievalPlanDto, list[str]]:
        """生成或回退受控 RAG 检索计划。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param settings: 当前科普配置。
        :return: RAG 检索计划与降级标记列表。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.retrieval_planner_agent_id,
            agent_version=settings.retrieval_planner_agent_version,
            stage="rag_query_planner",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "explanation_plan": plan.model_dump(mode="json"),
                "rag_policy": settings.rag.model_dump(mode="json"),
            },
            timeout_seconds=settings.timeouts.retrieval_planner_seconds,
        )
        if result is None:
            return (
                self._deterministic_retrieval_plan(
                    request=request,
                    brief=brief,
                    plan=plan,
                    settings=settings,
                ),
                ["retrieval_planner_unavailable"],
            )
        retrieval_plan = self._retrieval_plan_from_output(
            output=result.parsed_output,
            request=request,
            brief=brief,
            plan=plan,
            settings=settings,
        )
        if retrieval_plan is None:
            return (
                self._deterministic_retrieval_plan(
                    request=request,
                    brief=brief,
                    plan=plan,
                    settings=settings,
                ),
                ["retrieval_planner_invalid"],
            )
        return retrieval_plan, []

    def _retrieval_plan_from_output(
        self,
        *,
        output: JsonMap,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        settings: EducationAgentSettings,
    ) -> EducationRetrievalPlanDto | None:
        """从 AgentRunner 输出解析 RAG 检索计划。

        :param output: AgentRunner 结构化输出。
        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param settings: 当前科普配置。
        :return: 可用检索计划；解析失败时返回 None。
        """

        facets: list[RetrievalFacetDto] = []
        allowed_dimensions = {dimension.dimension_code for dimension in plan.dimensions}
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
            purpose_text = _read_string(item_map.get("retrieval_purpose"))
            try:
                purpose = (
                    EducationRetrievalPurpose(purpose_text)
                    if purpose_text is not None
                    else _purpose_for_dimension(dimension_code)
                )
            except ValueError:
                purpose = _purpose_for_dimension(dimension_code)
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
        if not facets:
            return None
        return EducationRetrievalPlanDto(
            plan_id=self._plan_id(request=request),
            facets=facets,
            dosage_filter_required=settings.rag.dosage_filter_required,
            ref_range_generation_forbidden=settings.rag.ref_range_generation_forbidden,
        )

    def _deterministic_retrieval_plan(
        self,
        *,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        settings: EducationAgentSettings,
    ) -> EducationRetrievalPlanDto:
        """构建保守确定性 RAG 检索计划。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param settings: 当前科普配置。
        :return: 不依赖模型输出的检索计划。
        """

        facets = [
            self._facet_for_dimension(
                brief=brief,
                dimension=dimension,
                settings=settings,
            )
            for dimension in plan.dimensions[: settings.rag.max_facets]
        ]
        return EducationRetrievalPlanDto(
            plan_id=self._plan_id(request=request),
            facets=facets,
            dosage_filter_required=settings.rag.dosage_filter_required,
            ref_range_generation_forbidden=settings.rag.ref_range_generation_forbidden,
        )

    def _plan_id(self, *, request: EducationGenerationRequestDto) -> str:
        """构建本轮检索计划 ID。

        :param request: 当前科普生成请求。
        :return: 稳定且短小的检索计划 ID。
        """

        digest = _text_hash(f"{request.trace_id}:{request.task_id}")[-12:]
        return f"education_plan:{digest}"

    def _facet_for_dimension(
        self,
        *,
        brief: EducationBriefDto,
        dimension: ExplanationDimensionDto,
        settings: EducationAgentSettings,
    ) -> RetrievalFacetDto:
        """为单个解释维度构建检索 facet。

        :param brief: 本轮科普 brief。
        :param dimension: 单个解释维度。
        :param settings: 当前科普配置。
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
                "generation_profile": _EDUCATION_PROFILE,
            },
            top_k=settings.rag.top_k,
            rerank_enabled=settings.rag.rerank_enabled,
            source_policy_required=True,
        )

    def _query_for_dimension(
        self,
        *,
        brief: EducationBriefDto,
        dimension_code: ExplanationDimensionCode,
    ) -> str:
        """按解释维度构建默认检索 query。

        :param brief: 本轮科普 brief。
        :param dimension_code: 解释维度代码。
        :return: 可交给 RAG 的查询文本。
        """

        return f"{brief.species_scope} {brief.main_axis} {dimension_code.value}"

    async def _retrieve_evidence(
        self,
        *,
        request: EducationGenerationRequestDto,
        retrieval_plan: EducationRetrievalPlanDto,
        settings: EducationAgentSettings,
    ) -> list[EducationRagResultDto]:
        """按检索计划执行受控 RAG 检索。

        :param request: 当前科普生成请求。
        :param retrieval_plan: 受控 RAG 检索计划。
        :param settings: 当前科普配置。
        :return: 本轮已获得或降级的 RAG 结果列表。
        """

        if not settings.rag.enabled:
            return [
                EducationRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason="EDUCATION_RAG_DISABLED",
                )
                for facet in retrieval_plan.facets
            ]
        results: list[EducationRagResultDto] = []
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
                result = EducationRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason="EDUCATION_RAG_TIMEOUT",
                )
            except Exception as exc:
                result = EducationRagResultDto(
                    retrieval_purpose=facet.retrieval_purpose,
                    dimension_code=facet.dimension_code,
                    query_hashes=list(facet.query_hashes),
                    degraded=True,
                    degraded_reason=f"EDUCATION_RAG_ERROR:{type(exc).__name__}",
                )
            results.append(result)
        return results

    def _organize_evidence(
        self,
        *,
        brief: EducationBriefDto,
        rag_results: list[EducationRagResultDto],
        retrieval_plan: EducationRetrievalPlanDto,
    ) -> tuple[list[EvidenceCardDto], list[str]]:
        """将 RAG 结果组织为写作可消费的证据卡。

        :param brief: 本轮科普 brief。
        :param rag_results: 本轮 RAG 检索结果。
        :param retrieval_plan: 受控 RAG 检索计划。
        :return: 证据卡列表与降级标记列表。
        """

        del retrieval_plan
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
        brief: EducationBriefDto,
        result: EducationRagResultDto,
        hint: EvidenceHintDto,
        index: int,
    ) -> EvidenceCardDto | None:
        """将单条证据摘要转换为证据卡。

        :param brief: 本轮科普 brief。
        :param result: 该证据所属的 RAG 检索结果。
        :param hint: RAG 返回的证据摘要。
        :param index: 本轮证据卡序号。
        :return: 可用证据卡；因物种或剂量风险过滤时返回 None。
        """

        if not self._species_compatible(brief=brief, hint=hint):
            return None
        if (
            result.dimension_code is ExplanationDimensionCode.MEDICATION_BOUNDARY
            and _contains_any(hint.summary, _T4_RISK_TERMS)
        ):
            return None
        return EvidenceCardDto(
            evidence_card_id=f"ecard_{index}",
            dimension_code=result.dimension_code,
            supported_claim_summary=hint.summary,
            species_scope=hint.species_scope,
            retrieval_ids=list(result.retrieval_ids),
            source_policy=hint.source_policy,
            public_citable=hint.public_citable,
            restricted=hint.restricted,
        )

    def _species_compatible(
        self,
        *,
        brief: EducationBriefDto,
        hint: EvidenceHintDto,
    ) -> bool:
        """判断证据物种范围是否适配当前科普 brief。

        :param brief: 本轮科普 brief。
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

    def _evaluate_sufficiency(
        self,
        *,
        plan: ExplanationPlanDto,
        evidence_cards: list[EvidenceCardDto],
        rag_results: list[EducationRagResultDto],
        settings: EducationAgentSettings,
    ) -> EvidenceSufficiencyResultDto:
        """评估证据是否足以生成科普草稿。

        :param plan: 解释维度计划。
        :param evidence_cards: 已组织的证据卡列表。
        :param rag_results: 本轮 RAG 检索结果。
        :param settings: 当前科普配置。
        :return: 证据充分性判定结果。
        """

        del rag_results
        card_dimensions = {card.dimension_code for card in evidence_cards}
        required_dimensions = {
            dimension.dimension_code
            for dimension in plan.dimensions
            if dimension.required
        }
        missing_dimensions = [
            dimension_code
            for dimension_code in required_dimensions
            if dimension_code not in card_dimensions
        ]
        if not evidence_cards:
            return EvidenceSufficiencyResultDto(
                status=EvidenceSufficiencyStatus.INSUFFICIENT,
                missing_dimensions=list(required_dimensions),
                degraded_reasons=["insufficient_evidence"],
                allow_full_answer=False,
            )
        if missing_dimensions:
            return EvidenceSufficiencyResultDto(
                status=EvidenceSufficiencyStatus.PARTIAL,
                missing_dimensions=missing_dimensions,
                degraded_reasons=["partial_evidence"],
                allow_full_answer=not settings.rag.required_for_medical,
            )
        return EvidenceSufficiencyResultDto(
            status=EvidenceSufficiencyStatus.SUFFICIENT,
            allow_full_answer=True,
        )

    def _build_rag_summary(
        self,
        *,
        rag_results: list[EducationRagResultDto],
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
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        evidence_cards: list[EvidenceCardDto],
        sufficiency: EvidenceSufficiencyResultDto,
        rag_summary: RagUsageSummaryDto,
        degraded_flags: list[str],
        settings: EducationAgentSettings,
    ) -> EducationDraftDto:
        """执行写作和自检，证据不足或写作失败时返回保守草稿。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param evidence_cards: 已组织的证据卡。
        :param sufficiency: 证据充分性结果。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param degraded_flags: 当前累计降级标记。
        :param settings: 当前科普配置。
        :return: 待输出安全审查的科普草稿。
        """

        if sufficiency.status is EvidenceSufficiencyStatus.INSUFFICIENT:
            return self._build_conservative_draft(
                request=request,
                brief=brief,
                plan=plan,
                rag_summary=rag_summary,
                degraded_flags=[*degraded_flags, "insufficient_evidence"],
                status=EducationDraftStatus.INSUFFICIENT_EVIDENCE,
                grounding_check=GroundingCheckSummaryDto(
                    passed=False,
                    risk_flags=["insufficient_evidence"],
                ),
                settings=settings,
            )
        writer_result = await self._run_sub_agent(
            request=request,
            agent_id=settings.writer_agent_id,
            agent_version=settings.writer_agent_version,
            stage="education_writer",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "explanation_plan": plan.model_dump(mode="json"),
                "evidence_cards": [
                    card.model_dump(mode="json") for card in evidence_cards
                ],
                "sufficiency": sufficiency.model_dump(mode="json"),
                "generation_constraints": list(plan.generation_constraints),
            },
            timeout_seconds=settings.timeouts.writer_seconds,
        )
        if writer_result is None:
            return self._build_conservative_draft(
                request=request,
                brief=brief,
                plan=plan,
                rag_summary=rag_summary,
                degraded_flags=[*degraded_flags, "education_writer_unavailable"],
                status=EducationDraftStatus.RAG_DEGRADED_CONSERVATIVE,
                grounding_check=GroundingCheckSummaryDto(
                    passed=False,
                    risk_flags=["writer_unavailable"],
                ),
                settings=settings,
            )
        draft_response = _read_string(writer_result.parsed_output.get("draft_response"))
        if draft_response is None:
            return self._build_conservative_draft(
                request=request,
                brief=brief,
                plan=plan,
                rag_summary=rag_summary,
                degraded_flags=[*degraded_flags, "education_writer_invalid"],
                status=EducationDraftStatus.RAG_DEGRADED_CONSERVATIVE,
                grounding_check=GroundingCheckSummaryDto(
                    passed=False,
                    risk_flags=["writer_invalid"],
                ),
                settings=settings,
            )
        evidence_bindings = self._evidence_bindings_from_output(
            output=writer_result.parsed_output,
            evidence_cards=evidence_cards,
        ) or self._deterministic_evidence_bindings(evidence_cards=evidence_cards)
        content_plan = self._content_plan_from_output(
            output=writer_result.parsed_output,
            brief=brief,
            plan=plan,
        )
        grounding_check = await self._run_grounding_check(
            request=request,
            brief=brief,
            plan=plan,
            evidence_cards=evidence_cards,
            draft_response=draft_response,
            settings=settings,
        )
        status = self._status_for_written_draft(
            sufficiency=sufficiency,
            rag_summary=rag_summary,
            grounding_check=grounding_check,
        )
        trace_patch = self._build_trace_patch(
            plan=plan,
            rag_summary=rag_summary,
            degraded_flags=degraded_flags,
            settings=settings,
        )
        return EducationDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=status,
            draft_response=draft_response[: settings.max_draft_chars],
            draft_response_ref=self._draft_response_ref(request=request),
            content_plan=content_plan,
            evidence_bindings=evidence_bindings,
            rag_summary=rag_summary,
            grounding_check=grounding_check,
            trace_patch=trace_patch,
        )

    def _build_conservative_draft(
        self,
        *,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        rag_summary: RagUsageSummaryDto,
        degraded_flags: list[str],
        status: EducationDraftStatus,
        grounding_check: GroundingCheckSummaryDto,
        settings: EducationAgentSettings,
    ) -> EducationDraftDto:
        """构建证据不足或依赖降级时的保守科普草稿。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param degraded_flags: 当前累计降级标记。
        :param status: 草稿状态。
        :param grounding_check: 接地性自检摘要。
        :param settings: 当前科普配置。
        :return: 保守科普草稿。
        """

        response = (
            f"关于“{brief.main_axis}”，目前可用证据不足，不能给出完整医学科普。"
            "可以先把它理解为需要结合物种、年龄、既往情况和具体表现来判断的主题；"
            "如果问题对应的是正在发生的症状、明显不适、持续恶化或疑似中毒，"
            "更稳妥的做法是尽快联系线下兽医。"
        )
        content_plan = self._content_plan_from_output(
            output={},
            brief=brief,
            plan=plan,
        )
        trace_patch = self._build_trace_patch(
            plan=plan,
            rag_summary=rag_summary,
            degraded_flags=degraded_flags,
            settings=settings,
        )
        return EducationDraftDto(
            task_id=request.task_id,
            current_pet_id=request.current_pet_id or request.context.current_pet_id,
            status=status,
            draft_response=response[: settings.max_draft_chars],
            draft_response_ref=self._draft_response_ref(request=request),
            content_plan=content_plan,
            evidence_bindings=[],
            rag_summary=rag_summary,
            grounding_check=grounding_check,
            trace_patch=trace_patch,
        )

    def _content_plan_from_output(
        self,
        *,
        output: JsonMap,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
    ) -> EducationContentPlanDto:
        """从写作输出或解释计划构建内容编排摘要。

        :param output: 写作 Agent 结构化输出。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :return: 科普正文内容编排计划。
        """

        section_titles = _strings_from_unknown_list(output.get("section_titles"))
        if not section_titles:
            section_titles = [
                dimension.dimension_code.value for dimension in plan.dimensions
            ]
        return EducationContentPlanDto(
            main_axis=plan.main_axis,
            section_titles=section_titles,
            selected_dimensions=[
                dimension.dimension_code for dimension in plan.dimensions
            ],
            continue_recent_topic=brief.continue_recent_topic,
            safety_boundary_hints=list(plan.safety_boundary_hints),
            citation_mode=plan.citation_mode,
        )

    def _evidence_bindings_from_output(
        self,
        *,
        output: JsonMap,
        evidence_cards: list[EvidenceCardDto],
    ) -> list[EvidenceBindingDto]:
        """从写作输出解析证据绑定摘要。

        :param output: 写作 Agent 结构化输出。
        :param evidence_cards: 已组织的证据卡。
        :return: 已校验的证据绑定列表。
        """

        valid_card_ids = {card.evidence_card_id for card in evidence_cards}
        bindings: list[EvidenceBindingDto] = []
        for item in _as_list(output.get("evidence_bindings")):
            item_map = _as_mapping(item)
            if item_map is None:
                continue
            card_ids = [
                card_id
                for card_id in _strings_from_unknown_list(
                    item_map.get("evidence_card_ids")
                )
                if card_id in valid_card_ids
            ]
            if not card_ids:
                continue
            retrieval_ids = _strings_from_unknown_list(item_map.get("retrieval_ids"))
            summary = (
                _read_string(item_map.get("binding_summary")) or "证据支持该声明。"
            )
            claim_id = (
                _read_string(item_map.get("claim_id")) or f"claim_{len(bindings) + 1}"
            )
            bindings.append(
                EvidenceBindingDto(
                    claim_id=claim_id,
                    evidence_card_ids=card_ids,
                    retrieval_ids=retrieval_ids,
                    binding_summary=summary,
                )
            )
        return bindings

    def _deterministic_evidence_bindings(
        self,
        *,
        evidence_cards: list[EvidenceCardDto],
    ) -> list[EvidenceBindingDto]:
        """根据证据卡构建默认证据绑定。

        :param evidence_cards: 已组织的证据卡。
        :return: 默认证据绑定列表。
        """

        return [
            EvidenceBindingDto(
                claim_id=f"claim_{index}",
                evidence_card_ids=[card.evidence_card_id],
                retrieval_ids=list(card.retrieval_ids),
                binding_summary=card.supported_claim_summary[:256],
            )
            for index, card in enumerate(evidence_cards, start=1)
        ]

    async def _run_grounding_check(
        self,
        *,
        request: EducationGenerationRequestDto,
        brief: EducationBriefDto,
        plan: ExplanationPlanDto,
        evidence_cards: list[EvidenceCardDto],
        draft_response: str,
        settings: EducationAgentSettings,
    ) -> GroundingCheckSummaryDto:
        """执行接地性自检，失败时使用确定性轻量扫描。

        :param request: 当前科普生成请求。
        :param brief: 本轮科普 brief。
        :param plan: 解释维度计划。
        :param evidence_cards: 已组织的证据卡。
        :param draft_response: 待检查的科普草稿正文。
        :param settings: 当前科普配置。
        :return: 接地性自检摘要。
        """

        result = await self._run_sub_agent(
            request=request,
            agent_id=settings.grounding_checker_agent_id,
            agent_version=settings.grounding_checker_agent_version,
            stage="grounding_checker",
            task_input={
                "brief": brief.model_dump(mode="json"),
                "explanation_plan": plan.model_dump(mode="json"),
                "evidence_cards": [
                    card.model_dump(mode="json") for card in evidence_cards
                ],
                "draft_response": draft_response,
            },
            timeout_seconds=settings.timeouts.grounding_seconds,
        )
        if result is None:
            return self._deterministic_grounding_check(draft_response=draft_response)
        summary = self._grounding_from_output(output=result.parsed_output)
        if summary is None:
            return self._deterministic_grounding_check(draft_response=draft_response)
        return summary

    def _grounding_from_output(
        self,
        *,
        output: JsonMap,
    ) -> GroundingCheckSummaryDto | None:
        """从接地性自检 Agent 输出解析检查摘要。

        :param output: 接地性自检 Agent 结构化输出。
        :return: 可用自检摘要；解析失败时返回 None。
        """

        try:
            return GroundingCheckSummaryDto(
                passed=_read_bool(output.get("passed"), default=True),
                risk_flags=_strings_from_unknown_list(output.get("risk_flags")),
                unsupported_claims=_strings_from_unknown_list(
                    output.get("unsupported_claims")
                ),
                forbidden_format_detected=_read_bool(
                    output.get("forbidden_format_detected"),
                    default=False,
                ),
                t4_risk_detected=_read_bool(
                    output.get("t4_risk_detected"),
                    default=False,
                ),
                reference_range_risk_detected=_read_bool(
                    output.get("reference_range_risk_detected"),
                    default=False,
                ),
                restricted_source_risk_detected=_read_bool(
                    output.get("restricted_source_risk_detected"),
                    default=False,
                ),
            )
        except ValidationError:
            return None

    def _deterministic_grounding_check(
        self,
        *,
        draft_response: str,
    ) -> GroundingCheckSummaryDto:
        """用轻量确定性扫描构建接地性自检摘要。

        :param draft_response: 待检查的科普草稿正文。
        :return: 接地性自检摘要。
        """

        forbidden_format = _contains_any(draft_response, _FORBIDDEN_FORMAT_TERMS)
        t4_risk = _contains_any(draft_response, _T4_RISK_TERMS)
        reference_range_risk = _contains_any(
            draft_response,
            _REFERENCE_RANGE_RISK_TERMS,
        )
        risk_flags: list[str] = []
        if forbidden_format:
            risk_flags.append("forbidden_format_detected")
        if t4_risk:
            risk_flags.append("t4_risk_detected")
        if reference_range_risk:
            risk_flags.append("reference_range_risk_detected")
        return GroundingCheckSummaryDto(
            passed=not risk_flags,
            risk_flags=risk_flags,
            forbidden_format_detected=forbidden_format,
            t4_risk_detected=t4_risk,
            reference_range_risk_detected=reference_range_risk,
        )

    def _status_for_written_draft(
        self,
        *,
        sufficiency: EvidenceSufficiencyResultDto,
        rag_summary: RagUsageSummaryDto,
        grounding_check: GroundingCheckSummaryDto,
    ) -> EducationDraftStatus:
        """根据证据、RAG 和自检状态选择草稿状态。

        :param sufficiency: 证据充分性结果。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param grounding_check: 接地性自检摘要。
        :return: 科普草稿状态。
        """

        if not grounding_check.passed:
            return EducationDraftStatus.NEEDS_SAFETY_REVIEW
        if sufficiency.status is EvidenceSufficiencyStatus.PARTIAL:
            return EducationDraftStatus.RAG_DEGRADED_CONSERVATIVE
        if rag_summary.degraded:
            return EducationDraftStatus.RAG_DEGRADED_CONSERVATIVE
        return EducationDraftStatus.DRAFT_READY

    def _build_trace_patch(
        self,
        *,
        plan: ExplanationPlanDto,
        rag_summary: RagUsageSummaryDto,
        degraded_flags: list[str],
        settings: EducationAgentSettings,
    ) -> EducationTracePatchDto:
        """构建科普 trace patch。

        :param plan: 解释维度计划。
        :param rag_summary: 本轮 RAG 使用摘要。
        :param degraded_flags: 当前累计降级标记。
        :param settings: 当前科普配置。
        :return: 可写入逻辑链的科普 trace patch。
        """

        return EducationTracePatchDto(
            education_agent_version=settings.education_agent_version,
            planner_version=settings.planner_version,
            writer_version=settings.writer_version,
            selected_dimensions=[
                dimension.dimension_code for dimension in plan.dimensions
            ],
            retrieval_ids=list(rag_summary.retrieval_ids),
            degraded_flags=_unique_strings(degraded_flags),
        )

    async def _run_sub_agent(
        self,
        *,
        request: EducationGenerationRequestDto,
        agent_id: str,
        agent_version: str,
        stage: str,
        task_input: JsonMap,
        timeout_seconds: float,
    ) -> AgentRunResultDto | None:
        """通过 AgentRunner 执行一个受控内部子 Agent。

        :param request: 当前科普生成请求。
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

    def _draft_response_ref(self, *, request: EducationGenerationRequestDto) -> str:
        """构建科普草稿引用 ID。

        :param request: 当前科普生成请求。
        :return: 可供后续图节点和 trace 使用的草稿引用。
        """

        return f"draft:{request.trace_id}:{request.task_id}"

    async def _write_trace_safely(
        self,
        *,
        request: EducationGenerationRequestDto,
        draft: EducationDraftDto,
    ) -> EducationTraceWriteResultDto:
        """以降级安全方式写入科普 trace 摘要。

        :param request: 当前科普生成请求。
        :param draft: 已生成的科普草稿。
        :return: trace 写入结果；异常时返回 degraded。
        """

        try:
            return await self._trace_sink.write_education_trace(
                EducationTraceRecordDto(
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
                    evidence_binding_count=len(draft.evidence_bindings),
                    rag_invoked=draft.rag_summary.rag_invoked,
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception as exc:
            return EducationTraceWriteResultDto(
                status=EducationTraceWriteStatus.DEGRADED,
                error_code=f"EDUCATION_TRACE_ERROR:{type(exc).__name__}",
                retryable=True,
                detail="EducationAgent trace 写入异常，已降级返回草稿",
            )

    def _record_observability(
        self,
        *,
        request: EducationGenerationRequestDto,
        draft: EducationDraftDto | None,
        duration_ms: int,
    ) -> None:
        """记录 EducationAgent 端到端观测事件。

        :param request: 当前科普生成请求。
        :param draft: 已生成草稿；失败时为 None。
        :param duration_ms: 端到端耗时毫秒数。
        :return: None。
        """

        if self._observability_provider is None:
            return
        status = draft.status.value if draft is not None else "failed"
        labels = {
            "component": _COMPONENT_NAME,
            "generation_profile": request.generation_profile,
            "status": status,
        }
        self._observability_provider.record_metric(
            metric_name="education_agent_latency_ms",
            value=float(duration_ms),
            metric_type=MetricType.HISTOGRAM,
            labels=labels,
            description="EducationAgent end-to-end latency in milliseconds.",
        )
        self._observability_provider.record_event(
            event_name="education_agent_generate_draft",
            component=_COMPONENT_NAME,
            level=StructuredLogLevel.INFO,
            safe_fields={
                "status": status,
                "generation_profile": request.generation_profile,
                "duration_ms": duration_ms,
            },
        )


__all__: tuple[str, ...] = ("DefaultEducationAgent",)
