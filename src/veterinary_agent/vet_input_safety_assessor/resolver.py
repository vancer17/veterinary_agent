##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/resolver.py
# 作用: 实现 VetInputSafetyAssessor 的确定性业务裁决器，归一化意图、路由、剖面、执行器与压缩策略。
# 边界: 不调用 LLM、RAG、OCR 或外部存储；只消费已结构化的信号、语义候选、抽取摘要和轻量上下文。
##################################################################################################

from veterinary_agent.config import VetInputSafetyAssessorSettings
from veterinary_agent.vet_context_builder import (
    ContextCompressionStrategy,
    VetAuditTier,
    VetExecutorKey,
    VetGenerationProfile,
)
from veterinary_agent.vet_input_safety_assessor.dto import (
    InputSafetySignalDto,
    LightweightAssessmentContextDto,
    LlmArbitrationResultDto,
    ResolvedProfileDecisionDto,
    SemanticRouteCandidateDto,
    StructuredSignalExtractionSummaryDto,
    VetInputAssessmentRequestDto,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    AssessmentMethod,
    DisambiguationMethod,
    RouteLabel,
    SafetySignalCode,
    SignalStrength,
    VetIntent,
)
from veterinary_agent.vet_task_decomposer import VetTaskType


class VetProfileDecisionResolver:
    """输入安全评估最终剖面裁决器。"""

    def resolve(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
        signals: list[InputSafetySignalDto],
        semantic_candidates: list[SemanticRouteCandidateDto],
        extraction_summary: StructuredSignalExtractionSummaryDto,
        arbitration: LlmArbitrationResultDto | None,
        semantic_router_unavailable: bool,
        local_extractor_unavailable: bool,
        llm_unavailable: bool,
    ) -> ResolvedProfileDecisionDto:
        """解析单个子任务的最终业务剖面裁决。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :param signals: 已检出的输入侧安全信号。
        :param semantic_candidates: 语义路由候选列表。
        :param extraction_summary: 本地结构化抽取摘要。
        :param arbitration: 可选 LLM 仲裁结果。
        :param semantic_router_unavailable: 语义路由是否不可用。
        :param local_extractor_unavailable: 本地抽取器是否不可用。
        :param llm_unavailable: LLM 仲裁是否不可用。
        :return: 最终剖面和执行器裁决。
        """

        del extraction_summary
        if settings.deterministic_saf01_override_enabled and self._has_signal(
            signals=signals,
            code=SafetySignalCode.SAF_01_TOXIC_SUBSTANCE,
        ):
            return self._safety_trigger_decision(
                intent=VetIntent.ACUTE_EVENT,
                confidence=1.0,
                reason_code="saf01_deterministic_override",
            )
        if self._should_trigger_saf03_realtime(
            settings=settings,
            signals=signals,
        ):
            return self._safety_trigger_decision(
                intent=VetIntent.ACUTE_EVENT,
                confidence=0.95,
                reason_code="saf03_realtime_or_l3_override",
            )
        if arbitration is not None and not self._has_hard_safety_signal(signals):
            return self._decision_from_arbitration(arbitration=arbitration)
        if self._is_report_task(request.task.task_type):
            return self._standard_decision(
                intent=VetIntent.REPORT_INTERPRETATION,
                executor_key=VetExecutorKey.LAB_REPORT_INTERPRETATION,
                confidence=0.82,
                method=AssessmentMethod.SEMANTIC_ROUTER,
                disambiguation_method=self._method_from_availability(
                    semantic_router_unavailable=semantic_router_unavailable,
                    local_extractor_unavailable=local_extractor_unavailable,
                    llm_unavailable=llm_unavailable,
                ),
                reason_code="report_task_standard_profile",
            )
        if self._is_education_context(
            task_type=request.task.task_type,
            signals=signals,
            semantic_candidates=semantic_candidates,
        ):
            return self._education_decision(
                confidence=self._best_semantic_score(semantic_candidates, default=0.76),
                reason_code="education_marker_or_task_type",
            )
        if self._is_nonmedical_task(request.task.task_type):
            return self._nonmedical_decision(
                intent=self._nonmedical_intent(request.task.task_type),
                confidence=self._best_semantic_score(semantic_candidates, default=0.72),
                signals=signals,
                reason_code="nonmedical_task_type",
            )
        if self._is_low_confidence_default(
            request=request,
            settings=settings,
            semantic_candidates=semantic_candidates,
        ):
            return self._cold_start_default_decision(
                request=request,
                settings=settings,
                context=request.light_context,
            )
        return self._standard_decision(
            intent=self._standard_intent(request.task.task_type),
            executor_key=VetExecutorKey.STANDARD_CONSULTATION,
            confidence=self._best_semantic_score(semantic_candidates, default=0.68),
            method=AssessmentMethod.SEMANTIC_ROUTER,
            disambiguation_method=DisambiguationMethod.SEMANTIC_ROUTER,
            reason_code="default_medical_standard",
        )

    def _has_signal(
        self,
        *,
        signals: list[InputSafetySignalDto],
        code: SafetySignalCode,
    ) -> bool:
        """判断信号列表是否包含指定编码。

        :param signals: 输入侧安全信号列表。
        :param code: 需要检查的安全信号码。
        :return: 若存在指定信号码则返回 True。
        """

        return any(signal.code is code for signal in signals)

    def _has_hard_safety_signal(self, signals: list[InputSafetySignalDto]) -> bool:
        """判断信号列表是否包含不可被 LLM 覆盖的硬安全信号。

        :param signals: 输入侧安全信号列表。
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

    def _should_trigger_saf03_realtime(
        self,
        *,
        settings: VetInputSafetyAssessorSettings,
        signals: list[InputSafetySignalDto],
    ) -> bool:
        """判断 SAF-03 是否满足急症安全触发条件。

        :param settings: 当前组件运行配置。
        :param signals: 输入侧安全信号列表。
        :return: 若应进入 safety_trigger 则返回 True。
        """

        if not settings.deterministic_saf03_realtime_override_enabled:
            return False
        saf03_signals = [
            signal
            for signal in signals
            if signal.code is SafetySignalCode.SAF_03_ACUTE_RED_FLAG
        ]
        if not saf03_signals:
            return False
        has_realtime = self._has_signal(
            signals=signals,
            code=SafetySignalCode.REALTIME_MARKER,
        )
        if has_realtime:
            return True
        education_or_hypothetical = self._has_signal(
            signals=signals,
            code=SafetySignalCode.EDUCATION_MARKER,
        ) or self._has_signal(
            signals=signals,
            code=SafetySignalCode.HYPOTHETICAL_MARKER,
        )
        if education_or_hypothetical:
            return False
        return any(signal.strength is SignalStrength.L3 for signal in saf03_signals)

    def _is_report_task(self, task_type: VetTaskType) -> bool:
        """判断子任务是否为报告读取或病历解析类任务。

        :param task_type: 子任务类型。
        :return: 若任务需要标准医学上下文和报告解读执行器则返回 True。
        """

        return task_type in {VetTaskType.REPORT_OCR, VetTaskType.RECORD_PARSE}

    def _is_nonmedical_task(self, task_type: VetTaskType) -> bool:
        """判断子任务是否属于纯非医疗养宠任务。

        :param task_type: 子任务类型。
        :return: 若任务属于营养、行为或护理则返回 True。
        """

        return task_type in {
            VetTaskType.NUTRITION,
            VetTaskType.BEHAVIOR,
            VetTaskType.CARE,
        }

    def _nonmedical_intent(self, task_type: VetTaskType) -> VetIntent:
        """将非医疗任务类型映射为输入侧意图。

        :param task_type: 子任务类型。
        :return: 非医疗输入侧意图。
        """

        if task_type is VetTaskType.NUTRITION:
            return VetIntent.NONMED_NUTRITION
        if task_type is VetTaskType.BEHAVIOR:
            return VetIntent.NONMED_BEHAVIOR
        return VetIntent.NONMED_CARE

    def _standard_intent(self, task_type: VetTaskType) -> VetIntent:
        """将普通医学或通用任务类型映射为输入侧意图。

        :param task_type: 子任务类型。
        :return: 标准问诊或普通问答意图。
        """

        if task_type is VetTaskType.TRIAGE:
            return VetIntent.SYMPTOM_TRIAGE
        return VetIntent.GENERAL_QA

    def _is_education_context(
        self,
        *,
        task_type: VetTaskType,
        signals: list[InputSafetySignalDto],
        semantic_candidates: list[SemanticRouteCandidateDto],
    ) -> bool:
        """判断当前子任务是否应进入科普剖面。

        :param task_type: 子任务类型。
        :param signals: 输入侧安全信号列表。
        :param semantic_candidates: 语义路由候选列表。
        :return: 若当前任务应进入 education 剖面则返回 True。
        """

        if task_type is VetTaskType.EDUCATION_QA:
            return True
        if self._has_signal(signals=signals, code=SafetySignalCode.EDUCATION_MARKER):
            return True
        return any(
            candidate.route_label == "education" and candidate.score >= 0.58
            for candidate in semantic_candidates
        )

    def _best_semantic_score(
        self,
        semantic_candidates: list[SemanticRouteCandidateDto],
        *,
        default: float,
    ) -> float:
        """读取语义候选中的最高分。

        :param semantic_candidates: 语义路由候选列表。
        :param default: 无候选时使用的默认分数。
        :return: 归一化后的最高分或默认分数。
        """

        if not semantic_candidates:
            return default
        return max(candidate.score for candidate in semantic_candidates)

    def _is_low_confidence_default(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
        semantic_candidates: list[SemanticRouteCandidateDto],
    ) -> bool:
        """判断当前任务是否需要冷启动保守默认裁决。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :param semantic_candidates: 语义路由候选列表。
        :return: 若任务置信度和语义候选均低于阈值则返回 True。
        """

        task_confidence_low = (
            request.task.confidence < settings.confidence.min_intent_confidence
        )
        if not semantic_candidates:
            return task_confidence_low
        best = max(semantic_candidates, key=lambda candidate: candidate.score)
        return (
            task_confidence_low
            and best.score < settings.confidence.min_semantic_score
            and best.margin < settings.confidence.min_semantic_margin
        )

    def _method_from_availability(
        self,
        *,
        semantic_router_unavailable: bool,
        local_extractor_unavailable: bool,
        llm_unavailable: bool,
    ) -> DisambiguationMethod:
        """根据弱依赖可用性选择消歧方法摘要。

        :param semantic_router_unavailable: 语义路由是否不可用。
        :param local_extractor_unavailable: 本地抽取器是否不可用。
        :param llm_unavailable: LLM 仲裁是否不可用。
        :return: 对应的消歧方法。
        """

        if not semantic_router_unavailable:
            return DisambiguationMethod.SEMANTIC_ROUTER
        if not local_extractor_unavailable:
            return DisambiguationMethod.STRUCTURED_EXTRACTION
        if not llm_unavailable:
            return DisambiguationMethod.LLM_ARBITRATED
        return DisambiguationMethod.FALLBACK_DEFAULT

    def _safety_trigger_decision(
        self,
        *,
        intent: VetIntent,
        confidence: float,
        reason_code: str,
    ) -> ResolvedProfileDecisionDto:
        """构建 safety_trigger 裁决。

        :param intent: 最终输入侧意图。
        :param confidence: 意图置信度。
        :param reason_code: 最终原因码。
        :return: safety_trigger 剖面裁决。
        """

        return ResolvedProfileDecisionDto(
            intent=intent,
            intent_confidence=confidence,
            generation_profile=VetGenerationProfile.SAFETY_TRIGGER,
            route=RouteLabel.SAFETY_TRIGGER,
            executor_key=VetExecutorKey.SAFETY_TRIGGER,
            compression_strategy=ContextCompressionStrategy.SAFETY_MINIMAL,
            disambiguation_method=DisambiguationMethod.DETERMINISTIC_OVERRIDE,
            audit_tier_floor=VetAuditTier.A,
            method=AssessmentMethod.DETERMINISTIC,
            fallback_used=False,
            reason_code=reason_code,
        )

    def _education_decision(
        self,
        *,
        confidence: float,
        reason_code: str,
    ) -> ResolvedProfileDecisionDto:
        """构建 education 裁决。

        :param confidence: 意图置信度。
        :param reason_code: 最终原因码。
        :return: education 剖面裁决。
        """

        return ResolvedProfileDecisionDto(
            intent=VetIntent.EDUCATION,
            intent_confidence=confidence,
            generation_profile=VetGenerationProfile.EDUCATION,
            route=RouteLabel.NORMAL,
            executor_key=VetExecutorKey.EDUCATION,
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
            disambiguation_method=DisambiguationMethod.SEMANTIC_ROUTER,
            audit_tier_floor=VetAuditTier.B,
            method=AssessmentMethod.SEMANTIC_ROUTER,
            fallback_used=False,
            reason_code=reason_code,
        )

    def _standard_decision(
        self,
        *,
        intent: VetIntent,
        executor_key: VetExecutorKey,
        confidence: float,
        method: AssessmentMethod,
        disambiguation_method: DisambiguationMethod,
        reason_code: str,
    ) -> ResolvedProfileDecisionDto:
        """构建 standard 类医学裁决。

        :param intent: 最终输入侧意图。
        :param executor_key: 实际医学执行器。
        :param confidence: 意图置信度。
        :param method: 最终评估方法。
        :param disambiguation_method: 消歧方法。
        :param reason_code: 最终原因码。
        :return: standard 生成剖面裁决。
        """

        return ResolvedProfileDecisionDto(
            intent=intent,
            intent_confidence=confidence,
            generation_profile=VetGenerationProfile.STANDARD,
            route=RouteLabel.NORMAL,
            executor_key=executor_key,
            compression_strategy=ContextCompressionStrategy.SINGLE_FULL,
            disambiguation_method=disambiguation_method,
            audit_tier_floor=VetAuditTier.A,
            method=method,
            fallback_used=False,
            reason_code=reason_code,
        )

    def _nonmedical_decision(
        self,
        *,
        intent: VetIntent,
        confidence: float,
        signals: list[InputSafetySignalDto],
        reason_code: str,
    ) -> ResolvedProfileDecisionDto:
        """构建纯非医疗养宠裁决。

        :param intent: 最终输入侧意图。
        :param confidence: 意图置信度。
        :param signals: 输入侧安全信号列表。
        :param reason_code: 最终原因码。
        :return: 非医疗执行器裁决。
        """

        audit_tier = VetAuditTier.B if signals else VetAuditTier.C
        return ResolvedProfileDecisionDto(
            intent=intent,
            intent_confidence=confidence,
            generation_profile=None,
            route=RouteLabel.NORMAL,
            executor_key=VetExecutorKey.NONMEDICAL_PET_CARE,
            compression_strategy=ContextCompressionStrategy.EDUCATION_LIGHT,
            disambiguation_method=DisambiguationMethod.SEMANTIC_ROUTER,
            audit_tier_floor=audit_tier,
            method=AssessmentMethod.SEMANTIC_ROUTER,
            fallback_used=False,
            reason_code=reason_code,
        )

    def _decision_from_arbitration(
        self,
        *,
        arbitration: LlmArbitrationResultDto,
    ) -> ResolvedProfileDecisionDto:
        """将 LLM 仲裁结果转换为最终裁决。

        :param arbitration: 已通过 schema 校验的 LLM 仲裁结果。
        :return: LLM 仲裁裁决。
        """

        audit_tier = (
            VetAuditTier.A
            if arbitration.executor_key
            in {
                VetExecutorKey.STANDARD_CONSULTATION,
                VetExecutorKey.SAFETY_TRIGGER,
                VetExecutorKey.LAB_REPORT_INTERPRETATION,
            }
            else VetAuditTier.B
        )
        return ResolvedProfileDecisionDto(
            intent=arbitration.intent,
            intent_confidence=arbitration.intent_confidence,
            generation_profile=arbitration.generation_profile,
            route=arbitration.route,
            executor_key=arbitration.executor_key,
            compression_strategy=arbitration.compression_strategy,
            disambiguation_method=DisambiguationMethod.LLM_ARBITRATED,
            audit_tier_floor=audit_tier,
            method=AssessmentMethod.LLM_ARBITRATED,
            fallback_used=False,
            reason_code=arbitration.reason_code,
        )

    def _cold_start_default_decision(
        self,
        *,
        request: VetInputAssessmentRequestDto,
        settings: VetInputSafetyAssessorSettings,
        context: LightweightAssessmentContextDto,
    ) -> ResolvedProfileDecisionDto:
        """构建冷启动低置信保守默认裁决。

        :param request: 单个子任务输入安全评估请求。
        :param settings: 当前组件运行配置。
        :param context: 轻量消歧上下文。
        :return: 冷启动或记忆推动后的默认裁决。
        """

        if context.previous_generation_profile is VetGenerationProfile.EDUCATION:
            return self._education_decision(
                confidence=0.58,
                reason_code="memory_pushed_previous_education",
            ).model_copy(
                update={"disambiguation_method": DisambiguationMethod.MEMORY_PUSHED}
            )
        if settings.cold_start_default_executor == "education":
            return self._education_decision(
                confidence=0.55,
                reason_code="cold_start_default_education",
            ).model_copy(update={"fallback_used": True})
        if settings.cold_start_default_executor == "nonmedical_pet_care":
            return self._nonmedical_decision(
                intent=self._nonmedical_intent(request.task.task_type)
                if self._is_nonmedical_task(request.task.task_type)
                else VetIntent.GENERAL_QA,
                confidence=0.55,
                signals=[],
                reason_code="cold_start_default_nonmedical",
            ).model_copy(update={"fallback_used": True})
        return self._standard_decision(
            intent=self._standard_intent(request.task.task_type),
            executor_key=VetExecutorKey.STANDARD_CONSULTATION,
            confidence=0.55,
            method=AssessmentMethod.FALLBACK_DEFAULT,
            disambiguation_method=DisambiguationMethod.COLD_START_DOWNGRADE,
            reason_code="cold_start_default_standard",
        ).model_copy(update={"fallback_used": True})


__all__: tuple[str, ...] = ("VetProfileDecisionResolver",)
