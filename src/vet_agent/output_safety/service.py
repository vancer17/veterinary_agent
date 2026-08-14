"""
文件：src/vet_agent/output_safety/service.py
作用：编排输出安全候选采集、策略裁决和最终响应复核。
范围：位于回复生成之后、记忆抽取与回合持久化之前，替代旧输出清洗与安全复核实现。
说明：本服务不执行字符串级局部修补；observe 模式仅记录信号，enforce 模式只执行策略层给出的整段动作。
"""

from __future__ import annotations

from vet_agent import AgentTurnResponse, SafetySignal, Settings, VetSegment
from vet_agent.observability import AgentPathNode
from vet_agent.output_safety.detectors import OutputSafetyDetector
from vet_agent.output_safety.models import (
    OutputSafetyCandidate,
    OutputSafetyDecision,
    OutputSafetyDecisionAction,
    OutputSafetyMode,
    OutputSafetyReviewContext,
)
from vet_agent.output_safety.policy import OutputSafetyPolicyClient
from vet_agent.output_safety.repository import OutputSafetyRepository


class OutputSafetyService:
    """编排输出安全候选采集与策略裁决。

    :return: 无返回值。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        repository: OutputSafetyRepository,
        detectors: tuple[OutputSafetyDetector, ...],
        policy_client: OutputSafetyPolicyClient,
    ) -> None:
        """初始化输出安全服务。

        :param settings: 应用配置对象。
        :param repository: 输出安全候选定义仓储。
        :param detectors: 输出安全检测器集合。
        :param policy_client: 输出安全策略裁决客户端。
        :return: 无返回值。
        """
        self.settings = settings
        self.repository = repository
        self.detectors = detectors
        self.policy_client = policy_client

    async def review_response(self, response: AgentTurnResponse) -> AgentTurnResponse:
        """对最终响应执行输出安全复核。

        :param response: 待复核的 Agent 响应。
        :return: 返回复核后的 Agent 响应；observe 模式保持原文不变。
        """
        self._append_path(response, AgentPathNode.OUTPUT_SAFETY_SERVICE)
        mode = _mode_from_settings(self.settings)
        if mode == OutputSafetyMode.DISABLED or not self.settings.enable_output_safety:
            decision = OutputSafetyDecision.allow_response(
                metadata={
                    "enabled": False,
                    "mode": OutputSafetyMode.DISABLED.value,
                    "candidate_count": 0,
                }
            )
            response.metadata["output_safety_decision"] = decision.to_metadata()
            return response

        context = OutputSafetyReviewContext.from_response(response)
        decision = await self.evaluate(context)
        self._append_policy_path(response, decision)
        response.safety_signals = self._dedupe_signals([*response.safety_signals, *decision.signals])
        response.metadata["output_safety_decision"] = {
            **decision.to_metadata(),
            "enabled": True,
            "mode": mode.value,
            "enforced": mode == OutputSafetyMode.ENFORCE,
        }
        if mode == OutputSafetyMode.OBSERVE:
            return response
        return self._apply_enforced_decision(response, decision)

    async def evaluate(self, context: OutputSafetyReviewContext) -> OutputSafetyDecision:
        """采集候选并执行输出安全策略裁决。

        :param context: 本轮输出安全复核上下文。
        :return: 返回输出安全策略裁决。
        """
        candidates: list[OutputSafetyCandidate] = []
        for detector in self.detectors:
            candidates.extend(detector.collect(context))
        deduped = self._dedupe_candidates(candidates)
        if not deduped and not self.settings.output_safety_policy_always_call:
            return OutputSafetyDecision.allow_response(
                metadata={
                    "candidate_count": 0,
                    "policy_backend": "skipped",
                }
            )
        decision = await self.policy_client.decide(context, tuple(deduped))
        return OutputSafetyDecision(
            action=decision.action,
            allow=decision.allow,
            message=decision.message,
            reasons=decision.reasons,
            candidates=decision.candidates,
            signals=decision.signals,
            replacement_text=decision.replacement_text,
            metadata={**decision.metadata, "candidate_count": len(deduped)},
        )

    def is_ready(self) -> bool:
        """检查输出安全服务依赖是否就绪。

        :return: 禁用模式返回 True；启用模式下仓储、检测器和策略客户端均就绪时返回 True。
        """
        mode = _mode_from_settings(self.settings)
        if mode == OutputSafetyMode.DISABLED or not self.settings.enable_output_safety:
            return True
        return (
            self.repository.is_ready()
            and self.policy_client.is_ready()
            and all(detector.is_ready() for detector in self.detectors)
        )

    def _apply_enforced_decision(
        self,
        response: AgentTurnResponse,
        decision: OutputSafetyDecision,
    ) -> AgentTurnResponse:
        """在 enforce 模式下应用策略动作。

        :param response: 待处理的 Agent 响应。
        :param decision: 输出安全策略裁决。
        :return: 返回应用动作后的 Agent 响应。
        :raises RuntimeError: rewrite 未就绪或策略缺少整段替换文本时抛出。
        """
        if decision.action in {OutputSafetyDecisionAction.ALLOW, OutputSafetyDecisionAction.OBSERVE}:
            return response
        if decision.rewrite_requested:
            raise RuntimeError("output safety rewrite action is not implemented")
        if decision.action not in {OutputSafetyDecisionAction.BLOCK, OutputSafetyDecisionAction.ESCALATE}:
            return response
        replacement_text = (decision.replacement_text or "").strip()
        if not replacement_text:
            raise RuntimeError("output safety policy requested response replacement without replacement_text")
        status = "blocked" if decision.action == OutputSafetyDecisionAction.BLOCK else "safety_escalated"
        response.status = status
        response.output_text = replacement_text
        response.segments = [
            VetSegment(
                type="output_safety",
                title="输出安全复核",
                status=status,
                content=replacement_text,
                output_text=replacement_text,
                evidence=response.evidence,
            )
        ]
        response.reasoning_display = None
        return response

    def _append_policy_path(self, response: AgentTurnResponse, decision: OutputSafetyDecision) -> None:
        """根据策略后端追加输出安全策略审计节点。

        :param response: 当前 Agent 响应。
        :param decision: 输出安全策略裁决。
        :return: 无返回值。
        """
        backend = str(decision.metadata.get("policy_backend") or "")
        if backend == "opa":
            self._append_path(response, AgentPathNode.OUTPUT_SAFETY_POLICY_OPA)
        if backend == "local":
            self._append_path(response, AgentPathNode.OUTPUT_SAFETY_POLICY_LOCAL)

    def _append_path(self, response: AgentTurnResponse, node: AgentPathNode) -> None:
        """向响应 metadata.multi_agent_path 追加审计节点。

        :param response: 当前 Agent 响应。
        :param node: 需要追加的审计节点。
        :return: 无返回值。
        """
        path = response.metadata.setdefault("multi_agent_path", [])
        if isinstance(path, list) and node.value not in path:
            path.append(node.value)

    def _dedupe_candidates(self, candidates: list[OutputSafetyCandidate]) -> tuple[OutputSafetyCandidate, ...]:
        """按候选编码、片段和命中线索去重。

        :param candidates: 待去重候选列表。
        :return: 返回去重后的候选元组。
        """
        seen: set[tuple[str, str | None, tuple[str, ...]]] = set()
        result: list[OutputSafetyCandidate] = []
        for candidate in candidates:
            key = (candidate.code, candidate.segment_id, candidate.matched_terms)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return tuple(result)

    def _dedupe_signals(self, signals: list[SafetySignal]) -> list[SafetySignal]:
        """按安全信号编码、级别、说明与线索去重。

        :param signals: 待去重安全信号列表。
        :return: 返回去重后的安全信号列表。
        """
        seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
        result: list[SafetySignal] = []
        for signal in signals:
            key = (signal.code, signal.severity, signal.message, tuple(signal.matched_terms))
            if key in seen:
                continue
            seen.add(key)
            result.append(signal)
        return result


def _mode_from_settings(settings: Settings) -> OutputSafetyMode:
    """从应用配置解析输出安全运行模式。

    :param settings: 应用配置对象。
    :return: 返回输出安全运行模式。
    :raises RuntimeError: 配置值不属于受支持模式时抛出。
    """
    raw_mode = settings.output_safety_mode.strip().lower()
    try:
        return OutputSafetyMode(raw_mode)
    except ValueError as exc:
        raise RuntimeError(f"unsupported OUTPUT_SAFETY_MODE: {settings.output_safety_mode}") from exc
