"""
文件：src/vet_agent/orchestrator.py
作用：提供兽医 Agent 项目的业务实现。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from vet_agent.agents import (
    ConsultationDecision,
    ConsultationStateAgent,
    MemoryExtractionAgent,
    QuestionPlanner,
    RagFollowupPlan,
    RagQuestionPlannerAgent,
    ResponseComposer,
    SafetyAgent,
    SafetyReviewAgent,
    SplitTask,
    TaskSplitterAgent,
)
from vet_agent import Settings
from vet_agent import AgentTurnRequest, AgentTurnResponse, StreamEvent, VetSegment
from vet_agent.repositories import RuleRepository
from vet_agent.runtime import QwenClient
from vet_agent.services import (
    KnowledgeService,
    LogicTraceStore,
    MemoryService,
    PetContextProvider,
    ReasoningDisplayBuilder,
)


class VetOrchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        context_provider: PetContextProvider,
        memory_service: MemoryService,
        trace_store: LogicTraceStore,
        knowledge_service: KnowledgeService,
        qwen_client: QwenClient,
        rule_repository: RuleRepository,
    ) -> None:
        """初始化当前对象。

        :param settings: 应用配置对象。
        :param context_provider: 参数 context_provider。
        :param memory_service: 参数 memory_service。
        :param trace_store: 参数 trace_store。
        :param knowledge_service: 参数 knowledge_service。
        :param qwen_client: 参数 qwen_client。
        :param rule_repository: 参数 rule_repository。
        :return: 无返回值。
        """
        self.settings = settings
        self.context_provider = context_provider
        self.memory_service = memory_service
        self.trace_store = trace_store
        self.knowledge_service = knowledge_service
        self.safety = SafetyAgent(rule_repository)
        self.safety_review = SafetyReviewAgent(self.safety)
        self.consultation = ConsultationStateAgent(rule_repository)
        self.task_splitter = TaskSplitterAgent(rule_repository, qwen_client, settings)
        self.rag_question_planner = RagQuestionPlannerAgent(qwen_client)
        self.memory_extractor = MemoryExtractionAgent(qwen_client, settings)
        self.composer = ResponseComposer(qwen_client, self.safety, QuestionPlanner())
        self.reasoning_display = ReasoningDisplayBuilder()

    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResponse:
        """执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        async with self._turn_lock(request):
            idempotency_key = request.turn_options.idempotency_key
            if idempotency_key:
                claim = await self.memory_service.begin_idempotency(
                    request.trusted_identity,
                    idempotency_key=idempotency_key,
                    request_id=request.request_context.request_id,
                    trace_id=request.request_context.trace_id,
                    wait_seconds=self.settings.idempotency_wait_seconds,
                    processing_ttl_seconds=self.settings.idempotency_processing_ttl_seconds,
                )
                if claim.get("state") == "replayed" and claim.get("response_snapshot"):
                    return AgentTurnResponse.model_validate(claim["response_snapshot"])
                if claim.get("state") == "busy":
                    raise TimeoutError("idempotent request is still processing")
                try:
                    return await self._run_turn_core(request)
                except Exception as exc:
                    await self.memory_service.mark_idempotency_failed(
                        request.trusted_identity,
                        idempotency_key=idempotency_key,
                        request_id=request.request_context.request_id,
                        trace_id=request.request_context.trace_id,
                        error_type=type(exc).__name__,
                    )
                    raise
            return await self._run_turn_core(request)

    async def _run_turn_core(self, request: AgentTurnRequest) -> AgentTurnResponse:
        """执行 _run_turn_core 内部辅助逻辑。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        user_text = request.joined_text()
        assessment = self.safety.analyze(user_text, request.attachments)
        model = request.model or self.settings.default_model

        if assessment.blocked or assessment.escalated:
            text = self.safety.forced_response(assessment)
            text, post_signals = self.safety.sanitize_output(text)
            signals = [*assessment.signals, *post_signals]
            segment = VetSegment(type="safety_triage", title="安全分诊", content=text, output_text=text)
            reasoning_display = self.reasoning_display.build_turn_display(
                status="blocked" if assessment.blocked else "safety_escalated",
                segment_id=segment.segment_id,
                evidence=[],
                safety_signals=signals,
            )
            segment.reasoning_display = reasoning_display
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
                metadata={"multi_agent_path": ["SafetyAgent"]},
            )
            return await self._finalize_and_persist(request, response, medical=True)

        pet_context = await self.context_provider.load(request.vet_context, request.metadata)
        memory = await self.memory_service.read(request.trusted_identity)
        previous_state = await self.memory_service.read_consultation_state(request.trusted_identity)
        continuing_consultation = self._has_unfinished_consultation_state(previous_state)
        if continuing_consultation:
            split_decision = None
        else:
            split_decision = await self.task_splitter.split(
                user_text,
                model=model,
                pet_context_summary=pet_context.summary(),
            )
            tasks = split_decision.tasks
            if len(tasks) > 1:
                response = await self._run_multi_task_turn(
                    request=request,
                    tasks=tasks,
                    split_decision=split_decision,
                    pet_context=pet_context,
                    memory=memory,
                    assessment=assessment,
                    model=model,
                )
                return await self._finalize_and_persist(request, response, medical=True)

        consultation_decision = self.consultation.update(
            previous_state,
            user_text,
            pet_context,
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
            await self.memory_service.save_consultation_state(
                request.trusted_identity,
                consultation_decision.state.to_dict(),
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
                    "multi_agent_path": [
                        "SafetyAgent",
                        "PetContextAgent",
                        "MemoryAgent",
                        "ConsultationStateAgent",
                        "KnowledgeAgent",
                        "RagQuestionPlannerAgent",
                    ],
                    "consultation_phase": consultation_decision.state.phase,
                    "consultation_state": consultation_decision.state.to_dict(),
                    "missing_slots": consultation_decision.missing_slots,
                    "followup_question_plan": followup_plan.to_metadata(),
                    **self._task_router_skip_metadata(continuing_consultation),
                },
            )
            return await self._finalize_and_persist(request, response, medical=True)

        await self.memory_service.save_consultation_state(
            request.trusted_identity,
            consultation_decision.state.to_dict(),
        )
        knowledge_hits, knowledge_evidence = await self.knowledge_service.retrieve(user_text)

        output_text, context_evidence = await self.composer.compose(
            user_text=user_text,
            pet_context=pet_context,
            memory=memory,
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
                "multi_agent_path": [
                    "SafetyAgent",
                    "PetContextAgent",
                    "MemoryAgent",
                    "KnowledgeAgent",
                    "QuestionPlannerAgent",
                    "QwenResponseAgent",
                    "SafetyReviewAgent",
                ],
                "litellm_configured": self.settings.litellm_configured,
                "consultation_phase": consultation_decision.state.phase,
                "consultation_state": consultation_decision.state.to_dict(),
                "missing_slots": consultation_decision.missing_slots,
                **self._task_router_skip_metadata(continuing_consultation),
            },
        )
        return await self._finalize_and_persist(request, response, medical=True)

    async def _run_multi_task_turn(
        self,
        *,
        request: AgentTurnRequest,
        tasks: list[SplitTask],
        split_decision,
        pet_context,
        memory: dict,
        assessment,
        model: str,
    ) -> AgentTurnResponse:
        """执行 _run_multi_task_turn 内部辅助逻辑。

        :param request: 请求对象。
        :param tasks: 任务列表。
        :param split_decision: 参数 split_decision。
        :param pet_context: 宠物上下文。
        :param memory: 参数 memory。
        :param assessment: 参数 assessment。
        :param model: 模型名称。
        :return: 返回函数执行结果。
        """
        task_states = await self.memory_service.read_task_consultation_states(request.trusted_identity)
        updated_task_states = dict(task_states)
        segments: list[VetSegment] = []
        all_evidence = []
        all_safety_signals = list(assessment.signals)
        task_summaries: list[dict] = []
        used_rag_question_planner = False
        used_response_composer = False

        for index, task in enumerate(tasks, start=1):
            consultation_decision = self.consultation.update(
                task_states.get(task.state_key),
                task.text,
                pet_context,
                max_questions=request.turn_options.max_followup_questions,
            )
            user_evidence = self.reasoning_display.user_answer_evidence(consultation_decision.state.to_dict())
            followup_plan: RagFollowupPlan | None = None

            if consultation_decision.ready:
                knowledge_hits, knowledge_evidence = await self.knowledge_service.retrieve(task.text)
                output_text, context_evidence = await self.composer.compose(
                    user_text=task.text,
                    pet_context=pet_context,
                    memory=memory,
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
                    "followup_question_plan": followup_plan.to_metadata() if followup_plan else None,
                }
            )

        await self.memory_service.save_task_consultation_states(
            request.trusted_identity,
            updated_task_states,
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
                    "SafetyAgent",
                    "PetContextAgent",
                    "MemoryAgent",
                    "TaskRouterAgent",
                    "ConsultationStateAgent",
                    "KnowledgeAgent",
                    *(["RagQuestionPlannerAgent"] if used_rag_question_planner else []),
                    *(["QwenResponseAgent"] if used_response_composer else []),
                    "SafetyReviewAgent",
                ],
                "task_count": len(tasks),
                "task_router_strategy": split_decision.strategy,
                "task_router_fallback_reason": split_decision.fallback_reason,
                "tasks": task_summaries,
                "consultation_states": updated_task_states,
                "litellm_configured": self.settings.litellm_configured,
            },
        )

    def _has_unfinished_consultation_state(self, state: dict | None) -> bool:
        """判断当前会话是否存在未完成的默认问诊状态。

        :param state: 已持久化的默认问诊状态。
        :return: 返回函数执行结果。
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

    def _task_router_skip_metadata(self, skipped: bool) -> dict[str, str | bool]:
        """构造任务拆分跳过审计信息。

        :param skipped: 是否跳过任务拆分。
        :return: 返回函数执行结果。
        """
        if not skipped:
            return {}
        return {
            "task_router_skipped": True,
            "task_router_strategy": "skipped_unfinished_consultation_state",
            "task_router_skip_reason": "当前 session 存在未完成问诊状态，本轮优先作为上一轮追问回答处理。",
        }

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
        return "\n".join(
            [
                user_text,
                f"宠物资料: {pet_context.summary()}",
                f"问诊方向: {consultation_decision.state.domain}",
                f"已知槽位: {slots}",
                f"缺失槽位: {missing}",
                "请检索与风险分层、鉴别观察点、下一步问诊要点相关的兽医知识。",
            ]
        )

    async def stream_turn(self, request: AgentTurnRequest):
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

    @asynccontextmanager
    async def _turn_lock(self, request: AgentTurnRequest):
        """执行 _turn_lock 内部辅助逻辑。

        :param request: 请求对象。
        :return: 返回异步执行结果。
        """
        lock_factory = getattr(self.memory_service, "turn_lock", None)
        if callable(lock_factory):
            async with lock_factory(request.trusted_identity):
                yield
            return
        yield

    async def _finalize_and_persist(
        self,
        request: AgentTurnRequest,
        response: AgentTurnResponse,
        *,
        medical: bool,
    ) -> AgentTurnResponse:
        """执行 _finalize_and_persist 内部辅助逻辑。

        :param request: 请求对象。
        :param response: 响应对象。
        :param medical: 是否属于医疗咨询回合。
        :return: 返回函数执行结果。
        """
        response = self.safety_review.review_response(response)
        extracted_facts = await self._extract_and_store_facts(request, response)
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
    ):
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
                vet_context=request.vet_context,
                model=response.model,
            )
        except Exception:
            return []
        stored = []
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
        if request.turn_options.idempotency_key:
            await self.memory_service.save_idempotency_response(
                request.trusted_identity,
                idempotency_key=request.turn_options.idempotency_key,
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                response_snapshot=response.model_dump(mode="json"),
            )

    def _chunks(self, text: str, size: int):
        """执行 _chunks 内部辅助逻辑。

        :param text: 待处理文本。
        :param size: 分片大小。
        :return: 返回函数执行结果。
        """
        for start in range(0, len(text), size):
            yield text[start : start + size]
