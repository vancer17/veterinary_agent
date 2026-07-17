"""
文件：src/vet_agent/ingress_adapter.py
作用：提供兽医 Agent 项目的业务实现。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from ingress.dto import AgentTurnRequest as IngressAgentTurnRequest

from vet_agent import Container
from vet_agent import (
    AgentTurnRequest,
    AttachmentRef,
    InputItem,
    RequestContext,
    TrustedIdentity,
    TurnOptions,
    VetContext,
)


class VetAgentIngressOrchestrator:
    def __init__(self, container: Container) -> None:
        """初始化当前对象。

        :param container: 参数 container。
        :return: 无返回值。
        """
        self.container = container

    async def is_ready(self) -> bool:
        """检查当前组件是否就绪。

        :return: 返回函数执行结果。
        """
        return self.container.ready

    async def create_turn(self, request: IngressAgentTurnRequest) -> Mapping[str, Any]:
        """创建一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        core_request = self._translate_request(request)
        response = await self.container.orchestrator.run_turn(core_request)
        return self._to_external_turn(response.model_dump(mode="json"))

    async def stream_turn(self, request: IngressAgentTurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        """以流式事件形式执行一个 Agent 对话回合。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        core_request = self._translate_request(request)
        response = await self.container.orchestrator.run_turn(core_request)
        external = self._to_external_turn(response.model_dump(mode="json"))
        yield {
            "event": "turn.started",
            "id": external["id"],
            "request_id": external["request_id"],
            "trace_id": external["trace_id"],
        }
        turn_reasoning = external.get("reasoning_display")
        if turn_reasoning and not turn_reasoning.get("segment_id"):
            yield {
                "event": "reasoning_display.started",
                "projection_id": turn_reasoning["projection_id"],
                "segment_id": None,
                "title": turn_reasoning.get("title"),
            }
            for chunk in self._chunks(turn_reasoning["text"], size=64):
                yield {
                    "event": "reasoning_display.delta",
                    "projection_id": turn_reasoning["projection_id"],
                    "text_delta": chunk,
                }
            yield {
                "event": "reasoning_display.completed",
                "reasoning_display": turn_reasoning,
            }
        for segment in external["segments"]:
            reasoning = segment.get("reasoning_display")
            if reasoning:
                yield {
                    "event": "reasoning_display.started",
                    "projection_id": reasoning["projection_id"],
                    "segment_id": reasoning.get("segment_id"),
                    "title": reasoning.get("title"),
                }
                for chunk in self._chunks(reasoning["text"], size=64):
                    yield {
                        "event": "reasoning_display.delta",
                        "projection_id": reasoning["projection_id"],
                        "text_delta": chunk,
                    }
                yield {
                    "event": "reasoning_display.completed",
                    "reasoning_display": reasoning,
                }

            yield {
                "event": "segment.started",
                "segment_id": segment["segment_id"],
                "index": segment["index"],
                "type": segment["type"],
                "title": segment["title"],
            }
            for chunk in self._chunks(segment["output_text"], size=80):
                yield {
                    "event": "segment.delta",
                    "segment_id": segment["segment_id"],
                    "delta": {"type": "output_text_delta", "text": chunk},
                }
            yield {
                "event": "segment.completed",
                "segment_id": segment["segment_id"],
                "status": segment["status"],
            }
        yield {
            "event": "turn.completed",
            "id": external["id"],
            "status": external["status"],
            "request_id": external["request_id"],
            "trace_id": external["trace_id"],
        }

    def _translate_request(self, request: IngressAgentTurnRequest) -> AgentTurnRequest:
        """执行 _translate_request 内部辅助逻辑。

        :param request: 请求对象。
        :return: 返回函数执行结果。
        """
        extra = request.turn_options.model_extra or {}
        max_followup_questions = getattr(request.turn_options, "max_followup_questions", None)
        return AgentTurnRequest(
            request_context=RequestContext(
                request_id=request.request_context.request_id,
                trace_id=request.request_context.trace_id,
                response_mode=request.request_context.response_mode,
                received_at=request.request_context.received_at,
            ),
            trusted_identity=TrustedIdentity(
                user_id=request.trusted_identity.user_id,
                session_id=request.trusted_identity.session_id,
                pet_id=request.trusted_identity.pet_id,
            ),
            input=self._input_items(request.input),
            attachments=[
                AttachmentRef(
                    attachment_id=item.attachment_id,
                    mime_type=item.mime_type,
                    purpose=item.purpose,
                    storage_ref=item.storage_ref,
                    metadata=getattr(item, "metadata", {}) or {},
                )
                for item in request.attachments
            ],
            metadata=request.metadata,
            model=request.model,
            turn_options=TurnOptions(
                idempotency_key=request.turn_options.idempotency_key,
                max_followup_questions=int(max_followup_questions or extra.get("max_followup_questions", 3)),
            ),
            vet_context=VetContext(
                user_id=request.vet_context.user_id,
                session_id=request.vet_context.session_id,
                pet_id=request.vet_context.pet_id,
                pet_info=request.vet_context.pet_info,
            ),
        )

    def _input_items(self, value: Any) -> list[InputItem]:
        """执行 _input_items 内部辅助逻辑。

        :param value: 待处理值。
        :return: 返回函数执行结果。
        """
        if value is None:
            return []
        if isinstance(value, str):
            return [InputItem(role="user", type="message", content=value)]
        if isinstance(value, dict):
            return [InputItem(**value)]
        items: list[InputItem] = []
        for item in value:
            if isinstance(item, str):
                items.append(InputItem(role="user", type="message", content=item))
            elif isinstance(item, dict):
                items.append(InputItem(**item))
        return items

    def _to_external_turn(self, response: Mapping[str, Any]) -> dict[str, Any]:
        """执行内部数据格式转换。

        :param response: 响应对象。
        :return: 返回函数执行结果。
        """
        output_text = str(response.get("output_text") or "")
        external_segments = [
            self._to_external_segment(segment, index)
            for index, segment in enumerate(list(response.get("segments") or []))
        ]
        return {
            "id": response.get("id"),
            "object": "agent.turn",
            "created_at": response.get("created_at"),
            "request_id": response.get("request_id"),
            "trace_id": response.get("trace_id"),
            "status": response.get("status"),
            "model": response.get("model"),
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": output_text}],
                }
            ],
            "segments": external_segments,
            "reasoning_display": response.get("reasoning_display"),
            "vet_result": response.get("vet_result") or {},
            "metadata": response.get("metadata") or {},
            "output_text": output_text,
            "evidence": response.get("evidence") or [],
            "safety_signals": response.get("safety_signals") or [],
        }

    def _to_external_segment(self, segment: Mapping[str, Any], index: int) -> dict[str, Any]:
        """执行内部数据格式转换。

        :param segment: 参数 segment。
        :param index: 序号。
        :return: 返回函数执行结果。
        """
        output_text = str(segment.get("output_text") or segment.get("content") or "")
        return {
            "segment_id": segment.get("segment_id") or f"seg_{index + 1:03d}",
            "index": index,
            "type": segment.get("type") or "medical_consultation",
            "title": segment.get("title") or "兽医 Agent 回复",
            "status": segment.get("status") or "completed",
            "output_text": output_text,
            "references": segment.get("references") or [],
            "reasoning_display": segment.get("reasoning_display"),
            "evidence": segment.get("evidence") or [],
        }

    def _chunks(self, text: str, size: int):
        """执行 _chunks 内部辅助逻辑。

        :param text: 待处理文本。
        :param size: 分片大小。
        :return: 返回函数执行结果。
        """
        for start in range(0, len(text), size):
            yield text[start : start + size]
