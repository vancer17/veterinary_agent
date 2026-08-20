"""
文件：src/vet_agent/clinical_safety/fallback.py
作用：定义临床安全链路的显式回退、降级状态与裁决结果模型。
说明：本文件只承载运行态状态契约，不执行数据库访问、模型调用或临床裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import ClinicalSafetyCandidate, ClinicalSafetySignal


ClinicalSafetyRetrievalStage = Literal[
    "vector",
    "none",
]
ClinicalSafetySemanticStage = Literal[
    "llm",
    "llm_low_confidence",
    "disabled",
    "unavailable",
    "failed",
    "invalid_schema",
    "skipped",
]

ClinicalSafetyPreconditionStrategy = Literal[
    "not_required",
    "no_present_evidence",
    "qwen_response_format",
    "qwen_low_confidence",
    "qwen_invalid_response",
    "qwen_unavailable",
    "qwen_failed",
    "qwen_timeout",
    "qwen_total_timeout",
    "invalid_contract",
]


@dataclass(frozen=True)
class ClinicalSafetyRetrievalState:
    """表示临床安全候选召回层的运行档位和回退信息。

    :param stage: 本轮最终使用的召回档位。
    :param degraded: 当前召回是否处于降级状态。
    :param reasons: 触发回退或无命中的原因列表。
    :param retrieval_source: 最终命中来源标识。
    :param vector_hit_count: 向量召回命中 chunk 数量。
    :param candidate_count: 资产候选数量。
    :return: 无返回值。
    """

    stage: ClinicalSafetyRetrievalStage = "none"
    degraded: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    retrieval_source: str = ""
    vector_hit_count: int = 0
    candidate_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入响应 metadata 的字典。

        :return: 返回召回回退状态字典。
        """
        return {
            "stage": self.stage,
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "retrieval_source": self.retrieval_source,
            "vector_hit_count": self.vector_hit_count,
            "candidate_count": self.candidate_count,
        }


