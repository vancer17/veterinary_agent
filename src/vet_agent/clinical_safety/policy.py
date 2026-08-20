"""
=============================================================================
文件：src/vet_agent/clinical_safety/policy.py
作用：定义临床安全裁决的结构化策略契约与 OPA 策略客户端。
范围：位于临床安全候选召回之后、Agent 主链路分支之前；负责将已召回候选和可信
      结构化语义提交给 OPA，并将 OPA 结果转换为稳定的安全裁决对象。
说明：本文件不扫描用户原始文本、不生成临床安全候选、不执行医学推理，也不提供
      生产环境的本地硬编码裁决回退。生产容器必须显式使用 OPA；测试替身由测试
      代码通过协议注入。
依赖：SQLAlchemy 数据仓储通过 ClinicalSafetyRepository 协议隔离；本文件不直接
      访问数据库表模型。
=============================================================================
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from vet_agent import AgentTurnRequest, SafetySignal

from .fallback import ClinicalSafetyRetrievalState
from .models import ClinicalSafetyCandidate
from .precondition import (
    ClinicalSafetyPreconditionAssessment,
    clinical_safety_required_context_hash,
)
from .semantic_extractor import ClinicalSafetySemanticResult
from .thresholds import ClinicalSafetyThresholds


class ClinicalSafetyPolicyAction(StrEnum):
    """表示临床安全策略可以返回的有限动作集合。

    :return: 无返回值；枚举值用于 OPA 与 Agent 主链路之间的稳定动作契约。
    """

    ALLOW = "allow"
    OBSERVE = "observe"
    ESCALATE = "escalate"
    BLOCK = "block"


@dataclass(frozen=True)
class ClinicalSafetyPolicyRequestContext:
    """表示临床安全策略裁决所需的可信请求范围摘要。

    :param request_id: 当前 Agent 回合请求标识。
    :param trace_id: 当前 Agent 回合链路追踪标识。
    :param user_id: 当前回合可信用户标识。
    :param pet_id: 当前回合可信宠物标识。
    :param session_id: 当前回合可信会话标识。
    :return: 无返回值；该对象只保存策略审计所需的范围摘要。
    """

    request_id: str = ""
    trace_id: str = ""
    user_id: str = ""
    pet_id: str = ""
    session_id: str = ""

    @classmethod
    def from_request(
        cls, request: AgentTurnRequest
    ) -> "ClinicalSafetyPolicyRequestContext":
        """从 Agent 回合请求构造临床安全策略范围摘要。

        :param request: 当前 Agent 回合请求对象。
        :return: 返回由已验证请求范围派生的策略上下文。
        """
        identity = request.trusted_identity
        return cls(
            request_id=request.request_context.request_id,
            trace_id=request.request_context.trace_id,
            user_id=identity.user_id,
            pet_id=identity.pet_id,
            session_id=identity.session_id,
        )

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


@dataclass(frozen=True)
class ClinicalSafetyPolicyInput:
    """表示提交给临床安全 OPA 策略的完整结构化输入。

    :param context: 当前回合的可信请求范围摘要。
    :param semantic_result: 临床安全结构化语义结果；不可信结果只保留降级状态。
    :param retrieval_state: 临床安全向量召回运行状态。
    :param candidates: 已由 pgvector 召回并按资产聚合的候选列表。
    :param precondition_assessments: 候选自然语言前提的语义蕴含评估结果。
    :param thresholds: 候选动作策略使用的阈值配置。
    :return: 无返回值；该对象是 Python 到 OPA 的唯一输入边界。
    """

    context: ClinicalSafetyPolicyRequestContext
    semantic_result: ClinicalSafetySemanticResult | None
    retrieval_state: ClinicalSafetyRetrievalState
    candidates: tuple[ClinicalSafetyCandidate, ...]
    thresholds: ClinicalSafetyThresholds
    precondition_assessments: Mapping[str, ClinicalSafetyPreconditionAssessment] = (
        field(default_factory=dict)
    )

    def to_payload(self) -> dict[str, Any]:
        """转换为 OPA Data API 请求所需的结构化 JSON 负载。

        :return: 返回不包含用户原始文本扫描路径的策略输入字典。
        """
        return {
            "context": self.context.to_policy_input(),
            "semantic": _semantic_payload(self.semantic_result),
            "retrieval": self.retrieval_state.to_dict(),
            "candidates": [
                _candidate_payload(candidate) for candidate in self.candidates
            ],
            "precondition_assessments": _precondition_assessments_payload(
                self.candidates,
                self.precondition_assessments,
            ),
            "thresholds": {
                "signal_min_score": self.thresholds.signal_min_score,
                "urgent_min_score": self.thresholds.urgent_min_score,
            },
        }


@dataclass(frozen=True)
class ClinicalSafetyPolicyDecision:
    """表示 OPA 返回的临床安全动作与安全信号。

    :param action: OPA 返回的有限策略动作。
    :param allow: 是否允许主 Agent 继续普通问诊链路。
    :param message: 面向 Agent 主链路和安全响应的策略说明。
    :param reasons: OPA 返回的结构化策略原因。
    :param signals: OPA 返回的安全信号列表。
    :param metadata: 策略后端、策略路径和调用审计摘要。
    :return: 无返回值。
    """

    action: ClinicalSafetyPolicyAction
    allow: bool
    message: str
    reasons: tuple[str, ...] = ()
    signals: tuple[SafetySignal, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """判断本轮临床安全策略是否阻断主链路。

        :return: 动作为 block 或策略明确禁止继续时返回 True。
        """
        return self.action == ClinicalSafetyPolicyAction.BLOCK or not self.allow

    @property
    def escalated(self) -> bool:
        """判断本轮临床安全策略是否升级为安全分诊。

        :return: 动作为 escalate 时返回 True。
        """
        return self.action == ClinicalSafetyPolicyAction.ESCALATE

    def to_metadata(self) -> dict[str, Any]:
        """转换为 Agent 响应 metadata 中的策略审计摘要。

        :return: 返回可序列化的临床安全策略决策字典。
        """
        return {
            "action": self.action.value,
            "allow": self.allow,
            "message": self.message,
            "reasons": list(self.reasons),
            "signals": [signal.model_dump(mode="json") for signal in self.signals],
            **dict(self.metadata),
        }


class ClinicalSafetyPolicyClient(Protocol):
    """定义临床安全策略客户端协议。

    :return: 无返回值；业务层通过该协议隔离 OPA 传输实现。
    """

    async def decide(
        self, policy_input: ClinicalSafetyPolicyInput
    ) -> ClinicalSafetyPolicyDecision:
        """根据结构化候选和可信语义执行临床安全动作裁决。

        :param policy_input: 已完成候选召回和语义状态归一的策略输入。
        :return: 返回临床安全策略决策。
        :raises RuntimeError: 策略服务不可用或返回结构不合法时抛出。
        """
        ...

    async def plan_preconditions(
        self,
        policy_input: ClinicalSafetyPolicyInput,
    ) -> tuple[str, ...]:
        """计算需要进入自然语言前提评估的候选资产集合。

        :param policy_input: 未附加前提评估结果的基础策略输入。
        :return: 返回需要评估的 asset_id 元组。
        :raises RuntimeError: 策略服务不可用或返回结构不合法时抛出。
        """
        ...

    def is_ready(self) -> bool:
        """检查临床安全策略客户端配置是否完整。

        :return: 客户端具备调用所需配置时返回 True。
        """
        ...


class OpaClinicalSafetyPolicyClient(ClinicalSafetyPolicyClient):
    """通过 OPA Data API 执行临床安全策略裁决。

    :return: 无返回值；该实现是生产环境临床安全策略的默认后端。
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
        """初始化 OPA 临床安全策略客户端。

        :param base_url: OPA Data API 基准地址，可包含网关前缀。
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
        self, policy_input: ClinicalSafetyPolicyInput
    ) -> ClinicalSafetyPolicyDecision:
        """向 OPA 提交临床安全结构化策略输入并解析结果。

        :param policy_input: 已完成候选召回和可信语义归一的策略输入。
        :return: 返回 OPA 临床安全策略决策。
        :raises RuntimeError: OPA 调用失败或返回契约不合法时抛出。
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
                    url,
                    headers=headers,
                    json={"input": policy_input.to_payload()},
                )
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            raise RuntimeError("clinical safety OPA policy decision failed") from exc
        payload = self._unwrap_result(result)
        return _decision_from_payload(
            payload,
            policy_backend="opa",
            policy_path=f"{self.package_path}/{self.rule_name}",
        )

    async def plan_preconditions(
        self,
        policy_input: ClinicalSafetyPolicyInput,
    ) -> tuple[str, ...]:
        """请求 OPA 计算需要自然语言前提评估的候选集合。

        :param policy_input: 未附加前提评估结果的基础策略输入。
        :return: 返回需要进入语义蕴含评估的 asset_id 元组。
        :raises RuntimeError: OPA 调用失败或返回结构不合法时抛出。
        """
        url = self._rule_url("precondition_plan")
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json={"input": policy_input.to_payload()},
                )
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            raise RuntimeError("clinical safety OPA precondition plan failed") from exc
        payload = self._unwrap_result(result)
        raw_asset_ids = payload.get("asset_ids")
        if not isinstance(raw_asset_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_asset_ids
        ):
            raise RuntimeError(
                "clinical safety OPA returned an invalid precondition plan"
            )
        return tuple(
            dict.fromkeys(item.strip() for item in raw_asset_ids if item.strip())
        )

    def is_ready(self) -> bool:
        """检查 OPA 临床安全策略客户端的连接参数是否完整。

        :return: OPA 地址、版本、策略包和规则名称均有效时返回 True。
        """
        return bool(
            self.base_url and self.version and self.package_path and self.rule_name
        )

    def _decision_url(self) -> str:
        """构造 OPA 临床安全决策 Data API URL。

        :return: 返回经过 URL 编码的临床安全策略决策地址。
        """
        return self._rule_url(self.rule_name)

    def _rule_url(self, rule_name: str) -> str:
        """构造临床安全指定规则的 OPA Data API URL。

        :param rule_name: OPA 规则名称。
        :return: 返回经过 URL 编码的临床安全规则地址。
        """
        package_parts = [
            quote(part, safe="")
            for part in self.package_path.replace("/", ".").split(".")
            if part
        ]
        rule = quote(rule_name.strip("/"), safe="")
        path = "/".join([*package_parts, rule])
        return f"{self.base_url}/data/{path}"

    def _unwrap_result(self, result: Any) -> dict[str, Any]:
        """从 OPA REST 响应中提取决策对象。

        :param result: OPA REST API 返回的 JSON 对象。
        :return: 返回 OPA 决策字典。
        :raises RuntimeError: 响应不是对象或缺少 result 对象时抛出。
        """
        if not isinstance(result, dict):
            raise RuntimeError("clinical safety OPA returned a non-object response")
        payload = result.get("result")
        if not isinstance(payload, dict):
            raise RuntimeError("clinical safety OPA returned an invalid result payload")
        return dict(payload)


