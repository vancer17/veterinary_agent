"""
=============================================================================
文件：src/vet_agent/consultation_state/policy.py
作用：定义问诊状态与回答充分性策略契约，以及 OPA、本地测试裁决客户端。
范围：位于问诊状态合并与追问规划之间；仅消费已结构化的状态、证据与意图，
      不扫描用户原始文本，不实现关键词匹配，不承担问题生成职责。
说明：生产路径应使用 OPA Data API；本地裁决器仅供测试或显式注入场景使用。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .errors import ConsultationStateDependencyError
from .models import (
    AnswerabilityDecision,
    ConsultationStatePolicyAction,
    ConsultationStatePolicyInput,
)


class ConsultationAnswerabilityPolicyClient(Protocol):
    """定义问诊回答充分性策略客户端协议。

    :return: 无返回值；业务层通过该协议隔离 OPA 传输实现。
    """

    async def decide(
        self, policy_input: ConsultationStatePolicyInput
    ) -> AnswerabilityDecision:
        """对本轮问诊状态执行回答充分性裁决。

        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :return: 返回问诊回答充分性策略决策。
        """
        ...

    def is_ready(self) -> bool:
        """检查策略客户端是否就绪。

        :return: 客户端具备调用所需配置时返回 True。
        """
        ...


@dataclass(frozen=True)
class ConsultationStatePolicyDecisionPayload:
    """表示问诊回答充分性策略返回的结构化负载。

    :param action: OPA 返回的有限策略动作。
    :param allow: 是否允许进入阶段性回答。
    :param mode: 决策模式。
    :param answer_scope: 回答范围。
    :param blocking_slots: 仍建议追问的槽位。
    :param unresolved_slots: 尚未确认但不再机械阻塞的槽位。
    :param reason: 决策原因。
    :param reasons: 更细粒度的策略原因列表。
    :return: 无返回值。
    """

    action: ConsultationStatePolicyAction
    allow: bool
    mode: str
    answer_scope: str
    blocking_slots: tuple[str, ...] = ()
    unresolved_slots: tuple[str, ...] = ()
    reason: str = ""
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_decision(
        self, *, backend: str, policy_path: str, policy_payload: dict[str, Any]
    ) -> AnswerabilityDecision:
        """转换为问诊状态链路可消费的统一决策对象。

        :param backend: 策略后端名称。
        :param policy_path: 策略路径。
        :param policy_payload: 策略输入或返回负载摘要。
        :return: 返回问诊回答充分性决策。
        """
        payload = dict(policy_payload)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return AnswerabilityDecision(
            decision=self.action.value,
            mode=self.mode,
            answer_scope=self.answer_scope,
            blocking_slots=list(self.blocking_slots),
            unresolved_slots=list(self.unresolved_slots),
            reason=self.reason,
            policy_backend=backend,
            policy_path=policy_path,
            policy_payload=payload,
        )


class OpaConsultationAnswerabilityPolicyClient(ConsultationAnswerabilityPolicyClient):
    """通过 OPA Data API 执行问诊回答充分性策略裁决。

    :return: 无返回值；该实现是生产环境默认后端。
    """

    def __init__(
        self,
        *,
        base_url: str,
        version: str,
        package_path: str,
        rule_name: str,
        auth_token: str | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        """初始化 OPA 问诊回答充分性策略客户端。

        :param base_url: OPA Data API 基准地址。
        :param version: OPA REST API 版本，例如 v1。
        :param package_path: OPA package 数据路径。
        :param rule_name: OPA 决策规则名称。
        :param auth_token: 可选的 OPA 鉴权令牌。
        :param timeout_seconds: 单次 OPA 请求超时时间。
        :return: 无返回值。
        """
        normalized_base_url = base_url.strip().rstrip("/")
        normalized_version = version.strip().strip("/")
        if normalized_base_url.endswith(f"/{normalized_version}"):
            self.base_url = normalized_base_url
        else:
            self.base_url = f"{normalized_base_url}/{normalized_version}"
        self.version = normalized_version
        self.package_path = package_path.strip("/")
        self.rule_name = rule_name.strip("/")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

    async def decide(
        self, policy_input: ConsultationStatePolicyInput
    ) -> AnswerabilityDecision:
        """向 OPA 提交问诊状态结构化输入并解析结果。

        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :return: 返回问诊回答充分性策略决策。
        :raises ConsultationStateDependencyError: OPA 不可用或响应契约不合法时抛出。
        """
        url = self._decision_url()
        headers = {"Content-Type": "application/json"}
        context = policy_input.context
        if context.request_id:
            headers["X-Request-ID"] = context.request_id
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url, headers=headers, json={"input": policy_input.to_policy_input()}
                )
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            raise ConsultationStateDependencyError(
                "consultation answerability OPA policy decision failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        payload = self._unwrap_result(result)
        decision = _decision_payload_from_dict(payload)
        return decision.to_decision(
            backend="opa",
            policy_path=f"{self.package_path}/{self.rule_name}",
            policy_payload={"input": policy_input.to_policy_input(), "result": payload},
        )

    def is_ready(self) -> bool:
        """检查 OPA 策略客户端配置是否完整。

        :return: OPA 连接参数齐全时返回 True。
        """
        return bool(
            self.base_url and self.version and self.package_path and self.rule_name
        )

    def _decision_url(self) -> str:
        """构造 OPA Data API 裁决 URL。

        :return: 返回可提交裁决请求的完整 URL。
        """
        package_parts = [
            quote(part, safe="")
            for part in self.package_path.replace("/", ".").split(".")
            if part
        ]
        rule = quote(self.rule_name, safe="")
        path = "/".join([*package_parts, rule])
        return f"{self.base_url}/data/{path}"

    def _unwrap_result(self, result: Any) -> dict[str, Any]:
        """归一 OPA 客户端返回结构。

        :param result: OPA 客户端原始返回。
        :return: 返回裁决字典。
        :raises ConsultationStateDependencyError: 响应结构不合法时抛出。
        """
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            return dict(result["result"])
        if isinstance(result, dict):
            return dict(result)
        raise ConsultationStateDependencyError(
            "consultation answerability OPA policy returned invalid decision payload",
            details={"reason": "invalid_opa_result_payload"},
        )


class LocalConsultationAnswerabilityPolicyClient(ConsultationAnswerabilityPolicyClient):
    """显式注入的本地问诊回答充分性策略客户端。

    说明：该实现只消费结构化状态、证据和意图，不扫描用户原始文本；
    仅供测试或开发场景使用，生产容器不会自动选择该实现。

    :return: 无返回值。
    """

    async def decide(
        self, policy_input: ConsultationStatePolicyInput
    ) -> AnswerabilityDecision:
        """对本轮问诊状态执行本地最小裁决。

        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :return: 返回问诊回答充分性策略决策。
        """
        minimum_context = self._has_minimum_context(policy_input)
        clinical_safety_precondition_unknown = bool(
            policy_input.evidence_profile.get("clinical_safety_precondition_unknown")
        )
        known_category_count = int(
            policy_input.evidence_profile.get("known_category_count") or 0
        )
        unresolved_slots = list(policy_input.unresolved_slots)
        advisory_slots = list(policy_input.advisory_slots)
        blocking_slots = advisory_slots or unresolved_slots

        if policy_input.intent.answer_now and minimum_context:
            return self._answer(
                mode="user_requested_answer_now",
                unresolved_slots=tuple(unresolved_slots),
                reason="用户明确要求根据现有信息先给阶段性判断。",
                policy_input=policy_input,
            )

        if (
            minimum_context
            and not unresolved_slots
            and not clinical_safety_precondition_unknown
        ):
            return self._answer(
                mode="slot_complete",
                unresolved_slots=tuple(unresolved_slots),
                reason="结构化证据已经足以支撑阶段性回答。",
                policy_input=policy_input,
            )

        if (
            minimum_context
            and policy_input.state.followup_rounds >= 1
            and known_category_count >= policy_input.limits.min_known_categories
            and not clinical_safety_precondition_unknown
        ):
            return self._answer(
                mode="sufficient_semantic_evidence",
                unresolved_slots=tuple(unresolved_slots),
                reason="已获得足够的结构化证据覆盖。",
                policy_input=policy_input,
            )

        if (
            minimum_context
            and policy_input.state.followup_rounds
            >= policy_input.limits.max_followup_rounds
        ):
            return self._answer(
                mode="max_followup_rounds_reached",
                unresolved_slots=tuple(unresolved_slots),
                reason="已达到连续追问轮数上限。",
                policy_input=policy_input,
            )

        if (
            minimum_context
            and clinical_safety_precondition_unknown
            and policy_input.state.followup_rounds
            < policy_input.limits.max_followup_rounds
        ):
            return self._ask(
                mode="clinical_safety_precondition_unknown",
                blocking_slots=tuple(
                    blocking_slots[: policy_input.limits.max_questions]
                ),
                unresolved_slots=tuple(unresolved_slots),
                policy_input=policy_input,
                reason="临床安全前提仍缺少关键症状或背景信息。",
            )

        return self._ask(
            mode="needs_high_value_evidence",
            blocking_slots=tuple(blocking_slots[: policy_input.limits.max_questions]),
            unresolved_slots=tuple(unresolved_slots),
            policy_input=policy_input,
            reason="仍缺少会明显影响分诊建议的高价值信息。",
        )

    def is_ready(self) -> bool:
        """检查本地策略客户端是否可用。

        :return: 始终返回 True。
        """
        return True

    def _has_minimum_context(self, policy_input: ConsultationStatePolicyInput) -> bool:
        """判断是否具备阶段性回答的最低上下文。

        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :return: 同时具备主诉和物种信息时返回 True。
        """
        return policy_input.state.has_chief_complaint and policy_input.state.has_species

    def _answer(
        self,
        *,
        mode: str,
        unresolved_slots: tuple[str, ...],
        reason: str,
        policy_input: ConsultationStatePolicyInput,
    ) -> AnswerabilityDecision:
        """构造允许阶段性回答的决策。

        :param mode: 回答模式。
        :param unresolved_slots: 尚未确认但不再阻塞的证据槽位。
        :param reason: 决策原因。
        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :return: 返回问诊回答充分性决策。
        """
        payload = policy_input.to_policy_input()
        decision = ConsultationStatePolicyDecisionPayload(
            action=ConsultationStatePolicyAction.ANSWER,
            allow=True,
            mode=mode,
            answer_scope="preliminary",
            blocking_slots=(),
            unresolved_slots=unresolved_slots,
            reason=reason,
            metadata={"policy_backend": "local"},
        )
        return decision.to_decision(
            backend="local", policy_path="local", policy_payload=payload
        )

    def _ask(
        self,
        *,
        mode: str,
        blocking_slots: tuple[str, ...],
        unresolved_slots: tuple[str, ...],
        policy_input: ConsultationStatePolicyInput,
        reason: str,
    ) -> AnswerabilityDecision:
        """构造继续追问的决策。

        :param mode: 追问决策模式。
        :param blocking_slots: 当前建议继续追问的槽位。
        :param unresolved_slots: 尚未确认但不再机械阻塞的槽位。
        :param policy_input: 已完成本地状态合并和证据归一的策略输入。
        :param reason: 决策原因。
        :return: 返回问诊回答充分性决策。
        """
        payload = policy_input.to_policy_input()
        decision = ConsultationStatePolicyDecisionPayload(
            action=ConsultationStatePolicyAction.ASK,
            allow=False,
            mode=mode,
            answer_scope="insufficient",
            blocking_slots=blocking_slots,
            unresolved_slots=unresolved_slots,
            reason=reason,
            metadata={"policy_backend": "local"},
        )
        return decision.to_decision(
            backend="local", policy_path="local", policy_payload=payload
        )


def _decision_payload_from_dict(
    payload: dict[str, Any],
) -> ConsultationStatePolicyDecisionPayload:
    """将 OPA 返回字典转换为问诊回答充分性策略负载。

    :param payload: OPA 返回的裁决字典。
    :return: 返回结构化策略负载。
    :raises ConsultationStateDependencyError: 策略动作非法时抛出。
    """
    required_fields = (
        "action",
        "allow",
        "mode",
        "answer_scope",
        "blocking_slots",
        "unresolved_slots",
        "reason",
    )
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid decision payload",
            details={
                "reason": "missing_required_fields",
                "missing_fields": missing_fields,
                "payload_keys": sorted(str(key) for key in payload.keys()),
            },
        )
    if not isinstance(payload["allow"], bool):
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid decision payload",
            details={
                "reason": "invalid_allow_type",
                "allow_type": type(payload["allow"]).__name__,
            },
        )
    if not isinstance(payload["blocking_slots"], list) or not isinstance(
        payload["unresolved_slots"], list
    ):
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid decision payload",
            details={
                "reason": "invalid_slot_list_type",
                "blocking_slots_type": type(payload["blocking_slots"]).__name__,
                "unresolved_slots_type": type(payload["unresolved_slots"]).__name__,
            },
        )

    raw_action = str(payload["action"])
    try:
        action = ConsultationStatePolicyAction(raw_action)
    except ValueError as exc:
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid action",
            details={"action": raw_action},
        ) from exc
    allow = payload["allow"]
    if allow != (action == ConsultationStatePolicyAction.ANSWER):
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid decision payload",
            details={
                "reason": "inconsistent_action_allow",
                "action": action.value,
                "allow": allow,
            },
        )
    raw_blocking_slots = payload["blocking_slots"]
    raw_unresolved_slots = payload["unresolved_slots"]
    reasons = tuple(
        str(item) for item in payload.get("reasons", []) if str(item).strip()
    )
    mode = str(payload["mode"]).strip()
    answer_scope = str(payload["answer_scope"]).strip()
    reason = str(payload["reason"]).strip()
    if not mode or not answer_scope or not reason:
        raise ConsultationStateDependencyError(
            "consultation answerability policy returned invalid decision payload",
            details={"reason": "empty_required_text_field"},
        )
    return ConsultationStatePolicyDecisionPayload(
        action=action,
        allow=allow,
        mode=mode,
        answer_scope=answer_scope,
        blocking_slots=tuple(
            str(item) for item in raw_blocking_slots if str(item).strip()
        ),
        unresolved_slots=tuple(
            str(item) for item in raw_unresolved_slots if str(item).strip()
        ),
        reason=reason,
        reasons=reasons,
        metadata={"policy_payload": payload},
    )
