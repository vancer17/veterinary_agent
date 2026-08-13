"""
文件：src/ingress/dto.py
作用：定义 HTTP 入口请求、响应与范围声明 DTO。
范围：入口层只做结构校验和传输层转换；身份、宠物资料与会话范围由 scope_assertion 统一承载。
说明：vet_context 仅保留非权威请求侧上下文，不再携带 user_id、pet_id 或 session_id。
"""


from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


JsonObject = dict[str, Any]
InputPayload = str | JsonObject | list[str | JsonObject]
ResponseMode = Literal["sync", "stream"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "agent-api-ingress"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    checks: dict[str, bool] = Field(default_factory=dict)


class VetContext(BaseModel):
    """表示外部请求携带的兽医上下文。

    说明：``pet_info`` 为请求侧自报资料，不作为服务端已验证宠物画像；身份范围来自 ``scope_assertion``。
    """

    model_config = ConfigDict(extra="forbid")

    pet_info: JsonObject = Field(
        default_factory=dict,
        description="请求侧自报宠物资料，仅供审计与后续受控确认，不可直接用于临床硬判断。",
    )


class ScopeAssertionAuthorization(BaseModel):
    """表示 BFF 已完成的宠物范围授权声明。

    :param ownership_verified: BFF 是否已校验当前用户拥有该宠物。
    :param pet_active: 当前宠物档案是否允许进入 Agent 服务。
    :param pet_status: 主服务宠物档案状态。
    :param pet_deleted: 主服务宠物档案是否已软删除。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ownership_verified: StrictBool = Field(description="BFF 已完成 owner_id 与 user_id 的归属校验。")
    pet_active: StrictBool = Field(description="宠物档案当前是否允许进入 Agent 主链路。")
    pet_status: str = Field(min_length=1, description="主服务 master_pet_info.status 原始状态。")
    pet_deleted: StrictBool = Field(description="主服务 master_pet_info.deleted_at 是否非空。")


class ScopeAssertionProfile(BaseModel):
    """表示 BFF 从主服务宠物基础资料归一得到的服务端画像。

    :param species: 归一物种，作为临床安全与问诊链路的最小必需字段。
    :param pet_code: 主服务宠物业务编码。
    :param name: 宠物名称。
    :param sex: 归一性别。
    :param birthday: 出生日期。
    :param age_months: 年龄月数。
    :param age: 年龄文本。
    :param breed: 归一品种。
    :param weight_kg: 千克体重。
    :param neutered: 是否绝育。
    :param neutered_date: 绝育日期。
    :param reproduction_status: 繁育状态。
    :param activity_level: 活跃度。
    :param region: 宠物所在地区。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    species: str = Field(min_length=1, description="归一物种；当前作为临床最小画像字段。")
    pet_code: str | None = Field(default=None, description="主服务宠物业务编码。")
    name: str | None = Field(default=None, description="宠物名称。")
    sex: str | None = Field(default=None, description="归一性别。")
    birthday: str | None = Field(default=None, description="出生日期，建议 ISO date。")
    age_months: int | None = Field(default=None, ge=0, description="年龄月数。")
    age: str | None = Field(default=None, description="兼容当前 Agent 消费的年龄文本。")
    breed: str | None = Field(default=None, description="归一品种，对应主服务 variety。")
    weight_kg: float | None = Field(default=None, gt=0, description="千克体重，对应主服务 weight。")
    neutered: bool | None = Field(default=None, description="是否绝育，对应主服务 sterilized。")
    neutered_date: str | None = Field(default=None, description="绝育日期，建议 ISO date。")
    reproduction_status: str | None = Field(default=None, description="繁育状态。")
    activity_level: int | None = Field(default=None, description="活跃度。")
    region: str | None = Field(default=None, description="宠物所在地区。")


class ScopeAssertionSource(BaseModel):
    """表示范围声明所使用的主服务数据来源。

    :param system: 签发声明的数据系统。
    :param database: 来源数据库名。
    :param table: 来源表名。
    :param record_id: 来源记录 ID。
    :param record_updated_at: 来源记录更新时间。
    :param data_source: 主服务记录中的数据来源。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    system: str = Field(min_length=1, description="签发声明的数据系统或服务名。")
    database: str | None = Field(default=None, description="来源数据库名。")
    table: Literal["master_pet_info"] = Field(description="来源表名；当前范围声明只接受主服务 master_pet_info。")
    record_id: str = Field(min_length=1, description="来源记录 ID。")
    record_updated_at: datetime = Field(description="来源记录更新时间。")
    data_source: str | None = Field(default=None, description="主服务宠物资料数据来源。")


class ScopeAssertionSessionPolicy(BaseModel):
    """表示本轮请求适用的会话范围策略。

    :param binding_mode: 会话绑定模式。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    binding_mode: Literal["single_user_pet_per_session"] = Field(
        default="single_user_pet_per_session",
        description="一个 session_id 只能绑定一个 user_id + pet_id。",
    )


class ScopeAssertion(BaseModel):
    """表示 BFF 对本轮 Agent 调用范围的服务端声明。

    :param schema_version: 范围声明结构版本。
    :param issuer: 声明签发方。
    :param issued_at: 声明签发时间。
    :param expires_at: 声明过期时间。
    :param user_id: BFF 已认证用户标识。
    :param pet_id: BFF 已完成归属校验的宠物标识。
    :param session_id: BFF 发放或复用的问诊会话标识。
    :param authorization: BFF 已完成的宠物范围授权声明。
    :param profile: BFF 从主服务归一得到的服务端宠物画像。
    :param source: 画像来源数据记录。
    :param session_policy: 会话范围策略。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    schema_version: Literal["v1"] = Field(description="范围声明结构版本。")
    issuer: str = Field(min_length=1, description="范围声明签发方。")
    issued_at: datetime = Field(description="范围声明签发时间。")
    expires_at: datetime | None = Field(default=None, description="范围声明过期时间。")
    user_id: str = Field(min_length=1, description="BFF 已认证用户标识。")
    pet_id: str = Field(min_length=1, description="BFF 已校验归属的宠物标识。")
    session_id: str = Field(min_length=1, description="BFF 发放或复用的会话标识。")
    authorization: ScopeAssertionAuthorization = Field(description="BFF 宠物范围授权声明。")
    profile: ScopeAssertionProfile = Field(description="服务端已验证宠物基础画像。")
    source: ScopeAssertionSource = Field(description="画像来源数据记录。")
    session_policy: ScopeAssertionSessionPolicy = Field(
        default_factory=ScopeAssertionSessionPolicy,
        description="本轮声明采用的会话范围绑定策略。",
    )

    @model_validator(mode="after")
    def validate_scope_integrity(self) -> Self:
        """校验入口范围声明内部字段的一致性与时间语义。

        :return: 返回通过结构一致性校验的入口范围声明。
        :raises ValueError: 时间缺少时区、过期区间非法或来源记录与宠物标识不一致时抛出。
        """
        timestamps = {
            "issued_at": self.issued_at,
            "source.record_updated_at": self.source.record_updated_at,
        }
        if self.expires_at is not None:
            timestamps["expires_at"] = self.expires_at
        for field_name, value in timestamps.items():
            if value.utcoffset() is None:
                raise ValueError(f"{field_name} must include timezone information")
        if self.source.record_id != self.pet_id:
            raise ValueError("source.record_id must match pet_id")
        return self

    def trusted_identity(self) -> TrustedIdentity:
        """从范围声明派生入口层内部可信身份。

        :return: 返回供入口编排器继续传递的内部身份。
        """
        return TrustedIdentity(user_id=self.user_id, session_id=self.session_id, pet_id=self.pet_id)


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    attachment_id: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    storage_ref: str = Field(min_length=1)
    metadata: JsonObject = Field(default_factory=dict)


class TurnOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    idempotency_key: str | None = None
    timeout_ms: int | None = Field(default=None, gt=0)
    response_mode: ResponseMode | None = None
    max_followup_questions: int = Field(default=3, ge=1, le=3)


class IngressRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str | None = Field(default=None, min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    input: InputPayload | None = None
    stream: StrictBool = False
    metadata: JsonObject = Field(default_factory=dict)
    scope_assertion: ScopeAssertion
    vet_context: VetContext = Field(default_factory=VetContext)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    turn_options: TurnOptions = Field(default_factory=TurnOptions)

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> IngressRequest:
        """执行 require_input_or_attachment 业务逻辑。

        :return: 返回函数执行结果。
        """
        if not _has_input(self.input) and not self.attachments:
            raise ValueError("input or attachments is required")
        return self

    def to_agent_turn_request(
        self,
        source_path: str,
        *,
        authorized_scope_context: JsonObject | None = None,
    ) -> AgentTurnRequest:
        """执行 to_agent_turn_request 业务逻辑。

        :param source_path: 来源接口路径。
        :param authorized_scope_context: 入口授权后生成的内部范围上下文快照。
        :return: 返回函数执行结果。
        """
        request_id = self.request_id or str(uuid4())
        trace_id = self.trace_id or request_id
        response_mode: ResponseMode = "stream" if self.stream else "sync"

        return AgentTurnRequest(
            request_context=RequestContext(
                request_id=request_id,
                trace_id=trace_id,
                response_mode=response_mode,
                received_at=datetime.now(timezone.utc),
                source_path=source_path,
            ),
            scope_assertion=self.scope_assertion,
            trusted_identity=self.scope_assertion.trusted_identity(),
            authorized_scope_context=authorized_scope_context,
            model=self.model,
            input=self.input,
            attachments=self.attachments,
            metadata=self.metadata,
            vet_context=self.vet_context,
            turn_options=self.turn_options,
        )


class RequestContext(BaseModel):
    request_id: str
    trace_id: str
    response_mode: ResponseMode
    received_at: datetime
    source_path: str


class TrustedIdentity(BaseModel):
    """表示入口层由范围声明派生的可信身份。

    :param user_id: 由 scope_assertion 派生的用户标识。
    :param session_id: 由 scope_assertion 派生的会话标识。
    :param pet_id: 由 scope_assertion 派生的宠物标识。
    :return: 无返回值。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str = Field(min_length=1, description="由范围声明派生的用户标识。")
    session_id: str = Field(min_length=1, description="由范围声明派生的会话标识。")
    pet_id: str = Field(min_length=1, description="由范围声明派生的宠物标识。")


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_context: RequestContext
    scope_assertion: ScopeAssertion
    trusted_identity: TrustedIdentity
    authorized_scope_context: JsonObject | None = Field(
        default=None,
        description="入口授权后生成的内部范围上下文快照；外部请求不得直接构造。",
    )
    model: str | None = None
    input: InputPayload | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    vet_context: VetContext
    turn_options: TurnOptions = Field(default_factory=TurnOptions)

    @model_validator(mode="after")
    def validate_identity_projection(self) -> Self:
        """校验入口内部请求中的身份投影与范围声明同源。

        :return: 返回通过身份一致性校验的入口回合请求。
        :raises ValueError: 内部可信身份与 scope_assertion 派生结果不一致时抛出。
        """
        expected = self.scope_assertion.trusted_identity()
        if self.trusted_identity != expected:
            raise ValueError("trusted_identity must be derived from scope_assertion")
        return self


def _has_input(value: InputPayload | None) -> bool:
    """执行 _has_input 内部辅助逻辑。

    :param value: 待处理值。
    :return: 返回函数执行结果。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_input(item) for item in value)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str):
            return bool(text.strip())
        if isinstance(text, list):
            return _has_input(text)
        if value.get("type") == "input_attachment" and value.get("attachment_id"):
            return True
    return bool(value)
