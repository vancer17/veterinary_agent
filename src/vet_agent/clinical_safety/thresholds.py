"""
文件：src/vet_agent/clinical_safety/thresholds.py
作用：定义临床安全召回与裁决的统一阈值。
说明：本文件只承载纯阈值与简单判据，不依赖数据库、LLM 或外部服务。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ClinicalSafetyThresholds:
    """定义临床安全召回与裁决的统一阈值。

    :param retrieval_min_score: 向量召回阶段的最低相似度阈值。
    :param signal_min_score: 向量候选进入一般安全信号裁决的最低分数。
    :param urgent_min_score: 向量候选触发紧急升级的最低分数。
    :param lexical_min_terms: 词面证据被视为有效的最少命中词数。
    :return: 无返回值。
    """

    retrieval_min_score: float = 0.35
    signal_min_score: float = 0.65
    urgent_min_score: float = 0.75
    lexical_min_terms: int = 2

    def __post_init__(self) -> None:
        """校验阈值是否处于合理范围。

        :return: 无返回值。
        """
        if not 0.0 < self.retrieval_min_score <= 1.0:
            raise ValueError("retrieval_min_score must be within (0, 1]")
        if not 0.0 < self.signal_min_score <= 1.0:
            raise ValueError("signal_min_score must be within (0, 1]")
        if not 0.0 < self.urgent_min_score <= 1.0:
            raise ValueError("urgent_min_score must be within (0, 1]")
        if self.urgent_min_score < self.signal_min_score:
            raise ValueError("urgent_min_score must be greater than or equal to signal_min_score")
        if self.lexical_min_terms < 1:
            raise ValueError("lexical_min_terms must be greater than or equal to 1")

    def supports_vector_signal(self, score: float) -> bool:
        """判断向量候选是否达到一般裁决门槛。

        :param score: 候选向量分数。
        :return: 达到门槛时返回 True。
        """
        return score >= self.signal_min_score

    def supports_urgent_vector_signal(self, score: float) -> bool:
        """判断向量候选是否达到紧急升级门槛。

        :param score: 候选向量分数。
        :return: 达到门槛时返回 True。
        """
        return score >= self.urgent_min_score

    def supports_lexical_signal(self, matched_terms: Sequence[str]) -> bool:
        """判断词面命中是否足以参与裁决。

        :param matched_terms: 候选命中词列表。
        :return: 命中词数量达到门槛时返回 True。
        """
        return len([term for term in matched_terms if term and term.strip()]) >= self.lexical_min_terms
