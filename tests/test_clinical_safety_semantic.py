"""
文件：tests/test_clinical_safety_semantic.py
作用：验证临床安全结构化语义抽取与基于语义的裁决行为。
说明：测试通过内存仓储和伪造 LLM 客户端验证最终形态链路，不依赖 PostgreSQL。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from vet_agent import Settings
from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyCandidate,
    ClinicalSafetyEvaluator,
    ClinicalSafetyRetriever,
    ClinicalSafetySemanticExtractorAgent,
    ClinicalSafetySemanticResult,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
)


class FakeQwenClient:
    """提供固定返回值的测试客户端。"""

    def __init__(self, raw_response: str) -> None:
        """初始化测试客户端。

        :param raw_response: 模拟模型返回文本。
        :return: 无返回值。
        """
        self.raw_response = raw_response

    @property
    def available(self) -> bool:
        """声明测试客户端始终可用。

        :return: 始终返回 True。
        """
        return True

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """返回预设模型输出。

        :param messages: 传入的消息列表。
        :param model: 模型名称。
        :param temperature: 采样温度。
        :return: 返回固定响应。
        """
        del messages, model, temperature
        return self.raw_response


class StaticClinicalSafetyRepository:
    """提供固定临床安全资产和 chunk 的内存仓储。"""

    def __init__(self, asset: ClinicalSafetyAsset, chunk: ClinicalSafetyChunk) -> None:
        """初始化测试仓储。

        :param asset: 用于召回和裁决的测试资产。
        :param chunk: 与资产关联的测试 chunk。
        :return: 无返回值。
        """
        self.asset = asset
        self.chunk = chunk

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
        """返回空向量命中，强制走文本回退路径。

        :param query_embedding: 查询向量。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回空列表。
        """
        del query_embedding, chunk_types, limit, min_score
        return []

    def retrieve_text_chunk_hits(
        self,
        query: str,
        *,
        chunk_types: tuple[ClinicalSafetyChunkType, ...],
        limit: int,
    ) -> list[ClinicalSafetyChunkHit]:
        """返回空文本命中，依赖资产结构化短语回退。

        :param query: 用户查询文本。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :return: 返回空列表。
        """
        del query, chunk_types, limit
        return []

    def is_ready(self) -> bool:
        """声明测试仓储始终可用。

        :return: 始终返回 True。
        """
        return True


def test_clinical_safety_semantic_extractor_parses_llm_json() -> None:
    """验证 LLM 结构化语义输出可以被稳定解析。

    :return: 无返回值；断言通过表示结构化语义抽取可用。
    """
    settings = Settings()
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "sex": "female",
              "age_group": "senior",
              "age_text": "8岁",
              "exposure_state": "confirmed",
              "symptom_state": "present",
              "temporal_state": "current",
              "intent_type": "toxicity",
              "high_risk_terms": ["泰诺", "呕吐"],
              "negated_terms": [],
              "confidence": 0.92,
              "rationale": "用户明确描述猫误食泰诺并出现呕吐。"
            }
            """
        ),
        settings,
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家猫误食泰诺后开始呕吐，想先确认要不要急诊。",
            pet_context_summary="宠物画像: 物种=猫, 年龄=8岁, 性别=母。",
            model="qwen-plus",
        )
    )

    assert result.strategy == "llm_semantic_extractor"
    assert result.species == "cat"
    assert result.sex == "female"
    assert result.age_group == "senior"
    assert result.exposure_state == "confirmed"
    assert result.intent_type == "toxicity"
    assert "泰诺" in result.high_risk_terms


def test_clinical_safety_semantic_low_confidence_falls_back_to_rule_result() -> None:
    """验证低置信度语义结果会退回为保守规则结果。

    :return: 无返回值；断言通过表示低置信度 LLM 输出不会直接进入裁决面。
    """
    settings = Settings(semantic_extraction_min_confidence=0.9)
    extractor = ClinicalSafetySemanticExtractorAgent(
        FakeQwenClient(
            """
            {
              "species": "cat",
              "sex": "female",
              "age_group": "senior",
              "age_text": "8岁",
              "exposure_state": "confirmed",
              "symptom_state": "present",
              "temporal_state": "current",
              "intent_type": "toxicity",
              "high_risk_terms": ["泰诺", "呕吐"],
              "negated_terms": [],
              "confidence": 0.31,
              "rationale": "看起来像误食泰诺并呕吐。"
            }
            """
        ),
        settings,
    )

    result = asyncio.run(
        extractor.extract(
            user_text="我家狗今天有点没精神，想先确认一下。",
            pet_context_summary="宠物画像: 物种=狗, 年龄=8岁, 性别=公。",
            model="qwen-plus",
        )
    )

    assert result.is_low_confidence()
    assert result.strategy == "llm_semantic_extractor_low_confidence"
    assert result.species == "dog"
    assert result.sex == "male"
    assert result.exposure_state != "confirmed"
    assert result.intent_type != "toxicity"


