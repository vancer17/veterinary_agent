"""
文件：src/vet_agent/agents/safety.py
作用：提供 Agent 输出安全清洗与安全信号聚合模型。
范围：仅负责生成后文本的确定性清洗，不再执行输入关键词阻断、附件判读阻断或旧 response_template 回查。
说明：基础输入安全已迁移至 vet_agent.input_safety；临床安全分诊由 clinical_safety 包负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vet_agent import SafetySignal
from vet_agent.repositories import RuleRepository


_DOSAGE_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg\s*/\s*kg|毫克/公斤|毫克每公斤|ml/kg|mL/kg|毫升/公斤)\b",
    re.IGNORECASE,
)
_MEDICAL_MARKERS = (
    "分诊",
    "诊断",
    "症状",
    "就诊",
    "兽医",
    "治疗",
    "检查",
    "呕吐",
    "腹泻",
    "呼吸",
    "疼痛",
    "用药",
)
_MEDICATION_MARKERS = (
    "药",
    "用药",
    "处方",
    "抗生素",
    "止痛",
    "消炎",
    "驱虫",
    "剂量",
    "mg",
    "ml",
)


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


class SafetyAgent:
    """执行 Agent 输出安全清洗。

    :return: 无返回值。
    """

    def __init__(self, rule_repository: RuleRepository | None = None) -> None:
        """初始化输出安全清洗器。

        :param rule_repository: 兼容旧构造签名的规则仓储；当前输出清洗不再读取该仓储。
        :return: 无返回值。
        """
        self.rule_repository = rule_repository

    def sanitize_output(self, text: str) -> tuple[str, list[SafetySignal]]:
        """清洗面向用户的输出文本。

        :param text: 待处理文本。
        :return: 返回清洗后的文本和新增安全信号。
        """
        sanitized, removed_count = _DOSAGE_PATTERN.subn(
            "【剂量已省略:请按药品使用说明书或遵从兽医指导】",
            text,
        )
        signals: list[SafetySignal] = []
        if removed_count > 0:
            signals.append(
                SafetySignal(
                    code="DOSAGE_REMOVED",
                    severity="caution",
                    message="输出安全审查移除了具体剂量表达。",
                )
            )
        if self._looks_medical(sanitized) and "线下兽医" not in sanitized:
            sanitized = f"{sanitized}\n\n这是辅助参考，请以线下兽医诊断为准。"
        if self._mentions_medication(sanitized) and "按药品使用说明书" not in sanitized:
            sanitized = f"{sanitized}\n涉及用药时，请按药品使用说明书或遵从兽医指导，具体药物与剂量由兽医确认。"
        return sanitized, signals

    def forced_response(self, assessment: SafetyAssessment) -> str:
        """根据安全信号生成临床安全分诊响应。

        :param assessment: 已完成的安全评估结果。
        :return: 返回面向用户的安全分诊文本。
        """
        urgent_signals = [signal for signal in assessment.signals if signal.severity in {"urgent", "blocked"}]
        if urgent_signals:
            reasons = "；".join(signal.message for signal in urgent_signals if signal.message)
            matched = "、".join(term for signal in urgent_signals for term in signal.matched_terms)
            prefix = "你描述里有需要优先线下处理的高风险信号"
            if reasons:
                prefix = f"{prefix}：{reasons}"
            if matched:
                prefix = f"{prefix}，相关线索: {matched}"
            return (
                f"{prefix}。请尽快联系线下兽医医院，若症状正在发生或持续加重，请按急诊处理。\n\n"
                "路上尽量保持宠物安静和保暖，不要自行喂人药或给不确定的药物。"
            )
        return "当前信息需要进一步确认。"

    def _looks_medical(self, text: str) -> bool:
        """判断输出文本是否包含医疗咨询语境。

        :param text: 待处理文本。
        :return: 需要补充线下兽医兜底说明时返回 True。
        """
        return self._contains_any(text, _MEDICAL_MARKERS)

    def _mentions_medication(self, text: str) -> bool:
        """判断输出文本是否涉及用药语境。

        :param text: 待处理文本。
        :return: 需要补充用药安全说明时返回 True。
        """
        return self._contains_any(text, _MEDICATION_MARKERS)

    def _contains_any(self, text: str, markers: tuple[str, ...]) -> bool:
        """判断文本是否包含任一输出审查标记。

        :param text: 待处理文本。
        :param markers: 输出审查标记集合。
        :return: 命中任一标记时返回 True。
        """
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in markers)
