"""
文件：tests/integration/test_memory_read_api_external.py
作用：通过真实 PostgreSQL、Mem0、LiteLLM 与 OPA 验证记忆读取 API 纵向链路。
范围：覆盖 Agent API 中的结构化记忆读取、当前 session 回合窗口、长期事实、
      宠物 episode 与 Mem0 语义投影召回，不使用本地内存模型或测试替身。
说明：本测试仅在显式开启 RUN_MEMORY_READ_API_EXTERNAL_TEST 时执行，供
      try-run 或人工发布前加严验证使用；测试数据使用唯一前缀并在结束后清理。
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ingress import create_app, set_orchestrator
from vet_agent import Container, Settings, VetAgentIngressOrchestrator, set_container
from vet_agent.db import ClinicalSafetyAssetModel, ClinicalSafetyChunkModel, make_session_factory
from vet_agent.observability import AgentPathNode


EXTERNAL_MEMORY_READ_FLAG = "RUN_MEMORY_READ_API_EXTERNAL_TEST"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 60.0
DEFAULT_DATABASE_RETRY_ATTEMPTS = 3
DEFAULT_DATABASE_RETRY_DELAY_SECONDS = 2.0
DEFAULT_MEM0_RETRY_ATTEMPTS = 6
DEFAULT_MEM0_RETRY_DELAY_SECONDS = 2.0
EXPECTED_EMBEDDING_DIMENSION = 1024
SUPPORTED_ALEMBIC_VERSIONS = {
    "0011_clinical_safety_vector_comments",
    "0012_clinical_safety_publish_contract",
    "0013_memory_read_comments",
    "0018_rag_retrieval_misses",
}

_T = TypeVar("_T")


@pytest.fixture
def external_env() -> dict[str, str]:
    """读取记忆读取外部 API 集成测试所需配置。

    :return: 返回外部依赖配置字典。
    """
    if not _enabled(EXTERNAL_MEMORY_READ_FLAG):
        pytest.skip(f"未开启 {EXTERNAL_MEMORY_READ_FLAG}，跳过记忆读取外部 API 集成测试。")
    required = {
        "DATABASE_URL": os.getenv("EXTERNAL_API_TEST_DATABASE_URL") or os.getenv("DATABASE_URL"),
        "LITELLM_BASE_URL": os.getenv("EXTERNAL_API_TEST_LITELLM_BASE_URL") or os.getenv("LITELLM_BASE_URL"),
        "LITELLM_API_KEY": os.getenv("EXTERNAL_API_TEST_LITELLM_API_KEY")
        or os.getenv("LITELLM_API_KEY")
        or os.getenv("LITELLM_MASTER_KEY"),
        "INPUT_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "CLINICAL_SAFETY_OPA_BASE_URL": os.getenv("EXTERNAL_API_TEST_OPA_BASE_URL")
        or os.getenv("CLINICAL_SAFETY_OPA_BASE_URL")
        or os.getenv("INPUT_SAFETY_OPA_BASE_URL"),
        "MEM0_BASE_URL": os.getenv("EXTERNAL_API_TEST_MEM0_BASE_URL") or os.getenv("MEM0_BASE_URL"),
        "MEM0_API_KEY": os.getenv("EXTERNAL_API_TEST_MEM0_API_KEY") or os.getenv("MEM0_API_KEY"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        pytest.fail(
            f"{EXTERNAL_MEMORY_READ_FLAG}=true 时缺少记忆读取外部依赖配置：{', '.join(missing)}。"
        )
    optional = {
        "QWEN_MODEL": os.getenv("EXTERNAL_API_TEST_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-plus")),
        "QWEN_EMBEDDING_MODEL": os.getenv(
            "EXTERNAL_API_TEST_QWEN_EMBEDDING_MODEL",
            os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        ),
    }
    return {**required, **optional}


@pytest.fixture
def external_prefix() -> str:
    """构造本轮记忆读取测试唯一数据前缀。

    :return: 返回测试数据前缀。
    """
    return f"memory_it_{uuid4().hex[:12]}"


@pytest.fixture
def external_database(external_env: dict[str, str], external_prefix: str) -> Iterator[str]:
    """准备记忆读取 API 测试所需的最小数据库基线。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回数据库连接串。
    """
    database_url = external_env["DATABASE_URL"]

    def cleanup_database() -> None:
        """清理记忆读取外部 API 测试数据。

        :return: 无返回值。
        """
        _cleanup_database_prefix(database_url, external_prefix)

    def assert_schema_ready() -> None:
        """校验外部数据库迁移版本和基础输入安全候选可用性。

        :return: 无返回值。
        """
        _assert_database_schema_ready(database_url)
        _assert_input_safety_baseline_ready(database_url)

    def prepare_baseline() -> None:
        """写入容器就绪检查所需的最小临床安全资产和向量 chunk。

        :return: 无返回值。
        """
        _prepare_clinical_safety_baseline(database_url, external_env, external_prefix)

    _with_database_retry(cleanup_database, action="清理记忆读取测试前缀数据")
    _with_database_retry(assert_schema_ready, action="校验记忆读取数据库基线")
    _with_database_retry(prepare_baseline, action="写入记忆读取测试临床安全基线")
    try:
        yield database_url
    finally:
        _with_database_retry(cleanup_database, action="清理记忆读取测试前缀数据")


@pytest.fixture
def clean_external_mem0(external_env: dict[str, str], external_prefix: str) -> Iterator[tuple[str, str, str]]:
    """清理本轮记忆读取测试使用的 Mem0 语义投影数据。

    :param external_env: 外部依赖配置。
    :param external_prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    user_id, pet_id, session_id = _scope_ids(external_prefix)
    _cleanup_mem0_scope(external_env, user_id=user_id, pet_id=pet_id)
    try:
        yield user_id, pet_id, session_id
    finally:
        _cleanup_mem0_scope(external_env, user_id=user_id, pet_id=pet_id)


