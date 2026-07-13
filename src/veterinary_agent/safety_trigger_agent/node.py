##################################################################################################
# 文件: src/veterinary_agent/safety_trigger_agent/node.py
# 作用: 提供 SafetyTriggerAgent 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 DTO 转换，不执行急症生成逻辑、不调度输出护栏或发布链路。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.safety_trigger_agent.dto import (
    JsonMap,
    SafetyTriggerRequestDto,
)
from veterinary_agent.safety_trigger_agent.enums import (
    SafetyTriggerErrorCode,
    SafetyTriggerOperation,
)
from veterinary_agent.safety_trigger_agent.errors import SafetyTriggerError
from veterinary_agent.safety_trigger_agent.service import SafetyTriggerAgent
from veterinary_agent.vet_context_builder import VetContextBundleDto


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


class SafetyTriggerAgentGraphNode:
    """将 SafetyTriggerAgent 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        agent: SafetyTriggerAgent,
        output_state_key: str = "safety_trigger_draft",
    ) -> None:
        """初始化急症图节点。

        :param agent: SafetyTriggerAgent 公共服务契约。
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
        """读取 graph state 中的急症请求并写回草稿结果。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 包含急症草稿、首发标记和 trace patch 的节点状态更新。
        :raises SafetyTriggerError: 当 state 缺少请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        draft = await self._agent.generate_draft(request)
        draft_payload = cast(JsonMap, draft.model_dump(mode="json"))
        return GraphNodeResult(
            state_patch={
                self._output_state_key: draft_payload,
                "safety_trigger_generation_status": draft.status.value,
                "safety_trigger_self_check": draft.self_check.model_dump(mode="json"),
                "safety_trigger_trace_patch": draft.trace_patch.model_dump(mode="json"),
                "safety_trigger_requires_first_segment": True,
                "safety_trigger_rag_invoked": False,
                "safety_trigger_retrieval_ids": [],
                "draft_response_ref": draft.draft_response_ref,
                "draft_response": draft.draft_response,
            }
        )

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> SafetyTriggerRequestDto:
        """从 graph state 和节点上下文构建严格急症请求。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的急症请求。
        :raises SafetyTriggerError: 当 state 缺少或包含非法请求时抛出。
        """

        raw_request = _as_mapping(state.get("safety_trigger_request")) or {}
        raw_context = raw_request.get("context") or state.get("context_bundle")
        if not isinstance(raw_context, dict):
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
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
            or "ACUTE_EVENT",
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
            "params_version": context.params_version,
            "config_snapshot_id": context.config_snapshot_id,
        }
        try:
            return SafetyTriggerRequestDto.model_validate(request_data)
        except ValidationError as exc:
            raw_task_id = request_data.get("task_id")
            task_id = raw_task_id if isinstance(raw_task_id, str) else None
            raise SafetyTriggerError(
                code=SafetyTriggerErrorCode.SAFETY_TRIGGER_CONTEXT_MISSING,
                operation=SafetyTriggerOperation.GENERATE_DRAFT,
                message="graph state 中的急症请求不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                task_id=task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc


__all__: tuple[str, ...] = ("SafetyTriggerAgentGraphNode",)
