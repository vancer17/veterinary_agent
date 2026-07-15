from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
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
    model_config = ConfigDict(extra="allow")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    pet_id: str = Field(min_length=1)
    pet_info: JsonObject = Field(default_factory=dict)


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
    vet_context: VetContext
    attachments: list[AttachmentRef] = Field(default_factory=list)
    turn_options: TurnOptions = Field(default_factory=TurnOptions)

    @model_validator(mode="after")
    def require_input_or_attachment(self) -> IngressRequest:
        if not _has_input(self.input) and not self.attachments:
            raise ValueError("input or attachments is required")
        return self

    def to_agent_turn_request(self, source_path: str) -> AgentTurnRequest:
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
            trusted_identity=TrustedIdentity(
                user_id=self.vet_context.user_id,
                session_id=self.vet_context.session_id,
                pet_id=self.vet_context.pet_id,
            ),
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
    user_id: str
    session_id: str
    pet_id: str


class AgentTurnRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    request_context: RequestContext
    trusted_identity: TrustedIdentity
    model: str | None = None
    input: InputPayload | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    vet_context: VetContext
    turn_options: TurnOptions = Field(default_factory=TurnOptions)


def _has_input(value: InputPayload | None) -> bool:
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
