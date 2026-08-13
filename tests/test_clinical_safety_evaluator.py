"""
文件：tests/test_clinical_safety_evaluator.py
作用：验证临床安全 evaluator 在 OPA 裁决迁移后的候选召回、策略输入组装与显式状态透出。
说明：本文件使用显式注入的测试策略替身，不验证生产本地回退裁决；生产策略行为由 OPA Rego 测试覆盖。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from vet_agent import SafetySignal
from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyAgeGroup,
    ClinicalSafetyCandidate,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    ClinicalSafetyExposureState,
    ClinicalSafetyEvaluator,
    ClinicalSafetyIntentType,
    ClinicalSafetyPolicyAction,
    ClinicalSafetyPolicyClient,
    ClinicalSafetyPolicyDecision,
    ClinicalSafetyPolicyInput,
    ClinicalSafetyResolutionState,
    ClinicalSafetyRetriever,
    ClinicalSafetySex,
    ClinicalSafetySemanticResult,
    ClinicalSafetySpecies,
    ClinicalSafetySymptomState,
    ClinicalSafetyTemporalScope,
    ClinicalSafetyTemporalState,
)


TOXIC_TEST_ASSET_TYPES = {"toxin", "human_drug", "plant_toxin", "chemical_toxin"}


class StaticClinicalSafetyPolicyClient(ClinicalSafetyPolicyClient):
    """提供 evaluator 测试用的结构化临床安全策略替身。

    说明：该替身只消费 evaluator 组装出的结构化候选与可信语义，不读取原始用户文本。
    """

    async def decide(self, policy_input: ClinicalSafetyPolicyInput) -> ClinicalSafetyPolicyDecision:
        """根据结构化策略输入返回测试决策。

        :param policy_input: evaluator 组装后的临床安全策略输入。
        :return: 返回用于断言 evaluator 数据链的测试策略决策。
        """
        candidates = [
            candidate
            for candidate in policy_input.candidates
            if not self._suppressed(candidate, policy_input)
            and candidate.score >= policy_input.thresholds.signal_min_score
        ]
        if not candidates:
            return ClinicalSafetyPolicyDecision(
                action=ClinicalSafetyPolicyAction.ALLOW,
                allow=True,
                message="测试临床安全策略允许继续执行。",
                metadata={"policy_backend": "static_test"},
            )
        signals = tuple(
            SafetySignal(
                code=candidate.asset.code,
                severity=self._severity(candidate, policy_input),
                message=candidate.asset.triage_message
                or candidate.asset.clinical_risk_summary
                or f"命中临床安全风险：{candidate.asset.canonical_name}",
                matched_terms=list(
                    dict.fromkeys(
                        [
                            *candidate.matched_terms(),
                            *(
                                policy_input.semantic_result.high_risk_terms
                                if policy_input.semantic_result is not None
                                and policy_input.semantic_result.is_trusted()
                                else ()
                            ),
                        ]
                    )
                ),
            )
            for candidate in candidates
        )
        action = (
            ClinicalSafetyPolicyAction.ESCALATE
            if any(signal.severity in {"urgent", "blocked"} for signal in signals)
            else ClinicalSafetyPolicyAction.OBSERVE
        )
        return ClinicalSafetyPolicyDecision(
            action=action,
            allow=action != ClinicalSafetyPolicyAction.BLOCK,
            message="测试临床安全策略完成结构化候选裁决。",
            reasons=tuple(candidate.asset.code for candidate in candidates),
            signals=signals,
            metadata={"policy_backend": "static_test"},
        )

    def is_ready(self) -> bool:
        """声明测试策略客户端可用。

        :return: 始终返回 True。
        """
        return True

    def _suppressed(
        self,
        candidate: ClinicalSafetyCandidate,
        policy_input: ClinicalSafetyPolicyInput,
    ) -> bool:
        """判断测试候选是否被可信否认暴露语义抑制。

        :param candidate: 待判断的候选对象。
        :param policy_input: evaluator 组装后的临床安全策略输入。
        :return: 符合可信否认暴露抑制条件时返回 True。
        """
        semantic = policy_input.semantic_result
        asset = candidate.asset
        return bool(
            semantic is not None
            and semantic.is_trusted()
            and asset.asset_type in TOXIC_TEST_ASSET_TYPES
            and semantic.exposure_state == "denied"
            and semantic.intent_type not in {"knowledge", "prevention"}
        )

    def _severity(
        self,
        candidate: ClinicalSafetyCandidate,
        policy_input: ClinicalSafetyPolicyInput,
    ) -> str:
        """根据结构化候选和测试阈值返回安全信号级别。

        :param candidate: 待转换为安全信号的候选对象。
        :param policy_input: evaluator 组装后的临床安全策略输入。
        :return: 返回 SafetySignal 可接受的严重级别。
        """
        asset = candidate.asset
        score = candidate.score
        if asset.severity == "urgent" or asset.action_class in {"emergency", "same_day_visit", "urgent_visit"}:
            return "urgent"
        if score >= policy_input.thresholds.urgent_min_score:
            return "urgent"
        return asset.severity


class DuplicateSignalClinicalSafetyPolicyClient(ClinicalSafetyPolicyClient):
    """提供重复安全信号合并测试用的策略替身。

    说明：该替身模拟真实 OPA 将不同候选或语义项映射为同一安全编码的场景，
    用于验证 evaluator 只负责审计展示去重，不执行临床动作裁决。
    """

    async def decide(self, policy_input: ClinicalSafetyPolicyInput) -> ClinicalSafetyPolicyDecision:
        """返回包含重复安全编码的测试策略决策。

        :param policy_input: evaluator 组装后的临床安全策略输入。
        :return: 返回用于触发安全信号合并分支的测试策略决策。
        """
        del policy_input
        return ClinicalSafetyPolicyDecision(
            action=ClinicalSafetyPolicyAction.ESCALATE,
            allow=True,
            message="测试临床安全策略返回重复安全信号。",
            signals=(
                SafetySignal(
                    code="CYANOSIS_RISK_PATTERN",
                    severity="caution",
                    message="命中较低等级测试信号。",
                    matched_terms=["牙龈发紫", "呼吸很快"],
                ),
                SafetySignal(
                    code="CYANOSIS_RISK_PATTERN",
                    severity="urgent",
                    message="命中较高等级测试信号。",
                    matched_terms=[" 牙龈发紫 ", "牙龈发紫并呼吸很快"],
                ),
            ),
            metadata={"policy_backend": "duplicate_signal_test"},
        )

    def is_ready(self) -> bool:
        """声明重复信号测试策略客户端可用。

        :return: 始终返回 True。
        """
        return True


class StaticEmbeddingClient:
    """提供固定 embedding 的 evaluator 测试客户端。"""

    @property
    def available(self) -> bool:
        """声明测试 embedding 客户端始终可用。

        :return: 始终返回 True。
        """
        return True

    def embed(self, text: str) -> list[float]:
        """为测试查询返回固定 embedding。

        :param text: 待向量化的查询文本。
        :return: 返回固定二维向量。
        """
        assert text
        return [0.2, 0.8]


class VectorHitClinicalSafetyRepository:
    """提供固定向量命中的 evaluator 内存仓储。"""

    def __init__(
        self,
        asset: ClinicalSafetyAsset,
        chunk: ClinicalSafetyChunk,
        *,
        hit_score: float = 0.91,
    ) -> None:
        """初始化 evaluator 测试仓储。

        :param asset: 用于候选归一的临床安全资产。
        :param chunk: 用于向量命中的临床安全 chunk。
        :param hit_score: 向量命中分数。
        :return: 无返回值。
        """
        self.asset = asset
        self.chunk = chunk
        self.hit_score = hit_score

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取测试资产。

        :param published_only: 是否仅返回发布态资产。
        :return: 返回单个测试资产。
        """
        del published_only
        return [self.asset]

    def chunks(
        self,
        *,
        chunk_type: ClinicalSafetyChunkType | None = None,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取测试 chunk。

        :param chunk_type: 限定读取的 chunk 类型。
        :param published_only: 是否仅返回发布态 chunk。
        :return: 返回测试 chunk 列表。
        """
        del published_only
        if chunk_type is not None and chunk_type != self.chunk.chunk_type:
            return []
        return [self.chunk]

    def asset_by_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> ClinicalSafetyAsset | None:
        """按标识读取测试资产。

        :param asset_id: 资产标识。
        :param published_only: 是否仅返回发布态资产。
        :return: 标识匹配时返回资产，否则返回 None。
        """
        del published_only
        return self.asset if asset_id == self.asset.asset_id else None

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取指定资产关联的测试 chunk。

        :param asset_id: 资产标识。
        :param published_only: 是否仅返回发布态 chunk。
        :return: 返回关联 chunk 列表。
        """
        del published_only
        return [self.chunk] if asset_id == self.asset.asset_id else []

    def retrieve_vector_chunk_hits(
        self,
        query_embedding: Sequence[float],
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
        min_score: float,
    ) -> list[ClinicalSafetyChunkHit]:
        """返回固定向量命中，模拟 PostgreSQL/pgvector 主路径。

        :param query_embedding: 查询向量。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回固定向量命中列表。
        """
        assert list(query_embedding) == [0.2, 0.8]
        assert self.chunk.chunk_type in chunk_types
        assert limit > 0
        if self.hit_score < min_score:
            return []
        return [
            ClinicalSafetyChunkHit(
                chunk=self.chunk,
                score=self.hit_score,
                distance=round(1.0 - self.hit_score, 4),
                score_type="cosine_similarity",
                retrieval_source="clinical_safety_pgvector",
                embedding_model="test-embedding",
            )
        ]

    def is_ready(self) -> bool:
        """声明测试仓储始终可用。

        :return: 始终返回 True。
        """
        return True


def _human_drug_asset(*, symptoms: tuple[str, ...] = ("呕吐",)) -> ClinicalSafetyAsset:
    """构造人用药物临床安全测试资产。

    :param symptoms: 资产症状线索。
    :return: 返回临床安全资产。
    """
    return ClinicalSafetyAsset(
        asset_id="safety_human_drug_001",
        asset_type="human_drug",
        canonical_name="对乙酰氨基酚",
        category="人用药物",
        species_scope=("cat", "dog"),
        sex_scope=(),
        age_scope=(),
        severity="urgent",
        action_class="emergency",
        code="TOXIC_SUBSTANCE",
        aliases=("泰诺", "扑热息痛"),
        carriers=(),
        user_expressions=(),
        symptoms=symptoms,
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛", *symptoms),
    )


def _danger_pattern_asset() -> ClinicalSafetyAsset:
    """构造危险体征测试资产。

    :return: 返回临床安全资产。
    """
    return ClinicalSafetyAsset(
        asset_id="safety_danger_pattern_001",
        asset_type="danger_pattern",
        canonical_name="发绀发紫",
        category="呼吸循环",
        species_scope=("cat", "dog"),
        sex_scope=(),
        age_scope=(),
        severity="caution",
        action_class="safety_warning",
        code="CYANOSIS_RISK_PATTERN",
        aliases=("皮肤发紫",),
        carriers=(),
        user_expressions=(),
        symptoms=("轻微不适",),
        recognition_phrases=("发绀", "发紫", "轻微不适"),
    )


def _recognition_chunk(asset: ClinicalSafetyAsset, *, text: str) -> ClinicalSafetyChunk:
    """构造临床安全识别 chunk。

    :param asset: 关联的临床安全资产。
    :param text: 向量化文本。
    :return: 返回临床安全 chunk。
    """
    return ClinicalSafetyChunk(
        chunk_id=f"{asset.asset_id}.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title=f"{asset.canonical_name} 风险识别",
        embedding_text=text,
        metadata={},
        review_status="approved",
    )


def _evaluator_for(
    asset: ClinicalSafetyAsset,
    chunk: ClinicalSafetyChunk,
    *,
    hit_score: float = 0.91,
    min_score: float = 0.35,
) -> ClinicalSafetyEvaluator:
    """构造使用固定向量命中的临床安全 evaluator。

    :param asset: 临床安全资产。
    :param chunk: 临床安全 chunk。
    :param hit_score: 向量命中分数。
    :param min_score: 向量召回最低分数。
    :return: 返回临床安全 evaluator。
    """
    retriever = ClinicalSafetyRetriever(
        VectorHitClinicalSafetyRepository(asset, chunk, hit_score=hit_score),
        embedding_client=StaticEmbeddingClient(),
        min_score=min_score,
    )
    return ClinicalSafetyEvaluator(retriever, StaticClinicalSafetyPolicyClient())


def _trusted_toxic_semantic(
    *,
    species: ClinicalSafetySpecies = "cat",
    sex: ClinicalSafetySex = "unknown",
    age_group: ClinicalSafetyAgeGroup = "adult",
    exposure_state: ClinicalSafetyExposureState = "confirmed",
    symptom_state: ClinicalSafetySymptomState = "present",
    temporal_state: ClinicalSafetyTemporalState = "current",
    temporal_scope: ClinicalSafetyTemporalScope = "ongoing",
    resolution_state: ClinicalSafetyResolutionState = "ongoing",
    intent_type: ClinicalSafetyIntentType = "toxicity",
    source_text: str = "我家猫误食泰诺后呕吐。",
) -> ClinicalSafetySemanticResult:
    """构造可信毒物语义输入。

    :param species: 物种归一结果。
    :param sex: 性别归一结果。
    :param age_group: 年龄阶段归一结果。
    :param exposure_state: 暴露状态。
    :param symptom_state: 症状状态。
    :param temporal_state: 时间状态。
    :param temporal_scope: 时间范围。
    :param resolution_state: 事件恢复状态。
    :param intent_type: 用户意图类型。
    :param source_text: 原始来源文本摘要。
    :return: 返回临床安全结构化语义结果。
    """
    return ClinicalSafetySemanticResult(
        species=species,
        sex=sex,
        age_group=age_group,
        age_text="3岁",
        exposure_state=exposure_state,
        symptom_state=symptom_state,
        temporal_state=temporal_state,
        temporal_scope=temporal_scope,
        resolution_state=resolution_state,
        temporal_text="现在" if temporal_scope == "ongoing" else "昨天",
        intent_type=intent_type,
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.95,
        strategy="litellm_response_format",
        source_text=source_text,
    )


def test_clinical_safety_evaluator_passes_low_confidence_state_to_policy() -> None:
    """验证低置信度语义只作为降级状态进入策略输入和结果 metadata。

    :return: 无返回值；断言通过表示 evaluator 不再用低置信语义执行 Python 裁决。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)
    semantic = ClinicalSafetySemanticResult(
        confidence=0.31,
        strategy="litellm_response_format_low_confidence",
        fallback_reason="semantic_llm_low_confidence:0.31",
        source_text="我家猫误食泰诺后呕吐。",
    )

    result = asyncio.run(
        evaluator.assess_with_resolution(
            "我家猫误食泰诺后呕吐。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=semantic,
        )
    )

    assert result.signals
    assert result.signals[0].severity == "urgent"
    assert result.fallback_state.semantic.stage == "llm_low_confidence"
    assert result.fallback_state.semantic.degraded is True
    assert result.fallback_state.semantic.strategy == "litellm_response_format_low_confidence"
    assert result.policy_decision["policy_backend"] == "static_test"


def test_clinical_safety_evaluator_uses_policy_to_suppress_denied_exposure() -> None:
    """验证可信否认暴露抑制由注入策略客户端完成。

    :return: 无返回值；断言通过表示 evaluator 只负责组装策略输入。
    """
    asset = _human_drug_asset(symptoms=())
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛")
    evaluator = _evaluator_for(asset, chunk)

    denied_signals = asyncio.run(
        evaluator.assess(
            "家里有泰诺，已经收起来了，没有给它吃。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=_trusted_toxic_semantic(
                exposure_state="denied",
                symptom_state="unknown",
                intent_type="other",
                source_text="家里有泰诺，已经收起来了，没有给它吃。",
            ),
        )
    )
    confirmed_signals = asyncio.run(
        evaluator.assess(
            "我家猫误食了泰诺，已经开始呕吐。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=_trusted_toxic_semantic(source_text="我家猫误食了泰诺，已经开始呕吐。"),
        ),
    )

    assert denied_signals == []
    assert confirmed_signals
    assert confirmed_signals[0].code == "TOXIC_SUBSTANCE"
    assert confirmed_signals[0].severity == "urgent"


def test_clinical_safety_evaluator_keeps_temporal_state_as_policy_input() -> None:
    """验证时间语义由策略输入透出，而非由 evaluator Python 分支降级。

    :return: 无返回值；断言通过表示 evaluator 不再承担时间动作裁决。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)

    result = asyncio.run(
        evaluator.assess_with_resolution(
            "昨天误食泰诺，今天已经完全恢复。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=_trusted_toxic_semantic(
                temporal_state="past",
                temporal_scope="recent_past",
                resolution_state="resolved",
                source_text="昨天误食泰诺，今天已经完全恢复。",
            ),
        )
    )

    assert result.signals
    assert result.signals[0].code == "TOXIC_SUBSTANCE"
    assert result.signals[0].severity == "urgent"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.fallback_state.semantic.degraded is False


