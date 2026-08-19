"""
=============================================================================
文件：src/vet_agent/clinical_safety/query.py
作用：定义临床安全候选召回使用的结构化查询正文与范围过滤契约。
范围：位于临床安全语义抽取与 pgvector 召回之间；本模块只整理 query_text、
      宠物画像范围与证据门槛，不执行向量召回、不执行策略裁决。
说明：该模块用于替代把宠物画像、年龄、性别、意图和语义提示拼接为单一字符串的
      旧回退路径，使召回输入保持可审计、可过滤与 Fail Fast。
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from .semantic_extractor import (
    ClinicalSafetyAgeGroup,
    ClinicalSafetyRiskEvidenceState,
    ClinicalSafetySex,
    ClinicalSafetySpecies,
)

MAX_RETRIEVAL_QUERY_TEXT_LENGTH = 2000
"""向量召回查询正文的最大字符数；超长输入只保留头部主诉，避免 embedding 稳定性劣化。"""

if TYPE_CHECKING:
    from .models import ClinicalSafetyAsset
    from .semantic_extractor import ClinicalSafetySemanticResult


@dataclass(frozen=True)
class ClinicalSafetyRetrievalScope:
    """表示临床安全召回使用的结构化适用范围。

    :param species: 结构化物种范围；unknown 表示不参与过滤。
    :param sex: 结构化性别范围；unknown 表示不参与过滤。
    :param age_group: 结构化年龄阶段范围；unknown 表示不参与过滤。
    :return: 无返回值；该对象仅用于召回时的候选适用性过滤。
    """

    species: ClinicalSafetySpecies = "unknown"
    sex: ClinicalSafetySex = "unknown"
    age_group: ClinicalSafetyAgeGroup = "unknown"

    @classmethod
    def from_semantic_result(cls, semantic_result: "ClinicalSafetySemanticResult | None") -> Self:
        """从可信临床安全语义结果构造召回范围。

        :param semantic_result: 临床安全结构化语义结果；不可信语义不会进入范围过滤。
        :return: 返回结构化召回范围；语义缺失或不可信时返回 unknown 范围。
        """
        if semantic_result is None or not semantic_result.is_trusted():
            return cls()
        return cls(
            species=semantic_result.species,
            sex=semantic_result.sex,
            age_group=semantic_result.age_group,
        )

    def matches_asset(self, asset: "ClinicalSafetyAsset") -> bool:
        """判断结构化召回范围是否适用于当前资产。

        :param asset: 已审核的临床安全资产候选。
        :return: 当资产允许在当前宠物范围内参与召回时返回 True。
        """
        if self.species != "unknown" and asset.species_scope and self.species not in asset.species_scope:
            return False
        if self.sex != "unknown" and asset.sex_scope and self.sex not in asset.sex_scope:
            return False
        if self.age_group != "unknown" and asset.age_scope and self.age_group not in asset.age_scope:
            return False
        return True


@dataclass(frozen=True)
class ClinicalSafetyRetrievalRequest:
    """表示临床安全候选召回所需的结构化查询请求。

    :param query_text: 本轮用于向量召回的事实正文。
    :param scope: 宠物画像与适用范围过滤条件。
    :param risk_evidence_state: 本轮是否具备进入强召回的证据边界。
    :return: 无返回值；该对象是 evaluator 到 retriever 的唯一查询输入。
    """

    query_text: str = ""
    scope: ClinicalSafetyRetrievalScope = field(default_factory=ClinicalSafetyRetrievalScope)
    risk_evidence_state: ClinicalSafetyRiskEvidenceState = "unknown"

    @classmethod
    def from_semantic_result(
        cls,
        query_text: str,
        semantic_result: "ClinicalSafetySemanticResult | None",
    ) -> Self:
        """根据可信语义结果构造结构化召回请求。

        :param query_text: 用户本轮原始事实文本；超长时仅保留头部主诉片段。
        :param semantic_result: 临床安全结构化语义结果；不可信结果会直接阻断强召回。
        :return: 返回经过证据边界约束的结构化召回请求。
        """
        scope = ClinicalSafetyRetrievalScope.from_semantic_result(semantic_result)
        if semantic_result is None or not semantic_result.is_trusted():
            return cls(query_text="", scope=scope, risk_evidence_state="unknown")
        normalized_query_text = query_text.strip()[:MAX_RETRIEVAL_QUERY_TEXT_LENGTH]
        if semantic_result.risk_evidence_state != "sufficient":
            normalized_query_text = ""
        return cls(
            query_text=normalized_query_text,
            scope=scope,
            risk_evidence_state=semantic_result.risk_evidence_state,
        )

    def normalized_query_text(self) -> str:
        """返回用于 embedding 的标准化查询正文。

        :return: 返回去除首尾空白后的查询正文。
        """
        return self.query_text.strip()

    def is_searchable(self) -> bool:
        """判断当前请求是否允许进入强召回路径。

        :return: 只有证据充分且查询正文非空时返回 True。
        """
        return self.risk_evidence_state == "sufficient" and bool(self.normalized_query_text())

    def skip_reason(self) -> str:
        """返回当前请求无法进入强召回的显式原因。

        :return: 返回空字符串或可审计的降级原因。
        """
        if self.risk_evidence_state == "insufficient":
            return "risk_evidence_not_sufficient"
        if self.risk_evidence_state == "unknown":
            return "risk_evidence_unknown"
        if not self.normalized_query_text():
            return "empty_query"
        return ""
