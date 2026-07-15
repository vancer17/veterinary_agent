from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now_utc() -> datetime:
    return datetime.now(UTC)


class InputItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = "user"
    type: str = "message"
    content: str | list[Any] | dict[str, Any] | None = None

    def text(self) -> str:
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
    model_config = ConfigDict(extra="allow")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    pet_id: str = Field(min_length=1)
    pet_info: dict[str, Any] = Field(default_factory=dict)


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
    vet_context: VetContext
    attachments: list[AttachmentRef] = Field(default_factory=list)
    turn_options: TurnOptions = Field(default_factory=TurnOptions)

    @field_validator("input", mode="before")
    @classmethod
    def normalize_input(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [{"role": "user", "type": "message", "content": value}]
        if isinstance(value, dict):
            return [value]
        return value

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> "IngressRequest":
        has_text = any(item.text().strip() for item in self.input)
        if not has_text and not self.attachments:
            raise ValueError("input or attachments must contain valid content")
        return self

    def joined_text(self) -> str:
        return "\n".join(item.text() for item in self.input if item.text().strip())

    def ensure_ids(self) -> "IngressRequest":
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


class TrustedIdentity(BaseModel):
    user_id: str
    session_id: str
    pet_id: str


class AgentTurnRequest(BaseModel):
    request_context: RequestContext
    trusted_identity: TrustedIdentity
    input: list[InputItem]
    attachments: list[AttachmentRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    turn_options: TurnOptions = Field(default_factory=TurnOptions)
    vet_context: VetContext

    @classmethod
    def from_ingress(cls, ingress: IngressRequest) -> "AgentTurnRequest":
        ingress.ensure_ids()
        return cls(
            request_context=RequestContext(
                request_id=ingress.request_id or f"req_{uuid4().hex}",
                trace_id=ingress.trace_id or f"tr_{uuid4().hex}",
                response_mode="stream" if ingress.stream else "sync",
            ),
            trusted_identity=TrustedIdentity(
                user_id=ingress.vet_context.user_id,
                session_id=ingress.vet_context.session_id,
                pet_id=ingress.vet_context.pet_id,
            ),
            input=ingress.input,
            attachments=ingress.attachments,
            metadata=ingress.metadata,
            model=ingress.model,
            turn_options=ingress.turn_options,
            vet_context=ingress.vet_context,
        )

    def joined_text(self) -> str:
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
        import json

        return f"event: {self.event}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    request_id: str | None = None
    trace_id: str | None = None