def _semantic_payload(
    semantic_result: ClinicalSafetySemanticResult | None,
) -> dict[str, Any]:
    """构造临床安全策略使用的可信语义字段。

    :param semantic_result: LiteLLM 结构化语义结果或显式空结果。
    :return: 返回供 OPA 消费的语义状态与可信事实。
    """
    if semantic_result is None:
        return {
            "trusted": False,
            "stage": "skipped",
            "strategy": "not_requested",
            "confidence": 0.0,
            "fallback_reason": "clinical_safety_semantic_result_missing",
            "species": "unknown",
            "sex": "unknown",
            "age_group": "unknown",
            "exposure_state": "unknown",
            "symptom_state": "unknown",
            "temporal_state": "unknown",
            "temporal_scope": "unclear",
            "resolution_state": "unknown",
            "intent_type": "other",
            "risk_evidence_state": "unknown",
            "observed_features": [],
        }
    fallback_state = semantic_result.to_fallback_state()
    trusted = semantic_result.is_trusted()
    return {
        "trusted": trusted,
        "stage": fallback_state.stage,
        "strategy": semantic_result.strategy,
        "confidence": semantic_result.confidence,
        "fallback_reason": semantic_result.fallback_reason,
        "species": semantic_result.species if trusted else "unknown",
        "sex": semantic_result.sex if trusted else "unknown",
        "age_group": semantic_result.age_group if trusted else "unknown",
        "exposure_state": semantic_result.exposure_state if trusted else "unknown",
        "symptom_state": semantic_result.symptom_state if trusted else "unknown",
        "temporal_state": semantic_result.temporal_state if trusted else "unknown",
        "temporal_scope": semantic_result.temporal_scope if trusted else "unclear",
        "resolution_state": semantic_result.resolution_state if trusted else "unknown",
        "intent_type": semantic_result.intent_type if trusted else "other",
        "risk_evidence_state": semantic_result.risk_evidence_state
        if trusted
        else "unknown",
        "observed_features": (
            [feature.to_policy_dict() for feature in semantic_result.observed_features]
            if trusted
            else []
        ),
    }