def test_clinical_safety_evaluator_treats_low_confidence_semantic_as_conservative() -> None:
    """验证低置信度语义不会把裁决推向更激进的升级。

    :return: 无返回值；断言通过表示低置信度语义只会保守处理。
    """
    asset = ClinicalSafetyAsset(
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
        symptoms=("呕吐",),
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛", "呕吐"),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_human_drug_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="对乙酰氨基酚 风险识别",
        embedding_text="泰诺；对乙酰氨基酚；扑热息痛；呕吐",
        metadata={},
        review_status="approved",
    )
    retriever = ClinicalSafetyRetriever(
        StaticClinicalSafetyRepository(asset, chunk),
        embedding_client=None,
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever)
    semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="confirmed",
        symptom_state="present",
        temporal_state="current",
        intent_type="toxicity",
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.31,
        strategy="llm_semantic_extractor_low_confidence",
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


def test_clinical_safety_evaluator_uses_semantic_exposure_state_to_suppress_denied_mentions() -> None:
    """验证结构化语义可以压掉“仅提到毒物但明确否认暴露”的误触发。

    :return: 无返回值；断言通过表示结构化语义已参与裁决。
    """
    asset = ClinicalSafetyAsset(
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
        symptoms=(),
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛"),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_human_drug_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="对乙酰氨基酚 风险识别",
        embedding_text="泰诺；对乙酰氨基酚；扑热息痛",
        metadata={},
        review_status="approved",
    )
    repository = StaticClinicalSafetyRepository(asset, chunk)
    retriever = ClinicalSafetyRetriever(repository, embedding_client=None, min_score=0.35)
    evaluator = ClinicalSafetyEvaluator(retriever)

    denied_semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="denied",
        symptom_state="unknown",
        temporal_state="current",
        intent_type="other",
        high_risk_terms=("泰诺",),
        negated_terms=(),
        confidence=0.88,
        strategy="llm_semantic_extractor",
        source_text="家里有泰诺，已经收起来了，没有给它吃。",
    )
    denied_signals = evaluator.assess(
        "家里有泰诺，已经收起来了，没有给它吃。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=denied_semantic,
    )

    confirmed_semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="confirmed",
        symptom_state="present",
        temporal_state="current",
        intent_type="toxicity",
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.95,
        strategy="llm_semantic_extractor",
        source_text="我家猫误食了泰诺，已经开始呕吐。",
    )
    confirmed_signals = evaluator.assess(
        "我家猫误食了泰诺，已经开始呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=confirmed_semantic,
    )

    assert denied_signals == []
    assert confirmed_signals
    assert confirmed_signals[0].code == "TOXIC_SUBSTANCE"
    assert confirmed_signals[0].severity == "urgent"


def test_clinical_safety_evaluator_downgrades_resolved_recent_past_toxic_event() -> None:
    """验证已恢复的近期既往毒物事件不会继续按当前急症升级。

    :return: 无返回值；断言通过表示时间语义已参与裁决降级。
    """
    asset = ClinicalSafetyAsset(
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
        symptoms=("呕吐",),
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛", "呕吐"),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_human_drug_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="对乙酰氨基酚 风险识别",
        embedding_text="泰诺；对乙酰氨基酚；扑热息痛；呕吐",
        metadata={},
        review_status="approved",
    )
    retriever = ClinicalSafetyRetriever(
        StaticClinicalSafetyRepository(asset, chunk),
        embedding_client=None,
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever)
    semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="confirmed",
        symptom_state="present",
        temporal_state="past",
        temporal_scope="recent_past",
        resolution_state="resolved",
        temporal_text="昨天",
        intent_type="toxicity",
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.95,
        strategy="llm_semantic_extractor",
        source_text="昨天误食泰诺，今天已经完全恢复。",
    )

    result = evaluator.assess_with_resolution(
        "昨天误食泰诺，今天已经完全恢复。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=semantic,
    )

    assert result.signals
    assert result.signals[0].code == "TOXIC_SUBSTANCE"
    assert result.signals[0].severity == "caution"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.fallback_state.semantic.degraded is False


def test_clinical_safety_evaluator_keeps_ongoing_toxic_event_urgent() -> None:
    """验证正在发生的毒物暴露仍保持急性升级。

    :return: 无返回值；断言通过表示当前事件不会被时间语义错误降级。
    """
    asset = ClinicalSafetyAsset(
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
        symptoms=("呕吐",),
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛", "呕吐"),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_human_drug_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="对乙酰氨基酚 风险识别",
        embedding_text="泰诺；对乙酰氨基酚；扑热息痛；呕吐",
        metadata={},
        review_status="approved",
    )
    retriever = ClinicalSafetyRetriever(
        StaticClinicalSafetyRepository(asset, chunk),
        embedding_client=None,
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever)
    semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="confirmed",
        symptom_state="present",
        temporal_state="current",
        temporal_scope="ongoing",
        resolution_state="ongoing",
        temporal_text="现在",
        intent_type="toxicity",
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.95,
        strategy="llm_semantic_extractor",
        source_text="现在误食泰诺并正在呕吐。",
    )

    result = evaluator.assess_with_resolution(
        "现在误食泰诺并正在呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=semantic,
    )

    assert result.signals
    assert result.signals[0].code == "TOXIC_SUBSTANCE"
    assert result.signals[0].severity == "urgent"
    assert result.fallback_state.semantic.stage == "llm"
    assert result.fallback_state.semantic.degraded is False


