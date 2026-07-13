##################################################################################################
# 文件: src/veterinary_agent/nonmedical_pet_care_agent/node.py
# 作用: 提供 NonmedicalPetCareAgent 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 DTO 转换，不执行非医疗建议生成逻辑、不调度输出护栏或发布链路。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.nonmedical_pet_care_agent.contract import (
    NonmedicalPetCareAgent,
)
from veterinary_agent.nonmedical_pet_care_agent.dto import (
    JsonMap,
    NonmedicalAdviceRequestDto,
)
from veterinary_agent.nonmedical_pet_care_agent.enums import (
    NonmedicalAgentErrorCode,
    NonmedicalAgentOperation,
)
from veterinary_agent.nonmedical_pet_care_agent.errors import NonmedicalAgentError
from veterinary_agent.vet_context_builder import VetContextBundleDto


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


class NonmedicalPetCareAgentGraphNode:
    """将 NonmedicalPetCareAgent 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        agent: NonmedicalPetCareAgent,
        output_state_key: str = "nonmedical_advice_draft",
    ) -> None:
        """初始化非医疗养宠图节点。

        :param agent: NonmedicalPetCareAgent 公共服务契约。
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
        """读取 graph state 中的非医疗请求并写回草稿结果。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 包含非医疗草稿、建议计划、约束、RAG 摘要和 trace patch 的节点状态更新。
        :raises NonmedicalAgentError: 当 state 缺少请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        draft = await self._agent.generate_draft(request)
        draft_payload = cast(JsonMap, draft.model_dump(mode="json"))
        state_patch: dict[str, object] = {
            self._output_state_key: draft_payload,
            "nonmedical_generation_status": draft.status.value,
            "nonmedical_advice_plan": draft.advice_plan.model_dump(mode="json"),
            "nonmedical_advice_constraints": [
                constraint.model_dump(mode="json")
                for constraint in draft.advice_constraints
            ],
            "nonmedical_personalization_plan": (
                draft.personalization_plan.model_dump(mode="json")
            ),
            "nonmedical_rag_summary": draft.rag_summary.model_dump(mode="json"),
            "nonmedical_self_check": draft.self_check.model_dump(mode="json"),
            "nonmedical_trace_patch": draft.trace_patch.model_dump(mode="json"),
            "nonmedical_rag_invoked": draft.rag_summary.rag_invoked,
            "nonmedical_retrieval_ids": list(draft.rag_summary.retrieval_ids),
            "draft_response_ref": draft.draft_response_ref,
            "draft_response": draft.draft_response,
        }
        if draft.status.value == "NEEDS_SAFETY_ESCALATION":
            state_patch["nonmedical_escalation_requested"] = True
            state_patch["escalation_request"] = {
                "reason_code": "NONMED_SAFETY_ESCALATION_REQUIRED",
                "target_profile": "safety_trigger",
                "summary": "非医疗链路收到 SAF-01 或 L3 强信号，建议升级处理。",
            }
        return GraphNodeResult(state_patch=state_patch)

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> NonmedicalAdviceRequestDto:
        """从 graph state 和节点上下文构建严格非医疗请求。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的非医疗请求。
        :raises NonmedicalAgentError: 当 state 缺少或包含非法请求时抛出。
        """

        raw_request = _as_mapping(state.get("nonmedical_advice_request")) or {}
        raw_context = raw_request.get("context") or state.get("context_bundle")
        if not isinstance(raw_context, dict):
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
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
            or "GENERAL_QA",
            "normalized_query": raw_request.get("normalized_query")
            or state.get("normalized_query")
            or state.get("original_user_message")
            or "",
            "generation_profile": raw_request.get("generation_profile")
            or (
                context_bundle.generation_profile.value
                if context_bundle.generation_profile is not None
                else None
            ),
            "executor_key": raw_request.get("executor_key")
            or context_bundle.executor_key.value,
            "assessment_summary": raw_request.get("assessment_summary")
            or state.get("assessment_summary")
            or state.get("vet_input_assessment_result")
            or {},
            "context": context_bundle,
            "params_version": context.params_version,
            "config_snapshot_id": context.config_snapshot_id,
        }
        try:
            return NonmedicalAdviceRequestDto.model_validate(request_data)
        except ValidationError as exc:
            raw_task_id = request_data.get("task_id")
            task_id = raw_task_id if isinstance(raw_task_id, str) else None
            raise NonmedicalAgentError(
                code=NonmedicalAgentErrorCode.NONMED_CONTEXT_MISSING,
                operation=NonmedicalAgentOperation.GENERATE_DRAFT,
                message="graph state 中的非医疗请求不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                task_id=task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc


__all__: tuple[str, ...] = ("NonmedicalPetCareAgentGraphNode",)
