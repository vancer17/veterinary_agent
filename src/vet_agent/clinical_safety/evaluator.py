"""
文件：src/vet_agent/clinical_safety/evaluator.py
作用：基于向量召回候选和可信结构化语义生成临床安全信号。
范围：位于临床安全候选召回之后、主 Agent 安全分诊之前；本层不执行文本关键词识别，不承担检索回退或策略状态机职责。
说明：候选来源必须来自 ClinicalSafetyRetriever 的向量召回结果；结构化语义缺失或低置信时只能保守使用候选分数，不补造暴露、否定、时间或物种事实。
"""

from __future__ import annotations

import re

from vet_agent import SafetySignal

from .fallback import (
    ClinicalSafetyEvaluationResult,
    ClinicalSafetyFallbackState,
    ClinicalSafetySemanticFallbackState,
)
from .models import ClinicalSafetyAsset, ClinicalSafetyCandidate, SafetySeverity
from .retriever import ClinicalSafetyRetriever
from .semantic_extractor import ClinicalSafetySemanticResult
from .thresholds import ClinicalSafetyThresholds


TOXIC_ASSET_TYPES = {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}


class ClinicalSafetyEvaluator:
    """根据向量召回候选和结构化语义生成临床安全信号。"""

    def __init__(
        self,
        retriever: ClinicalSafetyRetriever,
        *,
        thresholds: ClinicalSafetyThresholds | None = None,
    ) -> None:
        """初始化结构化临床安全评估器。

        :param retriever: 临床安全召回器。
        :param thresholds: 临床安全阈值对象；未提供时优先采用召回器内阈值。
        :return: 无返回值。
        """
        self.retriever = retriever
        self.thresholds = thresholds or getattr(retriever, "thresholds", ClinicalSafetyThresholds())

    def assess(
        self,
        text: str,
        context_text: str = "",
        age_text: str = "",
        *,
        semantic_result: ClinicalSafetySemanticResult | None = None,
    ) -> list[SafetySignal]:
        """根据当前文本与可信上下文执行临床安全评估。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达；当前仅用于召回查询增强，不用于关键词裁决。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回命中的标准安全信号列表。
        """
        return self.assess_with_resolution(
            text,
            context_text=context_text,
            age_text=age_text,
            semantic_result=semantic_result,
        ).signals

    def assess_with_resolution(
        self,
        text: str,
        context_text: str = "",
        age_text: str = "",
        *,
        semantic_result: ClinicalSafetySemanticResult | None = None,
    ) -> ClinicalSafetyEvaluationResult:
        """根据当前文本与可信上下文评估风险，并显式返回召回和语义状态。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达；当前仅用于召回查询增强，不用于关键词裁决。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回临床安全信号与显式状态。
        """
        retrieval_result = self.retriever.retrieve_with_resolution(
            self._build_query(
                text=text,
                context_text=context_text,
                age_text=age_text,
                semantic_result=semantic_result,
            )
        )
        trusted_semantic_result = self._trusted_semantic_result(semantic_result)
        signals = [
            signal
            for signal in (
                self._candidate_signal(
                    candidate,
                    semantic_result=trusted_semantic_result,
                )
                for candidate in retrieval_result.candidates
            )
            if signal is not None
        ]
        semantic_fallback = (
            semantic_result.to_fallback_state()
            if semantic_result is not None
            else ClinicalSafetySemanticFallbackState()
        )
        return ClinicalSafetyEvaluationResult(
            signals=self._dedupe_signals(signals),
            fallback_state=ClinicalSafetyFallbackState(
                retrieval=retrieval_result.state,
                semantic=semantic_fallback,
            ),
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
        base_query = "\n".join(part for part in (text, context_text, age_text) if part)
        if semantic_result is None:
            return base_query
        semantic_hints = semantic_result.to_query_hints()
        return "\n".join(part for part in (base_query, semantic_hints) if part)

    def _candidate_signal(
        self,
        candidate: ClinicalSafetyCandidate,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> SafetySignal | None:
        """将单个向量召回候选裁决为安全信号。

        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 候选满足当前风险条件时返回安全信号，否则返回 None。
        """
        asset = candidate.asset
        if not self._context_applies(asset, semantic_result):
            return None
        if not self._temporal_applies(asset, semantic_result):
            return None
        matched_terms = self._valid_matched_terms(candidate, semantic_result=semantic_result)
        if not self._intent_applies(
            asset,
            candidate,
            semantic_result=semantic_result,
        ):
            return None
        severity = self._effective_severity(
            asset,
            candidate,
            semantic_result=semantic_result,
        )
        return SafetySignal(
            code=asset.resolved_code(),
            severity=severity,
            message=self._signal_message(asset),
            matched_terms=self._audit_terms(matched_terms, semantic_result),
        )

    def _context_applies(
        self,
        asset: ClinicalSafetyAsset,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> bool:
        """判断候选资产的物种、性别和年龄上下文是否适用。

        :param asset: 待裁决的临床安全资产。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 当前上下文适用该资产时返回 True。
        """
        if semantic_result is None:
            return True
        if asset.species_scope and semantic_result.species != "unknown" and semantic_result.species not in asset.species_scope:
            return False
        if asset.sex_scope and semantic_result.sex != "unknown" and semantic_result.sex not in asset.sex_scope:
            return False
        if "senior" in asset.age_scope and semantic_result.age_group not in {"senior", "unknown"}:
            return False
        return True

    def _temporal_applies(
        self,
        asset: ClinicalSafetyAsset,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> bool:
        """判断候选风险是否仍适用于当前时间语境。

        :param asset: 待裁决的临床安全资产。
        :param semantic_result: 由结构化语义抽取器生成的时间和状态结果。
        :return: 当前时间语境仍支持该候选进入裁决时返回 True。
        """
        if semantic_result is None:
            return True
        scope = self._temporal_scope(semantic_result)
        if scope == "remote_past" and semantic_result.resolution_state == "resolved":
            if asset.asset_type in {"emergency_red_flag", "danger_pattern"}:
                return semantic_result.symptom_state == "present"
            if asset.asset_type not in TOXIC_ASSET_TYPES:
                return False
        if scope == "remote_past" and asset.asset_type in {"emergency_red_flag", "danger_pattern"}:
            return semantic_result.symptom_state == "present"
        return True

    def _valid_matched_terms(
        self,
        candidate: ClinicalSafetyCandidate,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> list[str]:
        """根据结构化否定语义过滤候选审计命中词。

        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回未被结构化否定语义排除的命中词列表。
        """
        semantic_negated_terms = {
            self._normalize_text(term)
            for term in (semantic_result.negated_terms if semantic_result is not None else ())
            if self._normalize_text(term)
        }
        matches: list[str] = []
        for term in candidate.matched_terms():
            normalized_term = self._normalize_text(term)
            if not normalized_term or normalized_term in semantic_negated_terms:
                continue
            matches.append(term)
        return self._compact_matches(matches)

    def _intent_applies(
        self,
        asset: ClinicalSafetyAsset,
        candidate: ClinicalSafetyCandidate,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> bool:
        """判断向量候选是否允许生成安全信号。

        :param asset: 待裁决的临床安全资产。
        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 当前候选需要生成安全信号时返回 True。
        """
        if asset.asset_type in TOXIC_ASSET_TYPES and semantic_result is not None:
            if semantic_result.exposure_state == "denied" and semantic_result.intent_type not in {"knowledge", "prevention"}:
                return False
            if semantic_result.exposure_state in {"confirmed", "possible"}:
                return candidate.score >= self.thresholds.retrieval_min_score
            if semantic_result.intent_type in {"knowledge", "prevention"}:
                return candidate.score >= self.thresholds.retrieval_min_score
        return candidate.score >= self.thresholds.signal_min_score

    def _effective_severity(
        self,
        asset: ClinicalSafetyAsset,
        candidate: ClinicalSafetyCandidate,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> SafetySeverity:
        """根据资产、向量分数和可信语义确定最终严重级别。

        :param asset: 待裁决的临床安全资产。
        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回写入 SafetySignal 的严重级别。
        """
        if asset.asset_type in TOXIC_ASSET_TYPES:
            return self._toxic_severity(
                candidate,
                semantic_result=semantic_result,
            )
        if asset.severity == "urgent" or asset.action_class in {"emergency", "same_day_visit"}:
            return self._adjust_temporal_severity("urgent", semantic_result)
        if candidate.score >= self.thresholds.urgent_min_score:
            return self._adjust_temporal_severity("urgent", semantic_result)
        return asset.severity

    def _toxic_severity(
        self,
        candidate: ClinicalSafetyCandidate,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> SafetySeverity:
        """确定毒物或人用药候选的严重级别。

        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回毒物或人用药候选严重级别。
        """
        if semantic_result is None:
            return "caution"
        if semantic_result.exposure_state == "confirmed":
            return self._adjust_temporal_severity("urgent", semantic_result)
        if semantic_result.exposure_state == "possible":
            severity: SafetySeverity = "urgent" if candidate.score >= self.thresholds.urgent_min_score else "caution"
            return self._adjust_temporal_severity(severity, semantic_result)
        return "caution"

    def _adjust_temporal_severity(
        self,
        severity: SafetySeverity,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> SafetySeverity:
        """根据时间范围和事件是否结束调整急性风险严重度。

        :param severity: 未应用时间调整前的基础严重度。
        :param semantic_result: 由结构化语义抽取器生成的时间和状态结果。
        :return: 返回应用时间语义后的安全严重度。
        """
        if semantic_result is None or severity != "urgent":
            return severity
        scope = self._temporal_scope(semantic_result)
        if scope == "recent_past" and semantic_result.resolution_state == "resolved":
            return "caution"
        if scope == "remote_past" and semantic_result.symptom_state != "present":
            return "caution"
        return severity

    def _temporal_scope(self, semantic_result: ClinicalSafetySemanticResult) -> str:
        """读取时间范围，并兼容未携带新时间范围字段的旧语义结果。

        :param semantic_result: 由结构化语义抽取器生成的结果。
        :return: 返回规范化的时间范围字符串。
        """
        if semantic_result.temporal_scope != "unclear":
            return semantic_result.temporal_scope
        if semantic_result.temporal_state == "current":
            return "ongoing"
        if semantic_result.temporal_state == "past":
            return "remote_past"
        return "unclear"

    def _trusted_semantic_result(
        self,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> ClinicalSafetySemanticResult | None:
        """筛选可进入候选适配与裁决输入的可信结构化语义。

        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 语义可信时返回原对象，否则返回 None。
        """
        if semantic_result is None:
            return None
        if not semantic_result.is_trusted():
            return None
        return semantic_result

    def _signal_message(self, asset: ClinicalSafetyAsset) -> str:
        """生成安全信号展示文案。

        :param asset: 已命中的临床安全资产。
        :return: 返回安全信号文案。
        """
        if asset.triage_message:
            return asset.triage_message
        if asset.clinical_risk_summary:
            return asset.clinical_risk_summary
        return f"命中临床安全风险：{asset.canonical_name}"

    def _audit_terms(
        self,
        matched_terms: list[str],
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> list[str]:
        """汇总安全信号审计命中词。

        :param matched_terms: 召回候选携带的命中词。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回压缩后的审计命中词列表。
        """
        semantic_terms = list(semantic_result.high_risk_terms) if semantic_result is not None else []
        return self._compact_matches([*matched_terms, *semantic_terms])

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
        """规范化文本，以降低大小写和空白字符对审计命中词压缩的影响。

        :param text: 待规范化的原始文本。
        :return: 返回小写且去除空白后的文本。
        """
        return re.sub(r"\s+", "", text.lower())