def test_clinical_safety_evaluator_returns_explicit_fallback_resolution() -> None:
    """验证临床安全裁决结果会显式暴露召回与语义回退状态。

    :return: 无返回值；断言通过表示显式回退状态已贯穿召回和裁决层。
    """
    asset = ClinicalSafetyAsset(
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
        symptoms=("呕吐",),
        recognition_phrases=("泰诺", "对乙酰氨基酚", "扑热息痛", "呕吐"),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_human_drug_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="对乙酰氨基酚 风险识别",
        embedding_text="泰诺；对乙酰氨基酚；扑热息痛；呕吐",
        metadata={},
        review_status="approved",
    )
    retriever = ClinicalSafetyRetriever(
        StaticClinicalSafetyRepository(asset, chunk),
        embedding_client=None,
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever)
    semantic = ClinicalSafetySemanticResult(
        species="cat",
        sex="unknown",
        age_group="adult",
        age_text="3岁",
        exposure_state="confirmed",
        symptom_state="present",
        temporal_state="current",
        intent_type="toxicity",
        high_risk_terms=("泰诺", "呕吐"),
        negated_terms=(),
        confidence=0.95,
        strategy="llm_semantic_extractor",
        source_text="我家猫误食泰诺后呕吐。",
    )

    result = evaluator.assess_with_resolution(
        "我家猫误食泰诺后呕吐。",
        context_text="宠物画像: 物种=猫, 年龄=3岁",
        age_text="3岁",
        semantic_result=semantic,
    )

    assert result.signals
    assert result.fallback_state.retrieval.stage == "asset_fallback"
    assert result.fallback_state.retrieval.degraded is True
    assert "embedding_client_unavailable" in result.fallback_state.retrieval.reasons
    assert result.fallback_state.semantic.stage == "llm"
    assert result.to_metadata()["fallback_state"]["retrieval"]["stage"] == "asset_fallback"


def test_clinical_safety_evaluator_separates_vector_and_lexical_thresholds() -> None:
    """验证向量裁决阈值与词面裁决阈值彼此独立。

    :return: 无返回值；断言通过表示不同证据类型不会共用同一阈值语义。
    """
    asset = ClinicalSafetyAsset(
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
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_danger_pattern_001.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="发绀发紫 风险识别",
        embedding_text="发绀；发紫；轻微不适",
        metadata={},
        review_status="approved",
    )
    retriever = ClinicalSafetyRetriever(
        StaticClinicalSafetyRepository(asset, chunk),
        embedding_client=None,
        min_score=0.35,
    )
    evaluator = ClinicalSafetyEvaluator(retriever)

    vector_candidate = ClinicalSafetyCandidate(
        asset=asset,
        score=0.68,
        chunk_hits=(
            ClinicalSafetyChunkHit(
                chunk=chunk,
                score=0.68,
                distance=0.32,
                score_type="cosine_similarity",
                retrieval_source="clinical_safety_pgvector",
                matched_terms=(),
            ),
        ),
        score_type="cosine_similarity",
        retrieval_source="clinical_safety_pgvector",
    )
    vector_signal = evaluator._candidate_signal(
        vector_candidate,
        normalized_query="",
        age_text="3岁",
        semantic_result=None,
        allow_semantic_escalation=True,
    )
    urgent_vector_candidate = ClinicalSafetyCandidate(
        asset=asset,
        score=0.78,
        chunk_hits=(
            ClinicalSafetyChunkHit(
                chunk=chunk,
                score=0.78,
                distance=0.22,
                score_type="cosine_similarity",
                retrieval_source="clinical_safety_pgvector",
                matched_terms=(),
            ),
        ),
        score_type="cosine_similarity",
        retrieval_source="clinical_safety_pgvector",
    )
    urgent_vector_signal = evaluator._candidate_signal(
        urgent_vector_candidate,
        normalized_query="",
        age_text="3岁",
        semantic_result=None,
        allow_semantic_escalation=True,
    )
    lexical_candidate = ClinicalSafetyCandidate(
        asset=asset,
        score=0.28,
        chunk_hits=(
            ClinicalSafetyChunkHit(
                chunk=chunk,
                score=0.28,
                distance=0.0,
                score_type="lexical_overlap",
                retrieval_source="clinical_safety_asset_fallback",
                matched_terms=("轻微不适",),
            ),
        ),
        score_type="lexical_overlap",
        retrieval_source="clinical_safety_asset_fallback",
    )
    lexical_signal = evaluator._candidate_signal(
        lexical_candidate,
        normalized_query="轻微不适",
        age_text="3岁",
        semantic_result=None,
        allow_semantic_escalation=True,
    )

    assert vector_signal is not None
    assert vector_signal.severity == "caution"
    assert urgent_vector_signal is not None
    assert urgent_vector_signal.severity == "urgent"
    assert lexical_signal is None