@pytest.fixture
def external_client(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
    external_database: str,
) -> Iterator[TestClient]:
    """构造接入真实外部依赖的本地 API 测试客户端。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 返回 FastAPI 测试客户端。
    """
    del external_database
    with _configured_environment(monkeypatch, external_env):
        container = Container(Settings.from_env())
        set_container(container)
        set_orchestrator(VetAgentIngressOrchestrator(container))
        try:
            with TestClient(create_app()) as client:
                yield client
        finally:
            set_orchestrator(None)
            set_container(None)


@pytest.mark.integration
def test_memory_read_external_dependencies_are_reachable(
    external_env: dict[str, str],
    external_database: str,
) -> None:
    """验证记忆读取 API 测试所需真实外部依赖可达。

    :param external_env: 外部依赖配置。
    :param external_database: 已校验的数据库连接串。
    :return: 无返回值；断言通过表示外部依赖具备执行条件。
    """
    del external_database
    with httpx.Client(timeout=_timeout()) as client:
        lite_llm = client.get(
            f"{external_env['LITELLM_BASE_URL'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {external_env['LITELLM_API_KEY']}"},
        )
        lite_llm.raise_for_status()
        model_ids = {item.get("id") for item in lite_llm.json().get("data", [])}
        assert external_env["QWEN_MODEL"] in model_ids
        assert external_env["QWEN_EMBEDDING_MODEL"] in model_ids

        vector = _embedding_vector(external_env, "记忆读取真实服务集成测试。", client=client)
        assert len(vector) == EXPECTED_EMBEDDING_DIMENSION

        mem0_config = client.get(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/configure",
            headers=_mem0_headers(external_env),
        )
        mem0_config.raise_for_status()

        mem0_search = client.post(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/search",
            headers=_mem0_headers(external_env),
            json={
                "query": "记忆读取依赖探测",
                "filters": {"user_id": f"probe_{uuid4().hex}", "run_id": f"probe_{uuid4().hex}"},
                "top_k": 1,
            },
        )
        mem0_search.raise_for_status()

        clinical_opa = client.post(
            _opa_data_url(
                external_env["CLINICAL_SAFETY_OPA_BASE_URL"],
                "vet_agent.clinical_safety",
                "decision",
            ),
            json={"input": _empty_clinical_safety_policy_input()},
        )
        clinical_opa.raise_for_status()
        assert clinical_opa.json()["result"]["action"] == "allow"


