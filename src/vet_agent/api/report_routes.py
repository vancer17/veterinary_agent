"""
文件：src/vet_agent/api/report_routes.py
作用：提供面向业务侧的 HTTP API 路由。
说明：本文件遵循项目标准文件树编排；跨包引用应通过对应包的 __init__.py 暴露能力。
"""


from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ingress import InvalidRequestError
from vet_agent import get_container
from vet_agent import TrustedIdentity


router = APIRouter(prefix="/reports", tags=["reports"])


class ReportParseRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    pet_id: str = Field(min_length=1)
    report_type: str = "unknown"
    oss_image_url: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_oss_image_url(cls, value: Any) -> Any:
        """执行 normalize_oss_image_url 业务逻辑。

        :param value: 待处理值。
        :return: 返回函数执行结果。
        """
        if not isinstance(value, dict):
            return value
        if value.get("oss_image_url"):
            return value
        for alias in ("image_url", "storage_ref", "oss_url", "file_url"):
            if value.get(alias):
                copied = dict(value)
                copied["oss_image_url"] = copied[alias]
                return copied
        return value


@router.post("/parse")
async def parse_report(payload: ReportParseRequest, request: Request):
    """执行 parse_report 业务逻辑。

    :param payload: 请求载荷。
    :param request: 请求对象。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(user_id=payload.user_id, session_id=payload.session_id, pet_id=payload.pet_id)
    principal = container.access_control.authenticate(request.headers)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
    if not payload.oss_image_url:
        raise InvalidRequestError("oss_image_url is required")
    try:
        return await container.report_service.parse_report(
            identity,
            oss_image_url=payload.oss_image_url,
            report_type=payload.report_type,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get("")
async def list_reports(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session_id: Annotated[str, Query(min_length=1)],
    pet_id: Annotated[str, Query(min_length=1)],
):
    """执行 list_reports 业务逻辑。

    :param request: 请求对象。
    :param user_id: 参数 user_id。
    :param session_id: 参数 session_id。
    :param pet_id: 参数 pet_id。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
    principal = container.access_control.authenticate(request.headers)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
    return {"items": await container.report_service.list_reports(identity)}


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session_id: Annotated[str, Query(min_length=1)],
    pet_id: Annotated[str, Query(min_length=1)],
):
    """执行 get_report 业务逻辑。

    :param report_id: 报告标识。
    :param request: 请求对象。
    :param user_id: 参数 user_id。
    :param session_id: 参数 session_id。
    :param pet_id: 参数 pet_id。
    :return: 返回异步执行结果。
    """
    container = get_container()
    identity = TrustedIdentity(user_id=user_id, session_id=session_id, pet_id=pet_id)
    principal = container.access_control.authenticate(request.headers)
    await container.access_control.authorize(identity, pet_info={}, principal=principal)
    report = await container.report_service.get_report(identity, report_id)
    if not report:
        raise InvalidRequestError("report not found")
    return report
