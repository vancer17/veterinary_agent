"""
文件：src/vet_agent/orchestrator.py
作用：编排兽医 Agent 单回合主业务链路。
范围：负责安全评估、范围上下文读取、问诊状态、任务拆分、RAG、回复生成、记忆写入与 trace 写入。
说明：幂等 claim、响应重放与 turn lock 已迁移至 turn execution 门禁；本文件仅通过门禁协议提交主链路执行闭包。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

from vet_agent.agents import (
    ConsultationDecision,
    ConsultationSemanticExtractorAgent,
    ConsultationStatePolicyContext,
    ConsultationStateService,
    SafetyAssessment,
    TaskRouterAgent,
)
from vet_agent import Settings
from vet_agent import (
    AgentTurnRequest,
    AgentTurnResponse,
    Evidence,
    SafetySignal,
    StreamEvent,
    TrustedIdentity,
    VetSegment,
)
from vet_agent.answer_rag import AnswerRagError, AnswerRagRequest, AnswerRagResult, AnswerRagServiceProtocol
from vet_agent.background_tasks import BackgroundTaskServiceProtocol, make_memory_extraction_task_metadata
from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluator,
    ClinicalSafetyEvaluationResult,
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetySemanticResult,
)
from vet_agent.followup_rag import (
    FollowupRagDependencyError,
    FollowupRagPlan,
    FollowupRagRequest,
    FollowupRagServiceProtocol,
)
from vet_agent.input_safety import InputSafetyDecision, InputSafetyRequestContext, InputSafetyService
from vet_agent.memory import MemoryContextBuilder, MemoryPromptContext, MemoryReadBundle, MemoryReadService
from vet_agent.memory_extraction import MemoryExtractionStrategy
from vet_agent.observability import AgentPathNode, build_agent_path
from vet_agent.output_safety import OutputSafetyService
from vet_agent.rag_miss_governance import RagMissRecordRequest, RagMissRecorderProtocol, RagMissScope
from vet_agent.response_generation import (
    ResponseGenerationRequest,
    ResponseGenerationServiceProtocol,
)
from vet_agent.runtime import QwenClient
from vet_agent.services import (
    LogicTraceStore,
    MemoryService,
    PetContext,
    PetContextProvider,
    ReasoningDisplayBuilder,
    TurnExecutionGateProtocol,
)
from vet_agent.task_routing import (
    DEFAULT_TASK_KEY,
    ActiveTaskState,
    RoutedTask,
    TaskRoutingDecision,
    TaskRoutingRequestContext,
)


class VetOrchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        context_provider: PetContextProvider,
        memory_service: MemoryService,
        memory_read_service: MemoryReadService,
        memory_context_builder: MemoryContextBuilder,
        trace_store: LogicTraceStore,
        answer_rag_service: AnswerRagServiceProtocol,
        rag_miss_recorder: RagMissRecorderProtocol,
        response_generation_service: ResponseGenerationServiceProtocol,
        qwen_client: QwenClient,
        consultation_state_service: ConsultationStateService,
        clinical_safety_evaluator: ClinicalSafetyEvaluator,
        clinical_safety_semantic_extractor: ClinicalSafetySemanticExtractorAgent,
        turn_execution_gate: TurnExecutionGateProtocol,
        input_safety_service: InputSafetyService,
        output_safety_service: OutputSafetyService,
        task_router: TaskRouterAgent,
        followup_rag_service: FollowupRagServiceProtocol,
        background_task_service: BackgroundTaskServiceProtocol,
    ) -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :param context_provider: 参数 context_provider。
        :param memory_service: 参数 memory_service。
        :param memory_read_service: 结构化记忆读取服务。
        :param memory_context_builder: 记忆提示词上下文编译器。
        :param trace_store: 参数 trace_store。
        :param answer_rag_service: 回答相关 RAG 服务，仅在 OPA answer 分支生成结构化回答证据。
        :param rag_miss_recorder: RAG 无命中治理记录器，仅负责缺口留痕，不改变 Fail Fast 结果。
        :param response_generation_service: 回复生成上下文编译与模型调用服务。
        :param qwen_client: 参数 qwen_client。
        :param consultation_state_service: 问诊状态与回答充分性服务。
        :param clinical_safety_evaluator: 结构化临床安全评估器。
        :param clinical_safety_semantic_extractor: 临床安全结构化语义抽取器。
        :param turn_execution_gate: 单回合执行门禁，负责 turn lock 与幂等基础设施控制。
        :param input_safety_service: 基础输入安全候选与 OPA 策略裁决服务。
        :param output_safety_service: 输出安全候选与策略裁决服务。
        :param task_router: 结构化任务路由 Agent。
        :param followup_rag_service: 追问相关 RAG 服务，仅在 OPA ask 分支生成结构化追问计划。
        :param background_task_service: 可持久化后台任务入队服务。
        :return: 无返回值。
        """
        self.settings = settings
        self.context_provider = context_provider
        self.memory_service = memory_service
        self.memory_read_service = memory_read_service
        self.memory_context_builder = memory_context_builder
        self.trace_store = trace_store
        self.turn_execution_gate = turn_execution_gate
        self.input_safety_service = input_safety_service
        self.output_safety_service = output_safety_service
        self.answer_rag_service = answer_rag_service
        self.rag_miss_recorder = rag_miss_recorder
        self.response_generation_service = response_generation_service
        self.clinical_safety = clinical_safety_evaluator
        self.clinical_safety_semantic_extractor = clinical_safety_semantic_extractor
        self.semantic_extractor = ConsultationSemanticExtractorAgent(qwen_client, settings)
        self.consultation = consultation_state_service
        self.task_router = task_router
        self.followup_rag_service = followup_rag_service
        self.background_task_service = background_task_service
        self.reasoning_display = ReasoningDisplayBuilder()

    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResponse:
        """执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        async def execute_turn() -> AgentTurnResponse:
            """在 turn execution 门禁放行后执行 Agent 主业务链路。

            :return: 返回本轮新生成的 Agent 响应。
            """
            return await self._run_turn_core(request)

        return await self.turn_execution_gate.run(request, execute_turn)

    async def _run_turn_core(self, request: AgentTurnRequest) -> AgentTurnResponse:
        """执行 _run_turn_core 内部辅助逻辑。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        user_text = request.joined_text()
        model = request.model or self.settings.default_model
        input_safety_decision = await self.input_safety_service.evaluate(
            InputSafetyRequestContext.from_request(request)
        )

        if input_safety_decision.blocked or input_safety_decision.escalated:
            return await self._input_safety_response(
                request=request,
                decision=input_safety_decision,
                model=model,
                evidence=[],
                agent_path=build_agent_path(
                    AgentPathNode.INPUT_SAFETY_SERVICE,
                    AgentPathNode.INPUT_SAFETY_POLICY_OPA,
                ),
            )

        assessment = SafetyAssessment.from_signals(list(input_safety_decision.signals))

        pet_context = await self.context_provider.load(
            request.trusted_identity,
            request.scope_assertion,
            request.vet_context,
            request.metadata,
            authorized_scope_context=request.authorized_scope_context,
        )
        clinical_semantic = await self.clinical_safety_semantic_extractor.extract(
            user_text=user_text,
            pet_context_summary=pet_context.summary(),
            model=model,
        )
        clinical_safety_result = await self.clinical_safety.assess_with_resolution(
            user_text,
            request=request,
            semantic_result=clinical_semantic,
        )
        clinical_signals = clinical_safety_result.signals
        assessment = SafetyAssessment.merge(assessment, SafetyAssessment.from_signals(clinical_signals))
        if assessment.blocked or assessment.escalated:
            return await self._safety_triage_response(
                request=request,
                assessment=assessment,
                model=model,
                evidence=pet_context.evidence,
                agent_path=build_agent_path(
                    AgentPathNode.INPUT_SAFETY_SERVICE,
                    AgentPathNode.INPUT_SAFETY_POLICY_OPA,
                    AgentPathNode.PET_CONTEXT_AGENT,
                    AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT,
                    AgentPathNode.CLINICAL_SAFETY_EVALUATOR,
                    AgentPathNode.CLINICAL_SAFETY_POLICY_OPA,
                ),
                clinical_safety_semantic=clinical_semantic,
                clinical_safety_resolution=clinical_safety_result,
                input_safety_decision=input_safety_decision,
            )

        memory_bundle = await self.memory_read_service.read(
            request.trusted_identity,
            current_user_text=user_text,
            pet_context_summary=pet_context.summary(),
        )
        memory_context = self.memory_context_builder.build(memory_bundle)
        previous_state = memory_bundle.consultation_state
        task_states = dict(memory_bundle.task_consultation_states)
        active_tasks = self._active_task_states(previous_state, task_states)
        routing_decision = await self.task_router.route(
            context=self._task_routing_context(request),
            user_text=user_text,
            pet_context_summary=pet_context.summary(),
            active_tasks=active_tasks,
            model=model,
        )
        tasks = routing_decision.tasks
        if len(tasks) > 1:
            default_previous_state = (
                previous_state
                if self._has_unfinished_consultation_state(previous_state)
                else None
            )
            response = await self._run_multi_task_turn(
                request=request,
                tasks=tasks,
                routing_decision=routing_decision,
                default_previous_state=default_previous_state,
                task_states=task_states,
                pet_context=pet_context,
                memory_context=memory_context,
                memory_bundle=memory_bundle,
                assessment=assessment,
                model=model,
                clinical_safety_semantic=clinical_semantic,
                clinical_safety_resolution=clinical_safety_result,
                input_safety_decision=input_safety_decision,
            )
            response = await self._finalize_and_persist(request, response, medical=True)
            return response

        task = tasks[0]
        active_previous_state = self._state_for_task(
            task,
            default_state=previous_state,
            task_states=task_states,
        )

        semantic_result = await self.semantic_extractor.extract(
            user_text=task.text,
            pet_context_summary=pet_context.summary(),
            previous_state=active_previous_state,
            model=model,
        )
        consultation_policy_context = ConsultationStatePolicyContext.from_identity(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            user_id=request.trusted_identity.user_id,
            pet_id=request.trusted_identity.pet_id,
            session_id=request.trusted_identity.session_id,
        )
        consultation_decision = await self.consultation.update(
            active_previous_state,
            task.text,
            pet_context,
            policy_context=consultation_policy_context,
            task_domain=task.domain,
            semantic_result=semantic_result,
            clinical_safety_semantic=clinical_semantic,
            max_questions=request.turn_options.max_followup_questions,
        )

        if not consultation_decision.ready:
            followup_plan, knowledge_evidence, consultation_decision = await self._create_followup_rag_plan(
                request=request,
                task=task,
                user_text=user_text,
                pet_context=pet_context,
                consultation_decision=consultation_decision,
                model=model,
                max_questions=request.turn_options.max_followup_questions,
            )
            await self._save_single_task_state(
                request.trusted_identity,
                task=task,
                state=consultation_decision.state.to_dict(),
                task_states=task_states,
            )
            output_text = self.consultation.format_followup_response(
                consultation_decision,
                question_reasons=followup_plan.reason_lines(),
            )
            user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
            evidence = [*user_evidence, *pet_context.evidence, *knowledge_evidence]
            segment = VetSegment(
                type="followup_consultation",
                title="补充问诊信息",
                content=output_text,
                output_text=output_text,
                evidence=evidence,
            )
            reasoning_display = self.reasoning_display.build_turn_display(
                status="requires_followup",
                segment_id=segment.segment_id,
                evidence=evidence,
                consultation_state=consultation_decision.state.to_dict(),
                missing_slots=consultation_decision.missing_slots,
                safety_signals=assessment.signals,
            )
            segment.reasoning_display = reasoning_display
            segment.references = self.reasoning_display.references_from_evidence(evidence)
            response = AgentTurnResponse(
                id=f"turn_{uuid4().hex}",
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                model=model,
                status="requires_followup",
                output_text=output_text,
                segments=[segment],
                reasoning_display=reasoning_display,
                vet_result={
                    "generation_profile": "rag_followup",
                    "route": "rag_guided_followup",
                    "audit_tier": "A",
                },
                safety_signals=assessment.signals,
                evidence=evidence,
                metadata={
                    "multi_agent_path": build_agent_path(
                        AgentPathNode.INPUT_SAFETY_SERVICE,
                        AgentPathNode.INPUT_SAFETY_POLICY_OPA,
                        AgentPathNode.PET_CONTEXT_AGENT,
                        AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT,
                        AgentPathNode.CLINICAL_SAFETY_EVALUATOR,
                        AgentPathNode.CLINICAL_SAFETY_POLICY_OPA,
                        AgentPathNode.MEMORY_AGENT,
                        AgentPathNode.TASK_ROUTER_AGENT,
                        AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT,
                        AgentPathNode.CONSULTATION_STATE_SERVICE,
                        AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA,
                        AgentPathNode.FOLLOWUP_RAG_SERVICE,
                        AgentPathNode.FOLLOWUP_RAG_RETRIEVER,
                        AgentPathNode.FOLLOWUP_RAG_PLANNER,
                    ),
                    "consultation_phase": consultation_decision.state.phase,
                    "consultation_state": consultation_decision.state.to_dict(),
                    "missing_slots": consultation_decision.missing_slots,
                    "answerability": consultation_decision.answerability,
                    "semantic_extraction": consultation_decision.state.semantic_extraction,
                    **self._clinical_safety_metadata(
                        clinical_safety_semantic=clinical_semantic,
                        clinical_safety_resolution=clinical_safety_result,
                    ),
                    "input_safety_decision": input_safety_decision.to_metadata(),
                    "followup_question_plan": followup_plan.to_metadata(),
                    "memory_read": memory_bundle.to_metadata(),
                    "memory_context": memory_context.metadata,
                    "task_router": routing_decision.to_metadata(),
                },
            )
            return await self._finalize_and_persist(request, response, medical=True)

        answer_rag_result, answer_rag_evidence = await self._create_answer_rag_context(
            request=request,
            task=task,
            pet_context=pet_context,
            consultation_decision=consultation_decision,
            model=model,
        )

        response_generation_result = await self.response_generation_service.generate(
            ResponseGenerationRequest(
                task=task,
                pet_context=pet_context,
                memory_context=memory_context,
                consultation_decision=consultation_decision,
                answer_rag_result=answer_rag_result,
                clinical_safety_semantic=clinical_semantic,
                clinical_safety_resolution=clinical_safety_result,
            ),
            model=model,
        )
        output_text = response_generation_result.text
        user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
        evidence = [*user_evidence, *response_generation_result.evidence, *answer_rag_evidence]
        segment = VetSegment(
            type="medical_consultation",
            title="症状判断与下一步",
            content=output_text,
            output_text=output_text,
            evidence=evidence,
        )
        reasoning_display = self.reasoning_display.build_turn_display(
            status="completed",
            segment_id=segment.segment_id,
            evidence=evidence,
            consultation_state=consultation_decision.state.to_dict(),
            missing_slots=consultation_decision.missing_slots,
            safety_signals=assessment.signals,
        )
        segment.reasoning_display = reasoning_display
        segment.references = self.reasoning_display.references_from_evidence(evidence)
        response = AgentTurnResponse(
            id=f"turn_{uuid4().hex}",
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            model=model,
            status="completed",
            output_text=output_text,
            segments=[segment],
            reasoning_display=reasoning_display,
            vet_result={
                "generation_profile": "standard",
                "route": "standard_consultation",
                "audit_tier": "A",
            },
            safety_signals=assessment.signals,
            evidence=evidence,
            metadata={
                "multi_agent_path": build_agent_path(
                    AgentPathNode.INPUT_SAFETY_SERVICE,
                    AgentPathNode.INPUT_SAFETY_POLICY_OPA,
                    AgentPathNode.PET_CONTEXT_AGENT,
                    AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT,
                    AgentPathNode.CLINICAL_SAFETY_EVALUATOR,
                    AgentPathNode.CLINICAL_SAFETY_POLICY_OPA,
                    AgentPathNode.MEMORY_AGENT,
                    AgentPathNode.TASK_ROUTER_AGENT,
                    AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT,
                    AgentPathNode.CONSULTATION_STATE_SERVICE,
                    AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA,
                    AgentPathNode.ANSWER_RAG_SERVICE,
                        AgentPathNode.ANSWER_RAG_RETRIEVER,
                        AgentPathNode.RESPONSE_GENERATION_CONTEXT_BUILDER,
                        AgentPathNode.QWEN_RESPONSE_AGENT,
                    ),
                "litellm_configured": self.settings.litellm_configured,
                "consultation_phase": consultation_decision.state.phase,
                "consultation_state": consultation_decision.state.to_dict(),
                "missing_slots": consultation_decision.missing_slots,
                "answerability": consultation_decision.answerability,
                "semantic_extraction": consultation_decision.state.semantic_extraction,
                **self._clinical_safety_metadata(
                    clinical_safety_semantic=clinical_semantic,
                    clinical_safety_resolution=clinical_safety_result,
                ),
                "input_safety_decision": input_safety_decision.to_metadata(),
                "memory_read": memory_bundle.to_metadata(),
                "memory_context": memory_context.metadata,
                "task_router": routing_decision.to_metadata(),
                "answer_rag": answer_rag_result.to_metadata(),
                "response_generation": response_generation_result.to_metadata(),
            },
        )
        response = await self._finalize_and_persist(request, response, medical=True)
        await self._persist_single_task_state(
            request.trusted_identity,
            task=task,
            state=None,
            task_states=task_states,
        )
        return response

    async def _safety_triage_response(
        self,
        *,
        request: AgentTurnRequest,
        assessment: SafetyAssessment,
        model: str,
        evidence: list[Evidence],
        agent_path: list[str],
        clinical_safety_semantic: ClinicalSafetySemanticResult | None = None,
        clinical_safety_resolution: ClinicalSafetyEvaluationResult | None = None,
        input_safety_decision: InputSafetyDecision | None = None,
    ) -> AgentTurnResponse:
        """根据安全评估构造并持久化安全分诊响应。

        :param request: 当前回合请求对象。
        :param assessment: 已完成的安全评估结果。
        :param model: 当前回合使用的模型名称。
        :param evidence: 可公开展示的上下文证据列表。
        :param agent_path: 当前安全分诊链路中参与的 Agent 名称。
        :param clinical_safety_semantic: 临床安全结构化语义结果。
        :param clinical_safety_resolution: 临床安全裁决和显式回退结果。
        :param input_safety_decision: 已完成的基础输入安全策略裁决；为空时不写入审计字段。
        :return: 返回已持久化的安全分诊响应。
        """
        text = self._safety_triage_response_text(assessment)
        signals = assessment.signals
        segment = VetSegment(
            type="safety_triage",
            title="安全分诊",
            content=text,
            output_text=text,
            evidence=evidence,
        )
        reasoning_display = self.reasoning_display.build_turn_display(
            status="blocked" if assessment.blocked else "safety_escalated",
            segment_id=segment.segment_id,
            evidence=evidence,
            safety_signals=signals,
        )
        segment.reasoning_display = reasoning_display
        segment.references = self.reasoning_display.references_from_evidence(evidence)
        response = AgentTurnResponse(
            id=f"turn_{uuid4().hex}",
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            model=model,
            status="blocked" if assessment.blocked else "safety_escalated",
            output_text=text,
            segments=[segment],
            reasoning_display=reasoning_display,
            vet_result={
                "generation_profile": "safety",
                "route": "safety_triage",
                "audit_tier": "A",
            },
            safety_signals=signals,
            evidence=evidence,
            metadata={
                "multi_agent_path": agent_path,
                **self._clinical_safety_metadata(
                    clinical_safety_semantic=clinical_safety_semantic,
                    clinical_safety_resolution=clinical_safety_resolution,
                ),
                **self._input_safety_metadata(input_safety_decision),
            },
        )
        response = await self._finalize_and_persist(request, response, medical=True)
        await self.memory_service.clear_consultation_state(request.trusted_identity)
        return response

    def _safety_triage_response_text(self, assessment: SafetyAssessment) -> str:
        """根据已裁决安全信号生成临床安全分诊响应文本。

        :param assessment: 已完成的安全评估结果。
        :return: 返回面向用户的安全分诊文本。
        """
        primary_signal = self._primary_triage_signal(assessment.signals)
        if primary_signal is not None:
            reasons = primary_signal.message
            matched = "、".join(dict.fromkeys(term for term in primary_signal.matched_terms if term))
            prefix = "你描述里有需要优先线下处理的高风险信号"
            if reasons:
                prefix = f"{prefix}：{reasons}"
            if matched:
                prefix = f"{prefix}，相关线索: {matched}"
            return (
                f"{prefix}。请尽快联系线下兽医医院，若症状正在发生或持续加重，请按急诊处理。\n\n"
                "路上尽量保持宠物安静和保暖，不要自行喂人药或给不确定的药物。"
            )
        return "当前信息需要进一步确认。"

    def _primary_triage_signal(self, signals: list[SafetySignal]) -> SafetySignal | None:
        """选择面向用户展示的主临床安全分诊信号。

        :param signals: 已由安全策略裁决后的全部安全信号。
        :return: 返回最高优先级的 urgent 或 blocked 信号；没有升级信号时返回 None。
        """
        priority = {"blocked": 2, "urgent": 1}
        triage_signals = [signal for signal in signals if signal.severity in priority]
        if not triage_signals:
            return None
        return sorted(triage_signals, key=lambda item: priority[item.severity], reverse=True)[0]

    async def _input_safety_response(
        self,
        *,
        request: AgentTurnRequest,
        decision: InputSafetyDecision,
        model: str,
        evidence: list[Evidence],
        agent_path: list[str],
    ) -> AgentTurnResponse:
        """根据基础输入安全策略裁决构造并持久化安全响应。

        :param request: 当前回合请求对象。
        :param decision: 基础输入安全策略裁决结果。
        :param model: 当前回合使用的模型名称。
        :param evidence: 可公开展示的上下文证据列表。
        :param agent_path: 当前输入安全链路中参与的组件名称。
        :return: 返回已持久化的输入安全响应。
        """
        status = "blocked" if decision.blocked else "safety_escalated"
        text = self._input_safety_response_text(decision)
        signals = list(decision.signals)
        segment = VetSegment(
            type="input_safety",
            title="输入安全",
            status=status,
            content=text,
            output_text=text,
            evidence=evidence,
        )
        reasoning_display = self.reasoning_display.build_turn_display(
            status=status,
            segment_id=segment.segment_id,
            evidence=evidence,
            safety_signals=signals,
        )
        segment.reasoning_display = reasoning_display
        segment.references = self.reasoning_display.references_from_evidence(evidence)
        response = AgentTurnResponse(
            id=f"turn_{uuid4().hex}",
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            model=model,
            status=status,
            output_text=text,
            segments=[segment],
            reasoning_display=reasoning_display,
            vet_result={
                "generation_profile": "input_safety",
                "route": "input_safety_policy",
                "audit_tier": "A",
            },
            safety_signals=signals,
            evidence=evidence,
            metadata={
                "multi_agent_path": agent_path,
                "input_safety_decision": decision.to_metadata(),
            },
        )
        response = await self.output_safety_service.review_response(response)
        response.metadata["memory_extraction"] = {
            "agent": "MemoryExtractionAgent",
            "stored_fact_count": 0,
            "fact_keys": [],
            "skipped_reason": "input_safety_policy_stopped_main_chain",
            "task_id": None,
            "task_status": "skipped",
            "task": None,
        }
        await self._persist(request, response, medical=False)
        await self.memory_service.clear_consultation_state(request.trusted_identity)
        return response

    def _input_safety_response_text(self, decision: InputSafetyDecision) -> str:
        """生成基础输入安全响应文本。

        :param decision: 基础输入安全策略裁决结果。
        :return: 返回面向用户的安全响应文本。
        """
        message = decision.message.strip() or "当前输入未通过基础安全策略裁决。"
        if decision.blocked:
            return f"{message}\n\n请调整问题或补充合规的文本、附件用途后重新提交。"
        return f"{message}\n\n如需继续，请补充与宠物健康咨询直接相关的必要信息。"

    def _input_safety_metadata(self, decision: InputSafetyDecision | None) -> dict[str, Any]:
        """构造基础输入安全策略裁决 metadata。

        :param decision: 基础输入安全策略裁决结果。
        :return: 返回可合并到 Agent 响应 metadata 的输入安全审计字段。
        """
        if decision is None:
            return {}
        return {"input_safety_decision": decision.to_metadata()}

    async def _run_multi_task_turn(
        self,
        *,
        request: AgentTurnRequest,
        tasks: tuple[RoutedTask, ...],
        routing_decision: TaskRoutingDecision,
        default_previous_state: dict[str, Any] | None,
        task_states: dict[str, Any],
        pet_context: PetContext,
        memory_context: MemoryPromptContext,
        memory_bundle: MemoryReadBundle,
        assessment: SafetyAssessment,
        model: str,
        clinical_safety_semantic: ClinicalSafetySemanticResult,
        clinical_safety_resolution: ClinicalSafetyEvaluationResult,
        input_safety_decision: InputSafetyDecision,
    ) -> AgentTurnResponse:
        """执行 _run_multi_task_turn 内部辅助逻辑。

        :param request: 请求对象。
        :param tasks: 任务列表。
        :param routing_decision: 已通过结构化契约和 OPA 准入的任务路由决策。
        :param default_previous_state: 当前 session 默认任务的未完成问诊状态。
        :param task_states: 当前 session 已持久化的非默认任务状态。
        :param pet_context: 宠物上下文。
        :param memory_context: 已分层编译的记忆提示词上下文。
        :param memory_bundle: 结构化记忆读取结果。
        :param assessment: 参数 assessment。
        :param model: 模型名称。
        :param clinical_safety_semantic: 临床安全结构化语义结果。
        :param clinical_safety_resolution: 临床安全显式回退结果。
        :param input_safety_decision: 基础输入安全策略裁决结果。
        :return: 返回函数执行结果。
        """
        updated_task_states = {
            task_key: state
            for task_key, state in task_states.items()
            if self._has_unfinished_consultation_state(state)
        }
        segments: list[VetSegment] = []
        all_evidence: list[Evidence] = []
        all_safety_signals = list(assessment.signals)
        task_summaries: list[dict[str, Any]] = []
        used_answer_rag_service = False
        used_followup_rag_service = False
        used_response_generation = False

        for index, task in enumerate(tasks, start=1):
            task_previous_state = self._state_for_task(
                task,
                default_state=default_previous_state,
                task_states=task_states,
            )
            semantic_result = await self.semantic_extractor.extract(
                user_text=task.text,
                pet_context_summary=pet_context.summary(),
                previous_state=task_previous_state,
                model=model,
            )
            consultation_policy_context = ConsultationStatePolicyContext.from_identity(
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                user_id=request.trusted_identity.user_id,
                pet_id=request.trusted_identity.pet_id,
                session_id=request.trusted_identity.session_id,
            )
            consultation_decision = await self.consultation.update(
                task_previous_state,
                task.text,
                pet_context,
                policy_context=consultation_policy_context,
                task_domain=task.domain,
                semantic_result=semantic_result,
                clinical_safety_semantic=clinical_safety_semantic,
                max_questions=request.turn_options.max_followup_questions,
            )
            user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
            followup_plan: FollowupRagPlan | None = None
            answer_rag_result: AnswerRagResult | None = None
            response_generation_metadata: dict[str, Any] | None = None

            if consultation_decision.ready:
                answer_rag_result, answer_rag_evidence = await self._create_answer_rag_context(
                    request=request,
                    task=task,
                    pet_context=pet_context,
                    consultation_decision=consultation_decision,
                    model=model,
                )
                response_generation_result = await self.response_generation_service.generate(
                    ResponseGenerationRequest(
                        task=task,
                        pet_context=pet_context,
                        memory_context=memory_context,
                        consultation_decision=consultation_decision,
                        answer_rag_result=answer_rag_result,
                        clinical_safety_semantic=clinical_safety_semantic,
                        clinical_safety_resolution=clinical_safety_resolution,
                    ),
                    model=model,
                )
                output_text = response_generation_result.text
                used_answer_rag_service = True
                used_response_generation = True
                response_generation_metadata = response_generation_result.to_metadata()
                segment_status = "completed"
                segment_type = "medical_consultation"
                evidence = [
                    *user_evidence,
                    *response_generation_result.evidence,
                    *answer_rag_evidence,
                ]
            else:
                followup_plan, knowledge_evidence, consultation_decision = await self._create_followup_rag_plan(
                    request=request,
                    task=task,
                    user_text=task.text,
                    pet_context=pet_context,
                    consultation_decision=consultation_decision,
                    model=model,
                    max_questions=request.turn_options.max_followup_questions,
                )
                used_followup_rag_service = True
                output_text = self.consultation.format_followup_response(
                    consultation_decision,
                    question_reasons=followup_plan.reason_lines(),
                )
                segment_status = "requires_followup"
                segment_type = "followup_consultation"
                evidence = [*user_evidence, *pet_context.evidence, *knowledge_evidence]

            if consultation_decision.ready:
                updated_task_states.pop(task.state_key, None)
            else:
                updated_task_states[task.state_key] = consultation_decision.state.to_dict()

            all_evidence.extend(evidence)

            segment = VetSegment(
                type=segment_type,
                title=f"任务 {index}: {task.title}",
                content=output_text,
                output_text=output_text,
                status=segment_status,
                evidence=evidence,
            )
            reasoning_display = self.reasoning_display.build_turn_display(
                status=segment_status,
                segment_id=segment.segment_id,
                evidence=evidence,
                consultation_state=consultation_decision.state.to_dict(),
                missing_slots=consultation_decision.missing_slots,
                safety_signals=assessment.signals,
            )
            segment.reasoning_display = reasoning_display
            segment.references = self.reasoning_display.references_from_evidence(evidence)
            segments.append(segment)
            task_summaries.append(
                {
                    "task_id": task.task_id,
                    "task_key": task.task_key,
                    "state_key": task.state_key,
                    "title": task.title,
                    "domain": task.domain,
                    "text": task.text,
                    "status": segment_status,
                    "missing_slots": consultation_decision.missing_slots,
                    "consultation_phase": consultation_decision.state.phase,
                    "consultation_state": consultation_decision.state.to_dict(),
                    "answerability": consultation_decision.answerability,
                    "semantic_extraction": consultation_decision.state.semantic_extraction,
                    "followup_question_plan": followup_plan.to_metadata() if followup_plan else None,
                    "answer_rag": answer_rag_result.to_metadata() if answer_rag_result else None,
                    "response_generation": response_generation_metadata,
                }
            )

        await self.memory_service.save_task_consultation_states(
            request.trusted_identity,
            updated_task_states,
            clear_default_state=self._should_clear_default_state_after_multi_task(tasks),
        )

        status = "requires_followup" if any(item["status"] == "requires_followup" for item in task_summaries) else "completed"
        output_text = "\n\n".join(f"{segment.title}\n{segment.output_text or segment.content}" for segment in segments)
        turn_reasoning_display = self.reasoning_display.build_multi_task_display(
            task_summaries=task_summaries,
            evidence=all_evidence,
            status=status,
        )
        return AgentTurnResponse(
            id=f"turn_{uuid4().hex}",
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            model=model,
            status=status,
            output_text=output_text,
            segments=segments,
            reasoning_display=turn_reasoning_display,
            vet_result={
                "generation_profile": "multi_task",
                "route": "multi_task_consultation",
                "audit_tier": "A",
                "task_count": len(tasks),
            },
            safety_signals=all_safety_signals,
            evidence=all_evidence,
            metadata={
                "multi_agent_path": [
                    *build_agent_path(
                        AgentPathNode.INPUT_SAFETY_SERVICE,
                        AgentPathNode.INPUT_SAFETY_POLICY_OPA,
                        AgentPathNode.PET_CONTEXT_AGENT,
                        AgentPathNode.CLINICAL_SAFETY_SEMANTIC_EXTRACTOR_AGENT,
                        AgentPathNode.CLINICAL_SAFETY_EVALUATOR,
                        AgentPathNode.CLINICAL_SAFETY_POLICY_OPA,
                        AgentPathNode.MEMORY_AGENT,
                        AgentPathNode.TASK_ROUTER_AGENT,
                        AgentPathNode.CONSULTATION_SEMANTIC_EXTRACTOR_AGENT,
                        AgentPathNode.CONSULTATION_STATE_SERVICE,
                        AgentPathNode.CONSULTATION_ANSWERABILITY_POLICY_OPA,
                    ),
                    *(
                        build_agent_path(
                            AgentPathNode.ANSWER_RAG_SERVICE,
                            AgentPathNode.ANSWER_RAG_RETRIEVER,
                        )
                        if used_answer_rag_service
                        else []
                    ),
                    *(
                        build_agent_path(
                            AgentPathNode.FOLLOWUP_RAG_SERVICE,
                            AgentPathNode.FOLLOWUP_RAG_RETRIEVER,
                            AgentPathNode.FOLLOWUP_RAG_PLANNER,
                        )
                        if used_followup_rag_service
                        else []
                    ),
                    *(
                        build_agent_path(
                            AgentPathNode.RESPONSE_GENERATION_CONTEXT_BUILDER,
                            AgentPathNode.QWEN_RESPONSE_AGENT,
                        )
                        if used_response_generation
                        else []
                    ),
                ],
                "task_count": len(tasks),
                "task_router": routing_decision.to_metadata(),
                **self._clinical_safety_metadata(
                    clinical_safety_semantic=clinical_safety_semantic,
                    clinical_safety_resolution=clinical_safety_resolution,
                ),
                "input_safety_decision": input_safety_decision.to_metadata(),
                "memory_read": memory_bundle.to_metadata(),
                "memory_context": memory_context.metadata,
                "tasks": task_summaries,
                "consultation_states": updated_task_states,
                "litellm_configured": self.settings.litellm_configured,
            },
        )

    def _active_task_states(
        self,
        default_state: dict[str, Any] | None,
        task_states: dict[str, Any],
    ) -> tuple[ActiveTaskState, ...]:
        """构造任务路由器使用的当前 session 活跃任务摘要。

        :param default_state: 当前 session 默认任务状态。
        :param task_states: 当前 session 非默认任务状态集合。
        :return: 返回按稳定任务键排序的活跃任务摘要元组。
        """
        active: list[ActiveTaskState] = []
        if self._has_unfinished_consultation_state(default_state):
            active.append(ActiveTaskState.from_state(DEFAULT_TASK_KEY, default_state or {}))
        for task_key, state in sorted(task_states.items()):
            if self._has_unfinished_consultation_state(state):
                active.append(ActiveTaskState.from_state(task_key, state))
        return tuple(active)

    def _state_for_task(
        self,
        task: RoutedTask,
        *,
        default_state: dict[str, Any] | None,
        task_states: dict[str, Any],
    ) -> dict[str, Any] | None:
        """读取任务执行所需的未完成问诊状态。

        :param task: 已通过任务路由准入的任务。
        :param default_state: 当前 session 默认任务状态。
        :param task_states: 当前 session 非默认任务状态集合。
        :return: 返回与当前任务关联的未完成状态；不存在或已完成时返回 None。
        """
        if task.existing_task_key == DEFAULT_TASK_KEY or task.state_key == DEFAULT_TASK_KEY:
            candidate = default_state
        else:
            candidate = task_states.get(task.state_key)
        return candidate if self._has_unfinished_consultation_state(candidate) else None

    def _task_routing_context(self, request: AgentTurnRequest) -> TaskRoutingRequestContext:
        """从核心请求构造任务路由策略所需的可信范围摘要。

        :param request: 当前 Agent 回合请求。
        :return: 返回不包含用户原始文本的任务路由策略上下文。
        """
        identity = request.trusted_identity
        return TaskRoutingRequestContext(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
        )

    async def _save_single_task_state(
        self,
        identity: TrustedIdentity,
        *,
        task: RoutedTask,
        state: dict[str, Any],
        task_states: dict[str, Any],
    ) -> None:
        """保存单任务问诊状态并保持默认与多任务状态边界。

        :param identity: 当前可信身份范围。
        :param task: 当前已执行任务。
        :param state: 当前任务新的问诊状态。
        :param task_states: 当前 session 非默认任务状态集合。
        :return: 无返回值。
        """
        if task.state_key == DEFAULT_TASK_KEY:
            await self.memory_service.save_consultation_state(identity, state)
            return
        await self.memory_service.save_task_consultation_states(
            identity,
            {**task_states, task.state_key: state},
        )

    async def _persist_single_task_state(
        self,
        identity: TrustedIdentity,
        *,
        task: RoutedTask,
        state: dict[str, Any] | None,
        task_states: dict[str, Any],
    ) -> None:
        """保存或清理单任务完成后的问诊状态。

        :param identity: 当前可信身份范围。
        :param task: 当前已执行任务。
        :param state: 未完成时应保存的状态；完成时传入 None。
        :param task_states: 当前 session 非默认任务状态集合。
        :return: 无返回值。
        """
        if state is not None:
            await self._save_single_task_state(
                identity,
                task=task,
                state=state,
                task_states=task_states,
            )
            return
        if task.state_key == DEFAULT_TASK_KEY:
            await self.memory_service.clear_default_consultation_state(identity)
            return
        remaining = dict(task_states)
        remaining.pop(task.state_key, None)
        await self.memory_service.save_task_consultation_states(identity, remaining)

    def _should_clear_default_state_after_multi_task(self, tasks: tuple[RoutedTask, ...]) -> bool:
        """判断多任务执行后是否需要清理默认问诊状态。

        :param tasks: 已通过任务路由策略准入的本轮任务集合。
        :return: 当本轮明确把 __default__ 活跃状态迁移为具体任务键时返回 True。
        """
        return any(
            task.existing_task_key == DEFAULT_TASK_KEY and task.state_key != DEFAULT_TASK_KEY
            for task in tasks
        )

    def _clinical_safety_metadata(
        self,
        *,
        clinical_safety_semantic: ClinicalSafetySemanticResult | None = None,
        clinical_safety_resolution: ClinicalSafetyEvaluationResult | None = None,
    ) -> dict[str, Any]:
        """构造临床安全链路的显式回退 metadata。

        :param clinical_safety_semantic: 临床安全结构化语义结果。
        :param clinical_safety_resolution: 临床安全裁决结果。
        :return: 返回临床安全元数据字典。
        """
        metadata: dict[str, Any] = {}
        if clinical_safety_semantic is not None:
            metadata["clinical_safety_semantic"] = clinical_safety_semantic.to_metadata()
        if clinical_safety_resolution is not None:
            metadata["clinical_safety_resolution"] = clinical_safety_resolution.to_metadata()
        return metadata

    def _has_unfinished_consultation_state(self, state: dict[str, Any] | None) -> bool:
        """判断问诊状态是否仍需要后续回合补充信息。

        :param state: 已持久化的问诊状态。
        :return: 状态存在且处于收集信息阶段时返回 True。
        """
        if not isinstance(state, dict) or not state:
            return False
        phase = str(state.get("phase") or "").strip()
        if phase == "ready_to_answer":
            return False
        has_consultation_trace = any(
            [
                state.get("chief_complaint"),
                state.get("asked_questions"),
                state.get("followup_rounds"),
                state.get("slots"),
            ]
        )
        return bool(has_consultation_trace and phase in {"", "collecting_info"})

    async def _create_answer_rag_context(
        self,
        *,
        request: AgentTurnRequest,
        task: RoutedTask,
        pet_context: PetContext,
        consultation_decision: ConsultationDecision,
        model: str,
    ) -> tuple[AnswerRagResult, list[Evidence]]:
        """基于回答相关 RAG 生成回答证据上下文。

        :param request: 当前 Agent 回合请求。
        :param task: 当前已通过任务路由和回答充分性裁决的任务。
        :param pet_context: 宠物上下文。
        :param consultation_decision: 当前问诊决策。
        :param model: 模型名称；保留于调用链审计边界。
        :return: 返回回答 RAG 结果与可进入响应的证据列表。
        """
        del model
        answer_request = AnswerRagRequest(
            user_text=task.text,
            pet_context_summary=pet_context.summary(),
            consultation_state=consultation_decision.state.to_dict(),
            answerability=consultation_decision.answerability,
            semantic_extraction=consultation_decision.state.semantic_extraction,
            task_domain=task.domain,
        )
        try:
            result = await self.answer_rag_service.retrieve(answer_request)
        except AnswerRagError as exc:
            try:
                await self._record_answer_rag_miss(
                    request=request,
                    task=task,
                    answer_request=answer_request,
                    error=exc,
                )
            except Exception as record_exc:
                exc.details["rag_miss_recording_failed"] = {
                    "error_type": type(record_exc).__name__,
                }
            raise
        return result, result.to_evidence()

    async def _record_answer_rag_miss(
        self,
        *,
        request: AgentTurnRequest,
        task: RoutedTask,
        answer_request: AnswerRagRequest,
        error: AnswerRagError,
    ) -> None:
        """记录回答 RAG 无命中或依赖失败产生的知识缺口治理事件。

        :param request: 当前 Agent 回合请求。
        :param task: 当前已通过任务路由和回答充分性裁决的任务。
        :param answer_request: 回答 RAG 结构化请求。
        :param error: 回答 RAG 原始异常。
        :return: 无返回值；记录完成后由调用方继续抛出原异常。
        """
        details = dict(getattr(error, "details", {}) or {})
        failure_reason = str(details.get("reason") or "answer_rag_failed")
        await self.rag_miss_recorder.record_miss(
            RagMissRecordRequest(
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                user_id=request.trusted_identity.user_id,
                pet_id=request.trusted_identity.pet_id,
                session_id=request.trusted_identity.session_id,
                rag_scope=RagMissScope.ANSWER_RAG,
                task_id=task.task_id,
                task_key=task.task_key,
                task_domain=task.domain,
                task_title=task.title,
                user_text=task.text,
                structured_query=str(details.get("query") or ""),
                consultation_state=dict(answer_request.consultation_state),
                answerability=dict(answer_request.answerability),
                semantic_extraction=dict(answer_request.semantic_extraction),
                allowed_chunk_types=tuple(str(item) for item in details.get("allowed_chunk_types") or ()),
                top_k=int(details.get("top_k") or 0),
                min_score=float(details.get("min_score") or 0.0),
                domain_filter=str(details.get("domain") or "") or None,
                failure_reason=failure_reason,
                error_type=type(error).__name__,
                error_message=str(error),
                error_details=details,
                metadata={
                    "agent_path_node": AgentPathNode.ANSWER_RAG_SERVICE.value,
                    "task_state_key": task.state_key,
                },
            )
        )

    async def _create_followup_rag_plan(
        self,
        *,
        request: AgentTurnRequest,
        task: RoutedTask,
        user_text: str,
        pet_context: PetContext,
        consultation_decision: ConsultationDecision,
        model: str,
        max_questions: int,
    ) -> tuple[FollowupRagPlan, list[Evidence], ConsultationDecision]:
        """基于追问相关 RAG 生成下一轮追问计划并写回问诊状态。

        :param request: 当前 Agent 回合请求。
        :param task: 当前已通过任务路由和回答充分性裁决的任务。
        :param user_text: 用户本轮输入文本。
        :param pet_context: 宠物上下文。
        :param consultation_decision: 当前问诊决策。
        :param model: 模型名称。
        :param max_questions: 最多追问数量。
        :return: 返回追问规划、知识库证据列表与更新后的问诊决策。
        """
        followup_request = FollowupRagRequest(
            user_text=user_text,
            pet_context_summary=pet_context.summary(),
            consultation_state=consultation_decision.state.to_dict(),
            missing_slots=consultation_decision.missing_slots,
            answerability=consultation_decision.answerability,
            model=model,
            max_questions=max_questions,
        )
        try:
            plan = await self.followup_rag_service.plan(followup_request)
        except FollowupRagDependencyError as exc:
            if self._should_record_followup_rag_miss(exc):
                try:
                    await self._record_followup_rag_miss(
                        request=request,
                        task=task,
                        followup_request=followup_request,
                        error=exc,
                    )
                except Exception as record_exc:
                    exc.details["rag_miss_recording_failed"] = {
                        "error_type": type(record_exc).__name__,
                    }
            raise
        planned_questions = plan.question_texts()
        consultation_decision.state.asked_questions.extend(planned_questions)
        consultation_decision.state.followup_rounds += 1
        updated_decision = ConsultationDecision(
            state=consultation_decision.state,
            ready=consultation_decision.ready,
            missing_slots=consultation_decision.missing_slots,
            questions=planned_questions,
            answerability=consultation_decision.answerability,
        )
        return plan, plan.to_evidence(), updated_decision

    async def _record_followup_rag_miss(
        self,
        *,
        request: AgentTurnRequest,
        task: RoutedTask,
        followup_request: FollowupRagRequest,
        error: FollowupRagDependencyError,
    ) -> None:
        """记录追问相关 RAG 无命中治理事件。

        :param request: 当前 Agent 回合请求。
        :param task: 当前已通过任务路由和回答充分性裁决的任务。
        :param followup_request: 追问 RAG 结构化请求。
        :param error: 追问 RAG 依赖异常。
        :return: 无返回值；记录完成后由调用方继续抛出原异常。
        """
        details = dict(getattr(error, "details", {}) or {})
        failure_reason = str(details.get("reason") or "followup_rag_failed")
        consultation_state = dict(followup_request.consultation_state or {})
        await self.rag_miss_recorder.record_miss(
            RagMissRecordRequest(
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                user_id=request.trusted_identity.user_id,
                pet_id=request.trusted_identity.pet_id,
                session_id=request.trusted_identity.session_id,
                rag_scope=RagMissScope.FOLLOWUP_RAG,
                task_id=task.task_id,
                task_key=task.task_key,
                task_domain=task.domain,
                task_title=task.title,
                user_text=task.text,
                structured_query=str(details.get("query") or ""),
                consultation_state=consultation_state,
                answerability=dict(followup_request.answerability),
                semantic_extraction=dict(consultation_state.get("semantic_extraction") or {}),
                allowed_chunk_types=tuple(str(item) for item in details.get("allowed_chunk_types") or ()),
                top_k=int(details.get("top_k") or 0),
                min_score=float(details.get("min_score") or 0.0),
                domain_filter=str(details.get("domain") or consultation_state.get("domain") or "") or None,
                failure_reason=failure_reason,
                error_type=type(error).__name__,
                error_message=str(error),
                error_details=details,
                metadata={
                    "agent_path_node": AgentPathNode.FOLLOWUP_RAG_SERVICE.value,
                    "task_state_key": task.state_key,
                    "missing_slots": list(followup_request.missing_slots),
                    "planner_called": False,
                    "runtime_action": "fail_fast",
                },
            )
        )

    def _should_record_followup_rag_miss(self, error: FollowupRagDependencyError) -> bool:
        """判断追问相关 RAG 依赖异常是否应进入治理留痕。

        :param error: 追问 RAG 依赖异常。
        :return: 仅当异常原因表示追问知识无合格命中时返回 True。
        """
        details = dict(getattr(error, "details", {}) or {})
        return str(details.get("reason") or "") == "no_approved_vector_hits"

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[str]:
        """以流式事件形式执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回异步执行结果。
        """
        response = await self.run_turn(request)
        yield StreamEvent(
            event="turn.started",
            data={
                "id": response.id,
                "request_id": request.request_context.request_id,
                "trace_id": request.request_context.trace_id,
            },
        ).to_sse()
        if response.reasoning_display and response.reasoning_display.segment_id is None:
            reasoning = response.reasoning_display
            yield StreamEvent(
                event="reasoning_display.started",
                data={
                    "projection_id": reasoning.projection_id,
                    "segment_id": reasoning.segment_id,
                    "title": reasoning.title,
                },
            ).to_sse()
            for chunk in self._chunks(reasoning.text, size=64):
                yield StreamEvent(
                    event="reasoning_display.delta",
                    data={"projection_id": reasoning.projection_id, "text_delta": chunk},
                ).to_sse()
                await asyncio.sleep(0)
            yield StreamEvent(
                event="reasoning_display.completed",
                data={"reasoning_display": reasoning.model_dump(mode="json")},
            ).to_sse()
        for index, segment in enumerate(response.segments):
            if segment.reasoning_display:
                reasoning = segment.reasoning_display
                yield StreamEvent(
                    event="reasoning_display.started",
                    data={
                        "projection_id": reasoning.projection_id,
                        "segment_id": reasoning.segment_id,
                        "title": reasoning.title,
                    },
                ).to_sse()
                for chunk in self._chunks(reasoning.text, size=64):
                    yield StreamEvent(
                        event="reasoning_display.delta",
                        data={"projection_id": reasoning.projection_id, "text_delta": chunk},
                    ).to_sse()
                    await asyncio.sleep(0)
                yield StreamEvent(
                    event="reasoning_display.completed",
                    data={"reasoning_display": reasoning.model_dump(mode="json")},
                ).to_sse()
            yield StreamEvent(
                event="segment.started",
                data={
                    "segment_id": segment.segment_id,
                    "index": index,
                    "type": segment.type,
                    "title": segment.title,
                },
            ).to_sse()
            for chunk in self._chunks(segment.output_text or segment.content, size=80):
                yield StreamEvent(
                    event="segment.delta",
                    data={"segment_id": segment.segment_id, "delta": {"type": "output_text_delta", "text": chunk}},
                ).to_sse()
                await asyncio.sleep(0)
            yield StreamEvent(
                event="segment.completed",
                data={"segment_id": segment.segment_id, "status": segment.status},
            ).to_sse()
        yield StreamEvent(
            event="turn.completed",
            data={"id": response.id, "status": response.status},
        ).to_sse()

    async def _finalize_and_persist(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
        *,
        medical: bool,
    ) -> AgentTurnResponse:
        """执行输出审查、记忆候选抽取任务入队与回合持久化。

        :param request: 请求对象。
        :param response: 响应对象。
        :param medical: 是否属于医疗咨询回合。
        :return: 返回函数执行结果。
        """
        response = await self.output_safety_service.review_response(response)
        if medical:
            response.metadata["memory_extraction_sources"] = list(self._memory_extraction_sources(request, response))
            background_task = None
            disabled_reason = None
            if self.settings.enable_memory_extraction:
                background_task = await self.background_task_service.enqueue_memory_candidate_extraction(
                    request,
                    response,
                )
                if not self.background_task_service.enabled:
                    disabled_reason = "background_task_service_disabled"
            else:
                disabled_reason = "memory_extraction_disabled"
            response.metadata["memory_extraction"] = make_memory_extraction_task_metadata(
                background_task,
                response_text=response.output_text,
                disabled_reason=disabled_reason,
            )
        else:
            response.metadata["memory_extraction"] = {
                "agent": "MemoryExtractionAgent",
                "strategy": MemoryExtractionStrategy.MEMORY_EXTRACTION_SKIPPED.value,
                "fallback_reason": "non_medical_turn",
                "confidence": 0.0,
                "source_text": response.output_text[:500],
                "trusted": False,
                "proposal_count": 0,
                "proposal_keys": [],
                "proposals": [],
                "stored_fact_count": 0,
                "stored_fact_keys": [],
                "task_id": None,
                "task_status": "skipped",
                "task": None,
            }
        await self._persist(request, response, medical=medical)
        return response

    def _memory_extraction_sources(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> tuple[dict[str, Any], ...]:
        """构造长期记忆候选抽取使用的显式来源片段。

        :param request: 请求对象。
        :param response: 响应对象。
        :return: 返回长期记忆候选抽取可见的来源片段字典元组。
        """
        raw_sources = response.metadata.get("memory_extraction_sources")
        if isinstance(raw_sources, list) and raw_sources:
            normalized = tuple(item for item in raw_sources if isinstance(item, dict))
            if normalized:
                return normalized

        task_summaries = response.metadata.get("tasks")
        if isinstance(task_summaries, list) and len(task_summaries) == len(response.segments) and len(task_summaries) > 1:
            sources: list[dict[str, Any]] = []
            for index, segment in enumerate(response.segments):
                task_summary = task_summaries[index] if isinstance(task_summaries[index], dict) else {}
                sources.append(
                    self._memory_extraction_task_source(
                        request,
                        segment,
                        task_summary,
                        index=index,
                    )
                )
            return tuple(sources)

        return (self._memory_extraction_turn_source(request, response),)

    def _memory_extraction_task_source(
        self,
        request: AgentTurnRequest,
        segment: VetSegment,
        task_summary: dict[str, Any],
        *,
        index: int,
    ) -> dict[str, Any]:
        """构造单个多任务长期记忆候选来源片段。

        :param request: 请求对象。
        :param segment: 当前任务执行结果段。
        :param task_summary: 当前任务的结构化摘要。
        :param index: 任务在当前回合中的顺序编号。
        :return: 返回长期记忆候选来源字典。
        """
        source_id = str(task_summary.get("task_id") or task_summary.get("task_key") or segment.segment_id)
        user_text = str(task_summary.get("text") or "").strip() or request.joined_text()
        return {
            "source_id": source_id,
            "entry_kind": "task",
            "user_text": user_text,
            "assistant_text": segment.output_text or segment.content,
            "task_id": task_summary.get("task_id"),
            "task_key": task_summary.get("task_key"),
            "task_title": task_summary.get("title") or segment.title,
            "task_domain": task_summary.get("domain") or "",
            "consultation_state": dict(task_summary.get("consultation_state") or {}),
            "metadata": {
                "index": index,
                "segment_id": segment.segment_id,
                "status": task_summary.get("status"),
                "consultation_phase": task_summary.get("consultation_phase"),
            },
        }

    def _memory_extraction_turn_source(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> dict[str, Any]:
        """构造单回合长期记忆候选来源片段。

        :param request: 请求对象。
        :param response: 响应对象。
        :return: 返回长期记忆候选来源字典。
        """
        return {
            "source_id": response.id,
            "entry_kind": "turn",
            "user_text": request.joined_text(),
            "assistant_text": response.output_text,
            "task_title": "当前回合",
            "task_domain": "",
            "consultation_state": dict(response.metadata.get("consultation_state") or {}),
            "metadata": {
                "segment_count": len(response.segments),
                "response_status": response.status,
            },
        }

    async def _persist(self, request: AgentTurnRequest, response: AgentTurnResponse, *, medical: bool) -> None:
        """执行 _persist 内部辅助逻辑。

        :param request: 请求对象。
        :param response: 响应对象。
        :param medical: 是否属于医疗咨询回合。
        :return: 返回函数执行结果。
        """
        await self.memory_service.remember_turn(
            request.trusted_identity,
            user_text=request.joined_text(),
            summary=response.output_text,
            medical=medical,
            metadata={
                "turn_id": response.id,
                "request_id": request.request_context.request_id,
                "trace_id": request.request_context.trace_id,
                "status": response.status,
                "response_snapshot": response.model_dump(mode="json"),
            },
        )
        await self.trace_store.write_turn(request, response)

    def _chunks(self, text: str, size: int) -> Iterator[str]:
        """执行 _chunks 内部辅助逻辑。

        :param text: 待处理文本。
        :param size: 分片大小。
        :return: 返回函数执行结果。
        """
        for start in range(0, len(text), size):
            yield text[start : start + size]