@pytest.mark.integration
def test_memory_read_api_uses_real_postgres_and_mem0_projection(
    external_client: TestClient,
    external_env: dict[str, str],
    clean_external_mem0: tuple[str, str, str],
    external_prefix: str,
) -> None:
    """验证 Agent API 回合读取真实 PostgreSQL 记忆与 Mem0 语义投影。

    :param external_client: 接入真实外部依赖的 API 测试客户端。
    :param external_env: 外部依赖配置。
    :param clean_external_mem0: 已清理的 Mem0 测试范围。
    :param external_prefix: 测试数据前缀。
    :return: 无返回值；断言通过表示记忆读取迁移后的真实主路径可用。
    """
    user_id, pet_id, session_id = clean_external_mem0
    marker = f"{external_prefix}_soft_stool_memory_marker"
    profile = _profile()

    _seed_mem0_memory(
        external_env,
        user_id=user_id,
        pet_id=pet_id,
        session_id=session_id,
        marker=marker,
    )
    _wait_for_mem0_recollection(external_env, user_id=user_id, pet_id=pet_id, marker=marker)

    first_response = external_client.post(
        "/agent/turns",
        json=_payload(
            f"请结合 {marker}，豆豆今天有一点软便，但精神食欲正常，没有呕吐，先给我观察建议。",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
            idempotency_key=f"{external_prefix}_idem_first",
        ),
    )
    assert first_response.status_code == 200, first_response.text
    first_data = first_response.json()
    _assert_main_chain_response(first_data)
    _assert_memory_read_metadata(
        first_data["metadata"],
        min_session_turns=0,
        min_facts=0,
        min_pet_episodes=0,
    )

    fact_response = external_client.put(
        "/memories/facts",
        json={
            "user_id": user_id,
            "session_id": session_id,
            "pet_id": pet_id,
            "fact_type": "medical",
            "fact_key": "digestive_observation",
            "fact_value": f"{marker}：轻微软便期间精神食欲正常，无呕吐。",
            "confidence": 1.0,
            "source_text": "记忆读取外部 API 集成测试人工事实。",
            "metadata": {"source": "memory_read_external_api_test", "marker": marker},
        },
    )
    assert fact_response.status_code == 200, fact_response.text

    second_response = external_client.post(
        "/agent/turns",
        json=_payload(
            f"{marker} 今天软便次数减少，饮水正常，仍然没有吐，接下来要怎么观察？",
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
            idempotency_key=f"{external_prefix}_idem_second",
        ),
    )
    assert second_response.status_code == 200, second_response.text
    second_data = second_response.json()
    _assert_main_chain_response(second_data)
    _assert_memory_read_metadata(
        second_data["metadata"],
        min_session_turns=1,
        min_facts=1,
        min_pet_episodes=1,
    )

    snapshot_response = external_client.get(
        "/memories",
        params={"user_id": user_id, "session_id": session_id, "pet_id": pet_id},
    )
    assert snapshot_response.status_code == 200, snapshot_response.text
    snapshot = snapshot_response.json()
    assert snapshot["pet"]["turns"]
    assert any(item["fact_key"] == "digestive_observation" for item in snapshot["pet"]["facts"])
    assert snapshot["session"]["turns"]


def _assert_main_chain_response(data: dict[str, Any]) -> None:
    """验证 API 响应已经进入非安全阻断的 Agent 主链路。

    :param data: API 响应字典。
    :return: 无返回值。
    """
    assert data["status"] in {"requires_followup", "completed"}
    metadata = data["metadata"]
    assert AgentPathNode.MEMORY_AGENT.value in metadata["multi_agent_path"]
    input_safety = metadata["input_safety_decision"]
    assert input_safety["allow"] is True
    assert input_safety["policy_backend"] == "opa"
    clinical_policy = metadata["clinical_safety_resolution"]["policy_decision"]
    assert clinical_policy["allow"] is True
    assert clinical_policy["policy_backend"] == "opa"


