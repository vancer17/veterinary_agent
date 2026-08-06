"""
文件：src/vet_agent/api/memory_routes.py
作用：提供面向业务侧的 HTTP API 路由。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ingress import ForbiddenError
from vet_agent import get_container
from vet_agent import TrustedIdentity


router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryCorrection(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    pet_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class FactCorrection(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    pet_id: str = Field(min_length=1)
    fact_type: str = Field(min_length=1)
    fact_key: str = Field(min_length=1)
    fact_value: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_text: str | None = None
    metadata: dict = Field(default_factory=dict)


@router.get("")
async def read_memory(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session_id: Annotated[str, Query(min_length=1)],
    pet_id: Annotated[str, Query(min_length=1)],
):
    """执行 read_memory 业务逻辑。

    :param request: 请求对象。
    :param user_id: 参数 user_id。
    :param session_id: 参数 session_id。
    :param pet_id: 参数 pet_id。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
    await _authorize_memory_request(request, identity)
    return await container.memory_service.read(
        identity
    )


@router.put("")
async def correct_memory(correction: MemoryCorrection, request: Request):
    """执行 correct_memory 业务逻辑。

    :param correction: 参数 correction。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(
        user_id=correction.user_id,
        session_id=correction.session_id,
        pet_id=correction.pet_id,
    )
    await _authorize_memory_request(request, identity)
    await container.memory_service.remember_turn(
        identity,
        user_text="[用户纠正记忆]",
        summary=correction.summary,
        medical=False,
        metadata={"source": "memory_correction"},
    )
    return {"status": "updated"}


@router.put("/facts")
async def correct_pet_fact(correction: FactCorrection, request: Request):
    """执行 correct_pet_fact 业务逻辑。

    :param correction: 参数 correction。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(
        user_id=correction.user_id,
        session_id=correction.session_id,
        pet_id=correction.pet_id,
    )
    await _authorize_memory_request(request, identity)
    await container.memory_service.upsert_pet_fact(
        identity,
        fact_type=correction.fact_type,
        fact_key=correction.fact_key,
        fact_value=correction.fact_value,
        confidence=correction.confidence,
        source_text=correction.source_text,
        metadata={"source": "memory_fact_correction", **correction.metadata},
    )
    return {"status": "updated", "fact_key": correction.fact_key}


@router.delete("/pets/{pet_id}")
async def delete_pet_memory(
    pet_id: str,
    request: Request,
    user_id: Annotated[str | None, Query(min_length=1)] = None,
    session_id: Annotated[str | None, Query(min_length=1)] = None,
):
    """执行 delete_pet_memory 业务逻辑。

    :param pet_id: 参数 pet_id。
    :param request: 请求对象。
    :param user_id: 参数 user_id。
    :param session_id: 参数 session_id。
    :return: 返回异步执行结果。
    """
    container = get_container()
    if user_id and session_id:
        identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
        await _authorize_memory_request(request, identity)
        await container.memory_service.delete_pet_memory(pet_id, user_id=user_id)
    else:
        settings = container.settings
        if settings.require_api_auth or settings.api_keys or settings.pet_authorization_mode == "strict":
            raise ForbiddenError("user_id and session_id are required to delete pet memory")
        await container.memory_service.delete_pet_memory(pet_id)
    return {"status": "deleted", "pet_id": pet_id}


async def _authorize_memory_request(request: Request, identity: TrustedIdentity) -> None:
    """执行内部授权逻辑。

    :param request: 请求对象。
    :param identity: 可信身份信息。
    :return: 返回函数执行结果。
    """
    container = get_container()
    principal = container.access_control.authenticate(request.headers)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
