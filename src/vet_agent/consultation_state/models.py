"""
=============================================================================
文件：src/vet_agent/consultation_state/models.py
作用：定义问诊状态与回答充分性迁移链路的稳定领域模型。
范围：承载当前 session 与 task 范围内的活跃问诊状态、回答充分性摘要、
      策略输入所需的请求范围与状态摘要，以及主链路向上层暴露的决策对象。
说明：本文件只定义跨数据链传递的结构化对象，不访问数据库、不调用外部服务、
      不扫描用户原始文本，也不承载 OPA 传输实现。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

SlotValue = str | bool | None


class ConsultationStatePolicyAction(StrEnum):
    """表示回答充分性策略能够返回的有限动作集合。

    :return: 无返回值；该枚举用于 OPA 与应用编排层之间的稳定动作契约。
    """

    ANSWER = "answer"
    ASK = "ask"
    REJECT = "reject"


@dataclass(frozen=True)
class ConsultationStatePolicyContext:
    """表示回答充分性策略裁决所需的可信请求范围摘要。

    :param request_id: 当前回合请求标识。
    :param trace_id: 当前回合追踪标识。
    :param user_id: 当前可信用户标识。
    :param pet_id: 当前可信宠物标识。
    :param session_id: 当前可信会话标识。
    :return: 无返回值。
    """

    request_id: str = ""
    trace_id: str = ""
    user_id: str = ""
    pet_id: str = ""
    session_id: str = ""

    def to_policy_input(self) -> dict[str, str]:
        """转换为 OPA 使用的结构化请求范围字典。

        :return: 返回不包含用户原始文本的请求范围摘要。
        """
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "pet_id": self.pet_id,
            "session_id": self.session_id,
        }

    @classmethod
    def from_identity(
        cls,
        *,
        request_id: str = "",
        trace_id: str = "",
        user_id: str = "",
        pet_id: str = "",
        session_id: str = "",
    ) -> "ConsultationStatePolicyContext":
        """从离散身份字段构造策略请求范围摘要。

        :param request_id: 当前回合请求标识。
        :param trace_id: 当前回合追踪标识。
        :param user_id: 当前可信用户标识。
        :param pet_id: 当前可信宠物标识。
        :param session_id: 当前可信会话标识。
        :return: 返回策略请求范围对象。
        """
        return cls(
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
        )


@dataclass(frozen=True)
class ConsultationStatePolicyState:
    """表示回答充分性策略裁决所需的活跃问诊状态摘要。

    :param domain: 当前任务域。
    :param phase: 当前问诊阶段。
    :param followup_rounds: 当前任务连续追问轮数。
    :param asked_question_count: 当前任务已问问题数量。
    :param has_chief_complaint: 是否已具备主诉摘要。
    :param has_species: 是否已具备宠物物种信息。
    :return: 无返回值。
    """

    domain: str
    phase: str
    followup_rounds: int
    asked_question_count: int
    has_chief_complaint: bool
    has_species: bool

    def to_policy_input(self) -> dict[str, Any]:
        """转换为 OPA 使用的结构化状态摘要。

        :return: 返回活跃问诊状态摘要字典。
        """
        return {
            "domain": self.domain,
            "phase": self.phase,
            "followup_rounds": self.followup_rounds,
            "asked_question_count": self.asked_question_count,
            "has_chief_complaint": self.has_chief_complaint,
            "has_species": self.has_species,
        }

    @classmethod
    def from_state(cls, state: "ConsultationState") -> "ConsultationStatePolicyState":
        """从活跃问诊状态构造策略状态摘要。

        :param state: 当前活跃问诊状态。
        :return: 返回用于策略裁决的状态摘要。
        """
        return cls(
            domain=state.domain,
            phase=state.phase,
            followup_rounds=state.followup_rounds,
            asked_question_count=len(state.asked_questions),
            has_chief_complaint=bool(state.chief_complaint),
            has_species=bool(state.slots.get("species")),
        )


@dataclass(frozen=True)
class ConsultationStatePolicyIntent:
    """表示回答充分性策略裁决所需的用户意图摘要。

    :param answer_now: 用户是否明确要求根据现有信息先答。
    :param wants_triage: 用户是否明确希望得到分诊或紧急度判断。
    :param correction: 用户是否明确纠正当前会话事实。
    :param raw_intent: 用户意图简短审计摘要。
    :return: 无返回值。
    """

    answer_now: bool = False
    wants_triage: bool = False
    correction: bool = False
    raw_intent: str = ""

    def to_policy_input(self) -> dict[str, Any]:
        """转换为 OPA 使用的结构化意图摘要。

        :return: 返回意图信号字典。
        """
        return {
            "answer_now": self.answer_now,
            "wants_triage": self.wants_triage,
            "correction": self.correction,
            "raw_intent": self.raw_intent,
        }


@dataclass(frozen=True)
class ConsultationStatePolicyLimits:
    """表示回答充分性策略裁决使用的固定门槛参数。

    :param max_followup_rounds: 同一任务允许的最大连续追问轮数。
    :param min_known_categories: 进入阶段性回答所需的最小已知证据维度数。
    :param max_questions: 本轮最多允许生成的追问问题数量。
    :return: 无返回值。
    """

    max_followup_rounds: int
    min_known_categories: int = 2
    max_questions: int = 3

    def to_policy_input(self) -> dict[str, int]:
        """转换为 OPA 使用的门槛参数字典。

        :return: 返回固定门槛参数。
        """
        return {
            "max_followup_rounds": self.max_followup_rounds,
            "min_known_categories": self.min_known_categories,
            "max_questions": self.max_questions,
        }


@dataclass(frozen=True)
class ConsultationStatePolicyInput:
    """表示提交给回答充分性 OPA 策略的完整结构化输入。

    :param context: 当前回合可信请求范围摘要。
    :param state: 当前问诊状态摘要。
    :param intent: 当前用户意图摘要。
    :param limits: 本轮回答充分性门槛参数。
    :param evidence_profile: 结构化证据画像。
    :param unresolved_slots: 仍需确认但尚未进入硬阻塞的槽位。
    :param advisory_slots: 本轮建议追问的槽位顺序。
    :return: 无返回值。
    """

    context: ConsultationStatePolicyContext
    state: ConsultationStatePolicyState
    intent: ConsultationStatePolicyIntent
    limits: ConsultationStatePolicyLimits
    evidence_profile: dict[str, Any]
    unresolved_slots: tuple[str, ...] = field(default_factory=tuple)
    advisory_slots: tuple[str, ...] = field(default_factory=tuple)

    def to_policy_input(self) -> dict[str, Any]:
        """转换为 OPA Data API 请求所需的结构化 JSON 负载。

        :return: 返回不包含原始文本扫描路径的策略输入字典。
        """
        return {
            "context": self.context.to_policy_input(),
            "state": self.state.to_policy_input(),
            "intent": self.intent.to_policy_input(),
            "limits": self.limits.to_policy_input(),
            "evidence_profile": dict(self.evidence_profile),
            "unresolved_slots": list(self.unresolved_slots),
            "advisory_slots": list(self.advisory_slots),
        }


@dataclass
class ConsultationState:
    """表示当前 session 与 task 范围内的活跃问诊状态。

    :param chief_complaint: 当前问诊主诉摘要。
    :param domain: 当前任务域。
    :param phase: 当前问诊阶段。
    :param slots: 当前回答提示词兼容使用的核心槽位派生视图。
    :param working_facts: 当前会话范围内的结构化核心事实工作记忆。
    :param observations: 当前会话范围内的开放观察。
    :param asked_questions: 已问过的追问问题。
    :param followup_rounds: 当前任务连续追问轮数。
    :param evidence_profile: 结构化证据画像。
    :param answerability: 回答充分性策略结果。
    :param user_intent: 本轮用户意图信号。
    :param semantic_extraction: 本轮问诊语义抽取 metadata。
    :param temporal_context: 临床安全链路提供的时间上下文摘要。
    :return: 无返回值。
    """

    chief_complaint: str | None = None
    domain: str = "general"
    phase: str = "collecting_info"
    slots: dict[str, SlotValue] = field(default_factory=dict)
    working_facts: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    asked_questions: list[str] = field(default_factory=list)
    followup_rounds: int = 0
    evidence_profile: dict[str, Any] = field(default_factory=dict)
    answerability: dict[str, Any] = field(default_factory=dict)
    user_intent: dict[str, Any] = field(default_factory=dict)
    semantic_extraction: dict[str, Any] = field(default_factory=dict)
    temporal_context: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConsultationState":
        """从普通字典恢复问诊状态。

        :param data: 结构化状态字典。
        :return: 返回函数执行结果。
        """
        if not data:
            return cls()
        return cls(
            chief_complaint=data.get("chief_complaint"),
            domain=data.get("domain") or "general",
            phase=data.get("phase") or "collecting_info",
            slots=dict(data.get("slots") or {}),
            working_facts=list(data.get("working_facts") or []),
            observations=list(data.get("observations") or []),
            asked_questions=list(data.get("asked_questions") or []),
            followup_rounds=int(data.get("followup_rounds") or 0),
            evidence_profile=dict(data.get("evidence_profile") or {}),
            answerability=dict(data.get("answerability") or {}),
            user_intent=dict(data.get("user_intent") or {}),
            semantic_extraction=dict(data.get("semantic_extraction") or {}),
            temporal_context=dict(data.get("temporal_context") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化的普通字典。

        :return: 返回函数执行结果。
        """
        return {
            "chief_complaint": self.chief_complaint,
            "domain": self.domain,
            "phase": self.phase,
            "slots": self.slots,
            "working_facts": self.working_facts,
            "observations": self.observations,
            "asked_questions": self.asked_questions,
            "followup_rounds": self.followup_rounds,
            "evidence_profile": self.evidence_profile,
            "answerability": self.answerability,
            "user_intent": self.user_intent,
            "semantic_extraction": self.semantic_extraction,
            "temporal_context": self.temporal_context,
        }

    def has_chief_complaint(self) -> bool:
        """判断当前状态是否已具备主诉。

        :return: 主诉存在且非空时返回 True。
        """
        return bool((self.chief_complaint or "").strip())

    def has_species(self) -> bool:
        """判断当前状态是否已具备物种信息。

        :return: 物种槽位存在且非空时返回 True。
        """
        return bool(self.slots.get("species"))


