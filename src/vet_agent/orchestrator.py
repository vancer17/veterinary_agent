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
    ConsultationStateAgent,
    MemoryExtractionAgent,
    MemoryFactCandidate,
    QuestionPlanner,
    RagFollowupPlan,
    RagQuestionPlannerAgent,
    ResponseComposer,
    SafetyAgent,
    SafetyAssessment,
    SafetyReviewAgent,
    TaskRouterAgent,
)
from vet_agent import Settings
from vet_agent import (
    AgentTurnRequest,
    AgentTurnResponse,
    Evidence,
    StreamEvent,
    TrustedIdentity,
    VetSegment,
)
from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluator,
    ClinicalSafetyEvaluationResult,
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetySemanticResult,
)
from vet_agent.input_safety import InputSafetyDecision, InputSafetyRequestContext, InputSafetyService
from vet_agent.memory import MemoryContextBuilder, MemoryPromptContext, MemoryReadBundle, MemoryReadService
from vet_agent.observability import AgentPathNode, build_agent_path
from vet_agent.repositories import RuleRepository
from vet_agent.runtime import QwenClient
from vet_agent.services import (
    KnowledgeService,
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
        knowledge_service: KnowledgeService,
        qwen_client: QwenClient,
        rule_repository: RuleRepository,
        clinical_safety_evaluator: ClinicalSafetyEvaluator,
        clinical_safety_semantic_extractor: ClinicalSafetySemanticExtractorAgent,
        turn_execution_gate: TurnExecutionGateProtocol,
        input_safety_service: InputSafetyService,
        task_router: TaskRouterAgent,
    ) -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :param context_provider: 参数 context_provider。
        :param memory_service: 参数 memory_service。
        :param memory_read_service: 结构化记忆读取服务。
        :param memory_context_builder: 记忆提示词上下文编译器。
        :param trace_store: 参数 trace_store。
        :param knowledge_service: 参数 knowledge_service。
        :param qwen_client: 参数 qwen_client。
        :param rule_repository: 参数 rule_repository。
        :param clinical_safety_evaluator: 结构化临床安全评估器。
        :param clinical_safety_semantic_extractor: 临床安全结构化语义抽取器。
        :param turn_execution_gate: 单回合执行门禁，负责 turn lock 与幂等基础设施控制。
        :param input_safety_service: 基础输入安全候选与 OPA 策略裁决服务。
        :param task_router: 结构化任务路由 Agent。
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
        self.knowledge_service = knowledge_service
        self.safety = SafetyAgent(rule_repository)
        self.clinical_safety = clinical_safety_evaluator
        self.clinical_safety_semantic_extractor = clinical_safety_semantic_extractor
        self.safety_review = SafetyReviewAgent(self.safety)
        self.semantic_extractor = ConsultationSemanticExtractorAgent(qwen_client, settings)
        self.consultation = ConsultationStateAgent(
            rule_repository,
            max_followup_rounds=settings.consultation_max_followup_rounds,
        )
        self.task_router = task_router
        self.rag_question_planner = RagQuestionPlannerAgent(qwen_client)
        self.memory_extractor = MemoryExtractionAgent(qwen_client, settings)
        self.composer = ResponseComposer(qwen_client, self.safety, QuestionPlanner())
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
            pet_context.summary(),
            age_text=str(pet_context.verified_profile.get("age") or ""),
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
        consultation_decision = self.consultation.update(
            active_previous_state,
            task.text,
            pet_context,
            task_domain=task.domain,
            semantic_result=semantic_result,
            clinical_safety_semantic=clinical_semantic,
            max_questions=request.turn_options.max_followup_questions,
        )

        if not consultation_decision.ready:
            followup_plan, knowledge_evidence, consultation_decision = await self._plan_followup_questions(
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
            output_text, post_signals = self.safety.sanitize_output(output_text)
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
                safety_signals=[*assessment.signals, *post_signals],
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
                safety_signals=[*assessment.signals, *post_signals],
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
                        AgentPathNode.CONSULTATION_STATE_AGENT,
                        AgentPathNode.ANSWERABILITY_EVALUATOR,
                        AgentPathNode.KNOWLEDGE_AGENT,
                        AgentPathNode.RAG_QUESTION_PLANNER_AGENT,
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

        knowledge_hits, knowledge_evidence = await self.knowledge_service.retrieve(task.text)

        output_text, context_evidence = await self.composer.compose(
            user_text=task.text,
            pet_context=pet_context,
            memory=memory_context,
            knowledge_hits=knowledge_hits,
            model=model,
            max_followup_questions=request.turn_options.max_followup_questions,
            consultation_context=self.consultation.format_state_for_prompt(consultation_decision.state),
            allow_followup=False,
        )
        output_text, post_signals = self.safety.sanitize_output(output_text)
        user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
        evidence = [*user_evidence, *context_evidence, *knowledge_evidence]
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
            safety_signals=[*assessment.signals, *post_signals],
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
            safety_signals=[*assessment.signals, *post_signals],
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
                    AgentPathNode.CONSULTATION_STATE_AGENT,
                    AgentPathNode.ANSWERABILITY_EVALUATOR,
                    AgentPathNode.KNOWLEDGE_AGENT,
                    AgentPathNode.QUESTION_PLANNER_AGENT,
                    AgentPathNode.QWEN_RESPONSE_AGENT,
                    AgentPathNode.SAFETY_REVIEW_AGENT,
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
        text = self.safety.forced_response(assessment)
        text, post_signals = self.safety.sanitize_output(text)
        signals = [*assessment.signals, *post_signals]
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
        text, post_signals = self.safety.sanitize_output(text)
        signals = [*decision.signals, *post_signals]
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
        response = self.safety_review.review_response(response)
        response.metadata["memory_extraction"] = {
            "agent": "MemoryExtractionAgent",
            "stored_fact_count": 0,
            "fact_keys": [],
            "skipped_reason": "input_safety_policy_stopped_main_chain",
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
        used_rag_question_planner = False
        used_response_composer = False

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
            consultation_decision = self.consultation.update(
                task_previous_state,
                task.text,
                pet_context,
                task_domain=task.domain,
                semantic_result=semantic_result,
                clinical_safety_semantic=clinical_safety_semantic,
                max_questions=request.turn_options.max_followup_questions,
            )
            user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
            followup_plan: RagFollowupPlan | None = None

            if consultation_decision.ready:
                knowledge_hits, knowledge_evidence = await self.knowledge_service.retrieve(task.text)
                output_text, context_evidence = await self.composer.compose(
                    user_text=task.text,
                    pet_context=pet_context,
                    memory=memory_context,
                    knowledge_hits=knowledge_hits,
                    model=model,
                    max_followup_questions=request.turn_options.max_followup_questions,
                    consultation_context=self.consultation.format_state_for_prompt(consultation_decision.state),
                    allow_followup=False,
                )
                used_response_composer = True
                segment_status = "completed"
                segment_type = "medical_consultation"
                evidence = [*user_evidence, *context_evidence, *knowledge_evidence]
            else:
                followup_plan, knowledge_evidence, consultation_decision = await self._plan_followup_questions(
                    user_text=task.text,
                    pet_context=pet_context,
                    consultation_decision=consultation_decision,
                    model=model,
                    max_questions=request.turn_options.max_followup_questions,
                )
                used_rag_question_planner = True
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

            output_text, post_signals = self.safety.sanitize_output(output_text)
            all_safety_signals.extend(post_signals)
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
                safety_signals=[*assessment.signals, *post_signals],
            )
            segment.reasoning_display = reasoning_display
            segment.references = self.reasoning_display.references_from_evidence(evidence)
            segments.append(segment)
            task_summaries.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "domain": task.domain,
                    "status": segment_status,
                    "missing_slots": consultation_decision.missing_slots,
                    "consultation_phase": consultation_decision.state.phase,
                    "answerability": consultation_decision.answerability,
                    "semantic_extraction": consultation_decision.state.semantic_extraction,
                    "followup_question_plan": followup_plan.to_metadata() if followup_plan else None,
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
                        AgentPathNode.CONSULTATION_STATE_AGENT,
                        AgentPathNode.ANSWERABILITY_EVALUATOR,
                        AgentPathNode.KNOWLEDGE_AGENT,
                    ),
                    *(
                        build_agent_path(AgentPathNode.RAG_QUESTION_PLANNER_AGENT)
                        if used_rag_question_planner
                        else []
                    ),
                    *(
                        build_agent_path(AgentPathNode.QWEN_RESPONSE_AGENT)
                        if used_response_composer
                        else []
                    ),
                    *build_agent_path(AgentPathNode.SAFETY_REVIEW_AGENT),
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

    async def _plan_followup_questions(
        self,
        *,
        user_text: str,
        pet_context,
        consultation_decision: ConsultationDecision,
        model: str,
        max_questions: int,
    ) -> tuple[RagFollowupPlan, list, ConsultationDecision]:
        """基于知识库反推下一轮追问，并写回问诊决策。

        :param user_text: 用户本轮输入文本。
        :param pet_context: 宠物上下文。
        :param consultation_decision: 当前问诊决策。
        :param model: 模型名称。
        :param max_questions: 最多追问数量。
        :return: 返回追问规划、知识库证据列表与更新后的问诊决策。
        """
        query = self._followup_knowledge_query(
            user_text=user_text,
            pet_context=pet_context,
            consultation_decision=consultation_decision,
        )
        try:
            knowledge_hits, knowledge_evidence = await self.knowledge_service.retrieve(query)
        except Exception:
            knowledge_hits = []
            knowledge_evidence = []

        fallback_questions = list(consultation_decision.questions)
        plan = await self.rag_question_planner.plan(
            user_text=user_text,
            pet_context_summary=pet_context.summary(),
            consultation_state=consultation_decision.state.to_dict(),
            missing_slots=consultation_decision.missing_slots,
            fallback_questions=fallback_questions,
            knowledge_hits=knowledge_hits,
            model=model,
            max_questions=max_questions,
        )
        if plan.questions:
            recent_questions = (
                consultation_decision.state.asked_questions[-len(fallback_questions) :]
                if fallback_questions
                else []
            )
            if fallback_questions and recent_questions == fallback_questions:
                consultation_decision.state.asked_questions = consultation_decision.state.asked_questions[
                    : -len(fallback_questions)
                ]
            planned_questions = plan.question_texts()
            for question in planned_questions:
                if question not in consultation_decision.state.asked_questions:
                    consultation_decision.state.asked_questions.append(question)
            consultation_decision = ConsultationDecision(
                state=consultation_decision.state,
                ready=consultation_decision.ready,
                missing_slots=consultation_decision.missing_slots,
                questions=planned_questions,
                answerability=consultation_decision.answerability,
            )
        return plan, knowledge_evidence, consultation_decision

    def _followup_knowledge_query(
        self,
        *,
        user_text: str,
        pet_context,
        consultation_decision: ConsultationDecision,
    ) -> str:
        """构造用于反推追问的知识库检索查询。

        :param user_text: 用户本轮输入文本。
        :param pet_context: 宠物上下文。
        :param consultation_decision: 当前问诊决策。
        :return: 返回函数执行结果。
        """
        state = consultation_decision.state.to_dict()
        slots = state.get("slots") or {}
        missing = "、".join(consultation_decision.missing_slots) or "无"
        answerability = state.get("answerability") or {}
        semantic = state.get("semantic_extraction") or {}
        return "\n".join(
            [
                user_text,
                f"宠物资料: {pet_context.summary()}",
                f"问诊方向: {consultation_decision.state.domain}",
                f"已知槽位: {slots}",
                f"语义抽取结果: {semantic}",
                f"本轮仍阻塞回答的高价值证据: {missing}",
                f"回答充分性判断: {answerability}",
                "请检索与风险分层、鉴别观察点、下一步问诊要点相关的兽医知识。",
            ]
        )

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
        """执行输出审查、记忆抽取与回合持久化。

        :param request: 请求对象。
        :param response: 响应对象。
        :param medical: 是否属于医疗咨询回合。
        :return: 返回函数执行结果。
        """
        response = self.safety_review.review_response(response)
        extracted_facts = await self._extract_and_store_facts(request, response) if medical else []
        response.metadata["memory_extraction"] = {
            "agent": "MemoryExtractionAgent",
            "stored_fact_count": len(extracted_facts),
            "fact_keys": [f"{item.fact_type}:{item.fact_key}" for item in extracted_facts],
        }
        path = response.metadata.get("multi_agent_path")
        if isinstance(path, list) and extracted_facts and "MemoryExtractionAgent" not in path:
            path.append("MemoryExtractionAgent")
        await self._persist(request, response, medical=medical)
        return response

    async def _extract_and_store_facts(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
    ) -> list[MemoryFactCandidate]:
        """执行内部抽取逻辑。

        :param request: 请求对象。
        :param response: 响应对象。
        :return: 返回异步执行结果。
        """
        try:
            facts = await self.memory_extractor.extract(
                identity=request.trusted_identity,
                user_text=request.joined_text(),
                response=response,
                model=response.model,
            )
        except Exception:
            return []
        stored: list[MemoryFactCandidate] = []
        for fact in facts:
            try:
                await self.memory_service.upsert_pet_fact(
                    request.trusted_identity,
                    fact_type=fact.fact_type,
                    fact_key=fact.fact_key,
                    fact_value=fact.fact_value,
                    confidence=fact.confidence,
                    source_turn_id=response.id,
                    source_text=fact.source_text,
                    metadata=fact.metadata or {"source": "MemoryExtractionAgent"},
                )
                stored.append(fact)
            except Exception:
                continue
        return stored

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