def _assert_memory_read_metadata(
    metadata: dict[str, Any],
    *,
    min_session_turns: int,
    min_facts: int,
    min_pet_episodes: int,
) -> None:
    """验证记忆读取 metadata 体现真实 PostgreSQL 与 Mem0 主路径。

    :param metadata: API 响应 metadata 字典。
    :param min_session_turns: 期望读取到的最小当前 session 回合数量。
    :param min_facts: 期望读取到的最小长期事实数量。
    :param min_pet_episodes: 期望读取到的最小宠物 episode 数量。
    :return: 无返回值。
    """
    memory_read = metadata["memory_read"]
    audit = memory_read["audit"]
    assert audit["purpose"] == "agent_turn"
    assert audit["source"] == "postgres_authoritative_memory_with_mem0_projection"
    assert audit["facts_count"] >= min_facts
    assert audit["session_turns_count"] >= min_session_turns
    assert audit["pet_episodes_count"] >= min_pet_episodes
    assert audit["semantic_status"] == "queried"
    assert audit["semantic_recollections_count"] >= 1
    assert audit["degraded"] is False
    assert audit["details"]["mem0_enabled"] is True
    assert memory_read["has_consultation_state"] in {True, False}

    memory_context = metadata["memory_context"]
    assert memory_context["prompt_chars"] > 0
    assert memory_context["audit"]["audit"]["semantic_status"] == "queried"


