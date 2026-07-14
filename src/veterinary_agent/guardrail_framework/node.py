##################################################################################################
# 文件: src/veterinary_agent/guardrail_framework/node.py
# 作用: 提供 GuardrailFramework 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 Guardrail DTO 转换，不实现业务安全规则、不调度业务生成或发布。
##################################################################################################

from collections.abc import Mapping

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.guardrail_framework.dto import (
    GuardrailRunContextDto,
    GuardrailRunRequestDto,
)
from veterinary_agent.guardrail_framework.enums import (
    GuardrailFrameworkErrorCode,
    GuardrailFrameworkOperation,
    GuardrailStage,
)
from veterinary_agent.guardrail_framework.errors import GuardrailFrameworkError
from veterinary_agent.guardrail_framework.service import GuardrailFramework


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


class GuardrailFrameworkGraphNode:
    """将 GuardrailFramework 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        guardrail_framework: GuardrailFramework,
        stage: GuardrailStage,
        input_state_key: str = "guardrail_request",
        output_state_key: str = "guardrail_result",
    ) -> None:
        """初始化 GuardrailFramework 图节点。

        :param guardrail_framework: GuardrailFramework 公共服务契约。
        :param stage: 当前图节点固定执行的护栏阶段。
        :param input_state_key: 从 graph state 读取请求的键名。
        :param output_state_key: 写回 graph state 的结果键名。
        :return: None。
        :raises ValueError: 当 state 键名为空时抛出。
        """

        if not input_state_key.strip():
            raise ValueError("input_state_key 不得为空")
        if not output_state_key.strip():
            raise ValueError("output_state_key 不得为空")
        self._guardrail_framework = guardrail_framework
        self._stage = stage
        self._input_state_key = input_state_key.strip()
        self._output_state_key = output_state_key.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 并执行护栏阶段。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文。
        :return: 包含护栏结果和状态摘要的节点结果。
        :raises GuardrailFrameworkError: 当 state 中的请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        result = await self._guardrail_framework.run_guardrail_stage(request)
        return GraphNodeResult(
            state_patch={
                self._output_state_key: result.model_dump(mode="json"),
                "guardrail_status": result.status.value,
                "guardrail_publish_allowed": result.publish_allowed,
                "guardrail_trace_degraded": result.trace_degraded,
            }
        )

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GuardrailRunRequestDto:
        """从 graph state 和节点上下文构建护栏运行请求。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的护栏运行请求。
        :raises GuardrailFrameworkError: 当 state 中请求缺失或无法校验时抛出。
        """

        raw_request = _as_mapping(state.get(self._input_state_key))
        if raw_request is None:
            raw_request = {}
        raw_context = _as_mapping(raw_request.get("context")) or {}
        task_id = _read_string(raw_context.get("task_id")) or _read_string(
            state.get("task_id")
        )
        if task_id is None:
            task_id = "task"
        try:
            return GuardrailRunRequestDto(
                stage=self._stage,
                context=GuardrailRunContextDto(
                    run_id=context.run_id,
                    trace_id=context.trace_id,
                    request_id=context.request_id,
                    session_id=context.session_id,
                    user_id=context.user_id,
                    pet_id=context.current_pet_id,
                    task_id=task_id,
                    segment_id=_read_string(raw_context.get("segment_id"))
                    or _read_string(state.get("segment_id")),
                    generation_profile=_read_string(
                        raw_context.get("generation_profile")
                    )
                    or _read_string(state.get("generation_profile")),
                    params_version=context.params_version,
                    config_snapshot_id=context.config_snapshot_id,
                ),
                task_input=dict(_as_mapping(raw_request.get("task_input")) or {}),
                candidate_text_ref=_read_string(raw_request.get("candidate_text_ref")),
                runtime_metadata=dict(
                    _as_mapping(raw_request.get("runtime_metadata")) or {}
                ),
            )
        except ValidationError as exc:
            raise GuardrailFrameworkError(
                code=GuardrailFrameworkErrorCode.GUARDRAIL_OUTPUT_SCHEMA_INVALID,
                operation=GuardrailFrameworkOperation.RUN_GUARDRAIL_STAGE,
                message="graph state 中的护栏请求不符合契约",
                retryable=False,
                stage=self._stage,
                request_id=context.request_id,
                trace_id=context.trace_id,
                run_id=context.run_id,
                task_id=task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc


__all__: tuple[str, ...] = ("GuardrailFrameworkGraphNode",)
