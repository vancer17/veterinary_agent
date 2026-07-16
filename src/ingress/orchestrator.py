from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from .dto import AgentTurnRequest
from .errors import OrchestratorUnavailableError


class Orchestrator(Protocol):
    async def is_ready(self) -> bool:
        ...

    async def create_turn(self, request: AgentTurnRequest) -> Mapping[str, Any]:
        ...

    def stream_turn(
        self, request: AgentTurnRequest
    ) -> AsyncIterator[Mapping[str, Any]]:
        ...


class UnavailableOrchestrator:
    async def is_ready(self) -> bool:
        return False

    async def create_turn(self, request: AgentTurnRequest) -> Mapping[str, Any]:
        raise OrchestratorUnavailableError(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
        )

    async def stream_turn(
        self, request: AgentTurnRequest
    ) -> AsyncIterator[Mapping[str, Any]]:
        raise OrchestratorUnavailableError(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
        )
        yield {}


_orchestrator: Orchestrator | None = None


def set_orchestrator(orchestrator: Orchestrator) -> None:
    global _orchestrator
    _orchestrator = orchestrator


async def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        from vet_agent.container import get_container
        from vet_agent.ingress_adapter import VetAgentIngressOrchestrator

        _orchestrator = VetAgentIngressOrchestrator(get_container())
    return _orchestrator
