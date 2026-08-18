"""
文件：tests/test_clinical_safety_no_legacy_fallback.py
作用：验证临床安全迁移后不会重新引入旧版关键词规则和默认编码回退路径。
范围：仅扫描临床安全运行时代码、主编排入口和离线转换器中的明确禁用实现片段。
说明：本文件不扫描文档、开发样例、供应商代码或医学资产正文，避免把治理资料中的文字说明误判为运行时代码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from vet_agent.clinical_safety import (
    ClinicalSafetyEvaluator,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyPolicyRequestContext,
    ClinicalSafetyRetrievalState,
    ClinicalSafetySemanticResult,
    ClinicalSafetyThresholds,
)


RUNTIME_SOURCE_PATHS: tuple[Path, ...] = (
    Path("src/vet_agent/clinical_safety"),
    Path("src/vet_agent/orchestrator.py"),
)
DISALLOWED_RUNTIME_REFERENCES: tuple[str, ...] = (
    "safety_rules.json",
    "SafetyAgent.analyze",
)
DISALLOWED_GENERATED_CODE_PATTERN = re.compile(
    r"return\s+f[\"'](?:TOXIC_SUBSTANCE|EMERGENCY_RED_FLAG|DANGER_PATTERN)[^\"']*\{index:03d\}"
)


def test_clinical_safety_runtime_does_not_reference_legacy_rule_paths() -> None:
    """验证临床安全运行时代码不再引用旧版安全规则入口。

    :return: 无返回值；断言通过表示生产链路不会回退到旧 safety_rules 或 SafetyAgent 分析路径。
    """
    source_text = "\n".join(_runtime_source_texts())

    for reference in DISALLOWED_RUNTIME_REFERENCES:
        assert reference not in source_text


def test_clinical_safety_converter_does_not_generate_numbered_fallback_codes() -> None:
    """验证离线转换器不再生成序号型临床安全默认编码。

    :return: 无返回值；断言通过表示缺失 code 的资产只能在转换阶段快速失败。
    """
    source_text = Path("scripts/clinical_safety/converter.py").read_text(encoding="utf-8")

    assert DISALLOWED_GENERATED_CODE_PATTERN.search(source_text) is None


def test_clinical_safety_policy_payload_excludes_raw_user_text() -> None:
    """验证 OPA 输入负载不包含未结构化处理的用户原文。

    :return: 无返回值；断言通过表示 OPA 不承担自然语言关键词扫描职责。
    """
    semantic = ClinicalSafetySemanticResult(
        species="dog",
        exposure_state="confirmed",
        symptom_state="present",
        intent_type="toxicity",
        risk_evidence_state="sufficient",
        high_risk_terms=("误食药物",),
        confidence=0.95,
        strategy="litellm_response_format",
        source_text="用户原始输入不应进入 OPA 负载",
    )
    policy_input = ClinicalSafetyPolicyInput(
        context=ClinicalSafetyPolicyRequestContext(),
        semantic_result=semantic,
        retrieval_state=ClinicalSafetyRetrievalState(),
        candidates=(),
        thresholds=ClinicalSafetyThresholds(),
    )

    payload = policy_input.to_payload()
    payload_text = json.dumps(payload, ensure_ascii=False)

    assert "source_text" not in payload["semantic"]
    assert payload["semantic"]["risk_evidence_state"] == "sufficient"
    assert "用户原始输入不应进入 OPA 负载" not in payload_text


def test_clinical_safety_semantic_result_remains_fact_container_only() -> None:
    """验证结构化语义结果不承载查询策略判断方法。

    :return: 无返回值；断言通过表示语义对象不再增长临床裁决职责。
    """
    assert not hasattr(ClinicalSafetySemanticResult, "has_positive_risk_evidence")


def test_clinical_safety_evaluator_removes_field_combination_recall_gate() -> None:
    """验证 evaluator 不再保留旧版字段组合强召回门槛。

    :return: 无返回值；断言通过表示强召回只消费显式证据充分性边界。
    """
    assert not hasattr(ClinicalSafetyEvaluator, "_should_request_strong_recall")


def _runtime_source_texts() -> list[str]:
    """读取临床安全运行时代码的源码文本。

    :return: 返回用于架构防退化扫描的源码文本列表。
    """
    texts: list[str] = []
    for path in RUNTIME_SOURCE_PATHS:
        if path.is_dir():
            texts.extend(item.read_text(encoding="utf-8") for item in sorted(path.glob("*.py")))
            continue
        texts.append(path.read_text(encoding="utf-8"))
    return texts
