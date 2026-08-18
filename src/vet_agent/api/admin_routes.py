"""
文件：src/vet_agent/api/admin_routes.py
作用：提供面向业务侧的 HTTP API 路由。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ingress import InvalidRequestError
from vet_agent import get_container
from vet_agent import TrustedIdentity


router = APIRouter(prefix="/admin", tags=["admin"])


class RagChunkUpdate(BaseModel):
    """定义 RAG chunk 治理更新请求。

    :return: 无返回值；该 DTO 只服务管理端资产治理。
    """

    enabled: bool | None = None
    review_status: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    disabled_reason: str | None = None
    reason: str | None = None


class RagMissUpdate(BaseModel):
    """定义 RAG 无命中治理记录更新请求。

    :return: 无返回值；该 DTO 只更新治理状态，不触发知识发布或回答回退。
    """

    status: str | None = Field(default=None, description="治理状态，例如 triaged、asset_drafted、published 或 dismissed。")
    review_notes: str | None = Field(default=None, description="治理人员处理备注。")
    linked_ingestion_batch: str | None = Field(default=None, description="关联的知识导入批次标识。")
    linked_chunk_ids: list[int] | None = Field(default=None, description="关联的正式知识 chunk 内部主键集合。")
    reason: str | None = Field(default=None, description="本次治理操作原因。")


class ClinicalKnowledgeImport(BaseModel):
    """结构化临床知识导入请求。

    :return: 无返回值。
    """

    model_config = ConfigDict(extra="allow")

    source: str = Field(default="common_conditions_handbook")
    version: str = Field(default="v1")
    publish: bool = False
    source_url: str | None = None
    conditions: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict, alias="_meta")


class ClinicalKnowledgePublish(BaseModel):
    """结构化临床知识发布请求。

    :return: 无返回值。
    """

    reason: str | None = None


@router.get("/rag/stats")
async def rag_stats(request: Request) -> dict[str, Any]:
    """执行 rag_stats 业务逻辑。

    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.rag_governance_service.stats()


@router.get("/rag/misses")
async def list_rag_misses(
    request: Request,
    rag_scope: str | None = None,
    status: str | None = None,
    task_domain: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """分页查询 RAG 无命中知识缺口治理记录。

    :param request: 请求对象。
    :param rag_scope: 可选 RAG 数据链范围过滤条件。
    :param status: 可选治理状态过滤条件。
    :param task_domain: 可选任务域过滤条件。
    :param limit: 返回数量上限。
    :param offset: 分页偏移量。
    :return: 返回 RAG 无命中治理记录分页结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    try:
        return await container.rag_miss_governance_service.list_misses(
            rag_scope=rag_scope,
            status=status,
            task_domain=task_domain,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.patch("/rag/misses/{miss_id}")
async def update_rag_miss(
    miss_id: str,
    payload: RagMissUpdate,
    request: Request,
) -> dict[str, Any]:
    """更新 RAG 无命中知识缺口治理记录。

    :param miss_id: RAG 无命中治理记录稳定标识。
    :param payload: 请求载荷。
    :param request: 请求对象。
    :return: 返回更新后的 RAG 无命中治理记录。
    """
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    try:
        return await container.rag_miss_governance_service.update_miss(
            miss_id,
            status=payload.status,
            review_notes=payload.review_notes,
            linked_ingestion_batch=payload.linked_ingestion_batch,
            linked_chunk_ids=tuple(payload.linked_chunk_ids) if payload.linked_chunk_ids is not None else None,
            actor_id=principal.user_id or principal.api_key_id,
            reason=payload.reason,
        )
    except (ValueError, KeyError) as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.post("/clinical-knowledge/conditions/preview")
async def preview_clinical_conditions(
    payload: ClinicalKnowledgeImport,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
):
    """预览结构化临床病症卡入库效果。

    :param payload: 请求载荷。
    :param request: 请求对象。
    :param limit: 返回数量上限。
    :return: 返回异步执行结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    raw_payload = payload.model_dump(mode="json", by_alias=True)
    raw_payload["_meta"] = raw_payload.pop("_meta", raw_payload.get("metadata", {}))
    return await container.clinical_knowledge_service.preview_conditions(raw_payload, limit=limit)


@router.post("/clinical-knowledge/conditions/import")
async def import_clinical_conditions(payload: ClinicalKnowledgeImport, request: Request):
    """导入结构化临床病症卡。

    :param payload: 请求载荷。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    raw_payload = payload.model_dump(mode="json", by_alias=True)
    raw_payload["_meta"] = raw_payload.pop("_meta", raw_payload.get("metadata", {}))
    return await container.clinical_knowledge_service.import_conditions(
        raw_payload,
        source=payload.source,
        version=payload.version,
        actor_id=principal.user_id or principal.api_key_id,
        publish=payload.publish,
        source_url=payload.source_url,
    )


@router.post("/clinical-knowledge/batches/{batch_id}/publish")
async def publish_clinical_knowledge_batch(
    batch_id: str,
    payload: ClinicalKnowledgePublish,
    request: Request,
):
    """发布结构化临床知识导入批次。

    :param batch_id: 导入批次标识。
    :param payload: 请求载荷。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    try:
        return await container.clinical_knowledge_service.publish_batch(
            batch_id,
            actor_id=principal.user_id or principal.api_key_id,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get("/clinical-knowledge/batches")
async def list_clinical_knowledge_batches(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """分页查询结构化临床知识导入批次。

    :param request: 请求对象。
    :param limit: 返回数量上限。
    :param offset: 分页偏移量。
    :return: 返回异步执行结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.clinical_knowledge_service.list_batches(limit=limit, offset=offset)


@router.get("/clinical-knowledge/conditions")
async def list_clinical_conditions(
    request: Request,
    review_status: str | None = None,
    system: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """分页查询结构化临床病症卡。

    :param request: 请求对象。
    :param review_status: 审核状态。
    :param system: 所属系统。
    :param limit: 返回数量上限。
    :param offset: 分页偏移量。
    :return: 返回异步执行结果。
    """
    container = get_container()
    container.access_control.authenticate(request.headers)
    return await container.clinical_knowledge_service.list_conditions(
        review_status=review_status,
        system=system,
        limit=limit,
        offset=offset,
    )


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
    await container.access_control.authorize_identity(identity, pet_info={}, principal=principal)
    return {"items": await container.report_service.list_reports(identity)}
