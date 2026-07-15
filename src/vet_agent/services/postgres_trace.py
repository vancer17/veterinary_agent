from __future__ import annotations

from typing import Any

from src.vet_agent.contracts import AgentTurnRequest, AgentTurnResponse
from src.vet_agent.db.models import LogicTraceModel
from src.vet_agent.db.session import make_session_factory


class PostgresLogicTraceStore:
    def __init__(self, database_url: str) -> None:
        self.session_factory = make_session_factory(database_url)

    async def write_turn(self, request: AgentTurnRequest, response: AgentTurnResponse) -> None:
        medical = bool(response.safety_signals) or any(word in response.output_text for word in ("分诊", "就医", "用药", "症状"))
        payload = {
            "safety_signals": [signal.model_dump() for signal in response.safety_signals],
            "evidence": [item.model_dump() for item in response.evidence],
            "reasoning_display": response.reasoning_display.model_dump(mode="json")
            if response.reasoning_display
            else None,
            "advice": response.output_text,
            "metadata": {"status": response.status, **response.metadata},
        }
        with self.session_factory.begin() as session:
            session.add(
                LogicTraceModel(
                    request_id=request.request_context.request_id,
                    trace_id=request.request_context.trace_id,
                    user_id=request.trusted_identity.user_id,
                    session_id=request.trusted_identity.session_id,
                    pet_id=request.trusted_identity.pet_id,
                    medical=medical,
                    payload=payload,
                )
            )

    async def write_error(self, request_id: str | None, trace_id: str | None, error: str, details: dict[str, Any] | None = None) -> None:
        with self.session_factory.begin() as session:
            session.add(
                LogicTraceModel(
                    request_id=request_id,
                    trace_id=trace_id,
                    medical=False,
                    payload={"error": error, "details": details or {}},
                )
            )
