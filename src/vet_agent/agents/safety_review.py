from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.vet_agent.agents.safety import SafetyAgent
from src.vet_agent.contracts import AgentTurnResponse, SafetySignal


@dataclass(frozen=True)
class SafetyReviewResult:
    text: str
    signals: list[SafetySignal] = field(default_factory=list)
    changed: bool = False


class SafetyReviewAgent:
    """Final user-visible output review before persistence and delivery."""

    def __init__(self, safety: SafetyAgent) -> None:
        self.safety = safety

    def review_response(self, response: AgentTurnResponse) -> AgentTurnResponse:
        review_signals: list[SafetySignal] = []
        changed_segments = 0
        for segment in response.segments:
            original = segment.output_text or segment.content
            reviewed = self.review_text(original)
            if reviewed.changed:
                changed_segments += 1
                segment.output_text = reviewed.text
                segment.content = reviewed.text
            review_signals.extend(reviewed.signals)

        if response.segments:
            if len(response.segments) == 1:
                response.output_text = response.segments[0].output_text or response.segments[0].content
            else:
                response.output_text = "\n\n".join(
                    f"{segment.title}\n{segment.output_text or segment.content}" for segment in response.segments
                )
        else:
            reviewed = self.review_text(response.output_text)
            response.output_text = reviewed.text
            review_signals.extend(reviewed.signals)
            changed_segments += 1 if reviewed.changed else 0

        response.safety_signals = self._dedupe([*response.safety_signals, *review_signals])
        response.metadata.setdefault("safety_review", {})
        response.metadata["safety_review"].update(
            {
                "agent": "SafetyReviewAgent",
                "changed_segments": changed_segments,
                "signal_count": len(review_signals),
            }
        )
        path = response.metadata.get("multi_agent_path")
        if isinstance(path, list) and "SafetyReviewAgent" not in path:
            path.append("SafetyReviewAgent")
        return response

    def review_text(self, text: str) -> SafetyReviewResult:
        sanitized, signals = self.safety.sanitize_output(text)
        extra: list[SafetySignal] = []
        reviewed = self._soften_definitive_diagnosis(sanitized)
        if reviewed != sanitized:
            extra.append(
                SafetySignal(
                    code="DEFINITIVE_DIAGNOSIS_SOFTENED",
                    severity="caution",
                    message="输出安全审查弱化了绝对化诊断表述。",
                )
            )
        scrubbed = self._scrub_internal_trace_terms(reviewed)
        if scrubbed != reviewed:
            extra.append(
                SafetySignal(
                    code="INTERNAL_TRACE_REMOVED",
                    severity="caution",
                    message="输出安全审查移除了内部提示或链路表述。",
                )
            )
        return SafetyReviewResult(
            text=scrubbed,
            signals=[*signals, *extra],
            changed=scrubbed != text or bool(signals or extra),
        )

    def _soften_definitive_diagnosis(self, text: str) -> str:
        replacements = {
            "确诊为": "更倾向于",
            "可以确诊": "需要线下检查确认",
            "一定是": "更像是",
            "肯定是": "更像是",
        }
        result = text
        for source, target in replacements.items():
            result = result.replace(source, target)
        return result

    def _scrub_internal_trace_terms(self, text: str) -> str:
        patterns = [
            r"(?i)system prompt[:：]?.*",
            r"(?i)chain[- ]?of[- ]?thought[:：]?.*",
            r"隐藏思维链[:：]?.*",
            r"内部提示词[:：]?.*",
        ]
        result = text
        for pattern in patterns:
            result = re.sub(pattern, "【内部处理信息已省略】", result)
        return result

    def _dedupe(self, signals: list[SafetySignal]) -> list[SafetySignal]:
        seen: set[tuple[str, str, str]] = set()
        result: list[SafetySignal] = []
        for signal in signals:
            key = (signal.code, signal.severity, signal.message)
            if key in seen:
                continue
            seen.add(key)
            result.append(signal)
        return result