def _candidate_payload(candidate: ClinicalSafetyCandidate) -> dict[str, Any]:
    """构造单个临床安全候选的策略输入字典。

    :param candidate: 已由向量召回器按资产聚合的候选。
    :return: 返回不包含原始资产全文的结构化候选字典。
    """
    asset = candidate.asset
    return {
        "asset_id": asset.asset_id,
        "code": asset.code,
        "asset_type": asset.asset_type,
        "canonical_name": asset.canonical_name,
        "species_scope": list(asset.species_scope),
        "sex_scope": list(asset.sex_scope),
        "age_scope": list(asset.age_scope),
        "severity": asset.severity,
        "action_class": asset.action_class,
        "score": candidate.score,
        "score_type": candidate.score_type,
        "retrieval_source": candidate.retrieval_source,
        "message": _candidate_message(candidate),
        "matched_terms": list(candidate.matched_terms()),
        "required_context": _required_context_payload(asset.required_context),
        "required_context_hash": clinical_safety_required_context_hash(
            asset.required_context
        ),
        "decision_hints": dict(asset.decision_hints),
    }


def _precondition_assessments_payload(
    candidates: tuple[ClinicalSafetyCandidate, ...],
    assessments: Mapping[str, ClinicalSafetyPreconditionAssessment],
) -> dict[str, dict[str, Any]]:
    """构造 OPA 使用的候选前提评估映射。

    :param candidates: 本轮参与策略裁决的候选列表。
    :param assessments: 以前提评估器返回的候选级评估映射。
    :return: 返回只包含候选关联、结构化状态和证据引用的评估投影。
    """
    payload: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not candidate.asset.required_context.get("symptoms", ()):
            continue
        assessment = assessments.get(candidate.asset.asset_id)
        if assessment is None:
            continue
        payload[candidate.asset.asset_id] = assessment.to_policy_dict()
    return payload


