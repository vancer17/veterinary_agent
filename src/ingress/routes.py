from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from .dto import AgentTurnRequest, HealthResponse, IngressRequest, ReadyResponse
from .errors import (
    ErrorResponse,
    InvalidRequestError,
    OrchestratorTimeoutError,
    OrchestratorUnavailableError,
)
from .orchestrator import Orchestrator, get_orchestrator
from src.vet_agent.container import get_container
from src.vet_agent.contracts import TrustedIdentity as CoreTrustedIdentity


router = APIRouter()


ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ErrorResponse}},
)
async def ready(
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> ReadyResponse:
    orchestrator_ready = await orchestrator.is_ready()
    if not orchestrator_ready:
        raise OrchestratorUnavailableError(details={"checks": _ready_checks(False)})
    return ReadyResponse(checks=_ready_checks(True))


@router.post("/agent/turns", response_model=None, responses=ERROR_RESPONSES)
async def create_agent_turn(
    payload: IngressRequest,
    request: Request,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> JSONResponse | StreamingResponse:
    return await _dispatch_turn(payload, request, orchestrator)


@router.post("/openai/v1/responses", response_model=None, responses=ERROR_RESPONSES)
async def create_openai_response(
    payload: IngressRequest,
    request: Request,
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> JSONResponse | StreamingResponse:
    return await _dispatch_turn(payload, request, orchestrator, openai_compatible=True)


async def _dispatch_turn(
    payload: IngressRequest,
    request: Request,
    orchestrator: Orchestrator,
    *,
    openai_compatible: bool = False,
) -> JSONResponse | StreamingResponse:
    _apply_header_ids(payload, request)
    turn_request = payload.to_agent_turn_request(source_path=request.url.path)
    await _authorize_turn(payload, request)

    if not await orchestrator.is_ready():
        raise OrchestratorUnavailableError(
            request_id=turn_request.request_context.request_id,
            trace_id=turn_request.request_context.trace_id,
        )

    if payload.stream:
        return StreamingResponse(
            _stream_events(orchestrator, turn_request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                **_trace_headers(turn_request),
            },
        )

    try:
        response = await orchestrator.create_turn(turn_request)
    except TimeoutError as exc:
        raise OrchestratorTimeoutError(
            request_id=turn_request.request_context.request_id,
            trace_id=turn_request.request_context.trace_id,
        ) from exc

    content = _to_openai_response(response) if openai_compatible else response
    return JSONResponse(
        content=jsonable_encoder(content),
        headers=_trace_headers(turn_request),
    )


async def _stream_events(
    orchestrator: Orchestrator,
    turn_request: AgentTurnRequest,
) -> AsyncIterator[str]:
    try:
        async for event in orchestrator.stream_turn(turn_request):
            yield _to_sse(event)
    except TimeoutError as exc:
        error = OrchestratorTimeoutError(
            request_id=turn_request.request_context.request_id,
            trace_id=turn_request.request_context.trace_id,
        )
        yield _to_sse(
            {
                "event": "turn.failed",
                "code": error.code.value,
                "message": error.message,
                "request_id": turn_request.request_context.request_id,
                "trace_id": turn_request.request_context.trace_id,
            }
        )
        return


def _to_sse(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event") or event.get("type") or "message")
    if "event" in event:
        payload = {key: value for key, value in event.items() if key != "event"}
    else:
        payload = event
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _to_openai_response(response: Mapping[str, Any]) -> dict[str, Any]:
    output_text = str(response.get("output_text") or "")
    return {
        "id": response.get("id"),
        "object": "response",
        "created_at": response.get("created_at"),
        "status": response.get("status"),
        "model": response.get("model"),
        "output": response.get("output")
        or [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
        "segments": response.get("segments") or [],
        "reasoning_display": response.get("reasoning_display"),
        "vet_result": response.get("vet_result") or {},
        "metadata": {
            "request_id": response.get("request_id"),
            "trace_id": response.get("trace_id"),
            **dict(response.get("metadata") or {}),
        },
        "output_text": output_text,
    }


def _apply_header_ids(payload: IngressRequest, request: Request) -> None:
    header_request_id = _header_value(request, "x-request-id")
    header_trace_id = _header_value(request, "x-trace-id")
    if header_request_id and payload.request_id and header_request_id != payload.request_id:
        raise InvalidRequestError(
            "X-Request-ID conflicts with request_id",
            request_id=payload.request_id,
            trace_id=payload.trace_id or header_trace_id or payload.request_id,
            details=[{"field": "request_id", "reason": "conflicts_with_header"}],
        )
    if header_trace_id and payload.trace_id and header_trace_id != payload.trace_id:
        raise InvalidRequestError(
            "X-Trace-ID conflicts with trace_id",
            request_id=payload.request_id or header_request_id,
            trace_id=payload.trace_id,
            details=[{"field": "trace_id", "reason": "conflicts_with_header"}],
        )
    if header_request_id and not payload.request_id:
        payload.request_id = header_request_id
    if header_trace_id and not payload.trace_id:
        payload.trace_id = header_trace_id


def _header_value(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _authorize_turn(payload: IngressRequest, request: Request) -> None:
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    await container.access_control.authorize(
        CoreTrustedIdentity(
            user_id=payload.vet_context.user_id,
            session_id=payload.vet_context.session_id,
            pet_id=payload.vet_context.pet_id,
        ),
        pet_info=payload.vet_context.pet_info,
        principal=principal,
    )


def _ready_checks(orchestrator_ready: bool) -> dict[str, bool]:
    return {
        "ingress_config": True,
        "rule_repository": orchestrator_ready,
        "knowledge_repository": orchestrator_ready,
        "orchestrator": orchestrator_ready,
    }


def _trace_headers(turn_request: AgentTurnRequest) -> dict[str, str]:
    return {
        "X-Request-ID": turn_request.request_context.request_id,
        "X-Trace-ID": turn_request.request_context.trace_id,
    }