@dataclass(frozen=True)
class AnswerabilityDecision:
    """表示回答充分性策略给出的下一步动作建议。

    :param decision: 策略动作，answer 表示阶段性回答，ask 表示继续追问。
    :param mode: 决策模式。
    :param answer_scope: 回答范围。
    :param blocking_slots: 仍阻塞回答的高价值证据槽位。
    :param unresolved_slots: 尚未确认但可能不再阻塞的证据槽位。
    :param reason: 决策原因。
    :param policy_backend: 策略后端名称。
    :param policy_path: 策略路径。
    :param policy_payload: 策略输入或返回负载摘要。
    :return: 无返回值。
    """

    decision: str
    mode: str
    answer_scope: str
    blocking_slots: list[str]
    unresolved_slots: list[str]
    reason: str
    policy_backend: str = ""
    policy_path: str = ""
    policy_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为响应 metadata 和持久化状态使用的字典。

        :return: 返回函数执行结果。
        """
        return {
            "decision": self.decision,
            "mode": self.mode,
            "answer_scope": self.answer_scope,
            "blocking_slots": self.blocking_slots,
            "unresolved_slots": self.unresolved_slots,
            "reason": self.reason,
            "policy_backend": self.policy_backend,
            "policy_path": self.policy_path,
            "policy_payload": self.policy_payload,
        }


@dataclass(frozen=True)
class ConsultationDecision:
    """表示问诊状态合并后的本轮业务决策。

    :param state: 更新后的活跃问诊状态。
    :param ready: 是否进入阶段性回答路径。
    :param missing_slots: 本轮仍建议追问的槽位。
    :param questions: 本轮要输出的追问问题。
    :param answerability: 回答充分性策略 metadata。
    :return: 无返回值。
    """

    state: ConsultationState
    ready: bool
    missing_slots: list[str]
    questions: list[str]
    answerability: dict[str, Any] = field(default_factory=dict)
