"""
文件：src/vet_agent/contracts.py
作用：定义兽医 Agent 核心请求、响应、范围声明和对话结果契约。
范围：本文件承载核心业务层的稳定数据结构；身份、宠物资料与会话范围由 scope_assertion 统一声明。
说明：vet_context 仅保留非权威请求侧上下文；跨包引用应通过 vet_agent 一级包入口暴露。
"""


from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


def now_utc() -> datetime:
    """执行 now_utc 业务逻辑。

    :return: 返回函数执行结果。
    """
    return datetime.now(UTC)


class InputItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = "user"
    type: str = "message"
    content: str | list[Any] | dict[str, Any] | None = None

    def text(self) -> str:
        """执行 text 业务逻辑。

        :return: 返回函数执行结果。
        """
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, dict):
            value = self.content.get("text") or self.content.get("content") or ""
            return str(value)
        parts: list[str] = []
        for item in self.content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)


class VetContext(BaseModel):
    """表示核心业务层使用的兽医上下文。

    说明：``pet_info`` 为请求侧自报资料；身份、宠物资料与会话范围只能来自 ``scope_assertion``。
    """

    model_config = ConfigDict(extra="forbid")

    pet_info: dict[str, Any] = Field(
        default_factory=dict,
        description="请求侧自报宠物资料，禁止作为权威画像或临床硬判断依据。",
    )


class TrustedIdentity(BaseModel):
    """表示 Agent 内部使用的可信身份范围投影。

    :param user_id: 由 scope_assertion 派生的用户标识。
    :param session_id: 由 scope_assertion 派生的会话标识。
    :param pet_id: 由 scope_assertion 派生的宠物标识。
    :return: 无返回值。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str = Field(min_length=1, description="由范围声明派生的用户标识。")
    session_id: str = Field(min_length=1, description="由范围声明派生的会话标识。")
    pet_id: str = Field(min_length=1, description="由范围声明派生的宠物标识。")


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
        """校验范围声明内部字段的一致性与时间语义。

        :return: 返回通过结构一致性校验的范围声明。
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
        """从范围声明派生 Agent 内部可信身份。

        :return: 返回供记忆、幂等、trace 与会话绑定使用的内部身份。
        """
        return TrustedIdentity(user_id=self.user_id, session_id=self.session_id, pet_id=self.pet_id)

    def profile_projection(self) -> dict[str, Any]:
        """生成可写入 Agent 本地画像投影的字典。

        :return: 返回去除空值后的服务端已验证宠物画像。
        """
        raw = self.profile.model_dump(mode="json", exclude_none=True)
        return {str(key): value for key, value in raw.items() if value not in (None, "")}


class AuthorizedScopeContext(BaseModel):
    """表示入口授权后生成的内部范围上下文快照。

    :param identity: 已通过范围授权的身份投影。
    :param verified_profile: 已通过范围授权的宠物画像投影。
    :param reported_pet_info: 请求侧未验证宠物资料审计副本。
    :param authorized_at: 入口授权完成时间。
    :return: 无返回值。
    """

    model_config = ConfigDict(extra="forbid")

    identity: TrustedIdentity = Field(description="已通过范围授权的身份投影。")
    verified_profile: dict[str, Any] = Field(default_factory=dict, description="已通过范围授权的宠物画像投影。")
    reported_pet_info: dict[str, Any] = Field(default_factory=dict, description="请求侧未验证宠物资料审计副本。")
    authorized_at: datetime = Field(default_factory=now_utc, description="入口授权完成时间。")


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    attachment_id: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    purpose: str = Field(
        default="unknown",
        description="Examples: lab_report, medical_record, radiology, photo.",
    )
    storage_ref: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    idempotency_key: str | None = None
    response_language: str = "zh-CN"
    max_followup_questions: int = Field(default=3, ge=1, le=3)


class IngressRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_id: str | None = None
    trace_id: str | None = None
    model: str | None = None
    input: list[InputItem] = Field(default_factory=list)
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    scope_assertion: ScopeAssertion
    vet_context: VetContext = Field(default_factory=VetContext)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    turn_options: TurnOptions = Field(default_factory=TurnOptions)

    @field_validator("input", mode="before")
    @classmethod
    def normalize_input(cls, value: Any) -> list[Any]:
        """执行 normalize_input 业务逻辑。

        :param value: 待处理值。
        :return: 返回函数执行结果。
        """
        if value is None:
            return []
        if isinstance(value, str):
            return [{"role": "user", "type": "message", "content": value}]
        if isinstance(value, dict):
            return [value]
        return value

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> "IngressRequest":
        """执行 require_input_or_attachment 业务逻辑。

        :return: 返回函数执行结果。
        """
        has_text = any(item.text().strip() for item in self.input)
        if not has_text and not self.attachments:
            raise ValueError("input or attachments must contain valid content")
        return self

    def joined_text(self) -> str:
        """执行 joined_text 业务逻辑。

        :return: 返回函数执行结果。
        """
        return "\n".join(item.text() for item in self.input if item.text().strip())

    def ensure_ids(self) -> "IngressRequest":
        """执行 ensure_ids 业务逻辑。

        :return: 返回函数执行结果。
        """
        if not self.request_id:
            self.request_id = f"req_{uuid4().hex}"
        if not self.trace_id:
            self.trace_id = f"tr_{uuid4().hex}"
        return self


