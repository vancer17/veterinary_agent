"""
文件：src/vet_agent/agents/safety.py
作用：提供 Agent 安全信号聚合模型。
范围：仅负责聚合已裁决的输入安全与临床安全信号，不执行输出文本清洗、关键词扫描或响应改写。
说明：基础输入安全已迁移至 vet_agent.input_safety；临床安全分诊由 clinical_safety 包负责；输出安全复核由 vet_agent.output_safety 负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vet_agent import SafetySignal


@dataclass(frozen=True)
class SafetyAssessment:
    """表示安全信号聚合后的分诊结果。

    :param escalated: 是否存在安全升级信号。
    :param blocked: 是否存在阻断信号。
    :param signals: 参与聚合的安全信号列表。
    :return: 无返回值。
    """

    escalated: bool = False
    blocked: bool = False
    signals: list[SafetySignal] = field(default_factory=list)

    @property
    def highest_status(self) -> str:
        """返回当前安全评估的最高状态。

        :return: 返回 `blocked`、`safety_escalated` 或 `ok`。
        """
        if self.blocked:
            return "blocked"
        if self.escalated:
            return "safety_escalated"
        return "ok"

    @classmethod
    def from_signals(cls, signals: list[SafetySignal]) -> "SafetyAssessment":
        """根据安全信号列表构造安全评估结果。

        :param signals: 安全信号列表。
        :return: 返回由信号推导出的安全评估结果。
        """
        return cls(
            escalated=any(signal.severity == "urgent" for signal in signals),
            blocked=any(signal.severity == "blocked" for signal in signals),
            signals=signals,
        )

    @classmethod
    def merge(cls, *assessments: "SafetyAssessment") -> "SafetyAssessment":
        """合并多个安全评估结果。

        :param assessments: 待合并的安全评估结果。
        :return: 返回合并后的安全评估结果。
        """
        merged_signals: list[SafetySignal] = []
        for assessment in assessments:
            merged_signals.extend(assessment.signals)
        return cls.from_signals(merged_signals)
