"""
文件：src/vet_agent/services/trace.py
作用：承载业务服务、记忆、报告解析、权限与治理逻辑。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vet_agent import AgentTurnRequest, AgentTurnResponse
from vet_agent.stores import JsonDocumentStore


class LogicTraceStore:
    def __init__(self, store: JsonDocumentStore) -> None:
        """初始化当前对象。

        :param store: 参数 store。
        :return: 无返回值。
        """
        self.store = store

    async def write_turn(self, request: AgentTurnRequest, response: AgentTurnResponse) -> None:
        """执行 write_turn 业务逻辑。

        :param request: 请求对象。
        :param response: 响应对象。
        :return: 返回函数执行结果。
        """
        medical = bool(response.safety_signals) or any(word in response.output_text for word in ("分诊", "就医", "用药", "症状"))
        self.store.append_jsonl(
            {
                "at": datetime.now(UTC).isoformat(),
                "request_id": request.request_context.request_id,
                "trace_id": request.request_context.trace_id,
                "user_id": request.trusted_identity.user_id,
                "session_id": request.trusted_identity.session_id,
                "pet_id": request.trusted_identity.pet_id,
                "medical": medical,
                "safety_signals": [signal.model_dump() for signal in response.safety_signals],
                "evidence": [item.model_dump() for item in response.evidence],
                "reasoning_display": response.reasoning_display.model_dump(mode="json")
                if response.reasoning_display
                else None,
                "advice": response.output_text,
                "metadata": {"status": response.status, **response.metadata},
            }
        )

    async def write_error(self, request_id: str | None, trace_id: str | None, error: str, details: dict[str, Any] | None = None) -> None:
        """执行 write_error 业务逻辑。

        :param request_id: 请求标识。
        :param trace_id: 链路追踪标识。
        :param error: 参数 error。
        :param details: 错误详情。
        :return: 返回函数执行结果。
        """
        self.store.append_jsonl(
            {
                "at": datetime.now(UTC).isoformat(),
                "request_id": request_id,
                "trace_id": trace_id,
                "error": error,
                "details": details or {},
            }
        )
