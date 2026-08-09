"""
文件：src/vet_agent/clinical_safety/evaluator.py
作用：基于向量召回候选、结构化上下文和当前主诉执行 P0 临床安全复核。
说明：本层负责把候选资产裁决为 SafetySignal；召回由 ClinicalSafetyRetriever 负责，结构化语义由专用抽取器提供。
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
from .semantic_extractor import ClinicalSafetySemanticResult
from .retriever import ClinicalSafetyRetriever
from .thresholds import ClinicalSafetyThresholds


class ClinicalSafetyEvaluator:
    """根据召回候选识别隐匿高风险组合并生成安全信号。"""

    _NEGATION_PHRASES: tuple[str, ...] = (
        "没有",
        "不是",
        "并非",
        "否认",
        "未见",
        "不见",
    )
    _SHORT_NEGATION_PREFIXES: tuple[str, ...] = ("没", "无", "未")
    _EXPOSURE_MARKERS: tuple[str, ...] = (
        "误食",
        "吃了",
        "吃到",
        "吞了",
        "吞下",
        "咽下",
        "舔了",
        "舔到",
        "喝了",
        "接触",
        "偷吃",
        "喂了",
        "摄入",
        "啃了",
        "咬了",
        "可能吃",
        "疑似吃",
        "刚才把",
    )
    _DOG_MARKERS: tuple[str, ...] = ("dog", "canine", "犬", "狗")
    _CAT_MARKERS: tuple[str, ...] = ("cat", "feline", "猫")
    _MALE_MARKERS: tuple[str, ...] = ("male", "雄", "公")
    _FEMALE_MARKERS: tuple[str, ...] = ("female", "雌", "母")
    _STANDALONE_RED_FLAG_TERMS: tuple[str, ...] = (
        "尿频尿少",
        "尿不出",
        "完全尿闭",
        "发绀",
        "舌头发紫",
        "牙龈发紫",
        "呼吸困难",
        "抽搐",
        "昏迷",
        "瘫倒",
        "休克",
        "吐血",
        "呕血",
        "血便",
        "便血",
    )

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
        """根据当前文本与宠物上下文评估隐匿高风险组合。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达。
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
        """根据当前文本与宠物上下文评估风险，并显式返回回退状态。

        :param text: 用户本轮主诉和补充信息。
        :param context_text: 宠物画像等可信上下文的文本摘要。
        :param age_text: 宠物年龄的原始文本表达。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回临床安全信号与回退状态。
        """
        senior_marker = "老年" if self._is_senior_age(age_text) else ""
        base_query = "\n".join(part for part in (text, context_text, age_text, senior_marker) if part)
        query = base_query
        if semantic_result is not None:
            semantic_hints = semantic_result.to_query_hints()
            if semantic_hints:
                query = "\n".join(part for part in (base_query, semantic_hints) if part)
        normalized_query = self._normalize_text(base_query)
        retrieval_result = self.retriever.retrieve_with_resolution(query)
        semantic_is_low_confidence = self._semantic_is_low_confidence(semantic_result)
        signals: list[SafetySignal] = []
        for candidate in retrieval_result.candidates:
            signal = self._candidate_signal(
                candidate,
                normalized_query,
                age_text=age_text,
                semantic_result=semantic_result,
                allow_semantic_escalation=not semantic_is_low_confidence,
            )
            if signal is not None:
                signals.append(signal)
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

    def _candidate_signal(
        self,
        candidate: ClinicalSafetyCandidate,
        normalized_query: str,
        *,
        age_text: str,
        semantic_result: ClinicalSafetySemanticResult | None,
        allow_semantic_escalation: bool,
    ) -> SafetySignal | None:
        """将召回候选裁决为安全信号。

        :param candidate: 已按资产聚合的临床安全候选。
        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param age_text: 宠物年龄的原始文本表达。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :param allow_semantic_escalation: 是否允许结构化语义参与更激进的裁决。
        :return: 候选满足当前风险条件时返回安全信号，否则返回 None。
        """
        asset = candidate.asset
        if not self._context_applies(
            asset,
            normalized_query,
            age_text=age_text,
            semantic_result=semantic_result,
        ):
            return None
        if not self._temporal_applies(asset, semantic_result):
            return None
        matched_terms = self._valid_matched_terms(candidate, normalized_query, semantic_result=semantic_result)
        if not matched_terms and candidate.score_type != "cosine_similarity":
            return None
        if not self._intent_applies(
            asset,
            normalized_query,
            matched_terms,
            candidate,
            semantic_result=semantic_result,
            allow_semantic_escalation=allow_semantic_escalation,
        ):
            return None
        severity = self._effective_severity(
            asset,
            normalized_query,
            matched_terms,
            candidate,
            semantic_result=semantic_result,
            allow_semantic_escalation=allow_semantic_escalation,
        )
        message = self._signal_message(asset)
        audit_terms = self._compact_matches(
            [
                *matched_terms,
                *(
                    list(semantic_result.high_risk_terms)
                    if semantic_result is not None
                    else []
                ),
            ]
        )
        return SafetySignal(
            code=asset.resolved_code(),
            severity=severity,
            message=message,
            matched_terms=audit_terms,
        )

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
        resolution_state = semantic_result.resolution_state
        if scope == "remote_past" and resolution_state == "resolved":
            if asset.asset_type in {"emergency_red_flag", "danger_pattern"}:
                return semantic_result.symptom_state == "present"
            if asset.asset_type not in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}:
                return False
        if scope == "remote_past" and asset.asset_type in {"emergency_red_flag", "danger_pattern"}:
            return semantic_result.symptom_state == "present"
        return True

    def _context_applies(
        self,
        asset: ClinicalSafetyAsset,
        normalized_query: str,
        *,
        age_text: str,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> bool:
        """判断候选资产的物种、性别和年龄上下文是否适用。

        :param asset: 待裁决的临床安全资产。
        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param age_text: 宠物年龄的原始文本表达。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 当前上下文适用该资产时返回 True。
        """
        species = semantic_result.species if semantic_result is not None and semantic_result.species != "unknown" else self._detected_species(normalized_query)
        if asset.species_scope and species is not None and species not in asset.species_scope:
            return False
        sex = semantic_result.sex if semantic_result is not None and semantic_result.sex != "unknown" else self._detected_sex(normalized_query)
        if asset.sex_scope and sex is not None and sex not in asset.sex_scope:
            return False
        semantic_age_group = semantic_result.age_group if semantic_result is not None else "unknown"
        if "senior" in asset.age_scope and not (
            semantic_age_group == "senior" or self._is_senior_age(age_text) or self._is_senior_age(normalized_query)
        ):
            return False
        return True

    def _valid_matched_terms(
        self,
        candidate: ClinicalSafetyCandidate,
        normalized_query: str,
        *,
        semantic_result: ClinicalSafetySemanticResult | None,
    ) -> list[str]:
        """过滤被否定表达修饰的候选命中词。

        :param candidate: 已按资产聚合的临床安全候选。
        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 返回未被否定修饰的命中词列表。
        """
        semantic_negated_terms = {
            self._normalize_text(term)
            for term in (semantic_result.negated_terms if semantic_result is not None else ())
            if self._normalize_text(term)
        }
        matches: list[str] = []
        for term in candidate.matched_terms():
            normalized_term = self._normalize_text(term)
            if not normalized_term:
                continue
            if normalized_term in semantic_negated_terms:
                continue
            if self._term_is_negated(normalized_query, normalized_term):
                continue
            matches.append(term)
        return self._compact_matches(matches)

    def _intent_applies(
        self,
        asset: ClinicalSafetyAsset,
        normalized_query: str,
        matched_terms: list[str],
        candidate: ClinicalSafetyCandidate,
        semantic_result: ClinicalSafetySemanticResult | None,
        allow_semantic_escalation: bool,
    ) -> bool:
        """判断候选是否对应当前正在发生或可能发生的安全风险。

        :param asset: 待裁决的临床安全资产。
        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param matched_terms: 过滤后的候选命中词。
        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :param allow_semantic_escalation: 是否允许结构化语义参与更激进的裁决。
        :return: 当前语义需要生成安全信号时返回 True。
        """
        if asset.asset_type in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}:
            if semantic_result is not None:
                if semantic_result.exposure_state == "denied" and semantic_result.intent_type not in {
                    "knowledge",
                    "prevention",
                }:
                    return False
                if not allow_semantic_escalation:
                    return self._has_toxic_substance_match(asset, matched_terms)
                if semantic_result.exposure_state in {"confirmed", "possible"}:
                    if matched_terms:
                        return self._has_toxic_substance_match(asset, matched_terms)
                    return candidate.score_type == "cosine_similarity" and self.thresholds.supports_vector_signal(candidate.score)
                if semantic_result.intent_type in {"knowledge", "prevention"}:
                    if matched_terms:
                        return self._has_toxic_substance_match(asset, matched_terms)
                    return candidate.score_type == "cosine_similarity" and self.thresholds.supports_vector_signal(candidate.score)
            return self._has_toxic_substance_match(asset, matched_terms)
        if matched_terms:
            return self._has_sufficient_lexical_evidence(matched_terms)
        return candidate.score_type == "cosine_similarity" and self.thresholds.supports_vector_signal(candidate.score)

    def _effective_severity(
        self,
        asset: ClinicalSafetyAsset,
        normalized_query: str,
        matched_terms: list[str],
        candidate: ClinicalSafetyCandidate,
        semantic_result: ClinicalSafetySemanticResult | None,
        allow_semantic_escalation: bool,
    ) -> SafetySeverity:
        """根据资产、用户意图和命中强度确定最终严重级别。

        :param asset: 待裁决的临床安全资产。
        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param matched_terms: 过滤后的候选命中词。
        :param candidate: 已按资产聚合的临床安全候选。
        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :param allow_semantic_escalation: 是否允许结构化语义参与更激进的裁决。
        :return: 返回写入 SafetySignal 的严重级别。
        """
        if asset.asset_type in {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}:
            if semantic_result is not None:
                if not allow_semantic_escalation:
                    return "caution"
                if semantic_result.exposure_state == "confirmed":
                    severity = "urgent"
                elif semantic_result.exposure_state == "possible":
                    severity = "urgent" if self._has_exposure_intent(normalized_query) else "caution"
                elif semantic_result.intent_type in {"knowledge", "prevention"}:
                    severity = "caution"
                else:
                    severity = "urgent" if self._has_exposure_intent(normalized_query) else "caution"
                return self._adjust_temporal_severity(severity, semantic_result)
            return "urgent" if self._has_exposure_intent(normalized_query) else "caution"
        if asset.severity == "urgent" or asset.action_class in {"emergency", "same_day_visit"}:
            return self._adjust_temporal_severity("urgent", semantic_result)
        if asset.asset_type in {"emergency_red_flag", "danger_pattern"} and len(matched_terms) >= 2:
            return self._adjust_temporal_severity("urgent", semantic_result)
        if candidate.score_type == "cosine_similarity" and self.thresholds.supports_urgent_vector_signal(candidate.score):
            return self._adjust_temporal_severity("urgent", semantic_result)
        return asset.severity

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

    def _semantic_is_low_confidence(self, semantic_result: ClinicalSafetySemanticResult | None) -> bool:
        """判断结构化语义是否属于低置信度回退结果。

        :param semantic_result: 由 LLM 抽取的结构化临床安全语义。
        :return: 低置信度结果返回 True，否则返回 False。
        """
        if semantic_result is None:
            return False
        return semantic_result.is_low_confidence()

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

    def _detected_species(self, normalized_query: str) -> str | None:
        """从查询和可信上下文中识别物种。

        :param normalized_query: 已规范化的用户输入与可信上下文。
        :return: 返回 dog、cat 或 None。
        """
        dog_seen = any(marker in normalized_query for marker in self._DOG_MARKERS)
        cat_seen = any(marker in normalized_query for marker in self._CAT_MARKERS)
        if dog_seen and not cat_seen:
            return "dog"
        if cat_seen and not dog_seen:
            return "cat"
        return None

    def _detected_sex(self, normalized_query: str) -> str | None:
        """从查询和可信上下文中识别性别。

        :param normalized_query: 已规范化的用户输入与可信上下文。
        :return: 返回 male、female 或 None。
        """
        male_seen = any(marker in normalized_query for marker in self._MALE_MARKERS)
        female_seen = any(marker in normalized_query for marker in self._FEMALE_MARKERS)
        if male_seen and not female_seen:
            return "male"
        if female_seen and not male_seen:
            return "female"
        return None

    def _has_exposure_intent(self, normalized_query: str) -> bool:
        """识别用户是否表达了实际或可能的毒物暴露。

        :param normalized_query: 已规范化的用户输入与可信上下文。
        :return: 存在暴露语义时返回 True。
        """
        return any(marker in normalized_query for marker in self._EXPOSURE_MARKERS)

    def _has_toxic_substance_match(
        self,
        asset: ClinicalSafetyAsset,
        matched_terms: list[str],
    ) -> bool:
        """判断毒物候选是否命中了物质名称、别名或暴露载体。

        :param asset: 待裁决的毒物或人药安全资产。
        :param matched_terms: 过滤后的候选命中词。
        :return: 命中物质本体或风险载体时返回 True。
        """
        substance_terms = {
            self._normalize_text(term)
            for term in (asset.canonical_name, *asset.aliases, *asset.carriers)
            if self._normalize_text(term)
        }
        return any(self._normalize_text(term) in substance_terms for term in matched_terms)

    def _has_sufficient_lexical_evidence(self, matched_terms: list[str]) -> bool:
        """判断文本命中是否具有足够的症状或表达特异性。

        :param matched_terms: 过滤后的候选命中词。
        :return: 至少两条线索，或命中可单独成红旗的短语时返回 True。
        """
        normalized_terms = [self._normalize_text(term) for term in matched_terms if self._normalize_text(term)]
        if self.thresholds.supports_lexical_signal(normalized_terms):
            return True
        return any(term in self._STANDALONE_RED_FLAG_TERMS for term in normalized_terms)

    def _term_is_negated(self, normalized_query: str, normalized_term: str) -> bool:
        """判断某个命中词在查询中是否被否定表达修饰。

        :param normalized_query: 已规范化的用户输入与可信上下文。
        :param normalized_term: 已规范化的命中词。
        :return: 命中被否定表达修饰时返回 True。
        """
        found = False
        index = normalized_query.find(normalized_term)
        while index >= 0:
            found = True
            if not self._is_negated_occurrence(normalized_query, index):
                return False
            index = normalized_query.find(normalized_term, index + len(normalized_term))
        return found

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

    def _is_negated_occurrence(self, normalized_query: str, index: int) -> bool:
        """判断命中词前的短窗口是否包含否定表达。

        :param normalized_query: 已规范化的完整检索文本。
        :param index: 命中词在检索文本中的起始位置。
        :return: 被否定表达修饰时返回 True，否则返回 False。
        """
        prefix_window = normalized_query[max(0, index - 6) : index]
        if any(phrase in prefix_window for phrase in self._NEGATION_PHRASES):
            return True
        return any(
            prefix_window.endswith(prefix)
            or prefix_window.endswith(f"{prefix}明显")
            or prefix_window.endswith(f"{prefix}完全")
            for prefix in self._SHORT_NEGATION_PREFIXES
        )

    def _is_senior_age(self, age_text: str) -> bool:
        """根据年龄文本识别是否属于中老年宠物。

        :param age_text: 宠物年龄的原始文本表达。
        :return: 属于中老年阶段时返回 True，否则返回 False。
        """
        normalized_age = self._normalize_text(age_text)
        if any(marker in normalized_age for marker in ("老年", "高龄", "senior", "middleaged", "中老年")):
            return True
        match = re.search(r"\d+(?:\.\d+)?", normalized_age)
        return bool(match and float(match.group(0)) >= 7)

    def _normalize_text(self, text: str) -> str:
        """规范化文本，以降低大小写和空白字符对匹配的影响。

        :param text: 待规范化的原始文本。
        :return: 返回小写且去除空白的文本。
        """
        return re.sub(r"\s+", "", text.lower())
