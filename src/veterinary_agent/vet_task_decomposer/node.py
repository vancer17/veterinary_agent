##################################################################################################
# 文件: src/veterinary_agent/vet_task_decomposer/node.py
# 作用: 提供 VetTaskDecomposer 到项目 GraphRuntime/LangGraph 节点契约的薄适配器。
# 边界: 只负责 graph state 与 DTO 转换，不自行调度后继节点、不实现 LLM 拆解或安全评估。
##################################################################################################

from collections.abc import Mapping
from typing import cast

from pydantic import ValidationError

from veterinary_agent.graph_runtime import (
    GraphNodeExecutionContext,
    GraphNodeResult,
    GraphState,
)
from veterinary_agent.vet_task_decomposer.dto import (
    AttachmentRefDto,
    JsonMap,
    VetTaskDecomposeRequestDto,
)
from veterinary_agent.vet_task_decomposer.enums import (
    VetTaskDecomposerErrorCode,
    VetTaskDecomposerOperation,
)
from veterinary_agent.vet_task_decomposer.errors import VetTaskDecomposerError
from veterinary_agent.vet_task_decomposer.service import VetTaskDecomposer


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


class VetTaskDecomposerGraphNode:
    """将 VetTaskDecomposer 服务接入 GraphRuntime 的单节点适配器。"""

    def __init__(
        self,
        *,
        decomposer: VetTaskDecomposer,
        output_state_key: str = "vet_task_decompose_result",
    ) -> None:
        """初始化任务拆解图节点。

        :param decomposer: VetTaskDecomposer 公共服务契约。
        :param output_state_key: 写入 graph business_state 的结果键名。
        :return: None。
        :raises ValueError: 当输出 state 键为空时抛出。
        """

        if not output_state_key.strip():
            raise ValueError("output_state_key 不得为空")
        self._decomposer = decomposer
        self._output_state_key = output_state_key.strip()

    async def __call__(
        self,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> GraphNodeResult:
        """读取 graph state 中的用户输入并写回任务拆解结果。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文。
        :return: 包含拆解结果、子任务和原始用户消息的节点状态更新。
        :raises VetTaskDecomposerError: 当 graph state 缺少请求或请求无法校验时抛出。
        """

        request = self._build_request_from_state(state=state, context=context)
        result = await self._decomposer.decompose(request)
        result_payload = cast(JsonMap, result.model_dump(mode="json"))
        task_payloads = [task.model_dump(mode="json") for task in result.tasks]
        return GraphNodeResult(
            state_patch={
                self._output_state_key: result_payload,
                "vet_sub_tasks": task_payloads,
                "task_decomposition_trace_summary": (
                    result.trace_summary.model_dump(mode="json")
                ),
                "original_user_message": request.user_message,
                "decomposition_status": result.status.value,
                "task_count": len(result.tasks),
                "task_types": [task.task_type.value for task in result.tasks],
            }
        )

    def _build_request_from_state(
        self,
        *,
        state: GraphState,
        context: GraphNodeExecutionContext,
    ) -> VetTaskDecomposeRequestDto:
        """从 graph state 和节点上下文构建严格拆解请求 DTO。

        :param state: 当前图运行的只读 state 视图。
        :param context: 当前节点执行上下文；身份字段以此为权威。
        :return: 已覆盖可信运行身份的拆解请求。
        :raises VetTaskDecomposerError: 当 state 缺少或包含非法请求时抛出。
        """

        raw_request = _as_mapping(state.get("request"))
        if raw_request is None:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INVALID_REQUEST,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="graph state 缺少 request",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
            )
        try:
            return VetTaskDecomposeRequestDto(
                request_id=context.request_id,
                trace_id=context.trace_id,
                run_id=context.run_id,
                session_id=context.session_id,
                user_id=context.user_id,
                current_pet_id=context.current_pet_id,
                user_message=self._extract_user_message(raw_request),
                attachments=self._build_attachment_refs(raw_request),
                params_version=context.params_version,
                config_snapshot_id=context.config_snapshot_id,
            )
        except ValidationError as exc:
            raise VetTaskDecomposerError(
                code=VetTaskDecomposerErrorCode.TASK_DECOMPOSE_INVALID_REQUEST,
                operation=VetTaskDecomposerOperation.VALIDATE_INPUT,
                message="graph state 中的任务拆解请求不符合契约",
                retryable=False,
                request_id=context.request_id,
                trace_id=context.trace_id,
                conflict_with={"validation_error_count": len(exc.errors())},
            ) from exc

    def _extract_user_message(self, raw_request: Mapping[str, object]) -> str:
        """从 GraphRuntime request 中提取用户文本原文。

        :param raw_request: GraphRuntime 初始 request 映射。
        :return: 按输入顺序拼接后的用户文本；无文本时返回空字符串。
        """

        text_parts: list[str] = []
        for raw_item in _as_list(raw_request.get("input")):
            item = _as_mapping(raw_item)
            if item is None:
                continue
            text = self._extract_text_from_input_item(item)
            if text:
                text_parts.append(text)
        return "\n".join(text_parts)

    def _extract_text_from_input_item(
        self,
        item: Mapping[str, object],
    ) -> str:
        """从单个输入项中提取全部 input_text 内容。

        :param item: 单个 GraphRuntime input item。
        :return: 当前输入项中的文本内容；无文本时返回空字符串。
        """

        text_parts: list[str] = []
        for raw_content in _as_list(item.get("content")):
            content = _as_mapping(raw_content)
            if content is None:
                continue
            if content.get("type") != "input_text":
                continue
            text = _read_string(content.get("text"))
            if text is not None:
                text_parts.append(text)
        return "\n".join(text_parts)

    def _build_attachment_refs(
        self,
        raw_request: Mapping[str, object],
    ) -> list[AttachmentRefDto]:
        """从 GraphRuntime request 中构建附件引用列表。

        :param raw_request: GraphRuntime 初始 request 映射。
        :return: 已按上传顺序归一化的附件引用列表。
        """

        attachments: list[AttachmentRefDto] = []
        for upload_order, raw_attachment in enumerate(
            _as_list(raw_request.get("attachments"))
        ):
            attachment = _as_mapping(raw_attachment)
            if attachment is None:
                continue
            attachment_id = _read_string(attachment.get("attachment_id"))
            mime_type = _read_string(attachment.get("mime_type"))
            if attachment_id is None or mime_type is None:
                continue
            attachments.append(
                AttachmentRefDto(
                    attachment_id=attachment_id,
                    mime_type=mime_type,
                    declared_type=_read_string(attachment.get("purpose")),
                    upload_order=upload_order,
                )
            )
        return attachments


__all__: tuple[str, ...] = ("VetTaskDecomposerGraphNode",)
