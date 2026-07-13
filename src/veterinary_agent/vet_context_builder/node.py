##################################################################################################
# 文件: src/veterinary_agent/vet_context_builder/node.py
# 作用: 提供 VetContextBuilder 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 state 与 DTO 转换，不自行调度后继节点、不实现来源读取或业务判决。
##################################################################################################

from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.vet_context_builder.dto import (
    JsonMap,
    VetContextBuildRequestDto,
)
from veterinary_agent.vet_context_builder.enums import (
    VetContextBuilderErrorCode,
    VetContextBuilderOperation,
)
from veterinary_agent.vet_context_builder.errors import VetContextBuilderError
from veterinary_agent.vet_context_builder.mapping import to_agent_prompt_blocks
from veterinary_agent.vet_context_builder.service import VetContextBuilder


class VetContextBuilderGraphNode:
    """将 VetContextBuilder 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        builder: VetContextBuilder,
        output_state_key: str = "context_bundle",
    ) -> None:
        """初始化上下文构建图节点。

        :param builder: VetContextBuilder 公共服务契约。
        :param output_state_key: 写入 graph business_state 的 bundle 键名。
        :return: None。
        :raises ValueError: 当输出 state 键为空时抛出。
        """

        if not output_state_key.strip():
            raise ValueError("output_state_key 不得为空")
        self._builder = builder
        self._output_state_key = output_state_key.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 中的构建请求并写回上下文 bundle。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文。
        :return: 包含领域 bundle、通用 prompt 块和槽位覆盖的节点状态更新。
        :raises VetContextBuilderError: 当 graph state 缺少构建请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        bundle = await self._builder.build(request)
        bundle_payload: JsonMap = cast(
            JsonMap,
            bundle.model_dump(mode="json"),
        )
        state_patch: dict[str, object] = {
            self._output_state_key: bundle_payload,
            "prompt_blocks": [
                block.model_dump(mode="json")
                for block in to_agent_prompt_blocks(bundle)
            ],
            "slot_coverage": bundle.slot_coverage.model_dump(mode="json"),
            "compression_audit": bundle.compression_audit.model_dump(mode="json"),
            "adapter_invoked": True,
        }
        return GraphNodeResult(state_patch=state_patch)

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> VetContextBuildRequestDto:
        """从 graph state 和节点上下文构建严格请求 DTO。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前图节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的上下文构建请求。
        :raises VetContextBuilderError: 当 state 缺少或包含非法构建请求时抛出。
        """

        raw_request = state.get("context_build_request")
        if not isinstance(raw_request, dict):
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_INVALID_REQUEST,
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="graph state 缺少 context_build_request",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                task_id=None,
            )
        request_data: dict[str, object] = {
            **raw_request,
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "run_id": context.run_id,
            "session_id": context.session_id,
            "user_id": context.user_id,
            "current_pet_id": context.current_pet_id,
            "params_version": context.params_version,
            "config_snapshot_id": context.config_snapshot_id,
        }
        try:
            return VetContextBuildRequestDto.model_validate(request_data)
        except ValidationError as exc:
            raw_task_id = request_data.get("task_id")
            task_id = raw_task_id if isinstance(raw_task_id, str) else None
            raise VetContextBuilderError(
                code=VetContextBuilderErrorCode.CONTEXT_INVALID_REQUEST,
                operation=VetContextBuilderOperation.BUILD_CONTEXT,
                message="graph state 中的 context_build_request 不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                task_id=task_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc


__all__: tuple[str, ...] = ("VetContextBuilderGraphNode",)
