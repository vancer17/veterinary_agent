"""
文件：tests/test_orchestrator_clinical_safety_baseline.py
作用：验证主编排器在临床安全阶段 4 中的用户可见响应投影边界。
范围：覆盖多个 urgent 或 blocked 安全信号进入响应层时只展示策略主信号，并防止
      reasoning display 或 matched_terms 绕过主信号投影。
说明：本文件不构造数据库、模型或召回依赖；安全候选完整审计仍由响应对象中的
      safety_signals 和 metadata 保留，用户文本只承载主信号。
"""

from __future__ import annotations

import pytest

from vet_agent.clinical_safety import ClinicalSafetySignal
from vet_agent import VetOrchestrator
from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluationResult,
    ClinicalSafetyFallbackState,
)
from vet_agent.services import ReasoningDisplayBuilder


def test_safety_triage_response_text_projects_only_primary_signal() -> None:
    """验证多个 urgent 候选不会在用户响应中被全部拼接展示。

    :return: 无返回值；断言通过表示临床安全响应层只投影策略主信号。
    """
    orchestrator = VetOrchestrator.__new__(VetOrchestrator)
    primary_signal = ClinicalSafetySignal(
        asset_id="safety_cyanosis",
        canonical_name="舌/牙龈发绀发紫",
        code="EMERGENCY_MODE_7K4Q9PXRAB",
        severity="urgent",
        message="呼吸循环异常需要优先线下处理。",
        matched_terms=["牙龈发紫"],
    )
    secondary_signal = ClinicalSafetySignal(
        asset_id="safety_gdv",
        canonical_name="胃扩张扭转",
        code="EMERGENCY_MODE_8K4Q9PXRAB",
        severity="urgent",
        message="胃扩张扭转风险需要优先线下处理。",
        matched_terms=["腹部膨大"],
    )
    resolution = ClinicalSafetyEvaluationResult(
        signals=[primary_signal, secondary_signal],
        fallback_state=ClinicalSafetyFallbackState(),
        primary_signal=primary_signal,
    )

    text = orchestrator._safety_triage_response_text(
        orchestrator._clinical_safety_primary_signal(resolution)
    )

    assert "呼吸循环异常需要优先线下处理" in text
    assert "相关线索" not in text
    assert "牙龈发紫" not in text
    assert "胃扩张扭转风险需要优先线下处理" not in text
    assert "腹部膨大" not in text


def test_reasoning_display_does_not_project_candidate_codes_or_terms() -> None:
    """验证安全分诊过程摘要不绕过主信号边界展示候选编码或命中词。

    :return: 无返回值；断言通过表示 reasoning display 只保留数量级安全摘要。
    """
    orchestrator = VetOrchestrator.__new__(VetOrchestrator)
    primary_signal = ClinicalSafetySignal(
        asset_id="safety_cyanosis",
        canonical_name="舌/牙龈发绀发紫",
        code="EMERGENCY_MODE_7K4Q9PXRAB",
        severity="urgent",
        message="呼吸循环异常需要优先线下处理。",
        matched_terms=["牙龈发紫"],
    )

    display = ReasoningDisplayBuilder().build_turn_display(
        status="safety_escalated",
        segment_id="seg_test",
        evidence=[],
        safety_signals=[primary_signal],
    )

    assert "识别到 1 个需要关注的安全信号" in display.text
    assert "EMERGENCY_MODE_7K4Q9PXRAB" not in display.text
    assert "牙龈发紫" not in display.text


def test_missing_policy_primary_signal_fails_fast() -> None:
    """验证升级响应缺少策略主信号时不得由响应层本地兜底选择。

    :return: 无返回值；断言通过表示主信号契约缺失会快速失败。
    """
    orchestrator = VetOrchestrator.__new__(VetOrchestrator)
    signals = [
        ClinicalSafetySignal(
            asset_id="safety_cyanosis",
            canonical_name="舌/牙龈发绀发紫",
            code="EMERGENCY_MODE_7K4Q9PXRAB",
            severity="urgent",
            message="呼吸循环异常需要优先线下处理。",
        )
    ]
    resolution = ClinicalSafetyEvaluationResult(
        signals=signals,
        fallback_state=ClinicalSafetyFallbackState(),
        primary_signal=None,
    )

    with pytest.raises(RuntimeError, match="policy primary signal"):
        orchestrator._clinical_safety_primary_signal(resolution)
