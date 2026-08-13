"""
文件：src/vet_agent/input_safety/models.py
作用：定义基础输入安全候选、裁决动作与策略请求上下文。
范围：仅描述输入安全链路的结构化事实，不执行关键词扫描、临床推理或外部策略调用。
说明：候选表示“需要策略关注的事实”，最终阻断、升级或放行由 OPA 策略裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vet_agent import AgentTurnRequest, AttachmentRef, SafetySignal


class InputSafetyCandidateCategory(StrEnum):
    """表示基础输入安全候选的有限类别。

    :return: 无返回值。
    """

    INTEGRITY = "integrity"
    PROMPT_ATTACK = "prompt_attack"
    UNOPENED_CAPABILITY = "unopened_capability"
    OUT_OF_SCOPE = "out_of_scope"


class InputSafetyCandidateSource(StrEnum):
    """表示基础输入安全候选的产生来源。

    :return: 无返回值。
    """

    STRUCTURED_REQUEST = "structured_request"
    GUARDRAILS = "guardrails"
    REPOSITORY_DEFINITION = "repository_definition"


class InputSafetyDecisionAction(StrEnum):
    """表示 OPA 对基础输入安全候选的有限动作裁决。

    :return: 无返回值。
    """

    ALLOW = "allow"
    OBSERVE = "observe"
    ESCALATE = "escalate"
    BLOCK = "block"


@dataclass(frozen=True)
class InputSafetyCandidate:
    """表示本轮输入安全链路采集到的结构化候选事实。

    :param code: 候选编码，用于 OPA 策略、审计与响应信号关联。
    :param category: 候选类别。
    :param source: 候选来源。
    :param severity: 候选建议严重级别；最终动作仍以 OPA 裁决为准。
    :param message: 面向审计和用户响应的候选说明。
    :param confidence: 候选置信度，范围为 0 到 1。
    :param matched_terms: 候选关联的结构化线索，不得来自业务硬编码关键词扫描。
    :param metadata: 候选附加审计信息。
    :return: 无返回值。
    """

    code: str
    category: InputSafetyCandidateCategory
    source: InputSafetyCandidateSource
    severity: str
    message: str
    confidence: float = 1.0
    matched_terms: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_policy_input(self) -> dict[str, Any]:
        """转换为 OPA 策略输入中的候选字典。

        :return: 返回可 JSON 序列化的候选字典。
        """
        return {
            "code": self.code,
            "category": self.category.value,
            "source": self.source.value,
            "severity": self.severity,
            "message": self.message,
            "confidence": self.confidence,
            "matched_terms": list(self.matched_terms),
            "metadata": dict(self.metadata),
        }

    def to_signal(self, *, severity: str | None = None, message: str | None = None) -> SafetySignal:
        """转换为主响应契约使用的安全信号。

        :param severity: 策略裁决后的严重级别；为空时使用候选自身级别。
        :param message: 策略裁决后的说明；为空时使用候选自身说明。
        :return: 返回安全信号。
        """
        return SafetySignal(
            code=self.code,
            severity=severity or self.severity,
            message=message or self.message,
            matched_terms=list(self.matched_terms),
        )


@dataclass(frozen=True)
class InputSafetyDecision:
    """表示 OPA 对本轮基础输入安全候选的裁决结果。

    :param action: 策略动作。
    :param allow: 是否允许继续进入 Agent 主业务链路。
    :param message: 策略裁决说明，用于安全响应和审计。
    :param reasons: 策略原因列表。
    :param candidates: 参与裁决的候选列表。
    :param signals: 需要进入 Agent 响应的安全信号列表。
    :param metadata: 策略裁决原始附加信息。
    :return: 无返回值。
    """

    action: InputSafetyDecisionAction
    allow: bool
    message: str
    reasons: tuple[str, ...] = ()
    candidates: tuple[InputSafetyCandidate, ...] = ()
    signals: tuple[SafetySignal, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """判断当前输入安全裁决是否阻断主链路。

        :return: 阻断时返回 True。
        """
        return self.action == InputSafetyDecisionAction.BLOCK or not self.allow

    @property
    def escalated(self) -> bool:
        """判断当前输入安全裁决是否需要安全升级响应。

        :return: 需要升级时返回 True。
        """
        return self.action == InputSafetyDecisionAction.ESCALATE

    @classmethod
    def allow_turn(cls, *, metadata: dict[str, Any] | None = None) -> "InputSafetyDecision":
        """构造允许继续执行主链路的默认裁决。

        :param metadata: 附加审计信息。
        :return: 返回允许裁决。
        """
        return cls(
            action=InputSafetyDecisionAction.ALLOW,
            allow=True,
            message="基础输入安全策略允许本轮继续进入 Agent 主链路。",
            metadata=metadata or {},
        )

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的可审计结构。

        :return: 返回输入安全裁决 metadata。
        """
        return {
            "action": self.action.value,
            "allow": self.allow,
            "message": self.message,
            "reasons": list(self.reasons),
            "candidates": [candidate.to_policy_input() for candidate in self.candidates],
            "signals": [signal.model_dump(mode="json") for signal in self.signals],
            **dict(self.metadata),
        }


@dataclass(frozen=True)
class InputSafetyRequestContext:
    """表示基础输入安全策略裁决所需的请求上下文。

    :param request_id: 本轮请求标识。
    :param trace_id: 本轮链路追踪标识。
    :param user_id: 本轮可信用户标识。
    :param pet_id: 本轮可信宠物标识。
    :param session_id: 本轮可信会话标识。
    :param text: 本轮用户输入聚合文本。
    :param attachments: 本轮附件引用列表。
    :param metadata: 本轮请求 metadata。
    :return: 无返回值。
    """

    request_id: str
    trace_id: str
    user_id: str
    pet_id: str
    session_id: str
    text: str
    attachments: tuple[AttachmentRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: AgentTurnRequest) -> "InputSafetyRequestContext":
        """从 Agent 回合请求构造输入安全上下文。

        :param request: 当前 Agent 回合请求。
        :return: 返回输入安全请求上下文。
        """
        identity = request.trusted_identity
        return cls(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
            text=request.joined_text(),
            attachments=tuple(request.attachments),
            metadata=dict(request.metadata),
        )

    def to_policy_input(self, candidates: tuple[InputSafetyCandidate, ...]) -> dict[str, Any]:
        """转换为 OPA 策略请求输入。

        :param candidates: 已采集的输入安全候选。
        :return: 返回可提交给 OPA 的策略输入。
        """
        return {
            "request": {
                "request_id": self.request_id,
                "trace_id": self.trace_id,
                "user_id": self.user_id,
                "pet_id": self.pet_id,
                "session_id": self.session_id,
                "text_length": len(self.text),
                "attachment_count": len(self.attachments),
                "metadata": dict(self.metadata),
            },
            "attachments": [
                {
                    "attachment_id": item.attachment_id,
                    "mime_type": item.mime_type,
                    "purpose": item.purpose,
                    "storage_ref": item.storage_ref,
                    "metadata": dict(item.metadata),
                }
                for item in self.attachments
            ],
            "candidates": [candidate.to_policy_input() for candidate in candidates],
        }
