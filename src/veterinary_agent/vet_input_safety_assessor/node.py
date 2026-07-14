##################################################################################################
# 文件: src/veterinary_agent/vet_input_safety_assessor/node.py
# 作用: 提供 VetInputSafetyAssessor 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 DTO 转换，不自行调度后继节点、不实现信号匹配或业务裁决。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.vet_input_safety_assessor.dto import (
    BatchVetInputAssessmentRequestDto,
    JsonMap,
    LightweightAssessmentContextDto,
)
from veterinary_agent.vet_input_safety_assessor.enums import (
    VetInputSafetyAssessorErrorCode,
    VetInputSafetyAssessorOperation,
)
from veterinary_agent.vet_input_safety_assessor.errors import (
    VetInputSafetyAssessorError,
)
from veterinary_agent.vet_input_safety_assessor.service import VetInputSafetyAssessor
from veterinary_agent.vet_task_decomposer import VetSubTaskDto


def _as_mapping(value: object) -> Mapping[str, object] | None:
    """将未知值安全读取为字符串键映射。

    :param value: 需要读取的未知值。
    :return: 字符串键映射；无法读取时返回 None。
    """

    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _as_list(value: object) -> list[object]:
    """将未知值安全读取为列表。

    :param value: 需要读取的未知值。
    :return: 若输入为列表或元组则返回普通列表，否则返回空列表。
    """

    if isinstance(value, list | tuple):
        return list(value)
    return []


def _read_string(value: object) -> str | None:
    """从未知值中读取非空字符串。

    :param value: 需要读取的未知值。
    :return: 去除首尾空白后的非空字符串；无法读取时返回 None。
    """

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class VetInputSafetyAssessorGraphNode:
    """将 VetInputSafetyAssessor 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        assessor: VetInputSafetyAssessor,
        output_state_key: str = "vet_input_assessment_results",
    ) -> None:
        """初始化输入安全评估图节点。

        :param assessor: VetInputSafetyAssessor 公共服务契约。
        :param output_state_key: 写入 graph business_state 的结果键名。
        :return: None。
        :raises ValueError: 当输出 state 键为空时抛出。
        """

        if not output_state_key.strip():
            raise ValueError("output_state_key 不得为空")
        self._assessor = assessor
        self._output_state_key = output_state_key.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 中的子任务并写回输入安全评估结果。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文。
        :return: 包含评估结果、摘要映射和上下文构建请求的节点状态更新。
        :raises VetInputSafetyAssessorError: 当 graph state 缺少请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        result = await self._assessor.batch_assess(request)
        result_payload = cast(JsonMap, result.model_dump(mode="json"))
        results = [item.model_dump(mode="json") for item in result.results]
        summaries_by_task_id = {
            item.task_id: item.assessment_summary for item in result.results
        }
        context_requests = self._build_context_requests(
            request=request,
            result_payloads=results,
        )
        state_patch: dict[str, object] = {
            self._output_state_key: results,
            "vet_input_assessment_batch_result": result_payload,
            "vet_input_assessment_result_by_task_id": {
                item["task_id"]: item for item in results
            },
            "assessment_summary_by_task_id": summaries_by_task_id,
            "context_build_requests": context_requests,
            "input_safety_status": result.status.value,
            "input_safety_trace_delivery_status": result.trace_delivery_status.value,
        }
        if len(results) == 1:
            state_patch["vet_input_assessment_result"] = results[0]
            state_patch["assessment_summary"] = result.results[0].assessment_summary
            state_patch["context_build_request"] = context_requests[0]
        return GraphNodeResult(state_patch=state_patch)

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> BatchVetInputAssessmentRequestDto:
        """从 graph state 和节点上下文构建严格批量评估请求 DTO。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的批量评估请求。
        :raises VetInputSafetyAssessorError: 当 state 缺少或包含非法请求时抛出。
        """

        try:
            tasks = self._read_tasks_from_state(state=state, context=context)
            light_context = self._read_light_context(state=state)
            return BatchVetInputAssessmentRequestDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                run_id=context.run_id,
                session_id=context.session_id,
                user_id=context.user_id,
                current_pet_id=context.current_pet_id,
                tasks=tasks,
                light_context=light_context,
                original_user_message=_read_string(state.get("original_user_message"))
                or "",
                params_version=context.params_version,
                config_snapshot_id=context.config_snapshot_id,
            )
        except ValidationError as exc:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="graph state 中的输入安全评估请求不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc

    def _read_tasks_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> list[VetSubTaskDto]:
        """从 graph state 中读取并校验子任务列表。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文。
        :return: 已完成 DTO 校验的子任务列表。
        :raises VetInputSafetyAssessorError: 当 state 中缺少子任务列表时抛出。
        :raises ValidationError: 当子任务 DTO 校验失败时抛出。
        """

        raw_tasks = _as_list(state.get("vet_sub_tasks"))
        if not raw_tasks:
            raise VetInputSafetyAssessorError(
                code=VetInputSafetyAssessorErrorCode.INPUT_ASSESS_INVALID_REQUEST,
                operation=VetInputSafetyAssessorOperation.BATCH_ASSESS_INPUT,
                message="graph state 缺少 vet_sub_tasks",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
        return [VetSubTaskDto.model_validate(raw_task) for raw_task in raw_tasks]

    def _read_light_context(
        self,
        *,
        state: GraphState,
    ) -> LightweightAssessmentContextDto:
        """从 graph state 中读取轻量消歧上下文。

        :param state: 当前图运行的只读 state 视图。
        :return: 已完成 DTO 校验的轻量上下文；缺失时返回默认空上下文。
        :raises ValidationError: 当轻量上下文 DTO 校验失败时抛出。
        """

        raw_context = _as_mapping(state.get("light_assessment_context"))
        if raw_context is None:
            raw_context = _as_mapping(state.get("light_context"))
        if raw_context is None:
            return LightweightAssessmentContextDto()
        return LightweightAssessmentContextDto.model_validate(raw_context)

    def _build_context_requests(
        self,
        *,
        request: BatchVetInputAssessmentRequestDto,
        result_payloads: list[JsonMap],
    ) -> list[JsonMap]:
        """根据输入安全评估结果构建 VetContextBuilder 请求载荷。

        :param request: 当前批量评估请求。
        :param result_payloads: 已序列化的评估结果列表。
        :return: 可写入 graph state 的上下文构建请求列表。
        """

        tasks_by_id = {task.task_id: task for task in request.tasks}
        context_requests: list[JsonMap] = []
        for result in result_payloads:
            task_id = str(result["task_id"])
            task = tasks_by_id[task_id]
            context_requests.append(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type.value,
                    "normalized_query": task.normalized_query,
                    "generation_profile": result.get("generation_profile"),
                    "route": result["route"],
                    "executor_key": result["executor_key"],
                    "compression_strategy": result["compression_strategy"],
                    "audit_tier": result["audit_tier_floor"],
                    "assessment_summary": result["assessment_summary"],
                    "observed_facts": [],
                }
            )
        return context_requests


__all__: tuple[str, ...] = ("VetInputSafetyAssessorGraphNode",)
