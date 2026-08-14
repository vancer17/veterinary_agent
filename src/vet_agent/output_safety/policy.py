"""
文件：src/vet_agent/output_safety/policy.py
作用：封装输出安全 OPA 策略裁决客户端与本地观测裁决器。
范围：负责将输出安全候选提交给策略层，并将策略结果转换为 Agent 安全信号。
说明：生产强制模式应使用 OPA；本地裁决器仅执行 observe，不提供 block、rewrite 或关键词回退。
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx

from vet_agent import SafetySignal
from vet_agent.output_safety.models import (
    OutputSafetyCandidate,
    OutputSafetyDecision,
    OutputSafetyDecisionAction,
    OutputSafetyReviewContext,
)


class OutputSafetyPolicyClient(Protocol):
    """定义输出安全策略裁决客户端协议。

    :return: 无返回值。
    """

    async def decide(
        self,
        context: OutputSafetyReviewContext,
        candidates: tuple[OutputSafetyCandidate, ...],
    ) -> OutputSafetyDecision:
        """对本轮输出安全候选执行策略裁决。

        :param context: 本轮输出安全复核上下文。
        :param candidates: 本轮输出安全候选。
        :return: 返回策略裁决结果。
        """
        ...

    def is_ready(self) -> bool:
        """检查策略客户端是否就绪。

        :return: 策略客户端可用时返回 True。
        """
        ...


class OpaOutputSafetyPolicyClient(OutputSafetyPolicyClient):
    """使用 OPA Data API 执行输出安全策略裁决。

    :return: 无返回值。
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
        """初始化 OPA 输出安全策略客户端。

        :param base_url: OPA Data API 基准地址，可包含 Nginx 前缀。
        :param version: OPA REST API 版本。
        :param package_path: OPA package 数据路径。
        :param rule_name: OPA 裁决规则名称。
        :param auth_token: OPA 鉴权令牌。
        :param timeout_seconds: OPA 调用超时时间。
        :return: 无返回值。
        """
        normalized = base_url.strip().rstrip("/")
        version_value = version.strip().strip("/")
        if normalized.endswith(f"/{version_value}"):
            self.base_url = normalized
        else:
            self.base_url = f"{normalized}/{version_value}"
        self.version = version.strip("/")
        self.package_path = package_path.strip("/")
        self.rule_name = rule_name.strip("/")
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

    async def decide(
        self,
        context: OutputSafetyReviewContext,
        candidates: tuple[OutputSafetyCandidate, ...],
    ) -> OutputSafetyDecision:
        """对本轮输出安全候选执行 OPA 策略裁决。

        :param context: 本轮输出安全复核上下文。
        :param candidates: 本轮输出安全候选。
        :return: 返回输出安全策略裁决。
        :raises RuntimeError: OPA 调用失败或响应结构不合法时抛出。
        """
        payload = context.to_policy_input(candidates)
        url = self._decision_url()
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, headers=headers, json={"input": payload})
                response.raise_for_status()
                result = response.json()
        except Exception as exc:
            raise RuntimeError("output safety OPA policy decision failed") from exc
        decision_payload = self._unwrap_result(result)
        return _decision_from_payload(decision_payload, candidates)

    def is_ready(self) -> bool:
        """检查 OPA 策略客户端配置是否完整。

        :return: OPA 连接参数齐全时返回 True。
        """
        return bool(self.base_url and self.version and self.package_path and self.rule_name)

    def _decision_url(self) -> str:
        """构造 OPA Data API 裁决 URL。

        :return: 返回可提交裁决请求的完整 URL。
        """
        package_parts = [quote(part, safe="") for part in self.package_path.replace("/", ".").split(".") if part]
        rule = quote(self.rule_name, safe="")
        path = "/".join([*package_parts, rule])
        return f"{self.base_url}/data/{path}"

    def _unwrap_result(self, result: Any) -> dict[str, Any]:
        """归一 OPA 客户端返回结构。

        :param result: OPA 客户端原始返回。
        :return: 返回裁决字典。
        :raises RuntimeError: 响应结构不合法时抛出。
        """
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            return dict(result["result"])
        if isinstance(result, dict):
            return dict(result)
        raise RuntimeError("output safety OPA policy returned invalid decision payload")


class LocalOutputSafetyPolicyClient(OutputSafetyPolicyClient):
    """本地输出安全观测裁决器。

    说明：该实现只把已结构化候选转换为 observe 裁决，不读取输出原文，不执行关键词规则，也不生成改写文本。

    :return: 无返回值。
    """

    async def decide(
        self,
        context: OutputSafetyReviewContext,
        candidates: tuple[OutputSafetyCandidate, ...],
    ) -> OutputSafetyDecision:
        """对本轮输出安全候选执行本地观测裁决。

        :param context: 本轮输出安全复核上下文。
        :param candidates: 本轮输出安全候选。
        :return: 返回输出安全策略裁决。
        """
        del context
        if not candidates:
            return OutputSafetyDecision.allow_response(metadata={"policy_backend": "local"})
        signals = tuple(candidate.to_signal() for candidate in candidates)
        return OutputSafetyDecision(
            action=OutputSafetyDecisionAction.OBSERVE,
            allow=True,
            message="输出安全策略记录候选并允许继续交付。",
            reasons=tuple(candidate.message for candidate in candidates),
            candidates=candidates,
            signals=signals,
            metadata={"policy_backend": "local"},
        )

    def is_ready(self) -> bool:
        """检查本地策略裁决器是否可用。

        :return: 始终返回 True。
        """
        return True


