"""
文件：tests/test_clinical_safety_retriever.py
作用：验证临床安全召回器优先使用 embedding 与向量仓储，不回退到文本临时路径。
说明：测试不依赖 PostgreSQL，通过内存仓储验证运行时分支选择和候选聚合契约。
"""

from __future__ import annotations

from collections.abc import Sequence

from vet_agent.clinical_safety import (
    ClinicalSafetyAsset,
    ClinicalSafetyChunk,
    ClinicalSafetyChunkHit,
    ClinicalSafetyChunkType,
    ClinicalSafetyRetriever,
)


class StaticEmbeddingClient:
    """提供固定 embedding 的测试客户端。"""

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


class UnavailableEmbeddingClient:
    """提供不可用 embedding 状态的测试客户端。"""

    @property
    def available(self) -> bool:
        """声明测试 embedding 客户端不可用。

        :return: 始终返回 False。
        """
        return False

    def embed(self, text: str) -> list[float]:
        """防止不可用客户端被错误调用。

        :param text: 待向量化的查询文本。
        :return: 本路径不应返回向量。
        :raises AssertionError: 该方法被调用时表示召回器未遵循可用性门禁。
        """
        raise AssertionError(f"不可用 embedding 客户端不应被调用：{text}")


class VectorOnlyClinicalSafetyRepository:
    """提供固定向量命中的内存临床安全仓储。"""

    def __init__(self, asset: ClinicalSafetyAsset, chunk: ClinicalSafetyChunk) -> None:
        """初始化内存测试仓储。

        :param asset: 用于候选聚合的测试资产。
        :param chunk: 用于向量命中的测试 chunk。
        :return: 无返回值。
        """
        self.asset = asset
        self.chunk = chunk
        self.vector_calls = 0

    def assets(self, *, published_only: bool = True) -> list[ClinicalSafetyAsset]:
        """读取测试资产。

        :param published_only: 是否仅读取发布态资产。
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
        :param published_only: 是否仅读取发布态 chunk。
        :return: 返回符合类型的测试 chunk。
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

        :param asset_id: 待读取资产标识。
        :param published_only: 是否仅读取发布态资产。
        :return: 标识匹配时返回测试资产，否则返回 None。
        """
        del published_only
        return self.asset if asset_id == self.asset.asset_id else None

    def chunks_by_asset_id(
        self,
        asset_id: str,
        *,
        published_only: bool = True,
    ) -> list[ClinicalSafetyChunk]:
        """读取测试资产关联的 chunk。

        :param asset_id: 待读取资产标识。
        :param published_only: 是否仅读取发布态 chunk。
        :return: 标识匹配时返回测试 chunk，否则返回空列表。
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
        """返回固定 pgvector 命中，并记录向量调用次数。

        :param query_embedding: 查询 embedding。
        :param chunk_types: 允许参与召回的 chunk 类型。
        :param limit: 返回 chunk 命中数量上限。
        :param min_score: 候选最低相似度分数。
        :return: 返回固定向量命中。
        """
        self.vector_calls += 1
        assert list(query_embedding) == [0.2, 0.8]
        assert self.chunk.chunk_type in chunk_types
        assert limit > 0
        assert min_score > 0
        return [
            ClinicalSafetyChunkHit(
                chunk=self.chunk,
                score=0.91,
                distance=0.09,
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


def test_retriever_prefers_pgvector_hits_when_embedding_is_available() -> None:
    """验证 embedding 可用时优先走向量仓储，不调用文本回退。

    :return: 无返回值；断言通过表示向量检索主路径已生效。
    """
    asset = ClinicalSafetyAsset(
        asset_id="safety_emergency_cyanosis",
        asset_type="emergency_red_flag",
        canonical_name="舌/牙龈发绀发紫",
        category="呼吸循环",
        species_scope=("dog", "cat"),
        sex_scope=(),
        age_scope=(),
        severity="urgent",
        action_class="emergency",
        aliases=("牙龈发紫",),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_emergency_cyanosis.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="舌/牙龈发绀发紫 风险识别",
        embedding_text="牙龈发紫；舌头发青；发绀",
        metadata={},
        review_status="approved",
    )
    repository = VectorOnlyClinicalSafetyRepository(asset, chunk)
    retriever = ClinicalSafetyRetriever(
        repository,
        StaticEmbeddingClient(),
        min_score=0.35,
    )

    candidates = retriever.retrieve("猫牙龈发紫，呼吸很快。")

    assert repository.vector_calls == 1
    assert len(candidates) == 1
    assert candidates[0].asset.resolved_code() == "CYANOSIS_RISK_PATTERN"
    assert candidates[0].score_type == "cosine_similarity"
    assert candidates[0].retrieval_source == "clinical_safety_pgvector"


def test_retriever_returns_empty_when_embedding_is_unavailable() -> None:
    """验证 embedding 不可用时不会回退到文本、文件或资产短语召回。

    :return: 无返回值；断言通过表示候选召回遵循向量主路径 Fail Fast 语义。
    """
    asset = ClinicalSafetyAsset(
        asset_id="safety_emergency_cyanosis",
        asset_type="emergency_red_flag",
        canonical_name="舌/牙龈发绀发紫",
        category="呼吸循环",
        species_scope=("dog", "cat"),
        sex_scope=(),
        age_scope=(),
        severity="urgent",
        action_class="emergency",
        aliases=("牙龈发紫",),
    )
    chunk = ClinicalSafetyChunk(
        chunk_id="safety_emergency_cyanosis.recognition.v1",
        asset_id=asset.asset_id,
        chunk_type="recognition",
        title="舌/牙龈发绀发紫 风险识别",
        embedding_text="牙龈发紫；舌头发青；发绀",
        metadata={},
        review_status="approved",
    )
    repository = VectorOnlyClinicalSafetyRepository(asset, chunk)
    retriever = ClinicalSafetyRetriever(
        repository,
        UnavailableEmbeddingClient(),
        min_score=0.35,
    )

    result = retriever.retrieve_with_resolution("猫牙龈发紫，呼吸很快。")

    assert repository.vector_calls == 0
    assert result.candidates == []
    assert result.state.stage == "none"
    assert result.state.degraded is True
    assert result.state.reasons == (
        "embedding_client_unavailable",
        "clinical_safety_retrieval_empty",
    )
    assert result.state.vector_hit_count == 0
    assert result.state.candidate_count == 0
