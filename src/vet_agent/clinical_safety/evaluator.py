"""
=============================================================================
文件：src/vet_agent/clinical_safety/evaluator.py
作用：编排临床安全候选召回与 OPA 策略裁决。
范围：位于临床安全语义抽取之后、主 Agent 安全分诊之前；本层只负责召回查询构造、
      策略输入组装、策略结果转换和显式审计状态透出。
说明：候选来源必须来自 ClinicalSafetyRetriever 的向量召回结果；本层不扫描用户原始文本、
      不生成候选、不根据关键词或 Python 分支执行最终动作裁决。
=============================================================================
"""

from __future__ import annotations

import unicodedata

from vet_agent import AgentTurnRequest, SafetySignal

from .fallback import (
    ClinicalSafetyEvaluationResult,
    ClinicalSafetyFallbackState,
    ClinicalSafetySemanticFallbackState,
)
from .policy import (
    ClinicalSafetyPolicyClient,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyPolicyRequestContext,
)
from .retriever import ClinicalSafetyRetriever
from .semantic_extractor import ClinicalSafetySemanticResult
from .thresholds import ClinicalSafetyThresholds


class ClinicalSafetyEvaluator:
    """编排临床安全向量候选召回与策略裁决。"""

    def __init__(
        self,
        retriever: ClinicalSafetyRetriever,
        policy_client: ClinicalSafetyPolicyClient,
        *,
        thresholds: ClinicalSafetyThresholds | None = None,
    ) -> None:
        """初始化临床安全裁决编排器。

        :param retriever: 临床安全召回器。
        :param policy_client: 临床安全策略裁决客户端。
        :param thresholds: 临床安全阈值对象；未提供时优先采用召回器内阈值。
        :return: 无返回值。
        """
        self.retriever = retriever
        self.policy_client = policy_client
        self.thresholds = thresholds or getattr(retriever, "thresholds", ClinicalSafetyThresholds())

    async def assess(
        self,
        text: str,
        context_text: str = "",
        age_text: str = "",
        *,
        request: AgentTurnRequest | None = None,
        semantic_result: ClinicalSafetySemanticResult | None = None,
    ) -> list[SafetySignal]:
        """根据当前文本与可信上下文执行临床安全策略裁决。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达；当前仅用于召回查询增强，不用于关键词裁决。
        :param request: 当前 Agent 回合请求；用于构造策略审计范围。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回命中的标准安全信号列表。
        """
        result = await self.assess_with_resolution(
            text,
            context_text=context_text,
            age_text=age_text,
            request=request,
            semantic_result=semantic_result,
        )
        return result.signals

    async def assess_with_resolution(
        self,
        text: str,
        context_text: str = "",
        age_text: str = "",
        *,
        request: AgentTurnRequest | None = None,
        semantic_result: ClinicalSafetySemanticResult | None = None,
    ) -> ClinicalSafetyEvaluationResult:
        """召回临床安全候选，提交 OPA 策略裁决，并显式返回审计状态。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达；当前仅用于召回查询增强，不用于关键词裁决。
        :param request: 当前 Agent 回合请求；为空时使用空审计范围，主要供单元测试替身使用。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回临床安全信号与显式状态。
        """
        trusted_semantic_result = self._trusted_semantic_result(semantic_result)
        retrieval_result = self.retriever.retrieve_with_resolution(
            self._build_query(
                text=text,
                context_text=context_text,
                age_text=age_text,
                semantic_result=trusted_semantic_result,
            )
        )
        semantic_fallback = (
            semantic_result.to_fallback_state()
            if semantic_result is not None
            else ClinicalSafetySemanticFallbackState(
                degraded=True,
                reasons=("clinical_safety_semantic_result_missing",),
                strategy="not_requested",
            )
        )
        fallback_state = ClinicalSafetyFallbackState(
            retrieval=retrieval_result.state,
            semantic=semantic_fallback,
        )
        decision = await self.policy_client.decide(
            ClinicalSafetyPolicyInput(
                context=(
                    ClinicalSafetyPolicyRequestContext.from_request(request)
                    if request is not None
                    else ClinicalSafetyPolicyRequestContext()
                ),
                semantic_result=semantic_result,
                retrieval_state=retrieval_result.state,
                candidates=tuple(retrieval_result.candidates),
                thresholds=self.thresholds,
            )
        )
        return ClinicalSafetyEvaluationResult(
            signals=self._dedupe_signals(list(decision.signals)),
            fallback_state=fallback_state,
            policy_decision=decision.to_metadata(),
        )

    def _build_query(
        self,
        *,
        text: str,
        context_text: str,
        age_text: str,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> str:
        """构造临床安全向量召回查询。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 可信宠物上下文摘要。
        :param age_text: 宠物年龄原文。
        :param semantic_result: 结构化临床安全语义结果。
        :return: 返回合并后的召回查询文本。
        """
        del context_text, age_text
        base_query = text.strip()
        if semantic_result is None or not semantic_result.is_trusted():
            return ""
        semantic_hints = semantic_result.to_query_hints()
        if not semantic_hints:
            return ""
        if not base_query:
            return ""
        return "\n".join(part for part in (base_query, semantic_hints) if part)

    def _trusted_semantic_result(
        self,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> ClinicalSafetySemanticResult | None:
        """筛选可进入召回增强的可信结构化语义。

        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 语义可信时返回原对象，否则返回 None；不可信语义仍会以降级状态进入 OPA。
        """
        if semantic_result is None:
            return None
        if not semantic_result.is_trusted():
            return None
        return semantic_result

    def _dedupe_signals(self, signals: list[SafetySignal]) -> list[SafetySignal]:
        """按安全编码去重，并保留更高严重级别的信号。

        :param signals: 原始安全信号列表。
        :return: 返回去重后的安全信号列表。
        """
        severity_rank = {"info": 0, "caution": 1, "urgent": 2, "blocked": 3}
        by_code: dict[str, SafetySignal] = {}
        for signal in signals:
            existing = by_code.get(signal.code)
            if existing is None:
                by_code[signal.code] = signal
                continue
            merged_terms = self._compact_matches([*existing.matched_terms, *signal.matched_terms])
            if severity_rank[signal.severity] > severity_rank[existing.severity]:
                signal.matched_terms[:] = merged_terms
                by_code[signal.code] = signal
            else:
                existing.matched_terms[:] = merged_terms
        return sorted(by_code.values(), key=lambda item: severity_rank[item.severity], reverse=True)

    def _compact_matches(self, matches: list[str]) -> list[str]:
        """压缩命中词，移除被更长命中短语包含的子串。

        :param matches: 原始命中词列表。
        :return: 返回去除冗余子串后的命中词列表。
        """
        unique_matches = list(dict.fromkeys(match for match in matches if match))
        normalized_pairs = [(term, self._normalize_text(term)) for term in unique_matches]
        compacted: list[str] = []
        for term, normalized_term in normalized_pairs:
            if any(
                term != other_term
                and normalized_term
                and normalized_term in other_normalized
                and len(normalized_term) < len(other_normalized)
                for other_term, other_normalized in normalized_pairs
            ):
                continue
            compacted.append(term)
        return compacted

    def _normalize_text(self, text: str) -> str:
        """归一化安全信号命中词，仅用于重复展示合并。

        :param text: 原始命中词。
        :return: 返回完成 Unicode 归一、大小写折叠与空白压缩后的命中词。
        """
        normalized = unicodedata.normalize("NFKC", text)
        return "".join(normalized.casefold().split())
