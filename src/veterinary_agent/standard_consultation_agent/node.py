##################################################################################################
# 文件: src/veterinary_agent/standard_consultation_agent/node.py
# 作用: 提供 StandardConsultationAgent 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 DTO 转换，不执行问诊业务逻辑、不调度输出护栏或发布链路。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.standard_consultation_agent.dto import (
    JsonMap,
    QuestionBudgetDto,
    StandardConsultationRequestDto,
    StandardSessionStateDto,
)
from veterinary_agent.standard_consultation_agent.enums import (
    StandardConsultationErrorCode,
    StandardConsultationOperation,
)
from veterinary_agent.standard_consultation_agent.errors import (
    StandardConsultationError,
)
from veterinary_agent.standard_consultation_agent.service import (
    StandardConsultationAgent,
)
from veterinary_agent.vet_context_builder import VetContextBundleDto


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class StandardConsultationAgentGraphNode:
    """将 StandardConsultationAgent 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        agent: StandardConsultationAgent,
        output_state_key: str = "standard_consultation_draft",
    ) -> None:
        """初始化标准问诊图节点。

        :param agent: StandardConsultationAgent 公共服务契约。
        :param output_state_key: 写入 graph business_state 的草稿键名。
        :return: None。
        :raises ValueError: 当输出 state 键为空时抛出。
        """

        if not output_state_key.strip():
            raise ValueError("output_state_key 不得为空")
        self._agent = agent
        self._output_state_key = output_state_key.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 中的标准问诊请求并写回草稿结果。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 包含标准问诊草稿、层级、追问和 trace patch 的节点状态更新。
        :raises StandardConsultationError: 当 state 缺少请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        draft = await self._agent.generate_draft(request)
        draft_payload = cast(JsonMap, draft.model_dump(mode="json"))
        state_patch: dict[str, object] = {
            self._output_state_key: draft_payload,
            "standard_generation_status": draft.status.value,
            "standard_reached_layer": draft.reached_layer.value,
            "standard_selected_questions": [
                question.model_dump(mode="json")
                for question in draft.selected_questions
            ],
            "standard_slot_progress_patch": draft.slot_progress_patch.model_dump(
                mode="json"
            ),
            "standard_trace_patch": draft.trace_patch.model_dump(mode="json"),
            "draft_response_ref": draft.draft_response_ref,
            "draft_response": draft.draft_response,
        }
        if draft.escalation_request is not None:
            state_patch["standard_escalation_requested"] = True
            state_patch["escalation_request"] = draft.escalation_request.model_dump(
                mode="json"
            )
        return GraphNodeResult(state_patch=state_patch)

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> StandardConsultationRequestDto:
        """从 graph state 和节点上下文构建严格标准问诊请求。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的标准问诊请求。
        :raises StandardConsultationError: 当 state 缺少或包含非法请求时抛出。
        """

        raw_request = _as_mapping(state.get("standard_consultation_request")) or {}
        raw_context = raw_request.get("context") or state.get("context_bundle")
        if not isinstance(raw_context, dict):
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_CONTEXT_MISSING,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="graph state 缺少 context_bundle",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
        context_bundle = VetContextBundleDto.model_validate(raw_context)
        request_data: dict[str, object] = {
            **raw_request,
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "run_id": context.run_id,
            "session_id": context.session_id,
            "user_id": context.user_id,
            "current_pet_id": context.current_pet_id,
            "task_id": raw_request.get("task_id") or context_bundle.task_id,
            "task_type": raw_request.get("task_type")
            or state.get("task_type")
            or "UNDECOMPOSED",
            "normalized_query": raw_request.get("normalized_query")
            or state.get("normalized_query")
            or state.get("original_user_message")
            or "",
            "generation_profile": raw_request.get("generation_profile")
            or (
                context_bundle.generation_profile.value
                if context_bundle.generation_profile is not None
                else ""
            ),
            "executor_key": raw_request.get("executor_key")
            or context_bundle.executor_key.value,
            "assessment_summary": raw_request.get("assessment_summary")
            or state.get("assessment_summary")
            or state.get("vet_input_assessment_result")
            or {},
            "context": context_bundle,
            "session_state": self._session_state_from_state(
                raw_request=raw_request,
                state=state,
            ),
            "question_budget": self._question_budget_from_state(
                raw_request=raw_request,
                state=state,
            ),
            "params_version": context.params_version,
            "config_snapshot_id": context.config_snapshot_id,
        }
        try:
            return StandardConsultationRequestDto.model_validate(request_data)
        except ValidationError as exc:
            raw_task_id = request_data.get("task_id")
            task_id = raw_task_id if isinstance(raw_task_id, str) else None
            raise StandardConsultationError(
                code=StandardConsultationErrorCode.STANDARD_CONTEXT_MISSING,
                operation=StandardConsultationOperation.GENERATE_DRAFT,
                message="graph state 中的标准问诊请求不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                task_id=task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc

    def _session_state_from_state(
        self,
        *,
        raw_request: Mapping[str, object],
        state: GraphState,
    ) -> StandardSessionStateDto:
        """从 graph state 读取标准问诊短期状态。

        :param raw_request: state 中的原始标准问诊请求。
        :param state: 当前图运行的只读 state 视图。
        :return: 标准问诊短期状态 DTO。
        """

        raw_session_state = raw_request.get("session_state") or state.get(
            "standard_session_state"
        )
        if isinstance(raw_session_state, dict):
            return StandardSessionStateDto.model_validate(raw_session_state)
        return StandardSessionStateDto()

    def _question_budget_from_state(
        self,
        *,
        raw_request: Mapping[str, object],
        state: GraphState,
    ) -> QuestionBudgetDto:
        """从 graph state 读取本轮问题预算。

        :param raw_request: state 中的原始标准问诊请求。
        :param state: 当前图运行的只读 state 视图。
        :return: 本轮问题预算 DTO。
        """

        raw_budget = raw_request.get("question_budget") or state.get("question_budget")
        if isinstance(raw_budget, dict):
            return QuestionBudgetDto.model_validate(raw_budget)
        raw_max_questions = _read_string(state.get("max_questions"))
        if raw_max_questions is not None and raw_max_questions.isdigit():
            return QuestionBudgetDto(max_questions=int(raw_max_questions))
        return QuestionBudgetDto()


__all__: tuple[str, ...] = ("StandardConsultationAgentGraphNode",)
