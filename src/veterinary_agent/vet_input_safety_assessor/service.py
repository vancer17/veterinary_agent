##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/service.py
# 作用: 实现 VetInputSafetyAssessor 应用内服务，编排词库匹配、语义候选、结构化抽取、LLM 仲裁和业务裁决。
# 边界: 不读取宠物画像、不调用 RAG、不生成对外回复、不写入长期记忆；仅返回输入安全评估结果和脱敏摘要。
##################################################################################################

import asyncio
from time import perf_counter
from typing import Protocol

from pydantic import ValidationError

from veterinary_agent.agent_runner import (
    AgentRunRequestDto,
    AgentRunStatus,
    AgentRunner,
    AgentRunnerError,
)
from veterinary_agent.config import (
    RuntimeConfigError,
    RuntimeConfigProvider,
    RuntimeConfigSnapshot,
    VetInputSafetyAssessorSettings,
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
)
from veterinary_agent.vet_input_safety_assessor.dto import (
    AssessmentTraceSummaryDto,
    BatchVetInputAssessmentRequestDto,
    BatchVetInputAssessmentResultDto,
    InputSafetySignalDto,
    JsonMap,
    LlmArbitrationResultDto,
    ResolvedProfileDecisionDto,
    SemanticRouteCandidateDto,
    StructuredSignalExtractionSummaryDto,
    VetInputAssessmentRequestDto,
    VetInputAssessmentResultDto,
    VetInputAssessmentTraceRecordDto,
    VetInputSafetyTraceWriteResultDto,
    build_input_text_hash,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    AssessmentStatus,
    RouteLabel,
    SafetySignalCode,
    SignalStrength,
    VetInputAssessmentTraceWriteStatus,
    VetInputSafetyAssessorErrorCode,
    VetInputSafetyAssessorOperation,
    VetIntent,
)
from veterinary_agent.vet_input_safety_assessor.errors import (
    VetInputSafetyAssessorError,
)
from veterinary_agent.vet_input_safety_assessor.matchers import (
    KeywordLexicalSignalMatcher,
    KeywordSemanticRouteClassifier,
)
from veterinary_agent.vet_input_safety_assessor.ports import (
    LexicalSignalMatcher,
    SemanticRouteClassifier,
    StructuredSignalExtractor,
    TodoStructuredSignalExtractor,
)
from veterinary_agent.vet_input_safety_assessor.resolver import (
    VetProfileDecisionResolver,
)
from veterinary_agent.vet_input_safety_assessor.trace import (
    TodoVetInputSafetyTraceSink,
    VetInputSafetyTraceSink,
)

_COMPONENT_NAME = "vet_input_safety_assessor"
_DISABLED_EXTRACTOR_VERSION = "disabled-local-extractor"


