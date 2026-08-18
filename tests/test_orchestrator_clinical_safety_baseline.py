"""
文件：tests/test_orchestrator_clinical_safety_baseline.py
作用：验证主编排器在临床安全迁移中的用户可见响应投影基线。
范围：覆盖多个 urgent 或 blocked 安全信号进入响应层时，只展示一个主分诊信号的边界。
说明：本文件不构造数据库、模型或召回依赖；安全候选完整审计仍由响应对象中的
      safety_signals 和 metadata 保留，用户文本只承载主信号。
"""

from __future__ import annotations

from vet_agent import SafetySignal, VetOrchestrator
from vet_agent.agents import SafetyAssessment


def test_safety_triage_response_text_projects_only_primary_signal() -> None:
    """验证多个 urgent 候选不会在用户响应中被全部拼接展示。

    :return: 无返回值；断言通过表示临床安全响应层只投影主分诊信号。
    """
    orchestrator = VetOrchestrator.__new__(VetOrchestrator)
    assessment = SafetyAssessment.from_signals(
        [
            SafetySignal(
                code="CYANOSIS_RISK_PATTERN",
                severity="urgent",
                message="呼吸循环异常需要优先线下处理。",
                matched_terms=["牙龈发紫"],
            ),
            SafetySignal(
                code="GDV_RISK_PATTERN",
                severity="urgent",
                message="胃扩张扭转风险需要优先线下处理。",
                matched_terms=["腹部膨大"],
            ),
        ]
    )

    text = orchestrator._safety_triage_response_text(assessment)

    assert "呼吸循环异常需要优先线下处理" in text
    assert "牙龈发紫" in text
    assert "胃扩张扭转风险需要优先线下处理" not in text
    assert "腹部膨大" not in text