def test_clinical_safety_evaluator_keeps_ongoing_toxic_event_urgent() -> None:
    """验证 evaluator 过渡层对正在发生的毒物暴露保持急性升级。

    :return: 无返回值；断言通过表示当前事件裁决断言已归入 evaluator 层。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)

    result = asyncio.run(
        evaluator.assess_with_resolution(
            "现在误食泰诺并正在呕吐。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=_trusted_toxic_semantic(source_text="现在误食泰诺并正在呕吐。"),
        )
    )

    assert result.signals
    assert result.signals[0].code == "TOXIC_SUBSTANCE"
    assert result.signals[0].severity == "urgent"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.fallback_state.semantic.degraded is False


def test_clinical_safety_evaluator_returns_explicit_vector_resolution() -> None:
    """验证 evaluator 过渡层显式暴露向量召回与语义状态。

    :return: 无返回值；断言通过表示召回状态已贯穿 evaluator 输出。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)

    result = asyncio.run(
        evaluator.assess_with_resolution(
            "我家猫误食泰诺后呕吐。",
            context_text="宠物画像: 物种=猫, 年龄=3岁",
            age_text="3岁",
            semantic_result=_trusted_toxic_semantic(source_text="我家猫误食泰诺后呕吐。"),
        )
    )

    assert result.signals
    assert result.fallback_state.retrieval.stage == "vector"
    assert result.fallback_state.retrieval.degraded is False
    assert result.fallback_state.retrieval.retrieval_source == "clinical_safety_pgvector"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.to_metadata()["fallback_state"]["retrieval"]["stage"] == "vector"