class DisabledOutputSafetyPolicyClient(OutputSafetyPolicyClient):
    """显式禁用输出安全策略裁决的测试客户端。

    说明：该实现仅供测试、禁用模式或临时嵌入场景通过依赖注入使用，生产启用模式不会默认选择该实现。

    :return: 无返回值。
    """

    async def decide(
        self,
        context: OutputSafetyReviewContext,
        candidates: tuple[OutputSafetyCandidate, ...],
    ) -> OutputSafetyDecision:
        """返回允许裁决并保留禁用审计信息。

        :param context: 本轮输出安全复核上下文。
        :param candidates: 本轮输出安全候选。
        :return: 返回允许继续交付的裁决。
        """
        del context
        return OutputSafetyDecision.allow_response(
            metadata={
                "policy_backend": "disabled",
                "candidate_count": len(candidates),
                "decision_id": f"output_safety_disabled_{uuid4().hex}",
            }
        )

    def is_ready(self) -> bool:
        """检查禁用策略客户端是否可用。

        :return: 始终返回 True。
        """
        return True


def _decision_from_payload(
    payload: dict[str, Any],
    candidates: tuple[OutputSafetyCandidate, ...],
) -> OutputSafetyDecision:
    """将 OPA 裁决响应转换为输出安全裁决对象。

    :param payload: OPA 返回的裁决字典。
    :param candidates: 本轮输出安全候选。
    :return: 返回输出安全裁决。
    :raises RuntimeError: 策略动作非法时抛出。
    """
    raw_action = str(payload.get("action") or ("allow" if payload.get("allow", True) else "block"))
    try:
        action = OutputSafetyDecisionAction(raw_action)
    except ValueError as exc:
        raise RuntimeError(f"invalid output safety policy action: {raw_action}") from exc
    allow = bool(payload.get("allow", action != OutputSafetyDecisionAction.BLOCK))
    message = str(payload.get("message") or _message_from_candidates(candidates))
    reasons = tuple(str(item) for item in payload.get("reasons", []) if str(item).strip())
    replacement = payload.get("replacement_text") or payload.get("response_text")
    replacement_text = str(replacement) if replacement is not None else None
    policy_signals = _signals_from_payload(payload, candidates, action)
    return OutputSafetyDecision(
        action=action,
        allow=allow,
        message=message,
        reasons=reasons,
        candidates=candidates,
        signals=policy_signals,
        replacement_text=replacement_text,
        metadata={"policy_backend": "opa", "policy_payload": payload},
    )


def _signals_from_payload(
    payload: dict[str, Any],
    candidates: tuple[OutputSafetyCandidate, ...],
    action: OutputSafetyDecisionAction,
) -> tuple[SafetySignal, ...]:
    """从 OPA 响应或候选构造安全信号。

    :param payload: OPA 返回的裁决字典。
    :param candidates: 本轮输出安全候选。
    :param action: 策略动作。
    :return: 返回安全信号元组。
    """
    raw_signals = payload.get("signals")
    if isinstance(raw_signals, list):
        signals: list[SafetySignal] = []
        for item in raw_signals:
            if not isinstance(item, dict):
                continue
            signals.append(
                SafetySignal(
                    code=str(item.get("code") or "OUTPUT_SAFETY_POLICY"),
                    severity=str(item.get("severity") or _severity_from_action(action)),
                    message=str(item.get("message") or payload.get("message") or ""),
                    matched_terms=[str(value) for value in item.get("matched_terms", [])],
                )
            )
        return tuple(signals)
    return tuple(
        candidate.to_signal(
            severity=_severity_from_action(action),
            message=payload.get("message") or candidate.message,
        )
        for candidate in candidates
    )


def _severity_from_action(action: OutputSafetyDecisionAction) -> str:
    """根据策略动作转换安全信号级别。

    :param action: 输出安全策略动作。
    :return: 返回安全信号级别。
    """
    if action == OutputSafetyDecisionAction.BLOCK:
        return "blocked"
    if action == OutputSafetyDecisionAction.ESCALATE:
        return "urgent"
    return "caution"


def _message_from_candidates(candidates: tuple[OutputSafetyCandidate, ...]) -> str:
    """根据候选集合生成默认裁决说明。

    :param candidates: 输出安全候选集合。
    :return: 返回默认裁决说明。
    """
    messages = [candidate.message for candidate in candidates if candidate.message]
    if messages:
        return "；".join(messages)
    return "输出安全策略识别到需要审计的候选。"
