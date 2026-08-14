"""
文件：src/vet_agent/clinical_safety/fallback.py
作用：定义临床安全链路的显式回退、降级状态与裁决结果模型。
说明：本文件只承载运行态状态契约，不执行数据库访问、模型调用或临床裁决。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from vet_agent import SafetySignal

if TYPE_CHECKING:
    from .models import ClinicalSafetyCandidate


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
    ) -> "ClinicalSafetySemanticFallbackState":
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
class ClinicalSafetyFallbackState:
    """聚合临床安全链路的召回与语义显式回退状态。

    :param retrieval: 候选召回层回退状态。
    :param semantic: 结构化语义层回退状态。
    :return: 无返回值。
    """

    retrieval: ClinicalSafetyRetrievalState = field(default_factory=ClinicalSafetyRetrievalState)
    semantic: ClinicalSafetySemanticFallbackState = field(default_factory=ClinicalSafetySemanticFallbackState)

    @property
    def degraded(self) -> bool:
        """判断临床安全链路是否发生任一层降级。

        :return: 召回或语义任一层降级时返回 True。
        """
        return self.retrieval.degraded or self.semantic.degraded

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入响应 metadata 的字典。

        :return: 返回完整临床安全回退状态字典。
        """
        return {
            "degraded": self.degraded,
            "retrieval": self.retrieval.to_dict(),
            "semantic": self.semantic.to_dict(),
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
    :param fallback_state: 临床安全链路的显式回退状态。
    :param policy_decision: 临床安全策略裁决摘要。
    :return: 无返回值。
    """

    signals: list[SafetySignal]
    fallback_state: ClinicalSafetyFallbackState
    policy_decision: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """转换为 Agent 响应 metadata 中的临床安全审计信息。

        :return: 返回临床安全裁决与回退状态摘要。
        """
        return {
            "agent": "ClinicalSafetyEvaluator",
            "signal_count": len(self.signals),
            "fallback_state": self.fallback_state.to_dict(),
            "policy_decision": dict(self.policy_decision),
        }
