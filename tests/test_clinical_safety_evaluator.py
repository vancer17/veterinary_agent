"""
文件：tests/test_clinical_safety_evaluator.py
作用：验证临床安全 evaluator 过渡层对向量候选、可信语义和显式状态的归一行为。
说明：本文件承接裁决过渡层测试；最终动作策略迁移到 OPA 后，应进一步迁入策略门面测试。
"""

from __future__ import annotations

from collections.abc import Sequence

from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyAgeGroup,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    ClinicalSafetyExposureState,
    ClinicalSafetyEvaluator,
    ClinicalSafetyIntentType,
    ClinicalSafetyResolutionState,
    ClinicalSafetyRetriever,
    ClinicalSafetySex,
    ClinicalSafetySemanticResult,
    ClinicalSafetySpecies,
    ClinicalSafetySymptomState,
    ClinicalSafetyTemporalScope,
    ClinicalSafetyTemporalState,
)


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
    return ClinicalSafetyEvaluator(retriever)


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


def test_clinical_safety_evaluator_treats_low_confidence_semantic_as_conservative() -> None:
    """验证低置信度语义不会把 evaluator 过渡层推向更激进升级。

    :return: 无返回值；断言通过表示低置信语义只作为显式状态进入结果。
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

    result = evaluator.assess_with_resolution(
        "我家猫误食泰诺后呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=semantic,
    )

    assert result.signals
    assert result.signals[0].severity != "urgent"
    assert result.signals[0].severity == "caution"
    assert result.fallback_state.semantic.stage == "llm_low_confidence"
    assert result.fallback_state.semantic.degraded is True
    assert result.fallback_state.semantic.strategy == "litellm_response_format_low_confidence"


def test_clinical_safety_evaluator_uses_trusted_denied_exposure_to_suppress_signal() -> None:
    """验证 evaluator 过渡层只使用可信否认暴露语义压制候选信号。

    :return: 无返回值；断言通过表示否认暴露断言已归入 evaluator 层。
    """
    asset = _human_drug_asset(symptoms=())
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛")
    evaluator = _evaluator_for(asset, chunk)

    denied_signals = evaluator.assess(
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
    confirmed_signals = evaluator.assess(
        "我家猫误食了泰诺，已经开始呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=_trusted_toxic_semantic(source_text="我家猫误食了泰诺，已经开始呕吐。"),
    )

    assert denied_signals == []
    assert confirmed_signals
    assert confirmed_signals[0].code == "TOXIC_SUBSTANCE"
    assert confirmed_signals[0].severity == "urgent"


def test_clinical_safety_evaluator_downgrades_resolved_recent_past_toxic_event() -> None:
    """验证 evaluator 过渡层对已恢复近期既往毒物事件执行保守降级。

    :return: 无返回值；断言通过表示时间裁决断言已归入 evaluator 层。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)

    result = evaluator.assess_with_resolution(
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

    assert result.signals
    assert result.signals[0].code == "TOXIC_SUBSTANCE"
    assert result.signals[0].severity == "caution"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.fallback_state.semantic.degraded is False


def test_clinical_safety_evaluator_keeps_ongoing_toxic_event_urgent() -> None:
    """验证 evaluator 过渡层对正在发生的毒物暴露保持急性升级。

    :return: 无返回值；断言通过表示当前事件裁决断言已归入 evaluator 层。
    """
    asset = _human_drug_asset()
    chunk = _recognition_chunk(asset, text="泰诺；对乙酰氨基酚；扑热息痛；呕吐")
    evaluator = _evaluator_for(asset, chunk)

    result = evaluator.assess_with_resolution(
        "现在误食泰诺并正在呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=_trusted_toxic_semantic(source_text="现在误食泰诺并正在呕吐。"),
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

    result = evaluator.assess_with_resolution(
        "我家猫误食泰诺后呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=_trusted_toxic_semantic(source_text="我家猫误食泰诺后呕吐。"),
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

    vector_signal = _evaluator_for(asset, chunk, hit_score=0.68).assess("轻微不适")
    urgent_vector_signal = _evaluator_for(asset, chunk, hit_score=0.78).assess("轻微不适")
    low_score_vector_signal = _evaluator_for(
        asset,
        chunk,
        hit_score=0.28,
        min_score=0.20,
    ).assess("轻微不适")

    assert vector_signal
    assert vector_signal[0].severity == "caution"
    assert urgent_vector_signal
    assert urgent_vector_signal[0].severity == "urgent"
    assert low_score_vector_signal == []