@contextmanager
def _configured_environment(
    monkeypatch: pytest.MonkeyPatch,
    external_env: dict[str, str],
) -> Iterator[None]:
    """向当前进程注入记忆读取外部 API 集成测试配置。

    :param monkeypatch: pytest 环境变量替换工具。
    :param external_env: 外部依赖配置。
    :return: 返回上下文管理器迭代器。
    """
    values = {
        "DATABASE_URL": external_env["DATABASE_URL"],
        "LITELLM_BASE_URL": external_env["LITELLM_BASE_URL"].rstrip("/"),
        "LITELLM_API_KEY": external_env["LITELLM_API_KEY"],
        "QWEN_MODEL": external_env["QWEN_MODEL"],
        "QWEN_EMBEDDING_MODEL": external_env["QWEN_EMBEDDING_MODEL"],
        "ENABLE_RAG_EMBEDDINGS": "false",
        "CLINICAL_SAFETY_VECTOR_MIN_SCORE": "0.999",
        "ENABLE_MEM0": "true",
        "MEM0_BASE_URL": external_env["MEM0_BASE_URL"].rstrip("/"),
        "MEM0_API_KEY": external_env["MEM0_API_KEY"],
        "MEMORY_READ_ALLOW_SEMANTIC_DEGRADED": "false",
        "MEMORY_READ_SEMANTIC_LIMIT": "5",
        "ENABLE_INPUT_SAFETY": "true",
        "INPUT_SAFETY_POLICY_BACKEND": "opa",
        "INPUT_SAFETY_OPA_BASE_URL": external_env["INPUT_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "CLINICAL_SAFETY_OPA_BASE_URL": external_env["CLINICAL_SAFETY_OPA_BASE_URL"].rstrip("/"),
        "ENABLE_INPUT_SAFETY_GUARDRAILS": "false",
        "ENABLE_LLM_SEMANTIC_EXTRACTION": "true",
        "ENABLE_LLM_TASK_SPLITTER": "false",
        "ENABLE_MEMORY_EXTRACTION": "false",
        "QWEN_MAX_RETRIES": "0",
        "QWEN_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "20",
        "LITELLM_TIMEOUT_SECONDS": str(_timeout()),
        "REQUIRE_API_AUTH": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield


def _payload(
    text_value: str,
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """构造记忆读取 API 集成测试请求载荷。

    :param text_value: 用户输入文本。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :param idempotency_key: 本轮幂等键。
    :return: 返回 API 请求载荷。
    """
    return {
        "model": "qwen-plus",
        "input": text_value,
        "stream": False,
        "scope_assertion": _scope_assertion(
            user_id=user_id,
            pet_id=pet_id,
            session_id=session_id,
            profile=profile,
        ),
        "vet_context": {"pet_info": profile},
        "attachments": [],
        "turn_options": {
            "idempotency_key": idempotency_key,
            "max_followup_questions": 2,
        },
    }


def _scope_assertion(
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """构造记忆读取 API 集成测试使用的可信范围声明。

    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param profile: 可信宠物画像。
    :return: 返回范围声明字典。
    """
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "v1",
        "issuer": "memory-read-api-integration-test",
        "issued_at": now,
        "user_id": user_id,
        "pet_id": pet_id,
        "session_id": session_id,
        "authorization": {
            "ownership_verified": True,
            "pet_active": True,
            "pet_status": "active",
            "pet_deleted": False,
        },
        "profile": profile,
        "source": {
            "system": "memory-read-api-integration-test",
            "database": "vet_agent",
            "table": "master_pet_info",
            "record_id": pet_id,
            "record_updated_at": now,
            "data_source": "integration_test",
        },
        "session_policy": {"binding_mode": "single_user_pet_per_session"},
    }


def _profile() -> dict[str, Any]:
    """构造记忆读取 API 集成测试宠物画像。

    :return: 返回可信宠物画像字典。
    """
    return {
        "species": "猫",
        "name": "豆豆",
        "age": "3岁",
        "sex": "female",
        "weight_kg": 4.1,
        "breed": "中华田园猫",
    }


def _assert_database_schema_ready(database_url: str) -> None:
    """确认外部数据库迁移版本满足记忆读取测试要求。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        version = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if version not in SUPPORTED_ALEMBIC_VERSIONS:
            pytest.fail(
                "外部数据库未迁移到当前记忆读取 API 测试所需版本。"
                f" 当前版本：{version!r}，期望版本之一：{sorted(SUPPORTED_ALEMBIC_VERSIONS)!r}。"
            )


def _assert_input_safety_baseline_ready(database_url: str) -> None:
    """确认基础输入安全候选定义表具备运行时最小基线。

    :param database_url: 数据库连接串。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        count = int(
            session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM input_safety_candidate_definitions
                    WHERE enabled IS TRUE
                    """
                )
            ).scalar_one()
            or 0
        )
    if count <= 0:
        pytest.fail("外部数据库缺少基础输入安全候选定义，无法执行真实 API 记忆读取测试。")


def _prepare_clinical_safety_baseline(
    database_url: str,
    external_env: dict[str, str],
    prefix: str,
) -> None:
    """写入容器就绪检查所需的最小临床安全资产与向量 chunk。

    :param database_url: 数据库连接串。
    :param external_env: 外部依赖配置。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    now = datetime.now(UTC)
    safety_asset_id = _safety_asset_id(prefix)
    safety_chunk_id = _safety_chunk_id(prefix)
    embedding_text = _clinical_safety_embedding_text(prefix)
    embedding = _embedding_vector(external_env, embedding_text)
    if len(embedding) != EXPECTED_EMBEDDING_DIMENSION:
        pytest.fail(f"外部 embedding 维度不符合 clinical_safety_chunks.embedding 要求：{len(embedding)}。")

    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        asset = session.get(ClinicalSafetyAssetModel, safety_asset_id)
        if asset is None:
            asset = ClinicalSafetyAssetModel(asset_id=safety_asset_id)
            session.add(asset)
        asset.code = "MEMORY_READ_EXTERNAL_OBSERVATION"
        asset.asset_type = "danger_pattern"
        asset.canonical_name = "记忆读取外部 API 测试普通观察候选"
        asset.category = "memory_read_external_api_test"
        asset.species_scope = []
        asset.sex_scope = []
        asset.age_scope = []
        asset.severity = "info"
        asset.action_class = "safety_warning"
        asset.aliases = []
        asset.carriers = []
        asset.user_expressions = ["普通软便观察，无急症表现。"]
        asset.symptoms = ["软便"]
        asset.recognition_phrases = ["轻微软便但精神食欲正常"]
        asset.required_context = {}
        asset.decision_hints = {"purpose": "container_readiness_only"}
        asset.clinical_risk_summary = "该资产仅用于外部 API 集成测试中的临床安全仓储就绪检查。"
        asset.triage_message = "当前信息更适合作为普通观察建议处理。"
        asset.source = {"system": "memory_read_external_api_test"}
        asset.raw_text = {"embedding_text": embedding_text}
        asset.version = "memory_read_external_api_test"
        asset.enabled = True
        asset.review_status = "approved"
        asset.published_at = now
        asset.metadata_json = {"memory_read_external_api_test": True, "prefix": prefix}

        chunk = session.get(ClinicalSafetyChunkModel, safety_chunk_id)
        if chunk is None:
            chunk = ClinicalSafetyChunkModel(chunk_id=safety_chunk_id, asset_id=safety_asset_id)
            session.add(chunk)
        chunk.asset_id = safety_asset_id
        chunk.chunk_type = "recognition"
        chunk.title = "记忆读取外部 API 测试普通观察 chunk"
        chunk.embedding_text = embedding_text
        chunk.embedding = embedding
        chunk.embedding_model = external_env["QWEN_EMBEDDING_MODEL"]
        chunk.embedding_dimension = len(embedding)
        chunk.content_hash = _content_hash(embedding_text)
        chunk.version = "memory_read_external_api_test"
        chunk.enabled = True
        chunk.review_status = "approved"
        chunk.metadata_json = {"memory_read_external_api_test": True, "prefix": prefix}


def _cleanup_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理记忆读取基线和运行期数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    _cleanup_runtime_database_prefix(database_url, prefix)
    _cleanup_baseline_database_prefix(database_url, prefix)


def _cleanup_runtime_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理记忆读取 API 运行期数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    user_pattern = f"{prefix}%"
    pet_pattern = f"{prefix}%"
    session_pattern = f"{prefix}%"
    with session_factory.begin() as session:
        for sql in (
            "DELETE FROM idempotency_records WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM logic_traces WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_memory_facts WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern",
            "DELETE FROM pet_memory_episodes WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM consultation_states WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM conversation_turns WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_session_bindings WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern OR session_id LIKE :session_pattern",
            "DELETE FROM pet_profiles WHERE user_id LIKE :user_pattern OR pet_id LIKE :pet_pattern",
        ):
            session.execute(
                text(sql),
                {
                    "user_pattern": user_pattern,
                    "pet_pattern": pet_pattern,
                    "session_pattern": session_pattern,
                },
            )


def _cleanup_baseline_database_prefix(database_url: str, prefix: str) -> None:
    """按测试前缀清理记忆读取最小临床安全基线数据。

    :param database_url: 数据库连接串。
    :param prefix: 测试数据前缀。
    :return: 无返回值。
    """
    session_factory = make_session_factory(database_url)
    with session_factory.begin() as session:
        session.execute(
            text("DELETE FROM clinical_safety_chunks WHERE chunk_id = :chunk_id OR asset_id = :asset_id"),
            {"chunk_id": _safety_chunk_id(prefix), "asset_id": _safety_asset_id(prefix)},
        )
        session.execute(
            text("DELETE FROM clinical_safety_assets WHERE asset_id = :asset_id"),
            {"asset_id": _safety_asset_id(prefix)},
        )


def _seed_mem0_memory(
    external_env: dict[str, str],
    *,
    user_id: str,
    pet_id: str,
    session_id: str,
    marker: str,
) -> None:
    """向真实 Mem0 写入本轮测试的语义记忆投影。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param session_id: 测试会话标识。
    :param marker: 测试唯一标记。
    :return: 无返回值。
    """
    payload = {
        "messages": [
            {
                "role": "user",
                "content": f"{marker}：豆豆既往出现过轻微软便，停止油腻零食后逐步好转。",
            }
        ],
        "user_id": user_id,
        "agent_id": "vet-agent",
        "run_id": pet_id,
        "metadata": {
            "source": "memory_read_external_api_test",
            "user_id": user_id,
            "pet_id": pet_id,
            "session_id": session_id,
            "memory_scope": "semantic",
            "marker": marker,
        },
        "infer": False,
    }
    with httpx.Client(timeout=_timeout()) as client:
        response = client.post(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/memories",
            headers=_mem0_headers(external_env),
            json=payload,
        )
        response.raise_for_status()


def _wait_for_mem0_recollection(
    external_env: dict[str, str],
    *,
    user_id: str,
    pet_id: str,
    marker: str,
) -> None:
    """等待真实 Mem0 完成本轮测试记忆写入后的可搜索状态。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param marker: 测试唯一标记。
    :return: 无返回值。
    """
    attempts = int(os.getenv("EXTERNAL_API_TEST_MEM0_RETRY_ATTEMPTS", str(DEFAULT_MEM0_RETRY_ATTEMPTS)))
    delay_seconds = float(os.getenv("EXTERNAL_API_TEST_MEM0_RETRY_DELAY_SECONDS", str(DEFAULT_MEM0_RETRY_DELAY_SECONDS)))
    for attempt in range(1, attempts + 1):
        if _mem0_search_has_marker(external_env, user_id=user_id, pet_id=pet_id, marker=marker):
            return
        if attempt < attempts:
            time.sleep(delay_seconds)
    pytest.fail("真实 Mem0 写入后未能按用户与宠物范围搜索到测试记忆。")


def _mem0_search_has_marker(
    external_env: dict[str, str],
    *,
    user_id: str,
    pet_id: str,
    marker: str,
) -> bool:
    """搜索真实 Mem0 并判断是否命中本轮测试标记。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :param marker: 测试唯一标记。
    :return: 命中本轮测试记忆时返回 True。
    """
    with httpx.Client(timeout=_timeout()) as client:
        response = client.post(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/search",
            headers=_mem0_headers(external_env),
            json={
                "query": f"{marker} 软便 油腻零食",
                "filters": {"user_id": user_id, "run_id": pet_id},
                "top_k": 5,
            },
        )
        response.raise_for_status()
        items = _mem0_items(response.json())
    return any(marker in str(item.get("memory") or item.get("content") or item.get("text") or "") for item in items)


def _cleanup_mem0_scope(external_env: dict[str, str], *, user_id: str, pet_id: str) -> None:
    """按用户与宠物范围清理真实 Mem0 测试记忆。

    :param external_env: 外部依赖配置。
    :param user_id: 测试用户标识。
    :param pet_id: 测试宠物标识。
    :return: 无返回值。
    """
    with httpx.Client(timeout=_timeout()) as client:
        response = client.delete(
            f"{external_env['MEM0_BASE_URL'].rstrip('/')}/memories",
            headers=_mem0_headers(external_env),
            params={"user_id": user_id, "run_id": pet_id},
        )
        response.raise_for_status()


def _mem0_items(data: Any) -> list[dict[str, Any]]:
    """从 Mem0 响应中提取候选记忆列表。

    :param data: Mem0 原始响应。
    :return: 返回原始候选记忆字典列表。
    """
    if isinstance(data, dict):
        raw_items = data.get("results") or data.get("memories") or data.get("data") or []
    else:
        raw_items = data
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _mem0_headers(external_env: dict[str, str]) -> dict[str, str]:
    """构造 Mem0 API 请求头。

    :param external_env: 外部依赖配置。
    :return: 返回 Mem0 请求头。
    """
    return {
        "Content-Type": "application/json",
        "X-API-Key": external_env["MEM0_API_KEY"],
    }


def _with_database_retry(operation: Callable[[], _T], *, action: str) -> _T:
    """对外部开发数据库短暂波动进行有限重试。

    :param operation: 需要执行的数据库操作。
    :param action: 当前操作说明，用于失败诊断。
    :return: 返回数据库操作结果。
    """
    attempts = int(os.getenv("EXTERNAL_API_TEST_DATABASE_RETRY_ATTEMPTS", str(DEFAULT_DATABASE_RETRY_ATTEMPTS)))
    delay_seconds = float(
        os.getenv("EXTERNAL_API_TEST_DATABASE_RETRY_DELAY_SECONDS", str(DEFAULT_DATABASE_RETRY_DELAY_SECONDS))
    )
    last_error: SQLAlchemyError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except SQLAlchemyError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(delay_seconds)
    raise AssertionError(f"外部数据库操作失败：{action}。") from last_error


def _scope_ids(prefix: str) -> tuple[str, str, str]:
    """构造记忆读取 API 测试的用户、宠物和会话标识。

    :param prefix: 测试数据前缀。
    :return: 返回 user_id、pet_id 和 session_id。
    """
    return (
        f"{prefix}_user_memory",
        f"{prefix}_pet_memory",
        f"{prefix}_session_memory",
    )


def _embedding_vector(
    external_env: dict[str, str],
    text_value: str,
    *,
    client: httpx.Client | None = None,
) -> list[float]:
    """通过真实 LiteLLM embedding API 生成测试向量。

    :param external_env: 外部依赖配置。
    :param text_value: 待向量化文本。
    :param client: 可复用的 HTTP 客户端。
    :return: 返回浮点向量。
    """
    if client is None:
        with httpx.Client(timeout=_timeout()) as owned_client:
            return _embedding_vector(external_env, text_value, client=owned_client)
    embedding = client.post(
        f"{external_env['LITELLM_BASE_URL'].rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {external_env['LITELLM_API_KEY']}"},
        json={
            "model": external_env["QWEN_EMBEDDING_MODEL"],
            "input": text_value,
        },
    )
    embedding.raise_for_status()
    vector = embedding.json()["data"][0]["embedding"]
    return [float(value) for value in vector]


def _empty_clinical_safety_policy_input() -> dict[str, Any]:
    """构造 OPA 临床安全依赖探测使用的空候选输入。

    :return: 返回 OPA Data API input 字典。
    """
    return {
        "context": {
            "request_id": "req_memory_read_dependency_probe",
            "trace_id": "trace_memory_read_dependency_probe",
            "user_id": "user_memory_read_dependency_probe",
            "pet_id": "pet_memory_read_dependency_probe",
            "session_id": "session_memory_read_dependency_probe",
        },
        "semantic": {
            "trusted": False,
            "stage": "skipped",
            "strategy": "not_requested",
            "confidence": 0.0,
            "species": "unknown",
            "sex": "unknown",
            "age_group": "unknown",
            "exposure_state": "unknown",
            "symptom_state": "unknown",
            "temporal_state": "unknown",
            "temporal_scope": "unclear",
            "resolution_state": "unknown",
            "intent_type": "other",
            "risk_evidence_state": "unknown",
            "high_risk_terms": [],
            "negated_terms": [],
        },
        "retrieval": {
            "stage": "none",
            "degraded": False,
            "reasons": [],
            "retrieval_source": "",
            "vector_hit_count": 0,
            "candidate_count": 0,
        },
        "thresholds": {
            "signal_min_score": 0.65,
            "urgent_min_score": 0.75,
        },
        "candidates": [],
    }


def _opa_data_url(base_url: str, package_name: str, rule_name: str) -> str:
    """构造兼容网关前缀的 OPA Data API 地址。

    :param base_url: OPA 服务基础地址，可为服务根路径或已经包含 /v1 的地址。
    :param package_name: Rego package 名称。
    :param rule_name: Rego 规则名称。
    :return: 返回 OPA Data API 完整地址。
    """
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.endswith("/v1"):
        normalized_base_url = f"{normalized_base_url}/v1"
    policy_path = "/".join((*package_name.split("."), rule_name))
    return f"{normalized_base_url}/data/{policy_path}"


def _safety_asset_id(prefix: str) -> str:
    """构造记忆读取测试临床安全资产标识。

    :param prefix: 测试数据前缀。
    :return: 返回资产标识。
    """
    return f"{prefix}_memory_read_observation"


def _safety_chunk_id(prefix: str) -> str:
    """构造记忆读取测试临床安全 chunk 标识。

    :param prefix: 测试数据前缀。
    :return: 返回 chunk 标识。
    """
    return f"{_safety_asset_id(prefix)}.recognition.v1"


def _clinical_safety_embedding_text(prefix: str) -> str:
    """返回记忆读取测试所需临床安全向量 chunk 文本。

    :param prefix: 测试数据前缀。
    :return: 返回用于生成测试向量的文本。
    """
    return (
        f"{prefix} 临床安全仓储就绪基线：轻微软便、精神食欲正常、无呕吐时，"
        "可作为普通观察咨询，不属于急症红旗。"
    )


def _content_hash(text_value: str) -> str:
    """生成测试基线文本内容哈希。

    :param text_value: 待哈希文本。
    :return: 返回十六进制哈希摘要。
    """
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _enabled(name: str) -> bool:
    """判断布尔环境变量是否开启。

    :param name: 环境变量名称。
    :return: 开启时返回 True。
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _timeout() -> float:
    """读取记忆读取外部 API 集成测试超时时间。

    :return: 返回超时时间秒数。
    """
    return float(os.getenv("EXTERNAL_API_TEST_TIMEOUT_SECONDS", str(DEFAULT_EXTERNAL_TIMEOUT_SECONDS)))