@dataclass(frozen=True)
class ClinicalSafetySemanticFallbackState:
    """表示临床安全结构化语义层的运行档位和回退信息。

    :param stage: 本轮最终使用的语义抽取档位。
    :param degraded: 当前语义抽取是否处于降级状态。
    :param reasons: 触发回退或降级的原因列表。
    :param confidence: 语义抽取置信度。
    :param strategy: 原始语义抽取策略标识。
    :return: 无返回值。
    """

    stage: ClinicalSafetySemanticStage = "skipped"
    degraded: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    strategy: str = "not_requested"

    @classmethod
    def from_parts(
        cls,
        *,
        strategy: str,
        confidence: float,
        fallback_reason: str | None = None,
    ) -> ClinicalSafetySemanticFallbackState:
        """根据语义抽取结果字段构造显式回退状态。

        :param strategy: 语义抽取策略标识。
        :param confidence: 语义抽取置信度。
        :param fallback_reason: 语义抽取回退原因。
        :return: 返回结构化语义回退状态。
        """
        normalized_strategy = strategy.strip() or "not_requested"
        if normalized_strategy == "litellm_response_format_low_confidence":
            return cls(
                stage="llm_low_confidence",
                degraded=True,
                reasons=cls._reasons(
                    fallback_reason,
                    default_reason=f"semantic_llm_low_confidence:{confidence:.2f}",
                ),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        if normalized_strategy == "litellm_response_format":
            return cls(
                stage="llm",
                degraded=False,
                reasons=cls._reasons(fallback_reason),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        if normalized_strategy == "semantic_extraction_disabled":
            return cls(
                stage="disabled",
                degraded=True,
                reasons=cls._reasons(
                    fallback_reason,
                    default_reason="llm_clinical_safety_semantic_extraction_disabled",
                ),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        if normalized_strategy == "semantic_extraction_unavailable":
            return cls(
                stage="unavailable",
                degraded=True,
                reasons=cls._reasons(
                    fallback_reason,
                    default_reason="llm_clinical_safety_semantic_extraction_unavailable",
                ),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        if normalized_strategy == "semantic_extraction_invalid_schema":
            return cls(
                stage="invalid_schema",
                degraded=True,
                reasons=cls._reasons(
                    fallback_reason,
                    default_reason="llm_clinical_safety_semantic_invalid_schema",
                ),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        if normalized_strategy == "semantic_extraction_failed":
            return cls(
                stage="failed",
                degraded=True,
                reasons=cls._reasons(
                    fallback_reason,
                    default_reason="llm_clinical_safety_semantic_extraction_failed",
                ),
                confidence=confidence,
                strategy=normalized_strategy,
            )
        return cls(
            stage="skipped",
            degraded=False,
            reasons=cls._reasons(fallback_reason),
            confidence=confidence,
            strategy=normalized_strategy,
        )

    @staticmethod
    def _reasons(reason: str | None, *, default_reason: str = "") -> tuple[str, ...]:
        """规范化回退原因。

        :param reason: 原始回退原因。
        :param default_reason: 原始原因为空时使用的默认原因。
        :return: 返回去空后的原因元组。
        """
        values = [reason or default_reason]
        return tuple(value for value in values if value)

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入响应 metadata 的字典。

        :return: 返回结构化语义回退状态字典。
        """
        return {
            "stage": self.stage,
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "confidence": self.confidence,
            "strategy": self.strategy,
        }


@dataclass(frozen=True)
class ClinicalSafetyPreconditionState:
    """表示自然语言候选前提评估层的运行状态和计数摘要。

    :param strategy: 本轮前提评估最终策略或失败状态。
    :param degraded: 前提评估链路是否发生依赖降级或协议错误。
    :param reasons: 前提评估降级、缺失或协议错误原因。
    :param candidate_count: 本轮参与评估编排的候选总数。
    :param required_count: 声明自然语言症状前提的候选数。
    :param satisfied_count: 被评估为满足前提的候选数。
    :param not_satisfied_count: 被评估为明确不满足前提的候选数。
    :param unknown_count: 因证据、置信或协议边界只能保持未知的候选数。
    :param requires_information: 是否存在可通过继续问诊补充的前提信息缺口。
    :param requested_model: 本次前提评估请求的默认模型。
    :param model_candidates: 本次前提评估允许使用的模型候选链。
    :param prompt_version: 前提评估提示词版本。
    :param response_schema_version: 前提评估响应结构版本。
    :param latency_ms: 本轮前提评估耗时。
    :param batch_count: 实际需要的模型请求批次数。
    :param deduplicated_group_count: 按 semantic_premise_hash 去重后的分组数。
    :return: 无返回值；该对象只承载审计状态，不承载最终动作。
    """

    strategy: ClinicalSafetyPreconditionStrategy = "not_required"
    degraded: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    candidate_count: int = 0
    required_count: int = 0
    satisfied_count: int = 0
    not_satisfied_count: int = 0
    unknown_count: int = 0
    requires_information: bool = False
    requested_model: str = ""
    model_candidates: tuple[str, ...] = ()
    prompt_version: str = ""
    response_schema_version: str = ""
    latency_ms: int = 0
    batch_count: int = 0
    deduplicated_group_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为临床安全响应 metadata 中的前提评估审计字典。

        :return: 返回可序列化的前提评估状态摘要。
        """
        return {
            "strategy": self.strategy,
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "candidate_count": self.candidate_count,
            "required_count": self.required_count,
            "satisfied_count": self.satisfied_count,
            "not_satisfied_count": self.not_satisfied_count,
            "unknown_count": self.unknown_count,
            "requires_information": self.requires_information,
            "requested_model": self.requested_model,
            "model_candidates": list(self.model_candidates),
            "prompt_version": self.prompt_version,
            "response_schema_version": self.response_schema_version,
            "latency_ms": self.latency_ms,
            "batch_count": self.batch_count,
            "deduplicated_group_count": self.deduplicated_group_count,
        }


@dataclass(frozen=True)
class ClinicalSafetyFallbackState:
    """聚合临床安全链路的召回、语义与前提评估显式回退状态。

    :param retrieval: 候选召回层回退状态。
    :param semantic: 结构化语义层回退状态。
    :param precondition: 自然语言候选前提评估层回退状态。
    :return: 无返回值。
    """

    retrieval: ClinicalSafetyRetrievalState = field(
        default_factory=ClinicalSafetyRetrievalState
    )
    semantic: ClinicalSafetySemanticFallbackState = field(
        default_factory=ClinicalSafetySemanticFallbackState
    )
    precondition: ClinicalSafetyPreconditionState = field(
        default_factory=ClinicalSafetyPreconditionState
    )

    @property
    def degraded(self) -> bool:
        """判断临床安全链路是否发生任一层降级。

        :return: 召回、语义或前提评估任一层降级时返回 True。
        """
        return (
            self.retrieval.degraded
            or self.semantic.degraded
            or self.precondition.degraded
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入响应 metadata 的字典。

        :return: 返回完整临床安全回退状态字典。
        """
        return {
            "degraded": self.degraded,
            "retrieval": self.retrieval.to_dict(),
            "semantic": self.semantic.to_dict(),
            "precondition": self.precondition.to_dict(),
        }


@dataclass(frozen=True)
class ClinicalSafetyRetrievalResult:
    """表示召回器返回的候选列表与显式召回状态。

    :param candidates: 按资产聚合后的临床安全候选。
    :param state: 本轮召回的显式回退状态。
    :return: 无返回值。
    """

    candidates: list[ClinicalSafetyCandidate]
    state: ClinicalSafetyRetrievalState

    def to_dict(self) -> dict[str, Any]:
        """转换为可审计的召回结果摘要。

        :return: 返回召回结果摘要字典。
        """
        return {
            "candidate_count": len(self.candidates),
            "state": self.state.to_dict(),
        }


@dataclass(frozen=True)
class ClinicalSafetyEvaluationResult:
    """表示临床安全裁决信号与完整回退状态。

    :param signals: 临床安全裁决生成的安全信号。
    :param primary_signal: OPA 裁定并透传给响应投影的唯一主安全信号。
    :param fallback_state: 临床安全链路的显式回退状态。
    :param policy_decision: 临床安全策略裁决摘要。
    :return: 无返回值。
    """

    signals: list[ClinicalSafetySignal]
    fallback_state: ClinicalSafetyFallbackState
    primary_signal: ClinicalSafetySignal | None = None
    policy_decision: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """转换为 Agent 响应 metadata 中的临床安全审计信息。

        :return: 返回临床安全裁决与回退状态摘要。
        """
        return {
            "agent": "ClinicalSafetyEvaluator",
            "signal_count": len(self.signals),
            "primary_signal": (
                self.primary_signal.model_dump(mode="json")
                if self.primary_signal is not None
                else None
            ),
            "requires_precondition_information": self.requires_precondition_information,
            "fallback_state": self.fallback_state.to_dict(),
            "policy_decision": dict(self.policy_decision),
        }

    @property
    def requires_precondition_information(self) -> bool:
        """判断临床安全前提层是否存在可继续问诊补充的信息缺口。

        :return: 前提评估状态要求补充信息时返回 True。
        """
        return self.fallback_state.precondition.requires_information