def _required_context_payload(
    value: dict[str, tuple[str, ...]],
) -> dict[str, list[str]]:
    """构造 OPA 裁决使用的候选前置上下文要求。

    :param value: 标准资产声明的 required_context。
    :return: 返回可序列化的 required_context 字典。
    """
    return {key: list(items) for key, items in value.items() if items}


def _candidate_message(candidate: ClinicalSafetyCandidate) -> str:
    """选择候选的安全信号展示文案。

    :param candidate: 已由向量召回器按资产聚合的候选。
    :return: 返回资产定义中的分诊文案、风险摘要或规范名称。
    """
    asset = candidate.asset
    if asset.triage_message:
        return asset.triage_message
    if asset.clinical_risk_summary:
        return asset.clinical_risk_summary
    return f"命中临床安全风险：{asset.canonical_name}"


def _decision_from_payload(
    payload: dict[str, Any],
    *,
    policy_backend: str,
    policy_path: str,
) -> ClinicalSafetyPolicyDecision:
    """将策略后端返回的决策字典转换为临床安全决策对象。

    :param payload: 策略后端返回的决策字典。
    :param policy_backend: 策略后端标识。
    :param policy_path: 策略包与规则路径。
    :return: 返回严格校验后的临床安全策略决策。
    :raises RuntimeError: 动作、allow、原因或安全信号结构不合法时抛出。
    """
    raw_action = payload.get("action")
    raw_allow = payload.get("allow")
    raw_message = payload.get("message")
    raw_reasons = payload.get("reasons")
    raw_signals = payload.get("signals")
    if not isinstance(raw_action, str):
        raise RuntimeError("clinical safety policy action is missing")
    if not isinstance(raw_allow, bool):
        raise RuntimeError("clinical safety policy allow must be boolean")
    if not isinstance(raw_message, str) or not raw_message.strip():
        raise RuntimeError("clinical safety policy message must be a non-empty string")
    if not isinstance(raw_reasons, list) or not all(
        isinstance(item, str) for item in raw_reasons
    ):
        raise RuntimeError("clinical safety policy reasons must be a string list")
    if not isinstance(raw_signals, list):
        raise RuntimeError("clinical safety policy signals must be a list")
    try:
        action = ClinicalSafetyPolicyAction(raw_action)
    except ValueError as exc:
        raise RuntimeError(
            f"invalid clinical safety policy action: {raw_action}"
        ) from exc
    if action == ClinicalSafetyPolicyAction.BLOCK and raw_allow:
        raise RuntimeError(
            "clinical safety policy block action cannot allow the main chain"
        )
    if action != ClinicalSafetyPolicyAction.BLOCK and not raw_allow:
        raise RuntimeError(
            "clinical safety non-block action cannot deny the main chain"
        )
    signals = tuple(_signal_from_payload(item) for item in raw_signals)
    if action == ClinicalSafetyPolicyAction.ALLOW and signals:
        raise RuntimeError("clinical safety allow action cannot return signals")
    if action == ClinicalSafetyPolicyAction.OBSERVE and any(
        signal.severity in {"urgent", "blocked"} for signal in signals
    ):
        raise RuntimeError(
            "clinical safety observe action cannot return urgent or blocked signals"
        )
    if action == ClinicalSafetyPolicyAction.ESCALATE and not any(
        signal.severity == "urgent" for signal in signals
    ):
        raise RuntimeError("clinical safety escalate action requires an urgent signal")
    if action == ClinicalSafetyPolicyAction.BLOCK and not any(
        signal.severity == "blocked" for signal in signals
    ):
        raise RuntimeError("clinical safety block action requires a blocked signal")
    return ClinicalSafetyPolicyDecision(
        action=action,
        allow=raw_allow,
        message=raw_message.strip(),
        reasons=tuple(item.strip() for item in raw_reasons if item.strip()),
        signals=signals,
        metadata={
            "policy_backend": policy_backend,
            "policy_path": policy_path,
        },
    )


def _signal_from_payload(payload: Any) -> SafetySignal:
    """将单个策略安全信号转换为主响应安全信号模型。

    :param payload: 策略返回的安全信号对象。
    :return: 返回经过 Pydantic 严格校验的安全信号。
    :raises RuntimeError: 信号不是对象或字段结构不合法时抛出。
    """
    if not isinstance(payload, dict):
        raise RuntimeError("clinical safety policy signal must be an object")
    try:
        return SafetySignal.model_validate(payload)
    except Exception as exc:
        raise RuntimeError("clinical safety policy returned an invalid signal") from exc
