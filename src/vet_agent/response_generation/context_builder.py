"""
=============================================================================
文件：src/vet_agent/response_generation/context_builder.py
作用：将回复生成所需的结构化上游结果编译为模型可消费的提示词上下文。
范围：位于临床安全、问诊充分性、宠物上下文、记忆与回答 RAG 之后；
      本层只做提示词组织与字符预算裁剪，不再扫描原始文本、不做事实推理、
      不做冲突裁决，也不承担输出安全清洗。
说明：该编译器是回复生成链路的最后一道结构化拼装层，必须消费已完成裁决
      的上游结果，不得回退到关键词状态机或自定义规则分支。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Iterable

from vet_agent.repositories import KnowledgeHit

from .errors import ResponseGenerationContractError
from .models import (
    ResponseGenerationContext,
    ResponseGenerationRequest,
    ResponseGenerationStrategy,
)
from .projections import (
    AnswerEvidenceContext,
    AnswerabilityGenerationContext,
    ClinicalSafetyGenerationContext,
    ConsultationGenerationContext,
    MemoryGenerationContext,
    stringify_generation_value,
)


class ResponseGenerationContextBuilder:
    """编译回复生成阶段的提示词上下文。

    :param max_prompt_chars: 允许进入模型调用的最大提示词字符预算。
    :return: 无返回值。
    """

    def __init__(self, *, max_prompt_chars: int = 12_000) -> None:
        """初始化回复生成上下文编译器。

        :param max_prompt_chars: 允许进入模型调用的最大提示词字符预算。
        :return: 无返回值。
        :raises ResponseGenerationContractError: 字符预算不合法时抛出。
        """
        if max_prompt_chars < 1:
            raise ResponseGenerationContractError(
                "response generation prompt budget must be positive",
                details={"max_prompt_chars": max_prompt_chars},
            )
        self.max_prompt_chars = max_prompt_chars

    def build(self, request: ResponseGenerationRequest) -> ResponseGenerationContext:
        """根据结构化上游结果编译回复生成提示词上下文。

        :param request: 回复生成结构化请求。
        :return: 返回编译后的回复生成上下文。
        :raises ResponseGenerationContractError: 请求缺少必要的上游结构化结果时抛出。
        """
        self._validate_request(request)
        system_prompt = self._system_prompt()
        response_format_section = self._response_format_section()
        body_sections = [
            self._task_metadata_section(request),
            self._clinical_safety_section(request),
            self._answerability_section(request),
            self._pet_context_section(request),
            self._consultation_state_section(request),
            self._memory_section(request),
            self._answer_rag_section(request.answer_rag_result.hits),
            self._current_task_text_section(request),
        ]
        body_source = "\n\n".join(section for section in body_sections if section.strip())
        content_budget = max(
            self.max_prompt_chars - len(system_prompt) - len(response_format_section) - 4,
            0,
        )
        if content_budget < 1:
            raise ResponseGenerationContractError(
                "response generation prompt budget is smaller than fixed prompt sections",
                details={
                    "max_prompt_chars": self.max_prompt_chars,
                    "system_prompt_chars": len(system_prompt),
                    "response_format_chars": len(response_format_section),
                },
            )
        body = self._fit_budget(body_source, content_budget)
        user_prompt = "\n\n".join(
            section for section in (body, response_format_section) if section.strip()
        )
        prompt_text = "\n\n".join(part for part in (system_prompt, user_prompt) if part.strip())
        metadata = {
            "strategy": ResponseGenerationStrategy.QWEN_RESPONSE_GENERATION.value,
            "prompt_chars": len(prompt_text),
            "system_prompt_chars": len(system_prompt),
            "user_prompt_chars": len(user_prompt),
            "content_budget_chars": content_budget,
            "content_truncated": len(body_source) > len(body),
            "task": request.task.to_metadata(),
            "consultation_phase": request.consultation_decision.state.phase,
            "consultation_ready": request.consultation_decision.ready,
            "answerability": dict(request.consultation_decision.answerability),
            "memory_context": dict(request.memory_context.metadata),
            "answer_rag": request.answer_rag_result.to_metadata(),
            "clinical_safety_semantic": request.clinical_safety_semantic.to_metadata(),
            "clinical_safety_resolution": request.clinical_safety_resolution.to_metadata(),
            "model_visible_projection": {
                "clinical_safety_fields": ["action", "allow", "message", "reasons"],
                "answerability_fields": ["decision", "answer_scope", "reason", "unresolved_slots"],
                "memory_sections": [
                    section.to_metadata()
                    for section in MemoryGenerationContext.from_prompt_context(
                        request.memory_context,
                        task_key=request.task.state_key,
                    ).sections
                ],
            },
        }
        messages = (
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        )
        return ResponseGenerationContext(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            messages=messages,
            metadata=metadata,
        )

    def _validate_request(self, request: ResponseGenerationRequest) -> None:
        """校验回复生成上下文编译入口请求。

        :param request: 回复生成结构化请求。
        :return: 无返回值。
        :raises ResponseGenerationContractError: 请求缺少必要的结构化上下文时抛出。
        """
        if not request.task.text.strip():
            raise ResponseGenerationContractError(
                "response generation task text is empty",
                details={"task_id": request.task.task_id, "task_key": request.task.task_key},
            )
        answerability = dict(request.consultation_decision.answerability or {})
        if not request.consultation_decision.ready or str(answerability.get("decision") or "").strip() != "answer":
            raise ResponseGenerationContractError(
                "response generation can only run after answer decision",
                details={
                    "ready": request.consultation_decision.ready,
                    "decision": answerability.get("decision"),
                },
            )
        if not request.answer_rag_result.hits:
            raise ResponseGenerationContractError(
                "response generation requires answer RAG hits",
                details={"task_id": request.task.task_id, "task_key": request.task.task_key},
            )
        for index, hit in enumerate(request.answer_rag_result.hits):
            AnswerEvidenceContext.from_hit(hit, index=index)

    def _system_prompt(self) -> str:
        """生成回复生成阶段的系统约束。

        :return: 返回系统提示词文本。
        """
        return "\n".join(
            (
                "你是兽医多 Agent 编排中的回复生成阶段。",
                "必须遵守:",
                "1. 不能替代线下兽医诊断。",
                "2. 只能基于已验证宠物资料、当前会话结构化事实、回答相关 RAG 证据与历史记忆组织回复，不要补写缺失事实。",
                "3. 只能解释和呈现上游临床安全裁决，不得自行新增、升级、降低或覆盖临床安全动作。",
                "4. 涉及用药只能给方向，不能给具体剂量数字；必须提示按药品使用说明书或遵从兽医指导。",
                "5. 上游回答充分性已允许阶段性回答，但不代表所有信息完整；必须说明未确认边界，不得重新进入追问流程。",
            )
        )

    def _task_metadata_section(self, request: ResponseGenerationRequest) -> str:
        """编译当前任务的稳定元数据。

        :param request: 回复生成结构化请求。
        :return: 返回当前任务元数据分区文本。
        """
        lines = [
            "当前任务元数据:",
            f"- 任务域: {request.task.domain}",
            f"- 标题: {request.task.title}",
            f"- 优先级: {request.task.priority}",
        ]
        return "\n".join(lines)

    def _clinical_safety_section(self, request: ResponseGenerationRequest) -> str:
        """编译临床安全裁决与语义摘要。

        :param request: 回复生成结构化请求。
        :return: 返回临床安全分区文本。
        """
        context = self._clinical_safety_generation_context(request)
        lines = [
            "临床安全裁决:",
            f"- 上游动作: {context.action or 'unknown'}",
            f"- 是否允许普通问诊链路: {context.allow}",
            f"- 裁决说明: {context.message or '暂无'}",
        ]
        if context.reasons:
            lines.append("- 裁决原因:")
            lines.extend(f"  - {reason}" for reason in context.reasons)
        lines.append("- 生成约束: 只能解释上游裁决，不得自行改变临床安全动作。")
        return "\n".join(lines)

    def _clinical_safety_generation_context(
        self,
        request: ResponseGenerationRequest,
    ) -> ClinicalSafetyGenerationContext:
        """从临床安全裁决结果中投影模型可见安全摘要。

        :param request: 回复生成结构化请求。
        :return: 返回不包含 policy payload 和语义抽取原文的安全投影。
        """
        policy_decision = dict(request.clinical_safety_resolution.policy_decision or {})
        reasons = tuple(
            str(reason).strip()
            for reason in policy_decision.get("reasons") or ()
            if str(reason).strip()
        )
        return ClinicalSafetyGenerationContext(
            action=str(policy_decision.get("action") or "unknown"),
            allow=bool(policy_decision.get("allow", True)),
            message=str(
                policy_decision.get("message")
                or policy_decision.get("reason")
                or "暂无明确裁决说明"
            ),
            reasons=reasons,
        )

    def _answerability_section(self, request: ResponseGenerationRequest) -> str:
        """编译当前任务的回答充分性决策摘要。

        :param request: 回复生成结构化请求。
        :return: 返回回答充分性分区文本。
        """
        context = self._answerability_generation_context(request)
        lines = [
            "当前任务回答充分性:",
            f"- 上游决策: {context.decision}",
            f"- 回答范围: {context.answer_scope or '阶段性建议'}",
            f"- 裁决原因: {context.reason or '暂无'}",
        ]
        if context.unresolved_slots:
            lines.append("- 尚未确认但不阻塞本轮阶段性回答的信息:")
            lines.extend(f"  - {slot}" for slot in context.unresolved_slots)
            lines.append("- 生成约束: 未确认信息只能作为边界说明，不得表述为已否认事实。")
        lines.append("- 执行路径: 本轮已进入 answer 分支，不得自行改为追问分支。")
        return "\n".join(lines)

    def _answerability_generation_context(
        self,
        request: ResponseGenerationRequest,
    ) -> AnswerabilityGenerationContext:
        """从回答充分性策略结果中投影模型可见回答边界。

        :param request: 回复生成结构化请求。
        :return: 返回回答充分性生成投影。
        """
        answerability = dict(request.consultation_decision.answerability or {})
        return AnswerabilityGenerationContext(
            decision=str(answerability.get("decision") or ""),
            answer_scope=str(answerability.get("answer_scope") or "阶段性建议"),
            reason=str(answerability.get("reason") or ""),
            unresolved_slots=tuple(
                str(item).strip()
                for item in answerability.get("unresolved_slots") or ()
                if str(item).strip()
            ),
        )

    def _pet_context_section(self, request: ResponseGenerationRequest) -> str:
        """编译服务端已验证宠物上下文。

        :param request: 回复生成结构化请求。
        :return: 返回宠物上下文分区文本。
        """
        summary = request.pet_context.summary().strip()
        if not summary:
            summary = "宠物画像: 当前暂无服务端已验证资料，相关字段按未知处理。"
        return "\n".join(("服务端已验证宠物资料:", summary))

    def _consultation_state_section(self, request: ResponseGenerationRequest) -> str:
        """编译当前任务的结构化问诊状态。

        :param request: 回复生成结构化请求。
        :return: 返回问诊状态分区文本。
        """
        context = self._consultation_generation_context(request)
        lines = [
            "当前会话上下文:",
            f"- 主诉: {context.chief_complaint or '未知'}",
            f"- 任务域: {context.domain}",
            f"- 阶段: {context.phase}",
        ]
        if context.slots:
            lines.append("核心槽位:")
            for slot_key, slot_value in context.slots:
                lines.append(f"- {slot_key}: {slot_value}")
        if context.working_facts:
            lines.append("结构化事实:")
            lines.extend(f"- {fact}" for fact in context.working_facts[-8:])
        if context.observations:
            lines.append("开放观察:")
            lines.extend(f"- {observation}" for observation in context.observations[-6:])
        if context.asked_questions:
            lines.append("已问问题:")
            lines.extend(f"- {question}" for question in context.asked_questions[-6:])
        if context.temporal_context:
            lines.append("临床安全时间上下文:")
            lines.extend(f"- {key}: {value}" for key, value in context.temporal_context)
        return "\n".join(line for line in lines if str(line).strip())

    def _consultation_generation_context(
        self,
        request: ResponseGenerationRequest,
    ) -> ConsultationGenerationContext:
        """从问诊状态投影模型可见的当前任务事实。

        :param request: 回复生成结构化请求。
        :return: 返回不包含语义抽取审计 metadata 的问诊事实投影。
        """
        state = request.consultation_decision.state
        return ConsultationGenerationContext(
            chief_complaint=state.chief_complaint or "",
            domain=state.domain,
            phase=state.phase,
            slots=tuple(
                (str(slot_key), stringify_generation_value(slot_value))
                for slot_key, slot_value in state.slots.items()
                if slot_value not in (None, "")
            ),
            working_facts=tuple(
                self._format_generation_mapping(fact)
                for fact in state.working_facts
                if isinstance(fact, dict)
            ),
            observations=tuple(
                self._format_generation_mapping(observation)
                for observation in state.observations
                if isinstance(observation, dict)
            ),
            asked_questions=tuple(str(question) for question in state.asked_questions if str(question).strip()),
            temporal_context=tuple(
                (str(key), str(value))
                for key, value in state.temporal_context.items()
                if str(value).strip()
            ),
        )

    def _memory_section(self, request: ResponseGenerationRequest) -> str:
        """编译历史记忆提示词分区。

        :param request: 回复生成结构化请求。
        :return: 返回历史记忆分区文本。
        """
        context = MemoryGenerationContext.from_prompt_context(
            request.memory_context,
            task_key=request.task.state_key,
        )
        if not context.sections:
            return "历史记忆:\n暂无可用历史记忆。"
        lines = [
            "历史记忆:",
            "说明: 历史记忆仅作为分层参考；session 共享上下文和语义线索不得覆盖当前任务结构化事实、已验证宠物资料或权威长期事实。",
        ]
        for section in context.sections:
            lines.append(f"{section.source_label}:")
            if section.authority == "semantic_hint":
                lines.append("- 来源等级: 语义线索，仅作历史参考。")
            elif section.authority == "conversational":
                lines.append("- 来源等级: 当前会话共享参考，不等同于当前任务专属事实。")
            elif section.authority == "episode":
                lines.append("- 来源等级: 宠物历史事件摘要。")
            else:
                lines.append("- 来源等级: 已验证长期事实。")
            lines.extend(section.content.splitlines())
        return "\n".join(lines)

    def _answer_rag_section(self, hits: list[KnowledgeHit]) -> str:
        """编译回答相关 RAG 证据分区。

        :param hits: 回答相关 RAG 命中列表。
        :return: 返回回答相关证据分区文本。
        """
        if not hits:
            raise ResponseGenerationContractError(
                "response generation answer RAG hits are empty",
                details={"reason": "empty_hits"},
            )
        lines = ["回答相关 RAG 证据:"]
        for index, hit in enumerate(hits):
            evidence = AnswerEvidenceContext.from_hit(hit, index=index)
            summary = self._truncate_text(evidence.summary, 360)
            lines.append(f"- {evidence.title}: {summary}（来源: {evidence.source_label}）")
        return "\n".join(lines)

    def _current_task_text_section(self, request: ResponseGenerationRequest) -> str:
        """编译当前用户任务文本分区。

        :param request: 回复生成结构化请求。
        :return: 返回当前用户任务文本分区。
        """
        return "\n".join(("当前用户任务文本:", request.task.text.strip()))

    def _response_format_section(self) -> str:
        """生成回复输出格式约束。

        :return: 返回回复格式提示词文本。
        """
        return "\n".join(
            (
                "请按以下结构回答:",
                "- 分诊/紧急度",
                "- 可能方向与依据",
                "- 现在可以做什么",
                "- 线下兽医兜底",
            )
        )

    def _format_generation_mapping(self, mapping: dict[str, object]) -> str:
        """将已白名单投影后的业务映射压缩为模型可见文本。

        :param mapping: 待展示的业务映射。
        :return: 返回不包含内部标识和审计 payload 的稳定文本。
        """
        if not mapping:
            return "暂无"
        hidden_keys = {
            "confidence",
            "metadata",
            "source_text",
            "raw_text",
            "policy_payload",
            "request_id",
            "trace_id",
            "task_id",
            "task_key",
            "pet_id",
            "user_id",
            "session_id",
        }
        parts: list[str] = []
        for key, value in mapping.items():
            if str(key) in hidden_keys or value in (None, ""):
                continue
            parts.append(f"{key}={stringify_generation_value(value)}")
        return "；".join(parts) or "暂无"

    def _fit_budget(self, text: str, limit: int) -> str:
        """按字符预算裁剪提示词内容。

        :param text: 原始提示词文本。
        :param limit: 字符预算上限。
        :return: 返回不超过字符预算的提示词文本。
        """
        if len(text) <= limit:
            return text
        return self._line_budget(text.splitlines(), limit)

    def _line_budget(self, lines: Iterable[str], limit: int) -> str:
        """按行保留提示词内容直到达到字符预算。

        :param lines: 原始提示词行迭代器。
        :param limit: 字符预算上限。
        :return: 返回预算内的提示词文本。
        """
        selected: list[str] = []
        size = 0
        for line in lines:
            next_size = size + len(line) + 1
            if next_size > limit:
                selected.append("……以上回复生成上下文已按字符预算截断。")
                break
            selected.append(line)
            size = next_size
        return "\n".join(selected)

    def _truncate_text(self, text: str, limit: int) -> str:
        """按字符数截断文本。

        :param text: 待截断文本。
        :param limit: 字符预算上限。
        :return: 返回截断后的文本。
        """
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}…"
