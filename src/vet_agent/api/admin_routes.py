"""
文件：src/vet_agent/api/admin_routes.py
作用：提供面向业务侧的 HTTP API 路由。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ingress import InvalidRequestError
from vet_agent import get_container
from vet_agent import TrustedIdentity


router = APIRouter(prefix="/admin", tags=["admin"])


class RagChunkUpdate(BaseModel):
    enabled: bool | None = None
    review_status: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    disabled_reason: str | None = None
    reason: str | None = None


@router.get("/rag/stats")
async def rag_stats(request: Request):
    """执行 rag_stats 业务逻辑。

    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
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
    """执行 list_rag_chunks 业务逻辑。

    :param request: 请求对象。
    :param review_status: 参数 review_status。
    :param limit: 返回数量上限。
    :param offset: 分页偏移量。
    :return: 返回异步执行结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.rag_governance_service.list_chunks(
        review_status=review_status,
        limit=limit,
        offset=offset,
    )


@router.patch("/rag/chunks/{chunk_id}")
async def update_rag_chunk(chunk_id: int, payload: RagChunkUpdate, request: Request):
    """执行 update_rag_chunk 业务逻辑。

    :param chunk_id: 参数 chunk_id。
    :param payload: 请求载荷。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
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
    """执行 admin_list_reports 业务逻辑。

    :param request: 请求对象。
    :param user_id: 参数 user_id。
    :param session_id: 参数 session_id。
    :param pet_id: 参数 pet_id。
    :return: 返回异步执行结果。
    """
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
    return {"items": await container.report_service.list_reports(identity)}
