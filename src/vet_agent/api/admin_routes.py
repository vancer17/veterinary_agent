from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ingress.errors import InvalidRequestError
from vet_agent.container import get_container
from vet_agent.contracts import TrustedIdentity


router = APIRouter(prefix="/admin", tags=["admin"])


class RagChunkUpdate(BaseModel):
    enabled: bool | None = None
    review_status: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    disabled_reason: str | None = None
    reason: str | None = None


@router.get("/rag/stats")
async def rag_stats(request: Request):
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.rag_governance_service.stats()


@router.get("/rag/chunks")
async def list_rag_chunks(
    request: Request,
    review_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.rag_governance_service.list_chunks(
        review_status=review_status,
        limit=limit,
        offset=offset,
    )


@router.patch("/rag/chunks/{chunk_id}")
async def update_rag_chunk(chunk_id: int, payload: RagChunkUpdate, request: Request):
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    try:
        return await container.rag_governance_service.update_chunk(
            chunk_id,
            enabled=payload.enabled,
            review_status=payload.review_status,
            quality_score=payload.quality_score,
            disabled_reason=payload.disabled_reason,
            actor_id=principal.user_id or principal.api_key_id,
            reason=payload.reason,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get("/reports")
async def admin_list_reports(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session_id: Annotated[str, Query(min_length=1)],
    pet_id: Annotated[str, Query(min_length=1)],
):
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
    return {"items": await container.report_service.list_reports(identity)}