class VetInputSafetyAssessor(Protocol):
    """VetInputSafetyAssessor 应用内服务接口。"""

    def is_ready(self) -> bool:
        """判断输入安全评估服务是否具备执行条件。

        :return: 若 RuntimeConfig 可用、组件启用且词库匹配器可用则返回 True。
        """

        ...

    async def assess(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> VetInputAssessmentResultDto:
        """评估单个子任务的输入安全与生成剖面。

        :param request: 单个子任务输入安全评估请求。
        :return: 单个子任务输入安全评估结果。
        :raises VetInputSafetyAssessorError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        ...

    async def batch_assess(
        self,
        request: BatchVetInputAssessmentRequestDto,
    ) -> BatchVetInputAssessmentResultDto:
        """批量评估当前轮子任务的输入安全与生成剖面。

        :param request: 当前轮子任务输入安全批量评估请求。
        :return: 按子任务输出的输入安全评估结果。
        :raises VetInputSafetyAssessorError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        ...


class DefaultVetInputSafetyAssessor:
    """VetInputSafetyAssessor 默认应用内实现。"""

    def __init__(
        self,
        *,
        runtime_config_provider: RuntimeConfigProvider,
        signal_matcher: LexicalSignalMatcher | None = None,
        semantic_classifier: SemanticRouteClassifier | None = None,
        structured_extractor: StructuredSignalExtractor | None = None,
        agent_runner: AgentRunner | None = None,
        resolver: VetProfileDecisionResolver | None = None,
        trace_sink: VetInputSafetyTraceSink | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        """初始化 VetInputSafetyAssessor 默认实现。

        :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
        :param signal_matcher: 可选输入安全词库匹配端口；缺失时使用本地关键词 matcher。
        :param semantic_classifier: 可选语义路由端口；缺失时使用本地轻量兜底分类器。
        :param structured_extractor: 可选本地结构化抽取端口；缺失时使用 TODO 空壳。
        :param agent_runner: 可选 AgentRunner 低置信仲裁端口。
        :param resolver: 可选最终业务裁决器。
        :param trace_sink: 可选输入安全 trace 写入端口；缺失时使用 TODO 空壳。
        :param observability_provider: 可选 Observability provider。
        :return: None。
        """

        self._runtime_config_provider = runtime_config_provider
        self._provided_signal_matcher = signal_matcher
        self._cached_signal_matcher: LexicalSignalMatcher | None = None
        self._cached_dictionary_version: str | None = None
        self._semantic_classifier = (
            semantic_classifier or KeywordSemanticRouteClassifier()
        )
        self._structured_extractor = (
            structured_extractor or TodoStructuredSignalExtractor()
        )
        self._agent_runner = agent_runner
        self._resolver = resolver or VetProfileDecisionResolver()
        self._trace_sink = trace_sink or TodoVetInputSafetyTraceSink()
        self._observability_provider = observability_provider

    def is_ready(self) -> bool:
        """判断输入安全评估服务是否具备执行条件。

        :return: 若 RuntimeConfig 可读取、组件启用且词库匹配器可用则返回 True。
        """

        if not self._runtime_config_provider.is_ready():
            return False
        try:
            snapshot = self._runtime_config_provider.current_snapshot()
            settings = snapshot.vet_input_safety_assessor
            matcher = self._signal_matcher(settings=settings)
        except (RuntimeConfigError, ValueError):
            return False
        return settings.enabled and matcher.is_ready()

    async def assess(
        self,
        request: VetInputAssessmentRequestDto,
    ) -> VetInputAssessmentResultDto:
        """评估单个子任务的输入安全与生成剖面。

        :param request: 单个子任务输入安全评估请求。
        :return: 单个子任务输入安全评估结果。
        :raises VetInputSafetyAssessorError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        batch_request = BatchVetInputAssessmentRequestDto(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=request.run_id,
            session_id=request.session_id,
            user_id=request.user_id,
            current_pet_id=request.current_pet_id,
            tasks=[request.task],
            light_context=request.light_context,
            original_user_message="",
            params_version=request.params_version,
            config_snapshot_id=request.config_snapshot_id,
        )
        result = await self.batch_assess(batch_request)
        return result.results[0]

    async def batch_assess(
        self,
        request: BatchVetInputAssessmentRequestDto,
    ) -> BatchVetInputAssessmentResultDto:
        """批量评估当前轮子任务的输入安全与生成剖面。

        :param request: 当前轮子任务输入安全批量评估请求。
        :return: 按子任务输出的输入安全评估结果。
        :raises VetInputSafetyAssessorError: 当前置契约或强依赖不满足执行条件时抛出。
        """

        started_monotonic = perf_counter()
        result: BatchVetInputAssessmentResultDto | None = None
        try:
            snapshot = self._load_config_snapshot(request=request)
            settings = snapshot.vet_input_safety_assessor
            self._validate_batch_request(request=request, settings=settings)
            assessment_results = []
            for task in request.tasks:
                single_request = self._single_request(
                    batch_request=request,
                    task_id=task.task_id,
                )
                assessment_results.append(
                    await self._assess_one(
                        request=single_request,
                        settings=settings,
                    )
                )
            batch_status = (
                AssessmentStatus.DEGRADED
                if any(
                    item.status is AssessmentStatus.DEGRADED
                    for item in assessment_results
                )
                else AssessmentStatus.SUCCEEDED
            )
            result = BatchVetInputAssessmentResultDto(
                results=assessment_results,
                status=batch_status,
            )
            trace_result = await self._write_trace_safely(
                request=request,
                result=result,
            )
            result = self._with_trace_status(
                result=result,
                trace_result=trace_result,
            )
            return result
        except VetInputSafetyAssessorError:
            raise
        except RuntimeConfigError as exc:
            raise VetInputSafetyAssessorError(
                code=(
                    VetInputSafetyAssessorErrorCode.INPUT_ASSESS_RUNTIME_CONFIG_UNAVAILABLE
                ),
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="VetInputSafetyAssessor 无法读取有效 RuntimeConfig 快照",
                retryable=exc.retryable,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"runtime_config_error_code": exc.code.value},
            ) from exc
        except ValidationError as exc:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="VetInputSafetyAssessor 输出结构不符合契约",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc
        except Exception as exc:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INTERNAL_ERROR,
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="VetInputSafetyAssessor 执行过程中发生未映射异常",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
            ) from exc
        finally:
            self._record_observability(
                request=request,
                result=result,
                duration_seconds=perf_counter() - started_monotonic,
            )

    def _load_config_snapshot(
        self,
        *,
        request: BatchVetInputAssessmentRequestDto,
    ) -> RuntimeConfigSnapshot:
        """读取并校验当前 RuntimeConfig 快照。

        :param request: 当前批量评估请求。
        :return: 当前 RuntimeConfig 快照。
        :raises VetInputSafetyAssessorError: 当组件未就绪或配置关闭时抛出。
        :raises RuntimeConfigError: 当 RuntimeConfig provider 不可用时抛出。
        """

        snapshot = self._runtime_config_provider.current_snapshot()
        settings = snapshot.vet_input_safety_assessor
        if not settings.enabled:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_NOT_READY,
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="VetInputSafetyAssessor 已被 RuntimeConfig 关闭",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
            )
        matcher = self._signal_matcher(settings=settings)
        if not matcher.is_ready():
            raise VetInputSafetyAssessorError(
                code=(
                    VetInputSafetyAssessorErrorCode.INPUT_ASSESS_SIGNAL_DICTIONARY_UNAVAILABLE
                ),
                operation=VetInputSafetyAssessorOperation.MATCH_SIGNALS,
                message="VetInputSafetyAssessor SAF 词库或匹配器不可用",
                retryable=True,
                request_id=request.request_id,
                trace_id=request.trace_id,
            )
        return snapshot

    def _validate_batch_request(
        self,
        *,
        request: BatchVetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
    ) -> None:
        """校验批量评估请求的业务前置条件。

        :param request: 当前批量评估请求。
        :param settings: 当前组件运行配置。
        :return: None。
        :raises VetInputSafetyAssessorError: 当请求违反输入安全评估契约时抛出。
        """

        if len(request.tasks) > settings.max_tasks_per_turn:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                operation=VetInputSafetyAssessorOperation.VALIDATE_INPUT,
                message="当前轮子任务数量超过输入安全评估上限",
                retryable=False,
                request_id=request.request_id,
                trace_id=request.trace_id,
                conflict_with={"task_count": len(request.tasks)},
            )
        for task in request.tasks:
            if task.current_pet_id != request.current_pet_id:
                raise VetInputSafetyAssessorError(
                    code=(
                        VetInputSafetyAssessorErrorCode.INPUT_ASSESS_CURRENT_PET_INVALID
                    ),
                    operation=VetInputSafetyAssessorOperation.VALIDATE_INPUT,
                    message="子任务 current_pet_id 与当前宠物不一致",
                    retryable=False,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    task_id=task.task_id,
                )
            if not task.normalized_query.strip():
                raise VetInputSafetyAssessorError(
                    code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_EMPTY_TASK_TEXT,
                    operation=VetInputSafetyAssessorOperation.VALIDATE_INPUT,
                    message="子任务文本为空",
                    retryable=False,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    task_id=task.task_id,
                )
            if len(task.normalized_query) > settings.max_task_text_chars:
                raise VetInputSafetyAssessorError(
                    code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                    operation=VetInputSafetyAssessorOperation.VALIDATE_INPUT,
                    message="子任务文本超过输入安全评估长度上限",
                    retryable=False,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    task_id=task.task_id,
                    conflict_with={"text_chars": len(task.normalized_query)},
                )

    def _single_request(
        self,
        *,
        batch_request: BatchVetInputAssessmentRequestDto,
        task_id: str,
    ) -> VetInputAssessmentRequestDto:
        """从批量请求中构建单个子任务评估请求。

        :param batch_request: 当前批量评估请求。
        :param task_id: 需要构建单任务请求的子任务 ID。
        :return: 单个子任务输入安全评估请求。
        :raises VetInputSafetyAssessorError: 当 task_id 不存在时抛出。
        """

        task = next(
            (
                candidate
                for candidate in batch_request.tasks
                if candidate.task_id == task_id
            ),
            None,
        )
        if task is None:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                operation=VetInputSafetyAssessorOperation.VALIDATE_INPUT,
                message="批量请求中缺少指定子任务",
                retryable=False,
                request_id=batch_request.request_id,
                trace_id=batch_request.trace_id,
                task_id=task_id,
            )
        original_hash = (
            build_input_text_hash(batch_request.original_user_message)
            if batch_request.original_user_message
            else None
        )
        return VetInputAssessmentRequestDto(
            request_id=batch_request.request_id,
            trace_id=batch_request.trace_id,
            run_id=batch_request.run_id,
            session_id=batch_request.session_id,
            user_id=batch_request.user_id,
            current_pet_id=batch_request.current_pet_id,
            task=task,
            light_context=batch_request.light_context,
            original_user_message_hash=original_hash,
            params_version=batch_request.params_version,
            config_snapshot_id=batch_request.config_snapshot_id,
        )

    async def _assess_one(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
    ) -> VetInputAssessmentResultDto:
        """评估单个子任务并产出完整评估结果。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :return: 单个子任务输入安全评估结果。
        """

        signals = self._signal_matcher(settings=settings).match(request)
        semantic_candidates, semantic_unavailable = await self._semantic_candidates(
            request=request,
            settings=settings,
        )
        extraction_summary, extractor_unavailable = await self._extraction_summary(
            request=request,
            settings=settings,
        )
        arbitration: LlmArbitrationResultDto | None = None
        llm_unavailable = False
        if self._needs_arbitration(
            request=request,
            settings=settings,
            signals=signals,
            semantic_candidates=semantic_candidates,
        ):
            arbitration, llm_unavailable = await self._llm_arbitration(
                request=request,
                settings=settings,
            )
        decision = self._resolver.resolve(
            request=request,
            settings=settings,
            signals=signals,
            semantic_candidates=semantic_candidates,
            extraction_summary=extraction_summary,
            arbitration=arbitration,
            semantic_router_unavailable=semantic_unavailable,
            local_extractor_unavailable=extractor_unavailable,
            llm_unavailable=llm_unavailable,
        )
        status = (
            AssessmentStatus.DEGRADED
            if semantic_unavailable
            or extractor_unavailable
            or llm_unavailable
            or decision.fallback_used
            else AssessmentStatus.SUCCEEDED
        )
        trace_summary = AssessmentTraceSummaryDto(
            assessor_version=settings.assessor_version,
            method=decision.method,
            llm_unavailable=llm_unavailable,
            semantic_router_unavailable=semantic_unavailable,
            local_extractor_unavailable=extractor_unavailable,
            fallback_used=decision.fallback_used,
            signal_codes=[signal.code for signal in signals],
            final_decision_reason_code=decision.reason_code,
        )
        assessment_summary = self._assessment_summary(
            request=request,
            settings=settings,
            signals=signals,
            semantic_candidates=semantic_candidates,
            extraction_summary=extraction_summary,
            decision=decision,
            trace_summary=trace_summary,
        )
        return VetInputAssessmentResultDto(
            task_id=request.task.task_id,
            current_pet_id=request.current_pet_id,
            status=status,
            signals=signals,
            intent=decision.intent,
            intent_confidence=decision.intent_confidence,
            generation_profile=decision.generation_profile,
            route=decision.route,
            executor_key=decision.executor_key,
            compression_strategy=decision.compression_strategy,
            disambiguation_method=decision.disambiguation_method,
            audit_tier_floor=decision.audit_tier_floor,
            assessment_summary=assessment_summary,
            trace_summary=trace_summary,
        )

    def _signal_matcher(
        self,
        *,
        settings: VetInputSafetyAssessorSettings,
    ) -> LexicalSignalMatcher:
        """读取当前可用的词库匹配器。

        :param settings: 当前组件运行配置。
        :return: 词库匹配器实例。
        """

        if self._provided_signal_matcher is not None:
            return self._provided_signal_matcher
        if self._cached_dictionary_version != settings.dictionary_version:
            self._cached_signal_matcher = KeywordLexicalSignalMatcher(
                dictionary_version=settings.dictionary_version
            )
            self._cached_dictionary_version = settings.dictionary_version
        if self._cached_signal_matcher is None:
            self._cached_signal_matcher = KeywordLexicalSignalMatcher(
                dictionary_version=settings.dictionary_version
            )
        return self._cached_signal_matcher

    async def _semantic_candidates(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
    ) -> tuple[list[SemanticRouteCandidateDto], bool]:
        """读取语义路由候选并返回可用性状态。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :return: 语义路由候选列表和不可用标记。
        """

        if not settings.semantic_router_enabled:
            return [], False
        if not self._semantic_classifier.is_ready():
            return [], True
        try:
            async with asyncio.timeout(settings.timeouts.semantic_router_seconds):
                return await self._semantic_classifier.classify(request), False
        except (TimeoutError, asyncio.TimeoutError, Exception):
            return [], True

    async def _extraction_summary(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
    ) -> tuple[StructuredSignalExtractionSummaryDto, bool]:
        """读取本地结构化抽取摘要并返回可用性状态。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :return: 结构化抽取摘要和不可用标记。
        """

        if not settings.local_extractor_enabled:
            return (
                StructuredSignalExtractionSummaryDto(
                    extractor_version=_DISABLED_EXTRACTOR_VERSION,
                    extracted_concept_types=[],
                    confidence=0.0,
                    unavailable=False,
                ),
                False,
            )
        if not self._structured_extractor.is_ready():
            return (
                await self._structured_extractor.extract(request),
                True,
            )
        try:
            async with asyncio.timeout(settings.timeouts.local_extractor_seconds):
                summary = await self._structured_extractor.extract(request)
                return summary, summary.unavailable
        except (TimeoutError, asyncio.TimeoutError, Exception):
            return (
                StructuredSignalExtractionSummaryDto(
                    extractor_version="local-extractor.timeout",
                    extracted_concept_types=[],
                    confidence=0.0,
                    unavailable=True,
                ),
                True,
            )

    def _needs_arbitration(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
        signals: list[InputSafetySignalDto],
        semantic_candidates: list[SemanticRouteCandidateDto],
    ) -> bool:
        """判断当前子任务是否需要低置信 LLM 仲裁。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :param signals: 已检出的输入侧安全信号。
        :param semantic_candidates: 语义路由候选列表。
        :return: 若需要触发 LLM 仲裁则返回 True。
        """

        if not settings.llm_arbitration_enabled:
            return False
        if self._has_hard_safety_signal(signals):
            return False
        if request.task.confidence < settings.confidence.min_intent_confidence:
            return True
        if not semantic_candidates:
            return True
        best = max(semantic_candidates, key=lambda candidate: candidate.score)
        return (
            best.score < settings.confidence.min_semantic_score
            or best.margin < settings.confidence.min_semantic_margin
        )

    def _has_hard_safety_signal(self, signals: list[InputSafetySignalDto]) -> bool:
        """判断是否存在不可被仲裁覆盖的硬安全信号。

        :param signals: 已检出的输入侧安全信号。
        :return: 若存在 SAF-01 或 L3 SAF-03 则返回 True。
        """

        return any(
            signal.code is SafetySignalCode.SAF_01_TOXIC_SUBSTANCE
            or (
                signal.code is SafetySignalCode.SAF_03_ACUTE_RED_FLAG
                and signal.strength is SignalStrength.L3
            )
            for signal in signals
        )

    async def _llm_arbitration(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
    ) -> tuple[LlmArbitrationResultDto | None, bool]:
        """执行低置信 LLM 仲裁。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :return: 仲裁结果和 LLM 不可用标记。
        """

        if self._agent_runner is None or not self._agent_runner.is_ready():
            return None, True
        try:
            async with asyncio.timeout(settings.timeouts.llm_arbitration_seconds):
                agent_result = await self._agent_runner.run_agent(
                    AgentRunRequestDto(
                        run_id=f"{request.run_id}:input_safety_arbitration",
                        trace_id=request.trace_id,
                        request_id=request.request_id,
                        session_id=request.session_id,
                        user_id=request.user_id,
                        agent_id=settings.arbitration_agent_id,
                        agent_version=settings.arbitration_agent_version,
                        task_input={
                            "task_id": request.task.task_id,
                            "task_type": request.task.task_type.value,
                            "normalized_query": request.task.normalized_query,
                            "current_pet_id": request.current_pet_id,
                        },
                        runtime_options={
                            "component": _COMPONENT_NAME,
                            "params_version": request.params_version,
                            "config_snapshot_id": request.config_snapshot_id,
                        },
                    )
                )
        except (AgentRunnerError, TimeoutError, asyncio.TimeoutError, Exception):
            return None, True
        if agent_result.status is not AgentRunStatus.SUCCEEDED:
            return None, True
        try:
            return self._parse_arbitration_output(agent_result.parsed_output), False
        except (ValidationError, ValueError):
            return None, True

    def _parse_arbitration_output(self, output: JsonMap) -> LlmArbitrationResultDto:
        """解析 AgentRunner 结构化仲裁输出。

        :param output: AgentRunner parsed_output 映射。
        :return: 已通过 DTO 校验的仲裁结果。
        :raises ValidationError: 当输出无法通过 DTO 校验时抛出。
        :raises ValueError: 当输出字段值不属于受控枚举时抛出。
        """

        generation_profile_value = output.get("generation_profile")
        generation_profile = (
            VetGenerationProfile(str(generation_profile_value))
            if isinstance(generation_profile_value, str)
            and generation_profile_value.strip()
            else None
        )
        return LlmArbitrationResultDto(
            intent=VetIntent(str(output.get("intent"))),
            intent_confidence=self._float_from_output(
                value=output.get("intent_confidence"),
                default=0.0,
            ),
            route=RouteLabel(str(output.get("route"))),
            generation_profile=generation_profile,
            executor_key=VetExecutorKey(str(output.get("executor_key"))),
            compression_strategy=ContextCompressionStrategy(
                str(output.get("compression_strategy"))
            ),
            reason_code=str(output.get("reason_code", "llm_arbitration")),
        )

    def _float_from_output(self, *, value: object, default: float) -> float:
        """从未知输出值中读取浮点数。

        :param value: AgentRunner 输出中的未知字段值。
        :param default: 无法读取浮点数时使用的默认值。
        :return: 归一化到 0 到 1 区间的浮点数。
        """

        if isinstance(value, int | float):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            try:
                return max(0.0, min(1.0, float(value)))
            except ValueError:
                return default
        return default

    def _assessment_summary(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
        signals: list[InputSafetySignalDto],
        semantic_candidates: list[SemanticRouteCandidateDto],
        extraction_summary: StructuredSignalExtractionSummaryDto,
        decision: ResolvedProfileDecisionDto,
        trace_summary: AssessmentTraceSummaryDto,
    ) -> JsonMap:
        """构建供下游消费的输入安全受控摘要。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :param signals: 已检出的输入侧安全信号。
        :param semantic_candidates: 语义路由候选列表。
        :param extraction_summary: 本地结构化抽取摘要。
        :param decision: 最终剖面裁决。
        :param trace_summary: 输入安全 trace 摘要。
        :return: 不含用户原文的受控评估摘要。
        """

        profile = (
            decision.generation_profile.value
            if decision.generation_profile is not None
            else None
        )
        return {
            "task_id": request.task.task_id,
            "intent": decision.intent.value,
            "intent_confidence": decision.intent_confidence,
            "generation_profile": profile,
            "route": decision.route.value,
            "executor_key": decision.executor_key.value,
            "compression_strategy": decision.compression_strategy.value,
            "disambiguation_method": decision.disambiguation_method.value,
            "audit_tier_floor": decision.audit_tier_floor.value,
            "signals": [signal.model_dump(mode="json") for signal in signals],
            "signal_codes": [signal.code.value for signal in signals],
            "semantic_candidates": [
                candidate.model_dump(mode="json") for candidate in semantic_candidates
            ],
            "structured_extraction": extraction_summary.model_dump(mode="json"),
            "trace_summary": trace_summary.model_dump(mode="json"),
            "assessor_version": settings.assessor_version,
            "dictionary_version": settings.dictionary_version,
            "params_version": request.params_version,
            "config_snapshot_id": request.config_snapshot_id,
            "final_decision_reason_code": decision.reason_code,
            "fallback_used": decision.fallback_used,
        }

    async def _write_trace_safely(
        self,
        *,
        request: BatchVetInputAssessmentRequestDto,
        result: BatchVetInputAssessmentResultDto,
    ) -> VetInputSafetyTraceWriteResultDto:
        """安全写入输入安全评估脱敏摘要。

        :param request: 当前批量评估请求。
        :param result: 当前批量评估结果。
        :return: trace 写入结果；异常会被转换为降级结果。
        """

        original_hash = (
            build_input_text_hash(request.original_user_message)
            if request.original_user_message
            else None
        )
        try:
            return await self._trace_sink.write_assessment_summary(
                VetInputAssessmentTraceRecordDto(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=request.run_id,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    current_pet_id=request.current_pet_id,
                    original_user_message_hash=original_hash,
                    result_summaries=[
                        item.assessment_summary for item in result.results
                    ],
                    params_version=request.params_version,
                    config_snapshot_id=request.config_snapshot_id,
                )
            )
        except Exception:
            return VetInputSafetyTraceWriteResultDto(
                status=VetInputAssessmentTraceWriteStatus.DEGRADED,
                error_code="VET_INPUT_SAFETY_TRACE_WRITE_FAILED",
                retryable=True,
                detail="VetInputSafetyAssessor trace 写入发生未映射异常",
            )

    def _with_trace_status(
        self,
        *,
        result: BatchVetInputAssessmentResultDto,
        trace_result: VetInputSafetyTraceWriteResultDto,
    ) -> BatchVetInputAssessmentResultDto:
        """将 trace 写入状态回填到批量和单任务结果。

        :param result: 原始批量评估结果。
        :param trace_result: trace 写入结果。
        :return: 已回填 trace 状态的批量评估结果。
        """

        updated_results = [
            item.model_copy(update={"trace_delivery_status": trace_result.status})
            for item in result.results
        ]
        status = (
            AssessmentStatus.DEGRADED
            if trace_result.status is VetInputAssessmentTraceWriteStatus.DEGRADED
            or any(item.status is AssessmentStatus.DEGRADED for item in updated_results)
            else result.status
        )
        return result.model_copy(
            update={
                "results": updated_results,
                "status": status,
                "trace_delivery_status": trace_result.status,
            }
        )

    def _record_observability(
        self,
        *,
        request: BatchVetInputAssessmentRequestDto,
        result: BatchVetInputAssessmentResultDto | None,
        duration_seconds: float,
    ) -> None:
        """记录输入安全评估指标与结构化事件。

        :param request: 当前批量评估请求。
        :param result: 当前批量评估结果；失败时为空。
        :param duration_seconds: 本次评估耗时，单位为秒。
        :return: None。
        """

        if self._observability_provider is None:
            return
        status = result.status.value if result is not None else "failed"
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_assessment_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"status": status},
            description="输入安全评估请求总数。",
        )
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_assessment_duration_ms",
            value=duration_seconds * 1000,
            metric_type=MetricType.HISTOGRAM,
            labels={"status": status},
            description="输入安全评估耗时，单位为毫秒。",
        )
        if result is None:
            self._observability_provider.record_event(
                event_name="vet_input_safety_assessor.failed",
                component=_COMPONENT_NAME,
                level=StructuredLogLevel.ERROR,
                safe_fields={"request_id": request.request_id},
            )
            return
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_task_count",
            value=len(result.results),
            metric_type=MetricType.HISTOGRAM,
            labels={"status": status},
            description="每轮参与输入安全评估的子任务数量。",
        )
        for item in result.results:
            self._record_result_metrics(result=item)

    def _record_result_metrics(self, *, result: VetInputAssessmentResultDto) -> None:
        """记录单个子任务评估结果指标。

        :param result: 单个子任务输入安全评估结果。
        :return: None。
        """

        if self._observability_provider is None:
            return
        profile = (
            result.generation_profile.value
            if result.generation_profile is not None
            else "none"
        )
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_intent_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"intent": result.intent.value},
            description="按最终意图统计的输入安全评估数量。",
        )
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_executor_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"executor_key": result.executor_key.value},
            description="按执行器统计的输入安全评估数量。",
        )
        self._observability_provider.record_metric(
            metric_name="vet_input_safety_profile_total",
            value=1,
            metric_type=MetricType.COUNTER,
            labels={"generation_profile": profile},
            description="按生成剖面统计的输入安全评估数量。",
        )
        for signal in result.signals:
            self._observability_provider.record_metric(
                metric_name="vet_input_safety_signal_total",
                value=1,
                metric_type=MetricType.COUNTER,
                labels={
                    "signal_code": signal.code.value,
                    "signal_strength": signal.strength.value,
                },
                description="按信号码和强度统计的输入安全信号数量。",
            )


def create_default_vet_input_safety_assessor(
    *,
    runtime_config_provider: RuntimeConfigProvider,
    signal_matcher: LexicalSignalMatcher | None = None,
    semantic_classifier: SemanticRouteClassifier | None = None,
    structured_extractor: StructuredSignalExtractor | None = None,
    agent_runner: AgentRunner | None = None,
    resolver: VetProfileDecisionResolver | None = None,
    trace_sink: VetInputSafetyTraceSink | None = None,
    observability_provider: ObservabilityProvider | None = None,
) -> DefaultVetInputSafetyAssessor:
    """创建默认 VetInputSafetyAssessor 服务。

    :param runtime_config_provider: RuntimeConfig 当前快照只读 provider。
    :param signal_matcher: 可选输入安全词库匹配端口。
    :param semantic_classifier: 可选语义路由端口。
    :param structured_extractor: 可选本地结构化抽取端口。
    :param agent_runner: 可选 AgentRunner 低置信仲裁端口。
    :param resolver: 可选最终业务裁决器。
    :param trace_sink: 可选 trace 写入端口。
    :param observability_provider: 可选 Observability provider。
    :return: 默认输入安全评估服务实现。
    """

    return DefaultVetInputSafetyAssessor(
        runtime_config_provider=runtime_config_provider,
        signal_matcher=signal_matcher,
        semantic_classifier=semantic_classifier,
        structured_extractor=structured_extractor,
        agent_runner=agent_runner,
        resolver=resolver,
        trace_sink=trace_sink,
        observability_provider=observability_provider,
    )


__all__: tuple[str, ...] = (
    "DefaultVetInputSafetyAssessor",
    "VetInputSafetyAssessor",
    "create_default_vet_input_safety_assessor",
)
