"""
文件：src/vet_agent/output_safety/models.py
作用：定义输出清洗与安全复核迁移后的结构化候选、策略裁决和复核上下文。
范围：位于回复生成之后、记忆抽取与持久化之前；本文件只描述数据链事实，不执行文本替换、关键词扫描或策略调用。
说明：候选表示 Guardrails 等检测器发现的待裁决事实，最终 allow、observe、block、escalate 或 rewrite 动作由策略层决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from vet_agent import AgentTurnResponse, SafetySignal


class OutputSafetyCandidateCategory(StrEnum):
    """表示输出安全候选的有限类别。

    :return: 无返回值；枚举值用于策略输入、仓储定义和审计 metadata 的稳定分类。
    """

    PROMPT_LEAKAGE = "prompt_leakage"
    PII = "pii"
    SECRET = "secret"
    DOSAGE = "dosage"
    MEDICATION = "medication"
    TOPIC_BOUNDARY = "topic_boundary"
    FORMAT = "format"


class OutputSafetyCandidateSource(StrEnum):
    """表示输出安全候选的产生来源。

    :return: 无返回值；枚举值用于区分 Guardrails、结构化响应字段和策略层补充信号。
    """

    GUARDRAILS = "guardrails"
    STRUCTURED_RESPONSE = "structured_response"
    POLICY = "policy"


class OutputSafetyDecisionAction(StrEnum):
    """表示策略层对输出安全候选的有限动作裁决。

    :return: 无返回值；枚举值用于约束输出复核动作，不允许业务层扩展隐式动作。
    """

    ALLOW = "allow"
    OBSERVE = "observe"
    REWRITE = "rewrite"
    BLOCK = "block"
    ESCALATE = "escalate"


class OutputSafetyMode(StrEnum):
    """表示输出安全门禁的运行模式。

    :return: 无返回值；枚举值用于控制复核服务是否只审计或执行策略动作。
    """

    DISABLED = "disabled"
    OBSERVE = "observe"
    ENFORCE = "enforce"


@dataclass(frozen=True)
class OutputSafetySegment:
    """表示输出安全复核使用的响应片段快照。

    :param segment_id: 响应片段稳定标识。
    :param segment_type: 响应片段类型。
    :param title: 响应片段标题。
    :param status: 响应片段状态。
    :param text: 面向用户可见的片段文本。
    :return: 无返回值。
    """

    segment_id: str
    segment_type: str
    title: str
    status: str
    text: str

    def to_policy_input(self) -> dict[str, Any]:
        """转换为策略输入中的片段字典。

        :return: 返回可 JSON 序列化的片段快照。
        """
        return {
            "segment_id": self.segment_id,
            "type": self.segment_type,
            "title": self.title,
            "status": self.status,
            "text": self.text,
        }


@dataclass(frozen=True)
class OutputSafetyCandidate:
    """表示输出安全检测器采集到的结构化候选事实。

    :param code: 候选编码，用于 OPA 策略、审计与响应安全信号关联。
    :param category: 候选类别。
    :param source: 候选来源。
    :param severity: 候选建议严重级别；最终动作仍以策略裁决为准。
    :param message: 面向审计和策略的候选说明。
    :param confidence: 候选置信度，范围为 0 到 1。
    :param segment_id: 候选关联的响应片段标识；为空时表示整体响应级候选。
    :param matched_terms: 候选关联的结构化线索，不得来自业务层字符串修补路径。
    :param metadata: 候选附加审计信息。
    :return: 无返回值。
    """

    code: str
    category: OutputSafetyCandidateCategory
    source: OutputSafetyCandidateSource
    severity: str
    message: str
    confidence: float = 1.0
    segment_id: str | None = None
    matched_terms: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_policy_input(self) -> dict[str, Any]:
        """转换为策略输入中的候选字典。

        :return: 返回可 JSON 序列化的候选字典。
        """
        return {
            "code": self.code,
            "category": self.category.value,
            "source": self.source.value,
            "severity": self.severity,
            "message": self.message,
            "confidence": self.confidence,
            "segment_id": self.segment_id,
            "matched_terms": list(self.matched_terms),
            "metadata": dict(self.metadata),
        }

    def to_signal(self, *, severity: str | None = None, message: str | None = None) -> SafetySignal:
        """转换为核心响应契约使用的安全信号。

        :param severity: 策略裁决后的安全信号级别；为空时使用候选默认级别。
        :param message: 策略裁决后的说明；为空时使用候选默认说明。
        :return: 返回安全信号。
        """
        return SafetySignal(
            code=self.code,
            severity=severity or self.severity,
            message=message or self.message,
            matched_terms=list(self.matched_terms),
        )


@dataclass(frozen=True)
class OutputSafetyDecision:
    """表示策略层对输出安全候选的裁决结果。

    :param action: 策略动作。
    :param allow: 是否允许当前响应继续交付。
    :param message: 策略裁决说明，用于审计。
    :param reasons: 策略原因列表。
    :param candidates: 参与裁决的候选列表。
    :param signals: 需要进入 Agent 响应的安全信号列表。
    :param replacement_text: 策略层给出的整段替换文本；仅 block、escalate 或后续 rewrite 可使用。
    :param metadata: 策略裁决原始附加信息。
    :return: 无返回值。
    """

    action: OutputSafetyDecisionAction
    allow: bool
    message: str
    reasons: tuple[str, ...] = ()
    candidates: tuple[OutputSafetyCandidate, ...] = ()
    signals: tuple[SafetySignal, ...] = ()
    replacement_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """判断当前输出安全裁决是否要求阻断响应交付。

        :return: 阻断时返回 True。
        """
        return self.action == OutputSafetyDecisionAction.BLOCK or not self.allow

    @property
    def escalated(self) -> bool:
        """判断当前输出安全裁决是否要求升级为安全响应。

        :return: 需要升级时返回 True。
        """
        return self.action == OutputSafetyDecisionAction.ESCALATE

    @property
    def rewrite_requested(self) -> bool:
        """判断当前输出安全裁决是否要求改写响应。

        :return: 策略动作是 rewrite 时返回 True。
        """
        return self.action == OutputSafetyDecisionAction.REWRITE

    @classmethod
    def allow_response(cls, *, metadata: dict[str, Any] | None = None) -> "OutputSafetyDecision":
        """构造允许响应继续交付的默认裁决。

        :param metadata: 附加审计信息。
        :return: 返回允许裁决。
        """
        return cls(
            action=OutputSafetyDecisionAction.ALLOW,
            allow=True,
            message="输出安全策略允许当前响应继续交付。",
            metadata=metadata or {},
        )

    def to_metadata(self) -> dict[str, Any]:
        """转换为响应 metadata 中的可审计结构。

        :return: 返回输出安全裁决 metadata。
        """
        return {
            "action": self.action.value,
            "allow": self.allow,
            "message": self.message,
            "reasons": list(self.reasons),
            "candidates": [candidate.to_policy_input() for candidate in self.candidates],
            "signals": [signal.model_dump(mode="json") for signal in self.signals],
            "replacement_text_present": bool(self.replacement_text),
            **dict(self.metadata),
        }


@dataclass(frozen=True)
class OutputSafetyReviewContext:
    """表示输出安全策略裁决所需的响应上下文。

    :param request_id: 本轮请求标识。
    :param trace_id: 本轮链路追踪标识。
    :param response_id: Agent 响应标识。
    :param model: 当前响应使用的模型名称。
    :param status: 当前响应状态。
    :param output_text: 当前整体响应文本。
    :param segments: 当前响应片段快照。
    :param metadata: 当前响应 metadata 快照。
    :return: 无返回值。
    """

    request_id: str
    trace_id: str
    response_id: str
    model: str
    status: str
    output_text: str
    segments: tuple[OutputSafetySegment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: AgentTurnResponse) -> "OutputSafetyReviewContext":
        """从 Agent 响应构造输出安全复核上下文。

        :param response: 待复核的 Agent 响应。
        :return: 返回输出安全复核上下文。
        """
        return cls(
            request_id=response.request_id,
            trace_id=response.trace_id,
            response_id=response.id,
            model=response.model,
            status=response.status,
            output_text=response.output_text,
            segments=tuple(
                OutputSafetySegment(
                    segment_id=segment.segment_id,
                    segment_type=segment.type,
                    title=segment.title,
                    status=segment.status,
                    text=segment.output_text or segment.content,
                )
                for segment in response.segments
            ),
            metadata=dict(response.metadata),
        )

    def texts_for_detection(self) -> tuple[OutputSafetySegment, ...]:
        """生成检测器需要逐段扫描的文本片段。

        :return: 返回待检测片段元组；无显式片段时使用整体输出构造虚拟片段。
        """
        if self.segments:
            return self.segments
        return (
            OutputSafetySegment(
                segment_id=self.response_id,
                segment_type="response",
                title="整体响应",
                status=self.status,
                text=self.output_text,
            ),
        )

    def to_policy_input(self, candidates: tuple[OutputSafetyCandidate, ...]) -> dict[str, Any]:
        """转换为策略请求输入。

        :param candidates: 本轮输出安全候选。
        :return: 返回可提交给策略引擎的输入字典。
        """
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "response_id": self.response_id,
            "model": self.model,
            "status": self.status,
            "output_text": self.output_text,
            "segments": [segment.to_policy_input() for segment in self.segments],
            "candidates": [candidate.to_policy_input() for candidate in candidates],
            "metadata": dict(self.metadata),
        }