def test_clinical_safety_evaluator_uses_vector_thresholds() -> None:
    """验证 evaluator 过渡层只根据向量候选阈值处理非毒物候选。

    :return: 无返回值；断言通过表示词面回退未进入候选裁决路径。
    """
    asset = _danger_pattern_asset()
    chunk = _recognition_chunk(asset, text="发绀；发紫；轻微不适")

    vector_signal = asyncio.run(_evaluator_for(asset, chunk, hit_score=0.68).assess("轻微不适"))
    urgent_vector_signal = asyncio.run(_evaluator_for(asset, chunk, hit_score=0.78).assess("轻微不适"))
    low_score_vector_signal = asyncio.run(
        _evaluator_for(
            asset,
            chunk,
            hit_score=0.28,
            min_score=0.20,
        ).assess("轻微不适")
    )

    assert vector_signal
    assert vector_signal[0].severity == "caution"
    assert urgent_vector_signal
    assert urgent_vector_signal[0].severity == "urgent"
    assert low_score_vector_signal == []


def test_clinical_safety_evaluator_compacts_duplicate_matched_terms() -> None:
    """验证 evaluator 只对策略返回的命中词执行展示去重。

    :return: 无返回值；断言通过表示重复命中词不会导致临床安全裁决链路异常。
    """
    asset = _danger_pattern_asset()
    chunk = _recognition_chunk(asset, text="牙龈发紫并呼吸很快；牙龈发紫；呼吸很快")
    retriever = ClinicalSafetyRetriever(
        VectorHitClinicalSafetyRepository(asset, chunk),
        embedding_client=StaticEmbeddingClient(),
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever, DuplicateSignalClinicalSafetyPolicyClient())

    signals = asyncio.run(
        evaluator.assess(
            "猫现在牙龈发紫，呼吸很快。",
            context_text="宠物画像: 物种=猫, 年龄=5岁",
            age_text="5岁",
        )
    )

    assert len(signals) == 1
    assert signals[0] == SafetySignal(
        code="CYANOSIS_RISK_PATTERN",
        severity="urgent",
        message="命中较高等级测试信号。",
        matched_terms=["牙龈发紫并呼吸很快"],
    )