class RequestContext(BaseModel):
    request_id: str
    trace_id: str
    response_mode: Literal["sync", "stream"]
    received_at: datetime = Field(default_factory=now_utc)


class AgentTurnRequest(BaseModel):
    request_context: RequestContext
    scope_assertion: ScopeAssertion
    trusted_identity: TrustedIdentity
    authorized_scope_context: AuthorizedScopeContext | None = Field(
        default=None,
        description="入口授权后生成的内部范围上下文快照；外部请求不得直接构造。",
    )
    input: list[InputItem]
    attachments: list[AttachmentRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    turn_options: TurnOptions = Field(default_factory=TurnOptions)
    vet_context: VetContext

    @model_validator(mode="after")
    def validate_identity_projection(self) -> Self:
        """校验核心请求中的内部身份投影与范围声明同源。

        :return: 返回通过身份一致性校验的核心回合请求。
        :raises ValueError: 内部可信身份与 scope_assertion 派生结果不一致时抛出。
        """
        expected = self.scope_assertion.trusted_identity()
        if self.trusted_identity != expected:
            raise ValueError("trusted_identity must be derived from scope_assertion")
        if self.authorized_scope_context is not None and self.authorized_scope_context.identity != expected:
            raise ValueError("authorized_scope_context.identity must match scope_assertion")
        return self

    @classmethod
    def from_ingress(cls, ingress: IngressRequest) -> "AgentTurnRequest":
        """执行 from_ingress 业务逻辑。

        :param ingress: 参数 ingress。
        :return: 返回函数执行结果。
        """
        ingress.ensure_ids()
        return cls(
            request_context=RequestContext(
                request_id=ingress.request_id or f"req_{uuid4().hex}",
                trace_id=ingress.trace_id or f"tr_{uuid4().hex}",
                response_mode="stream" if ingress.stream else "sync",
            ),
            scope_assertion=ingress.scope_assertion,
            trusted_identity=ingress.scope_assertion.trusted_identity(),
            input=ingress.input,
            attachments=ingress.attachments,
            metadata=ingress.metadata,
            model=ingress.model,
            turn_options=ingress.turn_options,
            vet_context=ingress.vet_context,
        )

    def joined_text(self) -> str:
        """执行 joined_text 业务逻辑。

        :return: 返回函数执行结果。
        """
        return "\n".join(item.text() for item in self.input if item.text().strip())


class Evidence(BaseModel):
    source: str
    detail: str
    public_citation: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningDisplay(BaseModel):
    projection_id: str
    segment_id: str | None = None
    title: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class VetSegment(BaseModel):
    segment_id: str = Field(default_factory=lambda: f"seg_{uuid4().hex}")
    type: str
    title: str
    content: str
    status: str = "completed"
    output_text: str | None = None
    references: list[dict[str, Any]] = Field(default_factory=list)
    reasoning_display: ReasoningDisplay | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class SafetySignal(BaseModel):
    code: str
    severity: Literal["info", "caution", "urgent", "blocked"]
    message: str
    matched_terms: list[str] = Field(default_factory=list)


class AgentTurnResponse(BaseModel):
    id: str
    object: str = "agent.turn"
    created_at: datetime = Field(default_factory=now_utc)
    request_id: str
    trace_id: str
    model: str
    status: Literal["completed", "requires_followup", "safety_escalated", "blocked"]
    output_text: str
    segments: list[VetSegment] = Field(default_factory=list)
    reasoning_display: ReasoningDisplay | None = None
    vet_result: dict[str, Any] = Field(default_factory=dict)
    safety_signals: list[SafetySignal] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamEvent(BaseModel):
    event: str
    data: dict[str, Any]

    def to_sse(self) -> str:
        """转换为 SSE 文本帧。

        :return: 返回函数执行结果。
        """
        import json

        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    request_id: str | None = None
    trace_id: str | None = None
    model_config = ConfigDict(extra="forbid")
